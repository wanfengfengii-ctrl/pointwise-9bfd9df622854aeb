from dataclasses import dataclass

STORE_SCHEMA_VERSION = 1
NAMESPACES = ("references", "mappings", "batches", "jobs", "results", "receipts")


@dataclass(frozen=True)
class SnapshotEnvelope:
    version: int
    state: dict


@dataclass(frozen=True)
class PublicationReceipt:
    job_id: str
    result_id: str
    payload_digest: str


def empty_snapshot():
    return {name: {} for name in NAMESPACES}


def validate_committed_result(result):
    required = {"result_id", "binding", "segments", "records", "audit", "receipt"}
    if not isinstance(result, dict) or set(result) != required:
        raise ValueError("invalid committed result shape")
    if (not isinstance(result["binding"], dict) or not isinstance(result["segments"], list)
            or not isinstance(result["records"], list) or not isinstance(result["audit"], dict)
            or not isinstance(result["receipt"], dict)):
        raise ValueError("invalid committed result containers")
    receipt = result["receipt"]
    if (set(receipt) != {"job_id", "result_id", "payload_digest"}
            or receipt["job_id"] != result["binding"].get("job_id")
            or receipt["result_id"] != result["result_id"]):
        raise ValueError("result receipt does not close")


def validate_snapshot_structure(state):
    if not isinstance(state, dict) or set(state) != set(NAMESPACES):
        raise ValueError("invalid snapshot namespaces")
    for name in NAMESPACES:
        if not isinstance(state[name], dict) or any(not isinstance(k, str) or not k
                                                    or not isinstance(v, dict) for k, v in state[name].items()):
            raise ValueError("invalid snapshot directory")
    for key, reference in state["references"].items():
        if reference.get("version_id") != key or not isinstance(reference.get("sequences"), dict):
            raise ValueError("invalid stored reference")
    for key, mapping in state["mappings"].items():
        if (mapping.get("mapping_id") != key or mapping.get("source_version") not in state["references"]
                or mapping.get("target_version") not in state["references"]):
            raise ValueError("mapping reference does not close")
    for key, job in state["jobs"].items():
        if job.get("job_id") != key or job.get("batch_id") not in state["batches"]:
            raise ValueError("job binding does not close")
        if "mapping_ids" in job:
            versions = job.get("reference_versions")
            if (not isinstance(job["mapping_ids"], list) or len(job["mapping_ids"]) != 2
                    or any(m not in state["mappings"] for m in job["mapping_ids"])
                    or not isinstance(versions, list) or len(versions) != 3
                    or any(v not in state["references"] for v in versions)):
                raise ValueError("job binding does not close")
        elif (job.get("mapping_id") not in state["mappings"]
                or job.get("source_version") not in state["references"]
                or job.get("target_version") not in state["references"]):
            raise ValueError("job binding does not close")
    for result_id, result in state["results"].items():
        validate_committed_result(result)
        receipt = result["receipt"]
        job_id = receipt["job_id"]
        if (result_id != result["result_id"] or job_id not in state["jobs"]
                or result["binding"] != state["jobs"][job_id]
                or state["receipts"].get(job_id) != receipt):
            raise ValueError("orphaned committed result")
    for job_id, receipt in state["receipts"].items():
        result = state["results"].get(receipt.get("result_id"))
        if result is None or result["receipt"] != receipt or receipt["job_id"] != job_id:
            raise ValueError("orphaned completion receipt")
