"""
WinCro 실행 화면 모듈

동작 재현 기능을 위한 UI를 제공합니다.
"""

import customtkinter as ctk
from typing import Optional, List
from pathlib import Path
import json

from ..utils.logger import get_logger
from ..utils.config import get_config, DATA_DIR
from ..utils.window_position import setup_window_position
from ..i18n import PLAYER, BUTTONS, SEQUENCE
from ..player import get_action_player, PlayerState, PlaybackProgress
from ..player.rule_executor import RuleExecutor
from ..database import get_db, Sequence, Action
from ..analyzer.automation_models import AutomationPlan, AutomationRule
from .main_window import BaseView, COLORS
from .analyzer_view import ImageCropDialog
import cv2
import numpy as np
from PIL import Image
import tkinter as tk
from tkinter import Canvas

logger = get_logger(__name__)

# 자동화 계획 저장 폴더
PLANS_DIR = DATA_DIR / "plans"

# 액션 클립보드 (복사/붙여넣기용) - 다이얼로그 간 공유
# 스레드 안전한 클립보드 관리
import threading
_action_clipboard = None  # AutomationRule 또는 Action 객체
_clipboard_lock = threading.Lock()

# 썸네일 캐시 (성능 최적화)
_thumbnail_cache = {}  # {cache_key: CTkImage}
_thumbnail_cache_lock = threading.Lock()
MAX_THUMBNAIL_CACHE = 100  # 최대 캐시 개수


def _get_file_mtime(image_path: str) -> float:
    """파일 수정 시간 가져오기"""
    try:
        return Path(image_path).stat().st_mtime
    except:
        return 0


def get_cached_thumbnail(image_path: str, size: tuple):
    """캐시된 썸네일 가져오기 (파일 수정 시간 확인)"""
    mtime = _get_file_mtime(image_path)
    cache_key = f"{image_path}_{size[0]}x{size[1]}_{mtime}"
    with _thumbnail_cache_lock:
        return _thumbnail_cache.get(cache_key)


def set_cached_thumbnail(image_path: str, size: tuple, ctk_image):
    """썸네일 캐시에 저장 (파일 수정 시간 포함)"""
    mtime = _get_file_mtime(image_path)
    cache_key = f"{image_path}_{size[0]}x{size[1]}_{mtime}"
    with _thumbnail_cache_lock:
        # 캐시가 너무 크면 오래된 항목 제거
        if len(_thumbnail_cache) >= MAX_THUMBNAIL_CACHE:
            # 첫 번째 항목 제거 (간단한 LRU)
            try:
                first_key = next(iter(_thumbnail_cache))
                del _thumbnail_cache[first_key]
            except:
                pass
        _thumbnail_cache[cache_key] = ctk_image


def invalidate_thumbnail_cache(image_path: str):
    """특정 이미지의 썸네일 캐시 무효화 (크롭 후 갱신용)"""
    with _thumbnail_cache_lock:
        keys_to_remove = [k for k in _thumbnail_cache if image_path in k]
        for key in keys_to_remove:
            del _thumbnail_cache[key]


def get_action_clipboard():
    """클립보드에서 액션 가져오기 (스레드 안전)"""
    with _clipboard_lock:
        return _action_clipboard


def set_action_clipboard(action):
    """클립보드에 액션 저장 (스레드 안전)"""
    global _action_clipboard
    with _clipboard_lock:
        _action_clipboard = action


