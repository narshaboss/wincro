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
    "card_border_width": 2,
    "canvas_border_width": 2,
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

# 화이트골드 기반 팔레트. 기존 키 이름은 유지해서 기능 코드를 건드리지 않는다.
COLORS = {
    # 배경색
    "bg_dark": "#0D0B08",
    "bg_sidebar": "#12100C",
    "bg_content": "#0F0D0A",
    "bg_card": "#17130E",
    "bg_card_hover": "#241C12",
    "bg_log": "#0A0907",
    "bg_elevated": "#1F170E",
    "bg_glass": "#15110C",

    # 강조색
    "accent": "#E0B341",
    "accent_hover": "#FACC15",
    "accent_blue": "#60A5FA",
    "accent_orange": "#F97316",
    "accent_red": "#F87171",
    "accent_pink": "#E879F9",
    "accent_pink_hover": "#F0ABFC",

    # 텍스트
    "text_primary": "#FFF7E6",
    "text_secondary": "#E8D3A6",
    "text_muted": "#BFA06A",
    "text_on_accent": "#0D0B08",

    # 경계선
    "border": "#8F5E0A",
    "separator": "#A97010",
    "button_border": "#000000",

    # 상태 색상
    "success": "#22C55E",
    "warning": "#FACC15",
    "error": "#F87171",
    "danger": "#EF4444",
    "danger_hover": "#F87171",
    "info": "#60A5FA",
    "success_text": "#BBF7D0",
    "warning_text": "#FDE68A",
    "info_text": "#BFDBFE",
    "accent_text": "#F8D879",
    "accent_blue_text": "#BFDBFE",
    "accent_pink_text": "#F9A8D4",
    "scroll_purple_text": "#DDD6FE",

    # player_view.py에서 사용되는 추가 색상
    "scroll_purple": "#C084FC",
    "child_bg": "#1C160F",
    "search_radius_purple": "#C084FC",
    "search_radius_purple_hover": "#DDD6FE",
    "confidence_amber": "#F97316",
    "confidence_amber_hover": "#FB923C",
    "delete_red": "#EF4444",
    "green_hover": "#4ADE80",
    "multi_image_orange": "#F97316",
    "lock_red": "#EF4444",
    "flatten_red": "#EF4444",
    "screenshot_blue": "#60A5FA",
    "subordinate_cyan": "#22D3EE",
    "random_orange": "#FB923C",
    "selection_green": "#22C55E",
    "hover_green": "#4ADE80",
    "hover_blue": "#93C5FD",

    # 기능형 예외 표면: 화면 영역 선택/이미지 크롭은 시각 대비가 우선이다.
    "overlay_dim": "#000000",
    "overlay_text": "#FFFFFF",
    "image_canvas_bg": "#2D2415",
    "image_canvas_border": "#7A4A00",
}
