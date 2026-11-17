from test_service import ServiceCase, FR, prepare, block, read, pair, emit


class TestProjection(ServiceCase):
    def test_identity_pair(self):
        job = prepare(self.service)
        result = self.service.execute_job(job)
        first, second = result["records"]
        self.assertEqual([(r["target_start"], r["target_end"], r["operations"], r["md"], r["nm"])
                          for r in (first, second)], [(0, 3, "3M", "3", 0), (4, 7, "3M", "3", 0)])
        self.assertEqual((first["mate_target_start"], second["mate_target_start"]), (4, 0))
        self.assertEqual((first["mate_strand"], second["mate_strand"]), (-1, 1))
        self.assertEqual((first["template_length"], second["template_length"]), (7, -7))
        self.assertTrue(first["proper_pair"] and second["proper_pair"])
        self.assertEqual(self.service.execute_job(job), result)
        self.assertEqual(self.service.metrics, {"conversion_calls": 1, "publication_calls": 1})

    def test_target_insertion(self):
        pairs = [pair(read("ACGT", "4M"), read("ACGT", "4M", mate=2, strand=-1))]
        job = prepare(self.service, "ACGT", "ACAGT", [block("AC-GT", "ACAGT")], pairs,
                      {**FR, "min_span": 5, "max_span": 5})
        result = self.service.execute_job(job)
        actual = [(r["target_start"], r["target_end"], r["operations"], r["md"], r["nm"])
                  for r in result["records"]]
        self.assertEqual(actual, [(0, 5, "2M1D2M", "2^A2", 1)] * 2)
        self.assertEqual([r["template_length"] for r in result["records"]], [5, -5])
        self.assertTrue(all(r["proper_pair"] for r in result["records"]))
        emit("v2.inner_gap", {"source": "ACGT", "target": "ACAGT", "read": "ACGT", "operations": "4M"},
             [[0, 5, "2M1D2M", "2^A2", 1]] * 2, [list(r) for r in actual],
             {"spans": [5, -5], "proper": True},
             {"spans": [r["template_length"] for r in result["records"]],
              "proper": all(r["proper_pair"] for r in result["records"])},
             ["readlift/projection/compose.py", "readlift/assembly/differences.py", "readlift/assembly/pairs.py"],
             "write columns AC-GT/ACAGT by hand; target-only A is one deletion")

    def test_source_gap_and_query_insertion(self):
        pairs = [pair(read("ACAAA", "2M1I2M"), read("TGT", "2M1D1M", mate=2, strand=-1))]
        job = prepare(self.service, "ACAA", "ACA", [block("ACAA", "AC-A")], pairs)
        result = self.service.execute_job(job)
        a, b = result["records"]
        self.assertEqual((a["operations"], a["md"], a["nm"]), ("2M2I1M", "3", 2))
        self.assertEqual((b["operations"], b["md"], b["nm"]), ("3M", "3", 0))
        events = result["audit"]["source_ledger"]["pair/1"]["projected_events"]
        self.assertEqual([e["source_position"] for e in events if e["operation"] == "M"], [0, 1, 3])
        self.assertEqual(result["audit"]["source_ledger"]["pair/2"]["elided_source_deletions"], [2])
        emit("v3.repeat_identity", {"source": "ACAA", "target": "ACA", "read": "ACAAA", "ops": "2M1I2M"},
             ["2M2I1M", "3", 2, [0, 1, 3]], [a["operations"], a["md"], a["nm"],
             [e["source_position"] for e in events if e["operation"] == "M"]],
             {"elided": [2]}, {"elided": result["audit"]["source_ledger"]["pair/2"]["elided_source_deletions"]},
             ["readlift/projection/compose.py", "readlift/projection/segments.py", "readlift/assembly/audit.py"],
             "column consumption ACAA/AC-A; last M is fixed source position 3")

    def test_anchor_and_edge_gaps(self):
        self.service.register_reference("src", {"chr": "ACGT"})
        self.service.register_reference("dst", {"chr": "ACAGT"})
        self.service.register_mapping("map", "src", "dst", [block("AC-GT", "ACAGT")])
        pairs = [pair(read("ACGGT", "2M1I2M"), read("AC", "2M", mate=2)),
                 pair(read("GT", "2M", start=2, pair="edge"), read("GT", "2M", mate=2, start=2, pair="edge"))]
        self.service.ingest_pairs("batch", pairs)
        result = self.service.execute_job(self.service.create_job("batch", "map", FR))
        by_id = {r["read_id"]: r for r in result["records"]}
        self.assertEqual(by_id["pair/1"]["operations"], "2M1I1D2M")
        self.assertEqual((by_id["pair/1"]["md"], by_id["pair/1"]["nm"]), ("2^A2", 2))
        self.assertEqual(by_id["pair/2"]["operations"], "2M")
        self.assertEqual((by_id["edge/1"]["target_start"], by_id["edge/1"]["operations"]), (3, "2M"))
        ledger = result["audit"]["source_ledger"]["pair/1"]["projected_events"]
        self.assertEqual(len([e for e in ledger if e["source_event_id"] == 2]), 1)

    def test_literal_n_and_operations(self):
        pairs = [pair(read("TNACG", "1S2M1I1D1M"), read("NACG", "4M", mate=2))]
        job = prepare(self.service, "NACG", pairs=pairs)
        result = self.service.execute_job(job)
        first = result["records"][0]
        self.assertEqual((first["md"], first["nm"], first["operations"]), ("2^C1", 2, "1S2M1I1D1M"))
        self.service.ingest_pairs("mismatch", [pair(read("AACG", "4M"), read("NNCG", "4M", mate=2))])
        result = self.service.execute_job(self.service.create_job("mismatch", "map", FR))
        self.assertEqual([(r["md"], r["nm"]) for r in result["records"]], [("0N3", 1), ("1A2", 1)])

    def test_fixed_binding_and_empty(self):
        job = prepare(self.service)
        fixed = self.service.jobs.get_job(job)
        self.service.register_mapping("later", "src", "dst", [block("ACGTACGT", "ACGTACGT", block_id="new")])
        result = self.service.execute_job(job)
        self.assertEqual(result["binding"]["mapping_id"], "map")
        self.assertEqual(result["binding"]["mapping_digest"], fixed.mapping_digest)
        self.assertEqual({e["block_id"] for e in result["audit"]["source_ledger"]["pair/1"]["projected_events"]},
                         {"block"})
        self.service.ingest_pairs("empty", [])
        empty = self.service.execute_job(self.service.create_job("empty", "map", FR))
        self.assertEqual((empty["segments"], empty["records"]), ([], []))
        self.assertEqual(empty["audit"], {"input_pairs": 0, "input_reads": 0, "segments": 0,
                         "primary_records": 0, "supplementary_records": 0, "proper_pairs": 0, "source_ledger": {}})
