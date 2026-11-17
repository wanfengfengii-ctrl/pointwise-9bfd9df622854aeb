import copy
import hashlib
import json
from readlift.archive.schema import STORE_SCHEMA_VERSION, validate_snapshot_structure


def canonical_json_bytes(data):
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("utf-8")


def content_digest(data):
    return hashlib.sha256(canonical_json_bytes(data)).hexdigest()


def detached_data(data):
    return copy.deepcopy(data)


def encode_snapshot(state):
    validate_snapshot_structure(state)
    return canonical_json_bytes({"version": STORE_SCHEMA_VERSION, "state": state,
                                 "digest": content_digest(state)})


def decode_snapshot(payload):
    try:
        envelope = json.loads(payload)
        if (set(envelope) != {"version", "state", "digest"}
                or type(envelope["version"]) is not int or envelope["version"] != STORE_SCHEMA_VERSION
                or envelope["digest"] != content_digest(envelope["state"])):
            raise ValueError("invalid snapshot envelope")
        state = envelope["state"]
        validate_snapshot_structure(state)
        for result in state["results"].values():
            body = {k: result[k] for k in ("binding", "segments", "records", "audit")}
            if content_digest(body) != result["receipt"]["payload_digest"]:
                raise ValueError("committed result checksum mismatch")
        return state
    except (TypeError, KeyError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("corrupt snapshot") from exc
