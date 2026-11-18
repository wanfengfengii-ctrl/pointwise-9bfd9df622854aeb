import argparse
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from readlift.service import ReadliftService


def flatten(suite):
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from flatten(test)
        else:
            yield test


def verify(names):
    suite = unittest.defaultTestLoader.discover("tests")
    if names:
        available = {test.id(): test for test in flatten(suite)}
        missing = sorted(set(names) - set(available))
        if missing:
            raise ValueError("unknown tests: " + ", ".join(missing))
        suite = unittest.TestSuite(available[name] for name in names)
    return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1


def demo(sample_path, store_path):
    sample = json.loads(Path(sample_path).read_text())
    with ReadliftService(store_path) as service:
        for reference in sample["references"]:
            service.register_reference(**reference)
        service.register_mapping(**sample["mapping"])
        service.ingest_pairs(**sample["batch"])
        job = service.create_job(sample["batch"]["batch_id"], sample["mapping"]["mapping_id"], sample["pairing"])
        result = service.execute_job(job)
        output = io.StringIO()
        service.export_result(result["result_id"], output)
        assert json.loads(output.getvalue()) == result
        actual = [[r["operations"], r["md"], r["nm"], r["template_length"]] for r in result["records"]]
        assert actual == [["3M", "3", 0, 7], ["3M", "3", 0, -7]]
        assert result["audit"]["proper_pairs"] == 1
        print(output.getvalue(), end="")
    with ReadliftService(store_path) as service:
        assert service.read_result(result["result_id"]) == result
        assert service.metrics["conversion_calls"] == 0
    print("POINTWISE_SCENARIO " + json.dumps(
        {"case_id": "v1.normal_pair", "input": sample["batch"],
         "expected": [["3M", "3", 0, 7], ["3M", "3", 0, -7]], "actual": actual,
         "downstream_expected": {"proper_pairs": 1, "reopen_equal": True},
         "downstream_actual": {"proper_pairs": result["audit"]["proper_pairs"], "reopen_equal": True},
         "code_paths": ["readlift/service.py", "readlift/projection/compose.py",
                        "readlift/assembly/pairs.py", "readlift/archive/store.py"],
         "oracle": "hand positions [0,3),[4,7); span 7; equality asserted after real reopen"},
        sort_keys=True))
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("tests", nargs="*")
    parser.add_argument("--demo", metavar="SAMPLE")
    parser.add_argument("--store", metavar="PATH")
    args = parser.parse_args()
    if args.demo:
        if args.tests:
            parser.error("test selectors cannot accompany demo")
        if args.store:
            code = demo(args.demo, args.store)
        else:
            with tempfile.TemporaryDirectory(prefix="readlift-demo-") as directory:
                code = demo(args.demo, Path(directory) / "archive.json")
        if code:
            return code
        from chain_cases import verify as chain_verify
        return chain_verify(True)
    if args.store:
        parser.error("--store requires --demo")
    code = verify(args.tests)
    if code or args.tests:
        return code
    from chain_cases import verify as chain_verify
    return chain_verify()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, KeyError, OSError) as exc:
        print(json.dumps({"error": str(exc)}, sort_keys=True), file=sys.stderr)
        raise SystemExit(2)
