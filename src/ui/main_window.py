"""
WinCro 메인 윈도우 모듈

프리미엄 UI 디자인 - 사이드바 네비게이션 + 하단 로그 패널
"""

import customtkinter as ctk
import tkinter as tk
import logging
import os
from datetime import datetime
from typing import Optional, Callable, Dict
from collections import deque
from threading import Lock
from pathlib import Path

import cv2
import numpy as np
import mss
from pynput import keyboard

from ..utils.logger import get_logger
from ..utils.config import get_config, save_config, DATA_DIR, APP_VERSION
from ..utils.window_position import setup_window_position
from ..i18n import t, VIEWS
from ..analyzer.automation_models import AutomationPlan
from ..player.rule_executor import RuleExecutor

PLANS_DIR = DATA_DIR / "plans"

# 랜덤 이름 목록 (100개) - 순수 한글
RANDOM_NAMES = [
    "글쓰기", "그림판", "달력", "시계", "계산",
    "설정", "도움말", "찾기", "열기", "닫기",
    "저장", "불러오기", "내보내기", "가져오기", "인쇄",
    "복사", "붙여넣기", "잘라내기", "되돌리기", "다시실행",
    "새로만들기", "삭제", "이름바꾸기", "속성", "정보",
    "화면", "소리", "알림", "전원", "절전",
    "밝기", "어둡게", "밤모드", "조용히", "끄기",
    "켜기", "잠금", "해제", "숨기기", "보이기",
    "확대", "축소", "전체화면", "창모드", "나가기",
    "돌아가기", "앞으로", "뒤로", "위로", "아래로",
    "왼쪽", "오른쪽", "가운데", "맞춤", "정렬",
    "글꼴", "굵게", "기울임", "밑줄", "취소선",
    "빨강", "파랑", "초록", "노랑", "검정",
    "흰색", "회색", "주황", "보라", "분홍",
    "문서", "사진", "영상", "음악", "내려받기",
    "올리기", "공유", "연결", "끊기", "다시연결",
    "새로고침", "동기화", "백업", "복원", "초기화",
    "검사", "치료", "정리", "최적화", "분석",
    "기록", "통계", "보고서", "요약", "상세",
    "시작", "중지", "일시정지", "계속", "완료",
]

logger = get_logger(__name__)

# 컬러 팔레트 (프리미엄 다크 테마)
COLORS = {
    "bg_dark": "#0d1117",
    "bg_sidebar": "#161b22",
    "bg_content": "#0d1117",
    "bg_card": "#21262d",
    "bg_card_hover": "#30363d",
    "bg_log": "#010409",
    "accent": "#238636",
    "accent_hover": "#2ea043",
    "accent_blue": "#58a6ff",
    "accent_orange": "#d29922",
    "accent_red": "#f85149",
    "text_primary": "#f0f6fc",
    "text_secondary": "#8b949e",
    "text_muted": "#484f58",
    "border": "#30363d",
    "success": "#3fb950",
    "warning": "#d29922",
    "error": "#f85149",
    "danger": "#f85149",
    "danger_hover": "#da3633",
    "info": "#58a6ff",
}


class GUILogHandler(logging.Handler):
    """GUI 로그 핸들러"""

    def __init__(self, callback, max_lines: int = 500):
        super().__init__()
        self._callback = callback
        self._max_lines = max_lines
        self._lock = Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            level = record.levelname
            with self._lock:
                self._callback(msg, level)
        except (RuntimeError, ValueError, OSError):
            self.handleError(record)


