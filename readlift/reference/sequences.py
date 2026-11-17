from dataclasses import dataclass
from hashlib import sha256
import json


def validate_bases(value):
    if not isinstance(value, str) or not value or set(value) - set("ACGTN"):
        raise ValueError("expected nonempty uppercase ACGTN")
    return value


def reverse_complement_bases(value):
    return value.translate(str.maketrans("ACGTN", "TGCAN"))[::-1]


def reference_digest(sequences):
    return sha256(json.dumps(sequences, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class ReferenceVersion:
    version_id: str
    sequences: tuple
    digest: str

    def to_data(self):
        return {"version_id": self.version_id, "sequences": dict(self.sequences), "digest": self.digest}

    @classmethod
    def from_data(cls, data):
        return cls(data["version_id"], tuple(sorted(data["sequences"].items())), data["digest"])


def reference_slice(reference, name, start, end):
    sequences = dict(reference.sequences) if isinstance(reference, ReferenceVersion) else reference["sequences"]
    sequence = sequences[name]
    if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(sequence):
        raise ValueError("reference interval out of bounds")
    return sequence[start:end]
