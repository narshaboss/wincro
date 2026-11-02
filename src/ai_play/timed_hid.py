"""GL1 bounded HID protocol. Never reconnects, uploads, retries or falls back."""

import time
import uuid

from ..agent_bridge.contracts import BridgeError


def exchange(device, command):
    with device._io_lock:
        port = device._serial
        if not port or not port.is_open:
            raise BridgeError("ARDUINO_DISCONNECTED", "아두이노 연결이 끊겼습니다.")
        previous = port.timeout
        try:
            port.reset_input_buffer()
            port.write((command + "\n").encode("ascii"))
            # Do not flush indefinitely; firmware watchdog also covers a stalled host.
            port.timeout = .15
            return port.readline().decode("ascii").strip()
        except Exception as exc:
            raise BridgeError("DELIVERY_UNKNOWN", "GL1 응답 미확인. 입력을 재전송하지 않습니다.") from exc
        finally:
            port.timeout = previous


def supported(device):
    return exchange(device, "GL1") == "GL1:READY"


def run(device, codes, milliseconds, check, *, clock=time.monotonic, sleep=time.sleep):
    token = uuid.uuid4().hex[:16]
    check()
    if not supported(device):
        raise BridgeError("TIMED_INPUT_UNSUPPORTED", "연결 장치에 GL1 자동해제 입력이 없습니다. 펌웨어를 자동 업로드하거나 소프트웨어 입력으로 대체하지 않습니다.")
    check()
    expected = "GL1:" + token + ":"
    started = False
    try:
        # A missing acknowledgement may still mean the keys were pressed.
        started = True
        result = exchange(device, "GLS," + token + "," + str(milliseconds) + "," + ",".join(map(str, codes)))
        if result != expected + "RUNNING":
            raise BridgeError("DELIVERY_UNKNOWN", "키 유지 접수 결과가 불확실합니다. 재전송하지 않습니다.")
        deadline = clock() + milliseconds / 1000 + .4
        while clock() < deadline:
            check()
            result = exchange(device, "GLP," + token)
            if result == expected + "DONE":
                check()
                return True
            if result != expected + "RUNNING":
                raise BridgeError("DELIVERY_UNKNOWN", "키 유지 완료·해제 확인 실패: " + result[:80])
            sleep(.05)
        raise BridgeError("DELIVERY_UNKNOWN", "키 유지 완료 응답 시간 초과")
    finally:
        if started:
            # Cancellation is idempotent; never send the action again.
            response = exchange(device, "GLC," + token)
            if response not in {expected + "DONE", expected + "CANCELLED", expected + "EXPIRED"}:
                raise BridgeError("RELEASE_UNKNOWN", "키 자동해제 결과 미확인. 장치·화면 확인 후 재개하세요.")