class VirtualScrollFrame(ctk.CTkFrame):
    """
    가상 스크롤 프레임 - 보이는 항목만 렌더링하여 성능 최적화

    수천 개의 항목도 부드럽게 스크롤 가능
    """

    def __init__(self, parent, item_height=75, buffer_count=3, **kwargs):
        # fg_color 기본값 설정
        if 'fg_color' not in kwargs:
            kwargs['fg_color'] = COLORS["bg_card"]
        super().__init__(parent, **kwargs)

        self._item_height = item_height  # 각 항목의 예상 높이
        self._buffer_count = buffer_count  # 위아래로 추가 렌더링할 항목 수
        self._items = []  # 전체 항목 데이터 리스트 (flat)
        self._visible_widgets = {}  # {index: widget} 현재 렌더링된 위젯
        self._render_callback = None  # 항목 렌더링 콜백 함수
        self._scroll_scheduled = False  # 스크롤 이벤트 디바운싱

        # Canvas 설정
        self._canvas = tk.Canvas(
            self,
            bg=self._apply_appearance_mode(COLORS["bg_card"]),
            highlightthickness=0,
            borderwidth=0,
        )

        # Scrollbar 설정
        self._scrollbar = ctk.CTkScrollbar(self, command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=self._scrollbar.set)

        # 레이아웃
        self._scrollbar.pack(side="right", fill="y")
        self._canvas.pack(side="left", fill="both", expand=True)

        # 내부 컨테이너 (항목들이 배치될 프레임)
        self._container = ctk.CTkFrame(self._canvas, fg_color="transparent")
        self._canvas_window = self._canvas.create_window(
            (0, 0), window=self._container, anchor="nw", tags="container"
        )

        # 이벤트 바인딩
        self._canvas.bind("<Configure>", self._on_canvas_configure)
        self._canvas.bind("<MouseWheel>", self._on_mousewheel)
        self._canvas.bind("<Button-4>", self._on_mousewheel_linux)  # Linux
        self._canvas.bind("<Button-5>", self._on_mousewheel_linux)  # Linux
        self._container.bind("<MouseWheel>", self._on_mousewheel)

        # 스크롤 이벤트 감지를 위한 추가 바인딩
        self._scrollbar.bind("<B1-Motion>", lambda e: self._schedule_render())
        self._scrollbar.bind("<ButtonRelease-1>", lambda e: self._schedule_render())

    def _apply_appearance_mode(self, color):
        """테마에 맞는 색상 반환"""
        if isinstance(color, tuple):
            return color[1] if ctk.get_appearance_mode() == "Dark" else color[0]
        return color

    def set_render_callback(self, callback):
        """
        항목 렌더링 콜백 설정
        callback(parent_frame, item_data, index) -> widget
        """
        self._render_callback = callback

    def set_items(self, items: list):
        """항목 목록 설정 및 초기 렌더링"""
        self._items = items
        self._clear_all_widgets()
        self._update_scroll_region()
        self._render_visible_items()

    def get_items(self):
        """현재 항목 목록 반환"""
        return self._items

    def refresh(self):
        """전체 새로고침"""
        self._clear_all_widgets()
        self._update_scroll_region()
        self._render_visible_items()

    def refresh_item(self, index: int):
        """특정 항목만 새로고침"""
        if index in self._visible_widgets:
            self._visible_widgets[index].destroy()
            del self._visible_widgets[index]
            self._render_single_item(index)

    def _clear_all_widgets(self):
        """모든 렌더링된 위젯 제거"""
        for widget in self._visible_widgets.values():
            try:
                widget.destroy()
            except:
                pass
        self._visible_widgets.clear()

    def _update_scroll_region(self):
        """스크롤 영역 크기 업데이트"""
        total_height = len(self._items) * self._item_height
        canvas_width = self._canvas.winfo_width()
        if canvas_width < 10:
            canvas_width = 800  # 기본값
        self._canvas.configure(scrollregion=(0, 0, canvas_width, total_height))
        self._canvas.itemconfig(self._canvas_window, width=canvas_width)
        # 컨테이너 높이도 설정 (place 배치용)
        self._container.configure(height=total_height)

    def _on_canvas_configure(self, event):
        """캔버스 크기 변경 시"""
        self._canvas.itemconfig(self._canvas_window, width=event.width)
        self._update_scroll_region()
        self._schedule_render()

    def _on_mousewheel(self, event):
        """마우스 휠 스크롤 (Windows/Mac)"""
        delta = -1 * (event.delta // 120)
        self._canvas.yview_scroll(delta, "units")
        self._schedule_render()

    def _on_mousewheel_linux(self, event):
        """마우스 휠 스크롤 (Linux)"""
        if event.num == 4:
            self._canvas.yview_scroll(-1, "units")
        elif event.num == 5:
            self._canvas.yview_scroll(1, "units")
        self._schedule_render()

    def _schedule_render(self):
        """렌더링 스케줄링 (디바운싱)"""
        if not self._scroll_scheduled:
            self._scroll_scheduled = True
            self.after(10, self._do_scheduled_render)

    def _do_scheduled_render(self):
        """스케줄된 렌더링 실행"""
        self._scroll_scheduled = False
        self._render_visible_items()

    def _get_visible_range(self):
        """현재 보이는 항목의 인덱스 범위 계산"""
        if not self._items:
            return 0, 0

        canvas_height = self._canvas.winfo_height()
        if canvas_height < 10:
            canvas_height = 600  # 기본값

        # 현재 스크롤 위치
        scroll_top = self._canvas.yview()[0]
        scroll_bottom = self._canvas.yview()[1]

        total_height = len(self._items) * self._item_height

        # 픽셀 위치로 변환
        top_px = scroll_top * total_height
        bottom_px = scroll_bottom * total_height

        # 인덱스 계산
        start_idx = max(0, int(top_px / self._item_height) - self._buffer_count)
        end_idx = min(len(self._items), int(bottom_px / self._item_height) + self._buffer_count + 1)

        return start_idx, end_idx

    def _render_visible_items(self):
        """보이는 범위의 항목만 렌더링"""
        if not self._items or not self._render_callback:
            return

        start_idx, end_idx = self._get_visible_range()

        # 범위 밖 위젯 제거
        to_remove = [idx for idx in self._visible_widgets if idx < start_idx or idx >= end_idx]
        for idx in to_remove:
            try:
                self._visible_widgets[idx].destroy()
            except:
                pass
            del self._visible_widgets[idx]

        # 새로운 항목 렌더링
        for idx in range(start_idx, end_idx):
            if idx not in self._visible_widgets:
                self._render_single_item(idx)

    def _render_single_item(self, index: int):
        """단일 항목 렌더링"""
        if index < 0 or index >= len(self._items):
            return

        if self._render_callback:
            item_data = self._items[index]
            widget = self._render_callback(self._container, item_data, index)
            if widget:
                # place로 정확한 위치에 배치
                y_pos = index * self._item_height
                widget.place(x=0, y=y_pos, relwidth=1.0)
                self._visible_widgets[index] = widget

                # 위젯 내부에도 마우스휠 바인딩 (스크롤 전파)
                self._bind_mousewheel_recursive(widget)

    def _bind_mousewheel_recursive(self, widget):
        """위젯과 모든 자식에 마우스휠 이벤트 바인딩"""
        try:
            widget.bind("<MouseWheel>", self._on_mousewheel, add="+")
            widget.bind("<Button-4>", self._on_mousewheel_linux, add="+")
            widget.bind("<Button-5>", self._on_mousewheel_linux, add="+")
            for child in widget.winfo_children():
                self._bind_mousewheel_recursive(child)
        except:
            pass

    def scroll_to_item(self, index: int):
        """특정 항목으로 스크롤"""
        if not self._items:
            return
        total_height = len(self._items) * self._item_height
        y_pos = index * self._item_height
        self._canvas.yview_moveto(y_pos / total_height)
        self._schedule_render()


class KeyInputDialog(ctk.CTkToplevel):
    """키 입력 감지 다이얼로그"""

    def __init__(self, parent):
        super().__init__(parent)

        self._captured_key = None

        self.title("키 입력")
        self.geometry("350x200")
        self.resizable(False, False)
        self.configure(fg_color=COLORS["bg_dark"])

        self.transient(parent)
        self.grab_set()

        # 중앙 배치
        self.update_idletasks()
        x = (self.winfo_screenwidth() - 350) // 2
        y = (self.winfo_screenheight() - 200) // 2
        self.geometry(f"+{x}+{y}")

        # 안내 텍스트
        ctk.CTkLabel(
            self,
            text="원하는 키를 누르세요",
            font=ctk.CTkFont(size=14),
            text_color=COLORS["text_primary"],
        ).pack(pady=(20, 10))

        # 감지된 키 표시
        self._key_label = ctk.CTkLabel(
            self,
            text="대기 중...",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color=COLORS["accent"],
        )
        self._key_label.pack(pady=10)

        # 버튼
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(pady=20)

        ctk.CTkButton(
            btn_frame,
            text="확인",
            width=120,
            height=40,
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color=COLORS["success"],
            hover_color="#2ea44f",
            text_color="white",
            corner_radius=8,
            command=self._on_ok,
        ).pack(side="left", padx=10)

        ctk.CTkButton(
            btn_frame,
            text="취소",
            width=120,
            height=40,
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color=COLORS["error"],
            hover_color="#c0392b",
            text_color="white",
            corner_radius=8,
            command=self._on_cancel,
        ).pack(side="left", padx=10)

        # 키 바인딩
        self.bind("<Key>", self._on_key_press)
        self.focus_set()

    def _on_key_press(self, event):
        """키 입력 감지"""
        key_name = event.keysym.lower()
        # 특수 키 이름 변환
        key_map = {
            "return": "enter",
            "escape": "esc",
            "control_l": "ctrl",
            "control_r": "ctrl",
            "alt_l": "alt",
            "alt_r": "alt",
            "shift_l": "shift",
            "shift_r": "shift",
        }
        self._captured_key = key_map.get(key_name, key_name)
        self._key_label.configure(text=self._captured_key.upper())

    def _on_ok(self):
        self.destroy()

    def _on_cancel(self):
        self._captured_key = None
        self.destroy()

    def get_key(self) -> Optional[str]:
        self.wait_window()
        return self._captured_key


class PlanDetailDialog(ctk.CTkToplevel):
    """자동화 계획 상세보기/수정 다이얼로그"""

    def __init__(self, parent, plan: AutomationPlan):
        super().__init__(parent)

        self._plan = plan
        self._thumbnail_refs = []
        self._modified = False
        self._scrollable = None
        self._collapsed_items = set()  # 접힌 규칙 ID
        self._all_collapsed = True  # 전체 접기 상태 (기본값: 접힘)

        # 자식이 있는 규칙은 기본적으로 접힌 상태로 시작
        self._init_collapsed_items()

        # 드래그 앤 드롭 상태
        self._drag_data = {"rule": None, "widget": None, "start_y": 0}
        self._drop_target = None
        self._rule_widgets = {}  # rule_id -> widget 매핑
        self._selected_rule = None  # 선택된 규칙

        # 부분 실행 상태
        self._is_running = False
        self._running_executor = None

        self.title(f"계획 수정 - {plan.name}")
        self.geometry("950x700")
        self.resizable(True, True)
        self.configure(fg_color=COLORS["bg_dark"])

        self.transient(parent)
        self.grab_set()

        # 창 위치 복원 및 자동 저장
        self.update_idletasks()
        setup_window_position(self, "PlanDetailDialog")

        self._setup_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _init_collapsed_items(self):
        """자식이 있는 규칙을 접힌 상태로 초기화"""
        def add_collapsed(rules):
            for rule in rules:
                if rule.children:
                    self._collapsed_items.add(rule.rule_id)
                    add_collapsed(rule.children)
        add_collapsed(self._plan.initial_rules)
        add_collapsed(self._plan.monitoring_rules)

    def _setup_ui(self):
        """UI 구성"""
        # 하단 버튼
        btn_frame = ctk.CTkFrame(self, fg_color=COLORS["bg_card"])
        btn_frame.pack(side="bottom", fill="x", pady=0)

        btn_row = ctk.CTkFrame(btn_frame, fg_color="transparent")
        btn_row.pack(pady=15)

        self._save_btn = ctk.CTkButton(
            btn_row,
            text="저장",
            command=self._save_plan,
            width=100,
            height=38,
            fg_color=COLORS["success"],
            hover_color="#2ea44f",
            font=ctk.CTkFont(size=13, weight="bold"),
            corner_radius=6,
        )
        self._save_btn.pack(side="left", padx=8)

        ctk.CTkButton(
            btn_row,
            text="닫기",
            command=self._on_close,
            width=100,
            height=38,
            fg_color=COLORS["bg_dark"],
            hover_color=COLORS["bg_card_hover"],
            text_color=COLORS["text_secondary"],
            corner_radius=6,
        ).pack(side="left", padx=8)

        # 메인 영역
        main = ctk.CTkFrame(self, fg_color="transparent")
        main.pack(fill="both", expand=True, padx=25, pady=20)

        # 헤더
        header = ctk.CTkFrame(main, fg_color="transparent")
        header.pack(fill="x")

        # 이름 수정 가능한 입력 필드
        self._name_entry = ctk.CTkEntry(
            header,
            width=250,
            height=36,
            font=ctk.CTkFont(size=16, weight="bold"),
            fg_color=COLORS["bg_dark"],
            border_color=COLORS["border"],
            text_color=COLORS["text_primary"],
        )
        self._name_entry.insert(0, self._plan.name)
        self._name_entry.pack(side="left")
        self._name_entry.bind("<KeyRelease>", lambda e: self._mark_modified())

        ctk.CTkLabel(
            header,
            text="(이름 수정 가능, 이미지 클릭하여 크롭/수정)",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["accent"],
        ).pack(side="left", padx=(15, 0))

        # 모두 접기/펼치기 버튼
        self._collapse_btn = ctk.CTkButton(
            header,
            text="모두 접기",
            command=self._toggle_all_collapse,
            width=80,
            height=30,
            fg_color=COLORS["bg_card"],
            hover_color=COLORS["bg_card_hover"],
            text_color=COLORS["text_secondary"],
            font=ctk.CTkFont(size=12),
            corner_radius=6,
        )
        self._collapse_btn.pack(side="right", padx=(5, 0))

        # 액션 추가 버튼들 (2줄 레이아웃)
        btn_container = ctk.CTkFrame(header, fg_color="transparent")
        btn_container.pack(side="right", padx=(5, 10))

        # 첫번째 줄: 텍스트 입력, 키 입력
        btn_row1 = ctk.CTkFrame(btn_container, fg_color="transparent")
        btn_row1.pack(fill="x", pady=(0, 3))

        ctk.CTkButton(
            btn_row1,
            text="+ 텍스트 입력",
            command=self._add_text_action,
            width=110,
            height=28,
            fg_color=COLORS["success"],
            hover_color="#2ea44f",
            font=ctk.CTkFont(size=12),
            corner_radius=6,
        ).pack(side="left", padx=(0, 5))

        ctk.CTkButton(
            btn_row1,
            text="+ 키 입력",
            command=self._add_key_action,
            width=110,
            height=28,
            fg_color=COLORS["accent_orange"],
            hover_color="#d08050",
            font=ctk.CTkFont(size=12),
            corner_radius=6,
        ).pack(side="left")

        # 두번째 줄: 마우스 입력, 이미지 입력
        btn_row2 = ctk.CTkFrame(btn_container, fg_color="transparent")
        btn_row2.pack(fill="x")

        ctk.CTkButton(
            btn_row2,
            text="+ 마우스 입력",
            command=self._add_mouse_action,
            width=110,
            height=28,
            fg_color=COLORS["accent_blue"],
            hover_color="#2563eb",
            font=ctk.CTkFont(size=12),
            corner_radius=6,
        ).pack(side="left", padx=(0, 5))

        ctk.CTkButton(
            btn_row2,
            text="+ 이미지 입력",
            command=self._add_image_action,
            width=110,
            height=28,
            fg_color="#b48ead",
            hover_color="#a07090",
            font=ctk.CTkFont(size=12),
            corner_radius=6,
        ).pack(side="left")

        # 세번째 줄: 스크린샷 파일, 하위종목해체
        btn_row3 = ctk.CTkFrame(btn_container, fg_color="transparent")
        btn_row3.pack(fill="x", pady=(3, 0))

        ctk.CTkButton(
            btn_row3,
            text="📷 스크린샷",
            command=self._add_screenshot_action,
            width=110,
            height=28,
            fg_color="#5e81ac",
            hover_color="#4c6a8a",
            font=ctk.CTkFont(size=12),
            corner_radius=6,
        ).pack(side="left", padx=(0, 5))

        ctk.CTkButton(
            btn_row3,
            text="🔓 하위해체",
            command=self._flatten_children,
            width=110,
            height=28,
            fg_color="#bf616a",
            hover_color="#a54950",
            font=ctk.CTkFont(size=12),
            corner_radius=6,
        ).pack(side="left")

        # 네번째 줄: 전체액션 랜덤
        btn_row4 = ctk.CTkFrame(btn_container, fg_color="transparent")
        btn_row4.pack(fill="x", pady=(3, 0))

        ctk.CTkButton(
            btn_row4,
            text="🎲 전체 랜덤",
            command=self._randomize_all_delays,
            width=110,
            height=28,
            fg_color="#d08770",
            hover_color="#b8705a",
            font=ctk.CTkFont(size=12),
            corner_radius=6,
        ).pack(side="left", padx=(0, 5))

        ctk.CTkButton(
            btn_row4,
            text="📁 하위종속",
            command=self._toggle_all_children,
            width=110,
            height=28,
            fg_color="#88c0d0",
            hover_color="#6a9fb0",
            font=ctk.CTkFont(size=12),
            corner_radius=6,
        ).pack(side="left")

        all_rules = self._plan.initial_rules + self._plan.monitoring_rules
        ctk.CTkLabel(
            main,
            text=f"총 {len(all_rules)}개 동작  |  생성: {self._plan.created_at[:10] if self._plan.created_at else '알 수 없음'}",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["text_secondary"],
        ).pack(anchor="w", pady=(5, 15))

        # 동작 목록 (가상 스크롤)
        self._scrollable = VirtualScrollFrame(
            main,
            item_height=75,  # 각 항목 높이
            buffer_count=5,  # 위아래 버퍼
            fg_color=COLORS["bg_card"],
            corner_radius=12,
        )
        self._scrollable.pack(fill="both", expand=True)
        self._scrollable.set_render_callback(self._render_rule_item)

        # 창이 표시된 후 렌더링 시작 (렉 방지)
        self.after(50, self._refresh_action_list)

    def _get_flat_rules(self) -> List[AutomationRule]:
        """계층 구조를 평탄화하여 모든 규칙 반환 (자식 포함)"""
        result = []
        for rule in self._plan.initial_rules:
            result.append(rule)
            result.extend(self._get_children_recursive(rule))
        return result

    def _get_children_recursive(self, rule: AutomationRule) -> List[AutomationRule]:
        """재귀적으로 자식 규칙들 반환"""
        result = []
        for child in rule.children:
            result.append(child)
            result.extend(self._get_children_recursive(child))
        return result

    def _get_flat_rules_with_depth(self) -> List[dict]:
        """
        가상 스크롤용 평탄화된 규칙 목록 생성
        펼쳐진 자식만 포함, depth와 index_str 정보 포함
        """
        result = []
        parent_indices = []  # 부모 인덱스 스택

        def add_rule(rule, depth, parent_idx_str):
            # 현재 규칙의 인덱스 문자열 생성
            if depth == 0:
                idx_str = str(len([r for r in result if r["depth"] == 0]) + 1)
            else:
                # 자식 인덱스 계산
                siblings = [r for r in result if r["depth"] == depth and r["parent_id"] == rule.parent_id]
                child_idx = len(siblings) + 1
                idx_str = f"{parent_idx_str}.{child_idx}"

            result.append({
                "rule": rule,
                "depth": depth,
                "index_str": idx_str,
                "parent_id": rule.parent_id,
            })

            # 접히지 않은 경우에만 자식 추가
            if rule.children and rule.rule_id not in self._collapsed_items:
                for child in rule.children:
                    add_rule(child, depth + 1, idx_str)

        for rule in self._plan.initial_rules:
            add_rule(rule, 0, "")

        return result

    def _refresh_action_list(self):
        """동작 목록 새로고침 (가상 스크롤 방식)"""
        logger.debug("[_refresh_action_list] 액션 목록 새로고침 시작")
        if self._scrollable is None:
            return

        self._thumbnail_refs = []
        self._rule_widgets = {}  # 위젯 매핑 초기화

        # 평탄화된 규칙 목록 생성
        flat_rules = self._get_flat_rules_with_depth()

        # 디버깅: 모니터링 상태 확인
        for item in flat_rules:
            r = item["rule"]
            if getattr(r, 'is_monitoring_mode', False) or getattr(r, 'monitoring_watches', []):
                logger.info(f"[리프레시] rule_id={r.rule_id}, object_id={id(r)}, is_monitoring={r.is_monitoring_mode}, watches={len(getattr(r, 'monitoring_watches', []))}")

        logger.debug(f"[_refresh_action_list] {len(flat_rules)}개 규칙 로드됨")

        # 가상 스크롤에 항목 설정
        self._scrollable.set_items(flat_rules)

    def _render_rule_item(self, parent, item_data: dict, index: int):
        """
        가상 스크롤 콜백: 단일 규칙 항목 렌더링
        _create_action_item과 유사하지만 pack 대신 반환
        """
        rule = item_data["rule"]
        depth = item_data["depth"]
        index_str = item_data["index_str"]

        return self._create_action_item_virtual(parent, rule, depth, index_str)

    def _create_action_item_virtual(self, parent, rule: AutomationRule, depth: int, index_str: str):
        """가상 스크롤용 항목 생성 (pack 대신 반환)"""
        return self._create_action_item(parent, rule, depth, index_str, use_pack=False)

    def _update_rule_buttons(self, rule: AutomationRule):
        """규칙의 버튼들만 업데이트 (전체 새로고침 없이)"""
        if rule.rule_id not in self._rule_widgets:
            return

        widgets = self._rule_widgets[rule.rule_id]

        # 반복 횟수 버튼 업데이트
        if "repeat_btn" in widgets:
            repeat_count = getattr(rule, 'repeat_count', 1)
            btn = widgets["repeat_btn"]
            btn.configure(
                text=f"x{repeat_count}",
                fg_color=COLORS["accent_blue"] if repeat_count > 1 else COLORS["bg_card"],
                text_color="white" if repeat_count > 1 else COLORS["text_secondary"],
            )

        # 대기시간 버튼 업데이트
        if "delay_btn" in widgets:
            wait_random = getattr(rule, 'wait_random', False)
            typing_random = getattr(rule, 'typing_random', False) if rule.action_type == "type" else False
            has_random = wait_random or typing_random
            btn = widgets["delay_btn"]
            btn.configure(
                text=f"{rule.wait_after:.1f}초" + ("*" if has_random else ""),
                fg_color=COLORS["success"] if has_random else COLORS["bg_card"],
                text_color="white" if has_random else COLORS["text_secondary"],
            )

    def _create_action_item(self, parent, rule: AutomationRule, depth: int = 0, index_str: str = "1", use_pack: bool = True):
        """동작 항목 생성 (드래그 앤 드롭 지원)"""
        index = index_str  # 계층적 번호 (예: "3", "3-1", "3-2-1")
        has_children = len(rule.children) > 0
        is_collapsed = rule.rule_id in self._collapsed_items

        # 깊이에 따른 들여쓰기
        indent = depth * 30

        # 외부 wrapper (item + children 포함) - 펼치기/접기 시 순서 유지를 위해
        item_wrapper = ctk.CTkFrame(parent, fg_color="transparent")
        if use_pack:
            item_wrapper.pack(fill="x")

        # 선택 상태에 따른 배경색
        is_selected = self._selected_rule is not None and self._selected_rule.rule_id == rule.rule_id
        bg_color = "#2e7d32" if is_selected else COLORS["bg_dark"]  # 선택 시 초록색

        item = ctk.CTkFrame(item_wrapper, fg_color=bg_color, corner_radius=8)
        item.pack(fill="x", pady=4, padx=(10 + indent, 10))

        # 위젯 매핑 저장
        self._rule_widgets[rule.rule_id] = {"widget": item, "rule": rule, "depth": depth, "wrapper": item_wrapper}

        # 클릭 시 선택
        def select_rule(event, r=rule):
            self._select_rule(r)
        item.bind("<Button-1>", select_rule)

        content = ctk.CTkFrame(item, fg_color="transparent")
        content.pack(fill="x", padx=12, pady=10)
        content.bind("<Button-1>", select_rule)  # content도 클릭 가능

        # 오른쪽 클릭 메뉴
        def show_context_menu(event, r=rule, d=depth):
            from tkinter import Menu
            popup = Menu(self, tearoff=0)
            popup.add_command(label="이름 설정", command=lambda: self._edit_rule_name(r))
            # 클릭 유형 변경 (클릭 계열 액션인 경우만)
            if r.action_type in ["click", "double_click", "right_click"]:
                click_menu = Menu(popup, tearoff=0)
                click_menu.add_command(
                    label="✓ 왼쪽 클릭" if r.action_type == "click" else "  왼쪽 클릭",
                    command=lambda: self._change_rule_click_type(r, "click")
                )
                click_menu.add_command(
                    label="✓ 더블 클릭" if r.action_type == "double_click" else "  더블 클릭",
                    command=lambda: self._change_rule_click_type(r, "double_click")
                )
                click_menu.add_command(
                    label="✓ 오른쪽 클릭" if r.action_type == "right_click" else "  오른쪽 클릭",
                    command=lambda: self._change_rule_click_type(r, "right_click")
                )
                popup.add_cascade(label="클릭 유형", menu=click_menu)
            popup.add_separator()
            popup.add_command(label="복사", command=lambda: self._copy_rule(r))
            # 붙여넣기 (클립보드에 내용이 있을 때만 활성화)
            if get_action_clipboard() is not None:
                # 모니터링 액션으로 붙이기 서브메뉴 (하위로 붙여넣기 위에 배치)
                monitor_menu = tk.Menu(popup, tearoff=0)
                has_monitoring = False
                for mi, mr in enumerate(self._plan.initial_rules):
                    if getattr(mr, 'is_monitoring_mode', False) and getattr(mr, 'monitoring_watches', []):
                        has_monitoring = True
                        # 액션 이름 (description 사용)
                        action_name = mr.description[:15] if mr.description else f"액션{mi+1}"
                        for wi, watch in enumerate(mr.monitoring_watches):
                            watch_label = f"{action_name} - 감시{wi+1}"
                            monitor_menu.add_command(
                                label=watch_label,
                                command=lambda m=mi, w=wi: self._paste_as_monitor_action(m, w)
                            )
                if has_monitoring:
                    popup.add_cascade(label="모니터링 액션으로 붙이기", menu=monitor_menu)
                popup.add_command(label="하위로 붙여넣기", command=lambda: self._paste_rule(r))
                popup.add_command(label="최상위에 붙여넣기", command=self._paste_rule_top)
            else:
                popup.add_command(label="하위로 붙여넣기", state="disabled")
                popup.add_command(label="최상위에 붙여넣기", state="disabled")
            if d > 0:  # 자식인 경우에만 종속 해제 표시
                popup.add_separator()
                popup.add_command(label="종속 해제", command=lambda: self._detach_rule(r))
            popup.tk_popup(event.x_root, event.y_root)

        # 드래그 및 우클릭 바인딩 헬퍼
        def bind_drag(widget):
            widget.bind("<Button-1>", lambda e, r=rule, w=item: self._on_drag_start(e, r, w))
            widget.bind("<B1-Motion>", self._on_drag_motion)
            widget.bind("<ButtonRelease-1>", self._on_drag_release)
            widget.bind("<Button-3>", lambda e, r=rule, d=depth: show_context_menu(e, r, d))
            # 드래그 커서 설정 - 내부 위젯에 직접 적용
            try:
                widget.configure(cursor="fleur")
                # CTk 내부 캔버스에도 커서 적용
                for child in widget.winfo_children():
                    child.configure(cursor="fleur")
            except (tk.TclError, AttributeError):
                pass

        # 액션 색상 (숫자 배지용)
        action_colors = {
            "click": COLORS["accent_blue"],
            "double_click": COLORS["accent_blue"],
            "right_click": COLORS["accent_blue"],
            "type": COLORS["success"],
            "hotkey": COLORS["accent_orange"],
            "key_press": COLORS["accent_orange"],
            "scroll": "#b48ead",
            "drag": COLORS["warning"],
        }
        color = action_colors.get(rule.action_type, COLORS["text_muted"])

        # 숫자 배지 (맨 앞에 위치)
        num_lbl = ctk.CTkLabel(
            content, text=f"{index}", font=ctk.CTkFont(size=12, weight="bold"),
            fg_color=color, text_color="white", corner_radius=4, width=26, height=22,
        )
        num_lbl.pack(side="left", padx=(0, 8))
        bind_drag(num_lbl)

        # 자식이 있으면 접기/펼치기 토글 버튼
        if has_children:
            toggle_btn = ctk.CTkButton(
                content,
                text="▶" if is_collapsed else "▼",
                font=ctk.CTkFont(size=10),
                fg_color="transparent",
                hover_color=COLORS["bg_card_hover"],
                text_color=COLORS["text_secondary"],
                width=24,
                height=24,
                corner_radius=4,
                command=lambda r=rule: self._toggle_item_collapse(r.rule_id),
            )
            toggle_btn.pack(side="left", padx=(0, 4))

        action_names = {
            "click": "왼쪽 클릭",
            "double_click": "더블 클릭",
            "right_click": "오른쪽 클릭",
            "type": "텍스트 입력",
            "hotkey": "단축키",
            "key_press": "키 입력",
            "scroll": "스크롤",
            "drag": "드래그",
        }

        # 썸네일 (클릭 가능)
        thumb = ctk.CTkFrame(content, fg_color=COLORS["bg_card"], width=50, height=50, corner_radius=6)
        thumb.pack(side="left", padx=(0, 10))
        thumb.pack_propagate(False)
        self._display_thumbnail(thumb, rule)

        # 정보 영역
        info = ctk.CTkFrame(content, fg_color="transparent")
        info.pack(side="left", fill="both", expand=True)

        # 번호 + 동작 유형
        row1 = ctk.CTkFrame(info, fg_color="transparent")
        row1.pack(fill="x", anchor="w")

        # 깊이 표시 (자식인 경우)
        if depth > 0:
            lbl = ctk.CTkLabel(row1, text="└", font=ctk.CTkFont(size=14), text_color=COLORS["text_muted"])
            lbl.pack(side="left", padx=(0, 4))
            bind_drag(lbl)

        type_lbl = ctk.CTkLabel(
            row1, text=action_names.get(rule.action_type, rule.action_type or "동작"),
            font=ctk.CTkFont(size=13, weight="bold"), text_color=color,
        )
        type_lbl.pack(side="left")
        bind_drag(type_lbl)

        # 이름(설명) 표시
        if rule.description:
            name_lbl = ctk.CTkLabel(
                row1, text=f" - {rule.description}",
                font=ctk.CTkFont(size=12), text_color=COLORS["text_primary"],
            )
            name_lbl.pack(side="left")
            bind_drag(name_lbl)

        # 자식 수 표시
        if has_children:
            child_lbl = ctk.CTkLabel(
                row1, text=f"  ({len(rule.children)}개 하위)",
                font=ctk.CTkFont(size=11), text_color=COLORS["text_muted"],
            )
            child_lbl.pack(side="left")
            bind_drag(child_lbl)

        # 모니터링 모드 표시 (초록색)
        if getattr(rule, 'is_monitoring_mode', False):
            # 모니터링 액션 개수 계산
            monitor_action_count = 0
            for w in getattr(rule, 'monitoring_watches', []):
                monitor_action_count += len(w.get('monitor_actions', []))

            monitoring_text = "  모니터링 모드"
            if monitor_action_count > 0:
                monitoring_text += f" ({monitor_action_count}개 액션)"

            monitoring_lbl = ctk.CTkLabel(
                row1, text=monitoring_text,
                font=ctk.CTkFont(size=11, weight="bold"), text_color="#2ecc71",
            )
            monitoring_lbl.pack(side="left")
            bind_drag(monitoring_lbl)

        # 빈 공간 (드래그 가능)
        spacer = ctk.CTkLabel(row1, text="", fg_color="transparent")
        spacer.pack(side="left", fill="x", expand=True)
        bind_drag(spacer)

        # 상세 정보
        row2 = ctk.CTkFrame(info, fg_color="transparent")
        row2.pack(fill="x", pady=(4, 0), anchor="w")

        details = []
        if rule.action_x is not None and rule.action_y is not None:
            details.append(f"({rule.action_x}, {rule.action_y})")
        if rule.action_text:
            text_preview = rule.action_text[:25] + "..." if len(rule.action_text) > 25 else rule.action_text
            details.append(f'"{text_preview}"')
        if rule.action_keys:
            details.append(f"[{' + '.join(rule.action_keys).upper()}]")

        if details:
            detail_lbl = ctk.CTkLabel(
                row2, text=" | ".join(details),
                font=ctk.CTkFont(size=11), text_color=COLORS["text_secondary"],
            )
            detail_lbl.pack(side="left")
            bind_drag(detail_lbl)

        # row2 빈 공간도 드래그 가능
        spacer2 = ctk.CTkLabel(row2, text="", fg_color="transparent")
        spacer2.pack(side="left", fill="x", expand=True)
        bind_drag(spacer2)

        # 버튼 영역
        btn_frame = ctk.CTkFrame(content, fg_color="transparent")
        btn_frame.pack(side="right", padx=(10, 0))

        # 위/아래 이동 버튼 (세로 배치, 크고 예쁘게)
        move_frame = ctk.CTkFrame(btn_frame, fg_color=COLORS["bg_card"], corner_radius=6)
        move_frame.pack(side="right", padx=(4, 0))

        ctk.CTkButton(
            move_frame,
            text="▲",
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color="transparent",
            hover_color=COLORS["accent_blue"],
            text_color=COLORS["text_secondary"],
            hover=True,
            width=28,
            height=18,
            corner_radius=4,
            command=lambda r=rule: self._move_rule_up(r),
        ).pack(side="top", padx=2, pady=(2, 0))

        ctk.CTkButton(
            move_frame,
            text="▼",
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color="transparent",
            hover_color=COLORS["accent_blue"],
            text_color=COLORS["text_secondary"],
            hover=True,
            width=28,
            height=18,
            corner_radius=4,
            command=lambda r=rule: self._move_rule_down(r),
        ).pack(side="top", padx=2, pady=(0, 2))

        # 삭제 버튼
        ctk.CTkButton(
            btn_frame,
            text="✕",
            font=ctk.CTkFont(size=12),
            fg_color=COLORS["error"],
            hover_color="#c0392b",
            text_color="white",
            width=30,
            height=26,
            corner_radius=4,
            command=lambda r=rule: self._delete_rule(r),
        ).pack(side="right", padx=(4, 0))

        # 테스트 실행 버튼
        ctk.CTkButton(
            btn_frame,
            text="▶",
            font=ctk.CTkFont(size=14),
            fg_color=COLORS["accent_orange"],
            hover_color="#d97706",
            text_color="white",
            width=30,
            height=26,
            corner_radius=4,
            command=lambda r=rule: self._test_run_rule(r),
        ).pack(side="right", padx=(4, 0))

        # 대기시간 버튼 (랜덤 여부 표시)
        wait_random = getattr(rule, 'wait_random', False)
        typing_random = getattr(rule, 'typing_random', False) if rule.action_type == "type" else False
        has_random = wait_random or typing_random
        delay_btn = ctk.CTkButton(
            btn_frame,
            text=f"{rule.wait_after:.1f}초" + ("*" if has_random else ""),
            font=ctk.CTkFont(size=11),
            fg_color=COLORS["success"] if has_random else COLORS["bg_card"],
            hover_color="#2ea44f" if has_random else COLORS["bg_card_hover"],
            text_color="white" if has_random else COLORS["text_secondary"],
            width=55,
            height=26,
            corner_radius=4,
            command=lambda r=rule: self._edit_wait_time(r),
        )
        delay_btn.pack(side="right", padx=(4, 0))

        # 반복 횟수 버튼
        repeat_count = getattr(rule, 'repeat_count', 1)
        repeat_btn = ctk.CTkButton(
            btn_frame,
            text=f"x{repeat_count}",
            font=ctk.CTkFont(size=11),
            fg_color=COLORS["accent_blue"] if repeat_count > 1 else COLORS["bg_card"],
            hover_color="#1a7fd4" if repeat_count > 1 else COLORS["bg_card_hover"],
            text_color="white" if repeat_count > 1 else COLORS["text_secondary"],
            width=40,
            height=26,
            corner_radius=4,
            command=lambda r=rule: self._edit_repeat_count(r),
        )
        repeat_btn.pack(side="right", padx=(4, 0))

        # 스킵 모드 버튼 (S) - 이미지 못찾으면 스킵
        is_skip = getattr(rule, 'skip_on_not_found', False)
        skip_btn = ctk.CTkButton(
            btn_frame,
            text="S",
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color="#2ecc71" if is_skip else COLORS["bg_card"],
            hover_color="#27ae60" if is_skip else COLORS["bg_card_hover"],
            text_color="white" if is_skip else COLORS["text_secondary"],
            width=30,
            height=26,
            corner_radius=4,
            command=lambda r=rule: self._toggle_skip_mode(r),
        )
        skip_btn.pack(side="right", padx=(4, 0))
        self._rule_widgets[rule.rule_id]["skip_btn"] = skip_btn

        # 트리거 이미지 버튼 (설정 여부 표시)
        has_trigger = bool(rule.trigger_image)
        ctk.CTkButton(
            btn_frame,
            text="c",
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#2ecc71" if has_trigger else COLORS["bg_card"],
            hover_color="#27ae60" if has_trigger else COLORS["bg_card_hover"],
            text_color="white" if has_trigger else COLORS["text_secondary"],
            width=30,
            height=26,
            corner_radius=4,
            command=lambda r=rule: self._edit_trigger_image(r),
        ).pack(side="right", padx=(4, 0))

        # 버튼 참조 저장 (개별 업데이트용)
        self._rule_widgets[rule.rule_id]["repeat_btn"] = repeat_btn
        self._rule_widgets[rule.rule_id]["delay_btn"] = delay_btn

        # 모니터링 모드 버튼
        is_monitoring = getattr(rule, 'is_monitoring_mode', False)
        monitor_actions_count = sum(len(w.get('monitor_actions', [])) for w in getattr(rule, 'monitoring_watches', []))
        if is_monitoring or monitor_actions_count > 0:
            logger.info(f"[M버튼 생성] rule={rule.action_type}, is_monitoring={is_monitoring}, actions={monitor_actions_count}")
        ctk.CTkButton(
            btn_frame,
            text="M",
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color="#2ecc71" if is_monitoring else COLORS["bg_card"],
            hover_color="#27ae60" if is_monitoring else COLORS["bg_card_hover"],
            text_color="white" if is_monitoring else COLORS["text_secondary"],
            width=30,
            height=26,
            corner_radius=4,
            command=lambda r=rule: self._edit_monitoring_mode(r),
        ).pack(side="right", padx=(4, 0))

        # 자식 규칙들 표시 (항상 생성, visibility로 제어)
        # item_wrapper 안에 생성하여 펼치기/접기 시 순서 유지
        # 가상 스크롤 모드(use_pack=False)에서는 자식을 여기서 생성하지 않음 (평탄화된 리스트로 처리)
        if has_children and use_pack:
            children_container = ctk.CTkFrame(item_wrapper, fg_color="transparent")
            if not is_collapsed:
                children_container.pack(fill="x")
            # 위젯 매핑에 children_container 추가
            self._rule_widgets[rule.rule_id]["children_container"] = children_container
            for child_idx, child in enumerate(rule.children, 1):
                child_index_str = f"{index}-{child_idx}"  # 예: "3-1", "3-2"
                self._create_action_item(children_container, child, depth + 1, index_str=child_index_str)

        return item_wrapper

    def _on_drag_start(self, event, rule: AutomationRule, widget):
        """드래그 시작"""
        self._drag_data["rule"] = rule
        self._drag_data["widget"] = widget
        self._drag_data["start_y"] = event.y_root
        widget.configure(fg_color=COLORS["accent_blue"])

    def _on_drag_motion(self, event):
        """드래그 중"""
        if not self._drag_data["rule"]:
            return

        # 마우스 위치에서 드롭 대상 찾기
        target_rule = None
        for rule_id, data in self._rule_widgets.items():
            if rule_id == self._drag_data["rule"].rule_id:
                continue  # 자기 자신 제외

            widget = data["widget"]
            try:
                # 위젯의 절대 위치 계산
                wy = widget.winfo_rooty()
                wh = widget.winfo_height()

                # 마우스가 위젯 영역 안에 있는지 확인
                if wy <= event.y_root <= wy + wh:
                    target_rule = data["rule"]
                    break
            except (tk.TclError, KeyError, AttributeError):
                continue

        # 이전 드롭 대상 하이라이트 제거
        if self._drop_target and self._drop_target != target_rule:
            try:
                old_data = self._rule_widgets.get(self._drop_target.rule_id)
                if old_data:
                    old_data["widget"].configure(fg_color=COLORS["bg_dark"])
            except (tk.TclError, KeyError, AttributeError):
                pass

        # 새 드롭 대상 하이라이트
        self._drop_target = target_rule
        if target_rule:
            try:
                target_data = self._rule_widgets.get(target_rule.rule_id)
                if target_data:
                    target_data["widget"].configure(fg_color=COLORS["success"])
            except (tk.TclError, KeyError, AttributeError):
                pass

    def _on_drag_release(self, event):
        """드래그 종료"""
        dragged_rule = self._drag_data["rule"]
        target_rule = self._drop_target

        # 원래 색상 복원
        if self._drag_data["widget"]:
            self._drag_data["widget"].configure(fg_color=COLORS["bg_dark"])
        if target_rule:
            try:
                target_data = self._rule_widgets.get(target_rule.rule_id)
                if target_data:
                    target_data["widget"].configure(fg_color=COLORS["bg_dark"])
            except (tk.TclError, KeyError, AttributeError):
                pass

        # 드롭 처리
        if dragged_rule and target_rule and dragged_rule != target_rule:
            self._move_rule_to_target(dragged_rule, target_rule)

        # 상태 초기화
        self._drag_data = {"rule": None, "widget": None, "start_y": 0}
        self._drop_target = None

    def _move_rule_to_target(self, dragged: AutomationRule, target: AutomationRule):
        """드래그한 규칙을 대상의 자식으로 이동"""
        # 순환 참조 방지: 드래그한 것이 대상의 부모인지 확인
        if self._is_ancestor(dragged, target):
            return

        # 현재 위치에서 제거
        if dragged in self._plan.initial_rules:
            self._plan.initial_rules.remove(dragged)
        else:
            parent = self._find_parent_rule(dragged)
            if parent and dragged in parent.children:
                parent.children.remove(dragged)

        # 대상의 자식으로 추가
        dragged.parent_id = target.rule_id
        target.children.append(dragged)

        self._modified = True
        self._refresh_action_list()
        logger.info(f"규칙을 '{target.rule_id}'의 하위로 이동")

    def _is_ancestor(self, potential_ancestor: AutomationRule, target: AutomationRule) -> bool:
        """potential_ancestor가 target의 조상인지 확인"""
        def check_children(rule):
            if rule == target:
                return True
            for child in rule.children:
                if check_children(child):
                    return True
            return False
        return check_children(potential_ancestor)

    def _display_thumbnail(self, parent, rule: AutomationRule):
        """썸네일 표시 (클릭하여 편집) - 캐시 사용"""
        image_path = rule.target_image

        if image_path and Path(image_path).exists():
            try:
                # 캐시된 썸네일 확인
                target_size = (60, 60)
                ctk_image = get_cached_thumbnail(image_path, target_size)

                if ctk_image is None:
                    # 캐시에 없으면 로드 (한글 경로 지원)
                    img_arr = np.fromfile(image_path, np.uint8)
                    img = cv2.imdecode(img_arr, cv2.IMREAD_COLOR)
                    if img is not None:
                        img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                        h, w = img_rgb.shape[:2]
                        scale = min(60 / w, 60 / h)
                        new_w, new_h = int(w * scale), int(h * scale)
                        resized = cv2.resize(img_rgb, (new_w, new_h))
                        pil_image = Image.fromarray(resized)
                        ctk_image = ctk.CTkImage(light_image=pil_image, dark_image=pil_image, size=(new_w, new_h))
                        # 캐시에 저장
                        set_cached_thumbnail(image_path, target_size, ctk_image)

                if ctk_image is not None:
                    # 클릭 가능한 버튼
                    thumb_btn = ctk.CTkButton(
                        parent,
                        image=ctk_image,
                        text="",
                        width=68,
                        height=68,
                        fg_color="transparent",
                        hover_color=COLORS["bg_card_hover"],
                        corner_radius=4,
                        command=lambda p=image_path, r=rule: self._open_image_editor(p, r),
                    )
                    thumb_btn.pack(expand=True)
                    self._thumbnail_refs.append(ctk_image)
                    return
            except (IOError, OSError, ValueError):
                pass

        icons = {"click": "🖱", "type": "⌨", "hotkey": "⌨", "scroll": "📜", "drag": "↔"}
        ctk.CTkLabel(
            parent,
            text=icons.get(rule.action_type, "📋"),
            font=ctk.CTkFont(size=20),
            text_color=COLORS["text_muted"],
        ).pack(expand=True)

    def _collect_all_image_rules(self) -> list:
        """이미지가 있는 모든 규칙 수집 (재귀)"""
        result = []
        def collect(rules):
            for r in rules:
                if r.target_image:
                    result.append(r)
                if r.children:
                    collect(r.children)
        collect(self._plan.initial_rules)
        collect(self._plan.monitoring_rules)
        return result

    def _open_image_editor(self, image_path: str, rule: AutomationRule):
        """이미지 편집기 열기"""
        # 모든 이미지 규칙 수집
        all_image_rules = self._collect_all_image_rules()
        current_index = -1
        for i, r in enumerate(all_image_rules):
            if r.rule_id == rule.rule_id:
                current_index = i
                break

        # 수정 여부 추적 (다이얼로그 닫힐 때 한 번만 새로고침)
        needs_refresh = [False]

        def on_crop_complete(new_path: str):
            self._modified = True
            needs_refresh[0] = True
            logger.info(f"이미지 크롭 완료: {new_path}")
            invalidate_thumbnail_cache(new_path)  # 캐시 무효화

        def on_delete():
            rule.target_image = None
            self._modified = True
            needs_refresh[0] = True
            invalidate_thumbnail_cache(image_path)  # 캐시 무효화
            logger.info(f"이미지 삭제됨: {rule.rule_id}")

        def on_change(new_path: str):
            rule.target_image = new_path
            self._modified = True
            needs_refresh[0] = True
            logger.info(f"이미지 변경 완료: {new_path}")

        def on_search_radius_change():
            self._modified = True
            needs_refresh[0] = True

        dialog = ImageCropDialog(
            self, image_path,
            on_crop=on_crop_complete,
            on_delete=on_delete,
            on_change=on_change,
            rule=rule,
            on_search_radius_change=on_search_radius_change,
            image_list=all_image_rules,
            current_index=current_index,
        )
        self.wait_window(dialog)

        # 다이얼로그 닫힌 후 한 번만 새로고침
        if needs_refresh[0]:
            self._refresh_action_list()

    def _mark_modified(self):
        """수정됨 표시"""
        self._modified = True

    def _save_plan(self):
        """계획 저장"""
        try:
            # 이름 업데이트
            new_name = self._name_entry.get().strip()
            if new_name:
                self._plan.name = new_name

            # 수정됨 표시 (수정된 계획은 재분석 불가)
            self._plan.modified = True

            PLANS_DIR.mkdir(parents=True, exist_ok=True)
            plan_file = PLANS_DIR / f"{self._plan.plan_id}.json"
            with open(plan_file, 'w', encoding='utf-8') as f:
                json.dump(self._plan.to_dict(), f, ensure_ascii=False, indent=2)
            logger.info(f"자동화 계획 저장: {plan_file}")

            # 연관된 녹화 이름도 업데이트 (계획 이름과 동기화)
            if new_name:
                db = get_db()
                recording = db.get_recording_by_plan_id(self._plan.plan_id)
                if recording and recording.id:
                    # 계획 이름에서 "_자동화" 접미사 제거 후 녹화 이름으로 사용
                    recording_name = new_name.replace("_자동화", "") if new_name.endswith("_자동화") else new_name
                    db.update_recording_name(recording.id, recording_name)
                    logger.info(f"녹화 이름 동기화: {recording_name}")

            self._modified = False

            from tkinter import messagebox
            messagebox.showinfo("저장 완료", "계획이 저장되었습니다.")
        except Exception as e:
            logger.error(f"계획 저장 실패: {e}")
            from tkinter import messagebox
            messagebox.showerror("저장 실패", f"저장 중 오류가 발생했습니다:\n{e}")

    def _delete_monitor_action(self, rule: AutomationRule, watch_idx: int, action_idx: int):
        """모니터링 액션 삭제"""
        monitoring_watches = getattr(rule, 'monitoring_watches', [])
        if watch_idx < len(monitoring_watches):
            monitor_actions = monitoring_watches[watch_idx].get('monitor_actions', [])
            if action_idx < len(monitor_actions):
                monitor_actions.pop(action_idx)
                self._modified = True
                self._refresh_action_list()
                logger.info(f"모니터링 액션 삭제: watch {watch_idx}, action {action_idx}")

    def _delete_rule(self, rule: AutomationRule):
        """규칙 삭제"""
        from tkinter import messagebox
        if not messagebox.askyesno("삭제 확인", "이 액션을 삭제하시겠습니까?"):
            return

        # 최상위에서 찾기
        if rule in self._plan.initial_rules:
            self._plan.initial_rules.remove(rule)
        elif rule in self._plan.monitoring_rules:
            self._plan.monitoring_rules.remove(rule)
        else:
            # 부모에서 찾아서 삭제
            parent = self._find_parent_rule(rule)
            if parent and rule in parent.children:
                parent.children.remove(rule)

        self._modified = True
        self._refresh_action_list()
        logger.info(f"규칙 삭제: {rule.rule_id}")

    def _move_rule_up(self, rule: AutomationRule):
        """규칙을 위로 이동"""
        # 부모가 있으면 부모의 children에서 이동
        parent = self._find_parent_rule(rule)
        if parent:
            rules_list = parent.children
        elif rule in self._plan.initial_rules:
            rules_list = self._plan.initial_rules
        elif rule in self._plan.monitoring_rules:
            rules_list = self._plan.monitoring_rules
        else:
            return

        idx = rules_list.index(rule)
        if idx > 0:
            rules_list[idx], rules_list[idx - 1] = rules_list[idx - 1], rules_list[idx]
            self._modified = True
            self._refresh_action_list()

    def _move_rule_down(self, rule: AutomationRule):
        """규칙을 아래로 이동"""
        # 부모가 있으면 부모의 children에서 이동
        parent = self._find_parent_rule(rule)
        if parent:
            rules_list = parent.children
        elif rule in self._plan.initial_rules:
            rules_list = self._plan.initial_rules
        elif rule in self._plan.monitoring_rules:
            rules_list = self._plan.monitoring_rules
        else:
            return

        idx = rules_list.index(rule)
        if idx < len(rules_list) - 1:
            rules_list[idx], rules_list[idx + 1] = rules_list[idx + 1], rules_list[idx]
            self._modified = True
            self._refresh_action_list()

    def _edit_wait_time(self, rule: AutomationRule):
        """대기시간 및 랜덤 설정"""
        is_type_action = rule.action_type == "type"
        dialog_height = 480 if is_type_action else 320

        dialog = ctk.CTkToplevel(self)
        dialog.title("대기시간 설정")
        dialog.geometry(f"400x{dialog_height}")
        dialog.resizable(False, False)
        dialog.configure(fg_color=COLORS["bg_dark"])
        dialog.transient(self)
        dialog.grab_set()

        # 중앙 배치
        dialog.update_idletasks()
        x = (dialog.winfo_screenwidth() - 400) // 2
        y = (dialog.winfo_screenheight() - dialog_height) // 2
        dialog.geometry(f"+{x}+{y}")

        # 스크롤 가능 프레임
        main_frame = ctk.CTkScrollableFrame(dialog, fg_color="transparent")
        main_frame.pack(fill="both", expand=True, padx=20, pady=15)

        # === 기본 대기시간 ===
        ctk.CTkLabel(main_frame, text="기본 대기시간 (초)",
                     font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", pady=(0, 5))
        wait_entry = ctk.CTkEntry(main_frame, width=200, height=38, font=ctk.CTkFont(size=14))
        wait_entry.insert(0, f"{rule.wait_after:.2f}")
        wait_entry.pack(anchor="w")

        # === 랜덤 대기시간 ===
        ctk.CTkLabel(main_frame, text="",
                     font=ctk.CTkFont(size=8)).pack()  # 구분선

        wait_random_var = ctk.BooleanVar(value=getattr(rule, 'wait_random', False))
        ctk.CTkCheckBox(main_frame, text="랜덤시간 활성화", variable=wait_random_var,
                        font=ctk.CTkFont(size=13)).pack(anchor="w", pady=(10, 5))

        wait_range_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        wait_range_frame.pack(anchor="w", pady=5)

        ctk.CTkLabel(wait_range_frame, text="±범위:", font=ctk.CTkFont(size=12)).pack(side="left")
        wait_range_entry = ctk.CTkEntry(wait_range_frame, width=100, height=32, font=ctk.CTkFont(size=13))
        wait_range_entry.insert(0, f"{getattr(rule, 'wait_random_range', 0.3):.2f}")
        wait_range_entry.pack(side="left", padx=(5, 10))
        ctk.CTkLabel(wait_range_frame, text="초", font=ctk.CTkFont(size=12),
                     text_color=COLORS["text_secondary"]).pack(side="left")

        # === 타이핑 랜덤 (텍스트 액션만) ===
        typing_random_var = ctk.BooleanVar(value=False)
        typing_delay_entry = None
        typing_range_entry = None

        if is_type_action:
            ctk.CTkLabel(main_frame, text="",
                         font=ctk.CTkFont(size=8)).pack()  # 구분선
            ctk.CTkLabel(main_frame, text="타이핑랜덤 (글자 사이사이에 딜레이)",
                         font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", pady=(10, 5))

            typing_random_var = ctk.BooleanVar(value=getattr(rule, 'typing_random', False))
            ctk.CTkCheckBox(main_frame, text="타이핑랜덤 활성화", variable=typing_random_var,
                            font=ctk.CTkFont(size=13)).pack(anchor="w", pady=5)

            # 기본 딜레이
            typing_base_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
            typing_base_frame.pack(anchor="w", pady=5)

            ctk.CTkLabel(typing_base_frame, text="기본 딜레이:", font=ctk.CTkFont(size=12)).pack(side="left")
            typing_delay_entry = ctk.CTkEntry(typing_base_frame, width=80, height=32, font=ctk.CTkFont(size=13))
            typing_delay_entry.insert(0, f"{getattr(rule, 'typing_delay', 0.1):.2f}")
            typing_delay_entry.pack(side="left", padx=(5, 10))
            ctk.CTkLabel(typing_base_frame, text="초", font=ctk.CTkFont(size=12),
                         text_color=COLORS["text_secondary"]).pack(side="left")

            # ±범위
            typing_range_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
            typing_range_frame.pack(anchor="w", pady=5)

            ctk.CTkLabel(typing_range_frame, text="±범위:", font=ctk.CTkFont(size=12)).pack(side="left")
            typing_range_entry = ctk.CTkEntry(typing_range_frame, width=80, height=32, font=ctk.CTkFont(size=13))
            typing_range_entry.insert(0, f"{getattr(rule, 'typing_delay_range', 0.05):.2f}")
            typing_range_entry.pack(side="left", padx=(5, 10))
            ctk.CTkLabel(typing_range_frame, text="초", font=ctk.CTkFont(size=12),
                         text_color=COLORS["text_secondary"]).pack(side="left")

        result = {"saved": False}

        def save():
            def parse_float(entry):
                val = entry.get().strip().replace(',', '.')
                return float(val) if val else 0.0
            try:
                wait_val = parse_float(wait_entry)
                wait_range = parse_float(wait_range_entry)

                if wait_val < 0:
                    from tkinter import messagebox
                    messagebox.showerror("오류", "대기시간은 0 이상이어야 합니다")
                    return

                if wait_range < 0:
                    from tkinter import messagebox
                    messagebox.showerror("오류", "±범위는 0 이상이어야 합니다")
                    return

                # 저장
                rule.wait_after = wait_val
                rule.wait_random = wait_random_var.get()
                rule.wait_random_range = wait_range

                # 타이핑 랜덤
                if is_type_action and typing_delay_entry and typing_range_entry:
                    typing_delay = parse_float(typing_delay_entry)
                    typing_range = parse_float(typing_range_entry)
                    if typing_delay < 0 or typing_range < 0:
                        from tkinter import messagebox
                        messagebox.showerror("오류", "딜레이와 범위는 0 이상이어야 합니다")
                        return
                    rule.typing_random = typing_random_var.get()
                    rule.typing_delay = typing_delay
                    rule.typing_delay_range = typing_range

                result["saved"] = True
                dialog.destroy()
            except ValueError:
                from tkinter import messagebox
                messagebox.showerror("오류", "숫자를 입력하세요")

        # 버튼
        btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_frame.pack(pady=15)
        ctk.CTkButton(btn_frame, text="저장", width=120, height=40,
                      font=ctk.CTkFont(size=14, weight="bold"), command=save).pack(side="left", padx=10)
        ctk.CTkButton(btn_frame, text="취소", width=120, height=40,
                      font=ctk.CTkFont(size=14, weight="bold"), fg_color=COLORS["bg_card"],
                      command=dialog.destroy).pack(side="left", padx=10)

        dialog.wait_window()

        if result["saved"]:
            self._modified = True
            self._update_rule_buttons(rule)
            logger.info(f"대기시간 설정 완료")

    def _edit_repeat_count(self, rule: AutomationRule):
        """반복 설정 (횟수 + 반복 대기시간 + 랜덤)"""
        dialog = ctk.CTkToplevel(self)
        dialog.title("반복 설정")
        dialog.geometry("350x420")
        dialog.resizable(False, False)
        dialog.configure(fg_color=COLORS["bg_dark"])
        dialog.transient(self)
        dialog.grab_set()

        # 중앙 배치
        dialog.update_idletasks()
        x = (dialog.winfo_screenwidth() - 350) // 2
        y = (dialog.winfo_screenheight() - 420) // 2
        dialog.geometry(f"+{x}+{y}")

        main_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        main_frame.pack(fill="both", expand=True, padx=20, pady=15)

        # 반복 횟수
        ctk.CTkLabel(main_frame, text="반복 횟수",
                     font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", pady=(0, 5))

        count_entry = ctk.CTkEntry(main_frame, width=200, height=38, font=ctk.CTkFont(size=14))
        count_entry.insert(0, str(getattr(rule, 'repeat_count', 1)))
        count_entry.pack(anchor="w")

        ctk.CTkLabel(main_frame, text="1 = 1회 실행, 2 = 2회 반복...",
                     font=ctk.CTkFont(size=11), text_color=COLORS["text_secondary"]).pack(anchor="w", pady=(5, 0))

        # 반복 대기시간
        ctk.CTkLabel(main_frame, text="반복 대기시간 (초)",
                     font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", pady=(15, 5))

        delay_entry = ctk.CTkEntry(main_frame, width=200, height=38, font=ctk.CTkFont(size=14))
        delay_entry.insert(0, f"{getattr(rule, 'repeat_delay', 0.5):.2f}")
        delay_entry.pack(anchor="w")

        ctk.CTkLabel(main_frame, text="반복 사이의 대기시간",
                     font=ctk.CTkFont(size=11), text_color=COLORS["text_secondary"]).pack(anchor="w", pady=(5, 0))

        # 랜덤 대기시간
        delay_random_var = ctk.BooleanVar(value=getattr(rule, 'repeat_delay_random', False))
        ctk.CTkCheckBox(main_frame, text="랜덤시간 활성화", variable=delay_random_var,
                        font=ctk.CTkFont(size=13)).pack(anchor="w", pady=(10, 5))

        delay_range_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        delay_range_frame.pack(anchor="w", pady=5)

        ctk.CTkLabel(delay_range_frame, text="±범위:", font=ctk.CTkFont(size=12)).pack(side="left")
        delay_range_entry = ctk.CTkEntry(delay_range_frame, width=100, height=32, font=ctk.CTkFont(size=13))
        delay_range_entry.insert(0, f"{getattr(rule, 'repeat_delay_random_range', 0.3):.2f}")
        delay_range_entry.pack(side="left", padx=(5, 10))
        ctk.CTkLabel(delay_range_frame, text="초", font=ctk.CTkFont(size=12),
                     text_color=COLORS["text_secondary"]).pack(side="left")

        result = {"saved": False}

        def save():
            try:
                count = int(count_entry.get().strip())
                delay = float(delay_entry.get().strip().replace(',', '.'))
                delay_range = float(delay_range_entry.get().strip().replace(',', '.'))
                if count < 1:
                    from tkinter import messagebox
                    messagebox.showerror("오류", "반복 횟수는 1 이상이어야 합니다")
                    return
                if delay < 0:
                    from tkinter import messagebox
                    messagebox.showerror("오류", "대기시간은 0 이상이어야 합니다")
                    return
                if delay_range < 0:
                    from tkinter import messagebox
                    messagebox.showerror("오류", "±범위는 0 이상이어야 합니다")
                    return
                rule.repeat_count = count
                rule.repeat_delay = delay
                rule.repeat_delay_random = delay_random_var.get()
                rule.repeat_delay_random_range = delay_range
                result["saved"] = True
                dialog.destroy()
            except ValueError:
                from tkinter import messagebox
                messagebox.showerror("오류", "숫자를 입력하세요")

        btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_frame.pack(pady=10)
        ctk.CTkButton(btn_frame, text="저장", width=100, height=36,
                      font=ctk.CTkFont(size=13, weight="bold"), command=save).pack(side="left", padx=8)
        ctk.CTkButton(btn_frame, text="취소", width=100, height=36,
                      font=ctk.CTkFont(size=13, weight="bold"), fg_color=COLORS["bg_card"],
                      command=dialog.destroy).pack(side="left", padx=8)

        dialog.wait_window()

        if result["saved"]:
            self._modified = True
            self._update_rule_buttons(rule)
            logger.info(f"반복 설정: {rule.repeat_count}회, 대기 {rule.repeat_delay}초 (랜덤: {rule.repeat_delay_random})")

    def _edit_rule_name(self, rule: AutomationRule):
        """규칙 이름 수정"""
        current_name = rule.description or ""
        dialog = ctk.CTkInputDialog(
            text=f"액션 이름을 입력하세요:\n현재: {current_name or '(없음)'}",
            title="액션 이름 수정",
        )
        result = dialog.get_input()

        if result is not None:  # 빈 문자열도 허용 (이름 삭제)
            rule.description = result.strip()
            self._modified = True
            self._refresh_action_list()
            logger.info(f"규칙 이름 수정: {rule.description}")

    def _change_rule_click_type(self, rule: AutomationRule, new_type: str):
        """클릭 유형 변경 (click, double_click, right_click)"""
        if rule.action_type == new_type:
            return  # 이미 같은 유형

        old_type = rule.action_type
        rule.action_type = new_type

        # 설명 자동 업데이트 (기존 설명이 클릭 유형 관련인 경우)
        type_names = {
            "click": "왼쪽 클릭",
            "double_click": "더블 클릭",
            "right_click": "오른쪽 클릭",
        }
        old_name = type_names.get(old_type, "")
        new_name = type_names.get(new_type, "")

        if rule.description and old_name and old_name in rule.description:
            rule.description = rule.description.replace(old_name, new_name)

        self._modified = True
        self._refresh_action_list()
        logger.info(f"클릭 유형 변경: {old_type} → {new_type}")

    def _stop_execution(self):
        """실행 중지"""
        if self._running_executor:
            logger.info("[부분실행] 중지 요청")
            try:
                self._running_executor.stop()
            except Exception as e:
                logger.error(f"[부분실행] 중지 오류: {e}")
            self._running_executor = None

        self._is_running = False
        self.title(f"계획 수정 - {self._plan.name}")
        self.configure(fg_color=COLORS["bg_dark"])
        self.update()

    def _test_run_rule(self, rule: AutomationRule):
        """해당 규칙부터 끝까지 실행 (토글 방식: 실행 중이면 중지)"""
        from tkinter import messagebox
        from ..player.rule_executor import get_rule_executor
        from ..analyzer.automation_models import AutomationPlan
        import threading

        # 이미 실행 중이면 중지
        if self._is_running:
            self._stop_execution()
            return

        # 모든 규칙을 평탄화 (자식 포함)
        def flatten_rules(rules):
            result = []
            for r in rules:
                result.append(r)
                if r.children:
                    result.extend(flatten_rules(r.children))
            return result

        all_rules_flat = flatten_rules(self._plan.initial_rules)

        # 클릭한 규칙의 인덱스 찾기 (평탄화된 리스트에서)
        try:
            rule_index = all_rules_flat.index(rule)
        except ValueError:
            # 리스트에 없으면 해당 규칙만 실행
            rules_to_run = [rule]
            rule_index = -1
        else:
            # 해당 인덱스부터 끝까지 모든 규칙 포함
            # executor가 다시 평탄화하므로 children을 비운 복사본 사용
            import copy
            rules_to_run = []
            for r in all_rules_flat[rule_index:]:
                r_copy = copy.copy(r)
                r_copy.children = []  # children 비움 (이미 평탄화됨)
                rules_to_run.append(r_copy)

        # 실행할 액션 개수 (이미 평탄화됨)
        remaining_count = len(rules_to_run)

        # 확인 메시지
        if not messagebox.askyesno(
            "실행 확인",
            f"'{rule.description or rule.action_type}'부터 끝까지 {remaining_count}개 액션을 실행합니다.\n\n계속하시겠습니까?"
        ):
            return

        # 수정된 내용이 있으면 저장 확인
        if self._modified:
            result = messagebox.askyesnocancel(
                "저장 확인",
                "수정된 내용이 있습니다.\n\n"
                "예: 저장 후 실행\n"
                "아니오: 저장 없이 실행\n"
                "취소: 실행 취소"
            )
            if result is None:  # 취소
                return
            if result:  # 예 - 저장 후 실행
                self._save_plan()

        logger.info(f"[부분실행] 준비: {rule.description or rule.action_type} ({remaining_count}개 액션)")

        # 부분 plan 생성
        partial_plan = AutomationPlan(
            name=f"{self._plan.name} (부분실행)",
            description=f"{rule.description or rule.action_type}",
            initial_rules=rules_to_run,
            monitoring_rules=[],
        )
        # goto 점프 시 원본 계획의 rules를 참조할 수 있도록 저장
        partial_plan._original_initial_rules = self._plan.initial_rules

        # 전역 executor 사용
        executor = get_rule_executor()
        self._running_executor = executor

        # 이미 실행 중이면 중지
        if executor.state.value in ["running_initial", "monitoring"]:
            executor.stop()
            import time
            time.sleep(0.3)

        # 실행 중 상태 표시
        self._is_running = True
        self.title("▶ 실행 중... (아무 ▶ 버튼 클릭시 중지)")
        self.configure(fg_color="#1a3a1a")  # 녹색 배경으로 변경
        self.update()

        # grab 해제 (다른 윈도우 조작 가능하게)
        try:
            self.grab_release()
        except tk.TclError:
            pass

        # 설정에 따라 메인 창 최소화 (pyautogui 간섭 방지)
        from ..utils.config import get_config
        config = get_config()
        main_window = self.winfo_toplevel().master  # 메인 윈도우 참조
        if config.ui.minimize_on_run and main_window:
            try:
                main_window.iconify()
                import time
                time.sleep(0.3)
            except (tk.TclError, KeyError, AttributeError):
                pass

        def on_complete(success, msg):
            logger.info(f"[부분실행] 완료: {msg}")
            # UI 스레드에서 상태 복원
            try:
                self.after(0, self._on_execution_complete)
                # 창 복원
                if config.ui.minimize_on_run and main_window:
                    self.after(100, lambda: main_window.deiconify())
            except (tk.TclError, KeyError, AttributeError):
                pass

        def on_error(msg, failed_rule):
            logger.error(f"[부분실행] 오류: {msg}")
            # 창 복원
            if config.ui.minimize_on_run and main_window:
                try:
                    self.after(100, lambda: main_window.deiconify())
                except tk.TclError:
                    pass

        executor.set_callbacks(on_complete=on_complete, on_error=on_error)

        # 별도 스레드에서 실행 시작
        def run():
            try:
                logger.info(f"[부분실행] 시작!")
                executor.execute_plan(partial_plan)
            except Exception as e:
                logger.error(f"[부분실행] 예외: {e}")
                import traceback
                logger.error(traceback.format_exc())

        threading.Thread(target=run, daemon=True).start()

    def _on_execution_complete(self):
        """실행 완료 후 UI 복원"""
        self._is_running = False
        self._running_executor = None
        self.title(f"계획 수정 - {self._plan.name}")
        self.configure(fg_color=COLORS["bg_dark"])

    def _toggle_skip_mode(self, rule: AutomationRule):
        """스킵 모드 토글 - 이미지 못찾으면 wait_after 후 다음 액션으로"""
        current = getattr(rule, 'skip_on_not_found', False)
        rule.skip_on_not_found = not current

        # 버튼 색상 업데이트
        if rule.rule_id in self._rule_widgets and "skip_btn" in self._rule_widgets[rule.rule_id]:
            btn = self._rule_widgets[rule.rule_id]["skip_btn"]
            is_skip = rule.skip_on_not_found
            btn.configure(
                fg_color="#2ecc71" if is_skip else COLORS["bg_card"],
                hover_color="#27ae60" if is_skip else COLORS["bg_card_hover"],
                text_color="white" if is_skip else COLORS["text_secondary"],
            )

        # 플랜 저장
        self._save_plan()

        status = "활성화" if rule.skip_on_not_found else "비활성화"
        logger.info(f"스킵 모드 {status}: {rule.description or rule.action_type}")

    def _edit_trigger_image(self, rule: AutomationRule):
        """트리거 이미지 설정 (이 이미지가 나타나면 액션 실행)"""
        from tkinter import filedialog
        import pyautogui

        dialog = ctk.CTkToplevel(self)
        dialog.title("트리거 이미지 설정")
        dialog.geometry("500x720")
        dialog.resizable(False, False)
        dialog.configure(fg_color=COLORS["bg_dark"])
        dialog.transient(self)
        dialog.grab_set()

        # 중앙 배치
        dialog.update_idletasks()
        x = (dialog.winfo_screenwidth() - 500) // 2
        y = (dialog.winfo_screenheight() - 500) // 2
        dialog.geometry(f"+{x}+{y}")

        main_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        main_frame.pack(fill="both", expand=True, padx=20, pady=15)

        # 설명
        ctk.CTkLabel(
            main_frame,
            text="이 이미지가 화면에 나타나면 액션을 실행합니다",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(anchor="w", pady=(0, 10))

        ctk.CTkLabel(
            main_frame,
            text="F8으로 캡쳐한 이미지 또는 파일을 선택하세요",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["text_secondary"],
        ).pack(anchor="w", pady=(0, 15))

        # 현재 트리거 이미지 표시
        preview_frame = ctk.CTkFrame(main_frame, fg_color=COLORS["bg_card"], corner_radius=8)
        preview_frame.pack(fill="x", pady=10)

        # 파일명 (크게 표시)
        filename_label = ctk.CTkLabel(
            preview_frame,
            text=Path(rule.trigger_image).name if rule.trigger_image else "(설정되지 않음)",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=COLORS["accent"],
        )
        filename_label.pack(pady=(10, 5))

        preview_label = ctk.CTkLabel(preview_frame, text="", width=200, height=150)
        preview_label.pack(pady=5)

        # 전체 경로 (작게 표시)
        path_label = ctk.CTkLabel(
            preview_frame,
            text=rule.trigger_image or "",
            font=ctk.CTkFont(size=9),
            text_color=COLORS["text_muted"],
            wraplength=460,
        )
        path_label.pack(pady=(0, 10))

        selected_path = {"value": rule.trigger_image}

        def update_preview(path):
            if path and Path(path).exists():
                try:
                    # 한글 경로 지원
                    img_arr = np.fromfile(path, np.uint8)
                    img = cv2.imdecode(img_arr, cv2.IMREAD_COLOR)
                    if img is not None:
                        img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                        h, w = img_rgb.shape[:2]
                        scale = min(180 / w, 130 / h)
                        new_w, new_h = int(w * scale), int(h * scale)
                        resized = cv2.resize(img_rgb, (new_w, new_h))
                        pil_img = Image.fromarray(resized)
                        ctk_img = ctk.CTkImage(light_image=pil_img, dark_image=pil_img, size=(new_w, new_h))
                        preview_label.configure(image=ctk_img, text="")
                        preview_label._ctk_image = ctk_img  # 참조 유지
                        filename_label.configure(text=Path(path).name)
                        path_label.configure(text=path)
                        return
                except Exception as e:
                    logger.warning(f"미리보기 로드 실패: {e}")
            preview_label.configure(image=None, text="미리보기 없음")
            filename_label.configure(text="(설정되지 않음)")
            path_label.configure(text=path or "")

        # 초기 미리보기
        update_preview(rule.trigger_image)

        # 버튼 영역
        btn_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        btn_frame.pack(fill="x", pady=15)

        def browse_trigger():
            # templates 폴더에서 선택
            templates_dir = DATA_DIR / "templates"
            templates_dir.mkdir(parents=True, exist_ok=True)
            path = filedialog.askopenfilename(
                title="트리거 이미지 선택",
                initialdir=str(templates_dir),
                filetypes=[("이미지 파일", "*.png *.jpg *.jpeg *.bmp")],
            )
            if path:
                selected_path["value"] = path
                update_preview(path)

        def clear_trigger():
            selected_path["value"] = None
            update_preview(None)

        ctk.CTkButton(
            btn_frame, text="이미지 선택", width=120, height=36,
            fg_color=COLORS["accent_blue"], hover_color="#2563eb",
            command=browse_trigger,
        ).pack(side="left", padx=(0, 5))

        ctk.CTkButton(
            btn_frame, text="해제", width=80, height=36,
            fg_color=COLORS["error"], hover_color="#c0392b",
            command=clear_trigger,
        ).pack(side="left", padx=5)

        def edit_crop():
            """선택된 이미지 크롭 편집"""
            if not selected_path["value"] or not Path(selected_path["value"]).exists():
                from tkinter import messagebox
                messagebox.showinfo("알림", "먼저 이미지를 선택하세요.")
                return

            def on_crop_done(new_path):
                selected_path["value"] = new_path
                update_preview(new_path)

            ImageCropDialog(
                dialog,
                selected_path["value"],
                on_crop=on_crop_done,
            )

        ctk.CTkButton(
            btn_frame, text="크롭/편집", width=100, height=36,
            fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
            command=edit_crop,
        ).pack(side="left", padx=5)

        # === 검색 영역 좌표 설정 ===
        coord_frame = ctk.CTkFrame(main_frame, fg_color=COLORS["bg_card"], corner_radius=8)
        coord_frame.pack(fill="x", pady=10)

        ctk.CTkLabel(
            coord_frame,
            text="검색 영역 좌표 (선택사항)",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(anchor="w", padx=10, pady=(10, 5))

        ctk.CTkLabel(
            coord_frame,
            text="좌표를 설정하면 해당 영역 근처에서만 트리거를 검색합니다",
            font=ctk.CTkFont(size=10),
            text_color=COLORS["text_muted"],
        ).pack(anchor="w", padx=10, pady=(0, 5))

        coord_input_frame = ctk.CTkFrame(coord_frame, fg_color="transparent")
        coord_input_frame.pack(fill="x", padx=10, pady=(0, 10))

        ctk.CTkLabel(coord_input_frame, text="X:", width=20).pack(side="left")
        trigger_x_entry = ctk.CTkEntry(coord_input_frame, width=80, height=30)
        trigger_x_entry.pack(side="left", padx=(0, 15))
        if rule.trigger_x is not None:
            trigger_x_entry.insert(0, str(rule.trigger_x))

        ctk.CTkLabel(coord_input_frame, text="Y:", width=20).pack(side="left")
        trigger_y_entry = ctk.CTkEntry(coord_input_frame, width=80, height=30)
        trigger_y_entry.pack(side="left", padx=(0, 15))
        if rule.trigger_y is not None:
            trigger_y_entry.insert(0, str(rule.trigger_y))

        def get_mouse_pos(event=None):
            """현재 마우스 위치 가져오기 (F9 단축키)"""
            dialog.withdraw()  # 다이얼로그 숨기기
            import time
            time.sleep(0.3)
            pos = pyautogui.position()
            trigger_x_entry.delete(0, "end")
            trigger_x_entry.insert(0, str(pos[0]))
            trigger_y_entry.delete(0, "end")
            trigger_y_entry.insert(0, str(pos[1]))
            dialog.deiconify()  # 다이얼로그 다시 표시
            dialog.focus_force()

        # F9 단축키 바인딩
        dialog.bind("<F9>", get_mouse_pos)

        def clear_coords():
            """좌표 초기화"""
            trigger_x_entry.delete(0, "end")
            trigger_y_entry.delete(0, "end")

        ctk.CTkButton(
            coord_input_frame, text="마우스 위치 (F9)", width=110, height=30,
            fg_color=COLORS["accent_blue"], hover_color="#2563eb",
            command=get_mouse_pos,
        ).pack(side="left", padx=(0, 5))

        ctk.CTkButton(
            coord_input_frame, text="초기화", width=60, height=30,
            fg_color=COLORS["bg_dark"], hover_color=COLORS["bg_card_hover"],
            command=clear_coords,
        ).pack(side="left")

        # === 신뢰도 설정 ===
        conf_frame = ctk.CTkFrame(main_frame, fg_color=COLORS["bg_card"], corner_radius=8)
        conf_frame.pack(fill="x", pady=10)

        ctk.CTkLabel(
            conf_frame,
            text="이미지 인식 신뢰도",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(anchor="w", padx=10, pady=(10, 5))

        ctk.CTkLabel(
            conf_frame,
            text="낮을수록 유연하게 인식, 높을수록 정확하게 인식 (기본값: 65%)",
            font=ctk.CTkFont(size=10),
            text_color=COLORS["text_muted"],
        ).pack(anchor="w", padx=10, pady=(0, 5))

        conf_input_frame = ctk.CTkFrame(conf_frame, fg_color="transparent")
        conf_input_frame.pack(fill="x", padx=10, pady=(0, 10))

        current_conf = getattr(rule, 'confidence', 0.65) or 0.65
        conf_var = ctk.DoubleVar(value=current_conf * 100)

        conf_slider = ctk.CTkSlider(
            conf_input_frame,
            from_=30,
            to=95,
            variable=conf_var,
            width=280,
            height=16,
            fg_color=COLORS["bg_dark"],
            progress_color=COLORS["accent"],
            button_color=COLORS["text_primary"],
            button_hover_color=COLORS["accent_hover"],
        )
        conf_slider.pack(side="left", padx=(0, 10))

        conf_label = ctk.CTkLabel(
            conf_input_frame,
            text=f"{int(conf_var.get())}%",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=COLORS["accent"],
            width=50,
        )
        conf_label.pack(side="left")

        def update_conf_label(*args):
            conf_label.configure(text=f"{int(conf_var.get())}%")

        conf_var.trace_add("write", update_conf_label)

        def save_confidence_only():
            rule.confidence = conf_var.get() / 100.0
            logger.info(f"트리거 이미지 신뢰도 저장: {int(conf_var.get())}%")
            self._modified = True
            from tkinter import messagebox
            messagebox.showinfo("저장 완료", f"신뢰도가 {int(conf_var.get())}%로 저장되었습니다.")

        ctk.CTkButton(
            conf_input_frame,
            text="신뢰도 저장",
            width=80,
            height=28,
            font=ctk.CTkFont(size=12),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=save_confidence_only,
        ).pack(side="left", padx=(10, 0))

        # 저장/취소 버튼
        bottom_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        bottom_frame.pack(pady=15)

        def save():
            rule.trigger_image = selected_path["value"]
            # 트리거 좌표 저장
            try:
                x_val = trigger_x_entry.get().strip()
                y_val = trigger_y_entry.get().strip()
                rule.trigger_x = int(x_val) if x_val else None
                rule.trigger_y = int(y_val) if y_val else None
            except ValueError:
                rule.trigger_x = None
                rule.trigger_y = None
            # 신뢰도 저장
            rule.confidence = conf_var.get() / 100.0
            logger.info(f"트리거 이미지 신뢰도 설정: {int(conf_var.get())}%")
            self._modified = True
            dialog.destroy()

        ctk.CTkButton(
            bottom_frame, text="저장", width=120, height=40,
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color=COLORS["success"], hover_color="#2ea44f",
            command=save,
        ).pack(side="left", padx=10)

        ctk.CTkButton(
            bottom_frame, text="취소", width=120, height=40,
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color=COLORS["bg_card"], hover_color=COLORS["bg_card_hover"],
            text_color=COLORS["text_secondary"],
            command=dialog.destroy,
        ).pack(side="left", padx=10)

        dialog.wait_window()
        self._refresh_action_list()

    def _edit_monitoring_mode(self, rule: AutomationRule):
        """모니터링 모드 설정"""
        from tkinter import filedialog, messagebox

        logger.info(f"[모니터링 편집] rule_id={rule.rule_id}, object_id={id(rule)}, 현재상태={rule.is_monitoring_mode}")

        dialog = ctk.CTkToplevel(self)
        dialog.title("모니터링 모드 설정")
        dialog.geometry("680x850")
        dialog.resizable(False, False)
        dialog.configure(fg_color=COLORS["bg_dark"])
        dialog.transient(self)
        dialog.grab_set()

        # 중앙 배치
        dialog.update_idletasks()
        x = (dialog.winfo_screenwidth() - 680) // 2
        y = (dialog.winfo_screenheight() - 850) // 2
        dialog.geometry(f"+{x}+{y}")

        main_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        main_frame.pack(fill="both", expand=True, padx=20, pady=15)

        # 모니터링 모드 활성화 체크박스
        is_monitoring_var = ctk.BooleanVar(value=getattr(rule, 'is_monitoring_mode', False))

        header_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        header_frame.pack(fill="x", pady=(0, 15))

        ctk.CTkCheckBox(
            header_frame,
            text="모니터링 모드 활성화",
            variable=is_monitoring_var,
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color="#2ecc71",
            fg_color="#2ecc71",
            hover_color="#27ae60",
        ).pack(side="left")

        # 설명
        ctk.CTkLabel(
            main_frame,
            text="이 액션의 타겟 이미지가 나타날 때까지 대기하면서,\n감시 이미지가 나타나면 지정된 액션으로 점프 후 복귀합니다.",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["text_secondary"],
            justify="left",
        ).pack(anchor="w", pady=(0, 15))

        # === 감시 목록 ===
        watch_frame = ctk.CTkFrame(main_frame, fg_color=COLORS["bg_card"], corner_radius=8)
        watch_frame.pack(fill="both", expand=True, pady=10)

        ctk.CTkLabel(
            watch_frame,
            text="감시 목록 (우클릭으로 모니터링 액션 붙여넣기)",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(anchor="w", padx=10, pady=(10, 5))

        # 부모 액션 목록 생성 (드롭다운용)
        action_options = []  # [(display_text, index), ...]
        action_names = {
            "click": "클릭", "double_click": "더블클릭", "right_click": "우클릭",
            "type": "입력", "hotkey": "단축키", "key_press": "키", "scroll": "스크롤", "drag": "드래그",
        }
        for i, r in enumerate(self._plan.initial_rules):
            action_type = action_names.get(r.action_type, r.action_type or "동작")
            desc = r.description[:15] + "..." if r.description and len(r.description) > 15 else (r.description or "")
            children_count = f" (+{len(r.children)})" if r.children else ""
            display_text = f"{i + 1}. {action_type}{children_count}"
            if desc:
                display_text += f" - {desc}"
            action_options.append((display_text, i))

        # 감시 목록 스크롤 영역
        watch_scroll = ctk.CTkScrollableFrame(watch_frame, fg_color="transparent", height=420)
        watch_scroll.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        # 현재 감시 목록 데이터 (내부 인덱스는 0부터)
        watches_data = []
        for w in getattr(rule, 'monitoring_watches', []):
            # monitor_action (단수) → monitor_actions (복수) 마이그레이션
            monitor_actions = w.get("monitor_actions", [])
            if not monitor_actions and w.get("monitor_action"):
                monitor_actions = [w.get("monitor_action")]
            watches_data.append({
                "image": w.get("image"),
                "goto_index": w.get("goto_index", 0),  # 내부 인덱스 (0부터)
                "search_region": w.get("search_region"),  # [x1, y1, x2, y2] 또는 None
                "monitor_actions": monitor_actions,  # 모니터링 액션 리스트
                "confidence": w.get("confidence", 0.65),  # 감시 이미지별 인식률
            })

        watch_widgets = []
        watch_row3_containers = {}  # {idx: row3_container} - 모니터링 액션 영역 참조
        watch_collapsed = {}  # {idx: bool} - 접힌 상태 저장 (기본: 접힘)

        def get_action_display_short(action):
            """모니터링 액션 표시 텍스트 반환"""
            if not action or not action.get("type"):
                return "?", COLORS["text_muted"]
            t = action.get("type")
            if t == "텍스트 입력":
                txt = action.get("text", "")[:10]
                return f"텍스트:{txt}{'...' if len(action.get('text', '')) > 10 else ''}", COLORS["success"]
            elif t == "키 입력":
                keys = action.get("keys", [])
                return f"키:{'+'.join(keys)}", COLORS["accent_orange"]
            elif t == "마우스 클릭":
                return f"클릭:({action.get('x')},{action.get('y')})", COLORS["accent_blue"]
            elif t == "이미지 클릭":
                img = Path(action.get("image", "")).name[:8] if action.get("image") else ""
                return f"이미지:{img}", "#b48ead"
            return "?", COLORS["text_muted"]

        def refresh_monitor_actions(watch_idx):
            """특정 watch의 모니터링 액션 영역만 업데이트 (전체 새로고침 없이)"""
            if watch_idx not in watch_row3_containers:
                return

            row3 = watch_row3_containers[watch_idx]
            # 기존 내용 삭제
            for child in row3.winfo_children():
                child.destroy()

            current_actions = watches_data[watch_idx].get("monitor_actions", [])
            if current_actions:
                # 헤더
                header_frame = ctk.CTkFrame(row3, fg_color="transparent")
                header_frame.pack(fill="x", padx=(30, 0), pady=(5, 5))

                ctk.CTkLabel(
                    header_frame, text=f"모니터링 액션 ({len(current_actions)}개)",
                    font=ctk.CTkFont(size=11, weight="bold"),
                    text_color="#2ecc71",
                ).pack(side="left")

                # 각 모니터링 액션을 카드 형태로 표시
                for ai, action in enumerate(current_actions):
                    ma_type = action.get("type", "알 수 없음")
                    ma_detail = ""

                    # 타입별 색상
                    type_colors = {
                        "텍스트 입력": COLORS["success"],
                        "키 입력": COLORS["accent_orange"],
                        "마우스 클릭": COLORS["accent_blue"],
                        "이미지 클릭": "#b48ead",
                        "스크롤": "#b48ead",
                        "드래그": COLORS["warning"],
                    }
                    ma_color = type_colors.get(ma_type, COLORS["text_muted"])

                    # 상세 정보 생성
                    click_type = action.get("click_type", "click")
                    click_suffix = {"double_click": " [더블]", "right_click": " [우클릭]"}.get(click_type, "")

                    if ma_type == "텍스트 입력":
                        text = action.get("text", "")
                        ma_detail = f'"{text[:20]}..."' if len(text) > 20 else f'"{text}"'
                    elif ma_type == "키 입력":
                        keys = action.get("keys", [])
                        ma_detail = f"[{' + '.join(keys).upper()}]"
                    elif ma_type == "마우스 클릭":
                        ma_detail = f"({action.get('x', 0)}, {action.get('y', 0)}){click_suffix}"
                    elif ma_type == "이미지 클릭":
                        img = action.get("image", "")
                        ma_detail = (Path(img).name[:15] if img else "") + click_suffix
                    elif ma_type == "스크롤":
                        ma_detail = f"{action.get('amount', 0)}"
                    elif ma_type == "드래그":
                        ma_detail = f"({action.get('from_x', 0)},{action.get('from_y', 0)})→({action.get('to_x', 0)},{action.get('to_y', 0)})"

                    # 액션 카드
                    action_card = ctk.CTkFrame(row3, fg_color="#1a3d2e", corner_radius=6)
                    action_card.pack(fill="x", padx=(30, 0), pady=2)

                    card_inner = ctk.CTkFrame(action_card, fg_color="transparent")
                    card_inner.pack(fill="x", padx=10, pady=6)

                    # 번호
                    ctk.CTkLabel(
                        card_inner, text=f"{ai + 1}.",
                        font=ctk.CTkFont(size=12, weight="bold"),
                        text_color="#2ecc71", width=24,
                    ).pack(side="left")

                    # 타입
                    ctk.CTkLabel(
                        card_inner, text=ma_type,
                        font=ctk.CTkFont(size=11, weight="bold"), text_color=ma_color,
                    ).pack(side="left")

                    # 상세 정보
                    if ma_detail:
                        ctk.CTkLabel(
                            card_inner, text=f"  {ma_detail}",
                            font=ctk.CTkFont(size=10), text_color=COLORS["text_secondary"],
                        ).pack(side="left")

                    # === 버튼 영역 (오른쪽) ===
                    # 삭제 버튼
                    def delete_action(i=watch_idx, a=ai):
                        if i < len(watches_data) and "monitor_actions" in watches_data[i]:
                            actions = watches_data[i]["monitor_actions"]
                            if 0 <= a < len(actions):
                                actions.pop(a)
                                refresh_monitor_actions(i)

                    ctk.CTkButton(
                        card_inner, text="✕", width=26, height=22,
                        font=ctk.CTkFont(size=10),
                        fg_color="#c0392b", hover_color="#e74c3c",
                        text_color="white", corner_radius=4,
                        command=delete_action,
                    ).pack(side="right", padx=(2, 0))

                    # 위/아래 이동 버튼
                    move_frame = ctk.CTkFrame(card_inner, fg_color="#0d1f17", corner_radius=4)
                    move_frame.pack(side="right", padx=(2, 0))

                    def move_up(i=watch_idx, a=ai):
                        if i < len(watches_data) and "monitor_actions" in watches_data[i]:
                            actions = watches_data[i]["monitor_actions"]
                            if 0 < a < len(actions):
                                actions[a], actions[a-1] = actions[a-1], actions[a]
                                refresh_monitor_actions(i)

                    def move_down(i=watch_idx, a=ai):
                        if i < len(watches_data) and "monitor_actions" in watches_data[i]:
                            actions = watches_data[i]["monitor_actions"]
                            if 0 <= a < len(actions) - 1:
                                actions[a], actions[a+1] = actions[a+1], actions[a]
                                refresh_monitor_actions(i)

                    ctk.CTkButton(
                        move_frame, text="▲", width=22, height=18,
                        font=ctk.CTkFont(size=10),
                        fg_color="transparent", hover_color=COLORS["accent_blue"],
                        text_color=COLORS["text_secondary"], corner_radius=2,
                        command=move_up,
                    ).pack(side="top")

                    ctk.CTkButton(
                        move_frame, text="▼", width=22, height=18,
                        font=ctk.CTkFont(size=10),
                        fg_color="transparent", hover_color=COLORS["accent_blue"],
                        text_color=COLORS["text_secondary"], corner_radius=2,
                        command=move_down,
                    ).pack(side="top")

                    # 반복 횟수 버튼
                    repeat_count = action.get("repeat_count", 1)

                    def edit_repeat(i=watch_idx, a=ai):
                        if i >= len(watches_data) or "monitor_actions" not in watches_data[i]:
                            return
                        actions = watches_data[i]["monitor_actions"]
                        if a < 0 or a >= len(actions):
                            return
                        act = actions[a]
                        cur = act.get("repeat_count", 1)
                        dlg = ctk.CTkInputDialog(text=f"반복 횟수 (현재: {cur}):", title="반복 횟수")
                        val = dlg.get_input()
                        if val and val.isdigit():
                            act["repeat_count"] = max(1, int(val))
                            refresh_monitor_actions(i)

                    ctk.CTkButton(
                        card_inner, text=f"x{repeat_count}", width=32, height=22,
                        font=ctk.CTkFont(size=10),
                        fg_color=COLORS["accent_blue"] if repeat_count > 1 else "#0d1f17",
                        hover_color="#2563eb",
                        text_color="white" if repeat_count > 1 else COLORS["text_secondary"],
                        corner_radius=4,
                        command=edit_repeat,
                    ).pack(side="right", padx=(2, 0))

                    # 대기시간 버튼
                    wait_after = action.get("wait_after", 0.5)
                    wait_random = action.get("wait_random", False)

                    def edit_wait(i=watch_idx, a=ai):
                        if i >= len(watches_data) or "monitor_actions" not in watches_data[i]:
                            return
                        actions = watches_data[i]["monitor_actions"]
                        if a < 0 or a >= len(actions):
                            return
                        act = actions[a]
                        # 대기시간 설정 다이얼로그
                        wait_dlg = ctk.CTkToplevel(dialog)
                        wait_dlg.title("대기시간 설정")
                        wait_dlg.geometry("300x280")
                        wait_dlg.transient(dialog)
                        wait_dlg.grab_set()
                        wait_dlg.configure(fg_color=COLORS["bg_dark"])

                        cur_wait = act.get("wait_after", 0.5)
                        cur_random = act.get("wait_random", False)
                        cur_range = act.get("wait_random_range", 0.3)

                        ctk.CTkLabel(wait_dlg, text="대기시간 (초):", font=ctk.CTkFont(size=12)).pack(pady=(15, 5))
                        wait_entry = ctk.CTkEntry(wait_dlg, width=100)
                        wait_entry.insert(0, str(cur_wait))
                        wait_entry.pack()

                        random_var = ctk.BooleanVar(value=cur_random)
                        ctk.CTkCheckBox(wait_dlg, text="랜덤 적용", variable=random_var).pack(pady=10)

                        ctk.CTkLabel(wait_dlg, text="랜덤 범위 (±초):", font=ctk.CTkFont(size=12)).pack(pady=(5, 5))
                        range_entry = ctk.CTkEntry(wait_dlg, width=100)
                        range_entry.insert(0, str(cur_range))
                        range_entry.pack()

                        def save_wait():
                            try:
                                act["wait_after"] = float(wait_entry.get())
                                act["wait_random"] = random_var.get()
                                act["wait_random_range"] = float(range_entry.get())
                                refresh_monitor_actions(i)
                                wait_dlg.destroy()
                            except ValueError:
                                pass

                        # 버튼 프레임
                        btn_frame = ctk.CTkFrame(wait_dlg, fg_color="transparent")
                        btn_frame.pack(pady=20)

                        ctk.CTkButton(
                            btn_frame, text="저장", width=100, height=36,
                            font=ctk.CTkFont(size=13, weight="bold"),
                            fg_color=COLORS["accent_blue"], hover_color="#2563eb",
                            command=save_wait
                        ).pack(side="left", padx=10)

                        ctk.CTkButton(
                            btn_frame, text="취소", width=100, height=36,
                            font=ctk.CTkFont(size=13, weight="bold"),
                            fg_color=COLORS["bg_card"], hover_color=COLORS["bg_card_hover"],
                            text_color=COLORS["text_secondary"],
                            command=wait_dlg.destroy
                        ).pack(side="left", padx=10)

                    ctk.CTkButton(
                        card_inner, text=f"{wait_after:.1f}s{'*' if wait_random else ''}", width=42, height=22,
                        font=ctk.CTkFont(size=10),
                        fg_color=COLORS["success"] if wait_random else "#0d1f17",
                        hover_color="#27ae60",
                        text_color="white" if wait_random else COLORS["text_secondary"],
                        corner_radius=4,
                        command=edit_wait,
                    ).pack(side="right", padx=(2, 0))

                    # 이미지 클릭일 때만 범위 설정 버튼 표시
                    if ma_type == "이미지 클릭":
                        search_region = action.get("search_region")
                        has_region = search_region is not None

                        def edit_region(i=watch_idx, a=ai):
                            act = watches_data[i]["monitor_actions"][a]
                            # 범위 설정 다이얼로그
                            region_dlg = ctk.CTkToplevel(dialog)
                            region_dlg.title("검색 범위 설정")
                            region_dlg.geometry("350x180")
                            region_dlg.transient(dialog)
                            region_dlg.grab_set()
                            region_dlg.configure(fg_color=COLORS["bg_dark"])

                            cur_region = act.get("search_region")

                            ctk.CTkLabel(
                                region_dlg, text="이미지 검색 범위 설정",
                                font=ctk.CTkFont(size=14, weight="bold")
                            ).pack(pady=(15, 10))

                            region_text = f"현재: ({cur_region[0]}, {cur_region[1]}) ~ ({cur_region[2]}, {cur_region[3]})" if cur_region else "현재: 전체화면"
                            region_label = ctk.CTkLabel(region_dlg, text=region_text, font=ctk.CTkFont(size=11))
                            region_label.pack(pady=5)

                            btn_frame = ctk.CTkFrame(region_dlg, fg_color="transparent")
                            btn_frame.pack(pady=15)

                            def select_region_drag():
                                region_dlg.withdraw()
                                dialog.withdraw()

                                def on_region_select(x1, y1, x2, y2):
                                    act["search_region"] = [x1, y1, x2, y2]
                                    refresh_monitor_actions(i)
                                    dialog.deiconify()
                                    dialog.grab_set()
                                    region_dlg.destroy()

                                def on_cancel():
                                    dialog.deiconify()
                                    dialog.grab_set()
                                    region_dlg.deiconify()
                                    region_dlg.grab_set()

                                from src.ui.analyzer_view import ScreenRegionSelector
                                self.after(100, lambda: ScreenRegionSelector(
                                    self, on_region_select, on_cancel
                                ))

                            ctk.CTkButton(
                                btn_frame, text="드래그로 지정", width=100, height=32,
                                fg_color=COLORS["accent_blue"], hover_color="#2563eb",
                                command=select_region_drag,
                            ).pack(side="left", padx=5)

                            def clear_region():
                                act["search_region"] = None
                                refresh_monitor_actions(i)
                                region_dlg.destroy()

                            ctk.CTkButton(
                                btn_frame, text="전체화면", width=80, height=32,
                                fg_color=COLORS["bg_card"], hover_color=COLORS["bg_card_hover"],
                                text_color=COLORS["text_secondary"],
                                command=clear_region,
                            ).pack(side="left", padx=5)

                            ctk.CTkButton(
                                btn_frame, text="닫기", width=60, height=32,
                                fg_color=COLORS["bg_card"], hover_color=COLORS["bg_card_hover"],
                                text_color=COLORS["text_secondary"],
                                command=region_dlg.destroy,
                            ).pack(side="left", padx=5)

                        ctk.CTkButton(
                            card_inner, text="범위" if not has_region else "범위✓", width=38, height=22,
                            font=ctk.CTkFont(size=9),
                            fg_color="#b48ead" if has_region else "#0d1f17",
                            hover_color="#c9a0c9",
                            text_color="white" if has_region else COLORS["text_secondary"],
                            corner_radius=4,
                            command=edit_region,
                        ).pack(side="right", padx=(2, 0))

        def load_thumbnail(path, size=(40, 40)):
            """이미지 썸네일 로드 (한글 경로 지원)"""
            if path and Path(path).exists():
                try:
                    img_arr = np.fromfile(path, np.uint8)
                    img = cv2.imdecode(img_arr, cv2.IMREAD_COLOR)
                    if img is not None:
                        img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                        h, w = img_rgb.shape[:2]
                        scale = min(size[0] / w, size[1] / h)
                        new_w, new_h = int(w * scale), int(h * scale)
                        resized = cv2.resize(img_rgb, (new_w, new_h))
                        pil_img = Image.fromarray(resized)
                        return ctk.CTkImage(light_image=pil_img, dark_image=pil_img, size=(new_w, new_h))
                except Exception:
                    pass
            return None

        def refresh_watch_list():
            """감시 목록 UI 갱신"""
            import pyautogui

            for w in watch_widgets:
                w.destroy()
            watch_widgets.clear()
            watch_row3_containers.clear()

            for idx, watch in enumerate(watches_data):
                # 기본값: 새 항목은 펼침, 기존 항목은 상태 유지
                if idx not in watch_collapsed:
                    watch_collapsed[idx] = True if watch.get("image") else False  # 이미지 있으면 접힘

                is_collapsed = watch_collapsed.get(idx, True)

                item_frame = ctk.CTkFrame(watch_scroll, fg_color=COLORS["bg_dark"], corner_radius=8)
                item_frame.pack(fill="x", pady=3)
                watch_widgets.append(item_frame)

                # 헤더 줄: 접기/펼치기 버튼 + 번호 + 썸네일 + 요약 + 삭제
                header_row = ctk.CTkFrame(item_frame, fg_color="transparent")
                header_row.pack(fill="x", padx=8, pady=(6, 3))

                # 접기/펼치기 버튼
                def toggle_collapse(i=idx):
                    watch_collapsed[i] = not watch_collapsed.get(i, True)
                    refresh_watch_list()

                toggle_btn = ctk.CTkButton(
                    header_row, text="▶" if is_collapsed else "▼", width=24, height=24,
                    font=ctk.CTkFont(size=10),
                    fg_color="transparent", hover_color=COLORS["bg_card"],
                    text_color=COLORS["text_muted"],
                    command=toggle_collapse,
                )
                toggle_btn.pack(side="left", padx=(0, 4))

                # 번호
                ctk.CTkLabel(
                    header_row, text=f"{idx + 1}.",
                    font=ctk.CTkFont(size=13, weight="bold"),
                    text_color=COLORS["text_primary"],
                    width=22,
                ).pack(side="left")

                # 이미지 썸네일 미리보기 (작은 크기)
                thumb_frame = ctk.CTkFrame(header_row, fg_color=COLORS["bg_card"], width=32, height=32, corner_radius=4)
                thumb_frame.pack(side="left", padx=(4, 8))
                thumb_frame.pack_propagate(False)

                thumb_label = ctk.CTkLabel(thumb_frame, text="", width=28, height=28)
                thumb_label.pack(expand=True)

                thumb_img = load_thumbnail(watch["image"], size=(28, 28))
                if thumb_img:
                    thumb_label.configure(image=thumb_img)
                    thumb_label._thumb_img = thumb_img
                else:
                    thumb_label.configure(text="?", font=ctk.CTkFont(size=12), text_color=COLORS["text_muted"])

                # 요약 정보 표시 (보기 쉽게)
                monitor_actions = watch.get("monitor_actions", [])
                current_goto = watch.get("goto_index", 0)

                # 흐름: 감시 - 모니터링액션실행 - 지정액션점프 - 복귀
                parts = ["감시"]
                if len(monitor_actions) > 0:
                    parts.append(f"액션{len(monitor_actions)}개 실행")
                if current_goto >= 0 and current_goto < len(action_options):
                    parts.append(f"액션{current_goto + 1} 점프")
                    parts.append("복귀")

                summary_text = " - ".join(parts)

                ctk.CTkLabel(
                    header_row, text=summary_text,
                    font=ctk.CTkFont(size=11),
                    text_color=COLORS["text_secondary"],
                    anchor="w",
                ).pack(side="left", fill="x", expand=True)

                # 삭제 버튼
                def delete_watch(i=idx):
                    watches_data.pop(i)
                    # 인덱스 재정렬
                    new_collapsed = {}
                    for k, v in watch_collapsed.items():
                        if k < i:
                            new_collapsed[k] = v
                        elif k > i:
                            new_collapsed[k - 1] = v
                    watch_collapsed.clear()
                    watch_collapsed.update(new_collapsed)
                    refresh_watch_list()

                ctk.CTkButton(
                    header_row, text="✕", width=28, height=24,
                    font=ctk.CTkFont(size=11),
                    fg_color=COLORS["error"], hover_color="#c0392b",
                    command=delete_watch,
                ).pack(side="right")

                # === 상세 설정 (펼쳤을 때만 표시) ===
                if not is_collapsed:
                    detail_frame = ctk.CTkFrame(item_frame, fg_color="transparent")
                    detail_frame.pack(fill="x", padx=12, pady=(0, 8))

                    # 첫 줄: 이미지 선택 + goto 드롭다운
                    row1 = ctk.CTkFrame(detail_frame, fg_color="transparent")
                    row1.pack(fill="x", pady=(0, 5))

                    # 이미지 선택 버튼
                    def select_watch_image(i=idx):
                        templates_dir = DATA_DIR / "templates"
                        templates_dir.mkdir(parents=True, exist_ok=True)
                        path = filedialog.askopenfilename(
                            title="감시 이미지 선택",
                            initialdir=str(templates_dir),
                            filetypes=[("이미지 파일", "*.png *.jpg *.jpeg *.bmp")],
                        )
                        if path:
                            watches_data[i]["image"] = path
                            refresh_watch_list()

                    ctk.CTkLabel(
                        row1, text="이미지:",
                        font=ctk.CTkFont(size=11),
                        text_color=COLORS["text_muted"],
                    ).pack(side="left", padx=(0, 5))

                    img_name = Path(watch["image"]).name[:20] + "..." if watch.get("image") and len(Path(watch["image"]).name) > 20 else (Path(watch["image"]).name if watch.get("image") else "없음")
                    ctk.CTkLabel(
                        row1, text=img_name,
                        font=ctk.CTkFont(size=11),
                        text_color=COLORS["accent"] if watch.get("image") else COLORS["text_muted"],
                    ).pack(side="left", padx=(0, 8))

                    ctk.CTkButton(
                        row1, text="선택", width=45, height=24,
                        font=ctk.CTkFont(size=11),
                        fg_color=COLORS["accent_blue"], hover_color="#2563eb",
                        command=select_watch_image,
                    ).pack(side="left", padx=(0, 15))

                    # 점프할 액션 선택 (드롭다운)
                    ctk.CTkLabel(
                        row1, text="→",
                        font=ctk.CTkFont(size=12, weight="bold"),
                        text_color=COLORS["text_secondary"],
                    ).pack(side="left", padx=(0, 5))

                    option_texts = [opt[0] for opt in action_options]
                    current_display = action_options[current_goto][0] if 0 <= current_goto < len(action_options) else ""

                    goto_combo = ctk.CTkComboBox(
                        row1,
                        values=option_texts,
                        width=180,
                        height=26,
                        font=ctk.CTkFont(size=11),
                        dropdown_font=ctk.CTkFont(size=10),
                        state="readonly",
                    )
                    goto_combo.pack(side="left")
                    if current_display:
                        goto_combo.set(current_display)

                    def on_goto_select(choice, i=idx):
                        for opt_text, opt_idx in action_options:
                            if opt_text == choice:
                                watches_data[i]["goto_index"] = opt_idx
                                break

                    goto_combo.configure(command=lambda choice, i=idx: on_goto_select(choice, i))

                    # 두 번째 줄: 검색 범위 설정
                    row2 = ctk.CTkFrame(detail_frame, fg_color="transparent")
                    row2.pack(fill="x", pady=(0, 5))

                    ctk.CTkLabel(
                        row2, text="검색범위:",
                        font=ctk.CTkFont(size=11),
                        text_color=COLORS["text_muted"],
                    ).pack(side="left", padx=(0, 5))

                    region = watch.get("search_region")
                    region_text = f"({region[0]}, {region[1]}) ~ ({region[2]}, {region[3]})" if region else "전체화면"

                    region_label = ctk.CTkLabel(
                        row2, text=region_text,
                        font=ctk.CTkFont(size=11),
                        text_color=COLORS["accent"] if region else COLORS["text_secondary"],
                        width=150,
                        anchor="w",
                    )
                    region_label.pack(side="left", padx=(0, 8))

                    def select_region(i=idx, rlbl=region_label):
                        dialog.withdraw()
                        self.after(100, lambda: self._open_region_selector(i, rlbl, watches_data, dialog))

                    ctk.CTkButton(
                        row2, text="범위 지정", width=65, height=24,
                        font=ctk.CTkFont(size=10),
                        fg_color=COLORS["accent_blue"], hover_color="#2563eb",
                        command=select_region,
                    ).pack(side="left", padx=(0, 5))

                    def clear_region(i=idx, rlbl=region_label):
                        watches_data[i]["search_region"] = None
                        rlbl.configure(text="전체화면", text_color=COLORS["text_secondary"])

                    ctk.CTkButton(
                        row2, text="초기화", width=45, height=24,
                        font=ctk.CTkFont(size=10),
                        fg_color=COLORS["bg_card"], hover_color=COLORS["bg_card_hover"],
                        text_color=COLORS["text_secondary"],
                        command=clear_region,
                    ).pack(side="left")

                    # 인식률 설정 줄
                    row_conf = ctk.CTkFrame(detail_frame, fg_color="transparent")
                    row_conf.pack(fill="x", pady=(0, 5))

                    ctk.CTkLabel(
                        row_conf, text="인식률:",
                        font=ctk.CTkFont(size=11),
                        text_color=COLORS["text_muted"],
                    ).pack(side="left", padx=(0, 5))

                    watch_conf = watch.get("confidence", 0.65)
                    conf_label = ctk.CTkLabel(
                        row_conf, text=f"{int(watch_conf * 100)}%",
                        font=ctk.CTkFont(size=11),
                        text_color=COLORS["accent"],
                        width=35,
                    )
                    conf_label.pack(side="left", padx=(0, 5))

                    def on_conf_change(val, i=idx, lbl=conf_label):
                        watches_data[i]["confidence"] = float(val)
                        lbl.configure(text=f"{int(float(val) * 100)}%")

                    conf_slider = ctk.CTkSlider(
                        row_conf, from_=0.3, to=1.0, width=120, height=16,
                        number_of_steps=70,
                        button_color=COLORS["accent"],
                        button_hover_color=COLORS["accent_hover"],
                        progress_color=COLORS["accent"],
                        command=on_conf_change,
                    )
                    conf_slider.set(watch_conf)
                    conf_slider.pack(side="left")

                    # 세 번째 줄: 모니터링 액션 목록
                    row3 = ctk.CTkFrame(detail_frame, fg_color="transparent")
                    row3.pack(fill="x", pady=(0, 5))
                    watch_row3_containers[idx] = row3
                    refresh_monitor_actions(idx)

                # 우클릭 메뉴 (모니터링 액션 붙여넣기)
                def show_context_menu(event, i=idx):
                    import tkinter as tk
                    menu = tk.Menu(item_frame, tearoff=0)

                    def paste_monitor_action():
                        clipboard = get_action_clipboard()
                        if clipboard is None:
                            messagebox.showinfo("클립보드 비어있음", "먼저 계획수정에서 액션을 복사하세요.")
                            return

                        def convert_to_monitor_action(act):
                            """액션을 monitor_action 형식으로 변환 (모든 속성 포함)"""
                            action_type = act.action_type
                            ma = None
                            if action_type == "type":
                                ma = {"type": "텍스트 입력", "text": act.action_text or ""}
                            elif action_type in ["hotkey", "key_press"]:
                                ma = {"type": "키 입력", "keys": act.action_keys or []}
                            elif action_type in ["click", "double_click", "right_click"]:
                                if getattr(act, 'target_image', None):
                                    ma = {"type": "이미지 클릭", "image": act.target_image, "click_type": action_type}
                                else:
                                    ma = {"type": "마우스 클릭", "x": act.action_x, "y": act.action_y, "click_type": action_type}
                            elif action_type == "scroll":
                                ma = {"type": "스크롤", "amount": getattr(act, 'scroll_amount', 0)}
                            elif action_type == "drag":
                                ma = {"type": "드래그", "from_x": act.action_x, "from_y": act.action_y,
                                      "to_x": getattr(act, 'drag_to_x', 0), "to_y": getattr(act, 'drag_to_y', 0)}
                            elif getattr(act, 'target_image', None):
                                ma = {"type": "이미지 클릭", "image": act.target_image}
                            else:
                                ma = {"type": "마우스 클릭", "x": act.action_x or 0, "y": act.action_y or 0}

                            # 공통 속성 복사
                            if ma:
                                ma["wait_after"] = getattr(act, 'wait_after', 0.5)
                                ma["wait_random"] = getattr(act, 'wait_random', False)
                                ma["wait_random_range"] = getattr(act, 'wait_random_range', 0.3)
                                ma["repeat_count"] = getattr(act, 'repeat_count', 1)
                                ma["repeat_delay"] = getattr(act, 'repeat_delay', 0.5)
                                ma["repeat_delay_random"] = getattr(act, 'repeat_delay_random', False)
                                ma["repeat_delay_random_range"] = getattr(act, 'repeat_delay_random_range', 0.3)
                                ma["typing_random"] = getattr(act, 'typing_random', False)
                                ma["typing_delay"] = getattr(act, 'typing_delay', 0.1)
                                ma["typing_delay_range"] = getattr(act, 'typing_delay_range', 0.05)
                                ma["confidence"] = getattr(act, 'confidence', 0.65)
                                ma["search_radius"] = getattr(act, 'search_radius', 0)
                            return ma

                        def collect_all_actions(act):
                            """액션과 모든 자식 액션을 수집"""
                            result = [act]
                            for child in getattr(act, 'children', []) or []:
                                result.extend(collect_all_actions(child))
                            return result

                        # 클립보드 액션과 모든 자식 수집
                        all_actions = collect_all_actions(clipboard)
                        monitor_actions = []
                        for act in all_actions:
                            ma = convert_to_monitor_action(act)
                            if ma:
                                monitor_actions.append(ma)

                        if monitor_actions:
                            if "monitor_actions" not in watches_data[i]:
                                watches_data[i]["monitor_actions"] = []
                            watches_data[i]["monitor_actions"].extend(monitor_actions)
                            # 접힌 상태면 펼치고 새로고침
                            if watch_collapsed.get(i, True):
                                watch_collapsed[i] = False
                                refresh_watch_list()
                            else:
                                refresh_monitor_actions(i)
                        else:
                            messagebox.showwarning("변환 실패", "이 액션은 모니터링 액션으로 변환할 수 없습니다.")

                    def clear_all_actions():
                        watches_data[i]["monitor_actions"] = []
                        # 접힌 상태면 요약 정보 업데이트 필요
                        refresh_watch_list()

                    menu.add_command(label="모니터링 액션 붙여넣기", command=paste_monitor_action)
                    if watches_data[i].get("monitor_actions"):
                        menu.add_separator()
                        menu.add_command(label="모니터링 액션 전체 삭제", command=clear_all_actions)

                    menu.tk_popup(event.x_root, event.y_root)

                item_frame.bind("<Button-3>", show_context_menu)
                for child in item_frame.winfo_children():
                    child.bind("<Button-3>", lambda e, i=idx: show_context_menu(e, i))

        refresh_watch_list()

        # 감시 추가 버튼
        def add_watch():
            new_idx = len(watches_data)
            watches_data.append({"image": None, "goto_index": 0, "search_region": None, "monitor_actions": [], "confidence": 0.65})
            watch_collapsed[new_idx] = False  # 새 항목은 펼침 상태
            refresh_watch_list()

        ctk.CTkButton(
            watch_frame, text="+ 감시 추가", width=100, height=30,
            fg_color=COLORS["success"], hover_color="#2ea44f",
            command=add_watch,
        ).pack(pady=(0, 10))

        # 설명 추가
        ctk.CTkLabel(
            main_frame,
            text="※ 선택한 액션에 하위 액션이 있으면 함께 실행됩니다",
            font=ctk.CTkFont(size=10),
            text_color=COLORS["text_muted"],
        ).pack(anchor="w", pady=(5, 0))

        # 저장/취소 버튼
        bottom_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        bottom_frame.pack(pady=15)

        def save():
            # 유효한 감시 항목만 저장 (이미지가 있는 것만)
            valid_watches = []
            total_monitor_actions = 0
            for w in watches_data:
                if w.get("image"):
                    monitor_actions = w.get("monitor_actions", [])
                    total_monitor_actions += len(monitor_actions)
                    valid_watches.append({
                        "image": w["image"],
                        "goto_index": w.get("goto_index", 0),  # 이미 0-based 인덱스
                        "search_region": w.get("search_region"),  # [x1, y1, x2, y2] 또는 None
                        "monitor_actions": monitor_actions,  # 모니터링 액션 리스트
                        "confidence": w.get("confidence", 0.65),  # 감시 이미지별 인식률
                    })

            # 모니터링 모드 활성화 조건 (엄격하게 적용):
            # 체크박스 ON + 감시 이미지 있음 + 모니터링 액션 1개 이상 - 모두 충족해야 활성화
            has_checkbox = is_monitoring_var.get()
            has_watches = len(valid_watches) > 0
            has_actions = total_monitor_actions > 0

            logger.info(f"[모니터링 저장] checkbox={has_checkbox}, watches={len(valid_watches)}, actions={total_monitor_actions}")

            # 먼저 무조건 비활성화 상태로 설정
            rule.is_monitoring_mode = False
            rule.monitoring_watches = []

            # 모든 조건 충족 시에만 활성화
            if has_checkbox and has_watches and has_actions:
                rule.is_monitoring_mode = True
                rule.monitoring_watches = valid_watches
                logger.info(f"[모니터링] 활성화됨 - {total_monitor_actions}개 액션")
            else:
                logger.info(f"[모니터링] 비활성화됨 - 조건 미충족 (checkbox={has_checkbox}, watches={has_watches}, actions={has_actions})")

            # 최종 상태 확인 로그
            logger.info(f"[모니터링 최종] is_monitoring_mode={rule.is_monitoring_mode}")

            self._modified = True
            dialog.destroy()

        ctk.CTkButton(
            bottom_frame, text="저장", width=120, height=40,
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color=COLORS["success"], hover_color="#2ea44f",
            command=save,
        ).pack(side="left", padx=10)

        ctk.CTkButton(
            bottom_frame, text="취소", width=120, height=40,
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color=COLORS["bg_card"], hover_color=COLORS["bg_card_hover"],
            text_color=COLORS["text_secondary"],
            command=dialog.destroy,
        ).pack(side="left", padx=10)

        dialog.wait_window()
        # 다이얼로그 종료 후 상태 확인
        logger.info(f"[다이얼로그 종료] rule.is_monitoring_mode={rule.is_monitoring_mode}, watches={len(rule.monitoring_watches)}")
        self._refresh_action_list()

    def _open_region_selector(self, watch_index: int, region_label, watches_data: list, parent_dialog):
        """드래그로 검색 범위 선택 (ScreenRegionSelector 사용)"""
        from src.ui.analyzer_view import ScreenRegionSelector

        def on_region_select(x1, y1, x2, y2):
            """영역 선택 완료"""
            watches_data[watch_index]["search_region"] = [x1, y1, x2, y2]
            region_label.configure(
                text=f"({x1}, {y1}) ~ ({x2}, {y2})",
                text_color=COLORS["accent"]
            )
            parent_dialog.deiconify()
            parent_dialog.grab_set()
            parent_dialog.focus_force()

        def on_cancel():
            """선택 취소"""
            parent_dialog.deiconify()
            parent_dialog.grab_set()
            parent_dialog.focus_force()

        # 영역 선택기 열기 (self를 부모로 사용해야 숨겨진 다이얼로그 문제 방지)
        ScreenRegionSelector(self, on_region_select, on_cancel)

    def _detach_rule(self, rule: AutomationRule):
        """규칙을 부모에서 분리하여 최상위로 이동"""
        parent = self._find_parent_rule(rule)
        if parent and rule in parent.children:
            parent.children.remove(rule)
            rule.parent_id = None
            # 최상위에 추가
            self._plan.initial_rules.append(rule)
            self._modified = True
            self._refresh_action_list()
            logger.info(f"규칙 종속 해제: {rule.rule_id}")

    def _copy_rule(self, rule: AutomationRule):
        """규칙 복사 (하위 규칙 포함)"""
        import copy
        # 깊은 복사로 하위 규칙까지 모두 복사
        set_action_clipboard(copy.deepcopy(rule))
        logger.info(f"규칙 복사됨: {rule.rule_id} (하위 {len(rule.children)}개 포함)")

    def _paste_rule(self, target_rule: AutomationRule):
        """클립보드의 규칙을 대상 규칙 아래에 붙여넣기"""
        clipboard_content = get_action_clipboard()
        if clipboard_content is None:
            return

        import copy
        import uuid

        # 새 ID로 복사본 생성
        def assign_new_ids(rule: AutomationRule, parent_id: str = None):
            """재귀적으로 새 ID 할당"""
            rule.rule_id = f"rule_{uuid.uuid4().hex[:8]}"
            rule.parent_id = parent_id
            for child in rule.children:
                assign_new_ids(child, rule.rule_id)

        # 깊은 복사 후 새 ID 할당
        new_rule = copy.deepcopy(clipboard_content)
        assign_new_ids(new_rule, target_rule.rule_id)

        # 대상 규칙의 자식으로 추가
        target_rule.children.append(new_rule)
        self._modified = True
        self._refresh_action_list()
        logger.info(f"규칙 붙여넣기: {new_rule.rule_id} -> {target_rule.rule_id}")

    def _paste_rule_top(self):
        """클립보드의 규칙을 최상위에 붙여넣기"""
        clipboard_content = get_action_clipboard()
        if clipboard_content is None:
            return

        import copy
        import uuid

        # 새 ID로 복사본 생성
        def assign_new_ids(rule: AutomationRule, parent_id: str = None):
            """재귀적으로 새 ID 할당"""
            rule.rule_id = f"rule_{uuid.uuid4().hex[:8]}"
            rule.parent_id = parent_id
            for child in rule.children:
                assign_new_ids(child, rule.rule_id)

        # 깊은 복사 후 새 ID 할당
        new_rule = copy.deepcopy(clipboard_content)
        assign_new_ids(new_rule, None)  # 최상위이므로 parent_id = None

        # 최상위에 추가
        self._plan.initial_rules.append(new_rule)
        self._modified = True
        self._refresh_action_list()
        logger.info(f"규칙 최상위에 붙여넣기: {new_rule.rule_id}")

    def _paste_as_monitor_action(self, rule_index: int, watch_index: int):
        """클립보드의 액션을 모니터링 액션으로 붙여넣기 (자식 포함)"""
        clipboard = get_action_clipboard()
        if clipboard is None:
            return

        if rule_index >= len(self._plan.initial_rules):
            return

        rule = self._plan.initial_rules[rule_index]
        watches = getattr(rule, 'monitoring_watches', [])
        if watch_index >= len(watches):
            return

        def convert_to_monitor_action(action):
            """액션을 monitor_action 형식으로 변환 (모든 속성 포함)"""
            action_type = action.action_type
            monitor_action = None

            if action_type == "type":
                monitor_action = {"type": "텍스트 입력", "text": action.action_text or ""}
            elif action_type in ["hotkey", "key_press"]:
                monitor_action = {"type": "키 입력", "keys": action.action_keys or []}
            elif action_type in ["click", "double_click", "right_click"]:
                if getattr(action, 'target_image', None):
                    monitor_action = {"type": "이미지 클릭", "image": action.target_image, "click_type": action_type}
                else:
                    monitor_action = {"type": "마우스 클릭", "x": action.action_x, "y": action.action_y, "click_type": action_type}
            elif action_type == "scroll":
                monitor_action = {"type": "스크롤", "amount": getattr(action, 'scroll_amount', 0)}
            elif action_type == "drag":
                monitor_action = {
                    "type": "드래그",
                    "from_x": action.action_x,
                    "from_y": action.action_y,
                    "to_x": getattr(action, 'drag_to_x', 0),
                    "to_y": getattr(action, 'drag_to_y', 0)
                }
            elif getattr(action, 'target_image', None):
                monitor_action = {"type": "이미지 클릭", "image": action.target_image, "click_type": "click"}

            # 공통 속성 복사
            if monitor_action:
                monitor_action["wait_after"] = getattr(action, 'wait_after', 0.5)
                monitor_action["wait_random"] = getattr(action, 'wait_random', False)
                monitor_action["wait_random_range"] = getattr(action, 'wait_random_range', 0.3)
                monitor_action["repeat_count"] = getattr(action, 'repeat_count', 1)
                monitor_action["repeat_delay"] = getattr(action, 'repeat_delay', 0.5)
                monitor_action["repeat_delay_random"] = getattr(action, 'repeat_delay_random', False)
                monitor_action["repeat_delay_random_range"] = getattr(action, 'repeat_delay_random_range', 0.3)
                monitor_action["typing_random"] = getattr(action, 'typing_random', False)
                monitor_action["typing_delay"] = getattr(action, 'typing_delay', 0.1)
                monitor_action["typing_delay_range"] = getattr(action, 'typing_delay_range', 0.05)
                monitor_action["confidence"] = getattr(action, 'confidence', 0.65)
                monitor_action["search_radius"] = getattr(action, 'search_radius', 0)

            return monitor_action

        def collect_all_actions(action):
            """액션과 모든 자식 액션을 수집"""
            result = [action]
            for child in getattr(action, 'children', []) or []:
                result.extend(collect_all_actions(child))
            return result

        # 클립보드 액션과 모든 자식 수집
        all_actions = collect_all_actions(clipboard)
        monitor_actions = []

        for action in all_actions:
            ma = convert_to_monitor_action(action)
            if ma:
                monitor_actions.append(ma)

        if monitor_actions:
            # monitor_actions 리스트에 추가 (없으면 생성)
            if "monitor_actions" not in watches[watch_index]:
                watches[watch_index]["monitor_actions"] = []
            watches[watch_index]["monitor_actions"].extend(monitor_actions)
            self._modified = True
            logger.info(f"모니터링 액션 추가: 액션{rule_index+1} 감시{watch_index+1} - {len(monitor_actions)}개 (자식 포함)")

    def _paste_as_monitoring_watch(self, rule: AutomationRule):
        """클립보드의 액션을 현재 액션의 모니터링 액션으로 추가 (자식 포함)"""
        clipboard = get_action_clipboard()
        if clipboard is None:
            return

        # monitoring_watches가 없으면 생성
        if not hasattr(rule, 'monitoring_watches') or rule.monitoring_watches is None:
            rule.monitoring_watches = []

        def convert_to_monitor_action(action):
            """액션을 monitor_action 형식으로 변환 (모든 속성 포함)"""
            action_type = action.action_type
            monitor_action = None

            if action_type == "type":
                monitor_action = {"type": "텍스트 입력", "text": action.action_text or ""}
            elif action_type in ["hotkey", "key_press"]:
                monitor_action = {"type": "키 입력", "keys": action.action_keys or []}
            elif action_type in ["click", "double_click", "right_click"]:
                if getattr(action, 'target_image', None):
                    monitor_action = {"type": "이미지 클릭", "image": action.target_image, "click_type": action_type}
                else:
                    monitor_action = {"type": "마우스 클릭", "x": action.action_x, "y": action.action_y, "click_type": action_type}
            elif action_type == "scroll":
                monitor_action = {"type": "스크롤", "amount": getattr(action, 'scroll_amount', 0)}
            elif action_type == "drag":
                monitor_action = {
                    "type": "드래그",
                    "from_x": action.action_x,
                    "from_y": action.action_y,
                    "to_x": getattr(action, 'drag_to_x', 0),
                    "to_y": getattr(action, 'drag_to_y', 0)
                }
            elif getattr(action, 'target_image', None):
                monitor_action = {"type": "이미지 클릭", "image": action.target_image, "click_type": "click"}
            else:
                monitor_action = {"type": "마우스 클릭", "x": action.action_x or 0, "y": action.action_y or 0, "click_type": "click"}

            # 공통 속성 복사
            if monitor_action:
                monitor_action["wait_after"] = getattr(action, 'wait_after', 0.5)
                monitor_action["wait_random"] = getattr(action, 'wait_random', False)
                monitor_action["wait_random_range"] = getattr(action, 'wait_random_range', 0.3)
                monitor_action["repeat_count"] = getattr(action, 'repeat_count', 1)
                monitor_action["repeat_delay"] = getattr(action, 'repeat_delay', 0.5)
                monitor_action["repeat_delay_random"] = getattr(action, 'repeat_delay_random', False)
                monitor_action["repeat_delay_random_range"] = getattr(action, 'repeat_delay_random_range', 0.3)
                monitor_action["typing_random"] = getattr(action, 'typing_random', False)
                monitor_action["typing_delay"] = getattr(action, 'typing_delay', 0.1)
                monitor_action["typing_delay_range"] = getattr(action, 'typing_delay_range', 0.05)
                monitor_action["confidence"] = getattr(action, 'confidence', 0.65)
                monitor_action["search_radius"] = getattr(action, 'search_radius', 0)

            return monitor_action

        def collect_all_actions(action):
            """액션과 모든 자식 액션을 수집"""
            result = [action]
            for child in getattr(action, 'children', []) or []:
                result.extend(collect_all_actions(child))
            return result

        # 클립보드 액션과 모든 자식 수집
        all_actions = collect_all_actions(clipboard)
        monitor_actions = []

        for action in all_actions:
            ma = convert_to_monitor_action(action)
            if ma:
                monitor_actions.append(ma)

        if not monitor_actions:
            messagebox.showwarning("경고", "이 액션은 모니터링 액션으로 변환할 수 없습니다.")
            return

        # 감시 항목이 여러 개면 선택, 1개면 바로 추가, 없으면 새로 생성
        if len(rule.monitoring_watches) > 1:
            from tkinter import simpledialog
            # 선택 다이얼로그 표시
            watch_options = []
            for i, w in enumerate(rule.monitoring_watches):
                img_name = Path(w.get("image", "")).name[:15] if w.get("image") else "이미지 없음"
                action_count = len(w.get("monitor_actions", []))
                watch_options.append(f"{i+1}. {img_name} ({action_count}개 액션)")

            selected = simpledialog.askinteger(
                "감시 항목 선택",
                f"모니터링 액션을 추가할 감시 항목을 선택하세요:\n\n" + "\n".join(watch_options) + "\n\n번호 입력 (1~{})".format(len(rule.monitoring_watches)),
                minvalue=1, maxvalue=len(rule.monitoring_watches)
            )
            if selected is None:
                return
            watch_idx = selected - 1
        elif len(rule.monitoring_watches) == 1:
            watch_idx = 0
        else:
            # 새 watch 생성
            new_watch = {
                "image": getattr(clipboard, 'target_image', None),
                "goto_index": -1,
                "monitor_actions": monitor_actions,
                "confidence": 0.65,
            }
            rule.monitoring_watches.append(new_watch)
            rule.is_monitoring_mode = True
            self._modified = True
            self._refresh_action_list()
            logger.info(f"모니터링 액션 추가 (새 감시 항목): {len(monitor_actions)}개")
            return

        # 선택된 감시 항목에 추가
        if "monitor_actions" not in rule.monitoring_watches[watch_idx]:
            rule.monitoring_watches[watch_idx]["monitor_actions"] = []
        rule.monitoring_watches[watch_idx]["monitor_actions"].extend(monitor_actions)

        rule.is_monitoring_mode = True
        self._modified = True
        self._refresh_action_list()
        logger.info(f"모니터링 액션 추가 (감시 {watch_idx+1}): {len(monitor_actions)}개")

    def _add_text_action(self):
        """텍스트 입력 액션 추가"""
        dialog = ctk.CTkInputDialog(
            text="입력할 텍스트를 입력하세요:",
            title="텍스트 액션 추가",
        )
        text = dialog.get_input()

        if text:
            new_rule = AutomationRule(
                rule_id=f"rule_{len(self._plan.initial_rules):04d}",
                action_type="type",
                action_text=text,
                wait_after=0.5,
            )
            self._plan.initial_rules.append(new_rule)
            self._modified = True
            self._refresh_action_list()
            logger.info(f"텍스트 액션 추가: {text[:30]}...")

    def _add_key_action(self):
        """키 입력 액션 추가"""
        dialog = KeyInputDialog(self)
        key = dialog.get_key()

        if key:
            new_rule = AutomationRule(
                rule_id=f"rule_{len(self._plan.initial_rules):04d}",
                action_type="hotkey",
                action_keys=[key.lower().strip()],
                wait_after=0.5,
            )
            self._plan.initial_rules.append(new_rule)
            self._modified = True
            self._refresh_action_list()
            logger.info(f"키 액션 추가: {key}")

    def _add_mouse_action(self):
        """마우스 클릭 액션 추가"""
        from tkinter import simpledialog

        # X 좌표 입력
        x = simpledialog.askinteger(
            "마우스 입력",
            "클릭할 X 좌표를 입력하세요:",
            parent=self,
            minvalue=0,
            maxvalue=9999
        )
        if x is None:
            return

        # Y 좌표 입력
        y = simpledialog.askinteger(
            "마우스 입력",
            "클릭할 Y 좌표를 입력하세요:",
            parent=self,
            minvalue=0,
            maxvalue=9999
        )
        if y is None:
            return

        new_rule = AutomationRule(
            rule_id=f"rule_{len(self._plan.initial_rules):04d}",
            action_type="click",
            action_x=x,
            action_y=y,
            wait_after=0.5,
        )
        self._plan.initial_rules.append(new_rule)
        self._modified = True
        self._refresh_action_list()
        logger.info(f"마우스 클릭 액션 추가: ({x}, {y})")

    def _add_image_action(self):
        """이미지 클릭 액션 추가"""
        from tkinter import filedialog
        import shutil
        import uuid

        # 이미지 파일 선택
        image_path = filedialog.askopenfilename(
            title="클릭할 이미지 선택",
            filetypes=[
                ("이미지 파일", "*.png *.jpg *.jpeg *.bmp *.gif"),
                ("모든 파일", "*.*"),
            ],
            initialdir=str(DATA_DIR / "templates"),
        )

        if not image_path:
            return

        try:
            # templates 폴더에 이미지 복사
            templates_dir = DATA_DIR / "templates"
            templates_dir.mkdir(parents=True, exist_ok=True)
            new_ext = Path(image_path).suffix
            new_filename = f"img_{uuid.uuid4().hex[:8]}{new_ext}"
            dest_path = templates_dir / new_filename
            shutil.copy2(image_path, dest_path)

            new_rule = AutomationRule(
                rule_id=f"rule_{len(self._plan.initial_rules):04d}",
                action_type="click",
                target_image=str(dest_path),
                wait_after=0.5,
            )
            self._plan.initial_rules.append(new_rule)
            self._modified = True
            self._refresh_action_list()
            logger.info(f"이미지 클릭 액션 추가: {dest_path}")
        except Exception as e:
            from tkinter import messagebox
            logger.error(f"이미지 액션 추가 실패: {e}")
            messagebox.showerror("오류", f"이미지 추가 실패: {e}")

    def _add_screenshot_action(self):
        """스크린샷 찍어서 이미지 액션으로 추가"""
        from tkinter import filedialog, messagebox
        import pyautogui
        from pathlib import Path

        # 저장 경로 선택
        file_path = filedialog.asksaveasfilename(
            title="스크린샷 저장",
            defaultextension=".png",
            filetypes=[("PNG 파일", "*.png"), ("모든 파일", "*.*")],
            initialdir=str(Path.home() / "Pictures"),
        )

        if not file_path:
            return

        try:
            # 스크린샷 찍기
            screenshot = pyautogui.screenshot()
            screenshot.save(file_path)
            logger.info(f"스크린샷 저장: {file_path}")

            # 이미지 액션으로 추가할지 확인
            if messagebox.askyesno("스크린샷 저장 완료", f"스크린샷이 저장되었습니다.\n\n{file_path}\n\n이 이미지를 클릭 액션으로 추가할까요?"):
                # templates 폴더에 복사
                import shutil
                templates_dir = DATA_DIR / "templates"
                templates_dir.mkdir(parents=True, exist_ok=True)

                # 파일명 생성
                existing = list(templates_dir.glob("screenshot_*.png"))
                new_num = len(existing) + 1
                new_filename = f"screenshot_{new_num:04d}.png"
                dest_path = templates_dir / new_filename
                shutil.copy2(file_path, dest_path)

                # 액션 추가
                new_rule = AutomationRule(
                    rule_id=f"rule_{len(self._plan.initial_rules):04d}",
                    action_type="click",
                    target_image=str(dest_path),
                    wait_after=0.5,
                )
                self._plan.initial_rules.append(new_rule)
                self._modified = True
                self._refresh_action_list()
                logger.info(f"스크린샷 액션 추가: {dest_path}")

        except Exception as e:
            logger.error(f"스크린샷 실패: {e}")
            messagebox.showerror("오류", f"스크린샷 실패: {e}")

    def _flatten_children(self):
        """선택된 액션의 하위 액션들을 같은 레벨로 해체"""
        from tkinter import messagebox

        # 선택된 규칙 확인
        if not hasattr(self, '_selected_rule') or self._selected_rule is None:
            messagebox.showwarning("경고", "먼저 하위 액션을 해체할 액션을 선택하세요.")
            return

        rule = self._selected_rule
        if not rule.children:
            messagebox.showinfo("알림", "이 액션에는 하위 액션이 없습니다.")
            return

        # 확인
        child_count = len(rule.children)
        if not messagebox.askyesno("하위 해체", f"{child_count}개의 하위 액션을 같은 레벨로 해체할까요?\n\n부모 액션 다음에 배치됩니다."):
            return

        try:
            # 부모 인덱스 찾기
            idx = self._plan.initial_rules.index(rule)

            # 자식들을 부모 다음에 삽입
            children = list(rule.children)  # 복사
            rule.children = []  # 부모에서 자식 제거

            for i, child in enumerate(children):
                child.parent_id = None  # 부모 참조 제거
                self._plan.initial_rules.insert(idx + 1 + i, child)

            self._modified = True
            self._refresh_action_list()
            logger.info(f"하위 해체 완료: {child_count}개 액션")
            messagebox.showinfo("완료", f"{child_count}개의 하위 액션이 해체되었습니다.")

        except ValueError:
            messagebox.showerror("오류", "액션을 찾을 수 없습니다.")
        except Exception as e:
            logger.error(f"하위 해체 실패: {e}")
            messagebox.showerror("오류", f"하위 해체 실패: {e}")

    def _select_rule(self, rule: AutomationRule):
        """규칙 선택 (초록색 하이라이트)"""
        # 이전 선택 해제
        if self._selected_rule is not None and self._selected_rule.rule_id in self._rule_widgets:
            old_widget = self._rule_widgets[self._selected_rule.rule_id].get("widget")
            if old_widget:
                old_widget.configure(fg_color=COLORS["bg_dark"])

        # 같은 규칙 다시 클릭하면 선택 해제
        if self._selected_rule is not None and self._selected_rule.rule_id == rule.rule_id:
            self._selected_rule = None
            logger.debug("규칙 선택 해제")
            return

        # 새 규칙 선택
        self._selected_rule = rule
        if rule.rule_id in self._rule_widgets:
            new_widget = self._rule_widgets[rule.rule_id].get("widget")
            if new_widget:
                new_widget.configure(fg_color="#2e7d32")  # 초록색
        logger.debug(f"규칙 선택: {rule.rule_id}")

    def _randomize_all_delays(self):
        """전체 액션의 랜덤 대기시간 체크 토글"""
        # 현재 상태 확인 (하나라도 False면 전체 True로, 전부 True면 전체 False로)
        all_random = True
        def check_random(rules):
            nonlocal all_random
            for rule in rules:
                if not getattr(rule, 'wait_random', False):
                    all_random = False
                    return
                if rule.children:
                    check_random(rule.children)
        check_random(self._plan.initial_rules)

        # 토글 적용
        new_state = not all_random
        count = 0
        def apply_random(rules):
            nonlocal count
            for rule in rules:
                rule.wait_random = new_state
                if new_state and (not hasattr(rule, 'wait_random_range') or rule.wait_random_range is None or rule.wait_random_range == 0):
                    rule.wait_random_range = 0.3
                count += 1
                if rule.children:
                    apply_random(rule.children)

        apply_random(self._plan.initial_rules)

        self._modified = True
        self._refresh_action_list()
        logger.info(f"전체 랜덤 {'활성화' if new_state else '비활성화'}: {count}개 액션")

    def _toggle_all_children(self):
        """모든 액션을 1번 액션의 하위로 종속/해제 토글"""
        from tkinter import messagebox

        if len(self._plan.initial_rules) < 2:
            return

        first_rule = self._plan.initial_rules[0]

        # 현재 상태 확인: 1번 액션에 자식이 있으면 해제, 없으면 종속
        if first_rule.children and len(first_rule.children) > 0:
            # 해제 확인
            if not messagebox.askyesno("하위 종속 해제", f"{len(first_rule.children)}개 액션의 하위 종속을 해제하시겠습니까?"):
                return

            # 해제: 1번 액션의 자식들을 같은 레벨로 이동
            children = list(first_rule.children)
            first_rule.children = []

            for child in children:
                child.parent_id = None
                self._plan.initial_rules.append(child)

            logger.info(f"하위 종속 해제: {len(children)}개 액션")
        else:
            # 종속 확인
            children_count = len(self._plan.initial_rules) - 1
            if not messagebox.askyesno("하위 종속", f"{children_count}개 액션을 1번 액션의 하위로 종속하시겠습니까?"):
                return

            # 종속: 2번 이후 모든 액션을 1번의 자식으로
            children_to_move = self._plan.initial_rules[1:]
            self._plan.initial_rules = [first_rule]

            for child in children_to_move:
                child.parent_id = first_rule.rule_id
                first_rule.children.append(child)

            # 접힌 상태 해제 (보이도록)
            self._collapsed_items.discard(first_rule.rule_id)

            logger.info(f"하위 종속: {len(children_to_move)}개 액션을 1번 아래로")

        self._modified = True
        self._refresh_action_list()

    def _toggle_all_collapse(self):
        """모든 액션 접기/펼치기"""
        if self._all_collapsed:
            # 모두 펼치기
            self._collapsed_items.clear()
            self._all_collapsed = False
            self._collapse_btn.configure(text="모두 접기")
        else:
            # 모두 접기 (자식이 있는 모든 규칙의 rule_id 추가)
            self._collapsed_items = set()
            for rule in self._plan.initial_rules:
                if rule.children:
                    self._collapsed_items.add(rule.rule_id)
                self._collect_parent_rule_ids(rule)
            self._all_collapsed = True
            self._collapse_btn.configure(text="모두 펼치기")
        self._refresh_action_list()

    def _collect_parent_rule_ids(self, rule: AutomationRule):
        """자식이 있는 모든 규칙의 ID 수집 (재귀)"""
        for child in rule.children:
            if child.children:
                self._collapsed_items.add(child.rule_id)
            self._collect_parent_rule_ids(child)

    def _toggle_item_collapse(self, rule_id: str):
        """개별 액션 접기/펼치기 (가상 스크롤: 리스트 재생성)"""
        if rule_id in self._collapsed_items:
            # 펼치기
            self._collapsed_items.discard(rule_id)
        else:
            # 접기
            self._collapsed_items.add(rule_id)

        # 가상 스크롤 리스트 새로고침
        self._refresh_action_list()

    def _move_to_child(self, rule: AutomationRule):
        """현재 규칙을 이전 규칙의 자식으로 이동"""
        # 현재 규칙의 인덱스 찾기
        try:
            idx = self._plan.initial_rules.index(rule)
        except ValueError:
            return

        if idx <= 0:
            return  # 첫 번째 규칙은 이동 불가

        # 이전 규칙의 자식으로 추가
        prev_rule = self._plan.initial_rules[idx - 1]
        self._plan.initial_rules.remove(rule)
        rule.parent_id = prev_rule.rule_id
        prev_rule.children.append(rule)

        self._modified = True
        self._refresh_action_list()
        logger.info(f"규칙 '{rule.rule_id}'을 '{prev_rule.rule_id}'의 하위로 이동")

    def _move_to_parent(self, rule: AutomationRule):
        """자식 규칙을 최상위로 이동"""
        # 부모 규칙 찾기
        parent_rule = self._find_parent_rule(rule)
        if not parent_rule:
            return

        # 부모에서 제거
        parent_rule.children.remove(rule)
        rule.parent_id = None

        # 부모 규칙 바로 뒤에 삽입
        try:
            parent_idx = self._plan.initial_rules.index(parent_rule)
            self._plan.initial_rules.insert(parent_idx + 1, rule)
        except ValueError:
            # 부모가 다른 규칙의 자식인 경우, 최상위로
            self._plan.initial_rules.append(rule)

        self._modified = True
        self._refresh_action_list()
        logger.info(f"규칙 '{rule.rule_id}'을 상위로 이동")

    def _find_parent_rule(self, target: AutomationRule) -> Optional[AutomationRule]:
        """대상 규칙의 부모 규칙 찾기"""
        for rule in self._plan.initial_rules:
            parent = self._find_parent_in_tree(rule, target)
            if parent:
                return parent
        return None

    def _find_parent_in_tree(self, rule: AutomationRule, target: AutomationRule) -> Optional[AutomationRule]:
        """트리에서 대상의 부모 찾기 (재귀)"""
        if target in rule.children:
            return rule
        for child in rule.children:
            parent = self._find_parent_in_tree(child, target)
            if parent:
                return parent
        return None

    def _on_close(self):
        """닫기"""
        # 실행 중이면 먼저 중지
        if self._is_running:
            self._stop_execution()

        if self._modified:
            from tkinter import messagebox
            if messagebox.askyesno("저장 확인", "수정된 내용이 있습니다. 저장하시겠습니까?"):
                self._save_plan()
        self.destroy()


class SequenceDetailDialog(ctk.CTkToplevel):
    """재생(녹화목록) 상세보기/수정 다이얼로그"""

    def __init__(self, parent, sequence: Sequence, db):
        super().__init__(parent)

        self._sequence = sequence
        self._db = db
        self._thumbnail_refs = []
        self._modified = False
        self._scrollable = None
        self._collapsed_items = set()  # 접힌 액션 인덱스
        self._all_collapsed = True  # 전체 접기 상태 (기본값: 접힘)

        # 자식이 있는 액션은 기본적으로 접힌 상태로 시작
        self._init_collapsed_items()

        # 드래그 앤 드롭 상태
        self._drag_data = {"action": None, "widget": None, "start_y": 0}
        self._drop_target = None
        self._action_widgets = {}  # action_id -> widget 매핑
        self._selected_action = None  # 선택된 액션

        self.title(f"재생 수정 - {sequence.name}")
        self.geometry("950x700")
        self.resizable(True, True)
        self.configure(fg_color=COLORS["bg_dark"])

        self.transient(parent)
        self.grab_set()

        # 창 위치 복원 및 자동 저장
        self.update_idletasks()
        setup_window_position(self, "SequenceDetailDialog")

        self._setup_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _init_collapsed_items(self):
        """자식이 있는 액션을 접힌 상태로 초기화"""
        def add_collapsed(actions):
            for action in actions:
                if action.children:
                    self._collapsed_items.add(action.action_id)
                    add_collapsed(action.children)
        if self._sequence.actions:
            add_collapsed(self._sequence.actions)

    def _setup_ui(self):
        """UI 구성"""
        # 하단 버튼
        btn_frame = ctk.CTkFrame(self, fg_color=COLORS["bg_card"])
        btn_frame.pack(side="bottom", fill="x", pady=0)

        btn_row = ctk.CTkFrame(btn_frame, fg_color="transparent")
        btn_row.pack(pady=15)

        self._save_btn = ctk.CTkButton(
            btn_row,
            text="저장",
            command=self._save_sequence,
            width=100,
            height=38,
            fg_color=COLORS["success"],
            hover_color="#2ea44f",
            font=ctk.CTkFont(size=13, weight="bold"),
            corner_radius=6,
        )
        self._save_btn.pack(side="left", padx=8)

        ctk.CTkButton(
            btn_row,
            text="닫기",
            command=self._on_close,
            width=100,
            height=38,
            fg_color=COLORS["bg_dark"],
            hover_color=COLORS["bg_card_hover"],
            text_color=COLORS["text_secondary"],
            corner_radius=6,
        ).pack(side="left", padx=8)

        # 메인 영역
        main = ctk.CTkFrame(self, fg_color="transparent")
        main.pack(fill="both", expand=True, padx=25, pady=20)

        # 헤더
        header = ctk.CTkFrame(main, fg_color="transparent")
        header.pack(fill="x")

        ctk.CTkLabel(
            header,
            text=self._sequence.name,
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(side="left")

        ctk.CTkLabel(
            header,
            text="(이미지 클릭하여 크롭/수정)",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["accent"],
        ).pack(side="left", padx=(15, 0))

        # 모두 접기/펼치기 버튼
        self._collapse_btn = ctk.CTkButton(
            header,
            text="모두 접기",
            command=self._toggle_all_collapse,
            width=80,
            height=30,
            fg_color=COLORS["bg_card"],
            hover_color=COLORS["bg_card_hover"],
            text_color=COLORS["text_secondary"],
            font=ctk.CTkFont(size=12),
            corner_radius=6,
        )
        self._collapse_btn.pack(side="right", padx=(5, 0))

        # 액션 추가 버튼들 (2줄 레이아웃)
        btn_container = ctk.CTkFrame(header, fg_color="transparent")
        btn_container.pack(side="right", padx=(5, 10))

        # 첫번째 줄: 텍스트 입력, 키 입력
        btn_row1 = ctk.CTkFrame(btn_container, fg_color="transparent")
        btn_row1.pack(fill="x", pady=(0, 3))

        ctk.CTkButton(
            btn_row1,
            text="+ 텍스트 입력",
            command=self._add_text_action,
            width=110,
            height=28,
            fg_color=COLORS["success"],
            hover_color="#2ea44f",
            font=ctk.CTkFont(size=12),
            corner_radius=6,
        ).pack(side="left", padx=(0, 5))

        ctk.CTkButton(
            btn_row1,
            text="+ 키 입력",
            command=self._add_key_action,
            width=110,
            height=28,
            fg_color=COLORS["accent_orange"],
            hover_color="#d08050",
            font=ctk.CTkFont(size=12),
            corner_radius=6,
        ).pack(side="left")

        # 두번째 줄: 마우스 입력, 이미지 입력
        btn_row2 = ctk.CTkFrame(btn_container, fg_color="transparent")
        btn_row2.pack(fill="x")

        ctk.CTkButton(
            btn_row2,
            text="+ 마우스 입력",
            command=self._add_mouse_action,
            width=110,
            height=28,
            fg_color=COLORS["accent_blue"],
            hover_color="#2563eb",
            font=ctk.CTkFont(size=12),
            corner_radius=6,
        ).pack(side="left", padx=(0, 5))

        ctk.CTkButton(
            btn_row2,
            text="+ 이미지 입력",
            command=self._add_image_action,
            width=110,
            height=28,
            fg_color="#b48ead",
            hover_color="#a07090",
            font=ctk.CTkFont(size=12),
            corner_radius=6,
        ).pack(side="left")

        # 세번째 줄: 스크린샷 파일, 하위종목해체
        btn_row3 = ctk.CTkFrame(btn_container, fg_color="transparent")
        btn_row3.pack(fill="x", pady=(3, 0))

        ctk.CTkButton(
            btn_row3,
            text="📷 스크린샷",
            command=self._add_screenshot_action,
            width=110,
            height=28,
            fg_color="#5e81ac",
            hover_color="#4c6a8a",
            font=ctk.CTkFont(size=12),
            corner_radius=6,
        ).pack(side="left", padx=(0, 5))

        ctk.CTkButton(
            btn_row3,
            text="🔓 하위해체",
            command=self._flatten_children,
            width=110,
            height=28,
            fg_color="#bf616a",
            hover_color="#a54950",
            font=ctk.CTkFont(size=12),
            corner_radius=6,
        ).pack(side="left")

        # 네번째 줄: 전체액션 랜덤
        btn_row4 = ctk.CTkFrame(btn_container, fg_color="transparent")
        btn_row4.pack(fill="x", pady=(3, 0))

        ctk.CTkButton(
            btn_row4,
            text="🎲 전체 랜덤",
            command=self._randomize_all_delays,
            width=110,
            height=28,
            fg_color="#d08770",
            hover_color="#b8705a",
            font=ctk.CTkFont(size=12),
            corner_radius=6,
        ).pack(side="left", padx=(0, 5))

        ctk.CTkButton(
            btn_row4,
            text="📁 하위종속",
            command=self._toggle_all_children,
            width=110,
            height=28,
            fg_color="#88c0d0",
            hover_color="#6a9fb0",
            font=ctk.CTkFont(size=12),
            corner_radius=6,
        ).pack(side="left")

        created_str = self._sequence.created_at.strftime("%Y-%m-%d") if self._sequence.created_at else "알 수 없음"
        ctk.CTkLabel(
            main,
            text=f"총 {len(self._sequence.actions)}개 액션  |  생성: {created_str}",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["text_secondary"],
        ).pack(anchor="w", pady=(5, 15))

        # 액션 목록
        self._scrollable = ctk.CTkScrollableFrame(
            main,
            fg_color=COLORS["bg_card"],
            corner_radius=12,
            scrollbar_button_color=COLORS["bg_card_hover"],
        )
        self._scrollable.pack(fill="both", expand=True)

        # 로딩 표시
        self._loading_label = ctk.CTkLabel(
            self._scrollable, text="로딩 중...",
            font=ctk.CTkFont(size=14), text_color=COLORS["text_secondary"]
        )
        self._loading_label.pack(pady=20)

        # 창이 표시된 후 렌더링 시작 (렉 방지)
        self.after(50, self._refresh_action_list)

    def _refresh_action_list(self):
        """액션 목록 새로고침 (배치 처리로 렉 방지)"""
        if self._scrollable is None:
            return

        # 진행 중인 배치 렌더링 취소
        if hasattr(self, '_batch_render_id') and self._batch_render_id:
            try:
                self.after_cancel(self._batch_render_id)
            except:
                pass
            self._batch_render_id = None

        # 기존 항목 일괄 삭제
        children = self._scrollable.winfo_children()
        for widget in children:
            widget.destroy()

        self._thumbnail_refs = []
        self._action_widgets = {}  # 위젯 매핑 초기화

        # 최상위 액션만 필터링 (parent_id가 없는 것)
        top_level_actions = []
        for action in self._sequence.actions:
            if not action.parent_id:
                top_level_actions.append(action)

        # 배치 처리로 렌더링 (한 번에 5개씩)
        actions_to_render = [(i + 1, action) for i, action in enumerate(top_level_actions)]
        self._render_actions_batch(actions_to_render, 0, batch_size=5)

    def _render_actions_batch(self, actions_list, start_idx, batch_size=5):
        """액션들을 배치로 나눠서 렌더링 (UI 블로킹 방지)"""
        if start_idx >= len(actions_list):
            self._batch_render_id = None
            return

        end_idx = min(start_idx + batch_size, len(actions_list))
        for i in range(start_idx, end_idx):
            idx, action = actions_list[i]
            self._create_action_item(self._scrollable, action, depth=0, index_str=str(idx))

        # 다음 배치를 after()로 예약 (UI 반응성 유지)
        if end_idx < len(actions_list):
            self._batch_render_id = self.after(1, lambda: self._render_actions_batch(actions_list, end_idx, batch_size))
        else:
            self._batch_render_id = None

    def _update_action_buttons(self, action: Action):
        """액션의 버튼들만 업데이트 (전체 새로고침 없이)"""
        if action.action_id not in self._action_widgets:
            return

        widgets = self._action_widgets[action.action_id]

        # 반복 횟수 버튼 업데이트
        if "repeat_btn" in widgets:
            repeat_count = getattr(action, 'repeat_count', 1)
            btn = widgets["repeat_btn"]
            btn.configure(
                text=f"x{repeat_count}",
                fg_color=COLORS["accent_blue"] if repeat_count > 1 else COLORS["bg_card"],
                text_color="white" if repeat_count > 1 else COLORS["text_secondary"],
            )

        # 대기시간 버튼 업데이트
        if "delay_btn" in widgets:
            wait_time = action.wait_after if action.wait_after else 0.5
            wait_random = getattr(action, 'wait_random', False)
            typing_random = getattr(action, 'typing_random', False) if action.action_type == "type" else False
            has_random = wait_random or typing_random
            btn = widgets["delay_btn"]
            btn.configure(
                text=f"{wait_time:.1f}초" + ("*" if has_random else ""),
                fg_color=COLORS["success"] if has_random else COLORS["bg_card"],
                text_color="white" if has_random else COLORS["text_secondary"],
            )

    def _create_action_item(self, parent, action: Action, depth: int = 0, index_str: str = "1"):
        """액션 항목 생성 (드래그 앤 드롭 지원)"""
        index = index_str  # 계층적 번호 (예: "3", "3-1", "3-2-1")
        has_children = len(action.children) > 0
        is_collapsed = action.action_id in self._collapsed_items

        # 깊이에 따른 들여쓰기
        indent = depth * 30

        # 외부 wrapper (item + children 포함) - 펼치기/접기 시 순서 유지를 위해
        item_wrapper = ctk.CTkFrame(parent, fg_color="transparent")
        item_wrapper.pack(fill="x")

        # 선택 상태에 따른 배경색
        is_selected = self._selected_action is not None and self._selected_action.action_id == action.action_id
        bg_color = "#2e7d32" if is_selected else COLORS["bg_dark"]  # 선택 시 초록색

        item = ctk.CTkFrame(item_wrapper, fg_color=bg_color, corner_radius=8)
        item.pack(fill="x", pady=4, padx=(10 + indent, 10))

        # 위젯 매핑 저장
        self._action_widgets[action.action_id] = {"widget": item, "action": action, "depth": depth, "wrapper": item_wrapper}

        # 클릭 시 선택
        def select_action(event, a=action):
            self._select_action(a)
        item.bind("<Button-1>", select_action)

        content = ctk.CTkFrame(item, fg_color="transparent")
        content.pack(fill="x", padx=12, pady=10)
        content.bind("<Button-1>", select_action)  # content도 클릭 가능

        # 오른쪽 클릭 메뉴
        def show_context_menu(event, a=action, d=depth):
            from tkinter import Menu
            popup = Menu(self, tearoff=0)
            popup.add_command(label="이름 설정", command=lambda: self._edit_action_name(a))
            # 클릭 유형 변경 (클릭 계열 액션인 경우만)
            if a.action_type in ["click", "double_click", "right_click"]:
                click_menu = Menu(popup, tearoff=0)
                click_menu.add_command(
                    label="✓ 왼쪽 클릭" if a.action_type == "click" else "  왼쪽 클릭",
                    command=lambda: self._change_action_click_type(a, "click")
                )
                click_menu.add_command(
                    label="✓ 더블 클릭" if a.action_type == "double_click" else "  더블 클릭",
                    command=lambda: self._change_action_click_type(a, "double_click")
                )
                click_menu.add_command(
                    label="✓ 오른쪽 클릭" if a.action_type == "right_click" else "  오른쪽 클릭",
                    command=lambda: self._change_action_click_type(a, "right_click")
                )
                popup.add_cascade(label="클릭 유형", menu=click_menu)
            popup.add_separator()
            popup.add_command(label="복사", command=lambda: self._copy_action(a))
            # 붙여넣기 (클립보드에 내용이 있을 때만 활성화)
            if get_action_clipboard() is not None:
                # 모니터링 액션으로 붙이기 서브메뉴 (하위로 붙여넣기 위에 배치)
                monitor_menu = tk.Menu(popup, tearoff=0)
                has_monitoring = False
                for mi, mr in enumerate(self._sequence.actions):
                    if getattr(mr, 'is_monitoring_mode', False) and getattr(mr, 'monitoring_watches', []):
                        has_monitoring = True
                        # 액션 이름 (description 사용)
                        action_name = mr.description[:15] if mr.description else f"액션{mi+1}"
                        for wi, watch in enumerate(mr.monitoring_watches):
                            watch_label = f"{action_name} - 감시{wi+1}"
                            monitor_menu.add_command(
                                label=watch_label,
                                command=lambda m=mi, w=wi: self._paste_as_monitor_action_seq(m, w)
                            )
                if has_monitoring:
                    popup.add_cascade(label="모니터링 액션으로 붙이기", menu=monitor_menu)
                popup.add_command(label="하위로 붙여넣기", command=lambda: self._paste_action(a))
                popup.add_command(label="최상위에 붙여넣기", command=self._paste_action_top)
            else:
                popup.add_command(label="하위로 붙여넣기", state="disabled")
                popup.add_command(label="최상위에 붙여넣기", state="disabled")
            if d > 0:  # 자식인 경우에만 종속 해제 표시
                popup.add_separator()
                popup.add_command(label="종속 해제", command=lambda: self._detach_action(a))
            popup.tk_popup(event.x_root, event.y_root)

        # 드래그 및 우클릭 바인딩 헬퍼
        def bind_drag(widget):
            widget.bind("<Button-1>", lambda e, a=action, w=item: self._on_drag_start(e, a, w))
            widget.bind("<B1-Motion>", self._on_drag_motion)
            widget.bind("<ButtonRelease-1>", self._on_drag_release)
            widget.bind("<Button-3>", lambda e, a=action, d=depth: show_context_menu(e, a, d))
            # 드래그 커서 설정 - 내부 위젯에 직접 적용
            try:
                widget.configure(cursor="fleur")
                # CTk 내부 캔버스에도 커서 적용
                for child in widget.winfo_children():
                    child.configure(cursor="fleur")
            except (tk.TclError, AttributeError):
                pass

        # 액션 색상 (숫자 배지용)
        action_colors = {
            "click": COLORS["accent_blue"],
            "double_click": COLORS["accent_blue"],
            "right_click": COLORS["accent_blue"],
            "type": COLORS["success"],
            "hotkey": COLORS["accent_orange"],
            "key_press": COLORS["accent_orange"],
            "scroll": "#b48ead",
            "drag": COLORS["warning"],
            "wait": COLORS["text_muted"],
            "wait_for_image": COLORS["accent"],
        }
        color = action_colors.get(action.action_type, COLORS["text_muted"])

        # 숫자 배지 (맨 앞에 위치)
        num_lbl = ctk.CTkLabel(
            content, text=f"{index}", font=ctk.CTkFont(size=12, weight="bold"),
            fg_color=color, text_color="white", corner_radius=4, width=26, height=22,
        )
        num_lbl.pack(side="left", padx=(0, 8))
        bind_drag(num_lbl)

        # 자식이 있으면 접기/펼치기 토글 버튼
        if has_children:
            toggle_btn = ctk.CTkButton(
                content,
                text="▶" if is_collapsed else "▼",
                font=ctk.CTkFont(size=10),
                fg_color="transparent",
                hover_color=COLORS["bg_card_hover"],
                text_color=COLORS["text_secondary"],
                width=24,
                height=24,
                corner_radius=4,
                command=lambda a=action: self._toggle_item_collapse(a.action_id),
            )
            toggle_btn.pack(side="left", padx=(0, 4))

        action_names = {
            "click": "왼쪽 클릭",
            "double_click": "더블 클릭",
            "right_click": "오른쪽 클릭",
            "type": "텍스트 입력",
            "hotkey": "단축키",
            "key_press": "키 입력",
            "scroll": "스크롤",
            "drag": "드래그",
            "wait": "대기",
            "wait_for_image": "이미지 대기",
        }

        # 썸네일
        thumb = ctk.CTkFrame(content, fg_color=COLORS["bg_card"], width=60, height=60, corner_radius=6)
        thumb.pack(side="left", padx=(0, 10))
        thumb.pack_propagate(False)
        self._display_thumbnail(thumb, action)

        # 정보 영역
        info = ctk.CTkFrame(content, fg_color="transparent")
        info.pack(side="left", fill="both", expand=True)

        # 번호 + 동작 유형
        row1 = ctk.CTkFrame(info, fg_color="transparent")
        row1.pack(fill="x", anchor="w")

        # 깊이 표시 (자식인 경우)
        if depth > 0:
            lbl = ctk.CTkLabel(row1, text="└", font=ctk.CTkFont(size=14), text_color=COLORS["text_muted"])
            lbl.pack(side="left", padx=(0, 4))
            bind_drag(lbl)

        type_lbl = ctk.CTkLabel(
            row1, text=action_names.get(action.action_type, action.action_type or "동작"),
            font=ctk.CTkFont(size=13, weight="bold"), text_color=color,
        )
        type_lbl.pack(side="left")
        bind_drag(type_lbl)

        # 이름(설명) 표시
        if action.description:
            name_lbl = ctk.CTkLabel(
                row1, text=f" - {action.description}",
                font=ctk.CTkFont(size=12), text_color=COLORS["text_primary"],
            )
            name_lbl.pack(side="left")
            bind_drag(name_lbl)

        # 자식 수 표시
        if has_children:
            child_lbl = ctk.CTkLabel(
                row1, text=f"  ({len(action.children)}개 하위)",
                font=ctk.CTkFont(size=11), text_color=COLORS["text_muted"],
            )
            child_lbl.pack(side="left")
            bind_drag(child_lbl)

        # 빈 공간 (드래그 가능)
        spacer = ctk.CTkLabel(row1, text="", fg_color="transparent")
        spacer.pack(side="left", fill="x", expand=True)
        bind_drag(spacer)

        # 상세 정보
        row2 = ctk.CTkFrame(info, fg_color="transparent")
        row2.pack(fill="x", pady=(4, 0), anchor="w")

        details = []
        if action.x is not None and action.y is not None:
            details.append(f"위치: ({action.x}, {action.y})")
        if action.text:
            text_preview = action.text[:30] + "..." if len(action.text) > 30 else action.text
            details.append(f'"{text_preview}"')
        if action.keys:
            details.append(f"[{' + '.join(action.keys).upper()}]")

        if details:
            detail_lbl = ctk.CTkLabel(
                row2, text="  |  ".join(details),
                font=ctk.CTkFont(size=11), text_color=COLORS["text_secondary"],
            )
            detail_lbl.pack(side="left")
            bind_drag(detail_lbl)

        # row2 빈 공간도 드래그 가능
        spacer2 = ctk.CTkLabel(row2, text="", fg_color="transparent")
        spacer2.pack(side="left", fill="x", expand=True)
        bind_drag(spacer2)

        # 버튼 영역
        btn_frame = ctk.CTkFrame(content, fg_color="transparent")
        btn_frame.pack(side="right", padx=(10, 0))

        # 위/아래 이동 버튼 (세로 배치, 크고 예쁘게)
        move_frame = ctk.CTkFrame(btn_frame, fg_color=COLORS["bg_card"], corner_radius=6)
        move_frame.pack(side="right", padx=(4, 0))

        ctk.CTkButton(
            move_frame,
            text="▲",
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color="transparent",
            hover_color=COLORS["accent_blue"],
            text_color=COLORS["text_secondary"],
            hover=True,
            width=28,
            height=18,
            corner_radius=4,
            command=lambda a=action: self._move_action_up(a),
        ).pack(side="top", padx=2, pady=(2, 0))

        ctk.CTkButton(
            move_frame,
            text="▼",
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color="transparent",
            hover_color=COLORS["accent_blue"],
            text_color=COLORS["text_secondary"],
            hover=True,
            width=28,
            height=18,
            corner_radius=4,
            command=lambda a=action: self._move_action_down(a),
        ).pack(side="top", padx=2, pady=(0, 2))

        # 삭제 버튼
        ctk.CTkButton(
            btn_frame,
            text="✕",
            font=ctk.CTkFont(size=12),
            fg_color=COLORS["error"],
            hover_color="#c0392b",
            text_color="white",
            width=30,
            height=26,
            corner_radius=4,
            command=lambda a=action: self._delete_action(a),
        ).pack(side="right", padx=(4, 0))

        # 스킵 모드 버튼 (S) - 이미지 못찾으면 스킵
        is_skip_action = getattr(action, 'skip_on_not_found', False)
        skip_btn_action = ctk.CTkButton(
            btn_frame,
            text="S",
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color="#2ecc71" if is_skip_action else COLORS["bg_card"],
            hover_color="#27ae60" if is_skip_action else COLORS["bg_card_hover"],
            text_color="white" if is_skip_action else COLORS["text_secondary"],
            width=30,
            height=26,
            corner_radius=4,
            command=lambda a=action: self._toggle_skip_mode_action(a),
        )
        skip_btn_action.pack(side="right", padx=(4, 0))
        self._action_widgets[action.action_id]["skip_btn"] = skip_btn_action

        # 반복 횟수 버튼
        repeat_count = getattr(action, 'repeat_count', 1)
        repeat_btn = ctk.CTkButton(
            btn_frame,
            text=f"x{repeat_count}",
            font=ctk.CTkFont(size=11),
            fg_color=COLORS["accent_blue"] if repeat_count > 1 else COLORS["bg_card"],
            hover_color="#1a7fd4" if repeat_count > 1 else COLORS["bg_card_hover"],
            text_color="white" if repeat_count > 1 else COLORS["text_secondary"],
            width=40,
            height=26,
            corner_radius=4,
            command=lambda a=action: self._edit_repeat_count_action(a),
        )
        repeat_btn.pack(side="right", padx=(4, 0))

        # 대기시간 버튼 (랜덤 여부 표시)
        wait_time = action.wait_after if action.wait_after else 0.5
        wait_random = getattr(action, 'wait_random', False)
        typing_random = getattr(action, 'typing_random', False) if action.action_type == "type" else False
        has_random = wait_random or typing_random
        delay_btn = ctk.CTkButton(
            btn_frame,
            text=f"{wait_time:.1f}초" + ("*" if has_random else ""),
            font=ctk.CTkFont(size=11),
            fg_color=COLORS["success"] if has_random else COLORS["bg_card"],
            hover_color="#2ea44f" if has_random else COLORS["bg_card_hover"],
            text_color="white" if has_random else COLORS["text_secondary"],
            width=55,
            height=26,
            corner_radius=4,
            command=lambda a=action: self._edit_wait_time_action(a),
        )
        delay_btn.pack(side="right", padx=(4, 0))

        # 버튼 참조 저장 (개별 업데이트용)
        self._action_widgets[action.action_id]["repeat_btn"] = repeat_btn
        self._action_widgets[action.action_id]["delay_btn"] = delay_btn

        # 자식 액션들 표시 (항상 생성, visibility로 제어)
        # item_wrapper 안에 생성하여 펼치기/접기 시 순서 유지
        if has_children:
            children_container = ctk.CTkFrame(item_wrapper, fg_color="transparent")
            if not is_collapsed:
                children_container.pack(fill="x")
            # 위젯 매핑에 children_container 추가
            self._action_widgets[action.action_id]["children_container"] = children_container
            for child_idx, child in enumerate(action.children, 1):
                child_index_str = f"{index}-{child_idx}"  # 예: "3-1", "3-2"
                self._create_action_item(children_container, child, depth + 1, index_str=child_index_str)

    def _display_thumbnail(self, parent, action: Action):
        """썸네일 표시 - 캐시 사용"""
        image_path = action.target_image

        if image_path and Path(image_path).exists():
            try:
                # 캐시된 썸네일 확인
                target_size = (60, 60)
                ctk_image = get_cached_thumbnail(image_path, target_size)

                if ctk_image is None:
                    # 캐시에 없으면 로드 (한글 경로 지원)
                    img_arr = np.fromfile(image_path, np.uint8)
                    img = cv2.imdecode(img_arr, cv2.IMREAD_COLOR)
                    if img is not None:
                        img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                        h, w = img_rgb.shape[:2]
                        scale = min(60 / w, 60 / h)
                        new_w, new_h = int(w * scale), int(h * scale)
                        resized = cv2.resize(img_rgb, (new_w, new_h))
                        pil_image = Image.fromarray(resized)
                        ctk_image = ctk.CTkImage(light_image=pil_image, dark_image=pil_image, size=(new_w, new_h))
                        # 캐시에 저장
                        set_cached_thumbnail(image_path, target_size, ctk_image)

                if ctk_image is not None:
                    thumb_btn = ctk.CTkButton(
                        parent,
                        image=ctk_image,
                        text="",
                        width=68,
                        height=68,
                        fg_color="transparent",
                        hover_color=COLORS["bg_card_hover"],
                        corner_radius=4,
                        command=lambda p=image_path, a=action: self._open_image_editor(p, a),
                    )
                    thumb_btn.pack(expand=True)
                    self._thumbnail_refs.append(ctk_image)
                    return
            except (IOError, OSError, ValueError):
                pass

        icons = {"click": "🖱", "type": "⌨", "hotkey": "⌨", "scroll": "📜", "drag": "↔", "wait": "⏳", "wait_for_image": "🔍"}
        ctk.CTkLabel(
            parent,
            text=icons.get(action.action_type, "📋"),
            font=ctk.CTkFont(size=20),
            text_color=COLORS["text_muted"],
        ).pack(expand=True)

    def _collect_all_image_actions(self) -> list:
        """이미지가 있는 모든 액션 수집 (재귀)"""
        result = []
        def collect(actions):
            for a in actions:
                if a.target_image:
                    result.append(a)
                if a.children:
                    collect(a.children)
        collect(self._sequence.actions)
        return result

    def _open_image_editor(self, image_path: str, action: Action):
        """이미지 편집기 열기"""
        # 모든 이미지 액션 수집
        all_image_actions = self._collect_all_image_actions()
        current_index = -1
        for i, a in enumerate(all_image_actions):
            if a.action_id == action.action_id:
                current_index = i
                break

        # 수정 여부 추적 (다이얼로그 닫힐 때 한 번만 새로고침)
        needs_refresh = [False]

        def on_crop_complete(new_path: str):
            self._modified = True
            needs_refresh[0] = True
            logger.info(f"이미지 크롭 완료: {new_path}")
            invalidate_thumbnail_cache(new_path)  # 캐시 무효화

        def on_delete():
            action.target_image = None
            self._modified = True
            needs_refresh[0] = True
            invalidate_thumbnail_cache(image_path)  # 캐시 무효화
            logger.info(f"이미지 삭제됨: {action.action_id}")

        def on_change(new_path: str):
            action.target_image = new_path
            self._modified = True
            needs_refresh[0] = True
            logger.info(f"이미지 변경 완료: {new_path}")

        dialog = ImageCropDialog(
            self, image_path,
            on_crop=on_crop_complete,
            on_delete=on_delete,
            on_change=on_change,
            image_list=all_image_actions,
            current_index=current_index,
        )
        self.wait_window(dialog)

        # 다이얼로그 닫힌 후 한 번만 새로고침
        if needs_refresh[0]:
            self._refresh_action_list()

    def _save_sequence(self):
        """재생 저장"""
        try:
            if self._sequence.id:
                self._db.update_sequence(self._sequence)
                logger.info(f"재생 저장: {self._sequence.name}")
                self._modified = False

                from tkinter import messagebox
                messagebox.showinfo("저장 완료", "재생가 저장되었습니다.")
        except Exception as e:
            logger.error(f"재생 저장 실패: {e}")
            from tkinter import messagebox
            messagebox.showerror("저장 실패", f"저장 중 오류가 발생했습니다:\n{e}")

    def _delete_action(self, action: Action):
        """액션 삭제"""
        from tkinter import messagebox
        if not messagebox.askyesno("삭제 확인", "이 액션을 삭제하시겠습니까?"):
            return

        # 최상위에서 찾기
        if action in self._sequence.actions:
            self._sequence.actions.remove(action)
        else:
            # 부모에서 찾아서 삭제
            parent = self._find_parent_action(action)
            if parent and action in parent.children:
                parent.children.remove(action)

        self._modified = True
        self._refresh_action_list()
        logger.info("액션 삭제됨")

    def _move_action_up(self, action: Action):
        """액션을 위로 이동"""
        # 부모가 있으면 부모의 children에서 이동
        parent = self._find_parent_action(action)
        if parent:
            actions_list = parent.children
        elif action in self._sequence.actions:
            actions_list = self._sequence.actions
        else:
            return

        idx = actions_list.index(action)
        if idx > 0:
            actions_list[idx], actions_list[idx - 1] = actions_list[idx - 1], actions_list[idx]
            self._modified = True
            self._refresh_action_list()

    def _move_action_down(self, action: Action):
        """액션을 아래로 이동"""
        # 부모가 있으면 부모의 children에서 이동
        parent = self._find_parent_action(action)
        if parent:
            actions_list = parent.children
        elif action in self._sequence.actions:
            actions_list = self._sequence.actions
        else:
            return

        idx = actions_list.index(action)
        if idx < len(actions_list) - 1:
            actions_list[idx], actions_list[idx + 1] = actions_list[idx + 1], actions_list[idx]
            self._modified = True
            self._refresh_action_list()

    def _edit_wait_time_action(self, action: Action):
        """대기시간 및 랜덤 설정"""
        is_type_action = action.action_type == "type"
        dialog_height = 480 if is_type_action else 320

        dialog = ctk.CTkToplevel(self)
        dialog.title("대기시간 설정")
        dialog.geometry(f"400x{dialog_height}")
        dialog.resizable(False, False)
        dialog.configure(fg_color=COLORS["bg_dark"])
        dialog.transient(self)
        dialog.grab_set()

        # 중앙 배치
        dialog.update_idletasks()
        x = (dialog.winfo_screenwidth() - 400) // 2
        y = (dialog.winfo_screenheight() - dialog_height) // 2
        dialog.geometry(f"+{x}+{y}")

        # 스크롤 가능 프레임
        main_frame = ctk.CTkScrollableFrame(dialog, fg_color="transparent")
        main_frame.pack(fill="both", expand=True, padx=20, pady=15)

        # === 기본 대기시간 ===
        ctk.CTkLabel(main_frame, text="기본 대기시간 (초)",
                     font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", pady=(0, 5))
        current_wait = action.wait_after if action.wait_after else 0.5
        wait_entry = ctk.CTkEntry(main_frame, width=200, height=38, font=ctk.CTkFont(size=14))
        wait_entry.insert(0, f"{current_wait:.2f}")
        wait_entry.pack(anchor="w")

        # === 랜덤 대기시간 ===
        ctk.CTkLabel(main_frame, text="",
                     font=ctk.CTkFont(size=8)).pack()  # 구분선

        wait_random_var = ctk.BooleanVar(value=getattr(action, 'wait_random', False))
        ctk.CTkCheckBox(main_frame, text="랜덤시간 활성화", variable=wait_random_var,
                        font=ctk.CTkFont(size=13)).pack(anchor="w", pady=(10, 5))

        wait_range_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        wait_range_frame.pack(anchor="w", pady=5)

        ctk.CTkLabel(wait_range_frame, text="±범위:", font=ctk.CTkFont(size=12)).pack(side="left")
        wait_range_entry = ctk.CTkEntry(wait_range_frame, width=100, height=32, font=ctk.CTkFont(size=13))
        wait_range_entry.insert(0, f"{getattr(action, 'wait_random_range', 0.3):.2f}")
        wait_range_entry.pack(side="left", padx=(5, 10))
        ctk.CTkLabel(wait_range_frame, text="초", font=ctk.CTkFont(size=12),
                     text_color=COLORS["text_secondary"]).pack(side="left")

        # === 타이핑 랜덤 (텍스트 액션만) ===
        typing_random_var = ctk.BooleanVar(value=False)
        typing_delay_entry = None
        typing_range_entry = None

        if is_type_action:
            ctk.CTkLabel(main_frame, text="",
                         font=ctk.CTkFont(size=8)).pack()  # 구분선
            ctk.CTkLabel(main_frame, text="타이핑랜덤 (글자 사이사이에 딜레이)",
                         font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", pady=(10, 5))

            typing_random_var = ctk.BooleanVar(value=getattr(action, 'typing_random', False))
            ctk.CTkCheckBox(main_frame, text="타이핑랜덤 활성화", variable=typing_random_var,
                            font=ctk.CTkFont(size=13)).pack(anchor="w", pady=5)

            # 기본 딜레이
            typing_base_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
            typing_base_frame.pack(anchor="w", pady=5)

            ctk.CTkLabel(typing_base_frame, text="기본 딜레이:", font=ctk.CTkFont(size=12)).pack(side="left")
            typing_delay_entry = ctk.CTkEntry(typing_base_frame, width=80, height=32, font=ctk.CTkFont(size=13))
            typing_delay_entry.insert(0, f"{getattr(action, 'typing_delay', 0.1):.2f}")
            typing_delay_entry.pack(side="left", padx=(5, 10))
            ctk.CTkLabel(typing_base_frame, text="초", font=ctk.CTkFont(size=12),
                         text_color=COLORS["text_secondary"]).pack(side="left")

            # ±범위
            typing_range_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
            typing_range_frame.pack(anchor="w", pady=5)

            ctk.CTkLabel(typing_range_frame, text="±범위:", font=ctk.CTkFont(size=12)).pack(side="left")
            typing_range_entry = ctk.CTkEntry(typing_range_frame, width=80, height=32, font=ctk.CTkFont(size=13))
            typing_range_entry.insert(0, f"{getattr(action, 'typing_delay_range', 0.05):.2f}")
            typing_range_entry.pack(side="left", padx=(5, 10))
            ctk.CTkLabel(typing_range_frame, text="초", font=ctk.CTkFont(size=12),
                         text_color=COLORS["text_secondary"]).pack(side="left")

        result = {"saved": False}

        def save():
            def parse_float(entry):
                val = entry.get().strip().replace(',', '.')
                return float(val) if val else 0.0
            try:
                wait_val = parse_float(wait_entry)
                wait_range = parse_float(wait_range_entry)

                if wait_val < 0:
                    from tkinter import messagebox
                    messagebox.showerror("오류", "대기시간은 0 이상이어야 합니다")
                    return

                if wait_range < 0:
                    from tkinter import messagebox
                    messagebox.showerror("오류", "±범위는 0 이상이어야 합니다")
                    return

                # 저장
                action.wait_after = wait_val
                action.wait_random = wait_random_var.get()
                action.wait_random_range = wait_range

                # 타이핑 랜덤
                if is_type_action and typing_delay_entry and typing_range_entry:
                    typing_delay = parse_float(typing_delay_entry)
                    typing_range = parse_float(typing_range_entry)
                    if typing_delay < 0 or typing_range < 0:
                        from tkinter import messagebox
                        messagebox.showerror("오류", "딜레이와 범위는 0 이상이어야 합니다")
                        return
                    action.typing_random = typing_random_var.get()
                    action.typing_delay = typing_delay
                    action.typing_delay_range = typing_range

                result["saved"] = True
                dialog.destroy()
            except ValueError:
                from tkinter import messagebox
                messagebox.showerror("오류", "숫자를 입력하세요")

        # 버튼
        btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_frame.pack(pady=15)
        ctk.CTkButton(btn_frame, text="저장", width=120, height=40,
                      font=ctk.CTkFont(size=14, weight="bold"), command=save).pack(side="left", padx=10)
        ctk.CTkButton(btn_frame, text="취소", width=120, height=40,
                      font=ctk.CTkFont(size=14, weight="bold"), fg_color=COLORS["bg_card"],
                      command=dialog.destroy).pack(side="left", padx=10)

        dialog.wait_window()

        if result["saved"]:
            self._modified = True
            self._update_action_buttons(action)
            logger.info(f"대기시간 설정 완료")

    def _toggle_skip_mode_action(self, action: Action):
        """스킵 모드 토글 - 이미지 못찾으면 wait_after 후 다음 액션으로"""
        current = getattr(action, 'skip_on_not_found', False)
        action.skip_on_not_found = not current

        # 버튼 색상 업데이트
        if action.action_id in self._action_widgets and "skip_btn" in self._action_widgets[action.action_id]:
            btn = self._action_widgets[action.action_id]["skip_btn"]
            is_skip = action.skip_on_not_found
            btn.configure(
                fg_color="#2ecc71" if is_skip else COLORS["bg_card"],
                hover_color="#27ae60" if is_skip else COLORS["bg_card_hover"],
                text_color="white" if is_skip else COLORS["text_secondary"],
            )

        # 수정됨 표시
        self._modified = True

        status = "활성화" if action.skip_on_not_found else "비활성화"
        logger.info(f"스킵 모드 {status}: {action.description or action.action_type}")

    def _edit_repeat_count_action(self, action: Action):
        """반복 설정 (횟수 + 반복 대기시간 + 랜덤)"""
        dialog = ctk.CTkToplevel(self)
        dialog.title("반복 설정")
        dialog.geometry("350x420")
        dialog.resizable(False, False)
        dialog.configure(fg_color=COLORS["bg_dark"])
        dialog.transient(self)
        dialog.grab_set()

        # 중앙 배치
        dialog.update_idletasks()
        x = (dialog.winfo_screenwidth() - 350) // 2
        y = (dialog.winfo_screenheight() - 420) // 2
        dialog.geometry(f"+{x}+{y}")

        main_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        main_frame.pack(fill="both", expand=True, padx=20, pady=15)

        # 반복 횟수
        ctk.CTkLabel(main_frame, text="반복 횟수",
                     font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", pady=(0, 5))

        count_entry = ctk.CTkEntry(main_frame, width=200, height=38, font=ctk.CTkFont(size=14))
        count_entry.insert(0, str(getattr(action, 'repeat_count', 1)))
        count_entry.pack(anchor="w")

        ctk.CTkLabel(main_frame, text="1 = 1회 실행, 2 = 2회 반복...",
                     font=ctk.CTkFont(size=11), text_color=COLORS["text_secondary"]).pack(anchor="w", pady=(5, 0))

        # 반복 대기시간
        ctk.CTkLabel(main_frame, text="반복 대기시간 (초)",
                     font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", pady=(15, 5))

        delay_entry = ctk.CTkEntry(main_frame, width=200, height=38, font=ctk.CTkFont(size=14))
        delay_entry.insert(0, f"{getattr(action, 'repeat_delay', 0.5):.2f}")
        delay_entry.pack(anchor="w")

        ctk.CTkLabel(main_frame, text="반복 사이의 대기시간",
                     font=ctk.CTkFont(size=11), text_color=COLORS["text_secondary"]).pack(anchor="w", pady=(5, 0))

        # 랜덤 대기시간
        delay_random_var = ctk.BooleanVar(value=getattr(action, 'repeat_delay_random', False))
        ctk.CTkCheckBox(main_frame, text="랜덤시간 활성화", variable=delay_random_var,
                        font=ctk.CTkFont(size=13)).pack(anchor="w", pady=(10, 5))

        delay_range_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        delay_range_frame.pack(anchor="w", pady=5)

        ctk.CTkLabel(delay_range_frame, text="±범위:", font=ctk.CTkFont(size=12)).pack(side="left")
        delay_range_entry = ctk.CTkEntry(delay_range_frame, width=100, height=32, font=ctk.CTkFont(size=13))
        delay_range_entry.insert(0, f"{getattr(action, 'repeat_delay_random_range', 0.3):.2f}")
        delay_range_entry.pack(side="left", padx=(5, 10))
        ctk.CTkLabel(delay_range_frame, text="초", font=ctk.CTkFont(size=12),
                     text_color=COLORS["text_secondary"]).pack(side="left")

        result = {"saved": False}

        def save():
            try:
                count = int(count_entry.get().strip())
                delay = float(delay_entry.get().strip().replace(',', '.'))
                delay_range = float(delay_range_entry.get().strip().replace(',', '.'))
                if count < 1:
                    from tkinter import messagebox
                    messagebox.showerror("오류", "반복 횟수는 1 이상이어야 합니다")
                    return
                if delay < 0:
                    from tkinter import messagebox
                    messagebox.showerror("오류", "대기시간은 0 이상이어야 합니다")
                    return
                if delay_range < 0:
                    from tkinter import messagebox
                    messagebox.showerror("오류", "±범위는 0 이상이어야 합니다")
                    return
                action.repeat_count = count
                action.repeat_delay = delay
                action.repeat_delay_random = delay_random_var.get()
                action.repeat_delay_random_range = delay_range
                result["saved"] = True
                dialog.destroy()
            except ValueError:
                from tkinter import messagebox
                messagebox.showerror("오류", "숫자를 입력하세요")

        btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_frame.pack(pady=10)
        ctk.CTkButton(btn_frame, text="저장", width=100, height=36,
                      font=ctk.CTkFont(size=13, weight="bold"), command=save).pack(side="left", padx=8)
        ctk.CTkButton(btn_frame, text="취소", width=100, height=36,
                      font=ctk.CTkFont(size=13, weight="bold"), fg_color=COLORS["bg_card"],
                      command=dialog.destroy).pack(side="left", padx=8)

        dialog.wait_window()

        if result["saved"]:
            self._modified = True
            self._update_action_buttons(action)
            logger.info(f"반복 설정: {action.repeat_count}회, 대기 {action.repeat_delay}초 (랜덤: {action.repeat_delay_random})")

    def _edit_action_name(self, action: Action):
        """액션 이름 수정"""
        current_name = action.description or ""
        dialog = ctk.CTkInputDialog(
            text=f"액션 이름을 입력하세요:\n현재: {current_name or '(없음)'}",
            title="액션 이름 수정",
        )
        result = dialog.get_input()

        if result is not None:  # 빈 문자열도 허용 (이름 삭제)
            action.description = result.strip()
            self._modified = True
            self._refresh_action_list()
            logger.info(f"액션 이름 수정: {action.description}")

    def _change_action_click_type(self, action: Action, new_type: str):
        """클릭 유형 변경 (click, double_click, right_click)"""
        if action.action_type == new_type:
            return  # 이미 같은 유형

        old_type = action.action_type
        action.action_type = new_type

        # 설명 자동 업데이트 (기존 설명이 클릭 유형 관련인 경우)
        type_names = {
            "click": "왼쪽 클릭",
            "double_click": "더블 클릭",
            "right_click": "오른쪽 클릭",
        }
        old_name = type_names.get(old_type, "")
        new_name = type_names.get(new_type, "")

        if action.description and old_name and old_name in action.description:
            action.description = action.description.replace(old_name, new_name)

        self._modified = True
        self._refresh_action_list()
        logger.info(f"클릭 유형 변경: {old_type} → {new_type}")

    def _detach_action(self, action: Action):
        """액션을 부모에서 분리하여 최상위로 이동"""
        parent = self._find_parent_action(action)
        if parent and action in parent.children:
            parent.children.remove(action)
            action.parent_id = None
            # 최상위에 추가
            self._sequence.actions.append(action)
            self._modified = True
            self._refresh_action_list()
            logger.info(f"액션 종속 해제: {action.action_id}")

    def _copy_action(self, action: Action):
        """액션 복사 (하위 액션 포함)"""
        import copy
        # 깊은 복사로 하위 액션까지 모두 복사
        set_action_clipboard(copy.deepcopy(action))
        logger.info(f"액션 복사됨: {action.action_id} (하위 {len(action.children)}개 포함)")

    def _paste_action(self, target_action: Action):
        """클립보드의 액션을 대상 액션 아래에 붙여넣기"""
        clipboard_content = get_action_clipboard()
        if clipboard_content is None:
            return

        import copy
        import uuid

        # 새 ID로 복사본 생성
        def assign_new_ids(act: Action, parent_id: int = None):
            """재귀적으로 새 ID 할당"""
            act.action_id = int(uuid.uuid4().int % 1000000000)  # 새 ID
            act.parent_id = parent_id
            for child in act.children:
                assign_new_ids(child, act.action_id)

        # 깊은 복사 후 새 ID 할당
        new_action = copy.deepcopy(clipboard_content)
        assign_new_ids(new_action, target_action.action_id)

        # 대상 액션의 자식으로 추가
        target_action.children.append(new_action)
        self._modified = True
        self._refresh_action_list()
        logger.info(f"액션 붙여넣기: {new_action.action_id} -> {target_action.action_id}")

    def _paste_action_top(self):
        """클립보드의 액션을 최상위에 붙여넣기"""
        clipboard_content = get_action_clipboard()
        if clipboard_content is None:
            return

        import copy
        import uuid

        # 새 ID로 복사본 생성
        def assign_new_ids(act: Action, parent_id: int = None):
            """재귀적으로 새 ID 할당"""
            act.action_id = int(uuid.uuid4().int % 1000000000)
            act.parent_id = parent_id
            for child in act.children:
                assign_new_ids(child, act.action_id)

        # 깊은 복사 후 새 ID 할당
        new_action = copy.deepcopy(clipboard_content)
        assign_new_ids(new_action, None)  # 최상위이므로 parent_id = None

        # 최상위에 추가
        self._sequence.actions.append(new_action)
        self._modified = True
        self._refresh_action_list()
        logger.info(f"액션 최상위에 붙여넣기: {new_action.action_id}")

    def _paste_as_monitoring_watch_action(self, action: Action):
        """클립보드의 액션을 현재 액션의 모니터링 액션으로 추가 (자식 포함)"""
        clipboard = get_action_clipboard()
        if clipboard is None:
            return

        def convert_to_monitor_action(act):
            """액션을 monitor_action 형식으로 변환 (모든 속성 포함)"""
            action_type = act.action_type
            monitor_action = None

            if action_type == "type":
                monitor_action = {"type": "텍스트 입력", "text": act.action_text or ""}
            elif action_type in ["hotkey", "key_press"]:
                monitor_action = {"type": "키 입력", "keys": act.action_keys or []}
            elif action_type in ["click", "double_click", "right_click"]:
                target_img = getattr(act, 'target_image', None)
                if target_img:
                    monitor_action = {"type": "이미지 클릭", "image": target_img, "click_type": action_type}
                else:
                    monitor_action = {"type": "마우스 클릭", "x": act.action_x, "y": act.action_y, "click_type": action_type}
            elif action_type == "scroll":
                monitor_action = {"type": "스크롤", "amount": getattr(act, 'scroll_amount', 0)}
            elif action_type == "drag":
                monitor_action = {
                    "type": "드래그",
                    "from_x": act.action_x,
                    "from_y": act.action_y,
                    "to_x": getattr(act, 'drag_to_x', 0),
                    "to_y": getattr(act, 'drag_to_y', 0)
                }
            else:
                monitor_action = {"type": "마우스 클릭", "x": act.action_x or 0, "y": act.action_y or 0, "click_type": "click"}

            # 공통 속성 복사
            if monitor_action:
                monitor_action["wait_after"] = getattr(act, 'wait_after', 0.5)
                monitor_action["wait_random"] = getattr(act, 'wait_random', False)
                monitor_action["wait_random_range"] = getattr(act, 'wait_random_range', 0.3)
                monitor_action["repeat_count"] = getattr(act, 'repeat_count', 1)
                monitor_action["repeat_delay"] = getattr(act, 'repeat_delay', 0.5)
                monitor_action["repeat_delay_random"] = getattr(act, 'repeat_delay_random', False)
                monitor_action["repeat_delay_random_range"] = getattr(act, 'repeat_delay_random_range', 0.3)
                monitor_action["typing_random"] = getattr(act, 'typing_random', False)
                monitor_action["typing_delay"] = getattr(act, 'typing_delay', 0.1)
                monitor_action["typing_delay_range"] = getattr(act, 'typing_delay_range', 0.05)
                monitor_action["confidence"] = getattr(act, 'confidence', 0.65)
                monitor_action["search_radius"] = getattr(act, 'search_radius', 0)

            return monitor_action

        def collect_all_actions(act):
            """액션과 모든 자식 액션을 수집"""
            result = [act]
            for child in getattr(act, 'children', []) or []:
                result.extend(collect_all_actions(child))
            return result

        # 클립보드 액션과 모든 자식 수집
        all_actions = collect_all_actions(clipboard)
        monitor_actions = []

        for act in all_actions:
            ma = convert_to_monitor_action(act)
            if ma:
                monitor_actions.append(ma)

        if not monitor_actions:
            return

        # monitoring_watches가 없으면 생성
        if not hasattr(action, 'monitoring_watches') or action.monitoring_watches is None:
            action.monitoring_watches = []

        # 감시 항목이 여러 개면 선택, 1개면 바로 추가, 없으면 새로 생성
        if len(action.monitoring_watches) > 1:
            from tkinter import simpledialog
            # 선택 다이얼로그 표시
            watch_options = []
            for i, w in enumerate(action.monitoring_watches):
                img_name = Path(w.get("image", "")).name[:15] if w.get("image") else "이미지 없음"
                action_count = len(w.get("monitor_actions", []))
                watch_options.append(f"{i+1}. {img_name} ({action_count}개 액션)")

            selected = simpledialog.askinteger(
                "감시 항목 선택",
                f"모니터링 액션을 추가할 감시 항목을 선택하세요:\n\n" + "\n".join(watch_options) + "\n\n번호 입력 (1~{})".format(len(action.monitoring_watches)),
                minvalue=1, maxvalue=len(action.monitoring_watches)
            )
            if selected is None:
                return
            watch_idx = selected - 1
        elif len(action.monitoring_watches) == 1:
            watch_idx = 0
        else:
            # 새 watch 항목 생성
            new_watch = {
                "image": getattr(clipboard, 'target_image', None),
                "goto_index": -1,
                "monitor_actions": monitor_actions,
                "confidence": 0.65,
            }
            action.monitoring_watches.append(new_watch)
            logger.info(f"모니터링 액션 추가 (새 감시 항목): {len(monitor_actions)}개")
            action.is_monitoring_mode = True
            self._modified = True
            self._refresh_action_list()
            return

        # 선택된 감시 항목에 추가
        if "monitor_actions" not in action.monitoring_watches[watch_idx]:
            action.monitoring_watches[watch_idx]["monitor_actions"] = []
        action.monitoring_watches[watch_idx]["monitor_actions"].extend(monitor_actions)

        logger.info(f"모니터링 액션 추가 (감시 {watch_idx+1}): {len(monitor_actions)}개")
        action.is_monitoring_mode = True
        self._modified = True
        self._refresh_action_list()

    def _paste_as_monitor_action_seq(self, action_index: int, watch_index: int):
        """클립보드의 액션을 모니터링 액션으로 붙여넣기 (Sequence용)"""
        clipboard = get_action_clipboard()
        if clipboard is None:
            return

        if action_index >= len(self._sequence.actions):
            return

        action = self._sequence.actions[action_index]
        watches = getattr(action, 'monitoring_watches', [])
        if watch_index >= len(watches):
            return

        # Action을 monitor_action 형식으로 변환 (모든 속성 포함)
        action_type = clipboard.action_type
        monitor_action = None

        if action_type == "type":
            monitor_action = {"type": "텍스트 입력", "text": clipboard.action_text or ""}
        elif action_type in ["hotkey", "key_press"]:
            monitor_action = {"type": "키 입력", "keys": clipboard.action_keys or []}
        elif action_type in ["click", "double_click", "right_click"]:
            if getattr(clipboard, 'target_image', None):
                monitor_action = {"type": "이미지 클릭", "image": clipboard.target_image, "click_type": action_type}
            else:
                monitor_action = {"type": "마우스 클릭", "x": clipboard.action_x, "y": clipboard.action_y, "click_type": action_type}
        elif action_type == "scroll":
            monitor_action = {"type": "스크롤", "amount": getattr(clipboard, 'scroll_amount', 0)}
        elif action_type == "drag":
            monitor_action = {
                "type": "드래그",
                "from_x": clipboard.action_x,
                "from_y": clipboard.action_y,
                "to_x": getattr(clipboard, 'drag_to_x', 0),
                "to_y": getattr(clipboard, 'drag_to_y', 0)
            }
        elif clipboard.target_image:
            monitor_action = {"type": "이미지 클릭", "image": clipboard.target_image, "click_type": "click"}

        if monitor_action:
            # 공통 속성 복사
            monitor_action["wait_after"] = getattr(clipboard, 'wait_after', 0.5)
            monitor_action["wait_random"] = getattr(clipboard, 'wait_random', False)
            monitor_action["wait_random_range"] = getattr(clipboard, 'wait_random_range', 0.3)
            monitor_action["repeat_count"] = getattr(clipboard, 'repeat_count', 1)
            monitor_action["repeat_delay"] = getattr(clipboard, 'repeat_delay', 0.5)
            monitor_action["repeat_delay_random"] = getattr(clipboard, 'repeat_delay_random', False)
            monitor_action["repeat_delay_random_range"] = getattr(clipboard, 'repeat_delay_random_range', 0.3)
            monitor_action["typing_random"] = getattr(clipboard, 'typing_random', False)
            monitor_action["typing_delay"] = getattr(clipboard, 'typing_delay', 0.1)
            monitor_action["typing_delay_range"] = getattr(clipboard, 'typing_delay_range', 0.05)
            monitor_action["confidence"] = getattr(clipboard, 'confidence', 0.65)
            monitor_action["search_radius"] = getattr(clipboard, 'search_radius', 0)

            # monitor_actions 리스트에 추가 (없으면 생성)
            if "monitor_actions" not in watches[watch_index]:
                watches[watch_index]["monitor_actions"] = []
            watches[watch_index]["monitor_actions"].append(monitor_action)
            self._modified = True
            logger.info(f"모니터링 액션 추가: 액션{action_index+1} 감시{watch_index+1} - {monitor_action['type']}")

    def _add_text_action(self):
        """텍스트 입력 액션 추가"""
        dialog = ctk.CTkInputDialog(
            text="입력할 텍스트를 입력하세요:",
            title="텍스트 액션 추가",
        )
        text = dialog.get_input()

        if text:
            from ..database.models import Action
            new_action = Action(
                action_type="type",
                text=text,
                timestamp=0,
                wait_after=0.5,
            )
            self._sequence.actions.append(new_action)
            self._modified = True
            self._refresh_action_list()
            logger.info(f"텍스트 액션 추가: {text[:30]}...")

    def _add_key_action(self):
        """키 입력 액션 추가"""
        dialog = KeyInputDialog(self)
        key = dialog.get_key()

        if key:
            from ..database.models import Action
            new_action = Action(
                action_type="hotkey",
                keys=[key.lower().strip()],
                timestamp=0,
                wait_after=0.5,
            )
            self._sequence.actions.append(new_action)
            self._modified = True
            self._refresh_action_list()
            logger.info(f"키 액션 추가: {key}")

    def _add_mouse_action(self):
        """마우스 클릭 액션 추가"""
        from tkinter import simpledialog
        from ..database.models import Action

        # X 좌표 입력
        x = simpledialog.askinteger(
            "마우스 입력",
            "클릭할 X 좌표를 입력하세요:",
            parent=self,
            minvalue=0,
            maxvalue=9999
        )
        if x is None:
            return

        # Y 좌표 입력
        y = simpledialog.askinteger(
            "마우스 입력",
            "클릭할 Y 좌표를 입력하세요:",
            parent=self,
            minvalue=0,
            maxvalue=9999
        )
        if y is None:
            return

        new_action = Action(
            action_type="click",
            x=x,
            y=y,
            timestamp=0,
            wait_after=0.5,
        )
        self._sequence.actions.append(new_action)
        self._modified = True
        self._refresh_action_list()
        logger.info(f"마우스 클릭 액션 추가: ({x}, {y})")

    def _add_image_action(self):
        """이미지 클릭 액션 추가"""
        from tkinter import filedialog
        import shutil
        import uuid
        from ..database.models import Action

        # 이미지 파일 선택
        image_path = filedialog.askopenfilename(
            title="클릭할 이미지 선택",
            filetypes=[
                ("이미지 파일", "*.png *.jpg *.jpeg *.bmp *.gif"),
                ("모든 파일", "*.*"),
            ],
            initialdir=str(DATA_DIR / "templates"),
        )

        if not image_path:
            return

        try:
            # templates 폴더에 이미지 복사
            templates_dir = DATA_DIR / "templates"
            templates_dir.mkdir(parents=True, exist_ok=True)
            new_ext = Path(image_path).suffix
            new_filename = f"img_{uuid.uuid4().hex[:8]}{new_ext}"
            dest_path = templates_dir / new_filename
            shutil.copy2(image_path, dest_path)

            new_action = Action(
                action_type="click",
                target_image=str(dest_path),
                timestamp=0,
                wait_after=0.5,
            )
            self._sequence.actions.append(new_action)
            self._modified = True
            self._refresh_action_list()
            logger.info(f"이미지 클릭 액션 추가: {dest_path}")
        except Exception as e:
            from tkinter import messagebox
            logger.error(f"이미지 액션 추가 실패: {e}")
            messagebox.showerror("오류", f"이미지 추가 실패: {e}")

    def _add_screenshot_action(self):
        """스크린샷 찍어서 이미지 액션으로 추가"""
        from tkinter import filedialog, messagebox
        from ..database.models import Action
        import pyautogui
        from pathlib import Path

        # 저장 경로 선택
        file_path = filedialog.asksaveasfilename(
            title="스크린샷 저장",
            defaultextension=".png",
            filetypes=[("PNG 파일", "*.png"), ("모든 파일", "*.*")],
            initialdir=str(Path.home() / "Pictures"),
        )

        if not file_path:
            return

        try:
            # 스크린샷 찍기
            screenshot = pyautogui.screenshot()
            screenshot.save(file_path)
            logger.info(f"스크린샷 저장: {file_path}")

            # 이미지 액션으로 추가할지 확인
            if messagebox.askyesno("스크린샷 저장 완료", f"스크린샷이 저장되었습니다.\n\n{file_path}\n\n이 이미지를 클릭 액션으로 추가할까요?"):
                # templates 폴더에 복사
                import shutil
                templates_dir = DATA_DIR / "templates"
                templates_dir.mkdir(parents=True, exist_ok=True)

                # 파일명 생성
                existing = list(templates_dir.glob("screenshot_*.png"))
                new_num = len(existing) + 1
                new_filename = f"screenshot_{new_num:04d}.png"
                dest_path = templates_dir / new_filename
                shutil.copy2(file_path, dest_path)

                # 액션 추가
                new_action = Action(
                    action_type="click",
                    target_image=str(dest_path),
                    timestamp=0,
                    wait_after=0.5,
                )
                self._sequence.actions.append(new_action)
                self._modified = True
                self._refresh_action_list()
                logger.info(f"스크린샷 액션 추가: {dest_path}")

        except Exception as e:
            logger.error(f"스크린샷 실패: {e}")
            messagebox.showerror("오류", f"스크린샷 실패: {e}")

    def _flatten_children(self):
        """선택된 액션의 하위 액션들을 같은 레벨로 해체"""
        from tkinter import messagebox

        # 선택된 액션 확인
        if not hasattr(self, '_selected_action') or self._selected_action is None:
            messagebox.showwarning("경고", "먼저 하위 액션을 해체할 액션을 선택하세요.")
            return

        action = self._selected_action
        if not action.children:
            messagebox.showinfo("알림", "이 액션에는 하위 액션이 없습니다.")
            return

        # 확인
        child_count = len(action.children)
        if not messagebox.askyesno("하위 해체", f"{child_count}개의 하위 액션을 같은 레벨로 해체할까요?\n\n부모 액션 다음에 배치됩니다."):
            return

        try:
            # 부모 인덱스 찾기
            idx = self._sequence.actions.index(action)

            # 자식들을 부모 다음에 삽입
            children = list(action.children)  # 복사
            action.children = []  # 부모에서 자식 제거

            for i, child in enumerate(children):
                child.parent_id = None  # 부모 참조 제거
                self._sequence.actions.insert(idx + 1 + i, child)

            self._modified = True
            self._refresh_action_list()
            logger.info(f"하위 해체 완료: {child_count}개 액션")
            messagebox.showinfo("완료", f"{child_count}개의 하위 액션이 해체되었습니다.")

        except ValueError:
            messagebox.showerror("오류", "액션을 찾을 수 없습니다.")
        except Exception as e:
            logger.error(f"하위 해체 실패: {e}")
            messagebox.showerror("오류", f"하위 해체 실패: {e}")

    def _select_action(self, action: Action):
        """액션 선택 (초록색 하이라이트)"""
        # 이전 선택 해제
        if self._selected_action is not None and self._selected_action.action_id in self._action_widgets:
            old_widget = self._action_widgets[self._selected_action.action_id].get("widget")
            if old_widget:
                old_widget.configure(fg_color=COLORS["bg_dark"])

        # 같은 액션 다시 클릭하면 선택 해제
        if self._selected_action is not None and self._selected_action.action_id == action.action_id:
            self._selected_action = None
            logger.debug("액션 선택 해제")
            return

        # 새 액션 선택
        self._selected_action = action
        if action.action_id in self._action_widgets:
            new_widget = self._action_widgets[action.action_id].get("widget")
            if new_widget:
                new_widget.configure(fg_color="#2e7d32")  # 초록색
        logger.debug(f"액션 선택: {action.action_id}")

    def _randomize_all_delays(self):
        """전체 액션의 랜덤 대기시간 체크 토글"""
        # 현재 상태 확인 (하나라도 False면 전체 True로, 전부 True면 전체 False로)
        all_random = True
        def check_random(actions):
            nonlocal all_random
            for action in actions:
                if not getattr(action, 'wait_random', False):
                    all_random = False
                    return
                if action.children:
                    check_random(action.children)
        check_random(self._sequence.actions)

        # 토글 적용
        new_state = not all_random
        count = 0
        def apply_random(actions):
            nonlocal count
            for action in actions:
                action.wait_random = new_state
                if new_state and (not hasattr(action, 'wait_random_range') or action.wait_random_range is None or action.wait_random_range == 0):
                    action.wait_random_range = 0.3
                count += 1
                if action.children:
                    apply_random(action.children)

        apply_random(self._sequence.actions)

        self._modified = True
        self._refresh_action_list()
        logger.info(f"전체 랜덤 {'활성화' if new_state else '비활성화'}: {count}개 액션")

    def _toggle_all_children(self):
        """모든 액션을 1번 액션의 하위로 종속/해제 토글"""
        from tkinter import messagebox

        if len(self._sequence.actions) < 2:
            return

        first_action = self._sequence.actions[0]

        # 현재 상태 확인: 1번 액션에 자식이 있으면 해제, 없으면 종속
        if first_action.children and len(first_action.children) > 0:
            # 해제 확인
            if not messagebox.askyesno("하위 종속 해제", f"{len(first_action.children)}개 액션의 하위 종속을 해제하시겠습니까?"):
                return

            # 해제: 1번 액션의 자식들을 같은 레벨로 이동
            children = list(first_action.children)
            first_action.children = []

            for child in children:
                child.parent_id = None
                self._sequence.actions.append(child)

            logger.info(f"하위 종속 해제: {len(children)}개 액션")
        else:
            # 종속 확인
            children_count = len(self._sequence.actions) - 1
            if not messagebox.askyesno("하위 종속", f"{children_count}개 액션을 1번 액션의 하위로 종속하시겠습니까?"):
                return

            # 종속: 2번 이후 모든 액션을 1번의 자식으로
            children_to_move = self._sequence.actions[1:]
            self._sequence.actions = [first_action]

            for child in children_to_move:
                child.parent_id = first_action.action_id
                first_action.children.append(child)

            # 접힌 상태 해제 (보이도록)
            self._collapsed_items.discard(first_action.action_id)

            logger.info(f"하위 종속: {len(children_to_move)}개 액션을 1번 아래로")

        self._modified = True
        self._refresh_action_list()

    def _toggle_all_collapse(self):
        """모든 액션 접기/펼치기"""
        if self._all_collapsed:
            # 모두 펼치기
            self._collapsed_items.clear()
            self._all_collapsed = False
            self._collapse_btn.configure(text="모두 접기")
        else:
            # 모두 접기 (자식이 있는 모든 액션의 action_id 추가)
            self._collapsed_items = set()
            for action in self._sequence.actions:
                if action.children:
                    self._collapsed_items.add(action.action_id)
                self._collect_parent_action_ids(action)
            self._all_collapsed = True
            self._collapse_btn.configure(text="모두 펼치기")
        self._refresh_action_list()

    def _collect_parent_action_ids(self, action: Action):
        """자식이 있는 모든 액션의 ID 수집 (재귀)"""
        for child in action.children:
            if child.children:
                self._collapsed_items.add(child.action_id)
            self._collect_parent_action_ids(child)

    def _toggle_item_collapse(self, action_id: str):
        """개별 액션 접기/펼치기 (새로고침 없이 visibility 토글)"""
        widget_data = self._action_widgets.get(action_id)
        if not widget_data:
            return

        children_container = widget_data.get("children_container")
        if not children_container:
            return

        if action_id in self._collapsed_items:
            # 펼치기
            self._collapsed_items.discard(action_id)
            children_container.pack(fill="x")
        else:
            # 접기
            self._collapsed_items.add(action_id)
            children_container.pack_forget()

        # 토글 버튼 텍스트 업데이트
        item_widget = widget_data.get("widget")
        if item_widget:
            try:
                # content > toggle_btn 찾기
                for child in item_widget.winfo_children():
                    for subchild in child.winfo_children():
                        if isinstance(subchild, ctk.CTkButton):
                            text = subchild.cget("text")
                            if text in ("▶", "▼"):
                                new_text = "▼" if action_id not in self._collapsed_items else "▶"
                                subchild.configure(text=new_text)
                                break
            except Exception:
                pass

    def _on_drag_start(self, event, action: Action, widget):
        """드래그 시작"""
        self._drag_data["action"] = action
        self._drag_data["widget"] = widget
        self._drag_data["start_y"] = event.y_root
        widget.configure(fg_color=COLORS["accent_blue"])

    def _on_drag_motion(self, event):
        """드래그 중"""
        if not self._drag_data["action"]:
            return

        # 마우스 위치에서 드롭 대상 찾기
        target_action = None
        for action_id, data in self._action_widgets.items():
            if action_id == self._drag_data["action"].action_id:
                continue  # 자기 자신 제외

            widget = data["widget"]
            try:
                # 위젯의 절대 위치 계산
                wy = widget.winfo_rooty()
                wh = widget.winfo_height()

                # 마우스가 위젯 영역 안에 있는지 확인
                if wy <= event.y_root <= wy + wh:
                    target_action = data["action"]
                    break
            except (tk.TclError, KeyError, AttributeError):
                continue

        # 이전 드롭 대상 하이라이트 제거
        if self._drop_target and self._drop_target != target_action:
            try:
                old_data = self._action_widgets.get(self._drop_target.action_id)
                if old_data:
                    old_data["widget"].configure(fg_color=COLORS["bg_dark"])
            except (tk.TclError, KeyError, AttributeError):
                pass

        # 새 드롭 대상 하이라이트
        self._drop_target = target_action
        if target_action:
            try:
                target_data = self._action_widgets.get(target_action.action_id)
                if target_data:
                    target_data["widget"].configure(fg_color=COLORS["success"])
            except (tk.TclError, KeyError, AttributeError):
                pass

    def _on_drag_release(self, event):
        """드래그 종료"""
        dragged_action = self._drag_data["action"]
        target_action = self._drop_target

        # 원래 색상 복원
        if self._drag_data["widget"]:
            self._drag_data["widget"].configure(fg_color=COLORS["bg_dark"])
        if target_action:
            try:
                target_data = self._action_widgets.get(target_action.action_id)
                if target_data:
                    target_data["widget"].configure(fg_color=COLORS["bg_dark"])
            except (tk.TclError, KeyError, AttributeError):
                pass

        # 드롭 처리
        if dragged_action and target_action and dragged_action != target_action:
            self._move_action_to_target(dragged_action, target_action)

        # 상태 초기화
        self._drag_data = {"action": None, "widget": None, "start_y": 0}
        self._drop_target = None

    def _move_action_to_target(self, dragged: Action, target: Action):
        """드래그한 액션을 대상의 자식으로 이동"""
        # 순환 참조 방지: 드래그한 것이 대상의 조상인지 확인
        if self._is_ancestor_action(dragged, target):
            return

        # 현재 위치에서 제거
        if dragged in self._sequence.actions:
            # 최상위에서 제거하지만 리스트에는 유지 (parent_id로 관리)
            pass
        else:
            parent = self._find_parent_action(dragged)
            if parent and dragged in parent.children:
                parent.children.remove(dragged)

        # 대상의 자식으로 추가
        dragged.parent_id = target.action_id
        if dragged not in target.children:
            target.children.append(dragged)

        self._modified = True
        self._refresh_action_list()
        logger.info(f"액션을 '{target.action_id}'의 하위로 이동")

    def _is_ancestor_action(self, potential_ancestor: Action, target: Action) -> bool:
        """potential_ancestor가 target의 조상인지 확인"""
        def check_children(action):
            if action == target:
                return True
            for child in action.children:
                if check_children(child):
                    return True
            return False
        return check_children(potential_ancestor)

    def _find_parent_action(self, target: Action) -> Optional[Action]:
        """대상 액션의 부모 액션 찾기"""
        for action in self._sequence.actions:
            parent = self._find_parent_in_tree_action(action, target)
            if parent:
                return parent
        return None

    def _find_parent_in_tree_action(self, action: Action, target: Action) -> Optional[Action]:
        """트리에서 대상의 부모 찾기 (재귀)"""
        if target in action.children:
            return action
        for child in action.children:
            parent = self._find_parent_in_tree_action(child, target)
            if parent:
                return parent
        return None

    def _on_close(self):
        """닫기"""
        if self._modified:
            from tkinter import messagebox
            if messagebox.askyesno("저장 확인", "수정된 내용이 있습니다. 저장하시겠습니까?"):
                self._save_sequence()
        self.destroy()


class PlayerView(BaseView):
    """
    실행 화면 클래스

    동작 재현 기능을 위한 UI를 제공합니다.
    """

    def __init__(self, parent, **kwargs):
        """실행 뷰 초기화"""
        super().__init__(parent, **kwargs)

        self._action_player = get_action_player()
        self._db = get_db()
        self._rule_executor: Optional[RuleExecutor] = None

        self._selected_sequence: Optional[Sequence] = None
        self._selected_plan: Optional[AutomationPlan] = None
        self._sequences: List[Sequence] = []
        self._automation_plans: List[AutomationPlan] = []
        self._selected_item_widget = None  # 선택된 항목 위젯

        self._setup_ui()
        self._setup_callbacks()
        self._load_sequences()
        self._load_automation_plans()

    def _setup_ui(self) -> None:
        """UI 구성"""
        # 스크롤 가능한 메인 컨테이너
        self._scroll_frame = ctk.CTkScrollableFrame(
            self,
            fg_color="transparent",
        )
        self._scroll_frame.pack(fill="both", expand=True)

        # 상단: 2x2 그리드 영역
        grid_frame = ctk.CTkFrame(self._scroll_frame, fg_color="transparent")
        grid_frame.pack(fill="both", expand=True, padx=8, pady=8)

        # 그리드 설정 (2행 2열, 균등 비율)
        grid_frame.grid_columnconfigure(0, weight=1, uniform="col")
        grid_frame.grid_columnconfigure(1, weight=1, uniform="col")
        grid_frame.grid_rowconfigure(0, weight=1, uniform="row")
        grid_frame.grid_rowconfigure(1, weight=1, uniform="row")

        # 좌상단: 재생 선택
        select_frame = ctk.CTkFrame(grid_frame, fg_color="transparent")
        select_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 4), pady=(0, 4))
        self._setup_sequence_selection(select_frame)

        # 우상단: 실행 설정
        control_frame = ctk.CTkFrame(grid_frame, fg_color="transparent")
        control_frame.grid(row=0, column=1, sticky="nsew", padx=(4, 0), pady=(0, 4))
        self._setup_control_panel(control_frame)

        # 좌하단: 실행 상태
        status_frame = ctk.CTkFrame(grid_frame, fg_color="transparent")
        status_frame.grid(row=1, column=0, sticky="nsew", padx=(0, 4), pady=(4, 0))
        self._setup_status_panel(status_frame)

        # 우하단: 실행 컨트롤 버튼
        btn_frame = ctk.CTkFrame(grid_frame, fg_color="transparent")
        btn_frame.grid(row=1, column=1, sticky="nsew", padx=(4, 0), pady=(4, 0))
        self._setup_action_buttons(btn_frame)

    def _setup_sequence_selection(self, parent) -> None:
        """재생 선택 패널 구성"""
        self._selection_card = self.create_card(parent, title="재생 선택")
        self._selection_card.pack(fill="both", expand=True)

        # 재생 목록
        self._sequence_frame = ctk.CTkScrollableFrame(
            self._selection_card,
            height=200,
            fg_color=COLORS["bg_dark"],
            corner_radius=8,
        )
        self._sequence_frame.pack(fill="both", expand=True, padx=15, pady=(0, 10))

        # 실행 버튼 프레임
        play_btn_frame = ctk.CTkFrame(self._selection_card, fg_color="transparent")
        play_btn_frame.pack(fill="x", padx=15, pady=(0, 8))

        self._play_btn = ctk.CTkButton(
            play_btn_frame,
            text="▶ 실행",
            command=self._on_play,
            width=90,
            height=36,
            fg_color=COLORS["success"],
            hover_color="#2ea44f",
            text_color="white",
            font=ctk.CTkFont(size=13, weight="bold"),
            corner_radius=6,
            state="disabled",
        )
        self._play_btn.pack(side="left", padx=(0, 6))

        self._pause_btn = ctk.CTkButton(
            play_btn_frame,
            text="⏸ 일시정지",
            command=self._on_pause,
            width=90,
            height=36,
            fg_color=COLORS["warning"],
            hover_color="#b8860b",
            text_color="white",
            font=ctk.CTkFont(size=13, weight="bold"),
            corner_radius=6,
            state="disabled",
        )
        self._pause_btn.pack(side="left", padx=(0, 6))

        self._stop_btn = ctk.CTkButton(
            play_btn_frame,
            text="⏹ 중지",
            command=self._on_stop,
            width=90,
            height=36,
            fg_color=COLORS["error"],
            hover_color="#c62828",
            text_color="white",
            font=ctk.CTkFont(size=13, weight="bold"),
            corner_radius=6,
            state="disabled",
        )
        self._stop_btn.pack(side="left")

        # 새로고침 버튼 프레임
        btn_frame = ctk.CTkFrame(self._selection_card, fg_color="transparent")
        btn_frame.pack(fill="x", padx=15, pady=(0, 10))

        self.create_button(
            btn_frame,
            text="새로고침",
            command=self._load_sequences,
            style="secondary",
            width=100,
        ).pack(side="right")

    def _setup_control_panel(self, parent) -> None:
        """컨트롤 패널 구성"""
        self._control_card = self.create_card(parent, title="실행 설정")
        self._control_card.pack(fill="both", expand=True)

        # 선택 정보 프레임
        info_frame = ctk.CTkFrame(
            self._control_card,
            fg_color=COLORS["bg_dark"],
            corner_radius=8,
        )
        info_frame.pack(fill="x", padx=15, pady=(0, 15))

        self._selected_label = self.create_label(
            info_frame,
            text="선택된 재생: 없음",
            style="body",
        )
        self._selected_label.pack(side="left", padx=15, pady=12)

        self._action_count_label = self.create_label(
            info_frame,
            text="",
            style="caption",
        )
        self._action_count_label.pack(side="left", padx=5, pady=12)

        # 설정 프레임
        settings_frame = ctk.CTkFrame(self._control_card, fg_color="transparent")
        settings_frame.pack(fill="x", padx=15, pady=(0, 15))

        # 실행 속도
        speed_frame = ctk.CTkFrame(settings_frame, fg_color="transparent")
        speed_frame.pack(fill="x", pady=5)

        speed_label_frame = ctk.CTkFrame(speed_frame, fg_color="transparent")
        speed_label_frame.pack(side="left")

        self.create_label(speed_label_frame, text=PLAYER["speed"], style="caption").pack(anchor="w")
        ctk.CTkLabel(
            speed_label_frame,
            text="0.5x=느림, 2.0x=빠름",
            font=ctk.CTkFont(size=9),
            text_color=COLORS["text_muted"],
        ).pack(anchor="w")

        self._speed_var = ctk.DoubleVar(value=1.0)
        self._speed_slider = ctk.CTkSlider(
            speed_frame,
            from_=0.5,
            to=2.0,
            variable=self._speed_var,
            width=180,
            height=16,
            fg_color=COLORS["bg_dark"],
            progress_color=COLORS["accent"],
            button_color=COLORS["text_primary"],
            button_hover_color=COLORS["accent_hover"],
        )
        self._speed_slider.pack(side="left", padx=10)

        self._speed_label = self.create_label(speed_frame, text="1.0x", style="body")
        self._speed_label.pack(side="left")
        self._speed_var.trace_add("write", self._update_speed_label)

        # 반복 횟수
        repeat_frame = ctk.CTkFrame(settings_frame, fg_color="transparent")
        repeat_frame.pack(fill="x", pady=5)

        repeat_label_frame = ctk.CTkFrame(repeat_frame, fg_color="transparent")
        repeat_label_frame.pack(side="left")

        self.create_label(repeat_label_frame, text=PLAYER["repeat_count"], style="caption").pack(anchor="w")
        ctk.CTkLabel(
            repeat_label_frame,
            text="재생 전체를 몇 번 반복",
            font=ctk.CTkFont(size=9),
            text_color=COLORS["text_muted"],
        ).pack(anchor="w")

        self._repeat_var = ctk.StringVar(value="1")
        self._repeat_entry = ctk.CTkEntry(
            repeat_frame,
            textvariable=self._repeat_var,
            width=60,
            height=32,
            fg_color=COLORS["bg_dark"],
            border_color=COLORS["border"],
            text_color=COLORS["text_primary"],
        )
        self._repeat_entry.pack(side="left", padx=10)

        # 재생횟수 저장 버튼
        self._repeat_save_btn = ctk.CTkButton(
            repeat_frame,
            text="저장",
            width=50,
            height=32,
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=self._save_repeat_count,
        )
        self._repeat_save_btn.pack(side="left", padx=5)

        self._infinite_var = ctk.BooleanVar(value=False)
        self._infinite_check = ctk.CTkCheckBox(
            repeat_frame,
            text=PLAYER["repeat_infinite"],
            variable=self._infinite_var,
            command=self._on_infinite_toggle,
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            text_color=COLORS["text_secondary"],
        )
        self._infinite_check.pack(side="left", padx=10)

    def _setup_status_panel(self, parent) -> None:
        """상태 패널 구성"""
        self._status_card = self.create_card(parent, title="실행 상태")
        self._status_card.pack(fill="both", expand=True)

        # 상태 표시 헤더
        status_header = ctk.CTkFrame(self._status_card, fg_color="transparent")
        status_header.pack(fill="x", padx=15, pady=(0, 10))

        self._status_indicator = ctk.CTkLabel(
            status_header,
            text="●",
            font=ctk.CTkFont(size=20),
            text_color=COLORS["text_secondary"],
        )
        self._status_indicator.pack(side="left")

        self._status_label = self.create_label(
            status_header,
            text=PLAYER["ready"],
            style="title",
        )
        self._status_label.pack(side="left", padx=10)

        # 진행률 프레임
        progress_frame = ctk.CTkFrame(
            self._status_card,
            fg_color=COLORS["bg_dark"],
            corner_radius=8,
        )
        progress_frame.pack(fill="x", padx=15, pady=(0, 15))

        # 스텝 레이블
        self._step_label = self.create_label(
            progress_frame,
            text=f"{PLAYER['current_step']}: 0 / 0",
            style="body",
        )
        self._step_label.pack(pady=(15, 10))

        # 진행 바
        self._progress_bar = ctk.CTkProgressBar(
            progress_frame,
            width=500,
            height=12,
            fg_color=COLORS["border"],
            progress_color=COLORS["accent"],
            corner_radius=6,
        )
        self._progress_bar.pack(pady=(0, 15), padx=20)
        self._progress_bar.set(0)

        # 현재 액션 정보
        action_frame = ctk.CTkFrame(self._status_card, fg_color="transparent")
        action_frame.pack(fill="x", padx=15, pady=(0, 10))

        self.create_label(action_frame, text="현재 액션:", style="caption").pack(
            anchor="w"
        )

        self._current_action_label = self.create_label(
            action_frame,
            text="-",
            style="body",
        )
        self._current_action_label.pack(anchor="w", padx=10)

    def _setup_action_buttons(self, parent) -> None:
        """실행 컨트롤 버튼 패널 구성"""
        card = self.create_card(parent, title="실행 컨트롤")
        card.pack(fill="both", expand=True)

        content = ctk.CTkFrame(card, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=15, pady=15)

        # 안내 문구
        ctk.CTkLabel(
            content,
            text="선택한 자동화를 실행합니다",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["text_muted"],
        ).pack(anchor="w", pady=(0, 15))

        # 버튼 프레임
        btn_frame = ctk.CTkFrame(content, fg_color="transparent")
        btn_frame.pack(fill="x")

        self._play_btn2 = ctk.CTkButton(
            btn_frame,
            text="▶ 실행",
            command=self._on_play,
            width=100,
            height=40,
            fg_color=COLORS["success"],
            hover_color="#2ea44f",
            text_color="white",
            font=ctk.CTkFont(size=14, weight="bold"),
            corner_radius=8,
            state="disabled",
        )
        self._play_btn2.pack(side="left", padx=(0, 8))

        self._pause_btn2 = ctk.CTkButton(
            btn_frame,
            text="⏸ 일시정지",
            command=self._on_pause,
            width=100,
            height=40,
            fg_color=COLORS["warning"],
            hover_color="#b8860b",
            text_color="white",
            font=ctk.CTkFont(size=14, weight="bold"),
            corner_radius=8,
            state="disabled",
        )
        self._pause_btn2.pack(side="left", padx=(0, 8))

        self._stop_btn2 = ctk.CTkButton(
            btn_frame,
            text="⏹ 중지",
            command=self._on_stop,
            width=100,
            height=40,
            fg_color=COLORS["error"],
            hover_color="#c62828",
            text_color="white",
            font=ctk.CTkFont(size=14, weight="bold"),
            corner_radius=8,
            state="disabled",
        )
        self._stop_btn2.pack(side="left")

    def _setup_callbacks(self) -> None:
        """콜백 설정"""
        self._action_player.set_callbacks(
            on_progress=self._on_progress,
            on_complete=self._on_complete,
            on_action_start=self._on_action_start,
        )

    def _load_sequences(self) -> None:
        """재생 목록 로드"""
        for widget in self._sequence_frame.winfo_children():
            widget.destroy()

        self._selected_item_widget = None  # 선택 상태 초기화
        self._sequences = self._db.get_all_sequences()
        self._load_automation_plans()

        has_items = False

        # 자동화 계획 먼저 표시
        if self._automation_plans:
            # 섹션 헤더 (라벨 + 정리 버튼)
            section_header = ctk.CTkFrame(self._sequence_frame, fg_color="transparent")
            section_header.pack(fill="x", padx=10, pady=(10, 5))

            ctk.CTkLabel(
                section_header,
                text="[ 자동화 계획 ]",
                font=ctk.CTkFont(size=12, weight="bold"),
                text_color=COLORS["accent"],
            ).pack(side="left")

            for plan in self._automation_plans:
                self._create_plan_item(plan)
            has_items = True

        # 일반 재생 표시
        if self._sequences:
            section_label = ctk.CTkLabel(
                self._sequence_frame,
                text="[ 일반 재생 ]",
                font=ctk.CTkFont(size=12, weight="bold"),
                text_color=COLORS["text_secondary"],
            )
            section_label.pack(anchor="w", padx=10, pady=(15, 5))

            for sequence in self._sequences:
                self._create_sequence_item(sequence)
            has_items = True

        if not has_items:
            empty_label = ctk.CTkLabel(
                self._sequence_frame,
                text="📋 실행할 재생가 없습니다\n\n사용 방법:\n1. 녹화 탭에서 화면 녹화\n2. 분석 탭에서 동작 분석\n3. 여기서 실행",
                text_color=COLORS["text_secondary"],
                font=ctk.CTkFont(size=12),
                justify="center",
            )
            empty_label.pack(expand=True, pady=30)

    def _load_automation_plans(self) -> None:
        """자동화 계획 목록 로드"""
        self._automation_plans = []

        if not PLANS_DIR.exists():
            return

        templates_dir = DATA_DIR / "templates"
        for plan_file in PLANS_DIR.glob("*.json"):
            try:
                with open(plan_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                plan = AutomationPlan.from_dict(data, templates_dir=templates_dir)
                if plan.user_verified:  # 승인된 계획만 표시
                    self._automation_plans.append(plan)
            except Exception as e:
                logger.error(f"자동화 계획 로드 실패 ({plan_file}): {e}")

        # 최신순 정렬
        self._automation_plans.sort(key=lambda p: p.created_at, reverse=True)
        logger.info(f"자동화 계획 {len(self._automation_plans)}개 로드")

    def _create_plan_item(self, plan: AutomationPlan) -> None:
        """자동화 계획 항목 생성"""
        # 연관된 녹화의 잠금 상태 확인
        db = get_db()
        recording = db.get_recording_by_plan_id(plan.plan_id)
        is_locked = recording.locked if recording else False

        item_frame = ctk.CTkFrame(
            self._sequence_frame,
            fg_color=COLORS["bg_dark"],
            corner_radius=8,
            cursor="hand2",
        )
        item_frame.pack(fill="x", padx=5, pady=3)

        # 클릭으로 선택 기능
        def on_click(event, p=plan, w=item_frame):
            self._select_plan_item(p, w)

        item_frame.bind("<Button-1>", on_click)

        # 상단: 이름 + 규칙 수
        top_row = ctk.CTkFrame(item_frame, fg_color="transparent")
        top_row.pack(fill="x", padx=10, pady=(8, 2))
        top_row.bind("<Button-1>", on_click)

        # 잠금 아이콘 (잠겨있으면 표시)
        if is_locked:
            lock_label = ctk.CTkLabel(
                top_row,
                text="🔒",
                font=ctk.CTkFont(size=14, weight="bold"),
                text_color="#ff6b6b",
                cursor="hand2",
            )
            lock_label.pack(side="left", padx=(0, 5))
            lock_label.bind("<Button-1>", on_click)

        # 이름
        name_label = ctk.CTkLabel(
            top_row,
            text=plan.name,
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COLORS["text_primary"],
            anchor="w",
            cursor="hand2",
        )
        name_label.pack(side="left", fill="x", expand=True)
        name_label.bind("<Button-1>", on_click)

        # 규칙 수
        rule_count = len(plan.initial_rules) + len(plan.monitoring_rules)
        rule_label = ctk.CTkLabel(
            top_row,
            text=f"{rule_count}개 동작",
            font=ctk.CTkFont(size=10),
            text_color=COLORS["text_muted"],
            cursor="hand2",
        )
        rule_label.pack(side="right")
        rule_label.bind("<Button-1>", on_click)

        # 하단: 빈 공간 (클릭 가능)
        btn_row = ctk.CTkFrame(item_frame, fg_color="transparent", height=10)
        btn_row.pack(fill="x", padx=10, pady=(2, 8))
        btn_row.bind("<Button-1>", on_click)

    def _rename_plan(self, plan: AutomationPlan) -> None:
        """자동화 계획 이름 수정"""
        dialog = ctk.CTkInputDialog(
            text=f"새 이름을 입력하세요:\n현재: {plan.name}",
            title="계획 이름 수정",
        )
        new_name = dialog.get_input()

        if new_name and new_name.strip():
            plan.name = new_name.strip()
            # JSON 파일에 저장
            plan_file = PLANS_DIR / f"{plan.plan_id}.json"
            try:
                with open(plan_file, 'w', encoding='utf-8') as f:
                    json.dump(plan.to_dict(), f, ensure_ascii=False, indent=2)
                self._load_sequences()  # 목록 새로고침
            except Exception as e:
                logger.error(f"계획 이름 수정 실패: {e}")

    def _select_plan_item(self, plan: AutomationPlan, widget) -> None:
        """자동화 계획 항목 선택 (클릭으로)"""
        # 이전 선택 해제
        if self._selected_item_widget:
            self._selected_item_widget.configure(fg_color=COLORS["bg_dark"])

        # 새 항목 선택 (초록색 배경)
        widget.configure(fg_color=COLORS["accent"])
        self._selected_item_widget = widget

        # 기존 선택 로직 호출
        self._select_plan(plan)

    def _select_plan(self, plan: AutomationPlan) -> None:
        """자동화 계획 선택"""
        self._selected_plan = plan
        self._selected_sequence = None  # 재생 선택 해제
        rule_count = len(plan.initial_rules) + len(plan.monitoring_rules)
        self._selected_label.configure(text=f"✓ {plan.name}")
        self._action_count_label.configure(text=f"({rule_count}개 동작)")
        self._play_btn.configure(state="normal")
        if hasattr(self, '_play_btn2'):
            self._play_btn2.configure(state="normal")

        # 저장된 재생횟수 불러오기
        saved_repeat = getattr(plan, 'total_repeat_count', 1) or 1
        self._repeat_var.set(str(saved_repeat))

    def _show_plan_detail(self, plan: AutomationPlan) -> None:
        """자동화 계획 상세보기"""
        dialog = PlanDetailDialog(self, plan)
        self.wait_window(dialog)

    def _show_sequence_detail(self, sequence: Sequence) -> None:
        """재생 상세보기"""
        dialog = SequenceDetailDialog(self, sequence, self._db)
        self.wait_window(dialog)
        # 다이얼로그 닫힌 후 목록 새로고침
        self._load_sequences()

    def _create_sequence_item(self, sequence: Sequence) -> None:
        """재생 항목 생성"""
        item_frame = ctk.CTkFrame(
            self._sequence_frame,
            fg_color=COLORS["bg_dark"],
            corner_radius=8,
            cursor="hand2",
        )
        item_frame.pack(fill="x", padx=5, pady=3)

        # 클릭으로 선택 기능
        def on_click(event, s=sequence, w=item_frame):
            self._select_sequence_item(s, w)

        item_frame.bind("<Button-1>", on_click)

        # 상단: 이름 + 액션 수
        top_row = ctk.CTkFrame(item_frame, fg_color="transparent")
        top_row.pack(fill="x", padx=10, pady=(8, 2))
        top_row.bind("<Button-1>", on_click)

        # 이름
        name_label = ctk.CTkLabel(
            top_row,
            text=sequence.name,
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COLORS["text_primary"],
            anchor="w",
            cursor="hand2",
        )
        name_label.pack(side="left", fill="x", expand=True)
        name_label.bind("<Button-1>", on_click)

        # 액션 수
        info_text = f"{len(sequence.actions)}개 동작"
        if sequence.run_count > 0:
            info_text += f" | 성공률 {sequence.success_rate:.0f}%"

        info_label = ctk.CTkLabel(
            top_row,
            text=info_text,
            font=ctk.CTkFont(size=10),
            text_color=COLORS["text_muted"],
            cursor="hand2",
        )
        info_label.pack(side="right")
        info_label.bind("<Button-1>", on_click)

        # 하단: 버튼들
        btn_row = ctk.CTkFrame(item_frame, fg_color="transparent")
        btn_row.pack(fill="x", padx=10, pady=(2, 8))
        btn_row.bind("<Button-1>", on_click)

        # 삭제 버튼 (빨간색)
        ctk.CTkButton(
            btn_row,
            text="삭제",
            command=lambda s=sequence: self._delete_sequence(s),
            width=40,
            height=20,
            fg_color=COLORS["error"],
            hover_color="#dc2626",
            text_color="white",
            font=ctk.CTkFont(size=11),
            corner_radius=4,
        ).pack(side="right")

    def _delete_sequence(self, sequence: Sequence) -> None:
        """재생 삭제"""
        from tkinter import messagebox

        # 삭제 확인
        confirm = messagebox.askyesno(
            "재생 삭제", f"'{sequence.name}'을(를) 삭제하시겠습니까?"
        )

        if not confirm:
            return

        # 선택된 재생가 삭제되는 경우 선택 해제
        if self._selected_sequence and self._selected_sequence.id == sequence.id:
            self._selected_sequence = None
            self._selected_label.configure(text="선택된 재생: 없음")
            self._action_count_label.configure(text="")
            self._play_btn.configure(state="disabled")

        # DB에서 삭제
        if sequence.id:
            self._db.delete_sequence(sequence.id)
            logger.info(f"재생 삭제: {sequence.name}")

        # 목록 새로고침
        self._load_sequences()

    def _rename_sequence(self, sequence: Sequence) -> None:
        """재생 이름 수정"""
        dialog = ctk.CTkInputDialog(
            text=f"새 이름을 입력하세요:\n현재: {sequence.name}",
            title="녹화 이름 수정",
        )
        new_name = dialog.get_input()

        if new_name and new_name.strip():
            new_name = new_name.strip()
            old_name = sequence.name
            sequence.name = new_name

            # DB에 저장
            if sequence.id:
                self._db.update_sequence(sequence)
                logger.info(f"재생 이름 변경: {old_name} -> {new_name}")

            # 선택된 재생였다면 라벨도 업데이트
            if self._selected_sequence and self._selected_sequence.id == sequence.id:
                self._selected_label.configure(text=f"선택된 재생: {new_name}")

            # 목록 새로고침
            self._load_sequences()

    def _select_sequence_item(self, sequence: Sequence, widget) -> None:
        """재생 항목 선택 (클릭으로)"""
        # 이전 선택 해제
        if self._selected_item_widget:
            self._selected_item_widget.configure(fg_color=COLORS["bg_dark"])

        # 새 항목 선택 (초록색 배경)
        widget.configure(fg_color=COLORS["accent"])
        self._selected_item_widget = widget

        # 기존 선택 로직 호출
        self._select_sequence(sequence)

    def _select_sequence(self, sequence: Sequence) -> None:
        """재생 선택"""
        self._selected_sequence = sequence
        self._selected_plan = None  # 자동화 계획 선택 해제
        self._selected_label.configure(text=f"✓ {sequence.name}")
        self._action_count_label.configure(text=f"({len(sequence.actions)}개 동작)")
        self._play_btn.configure(state="normal")

    def _show_help(self) -> None:
        """사용법 가이드 표시"""
        from .help_dialog import show_help_dialog

        show_help_dialog(self.winfo_toplevel(), show_on_startup_option=False)

    def _update_speed_label(self, *args) -> None:
        """속도 레이블 업데이트"""
        self._speed_label.configure(text=f"{self._speed_var.get():.1f}x")

    def _on_infinite_toggle(self) -> None:
        """무한 반복 토글"""
        if self._infinite_var.get():
            self._repeat_entry.configure(state="disabled")
        else:
            self._repeat_entry.configure(state="normal")

    def _save_repeat_count(self) -> None:
        """재생횟수 저장"""
        if not self._selected_plan:
            from tkinter import messagebox
            messagebox.showwarning("알림", "선택된 플랜이 없습니다.")
            return

        try:
            repeat_count = int(self._repeat_var.get() or 1)
            if repeat_count < 1:
                repeat_count = 1
            elif repeat_count > 9999:
                repeat_count = 9999

            self._selected_plan.total_repeat_count = repeat_count

            # 플랜 파일에 저장
            from ..utils.config import DATA_DIR
            PLANS_DIR = DATA_DIR / "plans"
            PLANS_DIR.mkdir(parents=True, exist_ok=True)
            plan_file = PLANS_DIR / f"{self._selected_plan.plan_id}.json"
            with open(plan_file, 'w', encoding='utf-8') as f:
                json.dump(self._selected_plan.to_dict(), f, ensure_ascii=False, indent=2)

            logger.info(f"재생횟수 저장: {repeat_count}회 - {self._selected_plan.name}")

            from tkinter import messagebox
            messagebox.showinfo("저장 완료", f"재생횟수 {repeat_count}회가 저장되었습니다.")
        except ValueError:
            from tkinter import messagebox
            messagebox.showerror("오류", "올바른 숫자를 입력하세요.")
        except Exception as e:
            logger.error(f"재생횟수 저장 실패: {e}")
            from tkinter import messagebox
            messagebox.showerror("저장 실패", f"저장 중 오류가 발생했습니다:\n{e}")

    def _on_play(self) -> None:
        """실행 시작"""
        print("[버튼] 실행 버튼 클릭됨")

        # 자동화 계획 실행
        if self._selected_plan:
            print(f"[실행] 자동화 계획 실행: {self._selected_plan.name}")
            self._play_automation_plan()
            return

        # 일반 재생 실행
        if not self._selected_sequence:
            print("[실행] 선택된 재생 없음")
            return

        repeat_count = (
            0 if self._infinite_var.get() else int(self._repeat_var.get() or 1)
        )

        # 실행 시작
        success = self._action_player.play(
            sequence=self._selected_sequence,
            repeat_count=repeat_count,
            speed_multiplier=self._speed_var.get(),
        )

        if success:
            self._update_ui_state(True)
            self._status_label.configure(text=PLAYER["running"])
            self._status_indicator.configure(text_color=COLORS["accent"])

    def _play_automation_plan(self) -> None:
        """자동화 계획 실행"""
        print("[실행] _play_automation_plan 시작")

        if not self._selected_plan:
            print("[실행] 선택된 계획 없음")
            return

        # 실행 전 최신 버전의 계획을 파일에서 다시 로드
        plan_to_execute = self._reload_plan_from_disk(self._selected_plan.plan_id)
        if not plan_to_execute:
            print("[실행] 계획 로드 실패, 기존 계획 사용")
            plan_to_execute = self._selected_plan

        # RuleExecutor 생성
        print("[실행] RuleExecutor 생성")
        self._rule_executor = RuleExecutor()
        self._rule_executor.set_callbacks(
            on_progress=self._on_plan_progress_callback,
            on_complete=self._on_plan_complete,
        )

        # 실행 시작 - UI 상태 변경
        print("[실행] UI 상태 변경 (running=True)")
        self._update_ui_state(True)

        self._status_label.configure(text="▶ 자동화 실행 중...")
        self._status_indicator.configure(text_color=COLORS["accent"])

        # 비동기 실행 (완료 콜백은 set_callbacks에서 이미 설정됨)
        self._rule_executor.execute_plan_async(plan_to_execute)

    def _reload_plan_from_disk(self, plan_id: str) -> Optional[AutomationPlan]:
        """디스크에서 최신 버전의 계획을 다시 로드"""
        try:
            plan_file = PLANS_DIR / f"{plan_id}.json"
            if plan_file.exists():
                with open(plan_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                templates_dir = DATA_DIR / "templates"
                plan = AutomationPlan.from_dict(data, templates_dir=templates_dir)
                logger.info(f"계획 재로드 완료: {plan.name}")

                # 디버그: trigger_image가 있는 규칙 확인
                def check_trigger_images(rules, depth=0):
                    for rule in rules:
                        if rule.trigger_image:
                            logger.info(f"[디버그] 트리거 이미지 발견: rule={rule.rule_id}, trigger={rule.trigger_image}")
                        if rule.children:
                            check_trigger_images(rule.children, depth + 1)

                check_trigger_images(plan.initial_rules)
                check_trigger_images(plan.monitoring_rules)

                return plan
        except Exception as e:
            logger.error(f"계획 재로드 실패: {e}")
        return None

    def _on_plan_progress_callback(self, progress) -> None:
        """자동화 계획 진행 상태 콜백 (ExecutionProgress 객체)"""
        self.after(
            0,
            lambda: self._update_plan_progress(
                progress.current_step,
                progress.total_steps,
                progress.current_rule_description,
            ),
        )

    def _on_plan_progress(self, current: int, total: int, message: str) -> None:
        """자동화 계획 진행 상태 업데이트"""
        self.after(0, lambda: self._update_plan_progress(current, total, message))

    def _update_plan_progress(self, current: int, total: int, message: str) -> None:
        """자동화 계획 진행 상태 UI 업데이트"""
        self._step_label.configure(text=f"{PLAYER['current_step']}: {current} / {total}")
        if total > 0:
            self._progress_bar.set(current / total)
        self._current_action_label.configure(text=message)

    def _on_plan_complete(self, success: bool, message: str) -> None:
        """자동화 계획 실행 완료"""
        self.after(0, lambda: self._show_plan_complete(success, message))

    def _show_plan_complete(self, success: bool, message: str) -> None:
        """자동화 계획 완료 표시"""
        self._update_ui_state(False)

        if success:
            self._status_label.configure(text="✅ 자동화 완료")
            self._status_indicator.configure(text_color=COLORS["info"])
        else:
            self._status_label.configure(text=f"❌ 실패: {message}")
            self._status_indicator.configure(text_color=COLORS["danger"])

        self._current_action_label.configure(text="-")
        self._rule_executor = None

    def _on_pause(self) -> None:
        """일시정지/재개"""
        print("[버튼] 일시정지 버튼 클릭됨")

        # 자동화 계획 실행 중인 경우
        if self._rule_executor:
            from ..player.rule_executor import ExecutionState

            if self._rule_executor.state == ExecutionState.PAUSED:
                self._rule_executor.resume()
                self._pause_btn.configure(text="⏸ 일시정지")
                if hasattr(self, '_pause_btn2'):
                    self._pause_btn2.configure(text="⏸ 일시정지")
                self._status_label.configure(text="자동화 실행 중...")
                self._status_indicator.configure(text_color=COLORS["accent"])
            else:
                self._rule_executor.pause()
                self._pause_btn.configure(text="▶ 계속")
                if hasattr(self, '_pause_btn2'):
                    self._pause_btn2.configure(text="▶ 계속")
                self._status_label.configure(text=PLAYER["paused"])
                self._status_indicator.configure(text_color=COLORS["warning"])
            return

        # 일반 재생 실행 중인 경우
        if self._action_player.state == PlayerState.PAUSED:
            self._action_player.resume()
            self._pause_btn.configure(text="⏸ 일시정지")
            if hasattr(self, '_pause_btn2'):
                self._pause_btn2.configure(text="⏸ 일시정지")
            self._status_label.configure(text=PLAYER["running"])
            self._status_indicator.configure(text_color=COLORS["accent"])
        else:
            self._action_player.pause()
            self._pause_btn.configure(text="▶ 계속")
            if hasattr(self, '_pause_btn2'):
                self._pause_btn2.configure(text="▶ 계속")
            self._status_label.configure(text=PLAYER["paused"])
            self._status_indicator.configure(text_color=COLORS["warning"])

    def _on_stop(self) -> None:
        """실행 중지"""
        print("[버튼] 중지 버튼 클릭됨")

        if self._rule_executor:
            print("[중지] rule_executor 중지 시도")
            try:
                self._rule_executor.stop()
                print("[중지] rule_executor.stop() 완료")
            except Exception as e:
                print(f"[중지] 오류: {e}")
            self._rule_executor = None
        else:
            print("[중지] action_player 중지 시도")
            self._action_player.stop()

        self._update_ui_state(False)
        self._status_label.configure(text="⏹ 중지됨")
        self._status_indicator.configure(text_color=COLORS["text_secondary"])
        self._current_action_label.configure(text="-")
        self._progress_bar.set(0)
        print("[중지] 완료")

    def _on_progress(self, progress: PlaybackProgress) -> None:
        """진행 상태 업데이트"""
        self.after(0, lambda: self._update_progress(progress))

    def _update_progress(self, progress: PlaybackProgress) -> None:
        """진행 상태 UI 업데이트"""
        self._step_label.configure(
            text=f"{PLAYER['current_step']}: {progress.current_step} / {progress.total_steps}"
        )
        self._progress_bar.set(progress.progress_percent / 100)

    def _on_action_start(self, index: int, action: Action) -> None:
        """액션 시작 콜백"""
        self.after(0, lambda: self._current_action_label.configure(text=str(action)))

    def _on_complete(self, success: bool, message: str) -> None:
        """실행 완료"""
        self.after(0, lambda: self._show_complete(success, message))

    def _show_complete(self, success: bool, message: str) -> None:
        """실행 완료 표시"""
        self._update_ui_state(False)

        if success:
            self._status_label.configure(text=f"✅ {PLAYER['completed']}")
            self._status_indicator.configure(text_color=COLORS["info"])
        else:
            self._status_label.configure(text=f"❌ {PLAYER['failed']}: {message}")
            self._status_indicator.configure(text_color=COLORS["danger"])

        self._current_action_label.configure(text="-")
        self._load_sequences()

    def _update_ui_state(self, running: bool) -> None:
        """UI 상태 업데이트"""
        print(f"[UI] 상태 변경: running={running}")

        if running:
            self._play_btn.configure(state="disabled")
            self._pause_btn.configure(state="normal")
            self._stop_btn.configure(state="normal")
            self._speed_slider.configure(state="disabled")
            self._repeat_entry.configure(state="disabled")
            # 두 번째 버튼 세트 동기화
            if hasattr(self, '_play_btn2'):
                self._play_btn2.configure(state="disabled")
                self._pause_btn2.configure(state="normal")
                self._stop_btn2.configure(state="normal")
        else:
            has_selection = (
                self._selected_sequence is not None or self._selected_plan is not None
            )
            self._play_btn.configure(state="normal" if has_selection else "disabled")
            self._pause_btn.configure(state="disabled")
            self._stop_btn.configure(state="disabled")
            self._pause_btn.configure(text="⏸ 일시정지")  # 텍스트 초기화
            self._speed_slider.configure(state="normal")
            if not self._infinite_var.get():
                self._repeat_entry.configure(state="normal")
            # 두 번째 버튼 세트 동기화
            if hasattr(self, '_play_btn2'):
                self._play_btn2.configure(state="normal" if has_selection else "disabled")
                self._pause_btn2.configure(state="disabled")
                self._stop_btn2.configure(state="disabled")
                self._pause_btn2.configure(text="⏸ 일시정지")  # 텍스트 초기화

    def refresh(self) -> None:
        """뷰 새로고침 (재생 목록 + 자동화 계획 모두 갱신)"""
        self._load_sequences()  # 이 메서드가 자동화 계획도 함께 로드함

    def cleanup(self) -> None:
        """리소스 정리"""
        if self._action_player.is_running:
            self._action_player.stop()
        if self._rule_executor:
            self._rule_executor.stop()
            self._rule_executor = None
