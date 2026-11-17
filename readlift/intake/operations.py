from dataclasses import dataclass
import re


@dataclass(frozen=True)
class Operation:
    count: int
    code: str


@dataclass(frozen=True)
class SourceEvent:
    event_id: int
    operation: str
    query_position: int | None
    source_position: int | None
    anchor: int


def normalize_operations(operations):
    result = []
    for operation in operations:
        count, code = operation.count, operation.code
        if type(count) is not int or count <= 0 or code not in ("M", "I", "D", "S"):
            raise ValueError("invalid operation")
        if result and result[-1].code == code:
            result[-1] = Operation(result[-1].count + count, code)
        else:
            result.append(Operation(count, code))
    if any(op.code == "S" and i not in (0, len(result) - 1) for i, op in enumerate(result)):
        raise ValueError("internal soft clipping")
    return tuple(result)


def parse_operations(text):
    if not isinstance(text, str) or not text:
        raise ValueError("operations required")
    pieces = list(re.finditer(r"([1-9][0-9]*)([MIDS])", text))
    if "".join(m.group() for m in pieces) != text:
        raise ValueError("invalid operation text")
    return normalize_operations(Operation(int(m[1]), m[2]) for m in pieces)


def format_operations(operations):
    return "".join(str(op.count) + op.code for op in normalize_operations(operations))


def query_consumption(operations):
    return sum(op.count for op in operations if op.code in "MIS")


def reference_consumption(operations):
    return sum(op.count for op in operations if op.code in "MD")


def expand_source_alignment(read):
    q, s, serial = 0, read.start, 0
    for op in parse_operations(read.operations):
        for _ in range(op.count):
            yield SourceEvent(serial, op.code, q if op.code in "MIS" else None,
                              s if op.code in "MD" else None, s)
            serial += 1
            q += op.code in "MIS"
            s += op.code in "MD"
