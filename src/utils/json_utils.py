from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Union


PathLike = Union[str, Path]
_WRITE_LOCK = threading.RLock()
_REPLACE_RETRY_DELAYS = (0.0, 0.05, 0.15, 0.35)


def load_json_file(path: PathLike) -> Any:
    """Load JSON using UTF-8 with BOM tolerance."""
    file_path = Path(path)
    with file_path.open("r", encoding="utf-8-sig") as f:
        return json.load(f)


def dump_json_file(path: PathLike, data: Any, *, ensure_ascii: bool = False, indent: int = 2) -> None:
    """Atomically write JSON and verify the durable file contents.

    The temporary file lives beside the destination so ``os.replace`` stays on
    the same filesystem.  A successful return means the final file was flushed,
    replaced, and read back byte-for-byte.
    """
    file_path = Path(path)
    payload = json.dumps(data, ensure_ascii=ensure_ascii, indent=indent).encode("utf-8")
    file_path.parent.mkdir(parents=True, exist_ok=True)

    temp_path: Path | None = None
    with _WRITE_LOCK:
        try:
            fd, temp_name = tempfile.mkstemp(
                dir=str(file_path.parent),
                prefix=f".{file_path.name}.",
                suffix=".tmp",
            )
            temp_path = Path(temp_name)
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())

            last_error: OSError | None = None
            for delay in _REPLACE_RETRY_DELAYS:
                if delay:
                    time.sleep(delay)
                try:
                    os.replace(temp_path, file_path)
                    temp_path = None
                    break
                except PermissionError as exc:
                    last_error = exc
            else:
                assert last_error is not None
                raise last_error

            if file_path.read_bytes() != payload:
                raise OSError(f"JSON save verification failed: {file_path}")
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass
