"""Hand-written chain inputs and observable outcomes, not a chain implementation."""
import argparse
import copy
import io
import json
import tempfile
from dataclasses import asdict, replace
from pathlib import Path
from readlift.service import ReadliftService
from readlift.reference.sequences import ReferenceVersion, reference_digest
from readlift.reference.validation import validate_reference_definition, validate_mapping_definition
from readlift.reference.blocks import MappingVersion, mapping_digest
from readlift.intake.records import source_pair_from_data
from readlift.intake.validation import validate_source_pair
from readlift.intake.operations import expand_source_alignment
from readlift.projection.compose import compose_read
from readlift.projection.segments import segment_composed_read
from readlift.assembly.records import assemble_read
from readlift.archive.store import SnapshotStore

PAIRING = {"mate1_strand": 1, "mate2_strand": -1, "order": "either",
           "min_span": 0, "max_span": 100}

def rc(text):
    return text.translate(str.maketrans("ACGTN", "TGCAN"))[::-1]

def block(identity, source, target, ss=0, ts=0, strand=1):
    return dict(block_id=identity, source_name="chr", source_start=ss,
                source_end=ss + len(source.replace("-", "")), target_name="chr",
                target_start=ts, target_end=ts + len(target.replace("-", "")),
                strand=strand, source_aligned=source, target_aligned=target)

def scenario(name, a, b, c, ab, bc, query=None, operations=None, expected=None):
    query = a if query is None else query
    quality = "".join(chr(65 + i) for i in range(len(query)))
    reads = [dict(read_id="p/" + str(m), pair_id="p", mate_index=m,
                  source_version="a", source_name="chr", start=0,
                  source_strand=1 if m == 1 else -1,
                  operations=operations or str(len(a)) + "M",
                  sequence=query if m == 1 else rc(query),
                  quality=quality if m == 1 else quality[::-1]) for m in (1, 2)]
    return dict(name=name, references={"a": a, "b": b, "c": c},
                mappings=[dict(mapping_id="ab", source_version="a", target_version="b", blocks=ab),
                          dict(mapping_id="bc", source_version="b", target_version="c", blocks=bc)],
                pair={"pair_id": "p", "reads": reads}, expected=expected)

def cases():
    split_ab = [block("left", "ACG", "ACG"),
                block("right", "TAA", "TAA", 3, 5, -1)]
    rejoin_bc = [block("left", "ACG", "ACG"),
                 block("right", "TTA", "TTA", 5, 3, -1)]
    permutation_ab = [block(str(i), s, s, i, j)
                      for i, j, s in [(0, 0, "A"), (1, 2, "C"), (2, 1, "G"), (3, 3, "T")]]
    permutation_bc = [block(str(i), s, s, i, j)
                      for i, j, s in [(0, 0, "A"), (1, 2, "G"), (2, 1, "C"), (3, 3, "T")]]
    # Expected entries: target_start, target_end, operations, MD, NM.
    return [
        scenario("cancel", "ACGT", "ACAGT", "ACGT",
                 [block("ab-gap", "AC-GT", "ACAGT")],
                 [block("bc-gap", "ACAGT", "AC-GT")],
                 expected=[[0, 4, "4M", "4", 0]]),
        scenario("independent", "ACGT", "ACGT", "ACAGT",
                 [block("ab-id", "ACGT", "ACGT")],
                 [block("bc-gap", "AC-GT", "ACAGT")],
                 query="ACAGT", operations="2M1I2M",
                 expected=[[0, 5, "2M1I1D2M", "2^A2", 2]]),
        scenario("double_reverse", "ACGTAA", "TTACGT", "ACGTAA",
                 [block("ab-rev", "ACGTAA", "ACGTAA", strand=-1)],
                 [block("bc-rev", "TTACGT", "TTACGT", strand=-1)],
                 expected=[[0, 6, "6M", "6", 0]]),
        scenario("rejoin", "ACGTAA", "ACGCCTTA", "ACGTAA", split_ab, rejoin_bc,
                 expected=[[0, 6, "6M", "6", 0]]),
        scenario("still_split", "ACGTAA", "ACGCCTTA", "ACGNNTAA", split_ab,
                 [block("left", "ACG", "ACG"), block("right", "TTA", "TTA", 5, 5, -1)],
                 expected=[[0, 3, "3M3S", "3", 0], [5, 8, "3S3M", "3", 0]]),
        scenario("deletion_bridge", "ACGT", "AGCT", "ACGT",
                 permutation_ab, permutation_bc, query="AT", operations="1M2D1M",
                 expected=[[0, 4, "1M2D1M", "1^CG1", 2]])
    ]

