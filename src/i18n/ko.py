"""
한글 번역 모듈

애플리케이션의 모든 UI 텍스트를 한글로 제공합니다.
"""

from typing import Dict

# 애플리케이션 정보
APP_INFO: Dict[str, str] = {
    "app_name": "Desktop",
    "app_name_full": "Desktop Window Manager",
    "version": "버전",
    "copyright": "Copyright 2024. Microsoft Corporation.",
}

# 메인 메뉴
MENU: Dict[str, str] = {
    "file": "파일",
    "edit": "편집",
    "view": "보기",
    "tools": "도구",
    "help": "도움말",
    "new": "새로 만들기",
    "open": "열기",
    "save": "저장",
    "save_as": "다른 이름으로 저장",
    "export": "내보내기",
    "import": "가져오기",
    "exit": "종료",
    "settings": "설정",
    "about": "정보",
}

# 탭/뷰 이름
VIEWS: Dict[str, str] = {
    "recorder": "녹화",
    "analyzer": "분석",
    "player": "실행",
    "settings": "설정",
    "sequences": "시퀀스 목록",
    "history": "실행 기록",
    "log": "로그",
}

# 녹화 관련
RECORDER: Dict[str, str] = {
    "title": "화면 녹화",
    "start": "녹화 시작",
    "stop": "녹화 중지",
    "pause": "일시 정지",
    "resume": "재개",
    "recording": "녹화 중...",
    "paused": "일시 정지됨",
    "stopped": "녹화 중지됨",
    "ready": "녹화 준비 완료",
    "select_area": "녹화 영역 선택",
    "full_screen": "전체 화면",
    "custom_area": "사용자 지정 영역",
    "include_cursor": "마우스 커서 포함",
    "include_clicks": "클릭 효과 표시",
    "fps": "프레임 레이트",
    "quality": "화질",
    "quality_low": "낮음",
    "quality_medium": "보통",
    "quality_high": "높음",
    "duration": "녹화 시간",
    "save_recording": "녹화 저장",
    "discard_recording": "녹화 취소",
    "recording_saved": "녹화가 저장되었습니다.",
    "recording_discarded": "녹화가 취소되었습니다.",
    "hotkey_start": "녹화 시작 단축키",
    "hotkey_stop": "녹화 중지 단축키",
}

# 분석 관련
ANALYZER: Dict[str, str] = {
    "title": "영상 분석",
    "select_video": "영상 파일 선택",
    "analyze": "분석 시작",
    "analyzing": "분석 중...",
    "analysis_complete": "분석 완료",
    "analysis_failed": "분석 실패",
    "progress": "진행률",
    "detected_actions": "감지된 동작",
    "action_count": "동작 수",
    "mouse_clicks": "마우스 클릭",
    "keyboard_inputs": "키보드 입력",
    "scrolls": "스크롤",
    "drags": "드래그",
    "waits": "대기",
    "save_sequence": "시퀀스로 저장",
    "edit_actions": "동작 편집",
    "preview": "미리보기",
    "confidence": "신뢰도",
    "timestamp": "시간",
    "action_type": "동작 유형",
    "target": "대상",
    "parameters": "매개변수",
}

# 실행 관련
PLAYER: Dict[str, str] = {
    "title": "동작 실행",
    "select_sequence": "시퀀스 선택",
    "play": "실행",
    "stop": "중지",
    "pause": "일시 정지",
    "resume": "재개",
    "running": "실행 중...",
    "paused": "일시 정지됨",
    "stopped": "실행 중지됨",
    "completed": "실행 완료",
    "failed": "실행 실패",
    "ready": "실행 준비 완료",
    "speed": "실행 속도",
    "repeat": "반복 실행",
    "repeat_count": "반복 횟수",
    "repeat_infinite": "무한 반복",
    "current_step": "현재 단계",
    "total_steps": "전체 단계",
    "elapsed_time": "경과 시간",
    "remaining_time": "남은 시간",
    "emergency_stop": "긴급 중지 (ESC 2회)",
    "ai_intervention": "AI 개입",
    "ai_analyzing": "AI가 화면을 분석 중입니다...",
    "ai_resolved": "AI가 문제를 해결했습니다.",
    "ai_failed": "AI가 문제를 해결하지 못했습니다.",
    "retry": "재시도",
    "skip": "건너뛰기",
    "abort": "중단",
}

