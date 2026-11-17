from dataclasses import replace
from test_service import ServiceCase, FR, prepare, direction_setup, block, read, pair, emit
from readlift.projection.compose import compose_read
from readlift.projection.segments import segment_composed_read
from readlift.assembly.records import assemble_read


class TestAssembly(ServiceCase):
    def test_direction_split_pair(self):
        result = self.service.execute_job(direction_setup(self.service))
        records = [r for r in result["records"] if r["mate_index"] == 1]
        actual = [[r["query_start"], r["query_end"], r["target_start"], r["target_end"],
                   r["strand"], r["sequence"], r["quality"], r["operations"], r["md"], r["nm"]]
                  for r in records]
        expected = [[0, 3, 0, 3, 1, "ACGTAA", "ABCDEF", "3M3S", "3", 0],
                    [3, 6, 5, 8, -1, "TTACGT", "FEDCBA", "3M3S", "3", 0]]
        self.assertEqual(actual, expected)
        self.assertEqual([r["primary"] for r in records], [True, False])
        self.assertFalse(any(r["proper_pair"] for r in result["records"]))
        self.assertEqual(result["audit"]["supplementary_records"], 1)
        self.assertEqual([r["mate_target_start"] for r in records], [0, 0])
        emit("v4.direction_payload", {"source": "ACGTAA", "target": "ACGCCTTA", "quality": "ABCDEF"},
             expected, actual, {"proper_pairs": 0, "supplementary": 1},
             {"proper_pairs": result["audit"]["proper_pairs"],
              "supplementary": result["audit"]["supplementary_records"]},
             ["readlift/projection/segments.py", "readlift/projection/orientation.py",
              "readlift/assembly/records.py", "readlift/assembly/pairs.py"],
             "hand partition at source 3; reverse complement TAA=TTA and reverse ABCDEF=FEDCBA")

    def test_primary_tie_and_source_negative(self):
        job = direction_setup(self.service)
        mapping = self.service.references.get_mapping("map")
        batch = self.service.batches.get_batch("batch")
        target = self.service.references.get_reference("dst")
        source_read = batch.pairs[0].reads[0]
        composed = compose_read(source_read, replace(mapping, blocks=tuple(reversed(mapping.blocks))))
        assembled = assemble_read(source_read, segment_composed_read(composed), target)
        self.assertEqual(assembled.primary_index, 0)
        self.assertEqual([r.target_start for r in assembled.records], [0, 5])
        negative = replace(source_read, source_strand=-1, sequence="TTACGT", quality="FEDCBA")
        composed = compose_read(negative, mapping)
        assembled = assemble_read(negative, segment_composed_read(composed), target)
        self.assertEqual([(r.query_start, r.query_end, r.strand, r.sequence, r.quality, r.operations)
                          for r in assembled.records],
                         [(3, 6, -1, "ACGTAA", "ABCDEF", "3M3S"),
                          (0, 3, 1, "TTACGT", "FEDCBA", "3M3S")])
        self.assertEqual([r.md for r in assembled.records], ["3", "3"])

    def test_pair_bounds_and_cross_sequence(self):
        prepare(self.service)
        for minimum, maximum, expected in [(7, 7, True), (0, 6, False), (8, 9, False)]:
            config = {**FR, "min_span": minimum, "max_span": maximum}
            result = self.service.execute_job(self.service.create_job("batch", "map", config))
            self.assertEqual([r["proper_pair"] for r in result["records"]], [expected, expected])
        reverse_order = {**FR, "order": "mate2_left"}
        result = self.service.execute_job(self.service.create_job("batch", "map", reverse_order))
        self.assertFalse(any(r["proper_pair"] for r in result["records"]))
        self.service.register_reference("split-target", {"a": "ACGT", "b": "ACGT"})
        blocks = [block("ACGT", "ACGT", block_id="a", target_name="a"),
                  block("ACGT", "ACGT", block_id="b", source_start=4, source_end=8, target_name="b")]
        self.service.register_mapping("cross", "src", "split-target", blocks)
        result = self.service.execute_job(self.service.create_job("batch", "cross", FR))
        self.assertEqual([r["template_length"] for r in result["records"]], [0, 0])
        self.assertFalse(any(r["proper_pair"] for r in result["records"]))
        self.assertEqual([r["mate_target_name"] for r in result["records"]], ["b", "a"])

    def test_contiguous_blocks_and_deletion_only_piece(self):
        blocks = [block("AC", "AC", block_id="a"),
                  block("GT", "GT", block_id="b", source_start=2, source_end=4, target_start=2, target_end=4)]
        pairs = [pair(read("ACGT", "4M"), read("ACGT", "4M", mate=2, strand=-1))]
        job = prepare(self.service, "ACGT", blocks=blocks, pairs=pairs)
        result = self.service.execute_job(job)
        self.assertEqual(len(result["segments"]), 2)
        self.assertEqual([r["operations"] for r in result["records"]], ["4M", "4M"])
        self.service.register_reference("jump-target", {"chr": "ACNNGT"})
        blocks[1] = {**blocks[1], "target_start": 4, "target_end": 6}
        self.service.register_mapping("jump", "src", "jump-target", blocks)
        self.service.ingest_pairs("deletion", [pair(read("AC", "2M2D"), read("GT", "2D2M", mate=2))])
        result = self.service.execute_job(self.service.create_job("deletion", "jump", FR))
        self.assertEqual([r["operations"] for r in result["records"]], ["2M", "2M"])
        self.assertEqual([r["target_start"] for r in result["records"]], [0, 4])
        self.assertEqual(len(result["audit"]["source_ledger"]["pair/1"]["undelivered_event_orders"]), 2)

    def test_uncovered_failure_and_no_m(self):
        blocks = [block("AC", "AC")]
        job = prepare(self.service, "ACGT", "ACGT", blocks,
                      [pair(read("ACGT", "4M"), read("ACGT", "4M", mate=2))])
        before = self.service.store.read_snapshot()
        with self.assertRaises(ValueError):
            self.service.execute_job(job)
        self.assertEqual(self.service.store.read_snapshot(), before)
        self.service.register_mapping("source-only", "src", "dst", [block("ACGT-", "----A",
                                      target_start=0, target_end=1)])
        job = self.service.create_job("batch", "source-only", FR)
        with self.assertRaises(ValueError):
            self.service.execute_job(job)
        self.assertEqual(self.service.store.read_snapshot()["results"], {})
        self.assertEqual(self.service.store.read_snapshot()["receipts"], {})
