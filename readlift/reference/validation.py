from readlift.reference.sequences import validate_bases, reference_slice, reverse_complement_bases
from readlift.reference.blocks import AlignmentBlock


def identifier(value):
    if not isinstance(value, str) or not value:
        raise ValueError("identity must be a nonempty string")
    return value


def validate_reference_definition(version_id, sequences):
    identifier(version_id)
    if not isinstance(sequences, dict) or not sequences:
        raise ValueError("reference needs named sequences")
    for name, bases in sequences.items():
        identifier(name)
        validate_bases(bases)
    return dict(sorted(sequences.items()))


def validate_block_consumption(block, source, target):
    identifier(block.block_id)
    identifier(block.source_name)
    identifier(block.target_name)
    if type(block.strand) is not int or block.strand not in (-1, 1):
        raise ValueError("mapping strand must be 1 or -1")
    a, b = block.source_aligned, block.target_aligned
    if not isinstance(a, str) or not isinstance(b, str) or not a or len(a) != len(b):
        raise ValueError("aligned rows must have equal positive length")
    if set(a + b) - set("ACGTN-") or any(x == y == "-" for x, y in zip(a, b)):
        raise ValueError("illegal aligned column")
    expected_source = reference_slice(source, block.source_name, block.source_start, block.source_end)
    expected_target = reference_slice(target, block.target_name, block.target_start, block.target_end)
    if block.strand == -1:
        expected_target = reverse_complement_bases(expected_target)
    if a.replace("-", "") != expected_source or b.replace("-", "") != expected_target:
        raise ValueError("aligned row disagrees with reference consumption")
    return {"source_bases": len(expected_source), "target_bases": len(expected_target),
            "columns": len(a), "source_only": b.count("-"), "target_only": a.count("-")}


def validate_block_intervals(blocks):
    for side in ("source", "target"):
        intervals = {}
        for block in blocks:
            name = getattr(block, side + "_name")
            intervals.setdefault(name, []).append((getattr(block, side + "_start"),
                                                   getattr(block, side + "_end")))
        for entries in intervals.values():
            entries.sort()
            if any(left[1] > right[0] for left, right in zip(entries, entries[1:])):
                raise ValueError("overlapping mapping blocks")


def validate_mapping_definition(mapping_id, source, target, blocks):
    identifier(mapping_id)
    if not isinstance(blocks, (list, tuple)):
        raise ValueError("blocks must be a finite list")
    try:
        blocks = tuple(b if isinstance(b, AlignmentBlock) else AlignmentBlock(**b) for b in blocks)
    except TypeError as exc:
        raise ValueError("invalid alignment block fields") from exc
    if len({b.block_id for b in blocks}) != len(blocks):
        raise ValueError("duplicate block identity")
    summaries = {b.block_id: validate_block_consumption(b, source, target) for b in blocks}
    validate_block_intervals(blocks)
    ordered = tuple(sorted(blocks, key=lambda b: (b.source_name, b.source_start, b.block_id)))
    return ordered, dict(sorted(summaries.items()))
