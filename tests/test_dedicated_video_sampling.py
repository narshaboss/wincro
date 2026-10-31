"""Offline media/screenshot simulations; no desktop capture or input."""

import ast
import json
from pathlib import Path
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import cv2
import numpy as np
import pytest

from src.player import dedicated_video_templates as bank
from src.player import rule_executor as runtime


ROOT = Path(__file__).resolve().parents[1]
CLIP = ROOT / "data/templates/template_video_20260703_154056_306.mp4"


@pytest.fixture(autouse=True)
def no_desktop(monkeypatch):
    bank._cache.clear()
    monkeypatch.setattr(runtime, "_grab_screen_bgr", Mock(side_effect=AssertionError("live capture forbidden")))
    monkeypatch.setattr(runtime, "get_input_controller", Mock(side_effect=AssertionError("input forbidden")))
    yield
    bank._cache.clear()


def read_clip():
    cap = cv2.VideoCapture(str(CLIP))
    frames = []
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            frames.append(frame)
    finally:
        cap.release()
    assert len(frames) == 199
    return frames


def native_coverage(frames, templates):
    scores = []
    for frame in frames:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        scores.append(max(float(cv2.matchTemplate(gray, t[0], cv2.TM_CCOEFF_NORMED)[0, 0]) for t in templates))
    return scores


def test_actual_clip_exposes_eight_frame_holes_and_adaptive_bank_covers_them():
    frames = read_clip()
    legacy = runtime._get_cached_video_template_frames(str(CLIP))
    improved = bank.get_dedicated_video_frames(CLIP)
    before, after = native_coverage(frames, legacy), native_coverage(frames, improved)
    assert len(legacy) == 8
    assert sum(s < .8 for s in before) == 20
    assert 8 < len(improved) <= bank.MAX_FRAMES
    assert min(after) >= .95
    assert all(not t[0].flags.writeable and not t[3].flags.writeable for t in improved)
    result = {"frames": len(frames), "legacy_templates": len(legacy), "new_templates": len(improved),
              "legacy_native_misses": sum(s < .8 for s in before), "new_native_misses": sum(s < .8 for s in after),
              "legacy_minimum_score": min(before), "new_minimum_score": min(after)}
    path = ROOT / "artifacts/window_control/dedicated_video_metrics.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2), encoding="utf-8")


def test_actual_clip_all_phases_use_real_runtime_with_original_color_checks(monkeypatch):
    frames = read_clip()
    current = [frames[0]]
    monkeypatch.setattr(runtime, "_grab_screen_bgr", lambda: current[0])
    executor = runtime.RuleExecutor()
    for frame in frames:
        current[0] = frame
        found = executor._find_image_on_screen(str(CLIP), .8, verify_color=True, verify_brightness=True,
                                               dedicated_video=True)
        assert found is not None
    assert len(runtime._get_cached_video_template_frames(str(CLIP))) == 8


@pytest.mark.parametrize("kind", ["png", "legacy_video", "video_action_png"])
def test_other_image_and_video_paths_never_use_new_loader(monkeypatch, tmp_path, kind):
    file = tmp_path / ("still.png" if kind != "legacy_video" else "condition.mp4")
    file.write_bytes(b"fixture")
    frame = np.random.default_rng(9).integers(0, 255, (20, 24, 3), dtype=np.uint8)
    template = (cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), 20, 24, frame, "legacy")
    old = Mock(return_value=[template])
    monkeypatch.setattr(runtime, "_get_cached_template_variants", old)
    forbidden = Mock(side_effect=AssertionError("other action used new loader"))
    monkeypatch.setattr(bank, "get_dedicated_video_frames", forbidden)
    monkeypatch.setattr(runtime, "_grab_screen_bgr", lambda: frame)
    executor = runtime.RuleExecutor()
    assert executor._find_image_on_screen(str(file), .99, dedicated_video=kind == "video_action_png")
    old.assert_called_once_with(str(file))
    forbidden.assert_not_called()


def test_region_and_visual_rejections_remain_strict(monkeypatch):
    frame = read_clip()[48]
    executor = runtime.RuleExecutor()
    screen = np.zeros((70, 90, 3), dtype=np.uint8)
    screen[12:44, 14:44] = frame
    monkeypatch.setattr(runtime, "_grab_screen_bgr", lambda: screen)
    assert executor._find_image_on_screen(str(CLIP), .8, [14, 12, 44, 44], True, True, True)
    assert executor._find_image_on_screen(str(CLIP), .8, [60, 50, 89, 69], True, True, True) is None
    assert "template_outside_region" in executor._dedicated_video_diagnostic
    monkeypatch.setattr(runtime, "_passes_image_visual_verification", lambda *_a, **_kw: False)
    assert executor._find_image_on_screen(str(CLIP), .8, [14, 12, 44, 44], True, True, True) is None
    assert "visual_rejected" in executor._dedicated_video_diagnostic


def test_negative_screen_is_not_accepted(monkeypatch):
    monkeypatch.setattr(runtime, "_grab_screen_bgr", lambda: np.zeros((50, 60, 3), dtype=np.uint8))
    executor = runtime.RuleExecutor()
    assert executor._find_image_on_screen(str(CLIP), .8, verify_color=True, verify_brightness=True,
                                           dedicated_video=True) is None
    assert "below_threshold" in executor._dedicated_video_diagnostic
    assert "best=0.0%" in executor._dedicated_video_diagnostic


