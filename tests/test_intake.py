import copy
from dataclasses import asdict
from test_service import ServiceCase, FR, prepare, read, pair


class TestIntake(ServiceCase):
    def test_invalid_pair_batch_atomic(self):
        prepare(self.service)
        good = pair(read("ACG", "3M", pair="one"), read("CGT", "3M", mate=2, pair="one"))
        bad = pair(read("ACG", "3M", pair="two"), read("CGT", "3M", mate=2, pair="two"))
        bad["reads"][1]["quality"] = "JJ"
        before = self.service.store.read_snapshot()
        with self.assertRaises(ValueError):
            self.service.ingest_pairs("retry", [good, bad])
        self.assertEqual(self.service.store.read_snapshot(), before)
        with self.assertRaises(KeyError):
            self.service.batches.get_batch("retry")
        bad["reads"][1]["quality"] = "JJJ"
        accepted = self.service.ingest_pairs("retry", [good, bad])
        self.assertEqual(self.service.ingest_pairs("retry", [bad, good]), accepted)
        good["reads"][0]["sequence"] = "AAA"
        with self.assertRaises(ValueError):
            self.service.ingest_pairs("retry", [good, bad])
        self.assertEqual(self.service.batches.get_batch("retry").to_data(), accepted)

    def test_operations_and_member_identity(self):
        prepare(self.service)
        base = pair(read("ACG", "3M"), read("CGT", "3M", mate=2))
        cases = [{"operations": "0M3M"}, {"operations": "3X"}, {"operations": "1M1S1M"},
                 {"operations": "2M"}, {"start": 7}, {"start": True}, {"source_strand": 0},
                 {"quality": "JJ\n"}, {"sequence": "acg"}, {"mate_index": True}, {"read_id": ""}]
        for changes in cases:
            candidate = copy.deepcopy(base)
            candidate["reads"][0].update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.service.ingest_pairs("invalid", [candidate])
        with self.assertRaises(ValueError):
            self.service.ingest_pairs("duplicate", [base, base])
        missing = copy.deepcopy(base)
        missing["reads"].pop()
        with self.assertRaises(ValueError):
            self.service.ingest_pairs("missing", [missing])
        duplicate_read = copy.deepcopy(base)
        duplicate_read["reads"][1]["read_id"] = duplicate_read["reads"][0]["read_id"]
        with self.assertRaises(ValueError):
            self.service.ingest_pairs("duplicate-read", [duplicate_read])
        normalized = copy.deepcopy(base)
        normalized["reads"][0]["operations"] = "1M2M"
        accepted = self.service.ingest_pairs("normalized", [normalized])
        self.assertEqual(accepted["pairs"][0]["reads"][0]["operations"], "3M")
        self.assertEqual(self.service.ingest_pairs("normalized", [base]), accepted)

    def test_job_binding(self):
        original_job = prepare(self.service)
        with self.assertRaises(KeyError):
            self.service.create_job("absent", "map", FR)
        with self.assertRaises(KeyError):
            self.service.create_job("batch", "absent", FR)
        for changes in ({"mate1_strand": 0}, {"order": "guess"}, {"min_span": -1},
                        {"min_span": 10, "max_span": 2}, {"max_span": True}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.service.create_job("batch", "map", {**FR, **changes})
        with self.assertRaises(ValueError):
            self.service.create_job("batch", "map", {})
        configuration = dict(FR)
        job = self.service.create_job("batch", "map", configuration)
        self.assertEqual(job, original_job)
        configuration["max_span"] = 0
        self.assertEqual(asdict(self.service.jobs.get_job(job))["pairing"], FR)
        self.service.register_reference("different", {"chr": "ACGTACGT"})
        self.service.register_mapping("wrong-source", "different", "dst",
                                      self.service.references.get_mapping("map").to_data()["blocks"])
        with self.assertRaises(ValueError):
            self.service.create_job("batch", "wrong-source", FR)