def materialize(case):
    refs = {}
    for name, bases in case["references"].items():
        seqs = validate_reference_definition(name, {"chr": bases})
        refs[name] = ReferenceVersion(name, tuple(seqs.items()), reference_digest(seqs))
    mappings = []
    for data in case["mappings"]:
        blocks, summary = validate_mapping_definition(data["mapping_id"],
                         refs[data["source_version"]], refs[data["target_version"]], data["blocks"])
        body = {**data, "blocks": [asdict(b) for b in blocks], "summary": summary}
        mappings.append(MappingVersion(data["mapping_id"], data["source_version"],
                                       data["target_version"], blocks, summary, mapping_digest(body)))
    pair = source_pair_from_data(case["pair"])
    class Catalog:
        def get_reference(self, name):
            return refs[name]
    validate_source_pair(pair, Catalog())
    return refs, mappings, pair

def fixtures():
    for case in cases():
        refs, mappings, pair = materialize(case)
        assert mappings[0].target_version == mappings[1].source_version
        assert len(pair.reads) == 2 and case["expected"]
        print(json.dumps({"case": case["name"], "expected": case["expected"]}, sort_keys=True))
    return 0

def probe_v1():
    """Observe the shortest record-to-record reuse; this is not V2 verification."""
    for case in cases():
        refs, mappings, pair = materialize(case)
        read = pair.reads[0]
        first = compose_read(read, mappings[0])
        mid = assemble_read(read, segment_composed_read(first), refs["b"])
        final = []
        for record in mid.records:
            synthetic = replace(read, source_version="b", source_name=record.target_name,
                                start=record.target_start, source_strand=record.strand,
                                operations=record.operations)
            second = compose_read(synthetic, mappings[1])
            assembled = assemble_read(synthetic, segment_composed_read(second), refs["c"])
            final.extend([[r.target_start, r.target_end, r.operations, r.md, r.nm]
                          for r in assembled.records])
        assigned = {e.order for s in mid.segments for e in s.events}
        omitted = [e.order for e in first.events if e.order not in assigned]
        print(json.dumps({"case": case["name"], "record_reuse": final,
                          "hand_expected": case["expected"], "omitted_middle_events": omitted},
                         sort_keys=True))
    return 0

def prepare(service, case):
    for name, bases in case["references"].items():
        service.register_reference(name, {"chr": bases})
    for mapping in case["mappings"]:
        service.register_mapping(**mapping)
    service.ingest_pairs("batch", [case["pair"]])

def check_result(case, result):
    for mate in (1, 2):
        records = [r for r in result["records"] if r["mate_index"] == mate]
        actual = [[r[k] for k in ("target_start", "target_end", "operations", "md", "nm")]
                  for r in records]
        assert actual == case["expected"], (case["name"], mate, actual, case["expected"])
        source = case["pair"]["reads"][mate - 1]
        for r in records:
            assert r["strand"] == source["source_strand"]
            assert r["sequence"] == case["pair"]["reads"][0]["sequence"]
            assert r["quality"] == case["pair"]["reads"][0]["quality"]
        assert [r["primary"] for r in records] == [True] + [False] * (len(records) - 1)
        intervals = [[0, len(source["sequence"])]]
        if case["name"] == "still_split":
            intervals = [[0, 3], [3, 6]] if mate == 1 else [[3, 6], [0, 3]]
        assert [[r["query_start"], r["query_end"]] for r in records] == intervals
        assert all(r["proper_pair"] == (len(case["expected"]) == 1) for r in records)
        ledger = result["audit"]["source_ledger"][source["read_id"]]
        original_read = source_pair_from_data(case["pair"]).reads[mate - 1]
        expected_source = [asdict(e) for e in expand_source_alignment(original_read)]
        assert ledger["source_events"] == expected_source
        assert len(ledger["stages"]) == 2
        for index, stage in enumerate(ledger["stages"]):
            assert stage["mapping_id"] == ("ab", "bc")[index]
            ids = [e["event_id"] for e in stage["events"]]
            assert ids == list(range(len(ids)))
        first, second = [s["events"] for s in ledger["stages"]]
        raw_ids = [e["event_id"] for e in expected_source if e["operation"] != "S"]
        actual_raw = [e["source_event_id"] for e in first if e["source_event_id"] is not None]
        assert sorted(actual_raw) == sorted(raw_ids)
        by_id = {e["event_id"]: e for e in second}
        assert all(e["parent_event_id"] is None or e["parent_event_id"] in range(len(first))
                   for e in second)
        children = {}
        for e in second:
            if e["parent_event_id"] is not None:
                children.setdefault(e["parent_event_id"], []).append(e["event_id"])
        for e in first:
            if e["fate"] == "continued":
                assert children.get(e["event_id"]) == [e["next_event_id"]]
                assert by_id[e["next_event_id"]]["source_event_id"] == e["source_event_id"]
            else:
                assert e["fate"] in ("elided", "undelivered")
                assert e["next_event_id"] is None and e["event_id"] not in children
        if case["name"] == "cancel":
            gap = [e for e in first if e["source_event_id"] is None and e["operation"] == "D"]
            assert len(gap) == 1
            child = by_id[gap[0]["next_event_id"]]
            assert child["operation"] is None and child["fate"] == "elided"
        if case["name"] == "independent":
            assert any(e["operation"] == "I" and e["source_event_id"] is not None for e in second)
            assert any(e["operation"] == "D" and e["source_event_id"] is None for e in second)
    binding = result["binding"]
    assert binding["mapping_ids"] == ["ab", "bc"]
    assert binding["reference_versions"] == ["a", "b", "c"]
    assert len(binding["mapping_digests"]) == 2 and len(binding["reference_digests"]) == 3

