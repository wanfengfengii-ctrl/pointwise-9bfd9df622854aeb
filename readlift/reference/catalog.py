from dataclasses import asdict
from readlift.reference.sequences import ReferenceVersion, reference_digest
from readlift.reference.blocks import MappingVersion, mapping_digest
from readlift.reference.validation import validate_reference_definition, validate_mapping_definition, identifier


class ReferenceCatalog:
    def __init__(self, store):
        self.store = store

    def _register(self, namespace, identity, data):
        current = self.store.read_snapshot()[namespace].get(identity)
        if current is not None:
            if current != data:
                raise ValueError("identity conflicts with registered content")
            return current
        def insert(state):
            state[namespace][identity] = data
            return data
        return self.store.atomic_update(insert)

    def register_reference(self, version_id, sequences):
        sequences = validate_reference_definition(version_id, sequences)
        value = ReferenceVersion(version_id, tuple(sequences.items()), reference_digest(sequences))
        return self._register("references", version_id, value.to_data())

    def get_reference(self, version_id):
        identifier(version_id)
        return ReferenceVersion.from_data(self.store.read_snapshot()["references"][version_id])

    def register_mapping(self, mapping_id, source_version, target_version, blocks):
        source, target = self.get_reference(source_version), self.get_reference(target_version)
        ordered, summary = validate_mapping_definition(mapping_id, source, target, blocks)
        body = {"mapping_id": mapping_id, "source_version": source_version,
                "target_version": target_version, "blocks": [asdict(b) for b in ordered],
                "summary": summary}
        value = MappingVersion(mapping_id, source_version, target_version, ordered, summary, mapping_digest(body))
        return self._register("mappings", mapping_id, value.to_data())

    def get_mapping(self, mapping_id):
        identifier(mapping_id)
        return MappingVersion.from_data(self.store.read_snapshot()["mappings"][mapping_id])
