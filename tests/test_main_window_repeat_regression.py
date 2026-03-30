from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN_WINDOW = ROOT / "src" / "ui" / "main_window.py"


def _read_text() -> str:
    return MAIN_WINDOW.read_text(encoding="utf-8")


def test_main_window_reloads_single_plan_before_repeat():
    text = _read_text()
    assert "def _mini_reload_plan_for_repeat(self, plan):" in text
    assert "reloaded_plan = self._mini_reload_plan_for_repeat(plan)" in text
    assert "threading.Thread(target=reload_and_execute_current, daemon=True).start()" in text


def test_main_window_repeat_reload_uses_source_file_when_available():
    text = _read_text()
    assert 'plan_path = getattr(plan, "_source_file", None)' in text
    assert "data = load_json_file(plan_file)" in text
    assert "reloaded_plan = AutomationPlan.from_dict(data, templates_dir=templates_dir)" in text
    assert 'reloaded_plan._source_file = str(plan_file)' in text
    assert 'reloaded_plan.total_repeat_count = getattr(plan, "total_repeat_count", 1) or 1' in text
