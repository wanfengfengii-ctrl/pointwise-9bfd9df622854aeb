from readlift.reference.validation import identifier
from readlift.reference.sequences import validate_bases, reference_slice
from readlift.intake.operations import parse_operations, query_consumption, reference_consumption


def validate_source_read(read, references):
    for value in (read.read_id, read.pair_id, read.source_version, read.source_name):
        identifier(value)
    if type(read.mate_index) is not int or read.mate_index not in (1, 2):
        raise ValueError("mate_index must be 1 or 2")
    if type(read.source_strand) is not int or read.source_strand not in (-1, 1):
        raise ValueError("source_strand must be 1 or -1")
    if type(read.start) is not int or read.start < 0:
        raise ValueError("source start must be a nonnegative integer")
    validate_bases(read.sequence)
    if (not isinstance(read.quality, str) or len(read.quality) != len(read.sequence)
            or any(not 32 <= ord(c) <= 126 for c in read.quality)):
        raise ValueError("quality must be equal-length printable ASCII")
    operations = parse_operations(read.operations)
    if query_consumption(operations) != len(read.sequence) or reference_consumption(operations) <= 0:
        raise ValueError("invalid query or reference consumption")
    reference_slice(references.get_reference(read.source_version), read.source_name,
                    read.start, read.start + reference_consumption(operations))


def validate_source_pair(pair, references):
    identifier(pair.pair_id)
    if len(pair.reads) != 2 or [r.mate_index for r in pair.reads] != [1, 2]:
        raise ValueError("pair must contain mates 1 and 2")
    for read in pair.reads:
        if read.pair_id != pair.pair_id:
            raise ValueError("pair identity mismatch")
        validate_source_read(read, references)


def validate_pair_batch(pairs, references):
    seen_pairs, seen_reads = set(), set()
    for pair in pairs:
        validate_source_pair(pair, references)
        if pair.pair_id in seen_pairs:
            raise ValueError("duplicate pair")
        seen_pairs.add(pair.pair_id)
        for read in pair.reads:
            if read.read_id in seen_reads:
                raise ValueError("duplicate read")
            seen_reads.add(read.read_id)
