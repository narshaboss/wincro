import threading
from types import SimpleNamespace

from src.ui import main_window
from src.ui.main_window import MainWindow


def test_completed_group_plan_schedules_the_next_plan_immediately():
    events = []
    host = SimpleNamespace(
        winfo_exists=lambda: True,
        _mini_stop_requested=False,
        _mini_current_repeat=0,
        _mini_total_repeat=1,
        _sequence_mode=True,
        _sequence_index=0,
        _sequence_plans=["first.json", "second.json"],
        _mini_playback_generation=17,
    )

    def post_lifecycle(callback, label):
        events.append(("post", label))
        callback()
        return True

    host._mini_post_lifecycle = post_lifecycle
    host._run_sequence_plan = lambda index, playback_generation: events.append(
        ("start", index, playback_generation)
    )

    MainWindow._mini_on_repeat_complete(host, True, "completed")

    assert host._mini_current_repeat == 1
    assert events == [
        ("post", "sequence-next-plan:2/2"),
        ("start", 1, 17),
    ]


def test_executor_handoff_waits_for_worker_exit_before_group_advance(monkeypatch):
    events = []
    advanced = threading.Event()

    class FakeExecutor:
        def set_callbacks(self, *, on_progress=None, on_complete=None, **_kwargs):
            self.on_progress = on_progress
            self.on_complete = on_complete

        def execute_plan_async(self, _plan, **_kwargs):
            events.append("execute-scheduled")
            self.on_complete(True, "completed")
            return True

        def clear_callbacks(self):
            events.append("callbacks-cleared")

        def wait_for_worker_exit(self, timeout):
            events.append(("worker-exit", timeout))
            return True

        def take_special_mode_route_handoff(self):
            events.append("handoff-read")
            return None

    monkeypatch.setattr(main_window, "RuleExecutor", FakeExecutor)

    host = SimpleNamespace(
        _mini_playback_generation=23,
        _mini_stop_requested=False,
        _ensure_arduino_ready_for_mini=lambda _label: True,
        _mini_is_current_playback_generation=lambda generation: generation == 23,
        _mini_on_progress=lambda _progress: None,
        winfo_exists=lambda: True,
    )

    def post_lifecycle(callback, label):
        events.append(("lifecycle", label))
        callback()
        return True

    def repeat_complete(success, message):
        events.append(("repeat-complete", success, message))
        advanced.set()

    host._mini_post_lifecycle = post_lifecycle
    host._mini_on_repeat_complete = repeat_complete
    host._mini_on_complete = lambda *_args, **_kwargs: None
    host._mini_on_playlist_skip = lambda _message: None
    host._mini_play_plan_rules = lambda _rules: None

    MainWindow._mini_run_plan_via_executor(host, object())

    assert advanced.wait(timeout=2.0)
    assert host._rule_executor is None
    assert events.index(("worker-exit", 5.0)) < next(
        index
        for index, event in enumerate(events)
        if isinstance(event, tuple) and event[0] == "lifecycle"
    )
    assert events[-2:] == [
        "handoff-read",
        ("repeat-complete", True, "completed"),
    ]
