"""
WinCro UI 테마 모듈.

색상과 치수는 한 곳에서 관리한다. 2026 iOS 개편은 개별 위젯을
하나씩 덮어쓰기보다 이 토큰을 중심으로 확산시키는 방식으로 진행한다.
"""

IOS_METRICS = {
    "window_padding": 18,
    "content_padding": 14,
    "card_radius": 22,
    "card_radius_compact": 18,
    "control_radius": 16,
    "control_radius_small": 12,
    "pill_radius": 999,
    "hairline": 1,
    "topbar_height": 64,
    "button_height": 40,
    "button_height_small": 32,
    "row_height": 54,
}

IOS_FONTS = {
    "family": "Segoe UI Variable",
    "fallback": "Segoe UI",
    "title_size": 18,
    "body_size": 14,
    "caption_size": 12,
    "micro_size": 10,
}

# iOS 다크 모드 기반 팔레트. 기존 키 이름은 유지해서 기능 코드를 건드리지 않는다.
COLORS = {
    # 배경색
    "bg_dark": "#000000",
    "bg_sidebar": "#111113",
    "bg_content": "#000000",
    "bg_card": "#1C1C1E",
    "bg_card_hover": "#2C2C2E",
    "bg_log": "#050506",
    "bg_elevated": "#242426",
    "bg_glass": "#161618",

    # 강조색
    "accent": "#0A84FF",
    "accent_hover": "#409CFF",
    "accent_blue": "#64D2FF",
    "accent_orange": "#FF9F0A",
    "accent_red": "#FF453A",
    "accent_pink": "#FF2D55",
    "accent_pink_hover": "#FF5C7A",

    # 텍스트
    "text_primary": "#F5F5F7",
    "text_secondary": "#AEAEB2",
    "text_muted": "#6E6E73",

    # 경계선
    "border": "#38383A",
    "separator": "#2C2C2E",

    # 상태 색상
    "success": "#30D158",
    "warning": "#FFD60A",
    "error": "#FF453A",
    "danger": "#FF453A",
    "danger_hover": "#FF6961",
    "info": "#64D2FF",

    # player_view.py에서 사용되는 추가 색상
    "scroll_purple": "#BF5AF2",
    "child_bg": "#1F1F22",
    "search_radius_purple": "#BF5AF2",
    "search_radius_purple_hover": "#DA8FFF",
    "confidence_amber": "#FF9F0A",
    "confidence_amber_hover": "#FFB340",
    "delete_red": "#FF453A",
    "green_hover": "#63E681",
    "multi_image_orange": "#FF9F0A",
    "lock_red": "#FF6961",
    "flatten_red": "#FF453A",
    "screenshot_blue": "#64D2FF",
    "subordinate_cyan": "#5AC8FA",
    "random_orange": "#FFB340",
    "selection_green": "#1F7A3A",
    "hover_green": "#63E681",
    "hover_blue": "#409CFF",
}
