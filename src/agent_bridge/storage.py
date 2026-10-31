"""Durable drafts and no-clobber publication; never edits existing plans."""

import os
from pathlib import Path
import tempfile
import uuid

from .contracts import BridgeError, decode, encoded


def within(path, root):
    path, root = Path(path), Path(root).resolve()
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(root) or not resolved.is_file() or path.is_symlink():
        raise BridgeError("PATH_DENIED", "File is outside the selected data root")
    return resolved


def read_json(path):
    return decode(Path(path).read_bytes())


def write_json(path, value):
    from ..utils.json_utils import dump_json_file
    dump_json_file(path, value)
    if read_json(path) != value:
        raise BridgeError("SAVE_STATE_UNKNOWN", "Saved contents could not be verified")


def publish_bytes(root, filename, payload):
    """Install an immutable file without a check-then-replace race."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    target = root / filename
    fd, name = tempfile.mkstemp(prefix=".ai-publish-", suffix=".tmp", dir=root)
    candidate = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        # A hard link is atomic and fails if target exists, on Windows and POSIX.
        # Both names live in the same directory/volume. Never fall back to replace.
        os.link(candidate, target)
        if target.read_bytes() != payload:
            raise BridgeError("SAVE_STATE_UNKNOWN", "Published file needs reconciliation")
    finally:
        candidate.unlink(missing_ok=True)
    return target


def new_id(prefix):
    return prefix + uuid.uuid4().hex
