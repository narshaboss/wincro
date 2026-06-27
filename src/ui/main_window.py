"""
WinCro 메인 윈도우 모듈

프리미엄 UI 디자인 - 사이드바 네비게이션 + 하단 로그 패널
"""

import customtkinter as ctk
import tkinter as tk
import logging
import os
import re
import threading
import time
from datetime import datetime
from typing import Optional, Callable, Dict
from collections import deque
from threading import Lock
from pathlib import Path

import cv2
import numpy as np
import mss
from pynput import keyboard
from PIL import Image

from ..utils.logger import get_logger
from ..utils.config import get_config, save_config, DATA_DIR, APP_VERSION
from ..utils.discord_notifier import (
    DiscordAlert,
    is_valid_discord_webhook_url,
    send_discord_alert_async,
)
from ..utils.app_identity import get_effective_app_name
from ..utils.json_utils import load_json_file
from ..utils.plan_sequence_groups import (
    get_active_plan_sequence_group,
    mirror_active_group_to_legacy,
    normalize_plan_sequence_groups,
    normalize_repeat_count,
    sync_plan_repeat_in_groups,
)
from ..utils.window_position import setup_window_position
from ..i18n import t, VIEWS
from ..analyzer.automation_models import AutomationPlan
from ..player.rule_executor import RuleExecutor, PLAYLIST_SKIP_TRIGGER_MISSING
from .capture_cleanup import remove_auto_capture_source_after_crop
from .text_overflow import truncate_ui_text
from .ui_batcher import BufferedRecordPump, UiCallbackDispatcher, dispatch_widget_after

PLANS_DIR = DATA_DIR / "plans"
WHITE_GOLD_CTK_THEME = Path(__file__).with_name("ctk_white_gold_theme.json")
MINI_GROUP_PREFIX = "그룹: "
PROJECT_ROOT = Path(__file__).resolve().parents[2]
APP_ICON_FILE = PROJECT_ROOT / "icon.ico"
APP_ICON_PREVIEW_FILE = PROJECT_ROOT / "icon_preview.png"

logger = get_logger(__name__)

# 컬러/치수 토큰 (theme.py에서 통합 관리)
from .theme import COLORS, IOS_FONTS, IOS_METRICS


def _apply_ctk_theme() -> None:
    """Apply the packaged CTk theme without making startup depend on one data file."""
    ctk.set_appearance_mode("dark")
    try:
        if WHITE_GOLD_CTK_THEME.exists():
            ctk.set_default_color_theme(str(WHITE_GOLD_CTK_THEME))
        else:
            logger.warning("CTk theme file missing, fallback to built-in theme: %s", WHITE_GOLD_CTK_THEME)
            ctk.set_default_color_theme("blue")
    except Exception as exc:
        logger.exception("CTk theme load failed, fallback to built-in theme: %s", exc)
        ctk.set_default_color_theme("blue")


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
        super().__init__(parent, fg_color=COLORS["bg_log"], corner_radius=0, **kwargs)

        self._log_buffer: deque = deque(maxlen=500)
        self._auto_scroll = True
        self._expanded = False  # 기본: 축소 상태
        self._lock = Lock()
        self._ui_dispatcher = UiCallbackDispatcher(self, tick_ms=25, max_callbacks_per_tick=48)
        self._log_pump = BufferedRecordPump(
            self,
            self._ui_dispatcher,
            self._flush_log_records,
            flush_interval_ms=40,
            max_items_per_flush=160,
        )

        # 높이 설정
        self._collapsed_height = 48  # 축소시 헤더만
        self._expanded_height = 500  # 확장시 크게 (화면의 절반 정도)

        self._setup_ui()
        self._setup_log_handler()

    def _setup_ui(self):
        # 헤더 바
        self._header = ctk.CTkFrame(self, fg_color=COLORS["bg_glass"], height=48, corner_radius=0)
        self._header.pack(fill="x")
        self._header.pack_propagate(False)

        # 토글 버튼 (클릭하면 확장/축소)
        self._toggle_btn = ctk.CTkButton(
            self._header,
            text="▶ 실시간 로그 (클릭하여 확장)",
            command=self._toggle_expand,
            width=180,
            height=32,
            fg_color=COLORS["bg_elevated"],
            hover_color=COLORS["bg_card_hover"],
            text_color=COLORS["text_primary"],
            border_width=IOS_METRICS["card_border_width"],
            border_color=COLORS["button_border"],
            font=ctk.CTkFont(family=IOS_FONTS["family"], size=12, weight="bold"),
        )
        self._toggle_btn.pack(side="left", padx=8, pady=8)

        # 필터 콤보 (한글화)
        self._filter_var = ctk.StringVar(value="전체")
        self._filter_combo = ctk.CTkComboBox(
            self._header,
            values=["전체", "정보", "경고", "오류"],
            variable=self._filter_var,
            command=self._on_filter_change,
            width=80,
            height=32,
            fg_color=COLORS["bg_elevated"],
            border_width=IOS_METRICS["card_border_width"],
            border_color=COLORS["button_border"],
            button_color=COLORS["bg_card_hover"],
            button_hover_color=COLORS["bg_card_hover"],
            dropdown_fg_color=COLORS["bg_elevated"],
            dropdown_hover_color=COLORS["bg_card_hover"],
            corner_radius=IOS_METRICS["control_radius_small"],
        )
        self._filter_combo.pack(side="left", padx=5, pady=8)

        # 지우기 버튼
        self._clear_btn = ctk.CTkButton(
            self._header,
            text="지우기",
            command=self._clear_logs,
            width=55,
            height=32,
            fg_color=COLORS["bg_elevated"],
            hover_color=COLORS["bg_card_hover"],
            text_color=COLORS["text_secondary"],
            border_width=IOS_METRICS["card_border_width"],
            border_color=COLORS["button_border"],
            corner_radius=IOS_METRICS["control_radius_small"],
        )
        self._clear_btn.pack(side="right", padx=8, pady=8)

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
            font=(IOS_FONTS["fallback"], 11),
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
            button_color=COLORS["bg_elevated"],
            button_hover_color=COLORS["bg_card_hover"],
        )
        scrollbar.pack(side="right", fill="y")
        self._log_text.configure(yscrollcommand=scrollbar.set, state="disabled")

        # 태그 설정
        self._log_text.tag_configure("DEBUG", foreground=COLORS["info"])
        self._log_text.tag_configure("INFO", foreground=COLORS["success"])
        self._log_text.tag_configure("WARNING", foreground=COLORS["warning"])
        self._log_text.tag_configure("ERROR", foreground=COLORS["error"])
        self._log_text.tag_configure("CRITICAL", foreground=COLORS["accent_pink"])

        # ANSI 색상 태그
        self._log_text.tag_configure("ansi_cyan", foreground=COLORS["accent_blue"])     # 청록 (액션 번호)
        self._log_text.tag_configure("ansi_green", foreground=COLORS["success"])   # 초록 (성공)
        self._log_text.tag_configure("ansi_yellow", foreground=COLORS["warning"])  # 노랑 (경고/대기)
        self._log_text.tag_configure("ansi_pink", foreground=COLORS["accent_pink"])    # 분홍 (중지)
        self._log_text.tag_configure("ansi_red", foreground=COLORS["error"])     # 빨강 (에러)

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
        self._log_pump.push((message, level))

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
            if code == '91':
                current_tag = 'ansi_red'
            elif code == '92':
                current_tag = 'ansi_green'
            elif code == '93':
                current_tag = 'ansi_yellow'
            elif code == '95':
                current_tag = 'ansi_pink'
            elif code == '96':
                current_tag = 'ansi_cyan'
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
        self._flush_log_records([(message, level)])

    def _matches_filter(self, level: str) -> bool:
        current_filter = self._filter_var.get() or ""
        values = list(self._filter_combo.cget("values") or [])
        if not values:
            return True
        if current_filter == values[0]:
            return True
        if len(values) > 1 and current_filter == values[1]:
            return level == "INFO"
        if len(values) > 2 and current_filter == values[2]:
            return level == "WARNING"
        if len(values) > 3 and current_filter == values[3]:
            return level == "ERROR"
        return True

    def _flush_log_records(self, records):
        if not records:
            return

        try:
            if not self.winfo_exists():
                return
            self._log_text.configure(state="normal")

            inserted = False
            for message, level in records:
                if not self._matches_filter(level):
                    continue
                inserted = True
                parsed = self._parse_ansi(message)
                for text, tag in parsed:
                    if tag:
                        self._log_text.insert("end", text, tag)
                    else:
                        self._log_text.insert("end", text, level)
                self._log_text.insert("end", "\n")

            line_count = self._get_line_count()
            if inserted and line_count > 500:
                self._log_text.delete("1.0", "100.0")
                line_count = self._get_line_count()

            if inserted and self._auto_scroll:
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
            self.after_idle(self._sync_layout)
        else:
            # 축소: 헤더만 표시
            self._log_container.pack_forget()
            self.configure(height=self._collapsed_height)
            # pack_configure로 높이 강제 적용
            self.pack_configure(ipadx=0, ipady=0)
            self._toggle_btn.configure(text="▶ 실시간 로그 (클릭하여 확장)")
            self.after_idle(self._sync_layout)

    def _sync_layout(self):
        try:
            if self.winfo_exists():
                self.update_idletasks()
        except tk.TclError:
            pass

    def _on_filter_change(self, value: str):
        self._refresh_display()

    def _clear_logs(self):
        with self._lock:
            self._log_buffer.clear()
        self._log_pump.clear()
        self._log_text.configure(state="normal")
        self._log_text.delete("1.0", "end")
        self._log_text.configure(state="disabled")
        self._count_label.configure(text="0개")

    def _refresh_display(self):
        self._log_pump.clear()
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
        self._log_pump.close()
        self._ui_dispatcher.close()

    def destroy(self):
        self.cleanup()
        super().destroy()


