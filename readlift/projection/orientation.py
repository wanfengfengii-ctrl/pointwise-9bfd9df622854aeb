from dataclasses import dataclass
from readlift.reference.sequences import reverse_complement_bases
from readlift.intake.operations import normalize_operations


@dataclass(frozen=True)
class OrientedQuery:
    sequence: str
    quality: str
    strand: int


def orient_query(read, strand):
    if strand not in (-1, 1):
        raise ValueError("invalid query direction")
    return OrientedQuery(read.sequence if strand == 1 else reverse_complement_bases(read.sequence),
                         read.quality if strand == 1 else read.quality[::-1], strand)


def compose_strand(source_strand, mapping_strand):
    return source_strand * mapping_strand


def to_original_query_interval(start, end, length, source_strand):
    return (start, end) if source_strand == 1 else (length - end, length - start)


def to_delivery_query_interval(start, end, length, delivery_strand):
    return to_original_query_interval(start, end, length, delivery_strand)


def orient_segment_operations(operations, mapping_strand):
    return normalize_operations(operations if mapping_strand == 1 else tuple(reversed(operations)))
