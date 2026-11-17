from dataclasses import dataclass
from readlift.archive.codec import detached_data
from readlift.archive.schema import validate_committed_result
from readlift.reference.validation import identifier


@dataclass(frozen=True)
class ResultView:
    result_id: str
    binding: dict
    segments: list
    records: list
    audit: dict
    receipt: dict

    def to_data(self):
        return result_view_to_data(self)


def read_committed_result(store, result_id):
    identifier(result_id)
    state = store.read_snapshot()
    result = state["results"][result_id]
    validate_committed_result(result)
    job_id = result["binding"]["job_id"]
    if state["receipts"].get(job_id) != result["receipt"]:
        raise KeyError(result_id)
    return ResultView(**detached_data(result))


def result_view_to_data(view):
    return detached_data({name: getattr(view, name) for name in
                          ("result_id", "binding", "segments", "records", "audit", "receipt")})
