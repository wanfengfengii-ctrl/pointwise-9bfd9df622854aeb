from dataclasses import dataclass, asdict
from readlift.archive.codec import content_digest


@dataclass(frozen=True)
class AlignmentBlock:
    block_id: str
    source_name: str
    source_start: int
    source_end: int
    target_name: str
    target_start: int
    target_end: int
    strand: int
    source_aligned: str
    target_aligned: str


@dataclass(frozen=True)
class MappingColumn:
    block_id: str
    column: int
    source_name: str
    source_position: int | None
    source_anchor: int
    target_name: str
    target_position: int | None
    strand: int


@dataclass(frozen=True)
class MappingVersion:
    mapping_id: str
    source_version: str
    target_version: str
    blocks: tuple
    summary: dict
    digest: str

    def to_data(self):
        result = asdict(self)
        result["blocks"] = [asdict(block) for block in self.blocks]
        return result

    @classmethod
    def from_data(cls, data):
        return cls(data["mapping_id"], data["source_version"], data["target_version"],
                   tuple(AlignmentBlock(**b) for b in data["blocks"]), data["summary"], data["digest"])


def expand_block(block):
    source = block.source_start
    target = block.target_start if block.strand == 1 else block.target_end - 1
    for number, (sbase, tbase) in enumerate(zip(block.source_aligned, block.target_aligned)):
        yield MappingColumn(block.block_id, number, block.source_name,
                            source if sbase != "-" else None, source,
                            block.target_name, target if tbase != "-" else None, block.strand)
        source += sbase != "-"
        if tbase != "-":
            target += block.strand


def build_source_index(mapping):
    index, gaps = {}, {}
    for block in mapping.blocks:
        for column in expand_block(block):
            if column.source_position is None:
                gaps.setdefault((column.source_name, column.source_anchor), []).append(column)
            else:
                key = (column.source_name, column.source_position)
                if key in index:
                    raise ValueError("ambiguous source mapping")
                index[key] = column
    return index, gaps


def mapping_digest(data):
    return content_digest(data)
