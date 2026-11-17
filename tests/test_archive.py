import copy
import io
import json
from dataclasses import replace
from pathlib import Path
from test_service import ServiceCase, FR, prepare, direction_setup
from readlift.service import ReadliftService
from readlift.archive.store import SnapshotStore
from readlift.projection.compose import compose_pair
from readlift.projection.segments import segment_composed_read
from readlift.assembly.records import assemble_read
from readlift.assembly.pairs import attach_pair_fields


class InterruptedStore(SnapshotStore):
    def checkpoint(self, stage, candidate):
        super().checkpoint(stage, candidate)
        if stage == getattr(self, "interrupt_stage", None):
            raise OSError("controlled storage interruption")


class FailingOutput:
    def __init__(self):
        self.prefix = ""
    def write(self, text):
        self.prefix += text
        if len(self.prefix) > 50:
            raise OSError("output interrupted")
        return len(text)


class TestArchive(ServiceCase):
    def test_reopen_and_export_failure(self):
        job = direction_setup(self.service)
        result = self.service.execute_job(job)
        identity = result["result_id"]
        baseline = io.StringIO()
        self.service.export_result(identity, baseline)
        returned = self.service.read_result(identity)
        returned["records"][0]["operations"] = "1M"
        returned["segments"].clear()
        returned["audit"]["source_ledger"]["pair/1"]["projected_events"][0]["block_id"] = "changed"
        returned["binding"]["pairing"]["max_span"] = 0
        result["records"].clear()
        self.assertEqual(json.loads(baseline.getvalue()), self.service.read_result(identity))
        failed = FailingOutput()
        with self.assertRaisesRegex(OSError, "output interrupted"):
            self.service.export_result(identity, failed)
        self.assertTrue(failed.prefix)
        retry = io.StringIO()
        self.service.export_result(identity, retry)
        self.assertEqual(retry.getvalue(), baseline.getvalue())
        calls = dict(self.service.metrics)
        self.assertEqual(self.service.execute_job(job)["result_id"], identity)
        self.assertEqual(self.service.metrics, calls)
        self.service.register_reference("new", {"chr": "NN"})
        self.service.ingest_pairs("empty", [])
        self.service.create_job("empty", "map", FR)
        self.assertEqual(self.service.read_result(identity), json.loads(baseline.getvalue()))
        self.service.close()
        with ReadliftService(self.path) as reopened:
            self.assertEqual(reopened.read_result(identity), json.loads(baseline.getvalue()))
            self.assertEqual(reopened.execute_job(job)["result_id"], identity)
            self.assertEqual(reopened.metrics, {"conversion_calls": 0, "publication_calls": 0})
            self.assertEqual(reopened.references.get_reference("new").to_data()["sequences"], {"chr": "NN"})

    def test_publish_rollback_and_retry(self):
        store = InterruptedStore(self.path)
        service = ReadliftService(self.path, store=store)
        self.addCleanup(service.close)
        old_job = prepare(service)
        old_result = service.execute_job(old_job)
        service.batches.ingest_pairs("second", service.batches.get_batch("batch").to_data()["pairs"])
        job = service.create_job("second", "map", FR)
        before = store.read_snapshot()
        for stage in ("candidate_result", "candidate_receipt", "before_replace"):
            store.interrupt_stage = stage
            with self.subTest(stage=stage), self.assertRaisesRegex(OSError, "controlled storage"):
                service.execute_job(job)
            self.assertEqual(store.read_snapshot(), before)
            with ReadliftService(self.path) as reopened:
                self.assertEqual(reopened.store.read_snapshot(), before)
                self.assertEqual(reopened.read_result(old_result["result_id"]), old_result)
                self.assertNotIn(job, reopened.store.read_snapshot()["receipts"])
            self.assertEqual(list(Path(self.directory.name).glob("*.tmp")), [])
        store.interrupt_stage = None
        result = service.execute_job(job)
        calls = dict(service.metrics)
        self.assertEqual(service.execute_job(job), result)
        self.assertEqual(service.metrics, calls)
        state = store.read_snapshot()
        self.assertEqual(len(state["results"]), 2)
        self.assertEqual(len(state["receipts"]), 2)
        self.assertEqual(state["receipts"][job]["result_id"], result["result_id"])

    def test_initialization_and_corruption(self):
        job = prepare(self.service)
        result = self.service.execute_job(job)
        self.service.close()
        before = self.path.read_bytes()
        with ReadliftService(self.path) as reopened:
            self.assertEqual(reopened.read_result(result["result_id"]), result)
        self.assertEqual(self.path.read_bytes(), before)
        broken = Path(self.directory.name) / "broken.json"
        for payload in (b"truncated", b"{}", before[:-5]):
            broken.write_bytes(payload)
            with self.subTest(payload=payload[:20]), self.assertRaises(ValueError):
                ReadliftService(broken)
            self.assertEqual(broken.read_bytes(), payload)
        with ReadliftService(self.path) as reopened:
            with self.assertRaises(KeyError):
                reopened.read_result("absent")
            with self.assertRaises(KeyError):
                reopened.execute_job("absent")

    def test_audit_guard(self):
        job = direction_setup(self.service)
        binding = self.service.jobs.get_job(job)
        batch = self.service.batches.get_batch(binding.batch_id)
        mapping = self.service.references.get_mapping(binding.mapping_id)
        target = self.service.references.get_reference(binding.target_version)
        projections, assembled = {}, {}
        for composed in compose_pair(batch.pairs[0], mapping):
            projections[composed.read.read_id] = composed
            segments = segment_composed_read(composed)
            assembled[composed.read.read_id] = assemble_read(composed.read, segments, target)
        first, second = (assembled[r.read_id] for r in batch.pairs[0].reads)
        records = attach_pair_fields(first, second, binding.pairing)
        candidates = []
        malformed = copy.deepcopy(records)
        malformed[0]["operations"] = "1M5S"
        candidates.append((assembled, malformed))
        candidates.append((assembled, records[:-1]))
        overlap = dict(assembled)
        segments = list(first.segments)
        segments[1] = replace(segments[1], query_start=2)
        overlap[first.read.read_id] = replace(first, segments=tuple(segments))
        candidates.append((overlap, records))
        for candidate, candidate_records in candidates:
            with self.assertRaises(ValueError):
                self.service.publisher.publish_result(binding, batch, projections, candidate, candidate_records, target)
            self.assertEqual(self.service.store.read_snapshot()["results"], {})
            self.assertEqual(self.service.store.read_snapshot()["receipts"], {})
        valid = self.service.publisher.publish_result(binding, batch, projections, assembled, records, target)
        self.assertEqual(valid["audit"]["segments"], 3)