class SidebarButton(ctk.CTkButton):
    """사이드바 네비게이션 버튼"""

    def __init__(self, parent, text, icon, command, **kwargs):
        super().__init__(
            parent,
            text=f"  {icon}  {text}",
            command=command,
            width=180,
            height=IOS_METRICS["row_height"],
            anchor="w",
            fg_color="transparent",
            hover_color=COLORS["bg_card"],
            text_color=COLORS["text_secondary"],
            font=ctk.CTkFont(family=IOS_FONTS["family"], size=14, weight="bold"),
            corner_radius=IOS_METRICS["control_radius"],
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

        # 윈도우 설정 - 사용자 지정 고정 이름 적용
        app_name = get_effective_app_name(self._config.ui, save_callback=save_config)
        self._app_name = app_name  # 로고에서도 사용
        self.title(f"{app_name}")
        self._brand_image_cache: Dict[tuple[int, int], ctk.CTkImage] = {}
        self._brand_name_labels = []
        self._apply_window_icon()

        # 창 모드별 크기 설정 (이전 값 호환)
        window_mode = self._config.ui.window_mode or "editor"
        # 이전 값 → 새 값 변환
        if window_mode == "small":
            window_mode = "play"
        elif window_mode in ("medium", "large"):
            window_mode = "editor"
        self._window_mode = window_mode
        if window_mode == "play":
            self.geometry("560x360")
            self.minsize(540, 350)
        else:  # editor
            self.geometry(f"{self._config.ui.window_width}x{self._config.ui.window_height}")
            self.minsize(1000, 700)

        self.configure(fg_color=COLORS["bg_content"])
        self._ui_dispatcher = UiCallbackDispatcher(self, tick_ms=20, max_callbacks_per_tick=96)
        self._mini_active_bar_snapshot = None

        # 테마 설정
        _apply_ctk_theme()

        # 윈도우 닫기 이벤트
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # 뷰 저장소
        self._views: Dict[str, ctk.CTkFrame] = {}
        self._view_factories: Dict[str, Callable] = {}  # 지연 생성용 팩토리
        self._current_view: Optional[str] = None
        self._nav_buttons: Dict[str, SidebarButton] = {}
        self._view_titles: Dict[str, str] = {}
        self._pending_view_id: Optional[str] = None
        self._view_switch_token = 0
        self._dirty_views = set()

        # 전역 F8 캡쳐 기능
        self._keyboard_listener: Optional[keyboard.Listener] = None
        self._capture_notification_label: Optional[ctk.CTkLabel] = None
        self._recording_active = False  # 녹화 중이면 전역 F8 비활성화

        # UI 구성
        self._setup_ui()

        # UI 설정 후 전역 단축키 활성화 (플레이 모드 제외)
        if self._window_mode != "play":
            self._setup_global_hotkey()

        # 창 위치 복원 및 자동 저장 (모드별로 따로 저장)
        self.update_idletasks()
        window_id = "MainWindow_play" if self._window_mode == "play" else "MainWindow_editor"
        setup_window_position(self, window_id)

        logger.info("메인 윈도우 초기화 완료")

    def update_title(self) -> None:
        """현재 설정값을 기준으로 창 제목/브랜드명을 즉시 갱신한다."""
        app_name = get_effective_app_name(self._config.ui, save_callback=save_config)
        self._app_name = app_name
        self.title(f"{app_name}")
        for label in getattr(self, "_brand_name_labels", []):
            try:
                label.configure(text=app_name)
            except (tk.TclError, RuntimeError):
                pass

    def after(self, ms, func=None, *args):
        """백그라운드 스레드의 after() 호출을 메인스레드 dispatcher로 우회한다."""
        return dispatch_widget_after(
            self,
            getattr(self, "_ui_dispatcher", None),
            super(MainWindow, self).after,
            ms,
            func,
            *args,
        )

    def _apply_window_icon(self) -> None:
        """데스크톱 바로가기와 같은 아이콘을 윈도우/상단 UI 기준 심볼로 사용한다."""
        try:
            if APP_ICON_FILE.exists():
                self.iconbitmap(str(APP_ICON_FILE))
        except Exception as e:
            logger.debug(f"윈도우 아이콘 적용 생략: {e}")

    def _get_brand_image(self, size: tuple[int, int]) -> Optional[ctk.CTkImage]:
        cached = self._brand_image_cache.get(size)
        if cached is not None:
            return cached
        if not APP_ICON_PREVIEW_FILE.exists():
            return None
        try:
            with Image.open(APP_ICON_PREVIEW_FILE) as image:
                icon = image.convert("RGBA").copy()
            ctk_image = ctk.CTkImage(light_image=icon, dark_image=icon, size=size)
            self._brand_image_cache[size] = ctk_image
            return ctk_image
        except Exception as e:
            logger.debug(f"브랜드 심볼 로드 실패: {e}")
            return None

    def _create_brand_lockup(self, parent, *, icon_size: int, text_size: int, compact: bool = False):
        brand = ctk.CTkFrame(parent, fg_color="transparent")
        image = self._get_brand_image((icon_size, icon_size))
        if image is not None:
            ctk.CTkLabel(brand, image=image, text="").pack(side="left", padx=(0, 8 if not compact else 6))
        else:
            ctk.CTkLabel(
                brand,
                text="⚔",
                font=ctk.CTkFont(size=icon_size - 2, weight="bold"),
                text_color=COLORS["warning_text"],
            ).pack(side="left", padx=(0, 8 if not compact else 6))
        name_label = ctk.CTkLabel(
            brand,
            text=self._app_name,
            font=ctk.CTkFont(size=text_size, weight="bold"),
            text_color=COLORS["warning_text"],
        )
        name_label.pack(side="left")
        if hasattr(self, "_brand_name_labels"):
            self._brand_name_labels.append(name_label)
        return brand

    def _setup_ui(self):
        # 메인 컨테이너
        self._main_container = ctk.CTkFrame(self, fg_color=COLORS["bg_content"])
        self._main_container.pack(fill="both", expand=True)

        # 플레이 모드면 미니 플레이어 UI
        if self._window_mode == "play":
            self._setup_mini_player_ui()
            return

        # 상단 네비게이션 바
        self._setup_topbar()

        # 콘텐츠 영역
        self._top_area = ctk.CTkFrame(self._main_container, fg_color=COLORS["bg_content"])
        self._top_area.pack(fill="both", expand=True)

        self._setup_content_area()

        # 하단 로그 패널
        self._setup_log_panel()

    def _setup_mini_player_ui(self):
        """미니 플레이어 UI (플레이 모드)"""
        import threading

        self._mini_plans = []
        self._rule_executor = None
        self._is_running = False
        self._is_paused = False
        self._mini_current_repeat = 0
        self._mini_total_repeat = 1

        # 플랜 순서 실행 상태
        self._sequence_mode = False
        self._sequence_plans = []  # 시퀀스 플랜 경로 리스트
        self._sequence_repeats = []  # 시퀀스 플랜별 반복횟수
        self._sequence_index = 0  # 현재 시퀀스 인덱스
        self._sequence_group_name = ""
        self._sequence_group_label = ""
        self._sequence_group_repeat_count = 1
        self._mini_active_plan = None
        self._mini_remaining_rules = []
        self._mini_gm_dialog = None
        self._mini_gm_after_id = None
        self._mini_gm_wait_after_id = None
        self._mini_gm_current_rule = None
        self._mini_stop_requested = False
        self._mini_playback_generation = 0
        self._mini_notification_after_id = None
        self._mini_notification_last_progress_at = time.monotonic()
        self._mini_notification_last_progress_text = ""
        self._mini_notification_last_progress_is_monitoring = False
        self._mini_notification_last_sent_at = {}

        # UI 먼저 생성 (빠르게)
        self._create_mini_player_ui()

        # 플랜 파일 로드는 백그라운드에서 수행
        def _load_plans_bg():
            try:
                import json
                plans = []
                logger.info(f"[미니플레이어] 플랜 폴더: {PLANS_DIR}")
                if PLANS_DIR.exists():
                    templates_dir = DATA_DIR / "templates"
                    for plan_file in PLANS_DIR.glob("*.json"):
                        try:
                            data = load_json_file(plan_file)
                            plan = AutomationPlan.from_dict(data, templates_dir=templates_dir)
                            # 원래 파일 경로 저장 (나중에 저장할 때 사용)
                            plan._source_file = str(plan_file)
                            plans.append(plan)
                            logger.info(f"[미니플레이어] 플랜 로드 성공: {plan.name}")
                        except Exception as e:
                            logger.error(f"플랜 로드 실패: {plan_file} - {e}")
                logger.info(f"[미니플레이어] 총 {len(plans)}개 플랜 로드됨")

                def _apply_plans():
                    self._mini_plans = plans
                    self._update_mini_plan_dropdown()
                    # 선택된 플랜의 저장된 재생횟수 로드
                    if self._mini_plans:
                        selected_name = self._mini_plan_var.get()
                        for plan in self._mini_plans:
                            if plan.name == selected_name:
                                saved_repeat = getattr(plan, 'total_repeat_count', 1) or 1
                                self._mini_repeat_var.set(str(saved_repeat))
                                logger.info(f"[미니플레이어] 초기 재생횟수 로드: {saved_repeat}회")
                                break

                try:
                    self.after(0, _apply_plans)
                except (tk.TclError, RuntimeError):
                    pass
            except Exception as e:
                logger.error(f"[미니플레이어] 백그라운드 플랜 로드 실패: {e}")
                # UI에 빈 플랜 리스트 적용하여 UI가 멈추지 않도록 함
                try:
                    self.after(0, lambda: self._update_mini_plan_dropdown())
                except (tk.TclError, RuntimeError):
                    pass

        threading.Thread(target=_load_plans_bg, daemon=True).start()

    def _create_mini_player_ui(self):
        """미니 플레이어 UI 요소 생성"""
        # 상단 프레임 (플랜 선택 + 컨트롤)
        top_frame = ctk.CTkFrame(
            self._main_container,
            fg_color=COLORS["bg_glass"],
            corner_radius=IOS_METRICS["card_radius"],
            border_width=IOS_METRICS["card_border_width"],
            border_color=COLORS["border"],
        )
        top_frame.pack(fill="x", padx=10, pady=(10, 5))

        # 플랜 드롭다운 (왼쪽)
        plan_names = self._mini_dropdown_values()
        self._mini_plan_var = ctk.StringVar(value=plan_names[0] if plan_names else "(플랜 없음)")
        self._mini_plan_dropdown = ctk.CTkComboBox(
            top_frame,
            variable=self._mini_plan_var,
            values=plan_names,
            width=180,
            height=IOS_METRICS["button_height_small"],
            fg_color=COLORS["bg_elevated"],
            border_color=COLORS["border"],
            button_color=COLORS["accent"],
            button_hover_color=COLORS["accent_hover"],
            dropdown_fg_color=COLORS["bg_elevated"],
            dropdown_hover_color=COLORS["bg_card_hover"],
            font=ctk.CTkFont(family=IOS_FONTS["family"], size=11, weight="bold"),
            state="readonly",
            command=self._on_mini_plan_changed,
        )
        self._mini_plan_dropdown.pack(side="left", padx=(10, 5), pady=8)
        self._style_mini_plan_dropdown()

        self._mini_version_label = ctk.CTkLabel(
            top_frame,
            text=f"v{APP_VERSION}",
            font=ctk.CTkFont(family=IOS_FONTS["family"], size=13, weight="bold"),
            text_color=COLORS["accent_blue_text"],
        )
        self._mini_version_label.pack(side="left", padx=(8, 4), pady=8)

        # 에디터 모드 전환 버튼 (오른쪽 끝)
        ctk.CTkButton(
            top_frame,
            text="에디터",
            width=78,
            height=IOS_METRICS["button_height_small"],
            font=ctk.CTkFont(family=IOS_FONTS["family"], size=12, weight="bold"),
            fg_color=COLORS["accent_pink"],
            hover_color=COLORS["accent_pink_hover"],
            text_color=COLORS["text_on_accent"],
            corner_radius=IOS_METRICS["pill_radius"],
            command=lambda: self._change_window_mode("editor"),
        ).pack(side="right", padx=(4, 10), pady=8)

        # 횟수 설정 (오른쪽)
        self._mini_repeat_var = ctk.StringVar(value="1")

        self._mini_repeat_save_btn = ctk.CTkButton(
            top_frame,
            text="저장",
            width=35,
            height=IOS_METRICS["button_height_small"],
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            font=ctk.CTkFont(family=IOS_FONTS["family"], size=10, weight="bold"),
            corner_radius=IOS_METRICS["pill_radius"],
            command=self._save_mini_repeat_count,
        )
        self._mini_repeat_save_btn.pack(side="right", padx=2, pady=8)

        ctk.CTkLabel(
            top_frame,
            text="회",
            font=ctk.CTkFont(family=IOS_FONTS["family"], size=11),
            text_color=COLORS["text_secondary"],
        ).pack(side="right")

        self._mini_repeat_entry = ctk.CTkEntry(
            top_frame,
            textvariable=self._mini_repeat_var,
            width=35,
            height=IOS_METRICS["button_height_small"],
            fg_color=COLORS["bg_elevated"],
            border_color=COLORS["border"],
            text_color=COLORS["text_primary"],
            font=ctk.CTkFont(family=IOS_FONTS["family"], size=11, weight="bold"),
            justify="center",
        )
        self._mini_repeat_entry.pack(side="right", padx=2, pady=8)

        ctk.CTkLabel(
            top_frame,
            text="반복:",
            font=ctk.CTkFont(family=IOS_FONTS["family"], size=11),
            text_color=COLORS["text_secondary"],
        ).pack(side="right", padx=(5, 2))

        # 현재 실행 중인 자동실행 그룹/재생목록을 한눈에 보여준다.
        active_frame = ctk.CTkFrame(
            self._main_container,
            fg_color=COLORS["bg_glass"],
            corner_radius=IOS_METRICS["card_radius_compact"],
            border_width=IOS_METRICS["card_border_width"],
            border_color=COLORS["border"],
        )
        active_frame.pack(fill="x", padx=10, pady=(0, 5))

        auto_state_frame = ctk.CTkFrame(active_frame, fg_color="transparent")
        auto_state_frame.pack(side="right", padx=(4, 10), pady=5)

        self._mini_auto_shutdown_var = ctk.BooleanVar(
            value=bool(getattr(self._config.system, "shutdown_enabled", True))
        )
        self._mini_auto_shutdown_label = ctk.CTkLabel(
            auto_state_frame,
            text="자동종료 확인 중",
            font=ctk.CTkFont(family=IOS_FONTS["family"], size=11, weight="bold"),
            text_color=COLORS["text_secondary"],
        )
        self._mini_auto_shutdown_label.pack(side="left", padx=(0, 4), pady=2)
        self._mini_auto_shutdown_label.bind(
            "<Button-1>",
            lambda _event: self._toggle_mini_auto_shutdown_from_indicator(),
        )
        self._mini_auto_shutdown_indicator = ctk.CTkButton(
            auto_state_frame,
            text="",
            width=18,
            height=18,
            corner_radius=9,
            fg_color=COLORS["error"],
            hover_color=COLORS["danger_hover"],
            border_width=2,
            border_color=COLORS["button_border"],
            command=self._toggle_mini_auto_shutdown_from_indicator,
        )
        self._mini_auto_shutdown_indicator.pack(side="left", padx=(0, 10), pady=2)

        self._mini_auto_update_var = ctk.BooleanVar(value=bool(self._config.update.auto_check))
        self._mini_auto_update_label = ctk.CTkLabel(
            auto_state_frame,
            text="자동업데이트 확인 중",
            font=ctk.CTkFont(family=IOS_FONTS["family"], size=11, weight="bold"),
            text_color=COLORS["text_secondary"],
        )
        self._mini_auto_update_label.pack(side="left", padx=(0, 4), pady=2)
        self._mini_auto_update_label.bind(
            "<Button-1>",
            lambda _event: self._toggle_mini_auto_update_from_indicator(),
        )
        self._mini_auto_update_indicator = ctk.CTkButton(
            auto_state_frame,
            text="",
            width=18,
            height=18,
            corner_radius=9,
            fg_color=COLORS["error"],
            hover_color=COLORS["danger_hover"],
            border_width=2,
            border_color=COLORS["button_border"],
            command=self._toggle_mini_auto_update_from_indicator,
        )
        self._mini_auto_update_indicator.pack(side="left", pady=2)
        self._update_mini_auto_shutdown_label()
        self._update_mini_auto_update_label()

        ctk.CTkLabel(
            active_frame,
            text="현재 실행",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=COLORS["text_secondary"],
        ).pack(side="left", padx=(12, 8), pady=7)

        self._mini_active_title = ctk.CTkLabel(
            active_frame,
            text="대기",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COLORS["accent_blue_text"],
            anchor="w",
            width=52,
        )
        self._mini_active_title.pack(side="left", padx=(0, 8), pady=7)

        self._mini_active_detail = ctk.CTkLabel(
            active_frame,
            text="실행 중인 재생목록 없음",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=COLORS["text_secondary"],
            anchor="w",
            width=280,
        )
        self._mini_active_detail.pack(side="left", fill="x", expand=True, padx=(0, 12), pady=7)
        self._mini_update_active_bar("대기")

        # 컨트롤 프레임 (실행/중지 버튼)
        ctrl_frame = ctk.CTkFrame(
            self._main_container,
            fg_color=COLORS["bg_glass"],
            corner_radius=IOS_METRICS["card_radius_compact"],
            border_width=IOS_METRICS["card_border_width"],
            border_color=COLORS["border"],
        )
        ctrl_frame.pack(fill="x", padx=10, pady=5)

        # 버튼들 (왼쪽)
        self._mini_play_btn = ctk.CTkButton(
            ctrl_frame,
            text="▶ 실행",
            width=116,
            height=38,
            fg_color=COLORS["success"],
            hover_color=COLORS["green_hover"],
            border_width=2,
            border_color=COLORS["button_border"],
            font=ctk.CTkFont(size=13, weight="bold"),
            corner_radius=IOS_METRICS["pill_radius"],
            command=self._mini_on_play,
        )
        self._mini_play_btn.pack(side="left", padx=(10, 6), pady=8)

        self._mini_pause_btn = ctk.CTkButton(
            ctrl_frame,
            text="⏸ 일시정지",
            width=116,
            height=38,
            fg_color=COLORS["warning"],
            hover_color=COLORS["confidence_amber_hover"],
            border_width=2,
            border_color=COLORS["button_border"],
            font=ctk.CTkFont(size=13, weight="bold"),
            corner_radius=IOS_METRICS["pill_radius"],
            command=self._mini_on_pause,
            state="disabled",
        )
        self._mini_pause_btn.pack(side="left", padx=6, pady=8)

        self._mini_stop_btn = ctk.CTkButton(
            ctrl_frame,
            text="⏹ 정지",
            width=116,
            height=38,
            fg_color=COLORS["error"],
            hover_color=COLORS["danger_hover"],
            border_width=2,
            border_color=COLORS["button_border"],
            font=ctk.CTkFont(size=13, weight="bold"),
            corner_radius=IOS_METRICS["pill_radius"],
            command=self._mini_on_stop,
            state="disabled",
        )
        self._mini_stop_btn.pack(side="left", padx=6, pady=8)

        # 상태 텍스트는 로그/현재 실행 바로 충분하므로 화면에는 표시하지 않는다.
        self._mini_status = ctk.CTkLabel(
            ctrl_frame,
            text="",
            font=ctk.CTkFont(size=11),
            text_color=COLORS["text_secondary"],
        )

        # 로그 영역 (남은 공간 전체 사용)
        log_frame = ctk.CTkFrame(
            self._main_container,
            fg_color=COLORS["bg_glass"],
            corner_radius=IOS_METRICS["card_radius_compact"],
            border_width=IOS_METRICS["card_border_width"],
            border_color=COLORS["border"],
        )
        log_frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        log_header = ctk.CTkFrame(log_frame, fg_color="transparent")
        log_header.pack(fill="x", padx=10, pady=(6, 3))

        ctk.CTkLabel(
            log_header,
            text="실행 로그",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=COLORS["text_secondary"],
        ).pack(side="left")

        self._mini_log_text = ctk.CTkTextbox(
            log_frame,
            fg_color=COLORS["bg_log"],
            text_color=COLORS["text_primary"],
            font=ctk.CTkFont(family=IOS_FONTS["fallback"], size=14),
            wrap="word",
        )
        self._mini_log_text.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        # 로그 색상 태그 설정
        self._mini_log_text._textbox.tag_configure("DEBUG", foreground=COLORS["info"])
        self._mini_log_text._textbox.tag_configure("INFO", foreground=COLORS["success"])
        self._mini_log_text._textbox.tag_configure("WARNING", foreground=COLORS["warning"])
        self._mini_log_text._textbox.tag_configure("ERROR", foreground=COLORS["error"])
        self._mini_log_text._textbox.tag_configure("ansi_cyan", foreground=COLORS["accent_blue"])
        self._mini_log_text._textbox.tag_configure("ansi_green", foreground=COLORS["success"])
        self._mini_log_text._textbox.tag_configure("ansi_yellow", foreground=COLORS["warning"])
        self._mini_log_text._textbox.tag_configure("ansi_pink", foreground=COLORS["accent_pink"])
        self._mini_log_text._textbox.tag_configure("ansi_red", foreground=COLORS["error"])

        # 로그 핸들러 설정
        self._setup_mini_log_handler()

        # 초기 플랜의 재생횟수 불러오기
        if self._mini_plans:
            initial_plan_name = self._mini_plan_var.get()
            for plan in self._mini_plans:
                if plan.name == initial_plan_name:
                    saved_repeat = getattr(plan, 'total_repeat_count', 1) or 1
                    self._mini_repeat_var.set(str(saved_repeat))
                    logger.info(f"[미니플레이어] 초기 재생횟수 로드: {saved_repeat}회")
                    break

    def _update_mini_auto_update_label(self):
        """플레이 모드 자동업데이트 상태 라벨 갱신."""
        if not hasattr(self, "_mini_auto_update_label"):
            return
        enabled = bool(self._mini_auto_update_var.get())
        status_color = COLORS["success"] if enabled else COLORS["error"]
        self._mini_auto_update_label.configure(
            text=f"자동업데이트 {'ON' if enabled else 'OFF'}",
            text_color=status_color,
        )
        if hasattr(self, "_mini_auto_update_indicator"):
            self._mini_auto_update_indicator.configure(
                fg_color=status_color,
                hover_color=COLORS["green_hover"] if enabled else COLORS["danger_hover"],
            )

    def _update_mini_auto_shutdown_label(self):
        """플레이 모드 자동종료 상태 라벨 갱신."""
        if not hasattr(self, "_mini_auto_shutdown_label"):
            return
        enabled = bool(self._mini_auto_shutdown_var.get())
        status_color = COLORS["success"] if enabled else COLORS["error"]
        self._mini_auto_shutdown_label.configure(
            text=f"자동종료 {'ON' if enabled else 'OFF'}",
            text_color=status_color,
        )
        if hasattr(self, "_mini_auto_shutdown_indicator"):
            self._mini_auto_shutdown_indicator.configure(
                fg_color=status_color,
                hover_color=COLORS["green_hover"] if enabled else COLORS["danger_hover"],
            )

    def _mini_active_group_name(self) -> str:
        try:
            group = get_active_plan_sequence_group(self._config.player)
            return str((group or {}).get("name", "") or "").strip()
        except Exception:
            return ""

    def _mini_plan_name_from_path(self, plan_path: str) -> str:
        path = Path(plan_path)
        for plan in getattr(self, "_mini_plans", []) or []:
            source = getattr(plan, "_source_file", "")
            if source and Path(source).name == path.name:
                return getattr(plan, "name", path.stem)
        try:
            data = load_json_file(path)
            return str(data.get("name") or path.stem)
        except Exception:
            return path.stem

    def _mini_set_status(self, text: str, text_color=None) -> None:
        """Avoid repainting the hidden mini status label when the value did not change."""
        if not hasattr(self, "_mini_status"):
            return
        text = truncate_ui_text(text, 90)
        try:
            current_text = self._mini_status.cget("text")
            if text_color is None:
                if current_text == text:
                    return
                self._mini_status.configure(text=text)
                return

            current_color = self._mini_status.cget("text_color")
            if current_text == text and current_color == text_color:
                return
            self._mini_status.configure(text=text, text_color=text_color)
        except (tk.TclError, RuntimeError, AttributeError):
            pass

    def _mini_update_active_bar(
        self,
        state: str,
        plan_name: str = "",
        group_name: str = "",
        index: int = 0,
        total: int = 0,
        repeat_count: int = 0,
        message: str = "",
    ) -> None:
        """현재 실행 그룹/재생목록 표시 바를 갱신한다."""
        if not hasattr(self, "_mini_active_title") or not hasattr(self, "_mini_active_detail"):
            return

        title = state or "대기"
        detail_parts = []
        detail_color = COLORS["text_secondary"]
        display_names = [name for name in (group_name, plan_name) if name]
        display_names = [truncate_ui_text(name, 34) for name in display_names]

        if display_names:
            # 실행 표시 바는 그룹/재생목록명만 강조한다. 액션명/진행 메시지는 로그와 상태줄에만 남긴다.
            detail = " > ".join(display_names)
            detail_color = COLORS["warning"]
        elif state == "대기":
            active_group = self._mini_active_group_name()
            if active_group and bool(getattr(self._config.player, "auto_run_enabled", False)):
                detail_parts.append(f"자동실행 준비: {active_group}")
        if not display_names:
            if total > 0 and index > 0:
                detail_parts.append(f"순서 {index}/{total}")
            if repeat_count > 0:
                detail_parts.append(f"반복 {repeat_count}회")
            if message:
                detail_parts.append(truncate_ui_text(message, 44))
            detail = " · ".join(detail_parts) if detail_parts else "실행 중인 재생목록 없음"
        title = truncate_ui_text(title, 12)
        detail = truncate_ui_text(detail, 82)
        color = COLORS["accent_blue"]
        if state in ("실행 중", "시퀀스"):
            color = COLORS["success"]
        elif state in ("실패", "중단"):
            color = COLORS["error"]
        elif state == "완료":
            color = COLORS["warning"]
        snapshot = (title, color, detail, detail_color)
        if snapshot == getattr(self, "_mini_active_bar_snapshot", None):
            return
        self._mini_active_bar_snapshot = snapshot
        self._mini_active_title.configure(text=title, text_color=color)
        self._mini_active_detail.configure(text=detail, text_color=detail_color)

    def _mini_notification_config(self):
        return getattr(get_config(), "notification", None)

    def _mini_notification_enabled(self, event_type: str) -> bool:
        config = self._mini_notification_config()
        if config is None or not bool(getattr(config, "discord_enabled", False)):
            return False
        if not is_valid_discord_webhook_url(getattr(config, "discord_webhook_url", "")):
            return False
        if event_type == "stuck":
            return bool(getattr(config, "discord_notify_on_stuck", True))
        if event_type == "failure":
            return bool(getattr(config, "discord_notify_on_failure", True))
        return True

    def _mini_reset_notification_runtime(self) -> None:
        self._mini_notification_last_progress_at = time.monotonic()
        self._mini_notification_last_progress_text = ""
        self._mini_notification_last_progress_is_monitoring = False

    def _mini_format_notification_progress(self, progress) -> str:
        raw_message = (
            getattr(progress, "current_rule_description", "")
            or getattr(progress, "message", "")
            or getattr(progress, "current_rule", "")
            or ""
        ).strip()
        action_number = str(getattr(progress, "current_action_number", "") or "").strip()
        action_name = str(getattr(progress, "current_action_name", "") or "").strip()

        match = re.match(r"^\[([^\]]+)\]\s*(.+)$", raw_message)
        if match:
            action_number = action_number or match.group(1).strip()
            action_name = action_name or match.group(2).strip()

        current = getattr(progress, "initial_completed", 0)
        total = getattr(progress, "initial_total", 0)
        parts: list[str] = []
        if current or total:
            parts.append(f"진행 {current}/{total}" if total else f"진행 {current}")

        if action_number or action_name:
            if action_number and action_name:
                parts.append(f"액션 [{action_number}] {action_name}")
            elif action_number:
                parts.append(f"액션 [{action_number}]")
            else:
                parts.append(f"액션 {action_name}")

        normalized_action = f"[{action_number}] {action_name}".strip()
        if raw_message and raw_message not in {normalized_action, action_name}:
            parts.append(f"상태 {raw_message}")

        return " | ".join(parts) or raw_message or "진행 갱신"

    def _mini_record_notification_progress(self, progress) -> None:
        try:
            message = self._mini_format_notification_progress(progress)
            self._mini_notification_last_progress_at = time.monotonic()
            self._mini_notification_last_progress_text = truncate_ui_text(message, 160)
            self._mini_notification_last_progress_is_monitoring = bool(
                getattr(progress, "current_action_is_monitoring", False)
            )
        except Exception:
            self._mini_notification_last_progress_at = time.monotonic()
            self._mini_notification_last_progress_is_monitoring = False

    def _mini_record_game_mode_notification_activity(self, gm=None) -> None:
        """Treat hidden special-mode runtime logs as playback progress for stuck alerts."""
        gm = gm if gm is not None else getattr(self, "_mini_gm_dialog", None)
        if gm is None:
            return
        try:
            if not gm.winfo_exists() or not getattr(gm, "_is_running", False):
                return
            activity_at = float(getattr(gm, "_last_runtime_activity_at", 0.0) or 0.0)
            if activity_at <= 0:
                activity_at = time.monotonic()
            last_progress_at = float(getattr(self, "_mini_notification_last_progress_at", 0.0) or 0.0)
            if activity_at < last_progress_at:
                return
            activity_text = str(getattr(gm, "_last_runtime_activity_text", "") or "특화모드 실행 중").strip()
            self._mini_notification_last_progress_at = activity_at
            self._mini_notification_last_progress_text = truncate_ui_text(f"특화모드 진행: {activity_text}", 160)
            self._mini_notification_last_progress_is_monitoring = False
        except (tk.TclError, RuntimeError, ValueError, TypeError):
            return

    def _mini_cancel_notification_watchdog(self) -> None:
        after_id = getattr(self, "_mini_notification_after_id", None)
        self._mini_notification_after_id = None
        if after_id:
            try:
                self.after_cancel(after_id)
            except (tk.TclError, RuntimeError, ValueError):
                pass

    def _mini_start_notification_watchdog(self, playback_generation: int | None = None) -> None:
        self._mini_cancel_notification_watchdog()
        if not self._mini_notification_enabled("stuck"):
            return
        if playback_generation is None:
            playback_generation = getattr(self, "_mini_playback_generation", 0)
        config = self._mini_notification_config()
        threshold = max(10, int(getattr(config, "discord_stuck_seconds", 120) or 120))
        delay_ms = max(5000, min(30000, threshold * 1000 // 2))
        try:
            self._mini_notification_after_id = self.after(
                delay_ms,
                lambda g=playback_generation: self._mini_check_notification_watchdog(g),
            )
        except (tk.TclError, RuntimeError):
            self._mini_notification_after_id = None

    def _mini_check_notification_watchdog(self, playback_generation: int | None = None) -> None:
        self._mini_notification_after_id = None
        if not self._mini_is_current_playback_generation(playback_generation):
            return
        if not getattr(self, "_is_running", False) or getattr(self, "_mini_stop_requested", False):
            return
        if getattr(self, "_is_paused", False):
            self._mini_start_notification_watchdog(playback_generation)
            return
        if not self._mini_notification_enabled("stuck"):
            return

        self._mini_record_game_mode_notification_activity()

        config = self._mini_notification_config()
        threshold = max(10, int(getattr(config, "discord_stuck_seconds", 120) or 120))
        if bool(getattr(self, "_mini_notification_last_progress_is_monitoring", False)):
            self._mini_start_notification_watchdog(playback_generation)
            return
        elapsed = time.monotonic() - float(getattr(self, "_mini_notification_last_progress_at", time.monotonic()))
        if elapsed >= threshold:
            self._mini_send_discord_alert(
                "stuck",
                "WinCro 장시간 진행 없음",
                f"{int(elapsed)}초 동안 진행 로그가 갱신되지 않았습니다.",
                fields=(
                    ("마지막 진행", getattr(self, "_mini_notification_last_progress_text", "") or "없음"),
                    ("감지 기준", f"{threshold}초"),
                ),
                playback_generation=playback_generation,
            )
        self._mini_start_notification_watchdog(playback_generation)

    def _mini_send_discord_alert(
        self,
        event_key: str,
        title: str,
        description: str,
        fields: tuple[tuple[str, str], ...] = (),
        playback_generation: int | None = None,
    ) -> None:
        if event_key == "stuck":
            event_type = "stuck"
        elif event_key == "group_complete":
            event_type = "complete"
        else:
            event_type = "failure"
        if not self._mini_notification_enabled(event_type):
            return
        if playback_generation is not None and not self._mini_is_current_playback_generation(playback_generation):
            return

        config = self._mini_notification_config()
        cooldown = max(10, int(getattr(config, "discord_cooldown_seconds", 300) or 300))
        now = time.monotonic()
        last_sent = float(getattr(self, "_mini_notification_last_sent_at", {}).get(event_key, 0.0))
        if now - last_sent < cooldown:
            return
        self._mini_notification_last_sent_at[event_key] = now

        app_config = get_config()
        plan_name = getattr(getattr(self, "_mini_active_plan", None), "name", "")
        if not plan_name and hasattr(self, "_mini_plan_var"):
            plan_name = self._mini_plan_var.get()
        group_name = getattr(self, "_sequence_group_name", "") if getattr(self, "_sequence_mode", False) else ""
        alert_fields = [
            ("그룹", group_name or "없음"),
            ("재생목록", plan_name or "알 수 없음"),
        ]
        alert_fields.extend(fields)
        if event_key == "stuck":
            alert_fields = [
                (key, value)
                for key, value in alert_fields
                if str(key).strip().lower() not in {"버전", "version", "app_version", "앱 버전"}
            ]

        alert = DiscordAlert(
            title=title,
            description=description,
            pc_number=getattr(app_config.system, "pc_number", ""),
            fields=tuple(alert_fields),
        )

        def _on_complete(result) -> None:
            if not result.ok:
                logger.warning(f"[디스코드알림] 전송 실패: {result.status} {result.detail}")

        send_discord_alert_async(getattr(config, "discord_webhook_url", ""), alert, on_complete=_on_complete)

    def _toggle_mini_auto_update_from_indicator(self):
        """원형 상태 표시 클릭 시 자동업데이트 ON/OFF를 전환한다."""
        self._mini_auto_update_var.set(not bool(self._mini_auto_update_var.get()))
        self._toggle_mini_auto_update()

    def _toggle_mini_auto_shutdown_from_indicator(self):
        """원형 상태 표시 클릭 시 자동종료 ON/OFF를 전환한다."""
        self._mini_auto_shutdown_var.set(not bool(self._mini_auto_shutdown_var.get()))
        self._toggle_mini_auto_shutdown()

    def _toggle_mini_auto_update(self):
        """플레이 모드에서 자동업데이트 설정을 즉시 저장한다."""
        enabled = bool(self._mini_auto_update_var.get())
        previous = bool(getattr(self._config.update, "auto_check", False))
        try:
            self._config.update.auto_check = enabled
            save_config()
            self._update_mini_auto_update_label()
            self._mini_status.configure(text=f"자동업데이트 {'ON' if enabled else 'OFF'} 저장됨")
            logger.info(f"[미니플레이어] 자동업데이트 설정 변경: {enabled}")
        except Exception as e:
            self._config.update.auto_check = previous
            self._mini_auto_update_var.set(previous)
            self._update_mini_auto_update_label()
            self._mini_status.configure(text="⚠ 자동업데이트 저장 실패")
            logger.error(f"[미니플레이어] 자동업데이트 설정 저장 실패: {e}")

    def _toggle_mini_auto_shutdown(self):
        """플레이 모드에서 PC 자동종료 설정을 즉시 저장하고 예약 작업을 동기화한다."""
        enabled = bool(self._mini_auto_shutdown_var.get())
        previous = bool(getattr(self._config.system, "shutdown_enabled", True))
        try:
            self._config.system.shutdown_enabled = enabled
            if not save_config():
                raise RuntimeError("config save returned False")

            from ..utils.shutdown_scheduler import sync_shutdown_task_from_config

            result = sync_shutdown_task_from_config(self._config.system)
            if not result.ok:
                raise RuntimeError(f"{result.status} {result.detail}".strip())

            self._update_mini_auto_shutdown_label()
            self._mini_status.configure(text=f"자동종료 {'ON' if enabled else 'OFF'} 적용됨")
            logger.info(
                f"[미니플레이어] 자동종료 설정 변경: enabled={enabled} "
                f"status={result.status} detail={result.detail}"
            )
        except Exception as e:
            self._config.system.shutdown_enabled = previous
            save_config()
            self._mini_auto_shutdown_var.set(previous)
            self._update_mini_auto_shutdown_label()
            self._mini_status.configure(text="⚠ 자동종료 예약 실패")
            logger.error(f"[미니플레이어] 자동종료 설정 저장/예약 실패: {e}")

    def _mini_group_label(self, group: dict) -> str:
        return f"{MINI_GROUP_PREFIX}{group.get('name', '그룹')}"

    def _mini_sequence_groups(self) -> list[dict]:
        try:
            return [
                group
                for group in normalize_plan_sequence_groups(self._config.player, mutate=True)
                if group.get("entries")
            ]
        except Exception:
            return []

    def _mini_dropdown_values(self) -> list[str]:
        values = [self._mini_group_label(group) for group in self._mini_sequence_groups()]
        values.extend([p.name for p in self._mini_plans])
        return values or ["(플랜 없음)"]

    def _mini_group_by_label(self, value: str) -> dict | None:
        if not value or not value.startswith(MINI_GROUP_PREFIX):
            return None
        for group in self._mini_sequence_groups():
            if self._mini_group_label(group) == value:
                return group
        return None

    def _mini_group_by_name(self, name: str) -> dict | None:
        group_name = str(name or "").strip()
        if not group_name:
            return None
        for group in self._mini_sequence_groups():
            if str(group.get("name", "") or "").strip() == group_name:
                return group
        return None

    def _mini_restore_group_selection(
        self,
        group_name: str = "",
        group_label: str = "",
        group_repeat: int | None = None,
    ) -> None:
        """Restore group selection after sequence stop/complete."""
        if not hasattr(self, "_mini_plan_var") or not hasattr(self, "_mini_repeat_var"):
            return

        group = self._mini_group_by_label(group_label) if group_label else None
        if group is None:
            group = self._mini_group_by_name(group_name)

        if group is not None:
            label = self._mini_group_label(group)
            repeat = normalize_repeat_count(group.get("repeat_count", group_repeat or 1))
            self._mini_plan_var.set(label)
            self._mini_repeat_var.set(str(repeat))
            self._style_mini_plan_dropdown()
            return

        if group_label and self._is_mini_group_label(group_label):
            repeat = normalize_repeat_count(group_repeat or 1)
            self._mini_plan_var.set(group_label)
            self._mini_repeat_var.set(str(repeat))
            self._style_mini_plan_dropdown()

    def _is_mini_group_label(self, value: str) -> bool:
        return bool(value and value.startswith(MINI_GROUP_PREFIX))

    def _style_mini_plan_dropdown(self) -> None:
        """그룹 재생목록을 노란색으로 표시해 일반 플랜과 구분한다."""
        dropdown = getattr(self, "_mini_plan_dropdown", None)
        plan_var = getattr(self, "_mini_plan_var", None)
        if not dropdown or not plan_var:
            return

        selected = plan_var.get()
        selected_color = COLORS["warning"] if self._is_mini_group_label(selected) else COLORS["text_primary"]
        try:
            dropdown.configure(text_color=selected_color)
        except Exception:
            pass

        menu = getattr(dropdown, "_dropdown_menu", None)
        values = list(getattr(menu, "_values", []) or self._mini_dropdown_values())
        if not menu or not values:
            return

        for index, value in enumerate(values):
            color = COLORS["warning"] if self._is_mini_group_label(value) else COLORS["text_primary"]
            try:
                menu.entryconfigure(index, foreground=color, activeforeground=color)
            except Exception:
                try:
                    menu.entryconfigure(index, foreground=color)
                except Exception:
                    pass

    def _mini_expand_group_sequence(self, group: dict) -> tuple[list[str], list[int]]:
        plans_dir = DATA_DIR / "plans"
        paths: list[str] = []
        repeats: list[int] = []
        entries = list(group.get("entries", []) or [])
        group_repeat = normalize_repeat_count(group.get("repeat_count", 1))
        for _ in range(group_repeat):
            for entry in entries:
                raw_path = str(entry.get("plan_path", "") or "").strip()
                if not raw_path:
                    continue
                paths.append(str(plans_dir / Path(raw_path).name))
                repeats.append(normalize_repeat_count(entry.get("repeat_count", 1)))
        return paths, repeats

    def _refresh_mini_plans_sync(self):
        """플랜 목록 새로고침 - 디스크에서 최신 버전 로드 (백그라운드 스레드에서 호출)"""
        import json
        old_count = len(self._mini_plans)
        plans = []
        if PLANS_DIR.exists():
            templates_dir = DATA_DIR / "templates"
            for plan_file in PLANS_DIR.glob("*.json"):
                try:
                    data = load_json_file(plan_file)
                    plan = AutomationPlan.from_dict(data, templates_dir=templates_dir)
                    # 원래 파일 경로 저장
                    plan._source_file = str(plan_file)
                    plans.append(plan)
                except Exception as e:
                    logger.error(f"[미니플레이어] 플랜 새로고침 실패: {plan_file} - {e}")
        self._mini_plans = plans
        logger.info(f"[미니플레이어] 플랜 새로고침 완료: {old_count} → {len(self._mini_plans)}개")

    def _refresh_mini_plans(self):
        """플랜 목록 새로고침 + UI 업데이트 (비차단)"""
        import threading

        def _load_and_update():
            self._refresh_mini_plans_sync()
            try:
                self.after(0, self._update_mini_plan_dropdown)
            except (tk.TclError, RuntimeError):
                pass

        threading.Thread(target=_load_and_update, daemon=True).start()

    def _update_mini_plan_dropdown(self):
        """플랜 드롭다운 UI 업데이트 (메인 스레드에서 호출)"""
        if hasattr(self, '_mini_plan_dropdown') and self._mini_plan_dropdown:
            plan_names = self._mini_dropdown_values()
            self._mini_plan_dropdown.configure(values=plan_names)
            self._style_mini_plan_dropdown()
            current = self._mini_plan_var.get()
            if current not in plan_names:
                self._mini_plan_var.set(plan_names[0] if plan_names else "(플랜 없음)")
                self._style_mini_plan_dropdown()
            self._on_mini_plan_changed(self._mini_plan_var.get())

    def _setup_mini_log_handler(self):
        """미니 플레이어용 로그 핸들러 설정"""
        import re
        import threading
        import collections

        existing_handler = getattr(self, "_mini_log_handler", None)
        if existing_handler is not None:
            try:
                logging.getLogger().removeHandler(existing_handler)
            except (ValueError, RuntimeError):
                pass
            self._mini_log_handler = None

        # ANSI 색상 코드 매핑
        ansi_map = {
            '\033[96m': 'ansi_cyan',    # 청록 (액션 번호)
            '\033[92m': 'ansi_green',   # 초록
            '\033[93m': 'ansi_yellow',  # 노랑
            '\033[95m': 'ansi_pink',    # 분홍
            '\033[91m': 'ansi_red',     # 빨강
        }

        # 로그 배칭을 위한 버퍼와 상태
        _log_buffer = collections.deque(maxlen=200)
        _log_lock = threading.Lock()
        _flush_scheduled = [False]
        _last_status = [None, None]  # [action_num, action_name]

        LOG_FLUSH_INTERVAL_MS = 100  # 100ms마다 배치 플러시

        def _flush_log_buffer():
            """버퍼에 쌓인 로그를 한꺼번에 UI에 반영 (메인 스레드에서 실행)"""
            _flush_scheduled[0] = False
            try:
                if not self.winfo_exists():
                    return

                # 버퍼에서 모든 로그 꺼내기
                with _log_lock:
                    batch = list(_log_buffer)
                    _log_buffer.clear()
                    status_update = tuple(_last_status)
                    _last_status[0] = None
                    _last_status[1] = None

                if not batch and not status_update[0]:
                    return

                # 상태 UI 업데이트 (마지막 것만)
                if status_update[0] and hasattr(self, '_mini_status'):
                    self._mini_set_status(
                        f"▶ [{status_update[0]}] {status_update[1][:20]}",
                        text_color=COLORS["accent_text"],
                    )

                # 로그 UI 일괄 업데이트
                if batch:
                    self._mini_log_text.configure(state="normal")
                    for msg, level, tag in batch:
                        self._mini_log_text.insert("end", f"{msg}\n", tag)

                    # 최대 100줄 유지 - 초과분 한번에 삭제
                    line_count = int(self._mini_log_text.index('end-1c').split('.')[0])
                    if line_count > 100:
                        self._mini_log_text.delete('1.0', f'{line_count - 100}.0')

                    self._mini_log_text.see("end")
                    self._mini_log_text.configure(state="disabled")
            except (tk.TclError, RuntimeError, AttributeError):
                pass

        def add_log(msg: str, level: str):
            """로그 추가 (백그라운드 스레드에서 호출됨) - 버퍼에 쌓고 배치 플러시"""
            try:
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

                # 액션 번호가 있으면 상태 업데이트용으로 저장
                action_match = re.search(r'\[([0-9\-]+)\]', msg)
                if action_match:
                    action_num = action_match.group(1)
                    action_name = msg.split(']', 1)[-1].strip() if ']' in msg else ""
                    if action_name:
                        with _log_lock:
                            _last_status[0] = action_num
                            _last_status[1] = action_name

                # 버퍼에 추가
                with _log_lock:
                    _log_buffer.append((msg, level, tag))

                # 플러시가 예약되지 않았으면 예약
                if not _flush_scheduled[0]:
                    _flush_scheduled[0] = True
                    try:
                        self.after(LOG_FLUSH_INTERVAL_MS, _flush_log_buffer)
                    except (tk.TclError, RuntimeError):
                        _flush_scheduled[0] = False
            except (tk.TclError, RuntimeError):
                pass

        self._mini_log_handler = GUILogHandler(add_log, max_lines=100)
        self._mini_log_handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s', '%H:%M:%S'))
        self._mini_log_handler.setLevel(logging.DEBUG)
        logging.getLogger().addHandler(self._mini_log_handler)

    def _show_mode_menu(self):
        """창 모드 변경 메뉴"""
        menu = tk.Menu(self, tearoff=0)
        menu.configure(
            bg=COLORS["bg_card"],
            fg=COLORS["text_primary"],
            activebackground=COLORS["accent"],
            activeforeground=COLORS["bg_card"],
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
            data = load_json_file(plan_file)
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
        import sys
        import subprocess
        import os

        logger.info(f"[모드변경] 현재 auto_check={self._config.update.auto_check}, 변경할 모드={mode}")

        # 미니 플레이어에서 실행 중인 작업 중지
        if hasattr(self, '_is_running') and self._is_running:
            logger.info("[모드변경] 실행 중인 작업 중지...")
            if hasattr(self, '_rule_executor') and self._rule_executor:
                try:
                    self._rule_executor.stop()
                except Exception as e:
                    logger.warning(f"[모드변경] 작업 중지 실패: {e}")
            self._is_running = False

        self._config.ui.window_mode = mode
        save_config()
        logger.info(f"[모드변경] 설정 저장 완료, auto_check={self._config.update.auto_check}")

        logger.info(f"창 모드 변경: {mode}, 자동 재시작...")

        # 리소스 정리 먼저 수행
        if self._keyboard_listener:
            try:
                self._keyboard_listener.stop()
                self._keyboard_listener = None
            except (OSError, RuntimeError):
                pass

        # 새 프로세스 시작 (부모 프로세스와 분리)
        try:
            if getattr(sys, 'frozen', False):
                # exe로 실행 중 - 인자 없이 실행
                exe_path = sys.executable
                # DETACHED_PROCESS로 완전히 분리된 프로세스 생성
                subprocess.Popen(
                    [exe_path],
                    creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
                )
            else:
                # 스크립트로 실행 중
                python = sys.executable
                script = sys.argv[0]
                subprocess.Popen(
                    [python, script],
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
                )
            logger.info("[모드변경] 새 프로세스 시작됨")
        except Exception as e:
            logger.error(f"[모드변경] 프로세스 시작 실패: {e}")

        # 데이터베이스 정리
        try:
            from ..database import get_db
            get_db().close()
        except Exception:
            pass

        # 현재 프로세스 종료
        try:
            self.destroy()
        except Exception:
            pass
        os._exit(0)  # sys.exit() 대신 즉시 종료 (스레드 정리 중 블로킹 방지)

    def _on_mini_plan_changed(self, plan_name: str):
        """미니 플레이어 - 플랜 변경 시 재생횟수 불러오기"""
        self._style_mini_plan_dropdown()
        if not plan_name or plan_name == "(플랜 없음)":
            return

        selected_group = self._mini_group_by_label(plan_name)
        if selected_group:
            group_repeat = normalize_repeat_count(selected_group.get("repeat_count", 1))
            self._mini_repeat_var.set(str(group_repeat))
            if not self._is_running and not self._sequence_mode:
                self._mini_update_active_bar("대기", group_name=selected_group.get("name", "그룹"))
            logger.info(
                f"[미니플레이어] 그룹 변경: {selected_group.get('name', '그룹')}, "
                f"그룹 반복: {group_repeat}회"
            )
            return

        for plan in self._mini_plans:
            if plan.name == plan_name:
                # 저장된 재생횟수 불러오기
                saved_repeat = getattr(plan, 'total_repeat_count', 1) or 1
                self._mini_repeat_var.set(str(saved_repeat))
                if not self._is_running and not self._sequence_mode:
                    self._mini_update_active_bar("대기", plan_name=plan_name)
                logger.info(f"[미니플레이어] 플랜 변경: {plan_name}, 재생횟수: {saved_repeat}")
                break

    def _save_mini_repeat_count(self):
        """미니 플레이어 - 재생횟수 저장"""
        import json
        plan_name = self._mini_plan_var.get()
        if not plan_name or plan_name == "(플랜 없음)":
            self._mini_status.configure(text="⚠ 플랜을 선택하세요")
            return

        try:
            repeat_count = int(self._mini_repeat_var.get())
            if repeat_count < 1:
                repeat_count = 1
            elif repeat_count > 9999:
                repeat_count = 9999
        except ValueError:
            self._mini_status.configure(text="⚠ 올바른 숫자를 입력하세요")
            return

        selected_group = self._mini_group_by_label(plan_name)
        if selected_group:
            try:
                config = get_config()
                normalize_plan_sequence_groups(config.player, mutate=True)
                group_id = selected_group.get("group_id", "")
                changed = False
                for group in config.player.plan_sequence_groups:
                    if group.get("group_id") == group_id:
                        group["repeat_count"] = repeat_count
                        changed = True
                        break
                if not changed:
                    self._mini_status.configure(text="⚠ 그룹을 찾을 수 없음")
                    return
                if config.player.active_plan_sequence_group_id == group_id:
                    mirror_active_group_to_legacy(config.player)
                save_config()
                self._mini_status.configure(text=f"✓ 그룹 반복 {repeat_count}회 저장됨")
                self._mini_update_active_bar("대기", group_name=selected_group.get("name", "그룹"))
                logger.info(f"[미니플레이어] 그룹 반복횟수 저장: {repeat_count}회 - {selected_group.get('name', '그룹')}")
            except Exception as e:
                logger.error(f"[미니플레이어] 그룹 반복횟수 저장 실패: {e}")
                self._mini_status.configure(text="⚠ 그룹 반복 저장 실패")
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
            selected_plan.total_repeat_count = repeat_count

            # 플랜 파일에 저장 (원래 파일 경로가 있으면 그 경로에, 없으면 plan_id.json)
            from .player_view import PLANS_DIR
            PLANS_DIR.mkdir(parents=True, exist_ok=True)
            if hasattr(selected_plan, '_source_file') and selected_plan._source_file:
                plan_file = Path(selected_plan._source_file)
            else:
                plan_file = PLANS_DIR / f"{selected_plan.plan_id}.json"
            with open(plan_file, 'w', encoding='utf-8') as f:
                json.dump(selected_plan.to_dict(), f, ensure_ascii=False, indent=2)

            # config의 plan_sequence_repeats도 동기화
            config = get_config()
            plan_file_str = str(plan_file)
            for i, seq_path in enumerate(config.player.plan_sequence):
                if str(Path(seq_path)) == str(plan_file) or seq_path == plan_file_str:
                    while len(config.player.plan_sequence_repeats) <= i:
                        config.player.plan_sequence_repeats.append(1)
                    config.player.plan_sequence_repeats[i] = repeat_count
            sync_plan_repeat_in_groups(config.player, plan_file_str, repeat_count)
            save_config()
            self._mini_status.configure(text=f"✓ 재생횟수 {repeat_count}회 저장됨")
            logger.info(f"[미니플레이어] 재생횟수 저장: {repeat_count}회 - {plan_name}")
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
            self._mini_pause_btn.configure(text="⏸ 일시정지")
            return

        plan_name = self._mini_plan_var.get()
        logger.info(f"[미니플레이어] 선택된 플랜: {plan_name}")

        if not plan_name or plan_name == "(플랜 없음)":
            self._mini_status.configure(text="⚠ 플랜을 선택하세요")
            return

        # 횟수 파싱
        try:
            repeat_count = int(self._mini_repeat_var.get())
            if repeat_count < 1:
                repeat_count = 1
            elif repeat_count > 9999:
                repeat_count = 9999
        except ValueError:
            repeat_count = 1

        playback_generation = self._mini_prepare_new_playback_request()

        selected_group = self._mini_group_by_label(plan_name)
        if selected_group:
            repeat_count = normalize_repeat_count(selected_group.get("repeat_count", repeat_count))
            self._mini_repeat_var.set(str(repeat_count))
            group_to_run = dict(selected_group)
            group_to_run["repeat_count"] = repeat_count
            plan_paths, repeats = self._mini_expand_group_sequence(group_to_run)
            if not plan_paths:
                self._mini_status.configure(text="⚠ 그룹에 재생목록이 없음")
                self._mini_update_active_bar("실패", group_name=selected_group.get("name", "그룹"), message="빈 그룹")
                return
            for path in plan_paths:
                if not Path(path).exists():
                    self._mini_status.configure(text=f"⚠ 플랜 파일 없음: {Path(path).name}")
                    self._mini_update_active_bar(
                        "실패",
                        group_name=selected_group.get("name", "그룹"),
                        message=f"파일 없음: {Path(path).name}",
                    )
                    return

            self._is_running = True
            self._mini_total_repeat = 1
            self._mini_current_repeat = 0
            self._mini_play_btn.configure(state="disabled")
            self._mini_pause_btn.configure(state="normal")
            self._mini_stop_btn.configure(state="normal")
            self._mini_status.configure(text="⏳ 그룹 시퀀스 로드 중...")
            self._mini_update_active_bar(
                "시퀀스",
                group_name=selected_group.get("name", "그룹"),
                total=len(plan_paths),
                repeat_count=repeat_count,
                message="그룹 실행 준비",
            )
            self._start_sequence_mode(
                plan_paths,
                repeats,
                group_name=selected_group.get("name", "그룹"),
                group_label=self._mini_group_label(selected_group),
                group_repeat=repeat_count,
                playback_generation=playback_generation,
            )
            return

        # 반복은 rule_executor에서 처리하므로 여기서는 1회만
        self._mini_total_repeat = 1
        self._mini_current_repeat = 0

        # UI 즉시 업데이트 (로딩 상태 표시)
        self._mini_play_btn.configure(state="disabled")
        self._mini_status.configure(text="⏳ 플랜 로드 중...")
        self._mini_update_active_bar("준비", plan_name=plan_name, repeat_count=repeat_count, message="플랜 로드 중")

        # 백그라운드에서 플랜 로드 후 실행 (메인 스레드 블로킹 방지)
        import threading
        def load_and_start():
            try:
                # 플랜 새로고침 (디스크 I/O - 백그라운드에서)
                self._refresh_mini_plans_sync()

                # 플랜 찾기
                cached_plan = None
                for p in self._mini_plans:
                    if p.name == plan_name:
                        cached_plan = p
                        break

                if not cached_plan:
                    try:
                        self.after(
                            0,
                            lambda g=playback_generation: self._mini_on_load_failed(
                                "⚠ 플랜을 찾을 수 없음",
                                playback_generation=g,
                            ),
                        )
                    except (tk.TclError, RuntimeError):
                        pass
                    return

                # JSON에서 최신 플랜 로드 (원래 파일 경로 사용)
                import json
                from .player_view import PLANS_DIR
                if hasattr(cached_plan, '_source_file') and cached_plan._source_file:
                    plan_file = Path(cached_plan._source_file)
                else:
                    plan_file = PLANS_DIR / f"{cached_plan.plan_id}.json"
                selected_plan = None
                if plan_file.exists():
                    try:
                        data = load_json_file(plan_file)
                        templates_dir = DATA_DIR / "templates"
                        selected_plan = AutomationPlan.from_dict(data, templates_dir=templates_dir)
                        # 원래 파일 경로 유지
                        selected_plan._source_file = str(plan_file)
                        for idx, rule in enumerate(selected_plan.initial_rules):
                            rule_conf = getattr(rule, 'confidence', 0)
                            logger.info(f"[미니플레이어] 룰 {idx+1}: {rule.action_type}, 인식률={rule_conf:.0%}")
                            if getattr(rule, 'is_monitoring_mode', False):
                                watches = getattr(rule, 'monitoring_watches', []) or []
                                logger.info(f"[미니플레이어] 룰 {idx+1}: 모니터링모드=True, 감시={len(watches)}개")
                        logger.info(f"[미니플레이어] 플랜 최신 버전 로드: {plan_name}")
                    except Exception as e:
                        logger.warning(f"[미니플레이어] 플랜 재로드 실패, 캐시 사용: {e}")
                        selected_plan = cached_plan
                else:
                    selected_plan = cached_plan

                # 플랜 객체에 반복횟수 설정 (rule_executor에서 사용)
                selected_plan.total_repeat_count = repeat_count

                if not self._mini_is_current_playback_generation(playback_generation):
                    logger.info("[mini-player] stale plan load ignored")
                    return

                # 메인 스레드에서 실행 시작
                try:
                    self.after(
                        0,
                        lambda g=playback_generation: self._mini_start_execution(
                            selected_plan,
                            repeat_count,
                            playback_generation=g,
                        ),
                    )
                except (tk.TclError, RuntimeError):
                    pass

            except Exception as e:
                logger.error(f"[미니플레이어] 플랜 로드 오류: {e}")
                try:
                    self.after(
                        0,
                        lambda err=e, g=playback_generation: self._mini_on_load_failed(
                            f"✗ 로드 오류: {err}",
                            playback_generation=g,
                        ),
                    )
                except (tk.TclError, RuntimeError):
                    pass

        threading.Thread(target=load_and_start, daemon=True).start()

    def _mini_prepare_new_playback_request(self) -> int:
        """새 실행 요청이 이전 중지/콜백 상태를 물고 시작하지 않게 초기화한다."""
        self._mini_playback_generation = getattr(self, "_mini_playback_generation", 0) + 1
        self._mini_stop_requested = False
        self._is_paused = False
        self._mini_cancel_game_mode_wait()
        self._mini_cancel_notification_watchdog()
        self._mini_reset_notification_runtime()
        self._mini_gm_current_rule = None
        self._mini_gm_previous_rule = None
        self._mini_next_gm_previous_rule = None
        try:
            self._mini_pause_btn.configure(text="⏸ 일시정지")
        except (tk.TclError, RuntimeError, AttributeError):
            pass
        return self._mini_playback_generation

    def _mini_is_current_playback_generation(self, playback_generation: int | None) -> bool:
        if playback_generation is None:
            return True
        return playback_generation == getattr(self, "_mini_playback_generation", 0)

    def _mini_cancel_sequence_start(self, message: str, playback_generation: int | None = None) -> None:
        """백그라운드 로드 후 시작이 취소될 때 실행 버튼이 잠긴 채 남지 않게 복구한다."""
        if not self._mini_is_current_playback_generation(playback_generation):
            return
        logger.info(message)
        self._is_running = False
        self._is_paused = False
        self._sequence_mode = False
        self._mini_cancel_notification_watchdog()
        self._sequence_plans = []
        self._sequence_repeats = []
        self._sequence_index = 0
        self._sequence_group_name = ""
        self._sequence_group_label = ""
        self._sequence_group_repeat_count = 1
        self._mini_active_plan = None
        self._mini_remaining_rules = []
        self._mini_stop_requested = False
        try:
            self._mini_play_btn.configure(state="normal")
            self._mini_pause_btn.configure(state="disabled", text="⏸ 일시정지")
            self._mini_stop_btn.configure(state="disabled")
            self._mini_status.configure(text="정지됨")
            self._mini_update_active_bar("중단", message="시작 취소됨")
        except (tk.TclError, RuntimeError, AttributeError):
            pass

    def _start_sequence_mode(
        self,
        plan_paths: list,
        repeats: list = None,
        group_name: str = "",
        group_label: str = "",
        group_repeat: int = 1,
        playback_generation: int | None = None,
    ):
        """플랜 순서 실행 모드 시작"""
        logger.info(f"[시퀀스] 플랜 순서 실행 시작: {len(plan_paths)}개 플랜")
        self._sequence_mode = True
        self._sequence_plans = list(plan_paths)
        # 반복횟수 리스트 (없거나 짧으면 0으로 채움 → 플랜 파일 값 사용)
        self._sequence_repeats = list(repeats) if repeats else []
        while len(self._sequence_repeats) < len(self._sequence_plans):
            self._sequence_repeats.append(0)
        self._sequence_index = 0
        self._sequence_group_name = group_name or self._mini_active_group_name()
        configured_group = self._mini_group_by_name(self._sequence_group_name)
        if configured_group is not None and group_repeat <= 1:
            group_repeat = normalize_repeat_count(configured_group.get("repeat_count", group_repeat))
        self._sequence_group_label = group_label or (
            self._mini_group_label(configured_group)
            if configured_group is not None
            else (self._mini_group_label({"name": self._sequence_group_name}) if self._sequence_group_name else "")
        )
        self._sequence_group_repeat_count = normalize_repeat_count(group_repeat)

        if self._sequence_group_label and hasattr(self, "_mini_plan_var"):
            self._mini_plan_var.set(self._sequence_group_label)
            self._mini_repeat_var.set(str(self._sequence_group_repeat_count))
            self._style_mini_plan_dropdown()

        # UI 업데이트
        self._mini_play_btn.configure(state="disabled")
        self._mini_status.configure(text="⏳ 시퀀스 로드 중...")
        self._mini_update_active_bar(
            "시퀀스",
            plan_name=self._mini_plan_name_from_path(self._sequence_plans[0]) if self._sequence_plans else "",
            group_name=self._sequence_group_name,
            total=len(self._sequence_plans),
            message="첫 재생목록 로드 중",
        )

        # 첫 번째 플랜 실행
        self._run_sequence_plan(0, playback_generation=playback_generation)

    def _run_sequence_plan(self, index: int, playback_generation: int | None = None):
        """시퀀스에서 지정 인덱스의 플랜 로드 및 실행"""
        import threading

        if playback_generation is None:
            playback_generation = getattr(self, "_mini_playback_generation", 0)

        if index >= len(self._sequence_plans):
            # 모든 시퀀스 완료
            self.after(0, lambda g=playback_generation: self._mini_on_complete(True, "", playback_generation=g))
            self._sequence_mode = False
            return

        plan_path = self._sequence_plans[index]
        self._sequence_index = index
        total = len(self._sequence_plans)
        logger.info(f"[시퀀스] 플랜 {index + 1}/{total} 로드: {plan_path}")

        def load_and_start():
            try:
                import json
                plan_file = Path(plan_path)
                if not plan_file.exists():
                    logger.error(f"[시퀀스] 플랜 파일 없음: {plan_path}")
                    try:
                        self.after(
                            0,
                            lambda p=plan_path, g=playback_generation: self._mini_on_complete(
                                False,
                                f"플랜 파일 없음: {p}",
                                playback_generation=g,
                            ),
                        )
                    except (tk.TclError, RuntimeError):
                        pass
                    self._sequence_mode = False
                    return

                data = load_json_file(plan_file)
                templates_dir = DATA_DIR / "templates"
                plan = AutomationPlan.from_dict(data, templates_dir=templates_dir)

                # 설정의 반복횟수 우선, 없으면(0) 플랜 파일 값 사용
                saved_repeat = self._sequence_repeats[index] if index < len(self._sequence_repeats) else 0
                repeat_count = saved_repeat if saved_repeat > 0 else (getattr(plan, 'total_repeat_count', 1) or 1)
                # 플랜 객체에 반복횟수 설정 (rule_executor에서 사용)
                plan.total_repeat_count = repeat_count
                logger.info(f"[시퀀스] 플랜 로드 성공: {plan.name}, 반복: {repeat_count}회 ({index + 1}/{total})")

                def start_on_main():
                    if not self._mini_is_current_playback_generation(playback_generation):
                        logger.info("[sequence] stale start skipped because playback generation changed")
                        return
                    if getattr(self, "_mini_stop_requested", False) or not getattr(self, "_is_running", False):
                        self._mini_cancel_sequence_start(
                            "[sequence] start skipped because playback already stopped",
                            playback_generation=playback_generation,
                        )
                        return
                    # 그룹 실행 중에는 선택값을 내부 플랜명으로 덮지 않는다.
                    # 그래야 중지 후 실행 버튼을 눌러도 그룹 전체가 다시 시작된다.
                    if hasattr(self, '_mini_plan_var'):
                        if self._sequence_mode and getattr(self, "_sequence_group_label", ""):
                            self._mini_plan_var.set(self._sequence_group_label)
                            self._mini_repeat_var.set(str(self._sequence_group_repeat_count))
                            self._style_mini_plan_dropdown()
                        else:
                            self._mini_plan_var.set(plan.name)
                            self._mini_repeat_var.set(str(repeat_count))
                    # 반복은 rule_executor에서 처리하므로 여기서는 1회만
                    self._mini_total_repeat = 1
                    self._mini_current_repeat = 0
                    self._mini_status.configure(
                        text=f"▶ 시퀀스 {index + 1}/{total} - {plan.name} (반복: {repeat_count}회)"
                    )
                    self._mini_update_active_bar(
                        "실행 중",
                        plan_name=plan.name,
                        group_name=self._sequence_group_name,
                        index=index + 1,
                        total=total,
                        repeat_count=repeat_count,
                    )
                    self._mini_start_execution(plan, repeat_count, playback_generation=playback_generation)

                try:
                    self.after(0, start_on_main)
                except (tk.TclError, RuntimeError):
                    pass

            except Exception as e:
                logger.error(f"[시퀀스] 플랜 로드 오류: {e}")
                try:
                    self.after(
                        0,
                        lambda err=e, g=playback_generation: self._mini_on_complete(
                            False,
                            f"시퀀스 로드 오류: {err}",
                            playback_generation=g,
                        ),
                    )
                except (tk.TclError, RuntimeError):
                    pass
                self._sequence_mode = False

        threading.Thread(target=load_and_start, daemon=True).start()

    def _mini_on_load_failed(self, message: str, playback_generation: int | None = None):
        """플랜 로드 실패 시 UI 복원"""
        if not self._mini_is_current_playback_generation(playback_generation):
            return
        self._mini_cancel_notification_watchdog()
        self._mini_send_discord_alert(
            "failure_load",
            "WinCro 재생목록 로드 실패",
            str(message),
            playback_generation=playback_generation,
        )
        self._is_running = False
        self._is_paused = False
        self._mini_stop_requested = False
        self._mini_status.configure(text=message)
        self._mini_update_active_bar("실패", message=message)
        self._mini_play_btn.configure(state="normal")
        self._mini_pause_btn.configure(state="disabled", text="⏸ 일시정지")
        self._mini_stop_btn.configure(state="disabled")

    def _ensure_arduino_ready_for_mini(self, context_label: str) -> bool:
        """미니 플레이어 재생 시작 전 Arduino 연결 확인"""
        from tkinter import messagebox
        from ..utils.input_controller import ensure_arduino_ready

        ok, detail = ensure_arduino_ready(force_connect=True)
        if ok:
            logger.info(f"[아두이노가드][mini] {context_label} 가능: {detail}")
            return True

        logger.warning(f"[아두이노가드][mini] {context_label} 차단: {detail}")
        try:
            self._mini_status.configure(text="⚠ 아두이노 연결 필요")
        except Exception:
            pass
        message = f"아두이노가 연결되지 않아 {context_label}을 시작할 수 없습니다.\n\n사유: {detail}"
        try:
            messagebox.showerror("아두이노 연결 필요", message, parent=self)
        except TypeError:
            messagebox.showerror("아두이노 연결 필요", message)
        return False

    def _mini_start_execution(self, selected_plan, repeat_count: int, playback_generation: int | None = None):
        """Mini player start entrypoint."""
        try:
            if not self._mini_is_current_playback_generation(playback_generation):
                logger.info("[mini-player] stale start ignored")
                return
            if not self._ensure_arduino_ready_for_mini("미니 플레이어 재생"):
                self._mini_send_discord_alert(
                    "failure_arduino",
                    "WinCro 재생 시작 실패",
                    "아두이노 연결 준비가 되지 않아 플레이모드 재생을 시작하지 못했습니다.",
                    playback_generation=playback_generation,
                )
                self._mini_play_btn.configure(state="normal")
                self._mini_pause_btn.configure(state="disabled", text="⏸ 일시정지")
                self._mini_stop_btn.configure(state="disabled")
                self._is_running = False
                return
            logger.info(f"[mini-player] start, repeat={repeat_count}")
            self._mini_active_plan = selected_plan
            self._mini_remaining_rules = []
            self._mini_next_gm_previous_rule = None
            self._mini_gm_previous_rule = None
            self._mini_trigger_rewind_attempts = {}
            self._mini_stop_requested = False
            self._mini_total_repeat = max(1, repeat_count)
            self._mini_current_repeat = 0
            self._is_running = True
            self._mini_reset_notification_runtime()
            self._mini_start_notification_watchdog(playback_generation)
            self._mini_pause_btn.configure(state="normal")
            self._mini_stop_btn.configure(state="normal")
            self._mini_status.configure(text=f"▶ 실행 중... (1/{self._mini_total_repeat}회)")
            if not self._sequence_mode:
                self._mini_update_active_bar(
                    "실행 중",
                    plan_name=getattr(selected_plan, "name", ""),
                    repeat_count=repeat_count,
                )
            self._mini_execute_plan(selected_plan)
        except Exception as e:
            logger.error(f"[mini-player] start error: {e}")
            self._mini_send_discord_alert(
                "failure_start_error",
                "WinCro 재생 시작 오류",
                str(e),
                playback_generation=playback_generation,
            )
            self._mini_status.configure(text=f"오류: {e}")
            self._mini_update_active_bar("실패", message=str(e))
            self._is_running = False
            self._mini_play_btn.configure(state="normal")
            self._mini_pause_btn.configure(state="disabled", text="⏸ 일시정지")
            self._mini_stop_btn.configure(state="disabled")

    def _mini_execute_plan(self, plan):
        """Run one plan using the same game_mode chain as editor/test mode."""
        try:
            self._mini_active_plan = plan
            self._mini_remaining_rules = []
            has_game_mode = any(
                getattr(rule, "enabled", True)
                and rule.action_type == "game_mode"
                and plan.game_modes.get(rule.rule_id)
                for rule in plan.initial_rules
            )

            if has_game_mode:
                logger.info("[mini-player] game_mode detected -> GameModeDialog chain")
                self._mini_play_plan_rules(plan.initial_rules)
                return

            run_plan = AutomationPlan(
                name=plan.name,
                description=plan.description,
                initial_rules=plan.initial_rules,
                monitoring_rules=plan.monitoring_rules,
            )
            run_plan.game_modes = plan.game_modes
            run_plan.total_repeat_count = 1
            if hasattr(plan, '_source_file'):
                run_plan._source_file = getattr(plan, '_source_file')
            self._mini_run_plan_via_executor(run_plan, chain_remaining=None)
        except Exception as e:
            logger.error(f"[mini-player] execute error: {e}")
            try:
                self.after(
                    0,
                    lambda err=e, g=getattr(self, "_mini_playback_generation", 0): self._mini_on_complete(
                        False,
                        str(err),
                        playback_generation=g,
                    ),
                )
            except (tk.TclError, RuntimeError):
                pass

    def _mini_play_plan_rules(self, rules_to_run):
        active_plan = self._mini_active_plan
        if active_plan is None:
            self._mini_on_complete(
                False,
                "no active plan",
                playback_generation=getattr(self, "_mini_playback_generation", 0),
            )
            return

        if not rules_to_run:
            self._mini_on_repeat_complete(True, "")
            return

        first_gm_idx = None
        for i, rule in enumerate(rules_to_run):
            if (
                getattr(rule, "enabled", True)
                and rule.action_type == "game_mode"
                and active_plan.game_modes.get(rule.rule_id)
            ):
                first_gm_idx = i
                break

        if first_gm_idx is None:
            self._mini_run_rules_via_executor(rules_to_run, chain_remaining=None)
            return

        if first_gm_idx == 0:
            gm_rule = rules_to_run[0]
            previous_rule = getattr(self, "_mini_next_gm_previous_rule", None)
            self._mini_remaining_rules = list(getattr(gm_rule, "children", []) or []) + list(rules_to_run[1:])
            logger.info(f"[mini-player] run game_mode first ({len(self._mini_remaining_rules)} remaining)")
            self._mini_run_game_mode(
                gm_rule.rule_id,
                source_rule=gm_rule,
                source_previous_rule=previous_rule,
            )
            return

        before_gm = list(rules_to_run[:first_gm_idx])
        gm_and_after = list(rules_to_run[first_gm_idx:])
        self._mini_next_gm_previous_rule = before_gm[-1] if before_gm else None
        logger.info(f"[mini-player] run {len(before_gm)} rules before game_mode")
        self._mini_run_rules_via_executor(before_gm, chain_remaining=gm_and_after)

    def _mini_game_mode_wait_seconds(self, rule) -> float:
        if rule is None:
            return 0.0
        try:
            wait_time = float(getattr(rule, "wait_after", 0.0) or 0.0)
            if getattr(rule, "wait_random", False):
                import random
                wait_range = float(getattr(rule, "wait_random_range", 0.3) or 0.0)
                wait_time += random.uniform(-wait_range, wait_range)
            return max(0.0, wait_time)
        except Exception:
            return 0.0

    def _mini_cancel_game_mode_wait(self) -> None:
        wait_after_id = getattr(self, "_mini_gm_wait_after_id", None)
        if wait_after_id is None:
            return
        try:
            self.after_cancel(wait_after_id)
        except (ValueError, tk.TclError, RuntimeError):
            pass
        self._mini_gm_wait_after_id = None

    def _mini_continue_after_game_mode_wait(self, rule, callback) -> None:
        wait_time = self._mini_game_mode_wait_seconds(rule)
        if wait_time <= 0:
            callback()
            return

        logger.info(f"[mini-player] game_mode wait_after {wait_time:.2f}s")
        self._mini_update_active_bar("대기", message=f"특화모드 완료 후 {wait_time:.1f}초 대기")

        def _finish_wait():
            self._mini_gm_wait_after_id = None
            if getattr(self, "_mini_stop_requested", False) or not getattr(self, "_is_running", False):
                return
            callback()

        self._mini_gm_wait_after_id = self.after(int(wait_time * 1000), _finish_wait)

    def _mini_run_game_mode(self, config_rule_id, source_rule=None, source_previous_rule=None):
        game_mode_generation = getattr(self, "_mini_playback_generation", 0)
        active_plan = self._mini_active_plan
        if active_plan is None:
            self._mini_on_complete(False, "no active plan", playback_generation=game_mode_generation)
            return
        if config_rule_id not in active_plan.game_modes:
            self._mini_on_game_mode_complete(False, "missing game mode")
            return
        if not self._ensure_arduino_ready_for_mini("미니 플레이어 특화모드"):
            self._mini_on_game_mode_complete(False, "아두이노 연결 필요")
            return

        from .player_view import GameModeDialog

        if not hasattr(self, "_mini_trigger_rewind_attempts"):
            self._mini_trigger_rewind_attempts = {}
        self._mini_gm_previous_rule = source_previous_rule
        self._mini_gm_dialog = GameModeDialog(
            self,
            active_plan,
            lambda: None,
            lambda: None,
            config_rule_id=config_rule_id,
            auto_run=True,
            source_rule=source_rule,
            source_previous_rule=source_previous_rule,
            trigger_rewind_attempts=self._mini_trigger_rewind_attempts,
        )
        self._mini_gm_current_rule = source_rule
        self._mini_cancel_game_mode_wait()
        self._mini_gm_dialog._suppress_completion_notification = True
        self._mini_gm_dialog.withdraw()
        self._rule_executor = None

        def _check_gm_done():
            if not self._mini_is_current_playback_generation(game_mode_generation):
                logger.info("[mini-player] stale game_mode monitor ignored")
                return
            gm = getattr(self, '_mini_gm_dialog', None)
            if gm is None:
                return
            try:
                if not gm.winfo_exists():
                    self._mini_gm_dialog = None
                    self._mini_on_game_mode_complete(False, "dialog closed")
                    return
                if not gm._is_running:
                    completed_ok = getattr(gm, '_completed_normally', False)
                    completion_msg = getattr(gm, '_completion_message', None)
                    skip_current_playlist = bool(getattr(gm, '_skip_current_playlist', False))
                    rewind_previous_action = bool(getattr(gm, '_rewind_previous_action', False))
                    rewind_delay = float(getattr(gm, "_rewind_delay", 0.0) or 0.0)
                    gm.destroy()
                    self._mini_gm_dialog = None
                    self._mini_on_game_mode_complete(
                        completed_ok,
                        completion_msg,
                        skip_current_playlist=skip_current_playlist,
                        rewind_previous_action=rewind_previous_action,
                        rewind_delay=rewind_delay,
                    )
                    return
                self._mini_record_game_mode_notification_activity(gm)
                self._mini_gm_after_id = self.after(500, _check_gm_done)
            except Exception:
                self._mini_gm_dialog = None
                self._mini_on_game_mode_complete(False, "dialog error")

        self._mini_gm_after_id = self.after(500, _check_gm_done)

    def _mini_on_game_mode_complete(
        self,
        success: bool,
        error_msg: str = None,
        *,
        skip_current_playlist: bool = False,
        rewind_previous_action: bool = False,
        rewind_delay: float = 0.0,
    ):
        if getattr(self, '_mini_stop_requested', False):
            self._mini_on_complete(
                False,
                error_msg or "stopped",
                playback_generation=getattr(self, "_mini_playback_generation", 0),
            )
            return
        remaining = list(getattr(self, '_mini_remaining_rules', []) or [])
        self._mini_remaining_rules = []
        gm_rule = getattr(self, "_mini_gm_current_rule", None)
        previous_rule = getattr(self, "_mini_gm_previous_rule", None)
        self._mini_gm_current_rule = None
        if rewind_previous_action:
            if previous_rule is not None and gm_rule is not None:
                retry_rules = [previous_rule, gm_rule] + remaining
                logger.warning(
                    f"[mini-player] game_mode trigger missing -> rewind previous action: "
                    f"{getattr(previous_rule, 'description', '') or previous_rule.action_type}"
                )
                self._is_running = True
                self._mini_stop_requested = False
                try:
                    self._mini_play_btn.configure(state="disabled")
                    self._mini_pause_btn.configure(state="normal")
                    self._mini_stop_btn.configure(state="normal")
                    self._mini_status.configure(text="↩ 트리거 미감지 → 전 액션 재시도 중...")
                    self._mini_update_active_bar(
                        "실행 중",
                        plan_name=getattr(getattr(self, "_mini_active_plan", None), "name", ""),
                        group_name=getattr(self, "_sequence_group_name", "") if self._sequence_mode else "",
                        index=self._sequence_index + 1 if self._sequence_mode else 0,
                        total=len(self._sequence_plans) if self._sequence_mode else 0,
                        repeat_count=self._mini_total_repeat if not self._sequence_mode else 0,
                        message="트리거 미감지 → 전 액션 재시도",
                    )
                except (tk.TclError, RuntimeError, AttributeError):
                    pass
                self._mini_next_gm_previous_rule = previous_rule
                try:
                    retry_delay = max(0.0, float(rewind_delay or 0.0))
                except (TypeError, ValueError):
                    retry_delay = 0.0

                def _retry_previous_action():
                    self._mini_gm_wait_after_id = None
                    if getattr(self, "_mini_stop_requested", False) or not getattr(self, "_is_running", False):
                        return
                    self._mini_play_plan_rules(retry_rules)

                if retry_delay > 0:
                    logger.info(f"[mini-player] trigger rewind delay {retry_delay:.2f}s")
                    try:
                        self._mini_update_active_bar(
                            "대기",
                            plan_name=getattr(getattr(self, "_mini_active_plan", None), "name", ""),
                            group_name=getattr(self, "_sequence_group_name", "") if self._sequence_mode else "",
                            index=self._sequence_index + 1 if self._sequence_mode else 0,
                            total=len(self._sequence_plans) if self._sequence_mode else 0,
                            repeat_count=self._mini_total_repeat if not self._sequence_mode else 0,
                            message=f"전 액션 재시도 대기 {retry_delay:.1f}초",
                        )
                    except (tk.TclError, RuntimeError, AttributeError):
                        pass
                    self._mini_gm_wait_after_id = self.after(int(retry_delay * 1000), _retry_previous_action)
                else:
                    _retry_previous_action()
                return
            logger.warning("[mini-player] game_mode rewind requested but previous rule is missing")
        if skip_current_playlist:
            message = error_msg or PLAYLIST_SKIP_TRIGGER_MISSING
            logger.warning(f"[mini-player] game_mode trigger missing -> playlist skip: {message}")
            self._mini_on_playlist_skip(message)
            return
        if success:
            def _continue_success():
                if remaining:
                    logger.info(f"[mini-player] game_mode complete -> continue {len(remaining)} rules")
                    self._mini_play_plan_rules(remaining)
                    return
                self._mini_on_repeat_complete(True, error_msg or "")

            self._mini_continue_after_game_mode_wait(gm_rule, _continue_success)
            return
        self._mini_on_repeat_complete(False, error_msg or "")

    def _mini_run_rules_via_executor(self, rules_to_run, chain_remaining=None):
        active_plan = self._mini_active_plan
        if active_plan is None:
            self._mini_on_complete(
                False,
                "no active plan",
                playback_generation=getattr(self, "_mini_playback_generation", 0),
            )
            return

        partial_plan = AutomationPlan(
            name=f"{active_plan.name} (partial)",
            description=f"partial {len(rules_to_run)} rules",
            initial_rules=list(rules_to_run),
            monitoring_rules=[],
        )
        partial_plan.game_modes = active_plan.game_modes
        partial_plan.total_repeat_count = 1
        self._mini_run_plan_via_executor(partial_plan, chain_remaining=chain_remaining)

    def _mini_run_plan_via_executor(self, plan_to_run, chain_remaining=None):
        if not self._ensure_arduino_ready_for_mini("미니 플레이어 재생"):
            self._mini_on_repeat_complete(False, "아두이노 연결 필요")
            return
        self._rule_executor = RuleExecutor()
        callback_generation = getattr(self, "_mini_playback_generation", 0)

        def on_progress(progress):
            if not self._mini_is_current_playback_generation(callback_generation):
                return
            self._mini_on_progress(progress)

        def on_complete(success: bool, message: str):
            try:
                if not self.winfo_exists():
                    return
                if not self._mini_is_current_playback_generation(callback_generation):
                    logger.info("[mini-player] stale executor completion ignored")
                    return
                if getattr(self, '_mini_stop_requested', False):
                    self._rule_executor = None
                    self.after(
                        0,
                        lambda g=callback_generation: self._mini_on_complete(
                            False,
                            "stopped",
                            playback_generation=g,
                        ),
                    )
                    return
                if isinstance(message, str) and message.startswith(PLAYLIST_SKIP_TRIGGER_MISSING):
                    self._rule_executor = None
                    self.after(
                        0,
                        lambda m=message, g=callback_generation: self._mini_on_playlist_skip(m)
                        if self._mini_is_current_playback_generation(g)
                        else None,
                    )
                    return
                if success and chain_remaining:
                    self._rule_executor = None
                    self.after(
                        0,
                        lambda g=callback_generation: self._mini_play_plan_rules(chain_remaining)
                        if self._mini_is_current_playback_generation(g)
                        else None,
                    )
                else:
                    self.after(
                        0,
                        lambda s=success, m=message, g=callback_generation: self._mini_on_repeat_complete(s, m)
                        if self._mini_is_current_playback_generation(g)
                        else None,
                    )
            except (tk.TclError, RuntimeError):
                pass

        self._rule_executor.set_callbacks(
            on_progress=on_progress,
            on_complete=on_complete,
        )
        self._rule_executor.execute_plan_async(plan_to_run)

    def _mini_on_playlist_skip(self, message: str):
        """트리거 미감지 옵션으로 현재 재생목록만 종료하고 다음 시퀀스로 진행."""
        try:
            if not self.winfo_exists():
                return

            logger.info(f"[sequence] current playlist skipped: {message}")

            if getattr(self, '_mini_stop_requested', False):
                self._sequence_mode = False
                self._mini_on_complete(
                    False,
                    "stopped",
                    playback_generation=getattr(self, "_mini_playback_generation", 0),
                )
                return

            if self._sequence_mode:
                playback_generation = getattr(self, "_mini_playback_generation", 0)
                next_index = self._sequence_index + 1
                if next_index < len(self._sequence_plans):
                    logger.info(
                        f"[sequence] plan {self._sequence_index + 1}/{len(self._sequence_plans)} "
                        f"skipped -> next"
                    )
                    self._mini_current_repeat = 0
                    self._mini_total_repeat = 1
                    self._mini_status.configure(text=f"⏭ 현재 재생목록 종료 → 다음 재생목록")
                    self._mini_update_active_bar(
                        "시퀀스",
                        group_name=getattr(self, "_sequence_group_name", ""),
                        index=next_index + 1,
                        total=len(self._sequence_plans),
                        message="트리거 미감지로 다음 재생목록 이동",
                    )
                    self._run_sequence_plan(next_index, playback_generation=playback_generation)
                    return

                logger.info(f"[sequence] complete after playlist skip ({len(self._sequence_plans)} plans)")
                self._sequence_mode = False
                self._mini_on_complete(True, "", playback_generation=playback_generation)
                return

            self._mini_on_complete(
                True,
                message,
                playback_generation=getattr(self, "_mini_playback_generation", 0),
            )
        except (tk.TclError, RuntimeError):
            pass

    def auto_run_sequence(self, plan_paths: list, repeats: list = None, group_name: str = "") -> bool:
        """시작 시 자동 실행 - 플랜 순서 모드 (플레이 모드 전용)"""
        logger.info(f"[자동실행-시퀀스] 플랜 순서 자동 실행 시작: {len(plan_paths)}개 플랜")

        if not plan_paths:
            logger.warning("[자동실행-시퀀스] 플랜 경로 리스트가 비어있음")
            self._mini_status.configure(text="⚠ 시퀀스 플랜 없음")
            return False

        # 파일 존재 확인
        for path in plan_paths:
            if not Path(path).exists():
                logger.error(f"[자동실행-시퀀스] 플랜 파일 없음: {path}")
                self._mini_status.configure(text=f"⚠ 플랜 파일 없음: {Path(path).name}")
                return False

        playback_generation = self._mini_prepare_new_playback_request()

        # UI 버튼 상태 업데이트
        self._mini_play_btn.configure(state="disabled")
        self._mini_pause_btn.configure(state="normal")
        self._mini_stop_btn.configure(state="normal")
        self._is_running = True

        # 시퀀스 모드 시작
        group = self._mini_group_by_name(group_name)
        group_label = self._mini_group_label(group) if group is not None else ""
        group_repeat = normalize_repeat_count(group.get("repeat_count", 1)) if group is not None else 1
        self._start_sequence_mode(
            plan_paths,
            repeats,
            group_name=group_name,
            group_label=group_label,
            group_repeat=group_repeat,
            playback_generation=playback_generation,
        )
        return True

    def _mini_on_pause(self):
        """Mini player pause/resume."""
        if getattr(self, '_mini_gm_dialog', None):
            self._mini_status.configure(text="특화모드 중에는 일시정지 불가")
            return
        if not self._rule_executor:
            return

        if self._is_paused:
            self._rule_executor.resume()
            self._is_paused = False
            self._mini_status.configure(text="▶ 실행 중...")
            self._mini_pause_btn.configure(text="⏸ 일시정지")
        else:
            self._rule_executor.pause()
            self._is_paused = True
            self._mini_status.configure(text="⏸ 일시정지됨")
            self._mini_pause_btn.configure(text="▶ 계속")

    def _mini_on_stop(self):
        """Mini player stop."""
        if getattr(self, '_mini_stop_requested', False):
            return
        self._mini_stop_requested = True
        self._mini_playback_generation = getattr(self, "_mini_playback_generation", 0) + 1
        self._mini_cancel_game_mode_wait()
        self._mini_cancel_notification_watchdog()
        self._mini_gm_current_rule = None
        stopped_group_name = getattr(self, "_sequence_group_name", "")
        stopped_group_label = getattr(self, "_sequence_group_label", "")
        stopped_group_repeat = getattr(self, "_sequence_group_repeat_count", 1)
        was_sequence = bool(self._sequence_mode or self._sequence_plans or stopped_group_label)

        if self._rule_executor:
            self._rule_executor.stop()

        gm = getattr(self, '_mini_gm_dialog', None)
        if gm is not None:
            try:
                if self.winfo_exists() and gm.winfo_exists():
                    self.after(0, gm._stop_execution)
                else:
                    gm._stop_event.set()
            except Exception:
                try:
                    gm._stop_event.set()
                except Exception:
                    pass
            try:
                gm._bosstest_stop_event.set()
                gm._bosstest_release_key()
            except Exception:
                pass

        self._is_running = False
        self._is_paused = False
        self._sequence_mode = False
        self._sequence_plans = []
        self._sequence_repeats = []
        self._sequence_index = 0
        self._sequence_group_name = ""
        self._sequence_group_label = ""
        self._sequence_group_repeat_count = 1
        self._mini_active_plan = None
        self._mini_remaining_rules = []
        self._mini_play_btn.configure(state="normal")
        self._mini_pause_btn.configure(state="disabled", text="⏸ 일시정지")
        self._mini_stop_btn.configure(state="disabled")
        if was_sequence:
            self._mini_restore_group_selection(stopped_group_name, stopped_group_label, stopped_group_repeat)
        self._mini_status.configure(text="⏹ 정지 요청 중...")
        self._mini_update_active_bar(
            "중단",
            group_name=stopped_group_name if was_sequence else "",
            message="정지 요청 중",
        )

    def _mini_on_progress(self, progress):
        """미니 플레이어 - 진행 콜백 (ExecutionProgress 객체)"""
        try:
            self._mini_record_notification_progress(progress)
            current = progress.initial_completed
            total = progress.initial_total
            message = progress.message or progress.current_rule or ""
            repeat_info = f"({self._mini_current_repeat + 1}/{self._mini_total_repeat}회)" if self._mini_total_repeat > 1 else ""
            if self._sequence_mode:
                seq_info = f"[{self._sequence_index + 1}/{len(self._sequence_plans)}] "
            else:
                seq_info = ""

            def update_status():
                try:
                    if self.winfo_exists() and hasattr(self, '_mini_status'):
                        self._mini_set_status(f"▶ {seq_info}{current}/{total} {repeat_info} - {message}")
                        plan_name = getattr(getattr(self, "_mini_active_plan", None), "name", "")
                        self._mini_update_active_bar(
                            "실행 중",
                            plan_name=plan_name,
                            group_name=getattr(self, "_sequence_group_name", "") if self._sequence_mode else "",
                            index=self._sequence_index + 1 if self._sequence_mode else 0,
                            total=len(self._sequence_plans) if self._sequence_mode else 0,
                            repeat_count=self._mini_total_repeat if not self._sequence_mode else 0,
                            message=f"{current}/{total} {message}".strip(),
                        )
                except (tk.TclError, RuntimeError):
                    pass

            self.after(0, update_status)
        except Exception as e:
            logger.debug(f"진행 콜백 오류: {e}")

    def _mini_on_repeat_complete(self, success, message):
        """Mini player one-run completion callback."""
        try:
            if not self.winfo_exists():
                return

            if getattr(self, '_mini_stop_requested', False):
                self._sequence_mode = False
                self.after(
                    0,
                    lambda g=getattr(self, "_mini_playback_generation", 0): self._mini_on_complete(
                        False,
                        "stopped",
                        playback_generation=g,
                    ),
                )
                return

            if not success:
                self._sequence_mode = False
                self.after(
                    0,
                    lambda m=message, g=getattr(self, "_mini_playback_generation", 0): self._mini_on_complete(
                        False,
                        m,
                        playback_generation=g,
                    ),
                )
                return

            self._mini_current_repeat += 1

            if self._mini_current_repeat >= self._mini_total_repeat:
                if self._sequence_mode:
                    next_index = self._sequence_index + 1
                    if next_index < len(self._sequence_plans):
                        logger.info(f"[sequence] plan {self._sequence_index + 1}/{len(self._sequence_plans)} complete -> next")
                        self.after(
                            0,
                            lambda idx=next_index, g=getattr(self, "_mini_playback_generation", 0): self._run_sequence_plan(
                                idx,
                                playback_generation=g,
                            ),
                        )
                        return
                    logger.info(f"[sequence] complete ({len(self._sequence_plans)} plans)")
                    self._sequence_mode = False
                    self.after(
                        0,
                        lambda g=getattr(self, "_mini_playback_generation", 0): self._mini_on_complete(
                            True,
                            "",
                            playback_generation=g,
                        ),
                    )
                    return

                self.after(
                    0,
                    lambda g=getattr(self, "_mini_playback_generation", 0): self._mini_on_complete(
                        True,
                        "",
                        playback_generation=g,
                    ),
                )
                return

            if not self._is_running:
                self._sequence_mode = False
                self.after(
                    0,
                    lambda g=getattr(self, "_mini_playback_generation", 0): self._mini_on_complete(
                        False,
                        "stopped",
                        playback_generation=g,
                    ),
                )
                return

            logger.info(f"[mini-player] repeat {self._mini_current_repeat + 1}/{self._mini_total_repeat}")

            def update_repeat_status():
                try:
                    if self.winfo_exists() and hasattr(self, '_mini_status'):
                        if self._sequence_mode:
                            seq_info = f"시퀀스 {self._sequence_index + 1}/{len(self._sequence_plans)} - "
                        else:
                            seq_info = ""
                        self._mini_set_status(
                            f"진행 중 {seq_info}({self._mini_current_repeat + 1}/{self._mini_total_repeat})"
                        )
                        self._mini_update_active_bar(
                            "실행 중",
                            plan_name=getattr(getattr(self, "_mini_active_plan", None), "name", ""),
                            group_name=getattr(self, "_sequence_group_name", "") if self._sequence_mode else "",
                            index=self._sequence_index + 1 if self._sequence_mode else 0,
                            total=len(self._sequence_plans) if self._sequence_mode else 0,
                            repeat_count=self._mini_total_repeat,
                            message=f"{self._mini_current_repeat + 1}/{self._mini_total_repeat}회 진행",
                        )
                except (tk.TclError, RuntimeError):
                    pass

            self.after(0, update_repeat_status)
        except (tk.TclError, RuntimeError):
            logger.debug("mini-player repeat callback skipped")
            return

        if self._sequence_mode and self._sequence_index < len(self._sequence_plans):
            playback_generation = getattr(self, "_mini_playback_generation", 0)

            def reload_and_execute():
                try:
                    import json
                    plan_path = self._sequence_plans[self._sequence_index]
                    data = load_json_file(plan_path)
                    templates_dir = DATA_DIR / 'templates'
                    plan = AutomationPlan.from_dict(data, templates_dir=templates_dir)
                    if not self._mini_is_current_playback_generation(playback_generation):
                        logger.info("[sequence] stale repeat reload ignored")
                        return
                    try:
                        self.after(
                            0,
                            lambda p=plan, g=playback_generation: self._mini_execute_plan(p)
                            if self._mini_is_current_playback_generation(g)
                            else None,
                        )
                    except (tk.TclError, RuntimeError):
                        pass
                except Exception as e:
                    logger.error(f"[sequence] reload error: {e}")
                    try:
                        self.after(
                            0,
                            lambda err=e, g=playback_generation: self._mini_on_complete(
                                False,
                                str(err),
                                playback_generation=g,
                            ),
                        )
                    except (tk.TclError, RuntimeError):
                        pass

            threading.Thread(target=reload_and_execute, daemon=True).start()
        else:
            plan = self._mini_active_plan
            if plan is not None:
                playback_generation = getattr(self, "_mini_playback_generation", 0)

                def reload_and_execute_current():
                    try:
                        reloaded_plan = self._mini_reload_plan_for_repeat(plan)
                        if not self._mini_is_current_playback_generation(playback_generation):
                            logger.info("[mini-player] stale repeat reload ignored")
                            return
                        try:
                            self.after(
                                0,
                                lambda p=reloaded_plan, g=playback_generation: self._mini_execute_plan(p)
                                if self._mini_is_current_playback_generation(g)
                                else None,
                            )
                        except (tk.TclError, RuntimeError):
                            pass
                    except Exception as e:
                        logger.error(f"[mini-player] repeat reload error: {e}")
                        try:
                            self.after(
                                0,
                                lambda err=e, g=playback_generation: self._mini_on_complete(
                                    False,
                                    str(err),
                                    playback_generation=g,
                                ),
                            )
                        except (tk.TclError, RuntimeError):
                            pass

                threading.Thread(target=reload_and_execute_current, daemon=True).start()

    def _mini_reload_plan_for_repeat(self, plan):
        """Reload the active plan from disk before the next repeat when possible."""
        plan_path = getattr(plan, "_source_file", None)
        if not plan_path:
            return plan

        plan_file = Path(plan_path)
        if not plan_file.exists():
            return plan

        data = load_json_file(plan_file)
        templates_dir = DATA_DIR / "templates"
        reloaded_plan = AutomationPlan.from_dict(data, templates_dir=templates_dir)
        reloaded_plan._source_file = str(plan_file)
        reloaded_plan.total_repeat_count = getattr(plan, "total_repeat_count", 1) or 1
        return reloaded_plan

    def _mini_on_complete(self, success, message, playback_generation: int | None = None):
        """Mini player completion callback."""
        if not self._mini_is_current_playback_generation(playback_generation):
            return
        was_sequence = self._sequence_mode or len(self._sequence_plans) > 0
        seq_count = len(self._sequence_plans)
        completed_group_name = getattr(self, "_sequence_group_name", "")
        completed_group_label = getattr(self, "_sequence_group_label", "")
        completed_group_repeat = getattr(self, "_sequence_group_repeat_count", 1)
        if success and was_sequence:
            self._mini_send_discord_alert(
                "group_complete",
                "WinCro 그룹 실행 완료",
                f"그룹 '{completed_group_name or '알 수 없음'}' 실행이 완료되었습니다.",
                fields=(
                    ("그룹", completed_group_name or "알 수 없음"),
                    ("재생목록 수", f"{seq_count}개"),
                    ("그룹 반복", f"{completed_group_repeat}회"),
                ),
                playback_generation=playback_generation,
            )
        if not success and message != "stopped":
            self._mini_send_discord_alert(
                "failure_complete",
                "WinCro 재생 실패",
                str(message or "알 수 없는 오류"),
                fields=(("중단 사유", str(message or "없음")),),
                playback_generation=playback_generation,
            )

        def update():
            try:
                after_id = getattr(self, '_mini_gm_after_id', None)
                if after_id:
                    self.after_cancel(after_id)
                self._mini_cancel_game_mode_wait()
                self._mini_cancel_notification_watchdog()
            except (tk.TclError, RuntimeError, ValueError):
                pass

            self._is_running = False
            self._is_paused = False
            self._sequence_mode = False
            self._sequence_plans = []
            self._sequence_repeats = []
            self._sequence_index = 0
            self._sequence_group_name = ""
            self._sequence_group_label = ""
            self._sequence_group_repeat_count = 1
            self._mini_active_plan = None
            self._mini_remaining_rules = []
            self._mini_gm_dialog = None
            self._mini_gm_after_id = None
            self._mini_gm_wait_after_id = None
            self._mini_gm_current_rule = None
            self._mini_stop_requested = False
            if was_sequence:
                self._mini_restore_group_selection(
                    completed_group_name,
                    completed_group_label,
                    completed_group_repeat,
                )
            self._mini_play_btn.configure(state="normal")
            self._mini_pause_btn.configure(state="disabled", text="⏸ 일시정지")
            self._mini_stop_btn.configure(state="disabled")
            if success:
                if was_sequence:
                    status = f"완료 ({seq_count}개 재생목록)"
                else:
                    status = f"완료 ({self._mini_total_repeat}회)"
                self._mini_update_active_bar("완료", message=status)
            else:
                status = "정지됨" if message == "stopped" else f"실패: {message}"
                self._mini_update_active_bar("중단" if message == "stopped" else "실패", message=status)
            self._mini_status.configure(text=status)

        self.after(0, update)

    def _setup_topbar(self):
        """상단 네비게이션 바 설정"""
        self._topbar = ctk.CTkFrame(
            self._main_container,
            fg_color=COLORS["bg_glass"],
            height=IOS_METRICS["topbar_height"],
            corner_radius=0,
        )
        self._topbar.pack(fill="x")
        self._topbar.pack_propagate(False)

        # 왼쪽: 로고
        logo_frame = ctk.CTkFrame(self._topbar, fg_color="transparent")
        logo_frame.pack(side="left", padx=20)
        self._create_brand_lockup(logo_frame, icon_size=34, text_size=22).pack(side="left")

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
                self._view_titles[view_id] = label

            btn = ctk.CTkButton(
                nav_container,
                text=f"{icon}  {label}",
                command=cmd,
                width=110,
                height=40,
                fg_color="transparent",
                hover_color=COLORS["bg_card_hover"],
                text_color=COLORS["text_secondary"],
                font=ctk.CTkFont(family=IOS_FONTS["family"], size=14, weight="bold"),
                corner_radius=IOS_METRICS["pill_radius"],
            )
            btn.pack(side="left", padx=3)
            if view_id != "help":
                self._nav_buttons[view_id] = btn

        # 오른쪽: 버전 정보
        version_frame = ctk.CTkFrame(
            self._topbar,
            fg_color=COLORS["bg_glass"],
            corner_radius=IOS_METRICS["pill_radius"],
            border_width=IOS_METRICS["card_border_width"],
            border_color=COLORS["border"],
        )
        version_frame.pack(side="right", padx=20, pady=12)

        self._version_label = ctk.CTkLabel(
            version_frame,
            text=f"  v{APP_VERSION}  ",
            font=ctk.CTkFont(family=IOS_FONTS["family"], size=13, weight="bold"),
            text_color=COLORS["accent_blue_text"],
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
            height=IOS_METRICS["button_height_small"],
            font=ctk.CTkFont(family=IOS_FONTS["family"], size=12, weight="bold"),
            fg_color=COLORS["bg_glass"],
            hover_color=COLORS["bg_card_hover"],
            text_color=COLORS["text_primary"],
            corner_radius=IOS_METRICS["pill_radius"],
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
        self._view_container.pack(
            fill="both",
            expand=True,
            padx=IOS_METRICS["content_padding"],
            pady=(IOS_METRICS["content_padding"], 6),
        )

        self._loading_view = ctk.CTkFrame(
            self._view_container,
            fg_color=COLORS["bg_glass"],
            corner_radius=IOS_METRICS["card_radius"],
            border_width=IOS_METRICS["card_border_width"],
            border_color=COLORS["border"],
        )
        self._loading_content = ctk.CTkFrame(self._loading_view, fg_color="transparent")
        self._loading_content.place(relx=0.5, rely=0.5, anchor="center")
        self._loading_title = ctk.CTkLabel(
            self._loading_content,
            text="화면 준비 중...",
            font=ctk.CTkFont(family=IOS_FONTS["family"], size=22, weight="bold"),
            text_color=COLORS["text_primary"],
        )
        self._loading_title.pack(pady=(0, 10))
        self._loading_desc = ctk.CTkLabel(
            self._loading_content,
            text="UI를 빠르게 전환하고 있습니다",
            font=ctk.CTkFont(family=IOS_FONTS["family"], size=13),
            text_color=COLORS["text_secondary"],
        )
        self._loading_desc.pack()
        self._loading_bar = ctk.CTkProgressBar(
            self._loading_content,
            width=240,
            mode="indeterminate",
            progress_color=COLORS["accent"],
            fg_color=COLORS["bg_elevated"],
        )
        self._loading_bar.pack(pady=(16, 0))

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
                    try:
                        self.after(0, self._capture_full_screen)
                    except (tk.TclError, RuntimeError):
                        pass
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
        """전체 화면 캡쳐 (F8) - 백그라운드에서 캡쳐 후 크롭 다이얼로그 열기"""
        import threading

        def _capture():
            try:
                templates_dir = DATA_DIR / "templates"
                templates_dir.mkdir(parents=True, exist_ok=True)

                with mss.mss() as sct:
                    if not sct.monitors:
                        logger.error("모니터를 찾을 수 없습니다")
                        return
                    monitor = sct.monitors[0]
                    screenshot = sct.grab(monitor)
                    screenshot_arr = np.array(screenshot)
                    if len(screenshot_arr.shape) == 3 and screenshot_arr.shape[2] == 4:
                        screenshot_bgr = cv2.cvtColor(screenshot_arr, cv2.COLOR_BGRA2BGR)
                    elif len(screenshot_arr.shape) == 3 and screenshot_arr.shape[2] == 3:
                        screenshot_bgr = cv2.cvtColor(screenshot_arr, cv2.COLOR_RGB2BGR)
                    else:
                        screenshot_bgr = screenshot_arr

                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
                filename = f"trigger_{timestamp}.png"
                filepath = templates_dir / filename

                success = cv2.imwrite(str(filepath), screenshot_bgr)
                if success:
                    logger.info(f"F8 전체화면 캡쳐 저장: {filepath}")
                    try:
                        self.after(0, lambda: self._open_crop_dialog(str(filepath)))
                    except (tk.TclError, RuntimeError):
                        pass
                else:
                    logger.error(f"이미지 저장 실패: {filepath}")

            except Exception as e:
                import traceback
                logger.error(f"화면 캡쳐 실패: {e}\n{traceback.format_exc()}")

        threading.Thread(target=_capture, daemon=True).start()

    def _open_crop_dialog(self, filepath: str):
        """크롭 다이얼로그 열기"""
        from .analyzer_view import ImageCropDialog

        crop_saved = {"value": False}  # 크롭 저장 여부 추적

        def on_crop_complete(path: str):
            crop_saved["value"] = True
            remove_auto_capture_source_after_crop(filepath, path)
            logger.info(f"크롭 완료: {path}")
            self._show_capture_notification(path)

        def on_delete():
            crop_saved["value"] = True  # 삭제도 처리된 것으로 간주
            logger.info(f"캡쳐 취소됨 (이미지 삭제)")

        def on_dialog_close():
            # 크롭 안 해도 전체 이미지 그대로 저장
            if not crop_saved["value"]:
                logger.info(f"크롭 없이 전체 이미지 저장: {filepath}")
                self._show_capture_notification(filepath)

        try:
            dialog = ImageCropDialog(
                self,
                filepath,
                on_crop=on_crop_complete,
                on_delete=on_delete,
            )
            dialog.title("F8 캡쳐 - 크롭하거나 그냥 닫으면 전체 저장")
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
            corner_radius=IOS_METRICS["pill_radius"],
            font=ctk.CTkFont(family=IOS_FONTS["family"], size=14, weight="bold"),
            text_color=COLORS["text_primary"],
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
        """뷰 전환 (지연 생성 포함)"""
        previous_view = self._current_view
        if view_id == previous_view and self._pending_view_id is None:
            self._set_nav_button_state(view_id)
            return

        self._pending_view_id = view_id
        self._view_switch_token += 1
        token = self._view_switch_token
        self._set_nav_button_state(view_id)

        # 뷰가 아직 생성 안 됐으면 팩토리로 지연 생성
        if view_id not in self._views and view_id in self._view_factories:
            self._show_loading_view(view_id)
            self.after_idle(
                lambda tok=token, target=view_id, previous=previous_view: self._materialize_view(tok, target, previous)
            )
            return

        if view_id not in self._views:
            self._pending_view_id = None
            return

        self.after_idle(lambda tok=token, target=view_id: self._show_ready_view(tok, target))

    def _set_nav_button_state(self, active_view_id: Optional[str]):
        for btn_id, btn in self._nav_buttons.items():
            if btn_id == active_view_id:
                btn.configure(fg_color=COLORS["accent"], text_color=COLORS["text_on_accent"])
            else:
                btn.configure(fg_color="transparent", text_color=COLORS["text_secondary"])

    def _hide_loading_view(self):
        try:
            self._loading_bar.stop()
        except (tk.TclError, RuntimeError, ValueError):
            pass
        try:
            if self._loading_view.winfo_manager():
                self._loading_view.pack_forget()
        except tk.TclError:
            pass

    def _show_loading_view(self, view_id: str):
        title = self._view_titles.get(view_id, view_id)
        self._loading_title.configure(text=f"{title} 준비 중...")
        self._loading_desc.configure(text="클릭은 즉시 반영하고, 무거운 UI만 안전하게 이어서 불러오는 중입니다")
        if self._current_view and self._current_view in self._views:
            try:
                self._views[self._current_view].pack_forget()
            except tk.TclError:
                pass
        self._hide_loading_view()
        self._loading_view.pack(fill="both", expand=True)
        self._loading_bar.start()

    def _materialize_view(self, token: int, view_id: str, previous_view: Optional[str]):
        if token != self._view_switch_token or not self.winfo_exists():
            return

        try:
            view = self._view_factories[view_id]()
            self._views[view_id] = view
        except Exception as e:
            logger.error(f"뷰 생성 실패 ({view_id}): {e}")
            self._hide_loading_view()
            self._pending_view_id = None
            if previous_view and previous_view in self._views:
                self._set_nav_button_state(previous_view)
                try:
                    self._views[previous_view].pack(fill="both", expand=True)
                except tk.TclError:
                    pass
                self._current_view = previous_view
            return

        self._show_ready_view(token, view_id)

    def _show_ready_view(self, token: int, view_id: str):
        if token != self._view_switch_token or not self.winfo_exists():
            return

        self._hide_loading_view()
        if self._current_view and self._current_view in self._views and self._current_view != view_id:
            try:
                self._views[self._current_view].pack_forget()
            except tk.TclError:
                pass
        try:
            self._views[view_id].pack(fill="both", expand=True)
        except tk.TclError:
            return
        self._current_view = view_id
        self._pending_view_id = None
        if view_id in self._dirty_views:
            self._dirty_views.discard(view_id)
            self.after_idle(lambda target=view_id: self._refresh_view_if_needed(target))

    def register_view(self, view_id: str, view: ctk.CTkFrame):
        """뷰 등록 (즉시 생성된 뷰)"""
        self._views[view_id] = view
        view.pack_forget()

        # 첫 번째 뷰면 표시
        if self._current_view is None:
            self._switch_view(view_id)

    def register_view_factory(self, view_id: str, factory: Callable):
        """뷰 팩토리 등록 (지연 생성용)"""
        self._view_factories[view_id] = factory

    def switch_to_first_view(self):
        """첫 번째 뷰로 전환 (팩토리 등록 후 호출)"""
        if self._current_view is None:
            self._switch_view("recorder")

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
        for view_id in list(self._views.keys()):
            if view_id == self._current_view:
                self._refresh_view_if_needed(view_id)
            else:
                self._dirty_views.add(view_id)

    def _refresh_view_if_needed(self, view_id: Optional[str]):
        if not view_id:
            return
        view = self._views.get(view_id)
        if view is None or not hasattr(view, "refresh"):
            return
        try:
            view.refresh()
        except Exception as e:
            logger.debug(f"뷰 새로고침 실패 ({view_id}): {e}")

    def set_tab(self, tab_name: str):
        """탭 전환 (하위 호환성)"""
        self._switch_view(tab_name)

    def get_tab_frame(self, tab_name: str) -> Optional[ctk.CTkFrame]:
        """탭 프레임 반환 (하위 호환성)"""
        return self._view_container

    def _on_close(self):
        """윈도우 닫기"""
        try:
            from .player_view import force_stop_all_game_modes

            stopped = force_stop_all_game_modes(
                reason="app_close",
                detail="MainWindow WM_DELETE_WINDOW",
                join_timeout=1.5,
            )
            if stopped:
                logger.warning(f"[안전종료] 실행 중 특화모드 {stopped}개 강제 중지 요청")
        except Exception as e:
            logger.error(f"[안전종료] 특화모드 강제 중지 실패: {e}")

        def _last_resort_exit():
            import time as _time
            _time.sleep(3.0)
            logger.error("[안전종료] 종료 후 프로세스가 남아 강제 종료합니다")
            os._exit(0)

        threading.Thread(target=_last_resort_exit, daemon=True).start()

        # 전역 키보드 리스너 정리
        if self._keyboard_listener:
            try:
                self._keyboard_listener.stop()
                self._keyboard_listener = None
            except (OSError, RuntimeError):
                pass

        # 미니 로그 핸들러 정리
        if hasattr(self, '_mini_log_handler') and self._mini_log_handler:
            logging.getLogger().removeHandler(self._mini_log_handler)
            self._mini_log_handler = None

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
            fg_color=COLORS["bg_glass"],
            corner_radius=IOS_METRICS["card_radius"],
            border_width=IOS_METRICS["card_border_width"],
            border_color=COLORS["border"],
            **kwargs
        )

        if title:
            title_label = ctk.CTkLabel(
                card,
                text=title,
                font=ctk.CTkFont(family=IOS_FONTS["family"], size=IOS_FONTS["title_size"], weight="bold"),
                text_color=COLORS["text_primary"],
            )
            title_label.pack(anchor="w", padx=22, pady=(18, 12))

            # 구분선
            ctk.CTkFrame(
                card,
                fg_color=COLORS["separator"],
                height=1,
            ).pack(fill="x", padx=18)

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
                "text_color": COLORS["text_on_accent"],
            },
            "secondary": {
                "fg_color": COLORS["bg_elevated"],
                "hover_color": COLORS["bg_card_hover"],
                "text_color": COLORS["text_secondary"],
            },
            "success": {
                "fg_color": COLORS["success"],
                "hover_color": COLORS["green_hover"],
                "text_color": COLORS["text_on_accent"],
            },
            "danger": {
                "fg_color": COLORS["error"],
                "hover_color": COLORS["danger_hover"],
                "text_color": COLORS["text_on_accent"],
            },
            "warning": {
                "fg_color": COLORS["warning"],
                "hover_color": COLORS["confidence_amber_hover"],
                "text_color": COLORS["text_on_accent"],
            },
            "ghost": {
                "fg_color": "transparent",
                "hover_color": COLORS["bg_card_hover"],
                "text_color": COLORS["text_secondary"],
            },
        }

        btn_style = styles.get(style, styles["primary"])

        # 기본값 설정 (kwargs로 덮어쓰기 가능)
        defaults = {
            "corner_radius": IOS_METRICS["control_radius"],
            "height": IOS_METRICS["button_height"],
            "font": ctk.CTkFont(family=IOS_FONTS["family"], size=IOS_FONTS["body_size"], weight="bold"),
            "border_width": 2,
            "border_color": COLORS["button_border"],
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
                "font": ctk.CTkFont(family=IOS_FONTS["family"], size=20, weight="bold"),
                "text_color": COLORS["text_primary"],
            },
            "subheading": {
                "font": ctk.CTkFont(family=IOS_FONTS["family"], size=16, weight="bold"),
                "text_color": COLORS["text_primary"],
            },
            "body": {
                "font": ctk.CTkFont(family=IOS_FONTS["family"], size=IOS_FONTS["body_size"]),
                "text_color": COLORS["text_secondary"],
            },
            "caption": {
                "font": ctk.CTkFont(family=IOS_FONTS["family"], size=IOS_FONTS["caption_size"]),
                "text_color": COLORS["text_muted"],
            },
            "success": {
                "font": ctk.CTkFont(family=IOS_FONTS["family"], size=IOS_FONTS["body_size"]),
                "text_color": COLORS["success"],
            },
            "warning": {
                "font": ctk.CTkFont(family=IOS_FONTS["family"], size=IOS_FONTS["body_size"]),
                "text_color": COLORS["warning"],
            },
            "error": {
                "font": ctk.CTkFont(family=IOS_FONTS["family"], size=IOS_FONTS["body_size"]),
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
