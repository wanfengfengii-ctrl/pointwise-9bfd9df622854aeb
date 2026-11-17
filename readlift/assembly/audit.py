from dataclasses import dataclass, asdict
from readlift.projection.segments import validate_segment_partition
from readlift.intake.operations import parse_operations, reference_consumption, query_consumption
from readlift.assembly.pairs import attach_pair_fields
from readlift.assembly.records import build_soft_clipped_operations, select_primary_segment
from readlift.intake.operations import format_operations
from readlift.assembly.differences import compute_difference_tags
from readlift.projection.orientation import orient_query
from readlift.reference.sequences import reference_slice


@dataclass(frozen=True)
class BatchAudit:
    input_pairs: int
    input_reads: int
    segments: int
    primary_records: int
    supplementary_records: int
    proper_pairs: int
    source_ledger: dict

    def to_data(self):
        return asdict(self)


def build_source_ledger(composed, segments):
    assigned = {e.order for s in segments for e in s.events}
    return {"source_events": [asdict(e) for e in composed.source_events],
            "projected_events": [asdict(e) for e in composed.events],
            "undelivered_event_orders": [e.order for e in composed.events if e.order not in assigned],
            "elided_source_deletions": [e.source_position for e in composed.events
                                        if e.operation is None and e.source_position is not None]}


def audit_read_partition(composed, assembled, records, target_reference):
    read = composed.read
    if read != assembled.read or len(records) != len(assembled.segments):
        raise ValueError("read handoff mismatch")
    validate_segment_partition(assembled.segments)
    expected_events = {e.event_id for e in composed.source_events if e.operation != "S"}
    actual_events = [e.source_event_id for e in composed.events if e.source_event_id is not None]
    if len(actual_events) != len(set(actual_events)) or set(actual_events) != expected_events:
        raise ValueError("source event identity was lost or duplicated")
    source_events = {e.order: e for e in composed.events}
    assigned = [e.order for s in assembled.segments for e in s.events]
    if len(assigned) != len(set(assigned)):
        raise ValueError("event assigned to multiple segments")
    for segment in assembled.segments:
        if any(source_events.get(e.order) != e for e in segment.events):
            raise ValueError("segment contains foreign events")
    if len({r["segment_index"] for r in records}) != len(records):
        raise ValueError("duplicate record segment")
    for i, (segment, record) in enumerate(zip(assembled.segments, records)):
        if (record.get("target_name"), record.get("target_start"), record.get("target_end"),
                record.get("strand"), record.get("query_start"), record.get("query_end")) != (
                segment.target_name, segment.target_start, segment.target_end, segment.strand,
                segment.query_start, segment.query_end):
            raise ValueError("record segment coordinates disagree")
        if (record.get("read_id"), record.get("pair_id"), record.get("mate_index")) != (
                read.read_id, read.pair_id, read.mate_index):
            raise ValueError("record source member disagrees")
        if record.get("operations") != format_operations(build_soft_clipped_operations(segment, read)):
            raise ValueError("record does not consume segment path")
        if record.get("primary") != (i == select_primary_segment(assembled.segments)):
            raise ValueError("incorrect primary choice")
        base = assembled.records[i].to_data()
        if any(record.get(k) != v for k, v in base.items()):
            raise ValueError("record handoff mismatch")
        operations = parse_operations(record["operations"])
        if (query_consumption(operations) != len(read.sequence)
                or reference_consumption(operations) != segment.target_end - segment.target_start):
            raise ValueError("record consumption mismatch")
        query = orient_query(read, segment.strand)
        if record["sequence"] != query.sequence or record["quality"] != query.quality:
            raise ValueError("record orientation mismatch")
        tags = compute_difference_tags(query.sequence, reference_slice(target_reference, segment.target_name,
                                       segment.target_start, segment.target_end), operations)
        if (record["md"], record["nm"]) != (tags.md, tags.nm):
            raise ValueError("difference tag mismatch")
    if sum(r["primary"] for r in records) != 1:
        raise ValueError("read must have one primary")


def audit_pair_records(first, second, records, pairing):
    expected = attach_pair_fields(first, second, pairing)
    if records != expected:
        raise ValueError("pair fields do not describe this conversion")


def audit_batch(batch, projections, assembled, records, pairing, target_reference):
    reads = {r.read_id: r for p in batch.pairs for r in p.reads}
    if set(projections) != set(reads) or set(assembled) != set(reads):
        raise ValueError("missing batch member")
    if any(r["read_id"] not in reads for r in records):
        raise ValueError("foreign delivered record")
    ledger = {}
    for read_id in sorted(reads):
        member_records = [r for r in records if r["read_id"] == read_id]
        audit_read_partition(projections[read_id], assembled[read_id], member_records, target_reference)
        ledger[read_id] = build_source_ledger(projections[read_id], assembled[read_id].segments)
    proper = 0
    for pair in batch.pairs:
        first, second = (assembled[r.read_id] for r in pair.reads)
        pair_records = [r for r in records if r["pair_id"] == pair.pair_id]
        audit_pair_records(first, second, pair_records, pairing)
        proper += all(r["proper_pair"] for r in pair_records)
    return BatchAudit(len(batch.pairs), len(reads), len(records), sum(r["primary"] for r in records),
                      sum(r["supplementary"] for r in records), proper, ledger)
