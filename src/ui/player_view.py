"""
WinCro 실행 화면 모듈

동작 재현 기능을 위한 UI를 제공합니다.
"""

import customtkinter as ctk
from typing import Optional, List
from pathlib import Path
import json
import time

from ..utils.logger import get_logger
from ..utils.config import get_config, DATA_DIR
from ..utils.window_position import setup_window_position
from ..i18n import PLAYER, BUTTONS, SEQUENCE
from ..player import get_action_player, PlayerState, PlaybackProgress
from ..player.rule_executor import RuleExecutor
from ..database import get_db, Sequence, Action
from ..analyzer.automation_models import AutomationPlan, AutomationRule, GameModeConfig
from .main_window import BaseView
from .theme import COLORS
from .constants import (
    ACTION_NAMES, ACTION_NAMES_SHORT, ACTION_COLORS,
    convert_to_monitor_action, collect_all_actions, assign_new_ids,
    get_action_clipboard, set_action_clipboard,
)
from .virtual_scroll import VirtualScrollFrame
from .key_input_dialog import KeyInputDialog
from .analyzer_view import ImageCropDialog
import cv2
import numpy as np
from PIL import Image
import tkinter as tk
from tkinter import Canvas

logger = get_logger(__name__)

# 자동화 계획 저장 폴더
PLANS_DIR = DATA_DIR / "plans"

# 스레드 관련
import threading

# 썸네일 캐시 (성능 최적화)
_thumbnail_cache = {}  # {cache_key: CTkImage}
_thumbnail_cache_lock = threading.Lock()
MAX_THUMBNAIL_CACHE = 100  # 최대 캐시 개수


def _get_file_mtime(image_path: str) -> float:
    """파일 수정 시간 가져오기"""
    try:
        return Path(image_path).stat().st_mtime
    except (OSError, FileNotFoundError, ValueError):
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
            except (StopIteration, KeyError, RuntimeError):
                pass
        _thumbnail_cache[cache_key] = ctk_image


