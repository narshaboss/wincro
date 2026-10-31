"""User-facing copy and summaries; no GUI, model calls, or playback imports."""

import math

from ..agent_bridge.contracts import digest, walk


ROLE_TEXT = "분석·판단은 GPT가 합니다. WinCro는 자료 전달·초안 편집·저장을 담당합니다."
ACTION_NAMES = {"click": "이미지 클릭", "double_click": "더블클릭", "right_click": "오른쪽 클릭",
                "type": "글자 입력", "hotkey": "키 입력", "key_press": "키 입력",
                "scroll": "스크롤", "drag": "드래그", "game_mode": "특화모드"}
HELP_TEXT = """처음 사용하는 순서

1. 자료 준비
따라 만들 작업을 녹화한 파일을 추가하세요. 비밀번호·인증코드가 담긴 영상은 사용하지 마세요. 기존 플랜을 참고하려면 참고 플랜도 선택하세요. 원본은 변경하지 않습니다.
녹화가 없어도 참고 플랜을 검토해 달라고 요청할 수 있습니다. 다만 영상 근거 없이 새 플랜의 제작 검수를 완료할 수는 없습니다.

2. GPT 연결
연결 준비를 켜고 '연결 설정 안내'를 확인하세요. 외부 GPT 도구에 WinCro 편집 도구(MCP)를 등록해야 합니다. 연결 준비만 켜서는 GPT가 연결되거나 분석을 시작하지 않습니다.
상단 GPT 연결 ON은 외부 도구와 실제 통신이 확인된 상태입니다. 분석 중이라는 뜻은 아닙니다. 확인 신호가 끊기면 약 12초 안에 OFF로 바뀝니다.

3. GPT에 요청
원하는 작업, 반복·종료 조건을 적고 'GPT 요청문 복사'를 누르세요. 연결된 GPT 대화에 붙여넣어 보내면 GPT가 장면을 보고 판단합니다. 이 버튼은 대화에 자동 전송하지 않습니다.
예: 제작 화면에서 재료가 떨어질 때까지 반복하고, 끝나면 종료해 줘. 확실하지 않은 동작은 먼저 질문해 줘.

4. 초안 확인·저장
'초안 검토'에서 액션 순서·대기·반복과 GPT 분석·검수 요약을 읽으세요. 수정하면 이전 검수는 다시 받아야 합니다. '초안 저장'은 작업 파일 보관이고, '새 플랜 등록'은 일반 플랜 목록에 사본을 추가하는 별도 단계입니다.
등록에는 GPT의 최신 분석과 양방향 영상 대조, 사용자 승인이 필요합니다. 원본·재생목록은 자동으로 바뀌지 않습니다. 실제 인식·재생은 별도로 테스트해야 합니다.

알아두세요
이 창에 GPT 모델이 내장된 것은 아닙니다. 로컬 MCP 연결을 지원하는 외부 도구와 별도 실행 환경이 필요합니다. 현재 개발판은 일반 액션 편집 중심이며 모니터링·자동목록 편집 및 특화모드 플랜 등록은 지원하지 않습니다.
연결을 끊거나 창을 닫으면 이전 연결 파일은 무효가 됩니다. 다시 연결할 때 새 경로로 설정하세요. 연결 파일 안의 인증 키를 공개하거나 파일을 채팅에 업로드하지 마세요.
"""


def parse_exclusions(raw):
    result = []
    for part in raw.split(","):
        if not part.strip():
            continue
        try:
            start, end = [float(v.strip()) for v in part.split("-")]
        except ValueError as exc:
            raise ValueError("제외 구간을 0-5,12-18처럼 초 단위로 입력하세요.") from exc
        if not all(math.isfinite(v) for v in (start, end)) or start < 0 or end <= start:
            raise ValueError("제외 구간의 끝은 시작보다 커야 합니다. 음수는 사용할 수 없습니다.")
        result.append([start, end])
    return result


def review_progress(state, dirty=False):
    if not state:
        return "아직 초안이 없습니다. 자료를 준비하고 GPT에 제작을 요청하세요.", False
    if state.get("publication"):
        return "새 플랜 등록 완료 · 재생목록 추가와 실제 실행은 별도입니다.", False
    if dirty:
        return "입력한 수정 사항을 먼저 저장하거나 취소하세요.", False
    rows = list(walk(state["plan"]))
    if state["plan"].get("game_modes") or any(r.get("action_type") == "game_mode" for r, *_ in rows):
        return "특화모드가 포함된 참고용 초안입니다. 이 창에서는 새 플랜으로 등록할 수 없습니다.", False
    current = digest(state["plan"])
    analysis = state.get("analysis") or {}
    if analysis.get("plan_digest") != current:
        return "GPT의 최신 분석이 필요합니다. 영상 분석과 초안 검수를 요청하세요.", False
    if analysis.get("open_questions"):
        return "GPT가 확인을 요청한 질문이 있습니다. 분석 요약을 읽고 답해주세요.", False
    checks = state.get("crosschecks", [])[-2:]
    ready = (len(checks) == 2 and [r.get("direction") for r in checks] == ["video_to_plan", "plan_to_video"]
             and all(r.get("passed") and r.get("plan_digest") == current for r in checks))
    if not ready:
        return "GPT의 영상 → 플랜, 플랜 → 영상 교차검수가 필요합니다.", False
    return "GPT 분석·양방향 검수 기록 확인됨 · 내용 검토 후 등록하세요. 실제 실행은 미검증입니다.", True


