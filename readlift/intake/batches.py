from dataclasses import dataclass
from readlift.intake.records import source_pair_from_data, source_pair_to_data
from readlift.intake.validation import validate_pair_batch
from readlift.reference.validation import identifier
from readlift.archive.codec import content_digest


def batch_digest(data):
    return content_digest(data)


@dataclass(frozen=True)
class PairedBatch:
    batch_id: str
    pairs: tuple
    digest: str

    def to_data(self):
        return {"batch_id": self.batch_id, "pairs": [source_pair_to_data(p) for p in self.pairs],
                "digest": self.digest}

    @classmethod
    def from_data(cls, data):
        return cls(data["batch_id"], tuple(source_pair_from_data(p) for p in data["pairs"]), data["digest"])


class BatchCatalog:
    def __init__(self, store, references):
        self.store, self.references = store, references

    def ingest_pairs(self, batch_id, pairs):
        identifier(batch_id)
        if not isinstance(pairs, (list, tuple)):
            raise ValueError("pairs must be a finite list")
        pairs = tuple(sorted((source_pair_from_data(p) for p in pairs), key=lambda p: p.pair_id))
        validate_pair_batch(pairs, self.references)
        body = {"batch_id": batch_id, "pairs": [source_pair_to_data(p) for p in pairs]}
        data = PairedBatch(batch_id, pairs, batch_digest(body)).to_data()
        current = self.store.read_snapshot()["batches"].get(batch_id)
        if current is not None:
            if current != data:
                raise ValueError("batch identity conflict")
            return current
        def insert(state):
            state["batches"][batch_id] = data
            return data
        return self.store.atomic_update(insert)

    def get_batch(self, batch_id):
        identifier(batch_id)
        return PairedBatch.from_data(self.store.read_snapshot()["batches"][batch_id])
