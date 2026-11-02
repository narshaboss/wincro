"""Opt-in native player entry for a compiled GPT routine; never reroutes legacy actions."""

import time

from ..agent_bridge.contracts import BridgeError
from ..agent_bridge.storage import new_id, write_json
from .actions import input_cost
from .behaviors import execute, validate_program, confirm_before_send
from .experience import memory_root

MAX_RECEIPT_RUNS = 1000


def run_saved(program, check, *, backend=None, transmit=None, receipt_root=None):
    program = validate_program(program)
    if backend is None:
        from .windows import WindowIO, windows
        target = program["target"]
        candidates = [w for w in windows() if w.executable_name.casefold() == target["executable"].casefold()
                      and w.title == target["title"]]
        if len(candidates) != 1:
            raise BridgeError("TARGET_AMBIGUOUS", "GPT 플랜 대상 창이 없거나 여러 개입니다. 임의 선택하지 않습니다.")
        backend = WindowIO(candidates[0])
        backend.validate()  # No automatic focus stealing from the ordinary player.
        if not backend.controller.play_lab_prepare():
            raise BridgeError("DEVICE_NOT_READY", "GPT 행동 플랜은 연결된 Arduino 전용입니다.")
    count = 0
    folder = None
    if transmit is None:
        root = receipt_root or memory_root() / "playback"
        if root.exists() and sum(1 for p in root.glob("run_*") if p.is_dir()) >= MAX_RECEIPT_RUNS:
            raise BridgeError("MEMORY_FULL", "GPT 플랜 실행 기록 1000회 한도입니다. 기존 근거를 보존하며 새 입력을 중단합니다.")
        folder = root / new_id("run_")
        folder.mkdir(parents=True, exist_ok=False)
        def transmit(action, region, purpose, guard):
            nonlocal count
            check()
            count += input_cost(action)
            if count > 100:
                raise BridgeError("BUDGET_EXHAUSTED", "저장 구간 입력 한도 도달")
            ident = new_id("input_")
            receipt = {"id": ident, "action": action, "purpose": purpose, "state": "unknown"}
            write_json(folder / (ident + ".json"), receipt)
            try:
                confirm_before_send(backend, region, guard)
                check()
            except BridgeError as exc:
                if exc.code == "BEHAVIOR_GUARD_CHANGED":
                    receipt["state"] = "not_sent"
                    write_json(folder / (ident + ".json"), receipt)
                raise
            if action["kind"] == "wait":
                end = time.monotonic() + action["seconds"]
                while time.monotonic() < end:
                    check()
                    time.sleep(min(.05, max(0, end - time.monotonic())))
                ok = True
            else:
                ok = backend.send(action, region, check)
            if not ok:
                raise BridgeError("DELIVERY_UNKNOWN", "GPT 플랜 입력 응답 미확인. 전체 구간을 재전송하지 않습니다.")
            receipt["state"] = "sent_not_game_success"
            write_json(folder / (ident + ".json"), receipt)
    try:
        result = execute(program, backend, check, transmit)
        if folder:
            write_json(folder / "result.json", result)
        return result
    finally:
        if not backend.release():
            raise BridgeError("RELEASE_UNKNOWN", "GPT 행동 플랜 입력 해제 미확인")