class LogPanel(ctk.CTkFrame):
    """하단 로그 패널 - 축소/확장 가능, 확장시 크게 표시"""

    def __init__(self, parent, **kwargs):
        super().__init__(parent, fg_color=COLORS["bg_log"], **kwargs)

        self._log_buffer: deque = deque(maxlen=500)
        self._auto_scroll = True
        self._expanded = False  # 기본: 축소 상태
        self._lock = Lock()

        # 높이 설정
        self._collapsed_height = 32  # 축소시 헤더만
        self._expanded_height = 500  # 확장시 크게 (화면의 절반 정도)

        self._setup_ui()
        self._setup_log_handler()

    def _setup_ui(self):
        # 헤더 바
        self._header = ctk.CTkFrame(self, fg_color=COLORS["bg_card"], height=32)
        self._header.pack(fill="x")
        self._header.pack_propagate(False)

        # 토글 버튼 (클릭하면 확장/축소)
        self._toggle_btn = ctk.CTkButton(
            self._header,
            text="▶ 실시간 로그 (클릭하여 확장)",
            command=self._toggle_expand,
            width=180,
            height=28,
            fg_color="transparent",
            hover_color=COLORS["bg_card_hover"],
            text_color=COLORS["text_primary"],
            font=ctk.CTkFont(size=12, weight="bold"),
        )
        self._toggle_btn.pack(side="left", padx=5, pady=2)

        # 필터 콤보 (한글화)
        self._filter_var = ctk.StringVar(value="전체")
        self._filter_combo = ctk.CTkComboBox(
            self._header,
            values=["전체", "정보", "경고", "오류"],
            variable=self._filter_var,
            command=self._on_filter_change,
            width=80,
            height=24,
            fg_color=COLORS["bg_dark"],
            border_color=COLORS["border"],
            button_color=COLORS["bg_card_hover"],
            dropdown_fg_color=COLORS["bg_card"],
        )
        self._filter_combo.pack(side="left", padx=5, pady=4)

        # 지우기 버튼
        self._clear_btn = ctk.CTkButton(
            self._header,
            text="지우기",
            command=self._clear_logs,
            width=55,
            height=24,
            fg_color=COLORS["bg_dark"],
            hover_color=COLORS["bg_card_hover"],
            text_color=COLORS["text_secondary"],
        )
        self._clear_btn.pack(side="right", padx=8, pady=4)

        # 로그 개수
        self._count_label = ctk.CTkLabel(
            self._header,
            text="0개",
            font=ctk.CTkFont(size=11),
            text_color=COLORS["text_muted"],
        )
        self._count_label.pack(side="right", padx=5)

        # 로그 텍스트 영역 컨테이너 (기본 숨김)
        self._log_container = ctk.CTkFrame(self, fg_color=COLORS["bg_log"])
        # 기본은 축소 상태이므로 pack 하지 않음

        # 로그 텍스트 (확장시 크게 보임)
        self._log_text = tk.Text(
            self._log_container,
            wrap="none",
            font=("Consolas", 11),
            bg=COLORS["bg_log"],
            fg=COLORS["text_secondary"],
            insertbackground=COLORS["text_primary"],
            selectbackground=COLORS["accent_blue"],
            relief="flat",
            padx=10,
            pady=5,
            height=28,  # 확장시 28줄 표시
        )
        self._log_text.pack(side="left", fill="both", expand=True)

        # 스크롤바
        scrollbar = ctk.CTkScrollbar(
            self._log_container,
            command=self._log_text.yview,
            fg_color=COLORS["bg_log"],
            button_color=COLORS["bg_card"],
            button_hover_color=COLORS["bg_card_hover"],
        )
        scrollbar.pack(side="right", fill="y")
        self._log_text.configure(yscrollcommand=scrollbar.set, state="disabled")

        # 태그 설정
        self._log_text.tag_configure("DEBUG", foreground="#88c0d0")
        self._log_text.tag_configure("INFO", foreground=COLORS["success"])
        self._log_text.tag_configure("WARNING", foreground=COLORS["warning"])
        self._log_text.tag_configure("ERROR", foreground=COLORS["error"])
        self._log_text.tag_configure("CRITICAL", foreground="#b48ead")

        # ANSI 색상 태그
        self._log_text.tag_configure("ansi_green", foreground="#50fa7b")   # 초록 (액션 시작)
        self._log_text.tag_configure("ansi_yellow", foreground="#f1fa8c")  # 노랑 (성공)
        self._log_text.tag_configure("ansi_pink", foreground="#ff79c6")    # 분홍 (중지/실패)

    def _setup_log_handler(self):
        root_logger = logging.getLogger()

        # 기존 GUILogHandler 제거 (중복 등록 방지)
        for handler in root_logger.handlers[:]:
            if isinstance(handler, GUILogHandler):
                root_logger.removeHandler(handler)

        self._handler = GUILogHandler(self._add_log_message)
        self._handler.setLevel(logging.DEBUG)
        self._handler.setFormatter(logging.Formatter(
            "%(asctime)s │ %(levelname)-7s │ %(message)s",
            datefmt="%H:%M:%S"
        ))
        root_logger.addHandler(self._handler)

    def _add_log_message(self, message: str, level: str):
        with self._lock:
            self._log_buffer.append((message, level))
        try:
            # 위젯이 아직 유효한 경우에만 UI 업데이트 스케줄
            if self.winfo_exists():
                self.after(0, self._update_display, message, level)
        except tk.TclError:
            # 위젯이 파괴된 경우 무시 (정상 종료 시나리오)
            pass

    def _parse_ansi(self, message: str):
        """ANSI 코드를 파싱하여 (텍스트, 태그) 리스트 반환"""
        import re
        ESC = '\x1b'
        pattern = re.compile(ESC + r'\[(\d+)m')

        result = []
        current_tag = None
        last_end = 0

        for match in pattern.finditer(message):
            before = message[last_end:match.start()]
            if before:
                result.append((before, current_tag))

            code = match.group(1)
            if code == '92':
                current_tag = 'ansi_green'
            elif code == '93':
                current_tag = 'ansi_yellow'
            elif code == '95':
                current_tag = 'ansi_pink'
            elif code == '0':
                current_tag = None

            last_end = match.end()

        remaining = message[last_end:]
        if remaining:
            result.append((remaining, current_tag))

        if not result:
            result.append((message, None))

        return result

    def _update_display(self, message: str, level: str):
        current_filter = self._filter_var.get()
        # 한글 필터 매핑
        filter_map = {"전체": "ALL", "정보": "INFO", "경고": "WARNING", "오류": "ERROR"}
        mapped_filter = filter_map.get(current_filter, current_filter)
        if mapped_filter != "ALL" and level != mapped_filter:
            return

        try:
            self._log_text.configure(state="normal")

            # ANSI 코드 파싱 및 색상 적용
            parsed = self._parse_ansi(message)
            for text, tag in parsed:
                if tag:
                    self._log_text.insert("end", text, tag)
                else:
                    self._log_text.insert("end", text, level)
            self._log_text.insert("end", "\n")

            # 최대 라인 수 유지 (안전한 라인 수 계산)
            line_count = self._get_line_count()
            if line_count > 500:
                self._log_text.delete("1.0", "100.0")
                line_count = self._get_line_count()

            if self._auto_scroll:
                self._log_text.see("end")
            self._log_text.configure(state="disabled")

            self._count_label.configure(text=f"{line_count}개")
        except tk.TclError:
            pass

    def _toggle_expand(self):
        self._expanded = not self._expanded
        if self._expanded:
            # 확장: 로그 영역 표시
            self._log_container.pack(fill="both", expand=True)
            self.configure(height=self._expanded_height)
            # pack_configure로 높이 강제 적용
            self.pack_configure(ipadx=0, ipady=0)
            self._toggle_btn.configure(text="▼ 실시간 로그 (클릭하여 축소)")
            # 로그 내용 갱신
            self._refresh_display()
            self.update_idletasks()
            self.update()
        else:
            # 축소: 헤더만 표시
            self._log_container.pack_forget()
            self.configure(height=self._collapsed_height)
            # pack_configure로 높이 강제 적용
            self.pack_configure(ipadx=0, ipady=0)
            self._toggle_btn.configure(text="▶ 실시간 로그 (클릭하여 확장)")
            self.update_idletasks()
            self.update()

    def _on_filter_change(self, value: str):
        self._refresh_display()

    def _clear_logs(self):
        with self._lock:
            self._log_buffer.clear()
        self._log_text.configure(state="normal")
        self._log_text.delete("1.0", "end")
        self._log_text.configure(state="disabled")
        self._count_label.configure(text="0개")

    def _refresh_display(self):
        self._log_text.configure(state="normal")
        self._log_text.delete("1.0", "end")
        current_filter = self._filter_var.get()
        # 한글 필터 매핑
        filter_map = {"전체": "ALL", "정보": "INFO", "경고": "WARNING", "오류": "ERROR"}
        mapped_filter = filter_map.get(current_filter, current_filter)

        with self._lock:
            for message, level in self._log_buffer:
                if mapped_filter == "ALL" or level == mapped_filter:
                    # ANSI 코드 파싱 및 색상 적용
                    parsed = self._parse_ansi(message)
                    for text, tag in parsed:
                        if tag:
                            self._log_text.insert("end", text, tag)
                        else:
                            self._log_text.insert("end", text, level)
                    self._log_text.insert("end", "\n")

        # 로그 개수 업데이트 (안전한 라인 수 계산)
        line_count = self._get_line_count()
        self._count_label.configure(text=f"{line_count}개")

        if self._auto_scroll:
            self._log_text.see("end")
        self._log_text.configure(state="disabled")

    def _get_line_count(self) -> int:
        """텍스트 위젯의 라인 수를 안전하게 반환"""
        try:
            index_str = self._log_text.index("end-1c")
            parts = index_str.split(".")
            if parts and parts[0].isdigit():
                return int(parts[0])
            return 0
        except (tk.TclError, ValueError, IndexError):
            return 0

    def cleanup(self):
        try:
            logging.getLogger().removeHandler(self._handler)
        except (ValueError, RuntimeError):
            pass


class SidebarButton(ctk.CTkButton):
    """사이드바 네비게이션 버튼"""

    def __init__(self, parent, text, icon, command, **kwargs):
        super().__init__(
            parent,
            text=f"  {icon}  {text}",
            command=command,
            width=180,
            height=44,
            anchor="w",
            fg_color="transparent",
            hover_color=COLORS["bg_card"],
            text_color=COLORS["text_secondary"],
            font=ctk.CTkFont(size=14),
            corner_radius=8,
            **kwargs
        )
        self._is_active = False

    def set_active(self, active: bool):
        self._is_active = active
        if active:
            self.configure(
                fg_color=COLORS["accent"],
                text_color=COLORS["text_primary"],
                hover_color=COLORS["accent_hover"],
            )
        else:
            self.configure(
                fg_color="transparent",
                text_color=COLORS["text_secondary"],
                hover_color=COLORS["bg_card"],
            )