def invalidate_thumbnail_cache(image_path: str):
    """특정 이미지의 썸네일 캐시 무효화 (크롭 후 갱신용)"""
    with _thumbnail_cache_lock:
        keys_to_remove = [k for k in _thumbnail_cache if image_path in k]
        for key in keys_to_remove:
            del _thumbnail_cache[key]



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

        # 다섯번째 줄: 게임 특화모드
        btn_row5 = ctk.CTkFrame(btn_container, fg_color="transparent")
        btn_row5.pack(fill="x", pady=(3, 0))

        ctk.CTkButton(
            btn_row5,
            text="🎮 특화모드",
            command=self._open_game_mode_dialog,
            width=110,
            height=28,
            fg_color="#a3be8c",
            hover_color="#8fa87a",
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
        action_colors = ACTION_COLORS
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

        action_names = ACTION_NAMES

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

        # 게임모드 수정 버튼
        if rule.action_type == "game_mode":
            ctk.CTkButton(
                btn_frame,
                text="⚙",
                font=ctk.CTkFont(size=14),
                fg_color="#a3be8c",
                hover_color="#8fa87a",
                text_color="white",
                width=30,
                height=26,
                corner_radius=4,
                command=self._open_game_mode_dialog,
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

        icons = {"click": "🖱", "type": "⌨", "hotkey": "⌨", "scroll": "📜", "drag": "↔", "game_mode": "🎮"}
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

    def _run_game_mode(self):
        """특화모드 실행"""
        from ..player.rule_executor import get_rule_executor
        import threading

        config = self._plan.game_mode
        if not config:
            return

        # 실행 중 상태 표시 (다른 액션과 동일하게 초록색 배경)
        self._is_running = True
        self.title("▶ 특화모드 실행 중... (ESC로 중지)")
        self.configure(fg_color="#1a3a1a")  # 녹색 배경
        self.update_idletasks()

        # grab 해제 (다른 윈도우 조작 가능하게)
        try:
            self.grab_release()
        except tk.TclError:
            pass

        # 전역 executor 사용
        executor = get_rule_executor()
        self._running_executor = executor

        def run():
            try:
                logger.info(f"[특화모드] 실행 시작!")
                result = executor.execute_game_mode(config)
                self.after(0, lambda: self._on_game_mode_complete(result))
            except Exception as e:
                logger.error(f"[특화모드] 실행 오류: {e}")
                import traceback
                logger.error(traceback.format_exc())
                self.after(0, lambda: self._on_game_mode_complete(False))

        threading.Thread(target=run, daemon=True).start()

    def _on_game_mode_complete(self, success: bool):
        """특화모드 완료"""
        self._is_running = False
        self.title(f"계획 수정 - {self._plan.name}")
        self.configure(fg_color=COLORS["bg_dark"])  # 배경색 복원
        self.update()
        from tkinter import messagebox
        if success:
            messagebox.showinfo("완료", "목표에 도달했습니다!")
        else:
            messagebox.showinfo("종료", "특화모드가 종료되었습니다.")

    def _test_run_rule(self, rule: AutomationRule):
        """해당 규칙부터 끝까지 실행 (토글 방식: 실행 중이면 중지)"""
        logger.info(f"[부분실행] 클릭됨: {rule.description or rule.action_type}, rule_id={rule.rule_id}")
        from tkinter import messagebox
        from ..player.rule_executor import get_rule_executor
        from ..analyzer.automation_models import AutomationPlan
        import threading

        # 이미 실행 중이면 중지
        if self._is_running:
            self._stop_execution()
            return

        # 게임모드 액션인 경우 특별 처리
        if rule.action_type == "game_mode" and self._plan.game_mode:
            if messagebox.askyesno("특화모드 실행", "특화모드를 실행합니다.\nESC로 중지 가능\n\n시작할까요?"):
                self._run_game_mode()
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

        # 클릭한 규칙의 인덱스 찾기 (rule_id로 비교 - 객체 동일성 대신)
        rule_index = -1
        logger.info(f"[부분실행] 검색할 rule_id={rule.rule_id}, action_type={rule.action_type}, 전체 {len(all_rules_flat)}개 규칙")
        # 처음 10개와 마지막 5개 rule_id 표시 (디버그용)
        if len(all_rules_flat) > 0:
            preview = [f"{r.rule_id}:{r.action_type}" for r in all_rules_flat[:10]]
            logger.info(f"[부분실행] 규칙 목록(앞10개): {preview}")
        for idx, r in enumerate(all_rules_flat):
            if r.rule_id == rule.rule_id:
                rule_index = idx
                logger.info(f"[부분실행] 원본에서 찾음: idx={idx}, 해당액션={r.action_type}")
                break

        if rule_index < 0:
            # 리스트에 없으면 해당 규칙만 실행
            logger.warning(f"[부분실행] rule_id={rule.rule_id}를 찾지 못함, 단일 실행")
            rules_to_run = [rule]
        else:
            # 해당 인덱스부터 끝까지 모든 규칙 포함
            # executor가 다시 평탄화하므로 children을 비운 복사본 사용
            import copy
            rules_to_run = []
            for r in all_rules_flat[rule_index:]:
                r_copy = copy.copy(r)
                r_copy.children = []  # children 비움 (이미 평탄화됨)
                rules_to_run.append(r_copy)
            # 첫 번째 액션 확인 로그
            if rules_to_run:
                first_r = rules_to_run[0]
                logger.info(f"[부분실행] 첫번째 액션: rule_id={first_r.rule_id}, type={first_r.action_type}, desc={first_r.description}")

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

        # 실행 전 플랜 파일에서 최신 데이터 리로드 (업데이트 반영)
        logger.info(f"[부분실행] 플랜 리로드 시작...")
        original_initial_rules = self._plan.initial_rules  # 기본값: 현재 플랜
        try:
            plan_file = PLANS_DIR / f"{self._plan.plan_id}.json"
            logger.debug(f"[부분실행] 플랜 파일: {plan_file}")
            if plan_file.exists():
                with open(plan_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                logger.debug(f"[부분실행] JSON 로드 완료")
                templates_dir = DATA_DIR / "templates"
                reloaded_plan = AutomationPlan.from_dict(data, templates_dir=templates_dir)
                logger.debug(f"[부분실행] from_dict 완료")
                # 리로드된 플랜으로 규칙 재구성
                all_rules_flat = flatten_rules(reloaded_plan.initial_rules)
                logger.info(f"[부분실행] 리로드 후 전체 {len(all_rules_flat)}개 규칙")
                # 인덱스 다시 찾기 (초기화 후 검색)
                rule_index = -1  # 초기화 추가
                for idx, r in enumerate(all_rules_flat):
                    if r.rule_id == rule.rule_id:
                        rule_index = idx
                        logger.info(f"[부분실행] 리로드 후 찾음: idx={idx}, 해당액션={r.action_type}")
                        break
                if rule_index >= 0:
                    import copy
                    rules_to_run = []
                    for r in all_rules_flat[rule_index:]:
                        r_copy = copy.copy(r)
                        r_copy.children = []
                        rules_to_run.append(r_copy)
                    remaining_count = len(rules_to_run)
                    # 리로드 성공 시 리로드된 플랜의 규칙 사용
                    original_initial_rules = reloaded_plan.initial_rules
                    # 리로드 후 첫 번째 액션 확인
                    if rules_to_run:
                        first_r = rules_to_run[0]
                        logger.info(f"[부분실행] 리로드 후 첫번째 액션: rule_id={first_r.rule_id}, type={first_r.action_type}")
                else:
                    # 리로드 후 찾지 못하면 클릭한 규칙만 단독 실행
                    logger.warning(f"[부분실행] 리로드 후 rule_id={rule.rule_id}를 찾지 못함! 클릭한 규칙만 실행")
                    rules_to_run = [rule]
                    remaining_count = 1
                logger.info(f"[부분실행] 플랜 최신 버전 로드됨")
        except Exception as e:
            logger.warning(f"[부분실행] 플랜 리로드 실패, 기존 데이터 사용: {e}")

        logger.info(f"[부분실행] 준비: {rule.description or rule.action_type} ({remaining_count}개 액션)")
        # 최종 확인: 실행할 첫번째 액션
        if rules_to_run:
            final_first = rules_to_run[0]
            logger.info(f"[부분실행] 최종 실행 첫번째: rule_id={final_first.rule_id}, type={final_first.action_type}, desc={final_first.description}")

        # 부분 plan 생성
        partial_plan = AutomationPlan(
            name=f"{self._plan.name} (부분실행)",
            description=f"{rule.description or rule.action_type}",
            initial_rules=rules_to_run,
            monitoring_rules=[],
        )
        # goto 점프 시 원본 계획의 rules를 참조할 수 있도록 저장 (리로드 성공 시 리로드된 규칙 사용)
        partial_plan._original_initial_rules = original_initial_rules

        # 전역 executor 사용
        executor = get_rule_executor()
        self._running_executor = executor

        # 이미 실행 중이면 중지 (비차단)
        if executor.state.value in ["running_initial", "monitoring"]:
            executor.stop()

        # 실행 중 상태 표시
        self._is_running = True
        self.title("▶ 실행 중... (아무 ▶ 버튼 클릭시 중지)")
        self.configure(fg_color="#1a3a1a")  # 녹색 배경으로 변경
        self.update_idletasks()

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
            except (tk.TclError, KeyError, AttributeError):
                pass

        def on_complete(success, msg):
            logger.info(f"[부분실행] 완료: {msg}")
            # UI 스레드에서 상태 복원
            try:
                if not self.winfo_exists():
                    return
                self.after(0, self._on_execution_complete)
                # 창 복원
                if config.ui.minimize_on_run and main_window:
                    self.after(100, lambda: main_window.deiconify())
            except (tk.TclError, KeyError, AttributeError, RuntimeError):
                pass

        def on_error(msg, failed_rule):
            logger.error(f"[부분실행] 오류: {msg}")
            # 창 복원
            if config.ui.minimize_on_run and main_window:
                try:
                    if not self.winfo_exists():
                        return
                    self.after(100, lambda: main_window.deiconify())
                except (tk.TclError, RuntimeError):
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
        dialog.geometry("500x780")
        dialog.resizable(False, False)
        dialog.configure(fg_color=COLORS["bg_dark"])
        dialog.transient(self)
        dialog.grab_set()

        # 중앙 배치
        dialog.update_idletasks()
        x = (dialog.winfo_screenwidth() - 500) // 2
        y = (dialog.winfo_screenheight() - 780) // 2
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

            def _capture_pos():
                pos = pyautogui.position()
                trigger_x_entry.delete(0, "end")
                trigger_x_entry.insert(0, str(pos[0]))
                trigger_y_entry.delete(0, "end")
                trigger_y_entry.insert(0, str(pos[1]))
                dialog.deiconify()  # 다이얼로그 다시 표시
                dialog.focus_force()

            dialog.after(300, _capture_pos)  # UI 차단 없이 300ms 대기

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

        # === 인식률 설정 ===
        conf_frame = ctk.CTkFrame(main_frame, fg_color=COLORS["bg_card"], corner_radius=8)
        conf_frame.pack(fill="x", pady=10)

        ctk.CTkLabel(
            conf_frame,
            text="이미지 인식률",
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
            logger.info(f"트리거 이미지 인식률 저장: {int(conf_var.get())}%")
            self._modified = True
            self._save_plan()  # JSON 파일에 즉시 저장
            from tkinter import messagebox
            messagebox.showinfo("저장 완료", f"인식률이 {int(conf_var.get())}%로 저장되었습니다.")

        ctk.CTkButton(
            conf_input_frame,
            text="인식률 저장",
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
            # 인식률 저장
            rule.confidence = conf_var.get() / 100.0
            logger.info(f"트리거 이미지 인식률 설정: {int(conf_var.get())}%")
            self._modified = True
            self._save_plan()  # JSON 파일에 즉시 저장
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
        from .monitoring_editor import MonitoringModeEditor

        editor = MonitoringModeEditor(self, rule, self._plan.initial_rules)
        editor.wait_window()
        if editor.was_saved:
            self._modified = True
        logger.info(f"[다이얼로그 종료] rule.is_monitoring_mode={rule.is_monitoring_mode}, watches={len(rule.monitoring_watches)}")
        self._refresh_action_list()

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

    def _open_game_mode_dialog(self):
        """게임 특화모드 설정 다이얼로그 열기"""
        dialog = GameModeDialog(self, self._plan, self._save_plan, self._refresh_action_list)
        dialog.grab_set()

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


class GameModeDialog(ctk.CTkToplevel):
    """게임 특화모드 설정 다이얼로그"""

    def __init__(self, parent, plan: AutomationPlan, save_callback, refresh_callback=None):
        super().__init__(parent)

        self._plan = plan
        self._save_callback = save_callback
        self._refresh_callback = refresh_callback
        self._thumbnail_refs = []
        self._is_running = False
        self._stop_event = threading.Event()

        if not plan.game_mode:
            from ..analyzer.automation_models import GameModeConfig
            plan.game_mode = GameModeConfig()
        self._config = plan.game_mode

        self.title("🎮 특화모드")
        self.geometry("1400x900")
        self.resizable(True, True)
        self.minsize(1200, 800)

        # 화면 중앙 배치
        self.update_idletasks()
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        self.geometry(f"1400x900+{(screen_w-1400)//2}+{(screen_h-900)//2}")

        self.lift()
        self.focus_force()
        self.attributes('-topmost', True)
        self.after(100, lambda: self.attributes('-topmost', False))
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_ui()
        self._update_previews()

    def _build_ui(self):
        """UI 구성"""
        main = ctk.CTkScrollableFrame(self, fg_color="transparent")
        main.pack(fill="both", expand=True, padx=15, pady=10)

        # === 이름 섹션 ===
        name_frame = ctk.CTkFrame(main, fg_color=COLORS["bg_card"], corner_radius=10)
        name_frame.pack(fill="x", pady=(0, 8))
        name_inner = ctk.CTkFrame(name_frame, fg_color="transparent")
        name_inner.pack(fill="x", padx=12, pady=8)
        ctk.CTkLabel(name_inner, text="이름:", width=40).pack(side="left")
        self._name_var = ctk.StringVar(value=self._config.name)
        ctk.CTkEntry(name_inner, textvariable=self._name_var, width=250, height=28,
                     placeholder_text="특화모드 이름 입력").pack(side="left", padx=(5, 0))

        # === 캐릭터/목표 이미지 (나란히) ===
        img_frame = ctk.CTkFrame(main, fg_color=COLORS["bg_card"], corner_radius=10)
        img_frame.pack(fill="x", pady=(0, 8))
        img_inner = ctk.CTkFrame(img_frame, fg_color="transparent")
        img_inner.pack(fill="x", padx=12, pady=10)

        # 캐릭터
        char_frame = ctk.CTkFrame(img_inner, fg_color="transparent")
        char_frame.pack(side="left", expand=True)
        ctk.CTkLabel(char_frame, text="캐릭터", font=ctk.CTkFont(size=11, weight="bold")).pack()
        self._char_preview = ctk.CTkLabel(char_frame, text="없음", width=80, height=80,
                                          fg_color=COLORS["bg_card_hover"], corner_radius=6)
        self._char_preview.pack(pady=5)
        char_btns = ctk.CTkFrame(char_frame, fg_color="transparent")
        char_btns.pack()
        ctk.CTkButton(char_btns, text="파일", width=50, height=24,
                      command=lambda: self._select_image("character")).pack(side="left", padx=2)
        ctk.CTkButton(char_btns, text="테스트", width=50, height=24, fg_color="#5e81ac",
                      command=lambda: self._test_find("character")).pack(side="left", padx=2)
        self._char_result = ctk.CTkLabel(char_frame, text="", font=ctk.CTkFont(size=9))
        self._char_result.pack()

        # 목표
        target_frame = ctk.CTkFrame(img_inner, fg_color="transparent")
        target_frame.pack(side="right", expand=True)
        ctk.CTkLabel(target_frame, text="목표", font=ctk.CTkFont(size=11, weight="bold")).pack()
        self._target_preview = ctk.CTkLabel(target_frame, text="없음", width=80, height=80,
                                            fg_color=COLORS["bg_card_hover"], corner_radius=6)
        self._target_preview.pack(pady=5)
        target_btns = ctk.CTkFrame(target_frame, fg_color="transparent")
        target_btns.pack()
        ctk.CTkButton(target_btns, text="파일", width=50, height=24,
                      command=lambda: self._select_image("target")).pack(side="left", padx=2)
        ctk.CTkButton(target_btns, text="테스트", width=50, height=24, fg_color="#5e81ac",
                      command=lambda: self._test_find("target")).pack(side="left", padx=2)
        self._target_result = ctk.CTkLabel(target_frame, text="", font=ctk.CTkFont(size=9))
        self._target_result.pack()

        # === 장애물 이미지 섹션 ===
        obs_frame = ctk.CTkFrame(main, fg_color=COLORS["bg_card"], corner_radius=10)
        obs_frame.pack(fill="x", pady=(0, 8))

        obs_header = ctk.CTkFrame(obs_frame, fg_color="transparent")
        obs_header.pack(fill="x", padx=12, pady=(8, 0))
        ctk.CTkLabel(obs_header, text="장애물 이미지", font=ctk.CTkFont(size=11, weight="bold")).pack(side="left")
        ctk.CTkButton(obs_header, text="+ 추가", width=60, height=24, fg_color="#a3be8c",
                      command=self._add_obstacle).pack(side="right")

        # 장애물 목록 (스크롤)
        self._obs_list_frame = ctk.CTkFrame(obs_frame, fg_color="transparent")
        self._obs_list_frame.pack(fill="x", padx=12, pady=8)
        self._refresh_obstacle_list()

        # === 설정 섹션 ===
        settings_frame = ctk.CTkFrame(main, fg_color=COLORS["bg_card"], corner_radius=10)
        settings_frame.pack(fill="x", pady=(0, 8))
        settings_inner = ctk.CTkFrame(settings_frame, fg_color="transparent")
        settings_inner.pack(fill="x", padx=12, pady=8)

        # 이동키
        keys_row = ctk.CTkFrame(settings_inner, fg_color="transparent")
        keys_row.pack(fill="x", pady=(0, 6))
        ctk.CTkLabel(keys_row, text="이동키:", width=50).pack(side="left")
        self._key_vars = {}
        for key, symbol in [("up", "↑"), ("down", "↓"), ("left", "←"), ("right", "→")]:
            ctk.CTkLabel(keys_row, text=symbol, width=15).pack(side="left")
            var = ctk.StringVar(value=self._config.move_keys.get(key, ""))
            self._key_vars[key] = var
            ctk.CTkEntry(keys_row, textvariable=var, width=45, height=24).pack(side="left", padx=(0, 6))

        # 설정값
        vals_row = ctk.CTkFrame(settings_inner, fg_color="transparent")
        vals_row.pack(fill="x")
        ctk.CTkLabel(vals_row, text="분석간격:").pack(side="left")
        self._interval_var = ctk.StringVar(value=str(self._config.analysis_interval))
        ctk.CTkEntry(vals_row, textvariable=self._interval_var, width=40, height=24).pack(side="left", padx=(2, 0))
        ctk.CTkLabel(vals_row, text="초").pack(side="left", padx=(2, 10))
        ctk.CTkLabel(vals_row, text="신뢰도:").pack(side="left")
        self._confidence_var = ctk.StringVar(value=str(self._config.confidence))
        ctk.CTkEntry(vals_row, textvariable=self._confidence_var, width=40, height=24).pack(side="left", padx=(2, 10))
        ctk.CTkLabel(vals_row, text="도달거리:").pack(side="left")
        self._threshold_var = ctk.StringVar(value=str(self._config.arrival_threshold))
        ctk.CTkEntry(vals_row, textvariable=self._threshold_var, width=40, height=24).pack(side="left", padx=(2, 0))
        ctk.CTkLabel(vals_row, text="px").pack(side="left")

        # === 버튼 ===
        btn_frame = ctk.CTkFrame(main, fg_color="transparent")
        btn_frame.pack(fill="x", pady=(5, 0))
        ctk.CTkButton(btn_frame, text="저장", width=80, height=32, fg_color="#5e81ac",
                      command=self._save_config).pack(side="left")
        ctk.CTkButton(btn_frame, text="닫기", width=80, height=32, fg_color=COLORS["bg_card_hover"],
                      command=self._on_close).pack(side="right")

    def _refresh_obstacle_list(self):
        """장애물 목록 갱신"""
        for w in self._obs_list_frame.winfo_children():
            w.destroy()

        if not self._config.obstacle_images:
            ctk.CTkLabel(self._obs_list_frame, text="장애물 이미지 없음",
                         font=ctk.CTkFont(size=10), text_color=COLORS["text_secondary"]).pack(pady=5)
            return

        for i, img_path in enumerate(self._config.obstacle_images):
            row = ctk.CTkFrame(self._obs_list_frame, fg_color="transparent")
            row.pack(fill="x", pady=2)

            # 썸네일
            thumb = ctk.CTkLabel(row, text="", width=40, height=40,
                                 fg_color=COLORS["bg_card_hover"], corner_radius=4)
            thumb.pack(side="left", padx=(0, 8))
            if Path(img_path).exists():
                self._load_thumb(img_path, thumb, size=40)

            # 파일명
            name = Path(img_path).name if img_path else "?"
            if len(name) > 20:
                name = name[:17] + "..."
            ctk.CTkLabel(row, text=name, font=ctk.CTkFont(size=10), anchor="w", width=150).pack(side="left")

            # 삭제 버튼
            ctk.CTkButton(row, text="✕", width=28, height=24, fg_color="#bf616a",
                          command=lambda idx=i: self._remove_obstacle(idx)).pack(side="right")

    def _add_obstacle(self):
        """장애물 이미지 추가"""
        from tkinter import filedialog
        paths = filedialog.askopenfilenames(filetypes=[("이미지", "*.png *.jpg *.bmp")])
        if paths:
            from ..utils.config import DATA_DIR
            import shutil
            for path in paths:
                dest = DATA_DIR / "templates" / f"obstacle_{Path(path).name}"
                dest.parent.mkdir(parents=True, exist_ok=True)
                if not dest.exists():
                    shutil.copy(path, dest)
                if str(dest) not in self._config.obstacle_images:
                    self._config.obstacle_images.append(str(dest))
            self._refresh_obstacle_list()

    def _remove_obstacle(self, index: int):
        """장애물 이미지 제거"""
        if 0 <= index < len(self._config.obstacle_images):
            self._config.obstacle_images.pop(index)
            self._refresh_obstacle_list()

    def _update_previews(self):
        if self._config.character_image and Path(self._config.character_image).exists():
            self._load_thumb(self._config.character_image, self._char_preview)
        else:
            self._char_preview.configure(image=None, text="없음")

        if self._config.target_image and Path(self._config.target_image).exists():
            self._load_thumb(self._config.target_image, self._target_preview)
        else:
            self._target_preview.configure(image=None, text="없음")

    def _load_thumb(self, path: str, label: ctk.CTkLabel, size: int = 80):
        try:
            from PIL import Image
            img = Image.open(path)
            img.thumbnail((size, size))
            ctk_img = ctk.CTkImage(light_image=img, dark_image=img, size=img.size)
            self._thumbnail_refs.append(ctk_img)
            label.configure(image=ctk_img, text="")
        except:
            label.configure(image=None, text="오류")

    def _select_image(self, img_type: str):
        from tkinter import filedialog
        path = filedialog.askopenfilename(filetypes=[("이미지", "*.png *.jpg *.bmp")])
        if path:
            from ..utils.config import DATA_DIR
            import shutil
            dest = DATA_DIR / "templates" / f"game_{img_type}_{Path(path).name}"
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(path, dest)
            if img_type == "character":
                self._config.character_image = str(dest)
            else:
                self._config.target_image = str(dest)
            self._update_previews()

    def _capture_image(self, img_type: str):
        self.withdraw()
        self.after(300, lambda: self._do_capture(img_type))

    def _do_capture(self, img_type: str):
        try:
            from ..utils.config import DATA_DIR
            from ..recorder.region_selector import RegionSelector
            import pyautogui
            from datetime import datetime

            shot = pyautogui.screenshot()
            region = RegionSelector(shot).select()
            if region:
                x1, y1, x2, y2 = region
                cropped = shot.crop((x1, y1, x2, y2))
                dest = DATA_DIR / "templates" / f"game_{img_type}_{datetime.now().strftime('%H%M%S')}.png"
                dest.parent.mkdir(parents=True, exist_ok=True)
                cropped.save(dest)
                if img_type == "character":
                    self._config.character_image = str(dest)
                else:
                    self._config.target_image = str(dest)
        except Exception as e:
            logger.error(f"캡처 실패: {e}")
        finally:
            self.deiconify()
            self.lift()
            self._update_previews()

    def _test_find(self, img_type: str):
        path = self._config.character_image if img_type == "character" else self._config.target_image
        label = self._char_result if img_type == "character" else self._target_result

        if not path or not Path(path).exists():
            label.configure(text="이미지 없음", text_color="#bf616a")
            return

        label.configure(text="검색중...", text_color=COLORS["text_secondary"])
        self.update()

        def search():
            try:
                from ..player.rule_executor import RuleExecutor
                result = RuleExecutor()._find_image_on_screen(path, float(self._confidence_var.get()))
                if result:
                    x, y, conf = result
                    # 인식률 표시
                    conf_text = f"발견! 인식률: {conf:.1%}"
                    color = "#a3be8c" if conf >= 0.8 else "#ebcb8b" if conf >= 0.6 else "#bf616a"
                    self.after(0, lambda: label.configure(text=conf_text, text_color=color))
                    # 애니메이션 원 표시
                    self.after(0, lambda: self._show_pulse_animation(x, y, img_type))
                else:
                    self.after(0, lambda: label.configure(text="못찾음", text_color="#bf616a"))
            except Exception as e:
                self.after(0, lambda: label.configure(text=f"오류", text_color="#bf616a"))

        threading.Thread(target=search, daemon=True).start()

    def _show_pulse_animation(self, x: int, y: int, img_type: str):
        """발견된 위치에 펄스 애니메이션 표시"""
        # 색상: 캐릭터=파랑, 목표=초록
        color = "#5e81ac" if img_type == "character" else "#a3be8c"

        # 투명 오버레이 창 생성
        overlay = tk.Toplevel(self)
        overlay.overrideredirect(True)
        overlay.attributes('-topmost', True)
        overlay.attributes('-transparentcolor', 'black')
        overlay.configure(bg='black')

        # 캔버스 크기
        canvas_size = 150
        overlay.geometry(f"{canvas_size}x{canvas_size}+{x - canvas_size//2}+{y - canvas_size//2}")

        canvas = tk.Canvas(overlay, width=canvas_size, height=canvas_size,
                          bg='black', highlightthickness=0)
        canvas.pack()

        center = canvas_size // 2
        circles = []

        # 애니메이션 프레임
        def animate(frame=0):
            if frame >= 30:  # 30프레임 후 종료
                try:
                    overlay.destroy()
                except:
                    pass
                return

            # 기존 원 삭제
            for c in circles:
                canvas.delete(c)
            circles.clear()

            # 펄스 효과: 원이 커졌다 작아졌다
            import math
            pulse = math.sin(frame * 0.4) * 0.5 + 0.5  # 0~1 사이 값

            # 바깥 원 (펄스)
            outer_r = 20 + int(pulse * 35)
            outer_alpha = int((1 - pulse) * 3)  # 두께 변화
            c1 = canvas.create_oval(
                center - outer_r, center - outer_r,
                center + outer_r, center + outer_r,
                outline=color, width=max(2, outer_alpha + 2), fill=''
            )
            circles.append(c1)

            # 중간 원 (역펄스)
            mid_r = 15 + int((1 - pulse) * 20)
            c2 = canvas.create_oval(
                center - mid_r, center - mid_r,
                center + mid_r, center + mid_r,
                outline=color, width=2, fill=''
            )
            circles.append(c2)

            # 중심점
            c3 = canvas.create_oval(
                center - 5, center - 5,
                center + 5, center + 5,
                outline=color, fill=color, width=0
            )
            circles.append(c3)

            overlay.after(33, lambda: animate(frame + 1))  # ~30fps

        animate()

    def _toggle_execution(self):
        if self._is_running:
            self._stop_execution()
        else:
            self._start_execution()

    def _start_execution(self):
        if not self._config.character_image or not Path(self._config.character_image).exists():
            from tkinter import messagebox
            messagebox.showerror("오류", "캐릭터 이미지를 설정하세요")
            return
        if not self._config.target_image or not Path(self._config.target_image).exists():
            from tkinter import messagebox
            messagebox.showerror("오류", "목표 이미지를 설정하세요")
            return

        self._apply_settings()
        self._is_running = True
        self._stop_event.clear()
        self._run_btn.configure(text="■ 중지", fg_color="#bf616a")
        self._status_label.configure(text="상태: 실행중", text_color="#a3be8c")
        threading.Thread(target=self._run_loop, daemon=True).start()

    def _stop_execution(self):
        self._stop_event.set()
        self._is_running = False
        self._run_btn.configure(text="▶ 시작", fg_color="#a3be8c")
        self._status_label.configure(text="상태: 중지됨", text_color="#d08770")

    def _run_loop(self):
        from ..player.rule_executor import RuleExecutor
        import pyautogui
        import keyboard

        executor = RuleExecutor()
        keyboard.add_hotkey('escape', self._stop_event.set)

        try:
            while not self._stop_event.is_set():
                char = executor._find_image_on_screen(self._config.character_image, self._config.confidence)
                if not char:
                    self.after(0, lambda: self._update_status("캐릭터 검색중", "-", "-", "-", "-"))
                    time.sleep(self._config.analysis_interval)
                    continue

                cx, cy, _ = char
                target = executor._find_image_on_screen(self._config.target_image, self._config.confidence)
                if not target:
                    self.after(0, lambda x=cx, y=cy: self._update_status("목표 검색중", f"({x},{y})", "-", "-", "-"))
                    time.sleep(self._config.analysis_interval)
                    continue

                tx, ty, _ = target
                dx, dy = tx - cx, ty - cy
                dist = (dx**2 + dy**2) ** 0.5

                if dist < self._config.arrival_threshold:
                    self.after(0, self._on_arrival)
                    return

                # 방향 결정 (dx>0이면 오른쪽, dy>0이면 아래)
                if abs(dx) > abs(dy):
                    if dx > 0:
                        key = self._config.move_keys.get("right", "Right")
                        dir_str = "→"
                    else:
                        key = self._config.move_keys.get("left", "Left")
                        dir_str = "←"
                else:
                    if dy > 0:
                        key = self._config.move_keys.get("down", "Down")
                        dir_str = "↓"
                    else:
                        key = self._config.move_keys.get("up", "Up")
                        dir_str = "↑"

                logger.info(f"[게임모드] 캐릭터({cx},{cy}) 목표({tx},{ty}) dx={dx} dy={dy} → {dir_str} key={key}")

                self.after(0, lambda x=cx, y=cy, tx=tx, ty=ty, d=dist, ds=dir_str:
                    self._update_status("이동중", f"({x},{y})", f"({tx},{ty})", f"{d:.0f}px", ds))

                # 키 입력 (누르고 있기)
                pyautogui.keyDown(key)
                time.sleep(0.05)
                pyautogui.keyUp(key)
                time.sleep(self._config.analysis_interval)

        except Exception as e:
            logger.error(f"게임모드 오류: {e}")
        finally:
            try:
                keyboard.remove_hotkey('escape')
            except:
                pass
            self.after(0, self._stop_execution)

    def _update_status(self, status, char, target, dist, direction):
        self._status_label.configure(text=f"상태: {status}")
        self._char_pos_label.configure(text=f"캐릭터: {char}")
        self._target_pos_label.configure(text=f"목표: {target}")
        self._distance_label.configure(text=f"거리: {dist}")
        self._direction_label.configure(text=f"방향: {direction}")

    def _on_arrival(self):
        self._stop_execution()
        self._status_label.configure(text="상태: 도달!", text_color="#a3be8c")
        from tkinter import messagebox
        messagebox.showinfo("완료", "목표 도달!")

    def _apply_settings(self):
        try:
            self._config.name = self._name_var.get().strip()
            self._config.analysis_interval = float(self._interval_var.get())
            self._config.confidence = float(self._confidence_var.get())
            self._config.arrival_threshold = int(self._threshold_var.get())
            for k, v in self._key_vars.items():
                self._config.move_keys[k] = v.get()
        except:
            pass

    def _save_config(self):
        self._config.enabled = True
        self._apply_settings()
        self._plan.game_mode = self._config

        # 액션 목록에 게임모드 규칙 추가/업데이트
        game_rule = None
        for rule in self._plan.initial_rules:
            if rule.action_type == "game_mode":
                game_rule = rule
                break

        # 이름 결정 (입력된 이름 또는 기본값)
        display_name = self._config.name if self._config.name else "특화모드"

        if not game_rule:
            # 새로 생성
            game_rule = AutomationRule(
                rule_type="fixed_sequence",
                action_type="game_mode",
                description=display_name,
                target_image=self._config.character_image,
                confidence=self._config.confidence,
            )
            self._plan.initial_rules.append(game_rule)
        else:
            # 기존 업데이트
            game_rule.target_image = self._config.character_image
            game_rule.confidence = self._config.confidence
            game_rule.description = display_name

        self._save_callback()

        # UI 갱신
        if self._refresh_callback:
            self._refresh_callback()

        from tkinter import messagebox
        messagebox.showinfo("저장", "저장 완료")

    def _on_close(self):
        if self._is_running:
            self._stop_execution()
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
            except (tk.TclError, ValueError):
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
        action_colors = ACTION_COLORS
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

        action_names = ACTION_NAMES

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

        icons = {"click": "🖱", "type": "⌨", "hotkey": "⌨", "scroll": "📜", "drag": "↔", "wait": "⏳", "wait_for_image": "🔍", "game_mode": "🎮"}
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
        try:
            if not self.winfo_exists():
                return
            self.after(
                0,
                lambda: self._update_plan_progress(
                    progress.current_step,
                    progress.total_steps,
                    progress.current_rule_description,
                ),
            )
        except (tk.TclError, RuntimeError):
            pass

    def _on_plan_progress(self, current: int, total: int, message: str) -> None:
        """자동화 계획 진행 상태 업데이트"""
        try:
            if not self.winfo_exists():
                return
            self.after(0, lambda: self._update_plan_progress(current, total, message))
        except (tk.TclError, RuntimeError):
            pass

    def _update_plan_progress(self, current: int, total: int, message: str) -> None:
        """자동화 계획 진행 상태 UI 업데이트"""
        try:
            if not self.winfo_exists():
                return
            self._step_label.configure(text=f"{PLAYER['current_step']}: {current} / {total}")
            if total > 0:
                self._progress_bar.set(current / total)
            self._current_action_label.configure(text=message)
        except (tk.TclError, RuntimeError, AttributeError):
            pass

    def _on_plan_complete(self, success: bool, message: str) -> None:
        """자동화 계획 실행 완료"""
        try:
            if not self.winfo_exists():
                return
            self.after(0, lambda: self._show_plan_complete(success, message))
        except (tk.TclError, RuntimeError):
            pass

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
        """실행 중지 (비차단 — stop() 호출 후 즉시 UI 갱신)"""
        logger.info("[버튼] 중지 버튼 클릭됨")

        if self._rule_executor:
            try:
                self._rule_executor.stop()  # 비차단: join은 내부 스레드에서 처리
            except Exception as e:
                logger.error(f"[중지] rule_executor 오류: {e}")
            self._rule_executor = None
        else:
            self._action_player.stop()  # 비차단: join은 내부 스레드에서 처리

        self._update_ui_state(False)
        self._status_label.configure(text="⏹ 중지됨")
        self._status_indicator.configure(text_color=COLORS["text_secondary"])
        self._current_action_label.configure(text="-")
        self._progress_bar.set(0)

    def _on_progress(self, progress: PlaybackProgress) -> None:
        """진행 상태 업데이트"""
        try:
            if not self.winfo_exists():
                return
            self.after(0, lambda: self._update_progress(progress))
        except (tk.TclError, RuntimeError):
            pass

    def _update_progress(self, progress: PlaybackProgress) -> None:
        """진행 상태 UI 업데이트"""
        try:
            if not self.winfo_exists():
                return
            self._step_label.configure(
                text=f"{PLAYER['current_step']}: {progress.current_step} / {progress.total_steps}"
            )
            self._progress_bar.set(progress.progress_percent / 100)
        except (tk.TclError, RuntimeError, AttributeError):
            pass

    def _on_action_start(self, index: int, action: Action) -> None:
        """액션 시작 콜백"""
        try:
            if not self.winfo_exists():
                return
            self.after(0, lambda: self._current_action_label.configure(text=str(action)))
        except (tk.TclError, RuntimeError):
            pass

    def _on_complete(self, success: bool, message: str) -> None:
        """실행 완료"""
        try:
            if not self.winfo_exists():
                return
            self.after(0, lambda: self._show_complete(success, message))
        except (tk.TclError, RuntimeError):
            pass

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
        # 배치 렌더링 타이머 취소
        if hasattr(self, '_batch_render_id') and self._batch_render_id:
            try:
                self.after_cancel(self._batch_render_id)
            except (ValueError, tk.TclError):
                pass
            self._batch_render_id = None

        if self._action_player.is_running:
            self._action_player.stop()
        if self._rule_executor:
            try:
                self._rule_executor.stop()
            except Exception as e:
                logger.warning(f"rule_executor 정리 오류: {e}")
            self._rule_executor = None
