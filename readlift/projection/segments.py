from dataclasses import dataclass
from readlift.intake.operations import Operation, normalize_operations
from readlift.projection.orientation import compose_strand


@dataclass(frozen=True)
class ProjectedSegment:
    read_id: str
    segment_index: int
    source_order: int
    target_name: str
    target_start: int
    target_end: int
    mapping_strand: int
    strand: int
    query_start: int
    query_end: int
    events: tuple


def segment_consumption(segment):
    return {"query": sum(e.operation in ("M", "I") for e in segment.events),
            "reference": sum(e.operation in ("M", "D") for e in segment.events)}


def validate_segment_partition(segments):
    intervals = sorted((s.query_start, s.query_end) for s in segments)
    if any(a[1] > b[0] for a, b in zip(intervals, intervals[1:])):
        raise ValueError("overlapping segment queries")
    for segment in segments:
        counts = segment_consumption(segment)
        queries = [e.query_index for e in segment.events if e.query_index is not None]
        targets = [e.target_position for e in segment.events if e.target_position is not None]
        if (not queries or not targets or len(queries) != len(set(queries))
                or sorted(queries) != list(range(segment.query_start, segment.query_end))
                or counts["reference"] != segment.target_end - segment.target_start
                or counts["query"] != segment.query_end - segment.query_start):
            raise ValueError("segment consumption is not closed")


def group_projected_events(events):
    groups, assigned = [], {}
    previous = None
    for event in events:
        if event.target_position is None:
            continue
        continuous = (previous is not None and previous.target_name == event.target_name
                      and previous.mapping_strand == event.mapping_strand
                      and event.target_position == previous.target_position + event.mapping_strand)
        if not continuous:
            groups.append([])
        groups[-1].append(event)
        assigned[event.order] = len(groups) - 1
        previous = event
    for event in events:
        if event.target_position is not None or event.source_position is None:
            continue
        candidates = [e for e in events if e.target_position is not None and e.block_id == event.block_id]
        following = [e for e in candidates if e.order > event.order]
        chosen = following[0] if following else (candidates[-1] if candidates else None)
        if chosen is not None:
            assigned[event.order] = assigned[chosen.order]
            groups[assigned[event.order]].append(event)
    source_owners = {e.source_position: e for e in events if e.source_position is not None}
    for event in events:
        if event.source_position is not None or event.operation != "I":
            continue
        owner = source_owners[event.owner_source]
        if owner.order in assigned:
            assigned[event.order] = assigned[owner.order]
            groups[assigned[event.order]].append(event)
    for group in groups:
        group.sort(key=lambda e: e.order)
    return groups, assigned


def segment_composed_read(composed):
    groups, assigned = group_projected_events(composed.events)
    segments = []
    for group in groups:
        queries = [e.query_index for e in group if e.query_index is not None]
        if not queries:
            continue
        targets = [e.target_position for e in group if e.target_position is not None]
        first = group[0]
        segments.append(ProjectedSegment(composed.read.read_id, len(segments), first.order,
                        first.target_name, min(targets), max(targets) + 1, first.mapping_strand,
                        compose_strand(composed.read.source_strand, first.mapping_strand),
                        min(queries), max(queries) + 1, tuple(group)))
    if not any(e.operation == "M" for s in segments for e in s.events):
        raise ValueError("read has no deliverable M segment")
    validate_segment_partition(segments)
    return tuple(segments)
