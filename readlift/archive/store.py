import os
import tempfile
from pathlib import Path
from readlift.archive.schema import empty_snapshot
from readlift.archive.codec import encode_snapshot, decode_snapshot, detached_data


class SnapshotStore:
    def __init__(self, path):
        self.path = Path(path)
        self.closed = False
        if self.path.exists():
            self._state = decode_snapshot(self.path.read_bytes())
        else:
            self._state = empty_snapshot()
            self._commit(self._state)

    @classmethod
    def open(cls, path):
        return cls(path)

    def _ensure_open(self):
        if self.closed:
            raise ValueError("store is closed")

    def checkpoint(self, stage, candidate):
        self._ensure_open()
        if stage not in ("candidate_result", "candidate_receipt", "before_replace"):
            raise ValueError("unknown commit stage")

    def read_snapshot(self):
        self._ensure_open()
        return detached_data(self._state)

    def _commit(self, candidate):
        payload = encode_snapshot(candidate)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.path.parent, prefix="." + self.path.name + "-",
                                              suffix=".tmp", delete=False) as stream:
                temporary = stream.name
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            self.checkpoint("before_replace", detached_data(candidate))
            os.replace(temporary, self.path)
        except BaseException:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass
            raise
        self._state = candidate

    def atomic_update(self, update):
        self._ensure_open()
        candidate = detached_data(self._state)
        value = update(candidate)
        detached_value = detached_data(value)
        self._commit(detached_data(candidate))
        return detached_value

    def close(self):
        self.closed = True
