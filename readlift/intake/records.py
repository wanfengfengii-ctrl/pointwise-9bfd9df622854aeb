from dataclasses import dataclass, asdict
from readlift.intake.operations import parse_operations, format_operations
from readlift.reference.validation import identifier


@dataclass(frozen=True)
class SourceRead:
    read_id: str
    pair_id: str
    mate_index: int
    source_version: str
    source_name: str
    start: int
    source_strand: int
    operations: str
    sequence: str
    quality: str


@dataclass(frozen=True)
class SourcePair:
    pair_id: str
    reads: tuple


def source_read_from_data(data):
    if not isinstance(data, dict):
        raise ValueError("read must be an object")
    try:
        read = SourceRead(**data)
    except TypeError as exc:
        raise ValueError("invalid read fields") from exc
    if type(read.mate_index) is not int or read.mate_index not in (1, 2):
        raise ValueError("mate_index must be 1 or 2")
    return SourceRead(**{**asdict(read), "operations": format_operations(parse_operations(read.operations))})


def source_pair_from_data(data):
    if not isinstance(data, dict) or set(data) != {"pair_id", "reads"}:
        raise ValueError("pair needs pair_id and reads")
    identifier(data["pair_id"])
    if not isinstance(data["reads"], (list, tuple)):
        raise ValueError("reads must be a finite list")
    return SourcePair(data["pair_id"], tuple(sorted(
        (source_read_from_data(r) for r in data["reads"]), key=lambda r: r.mate_index)))


def source_pair_to_data(pair):
    return {"pair_id": pair.pair_id, "reads": [asdict(r) for r in pair.reads]}