# 시퀀스 관련
SEQUENCE: Dict[str, str] = {
    "title": "시퀀스 관리",
    "name": "이름",
    "description": "설명",
    "created_at": "생성 시간",
    "last_run": "마지막 실행",
    "run_count": "실행 횟수",
    "success_rate": "성공률",
    "actions": "동작 목록",
    "new": "새 시퀀스",
    "edit": "편집",
    "delete": "삭제",
    "duplicate": "복제",
    "export": "내보내기",
    "import": "가져오기",
    "search": "검색",
    "sort_by": "정렬 기준",
    "sort_name": "이름순",
    "sort_date": "날짜순",
    "sort_runs": "실행 횟수순",
    "confirm_delete": "시퀀스를 삭제하시겠습니까?",
    "delete_success": "시퀀스가 삭제되었습니다.",
    "export_success": "시퀀스가 내보내기되었습니다.",
    "import_success": "시퀀스를 가져왔습니다.",
    "import_failed": "시퀀스 가져오기에 실패했습니다.",
}

# 설정 관련
SETTINGS: Dict[str, str] = {
    "title": "설정",
    "general": "일반",
    "recording": "녹화",
    "analysis": "분석",
    "playback": "재생",
    "ai": "AI 설정",
    "appearance": "외관",
    "language": "언어",
    "theme": "테마",
    "theme_dark": "다크",
    "theme_light": "라이트",
    "api_key": "API 키",
    "api_key_placeholder": "OpenAI API 키를 입력하세요",
    "api_key_saved": "API 키가 저장되었습니다.",
    "api_key_invalid": "유효하지 않은 API 키입니다.",
    "save": "저장",
    "cancel": "취소",
    "reset": "기본값으로 초기화",
    "reset_confirm": "설정을 기본값으로 초기화하시겠습니까?",
    "changes_saved": "변경사항이 저장되었습니다.",
    "confirm_before_run": "실행 전 확인",
    "minimize_on_run": "실행 시 최소화",
    "show_tooltips": "툴팁 표시",
}

# 동작 유형
ACTION_TYPES: Dict[str, str] = {
    "click": "클릭",
    "double_click": "더블클릭",
    "right_click": "우클릭",
    "drag": "드래그",
    "scroll": "스크롤",
    "type": "텍스트 입력",
    "hotkey": "단축키",
    "key_press": "키 입력",
    "wait": "대기",
    "screenshot": "스크린샷",
    "find_image": "이미지 찾기",
    "find_text": "텍스트 찾기",
    "condition": "조건",
    "loop": "반복",
}

# 버튼 및 공통 UI
BUTTONS: Dict[str, str] = {
    "ok": "확인",
    "cancel": "취소",
    "yes": "예",
    "no": "아니오",
    "apply": "적용",
    "close": "닫기",
    "save": "저장",
    "delete": "삭제",
    "edit": "편집",
    "add": "추가",
    "remove": "제거",
    "clear": "지우기",
    "refresh": "새로고침",
    "browse": "찾아보기",
    "select_all": "전체 선택",
    "deselect_all": "선택 해제",
    "copy": "복사",
    "paste": "붙여넣기",
    "cut": "잘라내기",
    "undo": "실행 취소",
    "redo": "다시 실행",
}

