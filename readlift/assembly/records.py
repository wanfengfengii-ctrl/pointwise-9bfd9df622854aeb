from dataclasses import dataclass, asdict
from readlift.intake.operations import Operation, normalize_operations, format_operations
from readlift.projection.orientation import orient_query, to_delivery_query_interval, orient_segment_operations
from readlift.assembly.differences import compute_difference_tags
from readlift.reference.sequences import reference_slice


@dataclass(frozen=True)
class AlignmentRecord:
    read_id: str
    pair_id: str
    mate_index: int
    segment_index: int
    target_name: str
    target_start: int
    target_end: int
    strand: int
    query_start: int
    query_end: int
    sequence: str
    quality: str
    operations: str
    md: str
    nm: int
    primary: bool
    supplementary: bool

    def to_data(self):
        return asdict(self)


@dataclass(frozen=True)
class AssembledRead:
    read: object
    segments: tuple
    records: tuple
    primary_index: int


def select_primary_segment(segments):
    return min(range(len(segments)),
               key=lambda i: (-(segments[i].query_end - segments[i].query_start), segments[i].source_order))


def build_soft_clipped_operations(segment, read):
    core = normalize_operations(Operation(1, e.operation) for e in segment.events if e.operation)
    core = orient_segment_operations(core, segment.mapping_strand)
    start, end = to_delivery_query_interval(segment.query_start, segment.query_end,
                                           len(read.sequence), segment.strand)
    return normalize_operations(
        ([Operation(start, "S")] if start else []) + list(core)
        + ([Operation(len(read.sequence) - end, "S")] if end < len(read.sequence) else []))


def assemble_read(read, segments, target_reference):
    primary = select_primary_segment(segments)
    records = []
    for i, segment in enumerate(segments):
        query = orient_query(read, segment.strand)
        operations = build_soft_clipped_operations(segment, read)
        target = reference_slice(target_reference, segment.target_name, segment.target_start, segment.target_end)
        tags = compute_difference_tags(query.sequence, target, operations)
        records.append(AlignmentRecord(read.read_id, read.pair_id, read.mate_index, i,
                       segment.target_name, segment.target_start, segment.target_end, segment.strand,
                       segment.query_start, segment.query_end, query.sequence, query.quality,
                       format_operations(operations), tags.md, tags.nm, i == primary, i != primary))
    return AssembledRead(read, segments, tuple(records), primary)
