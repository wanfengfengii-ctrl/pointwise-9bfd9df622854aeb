import json
from readlift.results import ResultView, result_view_to_data


def iter_export_chunks(result):
    data = result_view_to_data(result) if isinstance(result, ResultView) else result
    encoder = json.JSONEncoder(sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    yield from encoder.iterencode(data)
    yield "\n"


def write_result_export(result, target):
    for chunk in iter_export_chunks(result):
        written = target.write(chunk)
        if written is not None and written != len(chunk):
            raise OSError("short export write")
