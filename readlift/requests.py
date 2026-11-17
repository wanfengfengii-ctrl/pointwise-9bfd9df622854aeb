from copy import deepcopy
from readlift.reference.validation import identifier
from readlift.intake.records import source_pair_from_data, source_pair_to_data
from readlift.projection.jobs import PairingConfig
from dataclasses import asdict


def normalize_reference_request(version_id, sequences):
    identifier(version_id)
    if not isinstance(sequences, dict):
        raise ValueError("sequences must be an object")
    return version_id, deepcopy(sequences)


def normalize_mapping_request(mapping_id, source_version, target_version, blocks):
    for value in (mapping_id, source_version, target_version):
        identifier(value)
    if not isinstance(blocks, (list, tuple)):
        raise ValueError("blocks must be a finite list")
    return mapping_id, source_version, target_version, deepcopy(list(blocks))


def normalize_pairs_request(batch_id, pairs):
    identifier(batch_id)
    if not isinstance(pairs, (list, tuple)):
        raise ValueError("pairs must be a finite list")
    return batch_id, [source_pair_to_data(source_pair_from_data(p)) for p in pairs]


def normalize_job_request(batch_id, mapping_id, pairing):
    identifier(batch_id)
    identifier(mapping_id)
    if not isinstance(pairing, dict):
        raise ValueError("pairing must be an object")
    return batch_id, mapping_id, asdict(PairingConfig.from_data(deepcopy(pairing)))
