"""Helpers for cleaning up temporary full-screen capture sources."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Union

from ..utils.config import DATA_DIR
from ..utils.logger import get_logger

logger = get_logger(__name__)

PathLike = Union[str, Path]

_AUTO_CAPTURE_SOURCE_RE = re.compile(r"^trigger_\d{8}_\d{6}_\d{3,6}\.png$")


def _resolve_template_path(path_value: PathLike) -> Path:
    path = Path(path_value)
    if path.is_absolute():
        return path
    if path.parent == Path("."):
        return DATA_DIR / "templates" / path
    return DATA_DIR / path


def is_auto_capture_source_path(path_value: PathLike) -> bool:
    """Return True only for uncropped full-screen trigger captures."""
    try:
        path = _resolve_template_path(path_value)
        templates_dir = (DATA_DIR / "templates").resolve()
        return path.parent.resolve() == templates_dir and bool(_AUTO_CAPTURE_SOURCE_RE.fullmatch(path.name))
    except (OSError, RuntimeError, ValueError):
        return False


def remove_auto_capture_source_after_crop(source_path: PathLike, cropped_path: PathLike) -> bool:
    """Delete the temporary full capture only after a cropped file was saved."""
    if not source_path or not cropped_path:
        return False

    try:
        source = _resolve_template_path(source_path)
        cropped = _resolve_template_path(cropped_path)

        if source.resolve() == cropped.resolve():
            return False
        if not cropped.exists():
            return False
        if not is_auto_capture_source_path(source):
            return False
        if not source.exists():
            return False

        source.unlink()
        logger.info(f"크롭 저장 후 전체 캡처 원본 삭제: {source}")
        return True
    except (OSError, RuntimeError, ValueError) as exc:
        logger.warning(f"크롭 원본 정리 실패: source={source_path}, crop={cropped_path}, error={exc}")
        return False
