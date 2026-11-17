import io
import json
from pathlib import Path
import tempfile
import unittest
from readlift.service import ReadliftService


FR = {"mate1_strand": 1, "mate2_strand": -1, "order": "mate1_left", "min_span": 0, "max_span": 100}


def block(source, target, **changes):
    data = {"block_id": "block", "source_name": "chr", "source_start": 0,
            "source_end": len(source.replace("-", "")), "target_name": "chr",
            "target_start": 0, "target_end": len(target.replace("-", "")), "strand": 1,
            "source_aligned": source, "target_aligned": target}
    data.update(changes)
    return data


def read(sequence, operations, *, mate=1, start=0, strand=1, quality=None, pair="pair", name="chr"):
    return {"read_id": pair + "/" + str(mate), "pair_id": pair, "mate_index": mate,
            "source_version": "src", "source_name": name, "start": start, "source_strand": strand,
            "operations": operations, "sequence": sequence, "quality": quality or "J" * len(sequence)}


def pair(first, second):
    return {"pair_id": first["pair_id"], "reads": [first, second]}


def prepare(service, source="ACGTACGT", target=None, blocks=None, pairs=None, pairing=None):
    target = source if target is None else target
    service.register_reference("src", {"chr": source})
    service.register_reference("dst", {"chr": target})
    service.register_mapping("map", "src", "dst", blocks if blocks is not None else [block(source, target)])
    if pairs is None:
        pairs = [pair(read("ACG", "3M"), read("CGT", "3M", mate=2, start=4, strand=-1))]
    service.ingest_pairs("batch", pairs)
    return service.create_job("batch", "map", pairing or FR)


def direction_setup(service):
    blocks = [block("ACG", "ACG", block_id="a"),
              block("TAA", "TAA", block_id="b", source_start=3, source_end=6,
                    target_start=5, target_end=8, strand=-1)]
    pairs = [pair(read("ACGTAA", "6M", quality="ABCDEF"),
                  read("CGT", "3M", mate=2, strand=-1, quality="XYZ"))]
    return prepare(service, "ACGTAA", "ACGCCTTA", blocks, pairs)


def emit(case_id, input_data, expected, actual, downstream_expected, downstream_actual, paths, oracle):
    print("POINTWISE_SCENARIO " + json.dumps(
        {"case_id": case_id, "input": input_data, "expected": expected, "actual": actual,
         "downstream_expected": downstream_expected, "downstream_actual": downstream_actual,
         "code_paths": paths, "oracle": oracle}, sort_keys=True))


class ServiceCase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "readlift.json"
        self.service = ReadliftService(self.path)
        self.addCleanup(self.service.close)


class TestService(ServiceCase):
    def test_day_workflow(self):
        sample = json.loads((Path(__file__).resolve().parents[1] / "samples" / "day.json").read_text())
        for reference in sample["references"]:
            self.service.register_reference(**reference)
        self.service.register_mapping(**sample["mapping"])
        self.service.ingest_pairs(**sample["batch"])
        job = self.service.create_job("day", "day-map", sample["pairing"])
        result = self.service.execute_job(job)
        actual = [(r["operations"], r["md"], r["nm"], r["template_length"]) for r in result["records"]]
        self.assertEqual(actual, [("3M", "3", 0, 7), ("3M", "3", 0, -7)])
        self.assertEqual(result["audit"]["proper_pairs"], 1)
        output = io.StringIO()
        self.service.export_result(result["result_id"], output)
        self.assertEqual(json.loads(output.getvalue()), result)
        self.service.close()
        with ReadliftService(self.path) as reopened:
            self.assertEqual(reopened.read_result(result["result_id"]), result)
            self.assertEqual(reopened.metrics["conversion_calls"], 0)
        emit("v1.normal_pair", sample["batch"], [["3M", "3", 0, 7], ["3M", "3", 0, -7]],
             [list(r) for r in actual], {"proper_pairs": 1}, {"proper_pairs": result["audit"]["proper_pairs"]},
             ["readlift/service.py", "readlift/assembly/pairs.py", "readlift/archive/publish.py"],
             "independent positions [0,3),[4,7); outer span 7; exact base comparison")
