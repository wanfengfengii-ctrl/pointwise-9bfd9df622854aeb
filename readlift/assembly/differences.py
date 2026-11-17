from dataclasses import dataclass
from readlift.intake.operations import query_consumption, reference_consumption


@dataclass(frozen=True)
class DifferenceTags:
    md: str
    nm: int


def validate_difference_consumption(sequence, target, operations):
    if query_consumption(operations) != len(sequence) or reference_consumption(operations) != len(target):
        raise ValueError("difference consumption mismatch")


def compute_difference_tags(sequence, target, operations):
    validate_difference_consumption(sequence, target, operations)
    q = t = matches = nm = 0
    md = []
    for operation in operations:
        count, code = operation.count, operation.code
        if code == "M":
            for _ in range(count):
                if sequence[q] == target[t]:
                    matches += 1
                else:
                    md.extend((str(matches), target[t]))
                    matches = 0
                    nm += 1
                q += 1
                t += 1
        elif code == "D":
            md.extend((str(matches), "^" + target[t:t + count]))
            matches = 0
            t += count
            nm += count
        elif code == "I":
            q += count
            nm += count
        elif code == "S":
            q += count
    md.append(str(matches))
    return DifferenceTags("".join(md), nm)
