import copy
import io
import json
import tempfile
from pathlib import Path
from test_service import ServiceCase
from readlift.service import ReadliftService
from readlift.archive.store import SnapshotStore
import chain_cases

PAIRING = chain_cases.PAIRING


class InterruptedStore(SnapshotStore):
    def checkpoint(self, stage, candidate):
        super().checkpoint(stage, candidate)
        if stage == getattr(self, "blocked", None):
            raise OSError("chain publication interrupted")


def case_named(name):
    return next(c for c in chain_cases.cases() if c["name"] == name)


def record_path(result):
    return [[r["target_start"], r["target_end"], r["operations"], r["md"], r["nm"]]
            for r in result["records"]]


class TestChain(ServiceCase):
    def test_cancel_transport_and_elision(self):
        case = case_named("cancel")
        chain_cases.prepare(self.service, case)
        job = self.service.create_chain_job("batch", ["ab", "bc"], dict(PAIRING))
        result = self.service.execute_job(job)
        chain_cases.check_result(case, result)
        self.assertEqual(record_path(result), [[0, 4, "4M", "4", 0]] * 2)
        ledger = result["audit"]["source_ledger"]["p/1"]
        first, second = [s["events"] for s in ledger["stages"]]
        # Hand expectation: the middle-only A is one stage-1 D without source identity,
        # transported (continued) and elided on the stage-2 source-only column.
        gap = [e for e in first if e["source_event_id"] is None and e["operation"] == "D"]
        self.assertEqual(len(gap), 1)
        self.assertEqual((gap[0]["fate"], gap[0]["anchor"], gap[0]["target_position"]),
                         ("continued", 2, 2))
        child = {e["event_id"]: e for e in second}[gap[0]["next_event_id"]]
        self.assertEqual((child["operation"], child["fate"], child["source_position"]), (None, "elided", 2))
        self.assertEqual([e["segment_index"] for e in second if e["fate"] == "delivered"], [0] * 4)
        self.assertEqual(self.service.execute_job(job), result)

    def test_independent_insertion_and_deletion(self):
        case = case_named("independent")
        chain_cases.prepare(self.service, case)
        result = self.service.execute_job(self.service.create_chain_job("batch", ["ab", "bc"], dict(PAIRING)))
        chain_cases.check_result(case, result)
        self.assertEqual(record_path(result), [[0, 5, "2M1I1D2M", "2^A2", 2]] * 2)
        second = result["audit"]["source_ledger"]["p/1"]["stages"][1]["events"]
        # The original query insertion and the new target-only deletion stay independent.
        insertion = [e for e in second if e["operation"] == "I"]
        deletion = [e for e in second if e["operation"] == "D"]
        self.assertEqual((len(insertion), len(deletion)), (1, 1))
        self.assertIsNotNone(insertion[0]["source_event_id"])
        self.assertIsNone(deletion[0]["source_event_id"])
        self.assertIsNone(deletion[0]["parent_event_id"])
        self.assertEqual((deletion[0]["anchor"], deletion[0]["target_position"]), (2, 2))

    def test_double_reverse_restores_direction(self):
        case = case_named("double_reverse")
        chain_cases.prepare(self.service, case)
        result = self.service.execute_job(self.service.create_chain_job("batch", ["ab", "bc"], dict(PAIRING)))
        chain_cases.check_result(case, result)
        self.assertEqual(record_path(result), [[0, 6, "6M", "6", 0]] * 2)
        for record in result["records"]:
            self.assertEqual((record["sequence"], record["quality"]), ("ACGTAA", "ABCDEF"))
        self.assertEqual({r["mate_index"]: r["strand"] for r in result["records"]}, {1: 1, 2: -1})
        first, second = [s["events"] for s in result["audit"]["source_ledger"]["p/1"]["stages"]]
        self.assertEqual({e["mapping_strand"] for e in first}, {-1})
        self.assertEqual({e["mapping_strand"] for e in second}, {1})

    def test_rejoin_segments(self):
        case = case_named("rejoin")
        chain_cases.prepare(self.service, case)
        result = self.service.execute_job(self.service.create_chain_job("batch", ["ab", "bc"], dict(PAIRING)))
        chain_cases.check_result(case, result)
        # The middle segment boundary is not permanent: one final 6M segment per mate.
        self.assertEqual(record_path(result), [[0, 6, "6M", "6", 0]] * 2)
        self.assertEqual(len(result["segments"]), 2)
        self.assertTrue(all(r["proper_pair"] for r in result["records"]))
        self.assertEqual(result["audit"]["proper_pairs"], 1)

    def test_still_split_keeps_segments(self):
        case = case_named("still_split")
        chain_cases.prepare(self.service, case)
        result = self.service.execute_job(self.service.create_chain_job("batch", ["ab", "bc"], dict(PAIRING)))
        chain_cases.check_result(case, result)
        self.assertEqual(record_path(result), [[0, 3, "3M3S", "3", 0], [5, 8, "3S3M", "3", 0],
                                               [0, 3, "3M3S", "3", 0], [5, 8, "3S3M", "3", 0]])
        self.assertEqual([[r["query_start"], r["query_end"]] for r in result["records"]],
                         [[0, 3], [3, 6], [3, 6], [0, 3]])
        self.assertFalse(any(r["proper_pair"] for r in result["records"]))
        self.assertEqual(result["audit"]["supplementary_records"], 2)

    def test_deletion_bridge_rejoins(self):
        case = case_named("deletion_bridge")
        chain_cases.prepare(self.service, case)
        result = self.service.execute_job(self.service.create_chain_job("batch", ["ab", "bc"], dict(PAIRING)))
        chain_cases.check_result(case, result)
        self.assertEqual(record_path(result), [[0, 4, "1M2D1M", "1^CG1", 2]] * 2)
        # The two independent middle deletions are transported, not dropped.
        second = result["audit"]["source_ledger"]["p/1"]["stages"][1]["events"]
        deletions = [e for e in second if e["operation"] == "D"]
        self.assertEqual([e["target_position"] for e in deletions], [1, 2])
        self.assertEqual([e["fate"] for e in deletions], ["delivered", "delivered"])
        self.assertEqual(len(result["segments"]), 2)

    def test_undelivered_final_deletion(self):
        self.service.register_reference("a", {"chr": "ACGT"})
        self.service.register_reference("b", {"chr": "ACGT"})
        self.service.register_reference("c", {"chr": "ACNNGT"})
        self.service.register_mapping("ab", "a", "b", [chain_cases.block("id", "ACGT", "ACGT")])
        self.service.register_mapping("bc", "b", "c", [chain_cases.block("left", "AC", "AC"),
                                                       chain_cases.block("right", "GT", "GT", 2, 4)])
        pair = {"pair_id": "p", "reads": [
            dict(read_id="p/1", pair_id="p", mate_index=1, source_version="a", source_name="chr",
                 start=0, source_strand=1, operations="2M2D", sequence="AC", quality="AB"),
            dict(read_id="p/2", pair_id="p", mate_index=2, source_version="a", source_name="chr",
                 start=0, source_strand=-1, operations="2D2M", sequence="AC", quality="BA")]}
        self.service.ingest_pairs("batch", [pair])
        result = self.service.execute_job(self.service.create_chain_job("batch", ["ab", "bc"], dict(PAIRING)))
        self.assertEqual(record_path(result), [[0, 2, "2M", "2", 0], [4, 6, "2M", "2", 0]])
        self.assertEqual([r["template_length"] for r in result["records"]], [6, -6])
        self.assertTrue(all(r["proper_pair"] for r in result["records"]))
        ledger = result["audit"]["source_ledger"]
        for read_id, deletions in (("p/1", [4, 5]), ("p/2", [0, 1])):
            second = ledger[read_id]["stages"][1]["events"]
            gone = [e for e in second if e["operation"] == "D"]
            self.assertEqual([e["target_position"] for e in gone], deletions)
            self.assertEqual([e["fate"] for e in gone], ["undelivered", "undelivered"])
            self.assertEqual(ledger[read_id]["undelivered_events"], [e["event_id"] for e in gone])

    def test_chain_binding_validation(self):
        chain_cases.prepare(self.service, case_named("cancel"))
        before = self.service.store.read_snapshot()
        for ids in (["ab"], ["ab", "bc", "bc"], [], "ab", ["ab", 5], ["bc", "ab"], [""], None):
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                self.service.create_chain_job("batch", ids, dict(PAIRING))
        with self.assertRaises(KeyError):
            self.service.create_chain_job("batch", ["ab", "missing"], dict(PAIRING))
        with self.assertRaises(KeyError):
            self.service.create_chain_job("missing", ["ab", "bc"], dict(PAIRING))
        for pairing in ({}, {"mate1_strand": 0}, None):
            with self.assertRaises(ValueError):
                self.service.create_chain_job("batch", ["ab", "bc"], pairing)
        self.assertEqual(self.service.store.read_snapshot(), before)

    def test_missing_coverage_and_no_m_rejected(self):
        for mode in ("coverage", "no_m", "deleted_coverage"):
            case = copy.deepcopy(case_named("cancel" if mode != "deleted_coverage" else "deletion_bridge"))
            if mode == "coverage":
                case["mappings"][1]["blocks"] = [chain_cases.block("partial", "AC", "AC")]
            elif mode == "no_m":
                case["references"]["c"] = "A"
                case["mappings"][1]["blocks"] = [chain_cases.block("no-m", "ACAGT-", "-----A")]
            else:
                case["mappings"][1]["blocks"] = [b for b in case["mappings"][1]["blocks"]
                                                  if b["source_start"] != 1]
            with self.subTest(mode=mode):
                with tempfile.TemporaryDirectory(prefix="readlift-chain-test-") as directory:
                    with ReadliftService(Path(directory) / "archive.json") as service:
                        chain_cases.prepare(service, case)
                        job = service.create_chain_job("batch", ["ab", "bc"], dict(PAIRING))
                        before = service.store.read_snapshot()
                        with self.assertRaises(ValueError):
                            service.execute_job(job)
                        self.assertEqual(service.store.read_snapshot(), before)

    def test_binding_frozen_and_containers_isolated(self):
        case = case_named("independent")
        chain_cases.prepare(self.service, case)
        ids, pairing = ["ab", "bc"], dict(PAIRING)
        job = self.service.create_chain_job("batch", ids, pairing)
        ids.reverse()
        pairing["max_span"] = 0
        self.assertEqual(self.service.create_chain_job("batch", ["ab", "bc"], dict(PAIRING)), job)
        binding = self.service.jobs.get_job(job)
        self.assertEqual((binding.mapping_ids, binding.reference_versions), (["ab", "bc"], ["a", "b", "c"]))
        self.assertEqual((len(binding.mapping_digests), len(binding.reference_digests)), (2, 3))
        result = self.service.execute_job(job)
        self.service.register_mapping("later", "b", "c", case["mappings"][1]["blocks"])
        self.assertEqual(self.service.read_result(result["result_id"]), result)
        view = self.service.read_result(result["result_id"])
        view["audit"]["source_ledger"]["p/1"]["stages"][0]["events"].clear()
        view["binding"]["mapping_ids"].reverse()
        self.assertEqual(self.service.read_result(result["result_id"]), result)
        self.assertEqual(self.service.execute_job(job), result)

    def test_publish_failure_replay_and_reopen(self):
        store = InterruptedStore(self.path)
        service = ReadliftService(self.path, store=store)
        self.addCleanup(service.close)
        case = case_named("rejoin")
        chain_cases.prepare(service, case)
        old = service.execute_job(service.create_job("batch", "ab", dict(PAIRING)))
        job = service.create_chain_job("batch", ["ab", "bc"], dict(PAIRING))
        before = store.read_snapshot()
        for point in ("candidate_result", "candidate_receipt", "before_replace"):
            store.blocked = point
            with self.subTest(point=point), self.assertRaises(OSError):
                service.execute_job(job)
            self.assertEqual(store.read_snapshot(), before)
        store.blocked = None
        result = service.execute_job(job)
        chain_cases.check_result(case, result)
        self.assertEqual(service.read_result(old["result_id"]), old)
        calls = dict(service.metrics)
        self.assertEqual(service.execute_job(job), result)
        self.assertEqual(service.metrics, calls)
        output = io.StringIO()
        service.export_result(result["result_id"], output)
        self.assertEqual(json.loads(output.getvalue()), result)
        service.close()
        with ReadliftService(self.path) as reopened:
            self.assertEqual(reopened.execute_job(job), result)
            self.assertEqual(reopened.read_result(result["result_id"]), result)
            self.assertEqual(reopened.metrics, {"conversion_calls": 0, "publication_calls": 0})
            again = io.StringIO()
            reopened.export_result(result["result_id"], again)
            self.assertEqual(again.getvalue(), output.getvalue())

    def test_empty_batch_chain(self):
        self.service.register_reference("a", {"chr": "ACGT"})
        self.service.register_reference("b", {"chr": "ACGT"})
        self.service.register_reference("c", {"chr": "ACGT"})
        self.service.register_mapping("ab", "a", "b", [chain_cases.block("id", "ACGT", "ACGT")])
        self.service.register_mapping("bc", "b", "c", [chain_cases.block("id2", "ACGT", "ACGT")])
        self.service.ingest_pairs("empty", [])
        result = self.service.execute_job(self.service.create_chain_job("empty", ["ab", "bc"], dict(PAIRING)))
        self.assertEqual((result["segments"], result["records"]), ([], []))
        self.assertEqual(result["audit"], {"input_pairs": 0, "input_reads": 0, "segments": 0,
                         "primary_records": 0, "supplementary_records": 0, "proper_pairs": 0,
                         "source_ledger": {}})
