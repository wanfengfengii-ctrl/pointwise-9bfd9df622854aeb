from dataclasses import asdict
from readlift.archive.store import SnapshotStore
from readlift.reference.catalog import ReferenceCatalog
from readlift.intake.batches import BatchCatalog
from readlift.projection.jobs import JobCatalog, ChainJobBinding
from readlift.projection.compose import compose_pair
from readlift.projection.chain import compose_chain_read
from readlift.projection.segments import segment_composed_read
from readlift.assembly.records import assemble_read
from readlift.assembly.pairs import attach_pair_fields
from readlift.archive.publish import ResultPublisher
from readlift.results import read_committed_result, result_view_to_data
from readlift.export import write_result_export
from readlift.requests import (normalize_reference_request, normalize_mapping_request,
                               normalize_pairs_request, normalize_job_request,
                               normalize_chain_job_request)


class ReadliftService:
    def __init__(self, path, *, store=None):
        self.store = store if store is not None else SnapshotStore.open(path)
        self.references = ReferenceCatalog(self.store)
        self.batches = BatchCatalog(self.store, self.references)
        self.jobs = JobCatalog(self.store, self.references, self.batches)
        self.publisher = ResultPublisher(self.store)
        self.metrics = {"conversion_calls": 0, "publication_calls": 0}

    def register_reference(self, version_id, sequences):
        return self.references.register_reference(*normalize_reference_request(version_id, sequences))

    def register_mapping(self, mapping_id, source_version, target_version, blocks):
        return self.references.register_mapping(*normalize_mapping_request(
            mapping_id, source_version, target_version, blocks))

    def ingest_pairs(self, batch_id, pairs):
        return self.batches.ingest_pairs(*normalize_pairs_request(batch_id, pairs))

    def create_job(self, batch_id, mapping_id, pairing):
        return self.jobs.create_job(*normalize_job_request(batch_id, mapping_id, pairing))

    def create_chain_job(self, batch_id, mapping_ids, pairing):
        return self.jobs.create_chain_job(*normalize_chain_job_request(batch_id, mapping_ids, pairing))

    def execute_job(self, job_id):
        binding = self.jobs.get_job(job_id)
        existing = self.publisher.find_completed_result(job_id)
        if existing is not None:
            return existing
        if isinstance(binding, ChainJobBinding):
            return self._execute_chain_job(binding)
        batch = self.batches.get_batch(binding.batch_id)
        mapping = self.references.get_mapping(binding.mapping_id)
        source = self.references.get_reference(binding.source_version)
        target = self.references.get_reference(binding.target_version)
        if ((batch.digest, mapping.digest, source.digest, target.digest)
                != (binding.batch_digest, binding.mapping_digest, binding.source_digest, binding.target_digest)):
            raise ValueError("fixed task content digest mismatch")
        projections, assembled, records = {}, {}, []
        self.metrics["conversion_calls"] += 1
        for pair in batch.pairs:
            converted = compose_pair(pair, mapping)
            members = []
            for composed in converted:
                read = composed.read
                segments = segment_composed_read(composed)
                member = assemble_read(read, segments, target)
                projections[read.read_id] = composed
                assembled[read.read_id] = member
                members.append(member)
            records.extend(attach_pair_fields(*members, binding.pairing))
        result = self.publisher.publish_result(binding, batch, projections, assembled, records, target)
        self.metrics["publication_calls"] += 1
        return result

    def _execute_chain_job(self, binding):
        batch = self.batches.get_batch(binding.batch_id)
        mappings = [self.references.get_mapping(m) for m in binding.mapping_ids]
        references = [self.references.get_reference(v) for v in binding.reference_versions]
        if ((batch.digest, [m.digest for m in mappings], [r.digest for r in references])
                != (binding.batch_digest, binding.mapping_digests, binding.reference_digests)):
            raise ValueError("fixed task content digest mismatch")
        projections, assembled, records = {}, {}, []
        self.metrics["conversion_calls"] += 1
        for pair in batch.pairs:
            members = []
            for read in pair.reads:
                projection = compose_chain_read(read, mappings[0], mappings[1])
                member = assemble_read(read, projection.segments, references[-1])
                projections[read.read_id] = projection
                assembled[read.read_id] = member
                members.append(member)
            records.extend(attach_pair_fields(*members, binding.pairing))
        result = self.publisher.publish_chain_result(binding, batch, projections, assembled,
                                                     records, references[-1], mappings)
        self.metrics["publication_calls"] += 1
        return result

    def read_result(self, result_id):
        return result_view_to_data(read_committed_result(self.store, result_id))

    def export_result(self, result_id, target):
        write_result_export(read_committed_result(self.store, result_id), target)

    def close(self):
        self.store.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()
