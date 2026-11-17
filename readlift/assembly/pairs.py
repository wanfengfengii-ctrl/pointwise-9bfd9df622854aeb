from dataclasses import dataclass, asdict
from readlift.projection.jobs import PairingConfig


@dataclass(frozen=True)
class PairFields:
    mate_target_name: str
    mate_target_start: int
    mate_strand: int
    template_length: int
    proper_pair: bool


def compute_template_lengths(left, right):
    if left.target_name != right.target_name:
        return 0, 0
    span = max(left.target_end, right.target_end) - min(left.target_start, right.target_start)
    first_left = (left.target_start, left.mate_index) < (right.target_start, right.mate_index)
    return (span, -span) if first_left else (-span, span)


def is_proper_pair(first, second, config):
    a, b = first.records[first.primary_index], second.records[second.primary_index]
    if len(first.records) != 1 or len(second.records) != 1 or a.target_name != b.target_name:
        return False
    if a.strand != config.mate1_strand or b.strand != config.mate2_strand:
        return False
    if config.order == "mate1_left" and a.target_start > b.target_start:
        return False
    if config.order == "mate2_left" and b.target_start > a.target_start:
        return False
    span = abs(compute_template_lengths(a, b)[0])
    return config.min_span <= span <= config.max_span


def assemble_pair_fields(first, second, config):
    if isinstance(config, dict):
        config = PairingConfig.from_data(config)
    a, b = first.records[first.primary_index], second.records[second.primary_index]
    ta, tb = compute_template_lengths(a, b)
    proper = is_proper_pair(first, second, config)
    return (PairFields(b.target_name, b.target_start, b.strand, ta, proper),
            PairFields(a.target_name, a.target_start, a.strand, tb, proper))


def attach_pair_fields(first, second, config):
    fields = assemble_pair_fields(first, second, config)
    records = []
    for assembled, field in zip((first, second), fields):
        for record in assembled.records:
            data = record.to_data()
            data.update(asdict(field))
            data["proper_pair"] = field.proper_pair and record.primary
            records.append(data)
    return records