def readable_action(rule):
    keys = rule.get("action_keys") or []
    lines = ["동작: " + ACTION_NAMES.get(rule.get("action_type"), str(rule.get("action_type")))]
    if keys:
        lines.append("입력 키: " + " + ".join(str(k).upper() for k in keys))
    if rule.get("action_text"):
        lines.append("입력할 글자: " + str(rule["action_text"]))
    if rule.get("action_key_events"):
        lines.append(f"기록된 키 동작: {len(rule['action_key_events'])}개")
    if rule.get("target_image"):
        lines.append("찾을 이미지: " + rule["target_image"])
    if rule.get("target_images"):
        lines.append(f"추가 대상 이미지: {len(rule['target_images'])}개")
    region = rule.get("search_region")
    lines.append("검색 범위: " + (str(region) if region else "전체 화면 / 해당 없음"))
    lines.append("못 찾을 때: " + ("설정에 따라 건너뜀" if rule.get("skip_on_not_found") else "기존 액션 대기 규칙 사용"))
    lines.append("이미지·키·범위·상하위 변경은 연결된 GPT에 요청하세요.")
    return "\n".join(lines)


def readable_analysis(state):
    message, _ = review_progress(state)
    if not state:
        return message
    analysis = state.get("analysis") or {}
    steps = {row["rule_id"]: row for row in analysis.get("steps", [])}
    lines = ["분석 담당: GPT", message, "", "작업 목표: " + (analysis.get("goal") or "아직 분석되지 않았습니다.")]
    for rule, _, _, step in walk(state["plan"]):
        item = steps.get(rule["rule_id"])
        lines.extend(["", f"{step}. {rule.get('description') or ACTION_NAMES.get(rule.get('action_type'), '액션')}"])
        if item:
            lines.extend(["화면에서 확인: " + item["observation"], "동작 목적: " + item["purpose"],
                          "기대 결과: " + item["expected_result"], f"영상 근거: {item['seconds']}초"])
        else:
            lines.append("GPT의 행동 분석 기록이 없습니다.")
    if analysis.get("open_questions"):
        lines.extend(["", "사용자 확인이 필요한 질문", *["• " + q for q in analysis["open_questions"]]])
    lines.extend(["", "교차검수 기록"])
    for check in state.get("crosschecks", []):
        direction = "영상 → 플랜" if check["direction"] == "video_to_plan" else "플랜 → 영상"
        lines.append(direction + (": 대조 통과 기록" if check["passed"] else ": 수정·확인 필요"))
        lines.extend("• " + issue for issue in check.get("remaining_issues", []))
        for item in check.get("checks", []):
            if item["result"] != "match":
                lines.append("• " + item["note"])
    lines.extend(["", "GPT의 판단 기록입니다. 실제 인식·재생 성공을 증명하지는 않습니다."])
    return "\n".join(lines)


def friendly_error(error):
    code = str(error).split(":", 1)[0]
    messages = {
        "REVISION_CONFLICT": "GPT 또는 다른 편집에서 초안이 바뀌었습니다. 입력은 보관했습니다. 최신값을 확인한 뒤 다시 수정하세요.",
        "ANALYSIS_REQUIRED": "GPT의 최신 영상 분석이 필요합니다. 연결된 대화에 분석을 요청하세요.",
        "CROSSCHECK_REQUIRED": "GPT에게 영상과 플랜의 양방향 교차검수를 요청하세요.",
        "PROTECTED_ACTION": "현재 일반 액션만 편집할 수 있습니다. 특화모드 등 보호된 구성은 변경·등록할 수 없습니다.",
        "UNSUPPORTED_SCHEMA": "지원하지 않는 설정이 있어 등록하지 않았습니다. 초안은 보존됩니다.",
        "MEDIA_NOT_READY": "자료가 이동·변경되었거나 읽을 수 없습니다. 파일을 확인하고 다시 선택하세요.",
        "CONNECTION_FAILED": "연결 준비를 시작하지 못했습니다. 실행 권한을 확인하고 다시 시도하세요.",
        "APP_NOT_CONNECTED": "연결이 끊겼습니다. 연결 준비를 켜고 외부 GPT의 연결 설정을 확인하세요.",
        "SAVE_FAILED": "저장하지 못했습니다. 저장 위치·권한·남은 공간을 확인하세요. 성공 처리하지 않았습니다.",
        "SAVE_STATE_UNKNOWN": "저장 결과를 확정하지 못했습니다. 다시 등록하지 말고 저장된 초안·플랜을 먼저 확인하세요.",
    }
    return messages.get(code, "작업을 완료하지 못했습니다. 오류 상세를 확인한 뒤 다시 시도하세요.") + f" ({code})"


def authoring_request(goal, reference=None, draft_id=None, has_media=False):
    return "\n".join([
        "WinCro 편집 도구로 먼저 connection_status를 호출해 실제 연결을 확인해 줘.",
        "작업 요청: " + goal.strip(),
        "참고 플랜: " + (reference or "선택하지 않음"),
        "작업 초안 ID: " + (draft_id or "없음. 필요하면 독립 초안을 새로 만들어 줘."),
        ("선택한 녹화는 list_recordings로 확인해 줘." if has_media else "녹화 없음. 참고 플랜 분석만 가능하며 영상 검수를 완료한 것처럼 보고하지 마."),
        "영상 분석·행동 목적·규칙 판단은 GPT인 네가 직접 해 줘. WinCro의 입력 로그 자동 변환으로 대체하지 마.",
        "상위·하위 묶음, 창별 검색 범위, 키별 반복·대기를 확인하고 불명확한 의도는 질문해 줘.",
        "편집 후 영상 → 플랜, 플랜 → 영상을 번갈아 반복 대조하고 문제를 고치면 다시 분석·검수해 줘.",
        "원본·재생목록·특화모드·인식 기준은 바꾸지 말고, 실제 입력이나 자동 실행 없이 초안만 편집해 줘.",
        "검수 결과와 미확인 사항을 설명하고 사용자 승인 전에는 정식 등록하지 마.",
    ])