# 메시지 및 알림
MESSAGES: Dict[str, str] = {
    "welcome": "환영합니다!",
    "loading": "로딩 중...",
    "saving": "저장 중...",
    "processing": "처리 중...",
    "please_wait": "잠시 기다려 주세요...",
    "success": "성공",
    "error": "오류",
    "warning": "경고",
    "info": "정보",
    "confirm": "확인",
    "unsaved_changes": "저장되지 않은 변경사항이 있습니다. 저장하시겠습니까?",
    "exit_confirm": "프로그램을 종료하시겠습니까?",
    "operation_cancelled": "작업이 취소되었습니다.",
    "operation_completed": "작업이 완료되었습니다.",
    "file_not_found": "파일을 찾을 수 없습니다.",
    "invalid_file": "유효하지 않은 파일입니다.",
    "permission_denied": "권한이 없습니다.",
    "network_error": "네트워크 오류가 발생했습니다.",
    "unknown_error": "알 수 없는 오류가 발생했습니다.",
}

# 에러 메시지
ERRORS: Dict[str, str] = {
    "recording_failed": "녹화를 시작할 수 없습니다.",
    "analysis_failed": "영상 분석에 실패했습니다.",
    "playback_failed": "동작 실행에 실패했습니다.",
    "file_save_failed": "파일 저장에 실패했습니다.",
    "file_load_failed": "파일 로드에 실패했습니다.",
    "api_key_missing": "API 키가 설정되지 않았습니다.",
    "api_connection_failed": "API 연결에 실패했습니다.",
    "image_not_found": "화면에서 이미지를 찾을 수 없습니다.",
    "text_not_found": "화면에서 텍스트를 찾을 수 없습니다.",
    "timeout": "시간 초과되었습니다.",
    "sequence_empty": "시퀀스가 비어있습니다.",
    "invalid_action": "유효하지 않은 동작입니다.",
}

# 툴팁
TOOLTIPS: Dict[str, str] = {
    "record_start": "화면 녹화를 시작합니다 (F9)",
    "record_stop": "녹화를 중지합니다 (F10)",
    "analyze": "녹화된 영상을 분석하여 동작을 추출합니다",
    "play": "선택한 시퀀스를 실행합니다",
    "emergency_stop": "ESC 키를 2번 누르면 즉시 중지됩니다",
    "ai_mode": "예상치 못한 상황에서 AI가 자동으로 대응합니다",
    "speed_slider": "동작 실행 속도를 조절합니다 (0.5x ~ 2x)",
    "repeat_count": "시퀀스를 반복 실행할 횟수를 설정합니다",
}

# 모든 번역을 하나의 딕셔너리로 통합
TRANSLATIONS: Dict[str, Dict[str, str]] = {
    "app_info": APP_INFO,
    "menu": MENU,
    "views": VIEWS,
    "recorder": RECORDER,
    "analyzer": ANALYZER,
    "player": PLAYER,
    "sequence": SEQUENCE,
    "settings": SETTINGS,
    "action_types": ACTION_TYPES,
    "buttons": BUTTONS,
    "messages": MESSAGES,
    "errors": ERRORS,
    "tooltips": TOOLTIPS,
}


def get_text(category: str, key: str, default: str = "") -> str:
    """
    번역된 텍스트를 가져옵니다.

    Args:
        category: 카테고리 (예: 'menu', 'buttons')
        key: 텍스트 키
        default: 찾지 못할 경우 반환할 기본값

    Returns:
        str: 번역된 텍스트
    """
    if category in TRANSLATIONS:
        return TRANSLATIONS[category].get(key, default or key)
    return default or key


def t(key: str, default: str = "") -> str:
    """
    간단한 번역 헬퍼 함수

    'category.key' 형식의 키를 받아 번역된 텍스트를 반환합니다.

    Args:
        key: 'category.key' 형식의 키
        default: 기본값

    Returns:
        str: 번역된 텍스트
    """
    parts = key.split(".", 1)
    if len(parts) == 2:
        return get_text(parts[0], parts[1], default)
    return default or key
