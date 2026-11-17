from dataclasses import dataclass
from readlift.reference.blocks import build_source_index
from readlift.intake.operations import expand_source_alignment, parse_operations, reference_consumption
from readlift.projection.orientation import to_original_query_interval


@dataclass(frozen=True)
class ProjectedEvent:
    order: int
    operation: str | None
    query_index: int | None
    source_position: int | None
    anchor: int
    target_position: int | None
    target_name: str
    mapping_strand: int
    block_id: str
    column: int
    source_event_id: int | None
    owner_source: int | None


@dataclass(frozen=True)
class ComposedRead:
    read: object
    source_events: tuple
    events: tuple


def select_target_gap_events(gaps, source_name, start, end):
    return {anchor: tuple(columns) for (name, anchor), columns in gaps.items()
            if name == source_name and start < anchor < end}


def compose_read(read, mapping):
    index, gaps = build_source_index(mapping)
    start = read.start
    end = start + reference_consumption(parse_operations(read.operations))
    for position in range(start, end):
        if (read.source_name, position) not in index:
            raise ValueError("source position lacks fixed mapping coverage")
    source_events = tuple(expand_source_alignment(read))
    insertions, references = {}, {}
    for event in source_events:
        if event.operation == "I":
            insertions.setdefault(event.anchor, []).append(event)
        elif event.source_position is not None:
            references[event.source_position] = event
    target_gaps = select_target_gap_events(gaps, read.source_name, start, end)
    result = []
    def emit(operation, query, position, anchor, column, source_id=None, owner=None):
        original = None
        if query is not None:
            original = to_original_query_interval(query, query + 1, len(read.sequence), read.source_strand)[0]
        result.append(ProjectedEvent(len(result), operation, original, position, anchor,
                                     column.target_position if operation != "I" else None,
                                     column.target_name, column.strand, column.block_id,
                                     column.column, source_id, owner))
    for anchor in range(start, end + 1):
        owner = anchor if anchor < end else end - 1
        for event in insertions.get(anchor, ()):
            emit("I", event.query_position, None, anchor, index[(read.source_name, owner)],
                 event.event_id, owner)
        for column in target_gaps.get(anchor, ()):
            emit("D", None, None, anchor, column)
        if anchor == end:
            continue
        event = references[anchor]
        column = index[(read.source_name, anchor)]
        operation = event.operation
        if column.target_position is None:
            operation = "I" if operation == "M" else None
        emit(operation, event.query_position, anchor, anchor, column, event.event_id, anchor)
    return ComposedRead(read, source_events, tuple(result))


def compose_pair(pair, mapping):
    return tuple(compose_read(read, mapping) for read in pair.reads)