class MainWindow(ctk.CTk):
    """메인 윈도우 - 프리미엄 디자인"""

    def __init__(self):
        super().__init__()

        self._config = get_config()

        # 윈도우 설정 - 랜덤 이름 모드 적용
        import random
        if self._config.ui.random_name_mode:
            app_name = random.choice(RANDOM_NAMES)
        else:
            app_name = self._config.ui.app_name or "Desktop"
        self._app_name = app_name  # 로고에서도 사용
        self.title(f"{app_name}")

        # 창 모드별 크기 설정 (이전 값 호환)
        window_mode = self._config.ui.window_mode or "editor"
        # 이전 값 → 새 값 변환
        if window_mode == "small":
            window_mode = "play"
        elif window_mode in ("medium", "large"):
            window_mode = "editor"
        self._window_mode = window_mode
        if window_mode == "play":
            self.geometry("480x320")
            self.minsize(450, 280)
        else:  # editor
            self.geometry(f"{self._config.ui.window_width}x{self._config.ui.window_height}")
            self.minsize(1000, 700)

        self.configure(fg_color=COLORS["bg_dark"])

        # 테마 설정
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        # 윈도우 닫기 이벤트
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # 뷰 저장소
        self._views: Dict[str, ctk.CTkFrame] = {}
        self._current_view: Optional[str] = None
        self._nav_buttons: Dict[str, SidebarButton] = {}

        # 전역 F8 캡쳐 기능
        self._keyboard_listener: Optional[keyboard.Listener] = None
        self._capture_notification_label: Optional[ctk.CTkLabel] = None
        self._recording_active = False  # 녹화 중이면 전역 F8 비활성화

        # UI 구성
        self._setup_ui()

        # UI 설정 후 전역 단축키 활성화 (플레이 모드 제외)
        if self._window_mode != "play":
            self._setup_global_hotkey()

        # 창 위치 복원 및 자동 저장
        self.update_idletasks()
        setup_window_position(self, "MainWindow")

        logger.info("메인 윈도우 초기화 완료")

    def _setup_ui(self):
        # 메인 컨테이너
        self._main_container = ctk.CTkFrame(self, fg_color=COLORS["bg_dark"])
        self._main_container.pack(fill="both", expand=True)

        # 플레이 모드면 미니 플레이어 UI
        if self._window_mode == "play":
            self._setup_mini_player_ui()
            return

        # 상단 네비게이션 바
        self._setup_topbar()

        # 콘텐츠 영역
        self._top_area = ctk.CTkFrame(self._main_container, fg_color=COLORS["bg_dark"])
        self._top_area.pack(fill="both", expand=True)

        self._setup_content_area()

        # 하단 로그 패널
        self._setup_log_panel()

    def _setup_mini_player_ui(self):
        """미니 플레이어 UI (플레이 모드)"""
        import json
        # 플랜 로드
        self._mini_plans = []
        logger.info(f"[미니플레이어] 플랜 폴더: {PLANS_DIR}")
        if PLANS_DIR.exists():
            templates_dir = DATA_DIR / "templates"
            for plan_file in PLANS_DIR.glob("*.json"):
                try:
                    with open(plan_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        plan = AutomationPlan.from_dict(data, templates_dir=templates_dir)
                        self._mini_plans.append(plan)
                        logger.info(f"[미니플레이어] 플랜 로드 성공: {plan.name}")
                except Exception as e:
                    logger.error(f"플랜 로드 실패: {plan_file} - {e}")
        logger.info(f"[미니플레이어] 총 {len(self._mini_plans)}개 플랜 로드됨")

        self._rule_executor = None
        self._is_running = False
        self._is_paused = False
        self._mini_current_repeat = 0
        self._mini_total_repeat = 1

        # UI 생성
        self._create_mini_player_ui()

    def _create_mini_player_ui(self):
        """미니 플레이어 UI 요소 생성"""
        # 상단 프레임 (재생목록)
        top_frame = ctk.CTkFrame(self._main_container, fg_color=COLORS["bg_card"])
        top_frame.pack(fill="x", padx=10, pady=(10, 5))

        ctk.CTkLabel(
            top_frame,
            text="재생 목록",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COLORS["text_secondary"],
        ).pack(side="left", padx=10, pady=8)

        # 에디터 모드 전환 버튼 (오른쪽 끝)
        ctk.CTkButton(
            top_frame,
            text="✏ 에디터",
            width=70,
            height=26,
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color=COLORS["bg_card_hover"],
            hover_color=COLORS["border"],
            text_color=COLORS["text_primary"],
            corner_radius=6,
            command=lambda: self._change_window_mode("editor"),
        ).pack(side="right", padx=(0, 10), pady=8)

        # 횟수 설정 (오른쪽)
        repeat_frame = ctk.CTkFrame(top_frame, fg_color="transparent")
        repeat_frame.pack(side="right", padx=(0, 10), pady=8)

        # 재생횟수 저장 버튼
        self._mini_repeat_save_btn = ctk.CTkButton(
            repeat_frame,
            text="저장",
            width=35,
            height=28,
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            font=ctk.CTkFont(size=10),
            command=self._save_mini_repeat_count,
        )
        self._mini_repeat_save_btn.pack(side="right", padx=2)

        ctk.CTkLabel(
            repeat_frame,
            text="회",
            font=ctk.CTkFont(size=11),
            text_color=COLORS["text_secondary"],
        ).pack(side="right")

        self._mini_repeat_var = ctk.StringVar(value="1")
        self._mini_repeat_entry = ctk.CTkEntry(
            repeat_frame,
            textvariable=self._mini_repeat_var,
            width=40,
            height=28,
            fg_color=COLORS["bg_dark"],
            border_color=COLORS["border"],
            text_color=COLORS["text_primary"],
            font=ctk.CTkFont(size=11),
            justify="center",
        )
        self._mini_repeat_entry.pack(side="right", padx=2)

        ctk.CTkLabel(
            repeat_frame,
            text="반복:",
            font=ctk.CTkFont(size=11),
            text_color=COLORS["text_secondary"],
        ).pack(side="right", padx=(0, 5))

        # 새로고침 버튼
        ctk.CTkButton(
            top_frame,
            text="🔄",
            width=30,
            height=26,
            fg_color=COLORS["bg_dark"],
            hover_color=COLORS["border"],
            font=ctk.CTkFont(size=12),
            command=self._on_mini_refresh,
        ).pack(side="right", padx=(0, 5), pady=8)

        # 중간 프레임 (플랜 선택 + 부분실행)
        middle_frame = ctk.CTkFrame(self._main_container, fg_color=COLORS["bg_card"])
        middle_frame.pack(fill="x", padx=10, pady=5)

        # 플랜 드롭다운
        plan_names = [p.name for p in self._mini_plans] if self._mini_plans else ["(플랜 없음)"]
        self._mini_plan_var = ctk.StringVar(value=plan_names[0] if plan_names else "(플랜 없음)")
        self._mini_plan_dropdown = ctk.CTkComboBox(
            middle_frame,
            variable=self._mini_plan_var,
            values=plan_names,
            width=200,
            height=32,
            fg_color=COLORS["bg_dark"],
            border_color=COLORS["border"],
            button_color=COLORS["accent"],
            button_hover_color=COLORS["accent_hover"],
            dropdown_fg_color=COLORS["bg_card"],
            dropdown_hover_color=COLORS["bg_card_hover"],
            font=ctk.CTkFont(size=12),
            state="readonly",
        )
        self._mini_plan_dropdown.pack(side="left", padx=10, pady=10)

        # 부분 실행 버튼
        ctk.CTkButton(
            middle_frame,
            text="📋 부분실행",
            width=80,
            height=32,
            fg_color=COLORS["bg_dark"],
            hover_color=COLORS["border"],
            text_color=COLORS["text_primary"],
            font=ctk.CTkFont(size=11),
            command=self._open_partial_execution,
        ).pack(side="left", padx=(0, 10), pady=10)

        # 하단 프레임 (컨트롤)
        bottom_frame = ctk.CTkFrame(self._main_container, fg_color=COLORS["bg_card"])
        bottom_frame.pack(fill="x", padx=10, pady=(5, 10))

        # 실행/중지 버튼
        btn_frame = ctk.CTkFrame(bottom_frame, fg_color="transparent")
        btn_frame.pack(pady=10)

        self._mini_play_btn = ctk.CTkButton(
            btn_frame,
            text="▶ 실행",
            width=100,
            height=40,
            fg_color=COLORS["success"],
            hover_color="#45a049",
            font=ctk.CTkFont(size=14, weight="bold"),
            command=self._mini_on_play,
        )
        self._mini_play_btn.pack(side="left", padx=5)

        self._mini_pause_btn = ctk.CTkButton(
            btn_frame,
            text="⏸ 일시정지",
            width=100,
            height=40,
            fg_color=COLORS["warning"],
            hover_color="#e6a800",
            font=ctk.CTkFont(size=14, weight="bold"),
            command=self._mini_on_pause,
            state="disabled",
        )
        self._mini_pause_btn.pack(side="left", padx=5)

        self._mini_stop_btn = ctk.CTkButton(
            btn_frame,
            text="■ 중지",
            width=100,
            height=40,
            fg_color=COLORS["error"],
            hover_color="#d32f2f",
            font=ctk.CTkFont(size=14, weight="bold"),
            command=self._mini_on_stop,
            state="disabled",
        )
        self._mini_stop_btn.pack(side="left", padx=5)

        # 상태 표시
        self._mini_status = ctk.CTkLabel(
            bottom_frame,
            text="대기 중",
            font=ctk.CTkFont(size=11),
            text_color=COLORS["text_secondary"],
        )
        self._mini_status.pack(pady=(0, 10))

        # 로그 영역
        log_frame = ctk.CTkFrame(self._main_container, fg_color=COLORS["bg_card"])
        log_frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        ctk.CTkLabel(
            log_frame,
            text="실행 로그",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COLORS["text_secondary"],
        ).pack(anchor="w", padx=10, pady=(8, 5))

        self._mini_log = ctk.CTkTextbox(
            log_frame,
            fg_color=COLORS["bg_dark"],
            text_color=COLORS["text_primary"],
            font=ctk.CTkFont(family="Consolas", size=11),
            wrap="word",
        )
        self._mini_log.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        # 로그 핸들러 설정
        self._setup_mini_log_handler()

    def _refresh_mini_plans(self):
        """플랜 목록 새로고침 - 디스크에서 최신 버전 로드"""
        import json
        old_count = len(self._mini_plans)
        self._mini_plans = []
        if PLANS_DIR.exists():
            templates_dir = DATA_DIR / "templates"
            for plan_file in PLANS_DIR.glob("*.json"):
                try:
                    with open(plan_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        plan = AutomationPlan.from_dict(data, templates_dir=templates_dir)
                        self._mini_plans.append(plan)
                except Exception as e:
                    logger.error(f"[미니플레이어] 플랜 새로고침 실패: {plan_file} - {e}")
        logger.info(f"[미니플레이어] 플랜 새로고침 완료: {old_count} → {len(self._mini_plans)}개")

        # 드롭다운 업데이트
        if hasattr(self, '_mini_plan_dropdown') and self._mini_plan_dropdown:
            plan_names = [p.name for p in self._mini_plans] if self._mini_plans else ["(플랜 없음)"]
            self._mini_plan_dropdown.configure(values=plan_names)
            # 현재 선택 유지 또는 첫번째 선택
            current = self._mini_plan_var.get()
            if current not in plan_names:
                self._mini_plan_var.set(plan_names[0] if plan_names else "(플랜 없음)")

    def _setup_mini_log_handler(self):
        """미니 플레이어용 로그 핸들러 설정"""
        import re

        # ANSI 색상 코드 매핑
        ansi_map = {
            '\033[96m': 'ansi_cyan',    # 청록 (액션 번호)
            '\033[92m': 'ansi_green',   # 초록
            '\033[93m': 'ansi_yellow',  # 노랑
            '\033[95m': 'ansi_pink',    # 분홍
            '\033[91m': 'ansi_red',     # 빨강
        }

        def add_log(msg: str, level: str):
            try:
                self._mini_log_text.configure(state="normal")
                # 최대 100줄 유지
                line_count = int(self._mini_log_text.index('end-1c').split('.')[0])
                if line_count > 100:
                    self._mini_log_text.delete('1.0', '2.0')

                # 타임스탬프 제거하고 메시지만 표시
                if " - " in msg:
                    msg = msg.split(" - ", 1)[-1]

                # ANSI 코드 파싱하여 색상 적용
                tag = level if level in ["INFO", "WARNING", "ERROR", "DEBUG"] else "INFO"

                # ANSI 코드가 있는지 확인
                has_ansi = '\033[' in msg
                if has_ansi:
                    # ANSI 코드로 색상 결정
                    for ansi_code, ansi_tag in ansi_map.items():
                        if ansi_code in msg:
                            tag = ansi_tag
                            break
                    # ANSI 코드 제거
                    msg = re.sub(r'\033\[[0-9;]*m', '', msg)

                # 액션 번호가 있으면 상태 업데이트 (예: [3-1] 또는 [1])
                action_match = re.search(r'\[([0-9\-]+)\]', msg)
                if action_match and hasattr(self, '_mini_status'):
                    action_num = action_match.group(1)
                    # 액션 이름 추출
                    action_name = msg.split(']', 1)[-1].strip() if ']' in msg else ""
                    if action_name:
                        self._mini_status.configure(
                            text=f"▶ [{action_num}] {action_name[:20]}",
                            text_color=COLORS["accent"]
                        )

                self._mini_log_text.insert("end", f"{msg}\n", tag)
                self._mini_log_text.see("end")
                self._mini_log_text.configure(state="disabled")
            except (tk.TclError, RuntimeError):
                pass

        handler = GUILogHandler(add_log, max_lines=100)
        handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s', '%H:%M:%S'))
        handler.setLevel(logging.DEBUG)
        logging.getLogger().addHandler(handler)

    def _show_mode_menu(self):
        """창 모드 변경 메뉴"""
        menu = tk.Menu(self, tearoff=0)
        menu.configure(
            bg=COLORS["bg_card"],
            fg=COLORS["text_primary"],
            activebackground=COLORS["accent"],
            activeforeground="white",
        )
        menu.add_command(label="부분 액션 실행", command=self._open_partial_execution)

        # 버튼 위치에 메뉴 표시
        try:
            menu.tk_popup(self.winfo_pointerx(), self.winfo_pointery())
        finally:
            menu.grab_release()

    def _open_partial_execution(self):
        """에디터 모드의 계획 수정 다이얼로그 열기 (플레이 모드 방식으로 플랜 로드)"""
        import json
        from .player_view import PlanDetailDialog, PLANS_DIR

        # 선택된 플랜 가져오기
        plan_name = self._mini_plan_var.get()
        if not plan_name or plan_name == "(플랜 없음)":
            logger.warning("플랜을 먼저 선택하세요")
            return

        # 플랜 찾기 (plan_id 확인용)
        cached_plan = None
        for p in self._mini_plans:
            if p.name == plan_name:
                cached_plan = p
                break

        if not cached_plan:
            logger.warning("플랜을 찾을 수 없습니다")
            return

        # 플레이 모드처럼 JSON에서 플랜 새로 로드
        plan_file = PLANS_DIR / f"{cached_plan.plan_id}.json"
        if not plan_file.exists():
            logger.error(f"플랜 파일 없음: {plan_file}")
            return

        try:
            with open(plan_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                templates_dir = DATA_DIR / "templates"
                loaded_plan = AutomationPlan.from_dict(data, templates_dir=templates_dir)
            logger.info(f"[플레이모드 테스트] 플랜 로드: {plan_name}")
        except Exception as e:
            logger.error(f"플랜 로드 실패: {e}")
            return

        # 계획 수정 다이얼로그 열기 (에디터 모드와 동일한 UI)
        PlanDetailDialog(self, loaded_plan)

    def _change_window_mode(self, mode: str):
        """창 모드 변경 후 자동 재시작"""
        logger.info(f"[모드변경] 현재 auto_check={self._config.update.auto_check}, 변경할 모드={mode}")
        self._config.ui.window_mode = mode
        save_config()
        logger.info(f"[모드변경] 설정 저장 완료, auto_check={self._config.update.auto_check}")

        logger.info(f"창 모드 변경: {mode}, 자동 재시작...")

        # 자동 재시작
        import sys
        import subprocess

        # 현재 실행 파일 경로
        python = sys.executable
        script = sys.argv[0]

        # 새 프로세스 시작
        subprocess.Popen([python, script])

        # 현재 프로세스 종료
        self.destroy()
        sys.exit(0)

    def _on_mini_plan_changed(self, plan_name: str):
        """미니 플레이어 - 플랜 변경 시 재생횟수 불러오기"""
        if not plan_name or plan_name == "(플랜 없음)":
            return

        for plan in self._mini_plans:
            if plan.name == plan_name:
                # 저장된 재생횟수 불러오기
                saved_repeat = getattr(plan, 'total_repeat_count', 1) or 1
                self._mini_repeat_var.set(str(saved_repeat))
                logger.info(f"[미니플레이어] 플랜 변경: {plan_name}, 재생횟수: {saved_repeat}")
                break

    def _save_mini_repeat_count(self):
        """미니 플레이어 - 재생횟수 저장"""
        import json
        plan_name = self._mini_plan_var.get()
        if not plan_name or plan_name == "(플랜 없음)":
            self._mini_status.configure(text="⚠ 플랜을 선택하세요")
            return

        selected_plan = None
        for plan in self._mini_plans:
            if plan.name == plan_name:
                selected_plan = plan
                break

        if not selected_plan:
            self._mini_status.configure(text="⚠ 플랜을 찾을 수 없음")
            return

        try:
            repeat_count = int(self._mini_repeat_var.get())
            if repeat_count < 1:
                repeat_count = 1
            elif repeat_count > 9999:
                repeat_count = 9999

            selected_plan.total_repeat_count = repeat_count

            # 플랜 파일에 저장
            from .player_view import PLANS_DIR
            PLANS_DIR.mkdir(parents=True, exist_ok=True)
            plan_file = PLANS_DIR / f"{selected_plan.plan_id}.json"
            with open(plan_file, 'w', encoding='utf-8') as f:
                json.dump(selected_plan.to_dict(), f, ensure_ascii=False, indent=2)

            self._mini_status.configure(text=f"✓ 재생횟수 {repeat_count}회 저장됨")
            logger.info(f"[미니플레이어] 재생횟수 저장: {repeat_count}회 - {plan_name}")
        except ValueError:
            self._mini_status.configure(text="⚠ 올바른 숫자를 입력하세요")
        except Exception as e:
            logger.error(f"[미니플레이어] 재생횟수 저장 실패: {e}")
            self._mini_status.configure(text=f"⚠ 저장 실패")

    def _mini_on_play(self):
        """미니 플레이어 - 실행"""
        logger.info("[미니플레이어] 실행 버튼 클릭")

        if self._is_paused and self._rule_executor:
            # 일시중지 해제
            self._rule_executor.resume()
            self._is_paused = False
            self._mini_status.configure(text="▶ 실행 중...")
            self._mini_pause_btn.configure(text="⏸ 일시중지")
            return

        plan_name = self._mini_plan_var.get()
        logger.info(f"[미니플레이어] 선택된 플랜: {plan_name}")

        if not plan_name or plan_name == "(플랜 없음)":
            self._mini_status.configure(text="⚠ 플랜을 선택하세요")
            return

        # 실행 전 플랜 목록 새로고침 (에디터에서 변경된 내용 반영)
        self._refresh_mini_plans()

        # 횟수 파싱
        try:
            repeat_count = int(self._mini_repeat_var.get())
            if repeat_count < 1:
                repeat_count = 1
            elif repeat_count > 9999:
                repeat_count = 9999
        except ValueError:
            repeat_count = 1
        self._mini_total_repeat = repeat_count
        self._mini_current_repeat = 0

        # 플랜 찾기 (캐시에서 plan_id 확인)
        cached_plan = None
        for p in self._mini_plans:
            if p.name == plan_name:
                cached_plan = p
                break

        if not cached_plan:
            self._mini_status.configure(text="⚠ 플랜을 찾을 수 없음")
            return

        # JSON에서 최신 플랜 다시 로드 (수정사항 반영)
        import json
        from .player_view import PLANS_DIR
        plan_file = PLANS_DIR / f"{cached_plan.plan_id}.json"
        selected_plan = None
        if plan_file.exists():
            try:
                with open(plan_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    templates_dir = DATA_DIR / "templates"
                    selected_plan = AutomationPlan.from_dict(data, templates_dir=templates_dir)
                # 모니터링 모드 확인 로그 (디버그용 상세 출력)
                for idx, rule in enumerate(selected_plan.initial_rules):
                    if getattr(rule, 'is_monitoring_mode', False):
                        watches = getattr(rule, 'monitoring_watches', []) or []
                        logger.info(f"[미니플레이어] 룰 {idx+1}: 모니터링모드=True, 감시={len(watches)}개")
                        # 상세 디버그 로그
                        for w_idx, watch in enumerate(watches):
                            logger.debug(f"[미니플레이어] watch[{w_idx}] 키: {list(watch.keys())}")
                            logger.debug(f"[미니플레이어] watch[{w_idx}]['confidence']: {watch.get('confidence', 'NOT FOUND')}")
                            logger.debug(f"[미니플레이어] watch[{w_idx}]['search_region']: {watch.get('search_region', 'NOT FOUND')}")
                            monitor_actions = watch.get('monitor_actions', [])
                            for ma_idx, ma in enumerate(monitor_actions):
                                logger.debug(f"[미니플레이어] watch[{w_idx}].monitor_action[{ma_idx}] 키: {list(ma.keys()) if ma else 'None'}")
                                logger.debug(f"[미니플레이어] watch[{w_idx}].monitor_action[{ma_idx}]['confidence']: {ma.get('confidence', 'NOT FOUND') if ma else 'None'}")
                                logger.debug(f"[미니플레이어] watch[{w_idx}].monitor_action[{ma_idx}]['search_region']: {ma.get('search_region', 'NOT FOUND') if ma else 'None'}")
                logger.info(f"[미니플레이어] 플랜 최신 버전 로드: {plan_name}")
            except Exception as e:
                import traceback
                logger.warning(f"[미니플레이어] 플랜 재로드 실패, 캐시 사용: {e}")
                logger.warning(f"[미니플레이어] 상세 오류: {traceback.format_exc()}")
                selected_plan = cached_plan
                # 캐시 버전 정보 로그
                for idx, rule in enumerate(selected_plan.initial_rules):
                    if getattr(rule, 'is_monitoring_mode', False):
                        watches = getattr(rule, 'monitoring_watches', []) or []
                        logger.warning(f"[미니플레이어] 캐시 룰 {idx+1}: 감시={len(watches)}개 (이전 버전일 수 있음!)")
        else:
            selected_plan = cached_plan

        try:
            # RuleExecutor 생성 및 실행
            logger.info(f"[미니플레이어] RuleExecutor 생성, 반복: {repeat_count}회")
            self._rule_executor = RuleExecutor()
            self._rule_executor.set_callbacks(
                on_progress=self._mini_on_progress,
                on_complete=self._mini_on_repeat_complete,
            )

            self._is_running = True
            self._mini_play_btn.configure(state="disabled")
            self._mini_pause_btn.configure(state="normal")
            self._mini_stop_btn.configure(state="normal")
            self._mini_status.configure(text=f"▶ 실행 중... (1/{repeat_count}회)")

            # 비동기 실행
            import threading
            thread = threading.Thread(
                target=self._mini_execute_plan,
                args=(selected_plan,),
                daemon=True
            )
            thread.start()
            logger.info(f"[미니플레이어] 실행 스레드 시작")
        except Exception as e:
            logger.error(f"[미니플레이어] 실행 오류: {e}")
            self._mini_status.configure(text=f"✗ 오류: {e}")
            self._is_running = False
            self._mini_play_btn.configure(state="normal")
            self._mini_pause_btn.configure(state="disabled", text="⏸ 일시중지")
            self._mini_stop_btn.configure(state="disabled")

    def _mini_execute_plan(self, plan):
        """미니 플레이어 - 플랜 실행 (스레드)"""
        try:
            self._rule_executor.execute_plan(plan)
        except Exception as e:
            logger.error(f"[미니플레이어] 플랜 실행 오류: {e}")
            self.after(0, lambda: self._mini_on_complete(False, str(e)))

    def _mini_on_pause(self):
        """미니 플레이어 - 일시중지/재개"""
        if not self._rule_executor:
            return

        if self._is_paused:
            self._rule_executor.resume()
            self._is_paused = False
            self._mini_status.configure(text="▶ 실행 중...")
            self._mini_pause_btn.configure(text="⏸ 일시중지")
        else:
            self._rule_executor.pause()
            self._is_paused = True
            self._mini_status.configure(text="⏸ 일시중지됨")
            self._mini_pause_btn.configure(text="▶ 재개")

    def _mini_on_stop(self):
        """미니 플레이어 - 중지"""
        if self._rule_executor:
            self._rule_executor.stop()
        self._is_running = False
        self._is_paused = False
        self._mini_play_btn.configure(state="normal")
        self._mini_pause_btn.configure(state="disabled", text="⏸ 일시중지")
        self._mini_stop_btn.configure(state="disabled")
        self._mini_status.configure(text="■ 중지됨")

    def _mini_on_progress(self, progress):
        """미니 플레이어 - 진행 콜백 (ExecutionProgress 객체)"""
        current = progress.initial_completed
        total = progress.initial_total
        message = progress.message or progress.current_rule or ""
        repeat_info = f"({self._mini_current_repeat + 1}/{self._mini_total_repeat}회)" if self._mini_total_repeat > 1 else ""
        self.after(0, lambda: self._mini_status.configure(
            text=f"▶ {current}/{total} {repeat_info} - {message}"
        ))

    def _mini_on_repeat_complete(self, success, message):
        """미니 플레이어 - 1회 실행 완료 콜백 (반복 처리)"""
        if not success:
            # 실패하면 중지
            self.after(0, lambda: self._mini_on_complete(False, message))
            return

        self._mini_current_repeat += 1

        if self._mini_current_repeat >= self._mini_total_repeat:
            # 모든 반복 완료
            self.after(0, lambda: self._mini_on_complete(True, ""))
            return

        if not self._is_running:
            # 중지됨
            self.after(0, lambda: self._mini_on_complete(False, "중지됨"))
            return

        # 다음 반복 실행
        logger.info(f"[미니플레이어] 반복 {self._mini_current_repeat + 1}/{self._mini_total_repeat} 시작")
        self.after(0, lambda: self._mini_status.configure(
            text=f"▶ 실행 중... ({self._mini_current_repeat + 1}/{self._mini_total_repeat}회)"
        ))

        # 현재 선택된 플랜 다시 실행
        plan_name = self._mini_plan_var.get()
        selected_plan = None
        for p in self._mini_plans:
            if p.name == plan_name:
                selected_plan = p
                break

        if selected_plan:
            import threading
            thread = threading.Thread(
                target=self._mini_execute_plan,
                args=(selected_plan,),
                daemon=True
            )
            thread.start()

    def _mini_on_complete(self, success, message):
        """미니 플레이어 - 완료 콜백"""
        def update():
            self._is_running = False
            self._is_paused = False
            self._mini_play_btn.configure(state="normal")
            self._mini_pause_btn.configure(state="disabled", text="⏸ 일시중지")
            self._mini_stop_btn.configure(state="disabled")
            if success:
                status = f"✓ 완료 ({self._mini_total_repeat}회)"
            else:
                status = f"✗ 실패: {message}"
            self._mini_status.configure(text=status)
        self.after(0, update)

    def _setup_topbar(self):
        """상단 네비게이션 바 설정"""
        self._topbar = ctk.CTkFrame(
            self._main_container,
            fg_color=COLORS["bg_sidebar"],
            height=60,
            corner_radius=0,
        )
        self._topbar.pack(fill="x")
        self._topbar.pack_propagate(False)

        # 왼쪽: 로고
        logo_frame = ctk.CTkFrame(self._topbar, fg_color="transparent")
        logo_frame.pack(side="left", padx=20)

        ctk.CTkLabel(
            logo_frame,
            text=f"🤖 {self._app_name}",
            font=ctk.CTkFont(size=22, weight="bold"),
            text_color=COLORS["accent"],
        ).pack(side="left")

        # 네비게이션 버튼들 (순서: 녹화, 분석, 실행, 설정, 가이드)
        nav_items = [
            ("recorder", "화면 녹화", "⏺"),
            ("analyzer", "동작 분석", "🔍"),
            ("player", "실행", "▶"),
            ("settings", "환경 설정", "⚙"),
            ("guide", "가이드", "📖"),
        ]

        # 네비게이션 버튼 컨테이너
        nav_container = ctk.CTkFrame(self._topbar, fg_color="transparent")
        nav_container.pack(side="left", padx=30)

        for view_id, label, icon in nav_items:
            if view_id == "help":
                cmd = self._show_help
            else:
                cmd = lambda v=view_id: self._switch_view(v)

            btn = ctk.CTkButton(
                nav_container,
                text=f"{icon}  {label}",
                command=cmd,
                width=110,
                height=40,
                fg_color="transparent",
                hover_color=COLORS["bg_card"],
                text_color=COLORS["text_secondary"],
                font=ctk.CTkFont(size=14, weight="bold"),
                corner_radius=8,
            )
            btn.pack(side="left", padx=3)
            if view_id != "help":
                self._nav_buttons[view_id] = btn

        # 오른쪽: 버전 정보
        version_frame = ctk.CTkFrame(self._topbar, fg_color=COLORS["bg_card"], corner_radius=6)
        version_frame.pack(side="right", padx=20, pady=12)

        self._version_label = ctk.CTkLabel(
            version_frame,
            text=f"  v{APP_VERSION}  ",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=COLORS["accent_blue"],
        )
        self._version_label.pack(padx=8, pady=4)

        # 모드 전환 버튼 (버전 왼쪽)
        current_mode = self._config.ui.window_mode
        if current_mode == "play":
            mode_text = "✏ 에디터"
            next_mode = "editor"
        else:
            mode_text = "▶ 플레이"
            next_mode = "play"

        self._mode_switch_btn = ctk.CTkButton(
            self._topbar,
            text=mode_text,
            width=80,
            height=28,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color=COLORS["bg_card"],
            hover_color=COLORS["bg_card_hover"],
            text_color=COLORS["text_primary"],
            corner_radius=6,
            command=lambda: self._change_window_mode(next_mode),
        )
        self._mode_switch_btn.pack(side="right", padx=(0, 10), pady=12)

    def _setup_content_area(self):
        self._content_area = ctk.CTkFrame(
            self._top_area,
            fg_color=COLORS["bg_content"],
            corner_radius=0,
        )
        self._content_area.pack(fill="both", expand=True)

        # 뷰 컨테이너
        self._view_container = ctk.CTkFrame(
            self._content_area,
            fg_color=COLORS["bg_content"],
        )
        self._view_container.pack(fill="both", expand=True, padx=15, pady=(10, 5))

    def _setup_log_panel(self):
        # 로그 패널 (기본 축소 상태, 클릭하면 크게 확장)
        self._log_panel = LogPanel(self._main_container, height=32)
        self._log_panel.pack(fill="x", side="bottom")
        self._log_panel.pack_propagate(False)

    def _setup_global_hotkey(self):
        """전역 F8 캡쳐 단축키 설정"""
        def on_key_press(key):
            try:
                if key == keyboard.Key.f8:
                    logger.debug(f"F8 키 감지됨, 녹화 활성: {self._recording_active}")
                    # 녹화 중이면 전역 캡쳐 비활성화 (녹화 세션에서 처리)
                    if self._recording_active:
                        return
                    # UI 스레드에서 캡쳐 실행
                    logger.info("F8 전체화면 캡쳐 시작...")
                    self.after(0, self._capture_full_screen)
            except Exception as e:
                logger.error(f"F8 키 처리 오류: {e}")

        try:
            self._keyboard_listener = keyboard.Listener(on_press=on_key_press)
            self._keyboard_listener.start()
            logger.info("전역 F8 캡쳐 단축키 활성화")
        except Exception as e:
            logger.error(f"전역 단축키 설정 실패: {e}")

    def set_recording_active(self, active: bool):
        """녹화 상태 설정 (녹화 중이면 전역 F8 비활성화)"""
        self._recording_active = active

    def _capture_full_screen(self):
        """전체 화면 캡쳐 (F8) - 캡쳐 후 크롭 다이얼로그 자동 열기"""
        try:
            logger.debug("_capture_full_screen 호출됨")

            # templates 폴더 확인/생성 (트리거 이미지도 templates에 저장)
            templates_dir = DATA_DIR / "templates"
            templates_dir.mkdir(parents=True, exist_ok=True)
            logger.debug(f"템플릿 폴더: {templates_dir}")

            # 화면 캡쳐 (mss 사용 - 녹화와 동일한 방식)
            with mss.mss() as sct:
                if not sct.monitors:
                    logger.error("모니터를 찾을 수 없습니다")
                    return
                monitor = sct.monitors[0]  # 전체 화면
                screenshot = sct.grab(monitor)
                screenshot_arr = np.array(screenshot)
                # 채널 수 확인 후 변환
                if len(screenshot_arr.shape) == 3 and screenshot_arr.shape[2] == 4:
                    screenshot_bgr = cv2.cvtColor(screenshot_arr, cv2.COLOR_BGRA2BGR)
                elif len(screenshot_arr.shape) == 3 and screenshot_arr.shape[2] == 3:
                    screenshot_bgr = cv2.cvtColor(screenshot_arr, cv2.COLOR_RGB2BGR)
                else:
                    screenshot_bgr = screenshot_arr
            logger.debug(f"스크린샷 크기: {screenshot_bgr.shape}")

            # 파일 저장
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
            filename = f"trigger_{timestamp}.png"
            filepath = templates_dir / filename

            success = cv2.imwrite(str(filepath), screenshot_bgr)
            if success:
                logger.info(f"F8 전체화면 캡쳐 저장: {filepath}")
                # 크롭 다이얼로그 열기 (UI 스레드에서 실행)
                self.after(100, lambda: self._open_crop_dialog(str(filepath)))
            else:
                logger.error(f"이미지 저장 실패: {filepath}")

        except Exception as e:
            import traceback
            logger.error(f"화면 캡쳐 실패: {e}\n{traceback.format_exc()}")

    def _open_crop_dialog(self, filepath: str):
        """크롭 다이얼로그 열기"""
        from .analyzer_view import ImageCropDialog

        crop_saved = {"value": False}  # 크롭 저장 여부 추적

        def on_crop_complete(path: str):
            crop_saved["value"] = True
            logger.info(f"크롭 완료: {path}")
            self._show_capture_notification(path)

        def on_delete():
            crop_saved["value"] = True  # 삭제도 처리된 것으로 간주
            logger.info(f"캡쳐 취소됨 (이미지 삭제)")

        def on_dialog_close():
            # 크롭하지 않고 취소한 경우 파일 삭제
            if not crop_saved["value"]:
                try:
                    Path(filepath).unlink()
                    logger.info(f"크롭 취소 - 전체화면 이미지 삭제: {filepath}")
                except Exception as e:
                    logger.warning(f"이미지 삭제 실패: {e}")

        try:
            dialog = ImageCropDialog(
                self,
                filepath,
                on_crop=on_crop_complete,
                on_delete=on_delete,
            )
            dialog.title("F8 캡쳐 - 영역을 선택하여 크롭하세요")
            # 다이얼로그 닫힐 때 처리
            dialog.bind("<Destroy>", lambda e: on_dialog_close() if e.widget == dialog else None)
        except Exception as e:
            logger.error(f"크롭 다이얼로그 열기 실패: {e}")

    def _show_capture_notification(self, filepath: str):
        """캡쳐 완료 알림 표시"""
        # 기존 알림 제거
        if self._capture_notification_label:
            try:
                self._capture_notification_label.destroy()
            except tk.TclError:
                pass

        # 알림 레이블 생성
        filename = Path(filepath).name
        self._capture_notification_label = ctk.CTkLabel(
            self,
            text=f"📸 화면 캡쳐 완료: {filename}",
            fg_color=COLORS["accent"],
            corner_radius=8,
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color="white",
        )
        self._capture_notification_label.place(relx=0.5, rely=0.05, anchor="center")

        # 2초 후 알림 제거
        def remove_notification():
            if self._capture_notification_label:
                try:
                    self._capture_notification_label.destroy()
                    self._capture_notification_label = None
                except tk.TclError:
                    pass

        self.after(2000, remove_notification)

    def _switch_view(self, view_id: str):
        """뷰 전환"""
        # 현재 뷰 숨기기
        if self._current_view and self._current_view in self._views:
            self._views[self._current_view].pack_forget()

        # 네비게이션 버튼 상태 업데이트
        for btn_id, btn in self._nav_buttons.items():
            if btn_id == view_id:
                btn.configure(fg_color=COLORS["accent"], text_color="white")
            else:
                btn.configure(fg_color="transparent", text_color=COLORS["text_secondary"])

        # 새 뷰 표시
        if view_id in self._views:
            self._views[view_id].pack(fill="both", expand=True)
            self._current_view = view_id

            # 뷰 자동 새로고침
            if hasattr(self._views[view_id], 'refresh'):
                try:
                    self._views[view_id].refresh()
                except Exception as e:
                    logger.debug(f"뷰 새로고침 실패: {e}")

    def register_view(self, view_id: str, view: ctk.CTkFrame):
        """뷰 등록"""
        self._views[view_id] = view
        view.pack_forget()

        # 첫 번째 뷰면 표시
        if self._current_view is None:
            self._switch_view(view_id)

    def set_recorder_view(self, view):
        """녹화 뷰 설정"""
        self.register_view("recorder", view)

    def set_analyzer_view(self, view):
        """분석 뷰 설정"""
        self.register_view("analyzer", view)

    def set_player_view(self, view):
        """실행 뷰 설정"""
        self.register_view("player", view)

    def set_settings_view(self, view):
        """설정 뷰 설정"""
        self.register_view("settings", view)

    def set_guide_view(self, view):
        """가이드 뷰 설정"""
        self.register_view("guide", view)

    def set_log_view(self, view):
        """로그 뷰 설정 (더 이상 사용 안 함)"""
        pass  # 하단 로그 패널로 대체

    def get_view_container(self) -> ctk.CTkFrame:
        """뷰 컨테이너 반환"""
        return self._view_container

    def set_status(self, message: str):
        """상태 메시지 (로그에 표시)"""
        logger.info(message)

    def refresh_all_views(self):
        """모든 뷰 새로고침"""
        for view in self._views.values():
            if hasattr(view, 'refresh'):
                try:
                    view.refresh()
                except Exception as e:
                    logger.debug(f"뷰 새로고침 실패: {e}")

    def set_tab(self, tab_name: str):
        """탭 전환 (하위 호환성)"""
        self._switch_view(tab_name)

    def get_tab_frame(self, tab_name: str) -> Optional[ctk.CTkFrame]:
        """탭 프레임 반환 (하위 호환성)"""
        return self._view_container

    def _on_close(self):
        """윈도우 닫기"""
        # 전역 키보드 리스너 정리
        if self._keyboard_listener:
            try:
                self._keyboard_listener.stop()
                self._keyboard_listener = None
            except (OSError, RuntimeError):
                pass

        # 로그 패널 정리
        if hasattr(self, '_log_panel'):
            self._log_panel.cleanup()

        # 뷰 정리
        for view in self._views.values():
            if hasattr(view, 'cleanup'):
                view.cleanup()

        save_config()
        logger.info("애플리케이션 종료")
        self.destroy()

    def _show_help(self):
        """도움말 다이얼로그 표시"""
        from .help_dialog import HelpDialog
        HelpDialog(self, show_on_startup_option=False)

    def show_message(self, title: str, message: str, msg_type: str = "info"):
        """메시지 대화상자"""
        from tkinter import messagebox
        if msg_type == "info":
            messagebox.showinfo(title, message)
        elif msg_type == "warning":
            messagebox.showwarning(title, message)
        elif msg_type == "error":
            messagebox.showerror(title, message)

    def ask_confirmation(self, title: str, message: str) -> bool:
        """확인 대화상자"""
        from tkinter import messagebox
        return messagebox.askyesno(title, message)


class BaseView(ctk.CTkFrame):
    """뷰 기본 클래스 - 카드 스타일"""

    def __init__(self, parent, **kwargs):
        super().__init__(parent, fg_color=COLORS["bg_content"], **kwargs)
        self._config = get_config()

    def create_card(self, parent, title: str = None, **kwargs) -> ctk.CTkFrame:
        """카드 컴포넌트 생성"""
        card = ctk.CTkFrame(
            parent,
            fg_color=COLORS["bg_card"],
            corner_radius=12,
            border_width=1,
            border_color=COLORS["border"],
            **kwargs
        )

        if title:
            title_label = ctk.CTkLabel(
                card,
                text=title,
                font=ctk.CTkFont(size=16, weight="bold"),
                text_color=COLORS["text_primary"],
            )
            title_label.pack(anchor="w", padx=20, pady=(15, 10))

            # 구분선
            ctk.CTkFrame(
                card,
                fg_color=COLORS["border"],
                height=1,
            ).pack(fill="x", padx=15)

        return card

    def create_button(
        self,
        parent,
        text: str,
        command: Callable = None,
        style: str = "primary",
        **kwargs
    ) -> ctk.CTkButton:
        """스타일 버튼 생성"""
        styles = {
            "primary": {
                "fg_color": COLORS["accent"],
                "hover_color": COLORS["accent_hover"],
                "text_color": COLORS["text_primary"],
            },
            "secondary": {
                "fg_color": COLORS["bg_card"],
                "hover_color": COLORS["bg_card_hover"],
                "text_color": COLORS["text_secondary"],
            },
            "success": {
                "fg_color": COLORS["accent"],
                "hover_color": COLORS["accent_hover"],
                "text_color": COLORS["text_primary"],
            },
            "danger": {
                "fg_color": COLORS["error"],
                "hover_color": "#da3633",
                "text_color": COLORS["text_primary"],
            },
            "warning": {
                "fg_color": COLORS["warning"],
                "hover_color": "#d29922",
                "text_color": COLORS["bg_dark"],
            },
            "ghost": {
                "fg_color": "transparent",
                "hover_color": COLORS["bg_card"],
                "text_color": COLORS["text_secondary"],
            },
        }

        btn_style = styles.get(style, styles["primary"])

        # 기본값 설정 (kwargs로 덮어쓰기 가능)
        defaults = {
            "corner_radius": 8,
            "height": 36,
            "font": ctk.CTkFont(size=13),
        }
        defaults.update(btn_style)
        defaults.update(kwargs)

        return ctk.CTkButton(
            parent,
            text=text,
            command=command,
            **defaults
        )

    def create_label(
        self,
        parent,
        text: str,
        style: str = "body",
        **kwargs
    ) -> ctk.CTkLabel:
        """스타일 레이블 생성"""
        styles = {
            "heading": {
                "font": ctk.CTkFont(size=20, weight="bold"),
                "text_color": COLORS["text_primary"],
            },
            "subheading": {
                "font": ctk.CTkFont(size=16, weight="bold"),
                "text_color": COLORS["text_primary"],
            },
            "body": {
                "font": ctk.CTkFont(size=13),
                "text_color": COLORS["text_secondary"],
            },
            "caption": {
                "font": ctk.CTkFont(size=11),
                "text_color": COLORS["text_muted"],
            },
            "success": {
                "font": ctk.CTkFont(size=13),
                "text_color": COLORS["success"],
            },
            "warning": {
                "font": ctk.CTkFont(size=13),
                "text_color": COLORS["warning"],
            },
            "error": {
                "font": ctk.CTkFont(size=13),
                "text_color": COLORS["error"],
            },
        }

        label_style = styles.get(style, styles["body"])

        return ctk.CTkLabel(
            parent,
            text=text,
            **label_style,
            **kwargs
        )

    def refresh(self):
        """뷰 새로고침"""
        pass

    def cleanup(self):
        """리소스 정리"""
        pass
