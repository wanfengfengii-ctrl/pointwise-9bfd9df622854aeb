import copy
from test_service import ServiceCase, block


class TestReference(ServiceCase):
    def test_invalid_mapping_atomic(self):
        original = self.service.register_reference("src", {"chr": "ACGT"})
        self.service.register_reference("dst", {"chr": "ACAGT"})
        self.assertEqual(self.service.register_reference("src", {"chr": "ACGT"}), original)
        with self.assertRaises(ValueError):
            self.service.register_reference("src", {"chr": "AAAA"})
        self.assertEqual(self.service.references.get_reference("src").to_data(), original)
        good = block("AC-GT", "ACAGT")
        invalid = [
            {**good, "source_aligned": "AC--GT", "target_aligned": "ACA-GT"},
            {**good, "source_end": 3},
            {**good, "strand": -1},
            {**good, "target_end": 6},
            {**good, "strand": True},
            {**good, "source_start": -1},
        ]
        for candidate in invalid:
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                self.service.register_mapping("map", "src", "dst", [candidate])
            self.assertNotIn("map", self.service.store.read_snapshot()["mappings"])
        with self.assertRaises(ValueError):
            self.service.register_mapping("map", "src", "dst", [good, {**good, "block_id": "overlap"}])
        with self.assertRaises(KeyError):
            self.service.register_mapping("map", "absent", "dst", [good])
        mapping = self.service.register_mapping("map", "src", "dst", [good])
        self.assertEqual(self.service.register_mapping("map", "src", "dst", [good]), mapping)
        with self.assertRaises(ValueError):
            self.service.register_mapping("map", "src", "dst", [{**good, "block_id": "different"}])
        self.assertEqual(self.service.references.get_mapping("map").digest, mapping["digest"])
        mapping["summary"].clear()
        self.assertTrue(self.service.references.get_mapping("map").summary)

    def test_reference_and_mapping_values(self):
        for version, sequences in [("", {"chr": "A"}), ("src", {"": "A"}),
                                   ("src", {"chr": ""}), ("src", {"chr": "acgt"}),
                                   ("src", {"chr": "AX"}), ("src", {})]:
            with self.subTest(sequences=sequences), self.assertRaises(ValueError):
                self.service.register_reference(version, sequences)
        sequences = {"chr": "AC"}
        self.service.register_reference("src", sequences)
        sequences["chr"] = "TT"
        self.service.register_reference("dst", {"chr": "GT"})
        good = block("AC", "AC", strand=-1)
        result = self.service.register_mapping("reverse", "src", "dst", [good])
        self.assertEqual(result["summary"]["block"]["columns"], 2)
        self.service.register_reference("substitution", {"chr": "AG"})
        result = self.service.register_mapping("sub", "src", "substitution", [block("AC", "AG")])
        self.assertEqual(result["summary"]["block"]["source_bases"], 2)
        self.assertEqual(self.service.references.get_reference("src").to_data()["sequences"], {"chr": "AC"})
