"""
WinCro 로그 뷰어 모듈

실시간 로그를 GUI에서 확인할 수 있습니다.
"""

import logging
import tkinter as tk
from datetime import datetime
from typing import Optional, List
from collections import deque
from threading import Lock

import customtkinter as ctk

from .main_window import BaseView, GUILogHandler
from .theme import COLORS, IOS_FONTS, IOS_METRICS
from .ui_batcher import BufferedRecordPump, UiCallbackDispatcher
from ..utils.logger import get_logger

logger = get_logger(__name__)


class LogView(BaseView):
    """
    로그 뷰어 클래스

    실시간으로 애플리케이션 로그를 표시합니다.
    """

    def __init__(self, parent, **kwargs):
        """로그 뷰어 초기화"""
        super().__init__(parent, **kwargs)

        self._log_buffer: deque = deque(maxlen=1000)
        self._auto_scroll = True
        self._current_filter = "ALL"
        self._last_count_label_text: Optional[str] = None
        self._lock = Lock()
        self._ui_dispatcher = UiCallbackDispatcher(self, tick_ms=25, max_callbacks_per_tick=48)
        self._log_pump = BufferedRecordPump(
            self,
            self._ui_dispatcher,
            self._flush_log_records,
            flush_interval_ms=40,
            max_items_per_flush=200,
        )

        # UI 구성
        self._setup_ui()

        # 로그 핸들러 등록
        self._setup_log_handler()

        logger.info("로그 뷰어 초기화 완료")

    def _setup_ui(self) -> None:
        """UI 구성"""
        # 상단 컨트롤 영역
        self._setup_controls()

        # 로그 표시 영역
        self._setup_log_area()

    def _setup_controls(self) -> None:
        """상단 컨트롤 영역 구성"""
        control_frame = ctk.CTkFrame(
            self,
            fg_color=COLORS["bg_glass"],
            corner_radius=IOS_METRICS["card_radius_compact"],
            border_width=IOS_METRICS["card_border_width"],
            border_color=COLORS["border"],
        )
        control_frame.pack(fill="x", padx=10, pady=(10, 5))

        # 필터 레이블
        filter_label = ctk.CTkLabel(
            control_frame,
            text="필터:",
            font=ctk.CTkFont(family=IOS_FONTS["family"], size=12),
            text_color=COLORS["text_secondary"],
        )
        filter_label.pack(side="left", padx=(10, 5))

        # 로그 레벨 필터
        self._filter_var = ctk.StringVar(value="ALL")
        self._filter_combo = ctk.CTkComboBox(
            control_frame,
            values=["ALL", "DEBUG", "INFO", "WARNING", "ERROR"],
            variable=self._filter_var,
            command=self._on_filter_change,
            width=120,
            height=32,
            fg_color=COLORS["bg_elevated"],
            border_color=COLORS["border"],
            button_color=COLORS["bg_card_hover"],
            button_hover_color=COLORS["bg_card_hover"],
            dropdown_fg_color=COLORS["bg_elevated"],
            dropdown_hover_color=COLORS["bg_card_hover"],
            text_color=COLORS["text_primary"],
            corner_radius=IOS_METRICS["control_radius_small"],
        )
        self._filter_combo.pack(side="left", padx=5)

        # 자동 스크롤 체크박스
        self._auto_scroll_var = ctk.BooleanVar(value=True)
        self._auto_scroll_check = ctk.CTkCheckBox(
            control_frame,
            text="자동 스크롤",
            variable=self._auto_scroll_var,
            command=self._on_auto_scroll_change,
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            text_color=COLORS["text_secondary"],
        )
        self._auto_scroll_check.pack(side="left", padx=20)

        # 지우기 버튼
        self._clear_btn = ctk.CTkButton(
            control_frame,
            text="지우기",
            command=self._clear_logs,
            width=80,
            height=32,
            fg_color=COLORS["bg_elevated"],
            hover_color=COLORS["bg_card_hover"],
            text_color=COLORS["text_secondary"],
            border_width=IOS_METRICS["card_border_width"],
            border_color=COLORS["button_border"],
            corner_radius=IOS_METRICS["pill_radius"],
        )
        self._clear_btn.pack(side="right", padx=10)

        # 로그 개수 표시
        self._count_label = ctk.CTkLabel(
            control_frame,
            text="로그: 0개",
            font=ctk.CTkFont(family=IOS_FONTS["family"], size=11),
            text_color=COLORS["text_muted"],
        )
        self._count_label.pack(side="right", padx=10)

    def _setup_log_area(self) -> None:
        """로그 표시 영역 구성"""
        log_frame = ctk.CTkFrame(
            self,
            fg_color=COLORS["bg_glass"],
            corner_radius=IOS_METRICS["card_radius_compact"],
            border_width=IOS_METRICS["card_border_width"],
            border_color=COLORS["border"],
        )
        log_frame.pack(fill="both", expand=True, padx=10, pady=(5, 10))

        # 텍스트 영역 (tkinter Text 위젯 사용)
        self._log_text = tk.Text(
            log_frame,
            wrap="none",
            font=(IOS_FONTS["fallback"], 10),
            bg=COLORS["bg_log"],
            fg=COLORS["text_secondary"],
            insertbackground=COLORS["text_primary"],
            selectbackground=COLORS["accent"],
            relief="flat",
            padx=10,
            pady=10,
        )
        self._log_text.pack(side="left", fill="both", expand=True, padx=(10, 4), pady=10)

        # 수직 스크롤바
        v_scroll = ctk.CTkScrollbar(
            log_frame,
            orientation="vertical",
            command=self._log_text.yview,
        )
        v_scroll.pack(side="right", fill="y", padx=(0, 10), pady=10)
        self._log_text.configure(yscrollcommand=v_scroll.set)

        # 수평 스크롤바
        h_scroll_frame = ctk.CTkFrame(
            self,
            height=20,
            fg_color=COLORS["bg_glass"],
            corner_radius=IOS_METRICS["control_radius_small"],
            border_width=IOS_METRICS["card_border_width"],
            border_color=COLORS["border"],
        )
        h_scroll_frame.pack(fill="x", padx=10, pady=(0, 10))

        h_scroll = ctk.CTkScrollbar(
            h_scroll_frame,
            orientation="horizontal",
            command=self._log_text.xview,
        )
        h_scroll.pack(fill="x", padx=8, pady=4)
        self._log_text.configure(xscrollcommand=h_scroll.set)

        # 읽기 전용
        self._log_text.configure(state="disabled")

        # 태그 설정 (색상)
        self._log_text.tag_configure("DEBUG", foreground=COLORS["info"])
        self._log_text.tag_configure("INFO", foreground=COLORS["success"])
        self._log_text.tag_configure("WARNING", foreground=COLORS["warning"])
        self._log_text.tag_configure("ERROR", foreground=COLORS["error"])
        self._log_text.tag_configure("CRITICAL", foreground=COLORS["accent_pink"])
        self._log_text.tag_configure("TIMESTAMP", foreground=COLORS["accent_blue"])

        # ANSI 색상 태그 (메시지 내 색상용)
        self._log_text.tag_configure("ansi_cyan", foreground=COLORS["accent_blue"])       # 청록 (액션 번호)
        self._log_text.tag_configure("ansi_green", foreground=COLORS["success"])      # 초록 (성공)
        self._log_text.tag_configure("ansi_yellow", foreground=COLORS["warning"])     # 노랑 (경고/대기)
        self._log_text.tag_configure("ansi_pink", foreground=COLORS["accent_pink"])       # 분홍 (중지)
        self._log_text.tag_configure("ansi_red", foreground=COLORS["error"])        # 빨강 (에러)

    def _setup_log_handler(self) -> None:
        """로그 핸들러 등록"""
        self._handler = GUILogHandler(self._add_log_message)
        self._handler.setLevel(logging.DEBUG)
        self._handler.setFormatter(logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%H:%M:%S"
        ))

        # 루트 로거에 핸들러 추가
        logging.getLogger().addHandler(self._handler)

    def _add_log_message(self, message: str, level: str) -> None:
        """로그 메시지 추가"""
        with self._lock:
            self._log_buffer.append((message, level))
        self._log_pump.push((message, level))

    def _parse_ansi_message(self, message: str):
        """ANSI 코드를 파싱하여 (텍스트, 태그) 쌍의 리스트 반환"""
        import re

        # ANSI 코드 패턴 (ESC[숫자m 형식)
        # ESC 문자는 \x1b (hex) = \033 (octal) = chr(27)
        ESC = '\x1b'
        pattern = re.compile(ESC + r'\[(\d+)m')

        result = []
        current_tag = None
        last_end = 0

        for match in pattern.finditer(message):
            # ANSI 코드 이전 텍스트
            before = message[last_end:match.start()]
            if before:
                result.append((before, current_tag))

            # ANSI 코드 처리
            code = match.group(1)
            if code == '91':
                current_tag = 'ansi_red'     # 빨강
            elif code == '92':
                current_tag = 'ansi_green'   # 초록
            elif code == '93':
                current_tag = 'ansi_yellow'  # 노랑
            elif code == '95':
                current_tag = 'ansi_pink'    # 분홍
            elif code == '96':
                current_tag = 'ansi_cyan'    # 청록
            elif code == '0':
                current_tag = None           # 리셋

            last_end = match.end()

        # 남은 텍스트
        remaining = message[last_end:]
        if remaining:
            result.append((remaining, current_tag))

        # 파싱된 결과가 없으면 원본 반환
        if not result:
            result.append((message, None))

        return result

    def _update_log_display(self, message: str, level: str) -> None:
        """로그 표시 업데이트"""
        self._flush_log_records([(message, level)])

    def _flush_log_records(self, records) -> None:
        if not records:
            return

        try:
            self._log_text.configure(state="normal")

            inserted = False
            for message, level in records:
                if self._current_filter != "ALL" and level != self._current_filter:
                    continue
                inserted = True
                parsed = self._parse_ansi_message(message)
                for text, tag in parsed:
                    if tag:
                        self._log_text.insert("end", text, tag)
                    else:
                        self._log_text.insert("end", text, level)
                self._log_text.insert("end", "\n")

            if inserted and self._auto_scroll:
                self._log_text.see("end")

            self._log_text.configure(state="disabled")

            self._set_count_label(self._get_visible_line_count())

        except tk.TclError:
            pass  # 위젯이 파괴된 경우 무시

    def _get_visible_line_count(self) -> int:
        try:
            index_str = self._log_text.index("end-1c")
            parts = index_str.split(".")
            return int(parts[0]) if parts and parts[0].isdigit() else 0
        except (tk.TclError, ValueError, IndexError):
            return 0

    def _set_count_label(self, line_count: int) -> None:
        text = f"로그: {line_count}개"
        if self._last_count_label_text == text:
            return
        self._last_count_label_text = text
        self._count_label.configure(text=text)

    def _on_filter_change(self, value: str) -> None:
        """필터 변경 처리"""
        self._current_filter = value
        self._refresh_display()

    def _on_auto_scroll_change(self) -> None:
        """자동 스크롤 설정 변경"""
        self._auto_scroll = self._auto_scroll_var.get()

    def _clear_logs(self) -> None:
        """로그 지우기"""
        with self._lock:
            self._log_buffer.clear()
        self._log_pump.clear()

        self._log_text.configure(state="normal")
        self._log_text.delete("1.0", "end")
        self._log_text.configure(state="disabled")
        self._set_count_label(0)

    def _refresh_display(self) -> None:
        """필터에 따라 로그 다시 표시"""
        self._log_pump.clear()
        self._log_text.configure(state="normal")
        self._log_text.delete("1.0", "end")

        with self._lock:
            for message, level in self._log_buffer:
                if self._current_filter == "ALL" or level == self._current_filter:
                    # ANSI 코드 파싱 및 색상 적용
                    parsed = self._parse_ansi_message(message)
                    for text, tag in parsed:
                        if tag:
                            self._log_text.insert("end", text, tag)
                        else:
                            self._log_text.insert("end", text, level)
                    self._log_text.insert("end", "\n")

        if self._auto_scroll:
            self._log_text.see("end")

        self._log_text.configure(state="disabled")

        self._set_count_label(self._get_visible_line_count())

    def cleanup(self) -> None:
        """리소스 정리"""
        # 로그 핸들러 제거
        try:
            logging.getLogger().removeHandler(self._handler)
        except (ValueError, RuntimeError):
            pass
        self._log_pump.close()
        self._ui_dispatcher.close()

    def destroy(self) -> None:
        self.cleanup()
        super().destroy()
