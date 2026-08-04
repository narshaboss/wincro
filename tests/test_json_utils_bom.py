import json
from pathlib import Path

import pytest

import src.utils.json_utils as json_utils

from src.utils.json_utils import dump_json_file, load_json_file


def test_load_json_file_accepts_utf8_bom(tmp_path: Path):
    plan_file = tmp_path / "plan.json"
    payload = {"name": "원각공장", "initial_rules": [], "game_modes": {}}
    plan_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8-sig")

    data = load_json_file(plan_file)

    assert data["name"] == "원각공장"


def test_dump_json_file_writes_plain_utf8(tmp_path: Path):
    plan_file = tmp_path / "plan.json"
    dump_json_file(plan_file, {"name": "원각공장"}, ensure_ascii=False, indent=2)

    raw = plan_file.read_bytes()

    assert not raw.startswith(b"\xef\xbb\xbf")


def test_dump_json_file_retries_atomic_replace(monkeypatch, tmp_path: Path):
    target = tmp_path / "config.json"
    target.write_text('{"old": true}', encoding="utf-8")
    real_replace = json_utils.os.replace
    attempts = {"count": 0}

    def flaky_replace(source, destination):
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise PermissionError(5, "busy", str(destination))
        return real_replace(source, destination)

    monkeypatch.setattr(json_utils.os, "replace", flaky_replace)
    monkeypatch.setattr(json_utils.time, "sleep", lambda _delay: None)

    dump_json_file(target, {"new": "저장"})

    assert attempts["count"] == 3
    assert load_json_file(target) == {"new": "저장"}
    assert list(tmp_path.glob(".config.json.*.tmp")) == []


def test_dump_json_file_failure_preserves_previous_file(monkeypatch, tmp_path: Path):
    target = tmp_path / "plan.json"
    original = b'{"stable": true}'
    target.write_bytes(original)

    def blocked_replace(_source, destination):
        raise PermissionError(5, "busy", str(destination))

    monkeypatch.setattr(json_utils.os, "replace", blocked_replace)
    monkeypatch.setattr(json_utils.time, "sleep", lambda _delay: None)

    with pytest.raises(PermissionError):
        dump_json_file(target, {"stable": False})

    assert target.read_bytes() == original
    assert list(tmp_path.glob(".plan.json.*.tmp")) == []