class InterruptedStore(SnapshotStore):
    def checkpoint(self, stage, candidate):
        super().checkpoint(stage, candidate)
        if stage == getattr(self, "blocked", None):
            raise OSError("chain publication interrupted")

def expect(exception, action):
    try:
        action()
    except exception:
        return
    raise AssertionError("expected " + exception.__name__)

def verify(demo=False):
    selected = [c for c in cases() if c["name"] in ("cancel", "rejoin")] if demo else cases()
    for case in selected:
        with tempfile.TemporaryDirectory(prefix="readlift-chain-") as directory:
            path = Path(directory) / "archive.json"
            store = InterruptedStore(path)
            with ReadliftService(path, store=store) as service:
                prepare(service, case)
                old = service.execute_job(service.create_job("batch", "ab", PAIRING))
                before = store.read_snapshot()
                expect(ValueError, lambda: service.create_chain_job("batch", ["ab"], PAIRING))
                expect(KeyError, lambda: service.create_chain_job("batch", ["ab", "missing"], PAIRING))
                expect(ValueError, lambda: service.create_chain_job("batch", ["bc", "ab"], PAIRING))
                assert store.read_snapshot() == before
                ids, pairing = ["ab", "bc"], dict(PAIRING)
                job = service.create_chain_job("batch", ids, pairing)
                ids.reverse()
                pairing["max_span"] = 0
                assert service.create_chain_job("batch", ["ab", "bc"], PAIRING) == job
                before = store.read_snapshot()
                for point in ("candidate_result", "candidate_receipt", "before_replace"):
                    store.blocked = point
                    expect(OSError, lambda: service.execute_job(job))
                    assert store.read_snapshot() == before
                store.blocked = None
                result = service.execute_job(job)
                check_result(case, result)
                assert service.read_result(old["result_id"]) == old
                calls = dict(service.metrics)
                assert service.execute_job(job) == result and service.metrics == calls
                output = io.StringIO()
                service.export_result(result["result_id"], output)
                assert json.loads(output.getvalue()) == result
                service.read_result(result["result_id"])["audit"].clear()
                assert service.read_result(result["result_id"]) == result
                service.register_mapping("later", **{k: v for k, v in case["mappings"][1].items()
                                                      if k != "mapping_id"})
                assert service.read_result(result["result_id"]) == result
            with ReadliftService(path) as reopened:
                assert reopened.execute_job(job) == result
                assert reopened.read_result(result["result_id"]) == result
                assert reopened.metrics["conversion_calls"] == 0
                after = io.StringIO()
                reopened.export_result(result["result_id"], after)
                assert after.getvalue() == output.getvalue()
            print(json.dumps({"case": case["name"], "records": result["records"],
                              "audit": result["audit"]}, sort_keys=True))
    if not demo:
        for mode in ("coverage", "no_m", "deleted_coverage"):
            case = copy.deepcopy(cases()[0] if mode != "deleted_coverage" else cases()[-1])
            if mode == "coverage":
                case["mappings"][1]["blocks"] = [block("partial", "AC", "AC")]
            elif mode == "no_m":
                case["references"]["c"] = "A"
                case["mappings"][1]["blocks"] = [block("no-m", "ACAGT-", "-----A")]
            else:
                case["mappings"][1]["blocks"] = [b for b in case["mappings"][1]["blocks"]
                                                  if b["source_start"] != 1]
            with tempfile.TemporaryDirectory(prefix="readlift-chain-reject-") as directory:
                with ReadliftService(Path(directory) / "archive.json") as service:
                    prepare(service, case)
                    job = service.create_chain_job("batch", ["ab", "bc"], PAIRING)
                    before = service.store.read_snapshot()
                    expect(ValueError, lambda: service.execute_job(job))
                    assert service.store.read_snapshot() == before
            print(json.dumps({"rejected": mode}, sort_keys=True))
    return 0

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("fixtures", "probe-v1", "verify", "demo"))
    args = parser.parse_args()
    return {"fixtures": fixtures, "probe-v1": probe_v1,
            "verify": verify, "demo": lambda: verify(True)}[args.mode]()

if __name__ == "__main__":
    raise SystemExit(main())
