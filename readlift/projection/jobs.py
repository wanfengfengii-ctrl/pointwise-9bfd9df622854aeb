from dataclasses import dataclass, asdict
from readlift.archive.codec import content_digest, detached_data
from readlift.reference.validation import identifier


@dataclass(frozen=True)
class PairingConfig:
    mate1_strand: int
    mate2_strand: int
    order: str
    min_span: int
    max_span: int

    @classmethod
    def from_data(cls, data):
        try:
            config = cls(**data)
        except TypeError as exc:
            raise ValueError("explicit pairing configuration required") from exc
        if any(type(s) is not int or s not in (-1, 1) for s in (config.mate1_strand, config.mate2_strand)):
            raise ValueError("invalid pair direction")
        if config.order not in ("mate1_left", "mate2_left", "either"):
            raise ValueError("invalid pair order")
        if (type(config.min_span) is not int or type(config.max_span) is not int
                or not 0 <= config.min_span <= config.max_span):
            raise ValueError("invalid pair span")
        return config


@dataclass(frozen=True)
class JobBinding:
    job_id: str
    batch_id: str
    mapping_id: str
    source_version: str
    target_version: str
    batch_digest: str
    mapping_digest: str
    source_digest: str
    target_digest: str
    pairing: dict

    @classmethod
    def from_data(cls, data):
        return cls(**detached_data(data))


@dataclass(frozen=True)
class ChainJobBinding:
    job_id: str
    batch_id: str
    batch_digest: str
    mapping_ids: list
    mapping_digests: list
    reference_versions: list
    reference_digests: list
    pairing: dict

    @classmethod
    def from_data(cls, data):
        return cls(**detached_data(data))


def job_binding_digest(data):
    return content_digest(data)


class JobCatalog:
    def __init__(self, store, references, batches):
        self.store, self.references, self.batches = store, references, batches

    def create_job(self, batch_id, mapping_id, pairing):
        batch = self.batches.get_batch(batch_id)
        mapping = self.references.get_mapping(mapping_id)
        config = PairingConfig.from_data(pairing)
        if any(r.source_version != mapping.source_version for p in batch.pairs for r in p.reads):
            raise ValueError("batch and mapping source versions differ")
        source = self.references.get_reference(mapping.source_version)
        target = self.references.get_reference(mapping.target_version)
        body = {"batch_id": batch_id, "mapping_id": mapping_id, "source_version": mapping.source_version,
                "target_version": mapping.target_version, "batch_digest": batch.digest,
                "mapping_digest": mapping.digest, "source_digest": source.digest,
                "target_digest": target.digest, "pairing": asdict(config)}
        job_id = "job-" + job_binding_digest(body)
        data = asdict(JobBinding(job_id=job_id, **body))
        if job_id in self.store.read_snapshot()["jobs"]:
            return job_id
        def insert(state):
            state["jobs"][job_id] = data
            return job_id
        return self.store.atomic_update(insert)

    def create_chain_job(self, batch_id, mapping_ids, pairing):
        batch = self.batches.get_batch(batch_id)
        if (not isinstance(mapping_ids, (list, tuple)) or len(mapping_ids) != 2
                or any(not isinstance(m, str) or not m for m in mapping_ids)):
            raise ValueError("chain requires exactly two mapping ids")
        first = self.references.get_mapping(mapping_ids[0])
        second = self.references.get_mapping(mapping_ids[1])
        config = PairingConfig.from_data(pairing)
        if first.target_version != second.source_version:
            raise ValueError("mapping chain is not version-closed")
        if any(r.source_version != first.source_version for p in batch.pairs for r in p.reads):
            raise ValueError("batch and mapping source versions differ")
        versions = [first.source_version, first.target_version, second.target_version]
        references = [self.references.get_reference(v) for v in versions]
        body = {"batch_id": batch_id, "batch_digest": batch.digest,
                "mapping_ids": list(mapping_ids), "mapping_digests": [first.digest, second.digest],
                "reference_versions": versions,
                "reference_digests": [r.digest for r in references], "pairing": asdict(config)}
        job_id = "job-" + job_binding_digest(body)
        data = asdict(ChainJobBinding(job_id=job_id, **body))
        if job_id in self.store.read_snapshot()["jobs"]:
            return job_id
        def insert(state):
            state["jobs"][job_id] = data
            return job_id
        return self.store.atomic_update(insert)

    def get_job(self, job_id):
        identifier(job_id)
        data = self.store.read_snapshot()["jobs"][job_id]
        if "mapping_ids" in data:
            return ChainJobBinding.from_data(data)
        return JobBinding.from_data(data)
