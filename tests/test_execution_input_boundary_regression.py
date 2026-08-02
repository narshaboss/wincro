import threading

from src.analyzer.automation_models import AutomationPlan
from src.player import rule_executor
from src.player.rule_executor import ExecutionState, RuleExecutor


def test_repeat_boundary_releases_input_before_completion_callback(monkeypatch):
    events = []

    class FakeInputController:
        def release_all(self):
            events.append("release")
            return True

    monkeypatch.setattr(
        rule_executor,
        "get_input_controller",
        lambda: FakeInputController(),
    )

    plan = AutomationPlan(name="input-boundary", initial_rules=[], monitoring_rules=[])
    plan.total_repeat_count = 2
    executor = RuleExecutor()
    executor.set_callbacks(
        on_complete=lambda success, _message: events.append(f"complete:{success}"),
    )

    assert executor.execute_plan(plan) is True
    executor._execution_thread.join(timeout=3.0)

    assert not executor._execution_thread.is_alive()
    assert events == [
        "release",  # execution start
        "release",  # second repeat boundary
        "release",  # completion boundary
        "complete:True",
    ]


def test_completion_callback_is_not_followed_by_late_input_release(monkeypatch):
    events = []

    class FakeInputController:
        def release_all(self):
            events.append("release")
            return True

    monkeypatch.setattr(
        rule_executor,
        "get_input_controller",
        lambda: FakeInputController(),
    )

    plan = AutomationPlan(name="single-run", initial_rules=[], monitoring_rules=[])
    executor = RuleExecutor()
    executor.set_callbacks(
        on_complete=lambda _success, _message: events.append("next-run-scheduled"),
    )

    assert executor.execute_plan(plan) is True
    executor._execution_thread.join(timeout=3.0)

    assert events[-2:] == ["release", "next-run-scheduled"]


def test_concurrent_completion_notifies_callback_once(monkeypatch):
    events = []
    executor = RuleExecutor()
    executor._reset_input_state = lambda context: events.append(context) or True
    executor.set_callbacks(on_complete=lambda success, _message: events.append(success))

    barrier = threading.Barrier(3)

    def complete():
        barrier.wait()
        executor._notify_complete(True, "done")

    workers = [threading.Thread(target=complete) for _ in range(2)]
    for worker in workers:
        worker.start()
    barrier.wait()
    for worker in workers:
        worker.join(timeout=2.0)

    assert events.count("execution-complete") == 1
    assert events.count(True) == 1


def test_completion_release_failure_is_reported_as_failure_and_blocks_next_input(monkeypatch):
    events = []
    executor = RuleExecutor()
    executor._reset_input_state = lambda _context: False
    monkeypatch.setattr(
        rule_executor,
        "block_automation_input",
        lambda reason: events.append(f"block:{reason}"),
    )
    executor.set_callbacks(
        on_complete=lambda success, message: events.append((success, message)),
    )

    executor._notify_complete(True, "finished")

    assert events[0] == "block:RuleExecutor.completion_reset_failed"
    assert events[1][0] is False
    assert "입력 장치 안전 해제 실패" in events[1][1]


def test_execute_plan_reserves_running_state_before_thread_start(monkeypatch):
    class ReservedThread:
        def __init__(self, *args, **kwargs):
            self.started = False

        def start(self):
            self.started = True

        def is_alive(self):
            return self.started

    monkeypatch.setattr(rule_executor.threading, "Thread", ReservedThread)
    monkeypatch.setattr(rule_executor.pyautogui, "position", lambda: (0, 0))
    monkeypatch.setattr(rule_executor, "unblock_automation_input", lambda: None)

    executor = RuleExecutor()
    executor._reset_input_state = lambda _context: True
    plan = AutomationPlan(name="reserved", initial_rules=[], monitoring_rules=[])

    assert executor.execute_plan(plan) is True
    assert executor.state == ExecutionState.RUNNING_INITIAL
    assert executor.execute_plan(plan) is False


def test_execute_plan_thread_start_failure_rolls_back_input_and_state(monkeypatch):
    events = []

    class BrokenThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            raise RuntimeError("thread unavailable")

        def is_alive(self):
            return False

    monkeypatch.setattr(rule_executor.threading, "Thread", BrokenThread)
    monkeypatch.setattr(rule_executor.pyautogui, "position", lambda: (0, 0))
    monkeypatch.setattr(rule_executor, "unblock_automation_input", lambda: events.append("unblock"))
    monkeypatch.setattr(rule_executor, "block_automation_input", lambda reason: events.append(f"block:{reason}"))

    executor = RuleExecutor()
    executor._reset_input_state = lambda context: events.append(context) or True
    plan = AutomationPlan(name="broken-start", initial_rules=[], monitoring_rules=[])

    assert executor.execute_plan(plan) is False
    assert executor.state == ExecutionState.FAILED
    assert executor._execution_thread is None
    assert events == [
        "execution-start",
        "unblock",
        "block:RuleExecutor.start_failed",
        "execution-start-failed",
    ]
