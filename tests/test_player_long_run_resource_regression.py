from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RULE_EXECUTOR = ROOT / "src" / "player" / "rule_executor.py"


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def test_rule_executor_image_capture_does_not_spawn_per_capture_threads():
    text = _read_text(RULE_EXECUTOR)

    assert "def _grab_screen_bgr()" in text
    assert "import mss" in text
    assert "threading.Thread(target=capture_screen" not in text
    assert "threading.Thread(target=_grab_trigger" not in text
    assert "threading.Thread(target=_grab_all" not in text

    helper = text[
        text.index("def _grab_screen_bgr()"):
        text.index("# ANSI 색상 코드 상수", text.index("def _grab_screen_bgr()"))
    ]
    assert "ImageGrab.grab()" in helper

    image_search = text[
        text.index("def _find_image_on_screen("):
        text.index("def _wait_for_trigger(", text.index("def _find_image_on_screen("))
    ]
    trigger_wait = text[
        text.index("def _wait_for_trigger("):
        text.index("def _prepare_for_click_after_trigger", text.index("def _wait_for_trigger("))
    ]
    find_all = text[
        text.index("def _find_all_images_on_screen("):
        text.index("def _find_rule_image_click_target", text.index("def _find_all_images_on_screen("))
    ]

    assert "ImageGrab.grab()" not in image_search
    assert "ImageGrab.grab()" not in trigger_wait
    assert "ImageGrab.grab()" not in find_all
    assert "_grab_screen_bgr()" in image_search
    assert "_grab_screen_bgr()" in trigger_wait
    assert "_grab_screen_bgr()" in find_all