def test_cache_detects_same_metadata_content_replacement_and_is_bounded(monkeypatch, tmp_path):
    import os
    file = tmp_path / "clip.mp4"
    file.write_bytes(CLIP.read_bytes())
    first = bank.get_dedicated_video_frames(file)
    assert first
    cap = Mock(side_effect=AssertionError("cache miss"))
    real = bank.cv2.VideoCapture
    monkeypatch.setattr(bank.cv2, "VideoCapture", cap)
    assert bank.get_dedicated_video_frames(file)[0][0] is first[0][0]
    monkeypatch.setattr(bank.cv2, "VideoCapture", real)
    stat = file.stat()
    file.write_bytes(b"\0" * stat.st_size)
    os.utime(file, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert not bank.get_dedicated_video_frames(file)
    for i in range(6):
        file = tmp_path / f"clip{i}.mp4"
        file.write_bytes(CLIP.read_bytes())
        assert bank.get_dedicated_video_frames(file)
    assert len(bank._cache) <= 4
    assert sum(f[0].nbytes + f[3].nbytes for value in bank._cache.values() for f in value) <= bank.MAX_CACHE_BYTES


def test_stop_and_budget_fallback(monkeypatch):
    stop = threading.Event()
    stop.set()
    assert not bank.get_dedicated_video_frames(CLIP, stop)
    monkeypatch.setattr(bank, "MAX_PROBE_PIXELS", 1)
    assert not bank.get_dedicated_video_frames(CLIP)


def test_decode_cancellation_releases_capture(monkeypatch, tmp_path):
    path = tmp_path / "fake.mp4"
    path.write_bytes(b"test")
    stop = threading.Event()
    frame = np.random.default_rng(4).integers(0, 255, (10, 10, 3), dtype=np.uint8)
    cap = Mock()
    cap.isOpened.return_value = True
    cap.get.side_effect = lambda field: {cv2.CAP_PROP_FRAME_COUNT: 30,
                                      cv2.CAP_PROP_FRAME_WIDTH: 10, cv2.CAP_PROP_FRAME_HEIGHT: 10}[field]
    def read():
        stop.set()
        return True, frame
    cap.read.side_effect = read
    monkeypatch.setattr(bank.cv2, "VideoCapture", lambda _path: cap)
    assert bank.get_dedicated_video_frames(path, stop) == []
    cap.release.assert_called_once_with()
    assert not bank._cache


def test_large_encoded_video_falls_back_without_decoding(monkeypatch, tmp_path):
    path = tmp_path / "oversized.mp4"
    path.write_bytes(b"1234")
    monkeypatch.setattr(bank, "MAX_ENCODED_BYTES", 2)
    forbidden = Mock(side_effect=AssertionError("over-budget decode"))
    monkeypatch.setattr(bank.cv2, "VideoCapture", forbidden)
    assert bank.get_dedicated_video_frames(path) is None
    forbidden.assert_not_called()


@pytest.mark.parametrize("kind", ["이미지 클릭", "동영상클릭", "동영상 입력"])
def test_monitor_action_dispatch_changes_only_video_actions(monkeypatch, kind):
    executor = runtime.RuleExecutor()
    find = Mock(return_value=(15, 16, .99))
    monkeypatch.setattr(executor, "_find_image_on_screen", find)
    monkeypatch.setattr(executor, "_check_user_intervention", lambda: False)
    monkeypatch.setattr(executor, "_wait_for_resume", lambda: False)
    device = Mock()
    monkeypatch.setattr(runtime, "get_input_controller", lambda: device)
    assert executor._execute_monitor_action({"type": kind, "image": str(CLIP), "confidence": .8})
    assert find.call_args.kwargs.get("dedicated_video", False) == (kind != "이미지 클릭")
    assert find.call_args.args[1] == .8
    device.click.assert_called_once_with()


@pytest.mark.parametrize("dedicated", [False, True])
def test_video_test_ui_uses_same_frame_bank_as_runtime_without_opening_ui(monkeypatch, dedicated):
    from src.ui import monitoring_editor as ui

    editor = ui.MonitoringModeEditor
    host = SimpleNamespace(**{name: getattr(editor, name) for name in (
        "_safe_confidence", "_normalize_search_region_value", "_region_label_text", "_condition_verify_label",
    )})
    frame = read_clip()[48]
    monkeypatch.setattr(ui, "_grab_screen_bgr", lambda: frame)
    load = Mock(wraps=bank.get_dedicated_video_frames)
    monkeypatch.setattr(bank, "get_dedicated_video_frames", load)
    route = {"condition_image": str(CLIP), "condition_confidence": .8,
             "condition_verify_image_color": True, "condition_verify_image_brightness": True}
    if dedicated:
        route["dedicated_video_test"] = True
    result = editor._match_condition_image_for_test(host, route)
    assert result["ok"]
    if dedicated:
        load.assert_called_once_with(str(CLIP))
        assert result["found"]
    else:
        load.assert_not_called()


def test_ui_test_and_runtime_routes_are_opt_in_only():
    text = (ROOT / "src/ui/monitoring_editor.py").read_text(encoding="utf-8-sig")
    assert '"dedicated_video_test": True' in text
    assert 'if route.get("dedicated_video_test") and is_video_media_path(image_path):' in text
    tree = ast.parse((ROOT / "src/player/rule_executor.py").read_text(encoding="utf-8-sig"))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "_find_image_on_screen"]
    assert sum("dedicated_video" in ast.unparse(n) for n in calls) == 2
