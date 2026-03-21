from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Union


PathLike = Union[str, Path]


def load_json_file(path: PathLike) -> Any:
    """Load JSON using UTF-8 with BOM tolerance."""
    file_path = Path(path)
    with file_path.open("r", encoding="utf-8-sig") as f:
        return json.load(f)


def dump_json_file(path: PathLike, data: Any, *, ensure_ascii: bool = False, indent: int = 2) -> None:
    """Write JSON as plain UTF-8 to avoid reintroducing BOM."""
    file_path = Path(path)
    with file_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=ensure_ascii, indent=indent)

