from dataclasses import dataclass, asdict
from readlift.projection.segments import validate_segment_partition
from readlift.projection.compose import compose_read
from readlift.reference.blocks import build_source_index, expand_block
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


def audit_segment_records(read, segments, assembled_records, records, target_reference):
    if len(records) != len(segments):
        raise ValueError("read handoff mismatch")
    validate_segment_partition(segments)
    if len({r["segment_index"] for r in records}) != len(records):
        raise ValueError("duplicate record segment")
    for i, (segment, record) in enumerate(zip(segments, records)):
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
        if record.get("primary") != (i == select_primary_segment(segments)):
            raise ValueError("incorrect primary choice")
        base = assembled_records[i].to_data()
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


def audit_read_partition(composed, assembled, records, target_reference):
    read = composed.read
    if read != assembled.read:
        raise ValueError("read handoff mismatch")
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
    audit_segment_records(read, assembled.segments, assembled.records, records, target_reference)


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


def build_chain_source_ledger(projection, mappings):
    first, second = mappings
    return {"source_events": [asdict(e) for e in projection.source_events],
            "stages": [{"mapping_id": first.mapping_id,
                        "events": [asdict(e) for e in projection.stage1_events]},
                       {"mapping_id": second.mapping_id,
                        "events": [asdict(e) for e in projection.stage2_events]}],
            "undelivered_events": [e.event_id for e in projection.stage2_events
                                   if e.fate == "undelivered"],
            "elided_events": [e.event_id for e in projection.stage2_events if e.fate == "elided"]}


def audit_chain_read(projection, assembled, records, mappings, target_reference):
    read = projection.read
    if read != assembled.read:
        raise ValueError("read handoff mismatch")
    first_mapping, second_mapping = mappings
    stage1, stage2 = projection.stage1_events, projection.stage2_events
    if ([e.event_id for e in stage1] != list(range(len(stage1)))
            or [e.event_id for e in stage2] != list(range(len(stage2)))):
        raise ValueError("stage event identity is not consecutive")
    expected = {e.event_id for e in projection.source_events if e.operation != "S"}
    actual = [e.source_event_id for e in stage1 if e.source_event_id is not None]
    if len(actual) != len(set(actual)) or set(actual) != expected:
        raise ValueError("source event identity was lost or duplicated")
    fresh = compose_read(read, first_mapping)
    if len(fresh.events) != len(stage1):
        raise ValueError("first stage column consumption mismatch")
    for event, projected in zip(stage1, fresh.events):
        if ((event.operation, event.query_index, event.source_position, event.anchor,
             event.target_name, event.target_position, event.mapping_strand, event.block_id,
             event.column, event.source_event_id, event.owner_source)
                != (projected.operation, projected.query_index, projected.source_position,
                    projected.anchor, projected.target_name, projected.target_position,
                    projected.mapping_strand, projected.block_id, projected.column,
                    projected.source_event_id, projected.owner_source)):
            raise ValueError("first stage column consumption mismatch")
        if event.parent_event_id != event.source_event_id or event.segment_index is not None:
            raise ValueError("first stage evidence does not close")
    index, _ = build_source_index(second_mapping)
    columns = {}
    for block in second_mapping.blocks:
        for column in expand_block(block):
            columns[(column.block_id, column.column)] = column
    children_of = {}
    for event in stage2:
        if event.parent_event_id is not None:
            children_of.setdefault(event.parent_event_id, []).append(event)
        if event.mapping_strand not in (-1, 1):
            raise ValueError("invalid cumulative direction")
        if event.operation in ("M", "I"):
            if event.query_index is None:
                raise ValueError("query identity lost")
        elif event.operation in ("D", None):
            if event.query_index is not None:
                raise ValueError("deletion carries query identity")
        else:
            raise ValueError("unknown stage operation")
        if event.source_position is not None:
            column = index.get((event.source_name, event.source_position))
            if (column is None or (event.block_id, event.column, event.target_name,
                                   event.target_position)
                    != (column.block_id, column.column, column.target_name, column.target_position)):
                raise ValueError("second stage column consumption mismatch")
            parent = stage1[event.parent_event_id] if event.parent_event_id is not None else None
            if parent is None or event.mapping_strand != parent.mapping_strand * column.strand:
                raise ValueError("cumulative direction mismatch")
        elif (event.block_id, event.column) not in columns:
            raise ValueError("second stage column evidence missing")
        if event.parent_event_id is None:
            column = columns.get((event.block_id, event.column))
            if (event.source_event_id is not None or event.operation != "D" or column is None
                    or column.source_position is not None or column.source_anchor != event.anchor
                    or column.target_name != event.target_name
                    or column.target_position != event.target_position
                    or column.source_name != event.source_name):
                raise ValueError("second stage target column mismatch")
        elif event.source_event_id != stage1[event.parent_event_id].source_event_id:
            raise ValueError("source identity inheritance mismatch")
    for event in stage1:
        children = children_of.get(event.event_id, [])
        if event.fate == "continued":
            if len(children) != 1 or event.next_event_id != children[0].event_id:
                raise ValueError("continued event does not reference its child")
        elif (event.fate not in ("elided", "undelivered") or event.next_event_id is not None
                or children or (event.fate == "elided") != (event.operation is None)):
            raise ValueError("first stage fate does not close")
    delivered = {}
    for segment in assembled.segments:
        for event in segment.events:
            if event.event_id in delivered:
                raise ValueError("event assigned to multiple segments")
            delivered[event.event_id] = segment.segment_index
    by_id = {e.event_id: e for e in stage2}
    for segment in assembled.segments:
        if any(by_id.get(e.event_id) != e for e in segment.events):
            raise ValueError("segment contains foreign events")
    for event in stage2:
        if event.next_event_id is not None:
            raise ValueError("second stage has no continuation")
        if event.fate == "delivered":
            if (event.operation is None or event.segment_index is None
                    or delivered.get(event.event_id) != event.segment_index):
                raise ValueError("delivered event segment mismatch")
        elif event.fate == "elided":
            if event.operation is not None or event.segment_index is not None:
                raise ValueError("elided evidence mismatch")
        elif event.fate == "undelivered":
            if event.operation is None or event.segment_index is not None or event.event_id in delivered:
                raise ValueError("undelivered evidence mismatch")
        else:
            raise ValueError("unknown event fate")
    audit_segment_records(read, assembled.segments, assembled.records, records, target_reference)


def audit_chain_batch(batch, projections, assembled, records, pairing, target_reference, mappings):
    reads = {r.read_id: r for p in batch.pairs for r in p.reads}
    if set(projections) != set(reads) or set(assembled) != set(reads):
        raise ValueError("missing batch member")
    if any(r["read_id"] not in reads for r in records):
        raise ValueError("foreign delivered record")
    ledger = {}
    for read_id in sorted(reads):
        member_records = [r for r in records if r["read_id"] == read_id]
        audit_chain_read(projections[read_id], assembled[read_id], member_records,
                         mappings, target_reference)
        ledger[read_id] = build_chain_source_ledger(projections[read_id], mappings)
    proper = 0
    for pair in batch.pairs:
        first, second = (assembled[r.read_id] for r in pair.reads)
        pair_records = [r for r in records if r["pair_id"] == pair.pair_id]
        audit_pair_records(first, second, pair_records, pairing)
        proper += all(r["proper_pair"] for r in pair_records)
    return BatchAudit(len(batch.pairs), len(reads), len(records), sum(r["primary"] for r in records),
                      sum(r["supplementary"] for r in records), proper, ledger)
