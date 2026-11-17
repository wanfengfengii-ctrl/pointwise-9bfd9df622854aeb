from dataclasses import asdict
from readlift.archive.schema import PublicationReceipt, validate_committed_result
from readlift.archive.codec import content_digest, detached_data
from readlift.assembly.audit import audit_batch


def build_result_identity(binding):
    return "result-" + content_digest(binding)


class ResultPublisher:
    def __init__(self, store):
        self.store = store

    def find_completed_result(self, job_id):
        state = self.store.read_snapshot()
        receipt = state["receipts"].get(job_id)
        return None if receipt is None else detached_data(state["results"][receipt["result_id"]])

    def publish_result(self, binding, batch, projections, assembled, records, target_reference):
        data = asdict(binding)
        existing = self.find_completed_result(binding.job_id)
        if existing is not None:
            return existing
        audit = audit_batch(batch, projections, assembled, records, binding.pairing, target_reference)
        segments = [asdict(s) for pair in batch.pairs for read in pair.reads
                    for s in assembled[read.read_id].segments]
        # JSON roundtrip is intentional: durable views have only JSON containers.
        from json import loads
        from readlift.archive.codec import canonical_json_bytes
        body = loads(canonical_json_bytes({"binding": data, "segments": segments,
                                          "records": records, "audit": audit.to_data()}))
        result_id = build_result_identity(data)
        receipt = asdict(PublicationReceipt(binding.job_id, result_id, content_digest(body)))
        result = {"result_id": result_id, **body, "receipt": receipt}
        validate_committed_result(result)
        def commit(state):
            state["results"][result_id] = result
            self.store.checkpoint("candidate_result", detached_data(state))
            state["receipts"][binding.job_id] = receipt
            self.store.checkpoint("candidate_receipt", detached_data(state))
            return result
        return self.store.atomic_update(commit)
