"""Two-stage fixed-mapping transport (public increment contract in CHAIN.md).

Stage one reuses the V1 projection and grouping verbatim. Stage two transports
every middle-reference consumption position (including pure-deletion spans)
through the second mapping, keeps original query identity, and lets the final
segments re-form over the final reference with the cumulative direction.
"""
from dataclasses import dataclass, replace
from readlift.reference.blocks import build_source_index
from readlift.projection.compose import ComposedRead, compose_read, select_target_gap_events
from readlift.projection.segments import group_projected_events, segment_composed_read


@dataclass(frozen=True)
class ChainEvent:
    event_id: int
    order: int
    operation: str | None
    query_index: int | None
    source_event_id: int | None
    parent_event_id: int | None
    source_name: str | None
    source_position: int | None
    anchor: int | None
    target_name: str | None
    target_position: int | None
    mapping_strand: int | None
    block_id: str | None
    column: int | None
    owner_source: int | None
    fate: str | None
    next_event_id: int | None
    segment_index: int | None


@dataclass(frozen=True)
class ChainProjection:
    read: object
    source_events: tuple
    stage1_events: tuple
    stage2_events: tuple
    segments: tuple


def stage_one_events(composed):
    read = composed.read
    return tuple(ChainEvent(event.order, event.order, event.operation, event.query_index,
                            event.source_event_id, event.source_event_id, read.source_name,
                            event.source_position, event.anchor, event.target_name,
                            event.target_position, event.mapping_strand, event.block_id,
                            event.column, event.owner_source, None, None, None)
                 for event in composed.events)


def transport_stage_two(stage1, groups, mapping):
    """Carry stage-one middle spans through the second mapping, in source path order."""
    index, gaps = build_source_index(mapping)
    children, child_of = [], {}
    by_source = {e.source_position: e for e in stage1 if e.source_position is not None}

    def emit(operation, query_index, source_event_id, parent_event_id, source_name,
             source_position, anchor, target_name, target_position, mapping_strand,
             block_id, column, owner_source):
        event = ChainEvent(len(children), len(children), operation, query_index,
                           source_event_id, parent_event_id, source_name, source_position,
                           anchor, target_name, target_position, mapping_strand, block_id,
                           column, owner_source, None, None, None)
        children.append(event)
        if parent_event_id is not None:
            child_of[parent_event_id] = event
        return event

    for group in groups:
        bearing = [e for e in group if e.target_position is not None]
        for event in bearing:
            if (event.target_name, event.target_position) not in index:
                raise ValueError("middle position lacks fixed mapping coverage")
        positions = [e.target_position for e in bearing]
        start, end = min(positions), max(positions) + 1
        strand = bearing[0].mapping_strand
        span_gaps = select_target_gap_events(gaps, bearing[0].target_name, start, end)
        for event in group:
            if event.target_position is not None:
                anchor = event.target_position + (1 if strand == -1 else 0)
                for column in span_gaps.get(anchor, ()):
                    emit("D", None, None, None, column.source_name, None, column.source_anchor,
                         column.target_name, column.target_position, strand * column.strand,
                         column.block_id, column.column, None)
                column = index[(event.target_name, event.target_position)]
                if event.operation == "M":
                    operation = "M" if column.target_position is not None else "I"
                else:
                    operation = "D" if column.target_position is not None else None
                emit(operation, event.query_index, event.source_event_id, event.event_id,
                     event.target_name, event.target_position, event.target_position,
                     column.target_name, column.target_position if operation != "I" else None,
                     event.mapping_strand * column.strand, column.block_id, column.column,
                     event.target_position)
            elif event.operation == "I":
                right = next((e for e in group if e.order > event.order
                              and e.target_position is not None), None)
                left = next((e for e in reversed(group) if e.order < event.order
                             and e.target_position is not None), None)
                neighbor = right if right is not None else left
                column = index[(neighbor.target_name, neighbor.target_position)]
                if right is not None:
                    anchor = right.target_position + (1 if strand == -1 else 0)
                else:
                    anchor = left.target_position + (1 if strand == 1 else 0)
                owner = by_source.get(event.owner_source)
                owner_source = (owner.target_position
                                if owner is not None and owner.target_position is not None
                                else neighbor.target_position)
                emit("I", event.query_index, event.source_event_id, event.event_id,
                     event.target_name, None, anchor, column.target_name, None,
                     strand * column.strand, column.block_id, column.column, owner_source)
    return children, child_of


def compose_chain_read(read, first_mapping, second_mapping):
    composed = compose_read(read, first_mapping)
    stage1 = stage_one_events(composed)
    groups, assigned = group_projected_events(stage1)
    children, child_of = transport_stage_two(stage1, groups, second_mapping)
    final = segment_composed_read(ComposedRead(read, composed.source_events, tuple(children)))
    delivered = {}
    for segment in final:
        for event in segment.events:
            delivered[event.event_id] = segment.segment_index
    stage2 = tuple(replace(event,
                           fate=("elided" if event.operation is None else
                                 "delivered" if event.event_id in delivered else "undelivered"),
                           segment_index=(delivered.get(event.event_id)
                                          if event.operation is not None else None))
                   for event in children)
    by_id = {event.event_id: event for event in stage2}
    segments = tuple(replace(segment, events=tuple(by_id[event.event_id] for event in segment.events))
                     for segment in final)
    stage1_final = tuple(replace(event,
                                 fate=("continued" if event.event_id in child_of else
                                       "elided" if event.operation is None else "undelivered"),
                                 next_event_id=(child_of[event.event_id].event_id
                                                if event.event_id in child_of else None))
                         for event in stage1)
    return ChainProjection(read, composed.source_events, stage1_final, stage2, segments)
