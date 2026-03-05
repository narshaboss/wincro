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
from ..analyzer.automation_models import AutomationPlan, AutomationRule, GameModeConfig, MinimapConfig
from .main_window import BaseView
from .theme import COLORS
from .constants import (
    ACTION_NAMES, ACTION_NAMES_SHORT, ACTION_COLORS,
    convert_to_monitor_action, collect_all_actions, assign_new_ids,
    get_action_clipboard, set_action_clipboard,
    get_waypoint_clipboard, set_waypoint_clipboard,
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


def _safe_grab(timeout: float = 5.0):
    """ImageGrab.grab()을 타임아웃과 함께 실행 (DWM 행 방지)"""
    from PIL import ImageGrab
    result = [None]
    error = [None]

    def _grab():
        try:
            result[0] = ImageGrab.grab()
        except Exception as e:
            error[0] = e

    t = threading.Thread(target=_grab, daemon=True)
    t.start()
    t.join(timeout=timeout)
    if t.is_alive():
        logger.error(f"[스크린샷] ImageGrab.grab() 타임아웃 ({timeout}초)")
        return None
    if error[0]:
        raise error[0]
    return result[0]


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


def _convert_action_to_monitor_dict(action):
    """액션을 monitor_action 형식으로 변환 (모든 속성 포함).

    PlanDetailDialog, SequenceDetailDialog 등에서 클립보드 액션을
    모니터링 액션 딕셔너리로 변환할 때 공통으로 사용합니다.
    """
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
        root_count = 0
        child_counters = {}  # {parent_id: 현재 자식 번호}

        def add_rule(rule, depth, parent_idx_str):
            nonlocal root_count
            if depth == 0:
                root_count += 1
                idx_str = str(root_count)
            else:
                pid = rule.parent_id
                child_counters[pid] = child_counters.get(pid, 0) + 1
                idx_str = f"{parent_idx_str}.{child_counters[pid]}"

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
                command=lambda r=rule: self._open_game_mode_dialog(config_rule_id=r.rule_id),
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
        """썸네일 표시 (클릭하여 편집) - 캐시 사용, 비동기 로딩"""
        # game_mode는 이미지 없이 아이콘만 표시
        if rule.action_type == "game_mode":
            ctk.CTkLabel(
                parent, text="🎮", font=ctk.CTkFont(size=20),
                text_color=COLORS["text_muted"],
            ).pack(expand=True)
            return

        image_path = rule.target_image

        if image_path and Path(image_path).exists():
            try:
                # 캐시된 썸네일 확인
                target_size = (60, 60)
                ctk_image = get_cached_thumbnail(image_path, target_size)

                if ctk_image is not None:
                    # 캐시 히트 — 즉시 표시
                    thumb_btn = ctk.CTkButton(
                        parent, image=ctk_image, text="",
                        width=68, height=68,
                        fg_color="transparent",
                        hover_color=COLORS["bg_card_hover"],
                        corner_radius=4,
                        command=lambda p=image_path, r=rule: self._open_image_editor(p, r),
                    )
                    thumb_btn.pack(expand=True)
                    self._thumbnail_refs.append(ctk_image)
                    return

                # 캐시 미스 — 플레이스홀더 표시 후 백그라운드 로딩
                placeholder = ctk.CTkLabel(
                    parent, text="📷", font=ctk.CTkFont(size=18),
                    text_color=COLORS["text_muted"],
                )
                placeholder.pack(expand=True)

                def _load_thumb(path=image_path, r=rule, ph=placeholder, pr=parent):
                    try:
                        img_arr = np.fromfile(path, np.uint8)
                        img = cv2.imdecode(img_arr, cv2.IMREAD_COLOR)
                        if img is None:
                            return
                        img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                        h, w = img_rgb.shape[:2]
                        scale = min(60 / w, 60 / h)
                        new_w, new_h = int(w * scale), int(h * scale)
                        resized = cv2.resize(img_rgb, (new_w, new_h))
                        pil_image = Image.fromarray(resized)

                        def _apply():
                            try:
                                ctk_img = ctk.CTkImage(light_image=pil_image, dark_image=pil_image, size=(new_w, new_h))
                                set_cached_thumbnail(path, target_size, ctk_img)
                                ph.destroy()
                                thumb_btn = ctk.CTkButton(
                                    pr, image=ctk_img, text="",
                                    width=68, height=68,
                                    fg_color="transparent",
                                    hover_color=COLORS["bg_card_hover"],
                                    corner_radius=4,
                                    command=lambda p=path, ru=r: self._open_image_editor(p, ru),
                                )
                                thumb_btn.pack(expand=True)
                                self._thumbnail_refs.append(ctk_img)
                            except (tk.TclError, RuntimeError):
                                pass

                        self.after(0, _apply)
                    except (IOError, OSError, ValueError):
                        pass

                threading.Thread(target=_load_thumb, daemon=True).start()
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

        # game_modes에서 연결된 특화모드 설정도 제거
        if rule.action_type == "game_mode" and hasattr(self, '_plan'):
            self._plan.game_modes.pop(rule.rule_id, None)

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
            # 특화모드인 경우 GameModeConfig.name도 동기화
            if rule.action_type == "game_mode" and hasattr(self, '_plan'):
                _gm_cfg = self._plan.game_modes.get(rule.rule_id)
                if _gm_cfg is not None:
                    _gm_cfg.name = result.strip()
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
        # 숨겨진 GameModeDialog 중지
        _gm = getattr(self, '_gm_dialog', None)
        if _gm:
            logger.info("[부분실행] 특화모드 중지 요청")
            try:
                _gm._stop_event.set()
            except Exception:
                pass
            # 곧 _check_gm_done에서 destroy + _on_game_mode_complete 호출됨
            return

        if self._running_executor:
            logger.info("[부분실행] 중지 요청")
            try:
                self._running_executor.stop()
            except Exception as e:
                logger.error(f"[부분실행] 중지 오류: {e}")
            self._running_executor = None

        self._gm_remaining_rules = []
        self._is_running = False
        self.title(f"계획 수정 - {self._plan.name}")
        self.configure(fg_color=COLORS["bg_dark"])

    def _run_game_mode(self, config_rule_id=None):
        """특화모드 실행 — 숨긴 GameModeDialog로 _run_coordinate_loop 사용"""
        config = self._plan.game_modes.get(config_rule_id) if config_rule_id else self._plan.game_mode
        if not config:
            self._on_game_mode_complete(False, "특화모드 설정 없음")
            return

        # 실행 중 상태 표시 (PlanDetailDialog에서 관리)
        self._is_running = True
        self.title("▶ 특화모드 실행 중... (ESC로 중지)")
        self.configure(fg_color="#1a3a1a")
        self.update_idletasks()
        try:
            self.grab_release()
        except tk.TclError:
            pass

        # GameModeDialog를 숨겨서 생성 + 자동 실행
        self._gm_dialog = GameModeDialog(self, self._plan, self._save_plan, self._refresh_action_list,
                                         config_rule_id=config_rule_id, auto_run=True)
        self._gm_dialog.withdraw()  # 창 숨기기
        self._running_executor = None

        # 실행 완료 감시 (500ms 폴링)
        def _check_gm_done():
            gm = getattr(self, '_gm_dialog', None)
            if not gm:
                return
            try:
                if not gm.winfo_exists():
                    self._gm_dialog = None
                    self._on_game_mode_complete(False)
                    return
                if not gm._is_running:
                    # 정상 완료 vs 사용자 중지 구분 (_on_arrival에서 _completed_normally 설정)
                    completed_ok = getattr(gm, '_completed_normally', False)
                    gm.destroy()
                    self._gm_dialog = None
                    self._on_game_mode_complete(completed_ok)
                    return
                self.after(500, _check_gm_done)
            except Exception:
                self._gm_dialog = None
                self._on_game_mode_complete(False)

        self.after(500, _check_gm_done)

    def _on_game_mode_complete(self, success: bool, error_msg: str = None):
        """특화모드 완료 — 나머지 규칙이 있으면 이어서 실행"""
        remaining = getattr(self, '_gm_remaining_rules', [])
        self._gm_remaining_rules = []

        # 성공 + 나머지 규칙 있으면 → executor로 이어서 실행
        if success and remaining:
            logger.info(f"[부분실행] 특화모드 완료, 이후 {len(remaining)}개 액션 이어서 실행")
            self._run_remaining_rules(remaining)
            return

        # 나머지 없거나 실패 → UI 복원 + 메시지
        self._is_running = False
        self.title(f"계획 수정 - {self._plan.name}")
        self.configure(fg_color=COLORS["bg_dark"])
        self.update()
        from tkinter import messagebox
        if success:
            messagebox.showinfo("완료", "목표에 도달했습니다!")
        else:
            msg = error_msg if error_msg else "특화모드가 종료되었습니다."
            messagebox.showinfo("종료", msg)

    def _run_remaining_rules(self, rules_to_run):
        """특화모드 완료 후 나머지 규칙 실행 — game_mode는 GameModeDialog로 라우팅"""
        if not rules_to_run:
            self._is_running = False
            self.title(f"계획 수정 - {self._plan.name}")
            self.configure(fg_color=COLORS["bg_dark"])
            return

        # 첫 번째 game_mode 규칙 위치 찾기
        first_gm_idx = None
        for i, r in enumerate(rules_to_run):
            if r.action_type == "game_mode" and self._plan.game_modes.get(r.rule_id):
                first_gm_idx = i
                break

        if first_gm_idx is None:
            # game_mode 없음 → 전부 RuleExecutor
            self._run_remaining_via_executor(rules_to_run, chain_remaining=None)
            return

        if first_gm_idx == 0:
            # 첫 규칙이 game_mode → GameModeDialog로 바로 실행
            gm_rule = rules_to_run[0]
            self._gm_remaining_rules = rules_to_run[1:]
            logger.info(f"[이어서실행] game_mode → GameModeDialog ({len(self._gm_remaining_rules)}개 대기)")
            self._run_game_mode(config_rule_id=gm_rule.rule_id)
            return

        # game_mode가 중간 → 앞부분만 RuleExecutor, 완료 후 나머지 체이닝
        before_gm = rules_to_run[:first_gm_idx]
        gm_and_after = rules_to_run[first_gm_idx:]
        logger.info(f"[이어서실행] {len(before_gm)}개 먼저 실행, 이후 game_mode 체이닝")
        self._run_remaining_via_executor(before_gm, chain_remaining=gm_and_after)

    def _run_remaining_via_executor(self, rules_to_run, chain_remaining=None):
        """RuleExecutor로 non-game_mode 규칙 실행

        Args:
            rules_to_run: 실행할 규칙 리스트
            chain_remaining: 완료 시 _run_remaining_rules(chain_remaining) 체이닝 (None이면 종료)
        """
        from ..player.rule_executor import get_rule_executor
        from ..analyzer.automation_models import AutomationPlan
        import threading

        partial_plan = AutomationPlan(
            name=f"{self._plan.name} (이어서 실행)",
            description=f"특화모드 이후 {len(rules_to_run)}개 액션",
            initial_rules=rules_to_run,
            monitoring_rules=[],
        )
        partial_plan._original_initial_rules = self._plan.initial_rules
        partial_plan.game_modes = self._plan.game_modes

        executor = get_rule_executor()
        self._running_executor = executor

        if executor.state.value in ["running_initial", "monitoring"]:
            executor.stop()

        # 실행 중 상태 유지
        self._is_running = True
        self.title("▶ 이어서 실행 중... (아무 ▶ 버튼 클릭시 중지)")
        self.update_idletasks()

        try:
            self.grab_release()
        except tk.TclError:
            pass

        from ..utils.config import get_config
        config = get_config()
        main_window = self.winfo_toplevel().master

        def on_complete(success, msg):
            logger.info(f"[이어서실행] 완료: {msg}")
            try:
                if not self.winfo_exists():
                    return
                if success and chain_remaining:
                    # 다음 game_mode로 체이닝 (executor 정리 후 main thread에서)
                    self._running_executor = None
                    self.after(0, lambda: self._run_remaining_rules(chain_remaining))
                else:
                    self.after(0, self._on_execution_complete)
                    if config.ui.minimize_on_run and main_window:
                        self.after(100, lambda: main_window.deiconify())
            except (tk.TclError, KeyError, AttributeError, RuntimeError):
                pass

        def on_error(msg, failed_rule):
            logger.error(f"[이어서실행] 오류: {msg}")
            try:
                if not self.winfo_exists():
                    return
                self.after(0, self._on_execution_complete)
                if config.ui.minimize_on_run and main_window:
                    self.after(100, lambda: main_window.deiconify())
            except (tk.TclError, RuntimeError):
                pass

        executor.set_callbacks(on_complete=on_complete, on_error=on_error)

        def run():
            try:
                logger.info(f"[이어서실행] {len(rules_to_run)}개 액션 시작")
                executor.execute_plan(partial_plan)
            except Exception as e:
                logger.error(f"[이어서실행] 예외: {e}")
                import traceback
                logger.error(traceback.format_exc())

        threading.Thread(target=run, daemon=True).start()

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

        # 모든 규칙을 평탄화 (자식 포함)
        def flatten_rules(rules):
            result = []
            for r in rules:
                result.append(r)
                if r.children:
                    result.extend(flatten_rules(r.children))
            return result

        # 게임모드 액션인 경우 특별 처리 (이후 액션도 이어서 실행)
        if rule.action_type == "game_mode" and self._plan.game_modes.get(rule.rule_id):
            # 이 game_mode 이후 규칙들 수집
            all_rules_flat = flatten_rules(self._plan.initial_rules)
            _gm_idx = next((i for i, r in enumerate(all_rules_flat) if r is rule), -1)
            if _gm_idx < 0:
                _gm_idx = next((i for i, r in enumerate(all_rules_flat) if r.rule_id == rule.rule_id), -1)
            remaining_after = []
            if _gm_idx >= 0 and _gm_idx + 1 < len(all_rules_flat):
                import copy
                for r in all_rules_flat[_gm_idx + 1:]:
                    r_copy = copy.copy(r)
                    r_copy.children = []
                    remaining_after.append(r_copy)
            count_msg = f"\n(이후 {len(remaining_after)}개 액션도 이어서 실행)" if remaining_after else ""
            if messagebox.askyesno("특화모드 실행", f"특화모드를 실행합니다.\nESC로 중지 가능{count_msg}\n\n시작할까요?"):
                self._gm_remaining_rules = remaining_after
                self._run_game_mode(config_rule_id=rule.rule_id)
            return

        all_rules_flat = flatten_rules(self._plan.initial_rules)

        # 클릭한 규칙의 인덱스 찾기 (객체 동일성으로 비교 - rule_id 중복 문제 방지)
        rule_index = -1
        logger.info(f"[부분실행] 검색할 rule_id={rule.rule_id}, action_type={rule.action_type}, 전체 {len(all_rules_flat)}개 규칙")
        for idx, r in enumerate(all_rules_flat):
            if r is rule:
                rule_index = idx
                logger.info(f"[부분실행] 원본에서 찾음(객체동일성): idx={idx}, 해당액션={r.action_type}")
                break
        # 객체 동일성 실패 시 rule_id 폴백 (모든 동일 ID 중 가장 적합한 것 선택)
        if rule_index < 0:
            candidates = [(idx, r) for idx, r in enumerate(all_rules_flat) if r.rule_id == rule.rule_id]
            if len(candidates) == 1:
                rule_index = candidates[0][0]
                logger.info(f"[부분실행] rule_id로 찾음: idx={rule_index}")
            elif len(candidates) > 1:
                # 중복 ID: action_type과 description으로 구별
                for idx, r in candidates:
                    if r.action_type == rule.action_type and r.description == rule.description:
                        rule_index = idx
                        logger.info(f"[부분실행] 중복ID 중 매칭: idx={idx}, type={r.action_type}")
                        break
                if rule_index < 0:
                    rule_index = candidates[0][0]
                    logger.warning(f"[부분실행] 중복ID 첫번째 사용: idx={rule_index}")

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

        # 수정된 내용이 있으면 백그라운드 저장 (대화상자 없이)
        if self._modified:
            try:
                PLANS_DIR.mkdir(parents=True, exist_ok=True)
                plan_file = PLANS_DIR / f"{self._plan.plan_id}.json"
                with open(plan_file, 'w', encoding='utf-8') as f:
                    json.dump(self._plan.to_dict(), f, ensure_ascii=False, indent=2)
                self._modified = False
                logger.info(f"[부분실행] 플랜 자동 저장 완료")
            except Exception as e:
                logger.warning(f"[부분실행] 자동 저장 실패: {e}")

        # 현재 메모리의 플랜 데이터를 직접 사용 (느린 JSON 리로드 생략)
        original_initial_rules = self._plan.initial_rules

        logger.info(f"[부분실행] 준비: {rule.description or rule.action_type} ({remaining_count}개 액션)")
        # 최종 확인: 실행할 첫번째 액션
        if rules_to_run:
            final_first = rules_to_run[0]
            logger.info(f"[부분실행] 최종 실행 첫번째: rule_id={final_first.rule_id}, type={final_first.action_type}, desc={final_first.description}")

        # game_mode 규칙 포함 여부 확인
        # → 있으면 _run_remaining_rules 경유 (GameModeDialog 사용, route_ends/보스 지원)
        # → RuleExecutor.execute_game_mode_coordinate는 간소화 버전이라 route_ends 미지원 → 조기 도착 판정 버그
        _has_game_mode = any(
            r.action_type == "game_mode" and self._plan.game_modes.get(r.rule_id)
            for r in rules_to_run
        )
        if _has_game_mode:
            logger.info(f"[부분실행] game_mode 포함 → _run_remaining_rules 경유 (GameModeDialog 사용)")
            self._is_running = True
            self.title("▶ 실행 중... (아무 ▶ 버튼 클릭시 중지)")
            self.configure(fg_color="#1a3a1a")
            try:
                self.grab_release()
            except tk.TclError:
                pass
            self._run_remaining_rules(rules_to_run)
            return

        # 부분 plan 생성
        partial_plan = AutomationPlan(
            name=f"{self._plan.name} (부분실행)",
            description=f"{rule.description or rule.action_type}",
            initial_rules=rules_to_run,
            monitoring_rules=[],
        )
        # goto 점프 시 원본 계획의 rules를 참조할 수 있도록 저장 (리로드 성공 시 리로드된 규칙 사용)
        partial_plan._original_initial_rules = original_initial_rules
        # 특화모드 설정 복사
        partial_plan.game_modes = self._plan.game_modes

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
            try:
                if not self.winfo_exists():
                    return
                self.after(0, self._on_execution_complete)
                # 창 복원
                if config.ui.minimize_on_run and main_window:
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

    def _setup_copied_game_mode_maps(self, src_rule_id: str, config):
        """game_mode 붙여넣기 시 맵 파일 참조 설정 + 잠금 해제"""
        import os
        waypoints = getattr(config, 'waypoints', None) or []
        if not waypoints:
            return
        map_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "maps")
        src_prefix = src_rule_id.replace("rule_", "")[:8] + "_" if src_rule_id else ""
        for i, wp in enumerate(waypoints):
            if not isinstance(wp, (list, tuple)) or len(wp) < 2:
                continue
            seg_name = wp[2] if len(wp) >= 3 and wp[2] else f"경유지{i+1}"
            is_boss = int(wp[0]) == 0 and int(wp[1]) == 0
            suffix = "_boss_map.json" if is_boss else "_map.json"
            src_path = os.path.join(map_dir, f"{src_prefix}{i:02d}_{seg_name}{suffix}")
            # 설정 dict 확보
            if len(wp) < 4:
                wp.append({})
            if not isinstance(wp[3], dict):
                wp[3] = {}
            # 원본 맵 파일 존재하면 map_file로 참조 (첫 실행 시 자동 복사)
            if os.path.exists(src_path):
                wp[3]['map_file'] = src_path
            # 잠금 해제
            wp[3].pop('map_locked', None)

    def _copy_rule(self, rule: AutomationRule):
        """규칙 복사 (하위 규칙 포함)"""
        import copy
        # 깊은 복사로 하위 규칙까지 모두 복사
        copied_rule = copy.deepcopy(rule)
        # game_mode 액션이면 game_mode 설정도 함께 저장
        if rule.action_type == "game_mode" and hasattr(self, '_plan') and self._plan.game_modes.get(rule.rule_id):
            copied_rule._game_mode_config = copy.deepcopy(self._plan.game_modes[rule.rule_id])
        set_action_clipboard(copied_rule)
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
        old_rule_id = new_rule.rule_id  # 맵 파일 참조용 원본 ID
        assign_new_ids(new_rule, target_rule.rule_id)

        # game_mode 설정 복원
        if hasattr(new_rule, '_game_mode_config') and new_rule._game_mode_config:
            self._plan.game_modes[new_rule.rule_id] = copy.deepcopy(new_rule._game_mode_config)
            self._setup_copied_game_mode_maps(old_rule_id, self._plan.game_modes[new_rule.rule_id])
            delattr(new_rule, '_game_mode_config')

        # 대상 규칙의 자식으로 추가
        target_rule.children.append(new_rule)
        # 붙여넣은 규칙 + 모든 하위 규칙을 접힌 상태로 등록
        def _collapse_all(r):
            if r.children:
                self._collapsed_items.add(r.rule_id)
            for c in r.children:
                _collapse_all(c)
        _collapse_all(new_rule)
        # 부모도 자식이 생겼으면 접기
        if target_rule.rule_id not in self._collapsed_items and target_rule.children:
            self._collapsed_items.add(target_rule.rule_id)
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
        old_rule_id = new_rule.rule_id  # 맵 파일 참조용 원본 ID
        assign_new_ids(new_rule, None)  # 최상위이므로 parent_id = None

        # game_mode 설정 복원
        if hasattr(new_rule, '_game_mode_config') and new_rule._game_mode_config:
            self._plan.game_modes[new_rule.rule_id] = copy.deepcopy(new_rule._game_mode_config)
            self._setup_copied_game_mode_maps(old_rule_id, self._plan.game_modes[new_rule.rule_id])
            delattr(new_rule, '_game_mode_config')

        # 최상위에 추가
        self._plan.initial_rules.append(new_rule)
        # 붙여넣은 규칙 + 모든 하위 규칙을 접힌 상태로 등록
        def _collapse_all(r):
            if r.children:
                self._collapsed_items.add(r.rule_id)
            for c in r.children:
                _collapse_all(c)
        _collapse_all(new_rule)
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

        # 클립보드 액션과 모든 자식 수집
        all_actions = collect_all_actions(clipboard)
        monitor_actions = []

        for action in all_actions:
            ma = _convert_action_to_monitor_dict(action)
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

        # 클립보드 액션과 모든 자식 수집
        all_actions = collect_all_actions(clipboard)
        monitor_actions = []

        for action in all_actions:
            ma = _convert_action_to_monitor_dict(action)
            if ma:
                monitor_actions.append(ma)

        if not monitor_actions:
            from tkinter import messagebox
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
        import uuid
        dialog = ctk.CTkInputDialog(
            text="입력할 텍스트를 입력하세요:",
            title="텍스트 액션 추가",
        )
        text = dialog.get_input()

        if text:
            new_rule = AutomationRule(
                rule_id=f"rule_{uuid.uuid4().hex[:8]}",
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
        import uuid
        dialog = KeyInputDialog(self)
        key = dialog.get_key()

        if key:
            new_rule = AutomationRule(
                rule_id=f"rule_{uuid.uuid4().hex[:8]}",
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
        import uuid
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
            rule_id=f"rule_{uuid.uuid4().hex[:8]}",
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
                rule_id=f"rule_{uuid.uuid4().hex[:8]}",
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
        import uuid
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
            screenshot = _safe_grab()
            if screenshot is None:
                messagebox.showerror("오류", "스크린샷 캡처 실패 (타임아웃)")
                return
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
                    rule_id=f"rule_{uuid.uuid4().hex[:8]}",
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

    def _open_game_mode_dialog(self, config_rule_id=None):
        """게임 특화모드 설정 다이얼로그 열기

        Args:
            config_rule_id: 편집할 특화모드의 rule_id. None이면 새 특화모드 생성.
        """
        dialog = GameModeDialog(self, self._plan, self._save_plan, self._refresh_action_list,
                                config_rule_id=config_rule_id)
        dialog.grab_set()

    def _on_close(self):
        """닫기"""
        # 실행 중이면 먼저 중지
        if self._is_running:
            self._stop_execution()
            # GameModeDialog 실행 스레드 종료 대기
            _gm = getattr(self, '_gm_dialog', None)
            if _gm:
                _rt = getattr(_gm, '_run_thread', None)
                if _rt is not None and _rt.is_alive():
                    _rt.join(timeout=2.0)
                try:
                    _gm.destroy()
                except Exception:
                    pass
                self._gm_dialog = None

        if self._modified:
            from tkinter import messagebox
            if messagebox.askyesno("저장 확인", "수정된 내용이 있습니다. 저장하시겠습니까?"):
                self._save_plan()
        self.destroy()


class GameModeDialog(ctk.CTkToplevel):
    """게임 특화모드 설정 다이얼로그"""

    def __init__(self, parent, plan: AutomationPlan, save_callback, refresh_callback=None,
                 config_rule_id=None, auto_run=False):
        super().__init__(parent)

        self._plan = plan
        self._save_callback = save_callback
        self._refresh_callback = refresh_callback
        self._thumbnail_refs = []
        self._is_running = False
        self._stop_event = threading.Event()
        self._map_save_lock = threading.Lock()
        self._wp_cards = []
        self._auto_run = auto_run
        self._wp_empty_label = None
        self._config_rule_id = config_rule_id  # None이면 새 특화모드

        from ..analyzer.automation_models import GameModeConfig
        if config_rule_id and config_rule_id in plan.game_modes:
            # 기존 특화모드 편집
            self._config = plan.game_modes[config_rule_id]
        else:
            # 새 특화모드: 기본값 로드
            _defaults_file = DATA_DIR / "game_mode_defaults.json"
            if _defaults_file.exists():
                try:
                    with open(_defaults_file, 'r', encoding='utf-8') as f:
                        _defaults = json.load(f)
                    self._config = GameModeConfig.from_dict(_defaults)
                except Exception:
                    self._config = GameModeConfig()
            else:
                self._config = GameModeConfig()

        self.title("🎮 특화모드")
        self.resizable(True, True)
        self.minsize(1200, 800)

        # 화면 밖에 배치 (UI 구성 중 깜빡임 방지)
        self.geometry("+32000+32000")
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_ui()
        self._update_previews()

        # UI 구성 완료 → 저장된 위치로 이동 + 포커스
        self.after(50, self._restore_and_focus)

        # 자동 실행 모드: UI 구축 후 바로 실행 시작
        if self._auto_run:
            self.after(300, self._start_execution)

    def _build_ui(self):
        """UI 구성"""
        main = ctk.CTkScrollableFrame(self, fg_color="transparent")
        main.pack(fill="both", expand=True, padx=15, pady=10)

        # === 좌표 기반 모드 설정 프레임 ===
        self._coord_mode_frame = ctk.CTkFrame(main, fg_color="transparent")
        self._coord_mode_frame.pack(fill="x")
        self._build_coordinate_ui()

        # 좌표 모드 고정
        self._nav_mode_var = ctk.StringVar(value="coordinate")
        self._config.navigation_mode = "coordinate"

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

        # 설정값 (분석간격, 도달거리)
        vals_row = ctk.CTkFrame(settings_inner, fg_color="transparent")
        vals_row.pack(fill="x")
        ctk.CTkLabel(vals_row, text="분석간격:").pack(side="left")
        self._interval_var = ctk.StringVar(value=str(self._config.analysis_interval))
        ctk.CTkEntry(vals_row, textvariable=self._interval_var, width=50, height=24).pack(side="left", padx=(2, 0))
        ctk.CTkLabel(vals_row, text="초").pack(side="left", padx=(2, 15))
        ctk.CTkLabel(vals_row, text="도달거리:").pack(side="left")
        self._threshold_var = ctk.StringVar(value=str(self._config.arrival_threshold))
        ctk.CTkEntry(vals_row, textvariable=self._threshold_var, width=50, height=24).pack(side="left", padx=(2, 0))
        ctk.CTkLabel(vals_row, text="px").pack(side="left", padx=(2, 0))

        # 이동 스킬 설정
        skill_row = ctk.CTkFrame(settings_inner, fg_color="transparent")
        skill_row.pack(fill="x", pady=(6, 0))
        self._move_skill_enabled_var = ctk.BooleanVar(value=getattr(self._config, 'move_skill_enabled', False))
        ctk.CTkSwitch(skill_row, text="이동스킬", variable=self._move_skill_enabled_var,
                      width=40).pack(side="left")
        self._skill_key_var = ctk.StringVar(value=getattr(self._config, 'move_skill_key', ""))
        ctk.CTkEntry(skill_row, textvariable=self._skill_key_var, width=40, height=24,
                     placeholder_text="키").pack(side="left", padx=(10, 0))
        ctk.CTkLabel(skill_row, text="쿨타임:").pack(side="left", padx=(10, 0))
        self._move_skill_cd_var = ctk.StringVar(value=str(getattr(self._config, 'move_skill_cooldown', 5)))
        ctk.CTkEntry(skill_row, textvariable=self._move_skill_cd_var, width=40, height=24).pack(side="left", padx=(5, 0))
        ctk.CTkLabel(skill_row, text="초",
                     font=ctk.CTkFont(size=10), text_color=COLORS["text_secondary"]).pack(side="left", padx=(5, 0))

        # 상시 스킬 설정
        auto_skill_row = ctk.CTkFrame(settings_inner, fg_color="transparent")
        auto_skill_row.pack(fill="x", pady=(6, 0))
        self._auto_skill_enabled_var = ctk.BooleanVar(value=getattr(self._config, 'auto_skill_enabled', False))
        ctk.CTkSwitch(auto_skill_row, text="상시스킬", variable=self._auto_skill_enabled_var,
                      width=40).pack(side="left")
        self._auto_skill_key_var = ctk.StringVar(value=getattr(self._config, 'auto_skill_key', ""))
        ctk.CTkEntry(auto_skill_row, textvariable=self._auto_skill_key_var, width=40, height=24,
                     placeholder_text="키").pack(side="left", padx=(10, 0))
        ctk.CTkLabel(auto_skill_row, text="쿨타임:").pack(side="left", padx=(10, 0))
        self._auto_skill_cd_var = ctk.StringVar(value=str(getattr(self._config, 'auto_skill_cooldown', 5)))
        ctk.CTkEntry(auto_skill_row, textvariable=self._auto_skill_cd_var, width=40, height=24).pack(side="left", padx=(5, 0))
        ctk.CTkLabel(auto_skill_row, text="초",
                     font=ctk.CTkFont(size=10), text_color=COLORS["text_secondary"]).pack(side="left", padx=(5, 0))
        # 쿨타임 이미지 (같은 행)
        self._auto_skill_cd_preview = ctk.CTkLabel(auto_skill_row, text="없음",
                                                    width=24, height=24,
                                                    font=ctk.CTkFont(size=9))
        self._auto_skill_cd_preview.pack(side="left", padx=(8, 0))
        ctk.CTkButton(auto_skill_row, text="📷", width=24, height=22,
                      font=ctk.CTkFont(size=10),
                      command=self._select_auto_skill_cd_image).pack(side="left", padx=(2, 0))
        ctk.CTkButton(auto_skill_row, text="✕", width=20, height=22,
                      font=ctk.CTkFont(size=10), fg_color="#bf616a",
                      command=self._clear_auto_skill_cd_image).pack(side="left", padx=(1, 0))
        # 검색범위 (같은 행 — ScreenRegionSelector 사용)
        _cd_region = getattr(self._config, 'auto_skill_cd_region', None)
        self._auto_skill_cd_region_label = ctk.CTkLabel(
            auto_skill_row, text=self._format_coord_region(_cd_region),
            font=ctk.CTkFont(size=9), text_color=COLORS["text_secondary"], anchor="w")
        self._auto_skill_cd_region_label.pack(side="left", padx=(6, 0))
        ctk.CTkButton(auto_skill_row, text="범위", width=32, height=22,
                      font=ctk.CTkFont(size=9), fg_color="#5e81ac",
                      command=self._select_auto_skill_cd_region).pack(side="left", padx=(2, 0))
        self._update_previews()

        # 탈출 스킬 설정
        escape_skill_row = ctk.CTkFrame(settings_inner, fg_color="transparent")
        escape_skill_row.pack(fill="x", pady=(6, 0))
        self._escape_skill_enabled_var = ctk.BooleanVar(value=getattr(self._config, 'escape_skill_enabled', False))
        ctk.CTkSwitch(escape_skill_row, text="탈출스킬", variable=self._escape_skill_enabled_var,
                      width=40).pack(side="left")
        self._escape_skill_key_var = ctk.StringVar(value=getattr(self._config, 'escape_skill_key', ""))
        ctk.CTkEntry(escape_skill_row, textvariable=self._escape_skill_key_var, width=40, height=24,
                     placeholder_text="키").pack(side="left", padx=(10, 0))
        ctk.CTkLabel(escape_skill_row, text="쿨타임:").pack(side="left", padx=(10, 0))
        self._escape_skill_cd_var = ctk.StringVar(value=str(getattr(self._config, 'escape_skill_cooldown', 10)))
        ctk.CTkEntry(escape_skill_row, textvariable=self._escape_skill_cd_var, width=40, height=24).pack(side="left", padx=(5, 0))
        ctk.CTkLabel(escape_skill_row, text="초",
                     font=ctk.CTkFont(size=10), text_color=COLORS["text_secondary"]).pack(side="left", padx=(5, 0))

        # 보스 스킬 설정
        boss_skill_row = ctk.CTkFrame(settings_inner, fg_color="transparent")
        boss_skill_row.pack(fill="x", pady=(6, 0))
        self._boss_skill_enabled_var = ctk.BooleanVar(value=getattr(self._config, 'boss_skill_enabled', False))
        ctk.CTkSwitch(boss_skill_row, text="보스스킬", variable=self._boss_skill_enabled_var,
                      width=40).pack(side="left")
        self._boss_skill_key_var = ctk.StringVar(value=getattr(self._config, 'boss_skill_key', ""))
        ctk.CTkEntry(boss_skill_row, textvariable=self._boss_skill_key_var, width=40, height=24,
                     placeholder_text="키").pack(side="left", padx=(10, 0))
        ctk.CTkLabel(boss_skill_row, text="쿨타임:").pack(side="left", padx=(10, 0))
        self._boss_skill_cd_var = ctk.StringVar(value=str(getattr(self._config, 'boss_skill_cooldown', 3)))
        ctk.CTkEntry(boss_skill_row, textvariable=self._boss_skill_cd_var, width=40, height=24).pack(side="left", padx=(5, 0))
        ctk.CTkLabel(boss_skill_row, text="초",
                     font=ctk.CTkFont(size=10), text_color=COLORS["text_secondary"]).pack(side="left", padx=(5, 0))

        # === 실행 컨트롤 섹션 ===
        control_frame = ctk.CTkFrame(main, fg_color=COLORS["bg_card"], corner_radius=10)
        control_frame.pack(fill="x", pady=(0, 8))
        control_inner = ctk.CTkFrame(control_frame, fg_color="transparent")
        control_inner.pack(fill="x", padx=12, pady=8)

        self._status_label = ctk.CTkLabel(control_inner, text="상태: 대기", font=ctk.CTkFont(size=11))
        self._status_label.pack(side="left")

        # === 실시간 정보 섹션 ===
        info_frame = ctk.CTkFrame(main, fg_color=COLORS["bg_card"], corner_radius=10)
        info_frame.pack(fill="x", pady=(0, 8))

        info_header = ctk.CTkFrame(info_frame, fg_color="transparent")
        info_header.pack(fill="x", padx=12, pady=(8, 4))
        ctk.CTkLabel(info_header, text="📊 실시간 정보", font=ctk.CTkFont(size=11, weight="bold")).pack(side="left")
        ctk.CTkButton(info_header, text="로그 지우기", width=70, height=22, fg_color=COLORS["bg_card_hover"],
                      command=self._clear_log).pack(side="right")

        # 상태 표시 영역
        status_row = ctk.CTkFrame(info_frame, fg_color="transparent")
        status_row.pack(fill="x", padx=12, pady=(0, 4))

        # 캐릭터 위치
        char_info = ctk.CTkFrame(status_row, fg_color=COLORS["bg_card_hover"], corner_radius=6)
        char_info.pack(side="left", expand=True, fill="x", padx=(0, 4))
        ctk.CTkLabel(char_info, text="캐릭터", font=ctk.CTkFont(size=9), text_color=COLORS["text_secondary"]).pack()
        self._char_pos_label = ctk.CTkLabel(char_info, text="-", font=ctk.CTkFont(size=12, weight="bold"))
        self._char_pos_label.pack()

        # 목표 위치
        target_info = ctk.CTkFrame(status_row, fg_color=COLORS["bg_card_hover"], corner_radius=6)
        target_info.pack(side="left", expand=True, fill="x", padx=4)
        ctk.CTkLabel(target_info, text="목표", font=ctk.CTkFont(size=9), text_color=COLORS["text_secondary"]).pack()
        self._target_pos_label = ctk.CTkLabel(target_info, text="-", font=ctk.CTkFont(size=12, weight="bold"))
        self._target_pos_label.pack()

        # 거리
        dist_info = ctk.CTkFrame(status_row, fg_color=COLORS["bg_card_hover"], corner_radius=6)
        dist_info.pack(side="left", expand=True, fill="x", padx=4)
        ctk.CTkLabel(dist_info, text="거리", font=ctk.CTkFont(size=9), text_color=COLORS["text_secondary"]).pack()
        self._distance_label = ctk.CTkLabel(dist_info, text="-", font=ctk.CTkFont(size=12, weight="bold"))
        self._distance_label.pack()

        # 이동방향
        dir_info = ctk.CTkFrame(status_row, fg_color=COLORS["bg_card_hover"], corner_radius=6)
        dir_info.pack(side="left", expand=True, fill="x", padx=4)
        ctk.CTkLabel(dir_info, text="방향", font=ctk.CTkFont(size=9), text_color=COLORS["text_secondary"]).pack()
        self._direction_label = ctk.CTkLabel(dir_info, text="-", font=ctk.CTkFont(size=14, weight="bold"))
        self._direction_label.pack()

        # 키입력 횟수
        key_info = ctk.CTkFrame(status_row, fg_color=COLORS["bg_card_hover"], corner_radius=6)
        key_info.pack(side="left", expand=True, fill="x", padx=(4, 0))
        ctk.CTkLabel(key_info, text="키입력", font=ctk.CTkFont(size=9), text_color=COLORS["text_secondary"]).pack()
        self._key_count_label = ctk.CTkLabel(key_info, text="0회", font=ctk.CTkFont(size=12, weight="bold"))
        self._key_count_label.pack()

        # 로그 텍스트 영역 (더 크고 선명한 색상)
        self._log_text = ctk.CTkTextbox(info_frame, height=180, font=ctk.CTkFont(family="Consolas", size=13),
                                         fg_color="#1a1a2e", text_color="#00ff88", corner_radius=6)
        self._log_text.pack(fill="x", padx=12, pady=(4, 8))
        self._log_text.configure(state="disabled")
        self._key_press_count = 0  # 키 입력 횟수 카운터

        # === 버튼 ===
        btn_frame = ctk.CTkFrame(main, fg_color="transparent")
        btn_frame.pack(fill="x", pady=(5, 0))
        ctk.CTkButton(btn_frame, text="저장", width=80, height=32, fg_color="#5e81ac",
                      command=self._save_config_with_msg).pack(side="left")
        ctk.CTkButton(btn_frame, text="닫기", width=80, height=32, fg_color=COLORS["bg_card_hover"],
                      command=self._on_close).pack(side="right")

    def _update_previews(self):
        """상시 스킬 쿨타임 이미지 미리보기 업데이트"""
        auto_cd_img = getattr(self._config, 'auto_skill_cooldown_image', "")
        if auto_cd_img and Path(auto_cd_img).exists():
            self._load_thumb(auto_cd_img, self._auto_skill_cd_preview, size=50)
        else:
            self._auto_skill_cd_preview.configure(image=None, text="없음")

    def _load_thumb(self, path: str, label: ctk.CTkLabel, size: int = 80):
        try:
            from PIL import Image
            img = Image.open(path)
            img.thumbnail((size, size))
            ctk_img = ctk.CTkImage(light_image=img, dark_image=img, size=img.size)
            self._thumbnail_refs.append(ctk_img)
            label.configure(image=ctk_img, text="")
        except Exception as e:
            logger.warning(f"[특화모드] 썸네일 로드 실패: {path} - {e}")
            label.configure(image=None, text="오류")

    def _select_auto_skill_cd_image(self):
        """상시 스킬 쿨타임 이미지 선택"""
        from tkinter import filedialog
        path = filedialog.askopenfilename(filetypes=[("이미지", "*.png *.jpg *.bmp")])
        if path:
            from ..utils.config import DATA_DIR
            import shutil
            dest = DATA_DIR / "templates" / f"game_auto_skill_cd_{Path(path).name}"
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(path, dest)
            self._config.auto_skill_cooldown_image = str(dest)
            self._update_previews()

    def _clear_auto_skill_cd_image(self):
        """상시 스킬 쿨타임 이미지 제거"""
        self._config.auto_skill_cooldown_image = ""
        self._update_previews()

    def _select_auto_skill_cd_region(self):
        """상시 스킬 쿨타임 이미지 검색 영역 선택 (ScreenRegionSelector)"""
        from .analyzer_view import ScreenRegionSelector
        existing = getattr(self._config, 'auto_skill_cd_region', None)

        def on_select(x1, y1, x2, y2):
            self._config.auto_skill_cd_region = [x1, y1, x2, y2]
            self._auto_skill_cd_region_label.configure(
                text=self._format_coord_region([x1, y1, x2, y2]))

        ScreenRegionSelector(self, on_select, lambda: None, existing_region=existing)

    def _toggle_execution(self):
        # 스레드가 죽었는데 _is_running이 True로 남은 경우 강제 리셋
        if self._is_running:
            _rt = getattr(self, '_run_thread', None)
            if _rt is not None and not _rt.is_alive():
                logger.warning("[실행] 스레드 종료됨 but _is_running=True → 강제 리셋")
                self._is_running = False
                self._stop_event.set()
                self._run_btn.configure(text="▶ 전체 테스트", fg_color="#a3be8c")
                if hasattr(self, '_mapping_test_btn'):
                    self._mapping_test_btn.configure(text="▶ 전체맵핑테스트", fg_color="#5e81ac")
                self._status_label.configure(text="상태: 중지됨", text_color="#d08770")
                self._single_waypoint_mode = False
                self._is_mapping_test = False
                return
            self._stop_execution()
        else:
            self._single_waypoint_mode = False
            self._is_mapping_test = False
            self._start_execution()

    def _toggle_mapping_test(self):
        """전체 맵핑테스트 토글 (각 구간 전체맵핑 → 목표좌표 이동)"""
        if self._is_running:
            _rt = getattr(self, '_run_thread', None)
            if _rt is not None and not _rt.is_alive():
                logger.warning("[실행] 스레드 종료됨 but _is_running=True → 강제 리셋")
                self._is_running = False
                self._stop_event.set()
                self._run_btn.configure(text="▶ 전체 테스트", fg_color="#a3be8c")
                self._mapping_test_btn.configure(text="▶ 전체맵핑테스트", fg_color="#5e81ac")
                self._status_label.configure(text="상태: 중지됨", text_color="#d08770")
                self._single_waypoint_mode = False
                self._is_mapping_test = False
                return
            self._stop_execution()
        else:
            self._single_waypoint_mode = False
            self._is_mapping_test = True
            self._start_execution()

    def _run_single_mapping_test(self, idx: int):
        """특정 경유지만 맵핑테스트 (해당 경유지 좌표만 맵핑 후 종료)"""
        if self._is_running:
            prev_idx = getattr(self, '_single_waypoint_idx', -1)
            _rt = getattr(self, '_run_thread', None)
            if _rt is not None and not _rt.is_alive():
                # 스레드 죽음 → 정리 후 새 테스트
                self._is_running = False
                self._stop_event.set()
                if getattr(self, '_is_mapping_test', False) and prev_idx >= 0:
                    self._update_card_mapping_btn(prev_idx, False)
                self._run_btn.configure(text="▶ 전체 테스트", fg_color="#a3be8c")
                if hasattr(self, '_mapping_test_btn'):
                    self._mapping_test_btn.configure(text="▶ 전체맵핑테스트", fg_color="#5e81ac")
            elif getattr(self, '_is_mapping_test', False) and prev_idx == idx:
                # 같은 카드 클릭 → 토글 (중지만)
                self._stop_execution()
                return
            else:
                # 다른 테스트 실행 중 → 중지 후 새 테스트 스케줄
                self._stop_execution()
                self._single_waypoint_mode = True
                self._single_waypoint_idx = idx
                self._is_mapping_test = True
                self._update_card_mapping_btn(idx, True)
                self.after(500, self._start_execution)
                return
        self._single_waypoint_mode = True
        self._single_waypoint_idx = idx
        self._is_mapping_test = True
        # 버튼 → 중지 상태
        self._update_card_mapping_btn(idx, True)
        self.after(300, self._start_execution)

    def _run_single_waypoint(self, idx: int):
        """특정 경유지만 테스트 실행"""
        if self._is_running:
            # 스레드 죽었는데 _is_running True면 강제 리셋
            _rt = getattr(self, '_run_thread', None)
            if _rt is not None and not _rt.is_alive():
                self._is_running = False
                self._stop_event.set()
            else:
                self._stop_execution()
                return
        self._single_waypoint_mode = True
        self._single_waypoint_idx = idx
        # 이전 스레드 finally 정리 대기 후 시작
        self.after(300, self._start_execution)

    def _start_execution(self):
        try:
            self._apply_settings()
        except Exception as e:
            logger.error(f"[실행] 설정 적용 오류: {e}")
        self._is_running = True
        self._stop_event.clear()
        self._key_press_count = 0
        # 일반 전체 테스트에서는 맵 저장/기록 안 함
        self._no_save_mode = (not getattr(self, '_is_mapping_test', False)
                              and not getattr(self, '_is_mapping', False)
                              and not getattr(self, '_single_waypoint_mode', False))
        if getattr(self, '_is_mapping_test', False):
            self._mapping_test_btn.configure(text="■ 중지", fg_color="#bf616a")
        else:
            self._run_btn.configure(text="■ 중지", fg_color="#bf616a")
        self._status_label.configure(text="상태: 실행중", text_color="#a3be8c")
        self._append_log("="*40)
        if getattr(self, '_single_waypoint_mode', False):
            idx = getattr(self, '_single_waypoint_idx', 0)
            self._append_log(f"경유지 {idx+1} 테스트 시작 (ESC로 중지)")
        elif getattr(self, '_is_mapping_test', False):
            self._append_log(f"전체 맵핑테스트 시작 (ESC로 중지)")
        else:
            self._append_log(f"전체 테스트 시작 (ESC로 중지)")
        t = threading.Thread(target=self._run_loop, daemon=True)
        self._run_thread = t
        t.start()

    def _stop_execution(self):
        # 이미 중지된 상태면 무시 (중복 호출 방지)
        if not self._is_running and self._stop_event.is_set():
            return
        was_mapping = getattr(self, '_is_mapping', False)
        mapping_seg = getattr(self, '_current_segment_idx', -1)
        self._stop_event.set()
        self._is_running = False
        self._is_mapping = False

        # UI 업데이트 (예외 발생해도 중지 처리는 계속)
        try:
            self._run_btn.configure(text="▶ 전체 테스트", fg_color="#a3be8c")
            if hasattr(self, '_mapping_test_btn'):
                self._mapping_test_btn.configure(text="▶ 전체맵핑테스트", fg_color="#5e81ac")
            self._status_label.configure(text="상태: 중지됨", text_color="#d08770")
            self._append_log(f"실행 중지 - 총 키입력: {self._key_press_count}회")
            # 맵핑 카드 버튼 복원
            if was_mapping and mapping_seg >= 0:
                self._update_card_mapping_btn(mapping_seg, False)
            # 개별 테스트 카드 버튼 복원
            if getattr(self, '_single_waypoint_mode', False):
                single_idx = getattr(self, '_single_waypoint_idx', -1)
                # 맵핑테스트 버튼 복원
                if getattr(self, '_is_mapping_test', False) and single_idx >= 0:
                    self._update_card_mapping_btn(single_idx, False)
                self._single_waypoint_mode = False
            self._is_mapping_test = False
        except Exception:
            pass

        # 보스 경유지 플래그 리셋 (auto_save 차단 해제 — return 경로 누락 방지 안전망)
        self._boss_segment_active = False

        # 맵 데이터 저장 + 배지 갱신 (모두 백그라운드 스레드에서 수행)
        # 일반 전체 테스트에서는 맵 저장 건너뜀
        _skip_save = getattr(self, '_no_save_mode', False)
        def do_save():
            try:
                if not _skip_save and hasattr(self, '_game_map') and self._game_map.passable:
                    self._auto_save_map()
                    stats = self._game_map.get_statistics()
                    try:
                        self.after(0, lambda s=stats: self._append_log(
                            f"맵 저장 완료 ({s['total_tiles']}개 타일)"))
                    except (tk.TclError, RuntimeError):
                        pass
            except Exception:
                pass
            # 배지 갱신: 파일 I/O를 여기서 수행 (백그라운드), UI 업데이트만 메인스레드로
            self._refresh_badges_async()
        threading.Thread(target=do_save, daemon=True).start()

    def _run_loop(self):
        """좌표 기반 실행 루프"""
        _my_thread = threading.current_thread()
        try:
            self._run_coordinate_loop()
        except Exception as e:
            logger.error(f"[실행루프] 치명적 오류: {e}", exc_info=True)
            self.after(0, lambda err=str(e): self._append_log(f"⚠️ 치명적 오류: {err}"))
        finally:
            # 새 스레드가 이미 시작됐으면 이전 스레드의 정리를 스킵 (레이스 컨디션 방지)
            if getattr(self, '_run_thread', None) is not _my_thread:
                logger.debug("[실행루프] 새 스레드 감지 → 이전 스레드 정리 스킵")
                return
            self._stop_event.set()
            try:
                self.after(0, self._stop_execution)
            except Exception:
                self._is_running = False

    def _run_coordinate_loop(self):
        """좌표 기반 이동 루프 - 단순 알고리즘"""
        from ..utils.digit_templates import get_digit_matcher
        import pyautogui
        import keyboard
        pyautogui.FAILSAFE = False

        # 템플릿 매처 확인
        matcher = get_digit_matcher()
        if not matcher.has_all_templates():
            missing = matcher.get_missing_digits()
            self.after(0, lambda: self._append_log(f"⚠️ 템플릿 미완성: {missing}"))
            self.after(0, self._stop_execution)
            return

        _escape_hotkey_id = keyboard.add_hotkey('escape', self._stop_event.set)
        self._key_press_count = 0

        # 설정값
        smooth_move = getattr(self._config, 'smooth_move', False)
        interval = self._config.analysis_interval

        # 경유지 기반 목표 리스트 (target_x/y 제거, 경유지만 사용)
        waypoints_raw = getattr(self._config, 'waypoints', []) or []
        final_wp_idx = getattr(self._config, 'final_waypoint_idx', -1)
        if final_wp_idx < 0 or final_wp_idx >= len(waypoints_raw):
            final_wp_idx = len(waypoints_raw) - 1

        # 개별 경유지 테스트 모드
        single_mode = getattr(self, '_single_waypoint_mode', False)
        single_idx = getattr(self, '_single_waypoint_idx', 0)

        # 맵핑 모드: 타겟 구간이 final_wp_idx 뒤에 있어도 포함
        if getattr(self, '_is_mapping', False):
            mapping_seg = getattr(self, '_current_segment_idx', 0)
            final_wp_idx = max(final_wp_idx, mapping_seg)
        elif single_mode:
            # 개별 테스트 모드: 해당 경유지만
            final_wp_idx = single_idx

        # 최종 목표까지의 경유지만 포함
        all_targets = []  # [(x, y, name, is_boss, boss_img, char_img, arrival_keys, route_ends), ...]
        start_idx = single_idx if single_mode else 0
        for i, wp in enumerate(waypoints_raw):
            if i < start_idx:
                continue
            if i > final_wp_idx:
                break
            if isinstance(wp, (list, tuple)) and len(wp) >= 2:
                wp_name = wp[2] if len(wp) >= 3 and wp[2] else f"경유지{i+1}"
                is_boss = (int(wp[0]) == 0 and int(wp[1]) == 0)
                boss_img_path = None
                char_img_path = None
                arrival_keys = []
                route_ends = []  # 도착좌표 리스트 [(x,y), ...]
                route_starts = []  # 출발좌표 리스트 [(x,y), ...]
                _disabled_ends = []  # 비활성 도착좌표 (벽으로 처리)
                if len(wp) >= 4 and isinstance(wp[3], dict):
                    boss_img_path = wp[3].get("target_image")
                    char_img_path = wp[3].get("character_image")
                    arrival_keys = wp[3].get("arrival_keys", [])
                    # 구버전 호환
                    if not arrival_keys and wp[3].get("arrival_key"):
                        arrival_keys = [{"key": k.strip(), "wait_after": 0.3}
                                        for k in wp[3]["arrival_key"].split(",") if k.strip()]
                    # 도착좌표 리스트 추출 (활성만 / 비활성은 벽으로)
                    for e in wp[3].get("route_ends", []):
                        ex, ey = e.get("x"), e.get("y")
                        if ex is not None and ey is not None:
                            if e.get("enabled", True):
                                route_ends.append((int(ex), int(ey)))
                            else:
                                _disabled_ends.append((int(ex), int(ey)))
                    # 출발좌표 리스트 추출
                    for s in wp[3].get("route_starts", []):
                        sx, sy = s.get("x"), s.get("y")
                        if sx is not None and sy is not None:
                            route_starts.append((int(sx), int(sy)))
                # 벽좌표 리스트 추출
                route_walls = []
                if len(wp) >= 4 and isinstance(wp[3], dict):
                    for w in wp[3].get("route_walls", []):
                        wx, wy = w.get("x"), w.get("y")
                        if wx is not None and wy is not None:
                            route_walls.append((int(wx), int(wy)))
                # 비활성 도착좌표 → 벽 등록 (맵 오염 방지)
                route_walls.extend(_disabled_ends)
                map_locked = bool(wp[3].get('map_locked')) if len(wp) >= 4 and isinstance(wp[3], dict) else False
                all_targets.append((int(wp[0]), int(wp[1]), wp_name, is_boss, boss_img_path, char_img_path, arrival_keys, route_ends, route_starts, map_locked, route_walls))

        if not all_targets:
            self.after(0, lambda: self._append_log("⚠️ 경유지가 없습니다. 경유지를 추가하세요."))
            self.after(0, self._stop_execution)
            return

        # 맵핑 모드: 해당 구간부터 시작 / 테스트 모드: 처음부터
        if getattr(self, '_is_mapping', False):
            mapping_seg = getattr(self, '_current_segment_idx', 0)
            if 0 <= mapping_seg < len(all_targets):
                target_idx = mapping_seg
            else:
                target_idx = 0
                self._current_segment_idx = 0
        else:
            target_idx = 0
            # 개별 경유지 테스트: 해당 경유지의 맵 로드 (순찰 좌표 등)
            # ※ _current_segment_idx를 미리 변경하면 안 됨!
            #    _switch_segment_map이 현재 인덱스(0)의 맵을 저장한 후 내부에서 변경함
            if single_mode:
                self._switch_segment_map(single_idx)
            else:
                # 전체 테스트: 반드시 세그먼트 0 맵 로드 (이전 테스트 데이터 오염 방지)
                self._switch_segment_map(0)

        import random as _rand

        def _pick_target(tidx):
            """도착좌표가 여러 개면 랜덤 선택, 없으면 기본 wp 좌표"""
            _re = all_targets[tidx][7] if len(all_targets[tidx]) > 7 else []
            if _re:
                tx, ty = _rand.choice(_re)
            else:
                tx, ty = all_targets[tidx][0], all_targets[tidx][1]
            return tx, ty

        target_x, target_y = _pick_target(target_idx)

        self.after(0, lambda tx=target_x, ty=target_y, n=all_targets[target_idx][2]:
            self._append_log(f"🎯 목표: ({tx}, {ty}) [{n}]"))

        # 맵 데이터 사용 여부
        use_map = hasattr(self, '_game_map')
        # 맵 기록: 맵핑테스트에서만 활성화 (전체테스트/부분실행에서는 맵 오염 방지)
        _no_save = getattr(self, '_no_save_mode', False)
        mapping_on = not _no_save  # 맵핑테스트=True, 전체테스트/부분실행=False
        _segment_map_locked = all_targets[target_idx][9] if len(all_targets[target_idx]) > 9 else False
        if _segment_map_locked:
            mapping_on = False

        # 맵 상태 로깅 (UI에도 표시)
        if use_map:
            stats = self._game_map.get_statistics()
            wall_count = stats['blocked_tiles']
            passable_count = stats['passable_tiles']
            soft_count = stats.get('soft_blocked_tiles', 0)
            self.after(0, lambda: self._append_log(f"📍 맵 로드: 이동가능 {passable_count}개, 벽 {wall_count}개, 임시벽 {soft_count}개"))
            if wall_count > 0:
                walls_preview = list(self._game_map.blocked)[:5]
                self.after(0, lambda wp=walls_preview: self._append_log(f"🧱 기존 벽: {wp}..."))
        else:
            self.after(0, lambda: self._append_log("⚠️ 맵 참조 비활성화!"))

        # 4방향 정의
        DIRECTIONS_4 = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}

        # 상태 변수
        prev_x, prev_y = None, None
        last_dir = None
        stuck_count = 0
        total_stuck_count = 0  # 탈출 스킬용 연속 정체 카운트
        tick_counter = 0  # soft_blocked tick용 카운터
        explored_from = {}  # {(x,y): set(방향)} — 자동 탐색용 (최대 500개)
        mark_portal_entry = False  # 구간 전환 후 첫 좌표를 입구 포탈로 등록
        unknown_path_fails = 0  # A* unknown 경로 연속 실패 횟수
        portal_grace = 0  # 구간 전환 후 점프 필터 비활성 카운터
        _jump_reject_coord = None  # 연속 거부된 좌표
        _jump_reject_count = 0  # 연속 거부 횟수
        _pending_start_pos = None  # 포탈 전환 후 등록된 출발지 (교정용)
        self._start_registered = False  # 시작 위치 등록 1회 제한 플래그
        # 전체맵핑: 목표 좌표 없이 현재 위치에서 바로 프런티어 탐색
        mapping_mode = getattr(self, '_mapping_mode', 'path')
        _is_mapping_test = getattr(self, '_is_mapping_test', False)
        # 맵핑테스트: 출발좌표 있는 경유지만 전체맵핑, 없으면 경로맵핑만
        _mt_has_starts = bool(all_targets[target_idx][8]) if _is_mapping_test and len(all_targets[target_idx]) > 8 else False
        full_mapping_exploring = (not _segment_map_locked) and ((getattr(self, '_is_mapping', False) and mapping_mode == "full") or (_is_mapping_test and _mt_has_starts))
        _is_mapping_mode = _is_mapping_test or full_mapping_exploring  # 맵핑 동작 통일 플래그
        # 출발좌표 없는 맵핑테스트: 근처 전체맵핑 후 경로맵핑 (2단계)
        _local_explore_phase = _is_mapping_test and not _mt_has_starts and not _segment_map_locked  # 1단계: 근처 전체맵핑 (잠금맵 제외)
        _local_explore_radius = 10  # 반경 제한 (상하좌우 각 10칸 정사각형)
        _local_explore_center = None  # 시작 위치 (첫 좌표 읽기 시 설정)
        # 보스 던전 상태 변수
        boss_mode = "exploring"       # "exploring" | "patrolling" | "approaching"
        boss_patrol = None             # MapPatroller 인스턴스 (순찰 좌표 기반)
        explore_target = None          # (x,y) — 탐색 목표 (도달 전까지 고정)
        explore_target_tries = 0       # 현재 explore_target 시도 횟수
        explore_unreachable = set()    # 도달 불가 프런티어 좌표들
        _portal_protected = set()      # 포탈 좌표 (절대 unblock 금지)
        _is_backtrack_dir = False      # find_path_direction이 백트래킹으로 반환했는지
        boss_no_frontier_count = 0     # 프런티어 미발견 연속 횟수 (탐색 완료 판정용)
        boss_pre_teleport = False      # 보스 던전 전환 후 텔레포트 대기 (맵 기록 중지)
        boss_approach_steps = 0        # 보스 감지 횟수
        boss_appr_miss = 0            # approaching 모드 연속 미감지 횟수
        boss_first_detect_iter = 0    # 보스 최초 감지 시점 (iteration)
        boss_approaching = False       # (미사용, 호환성)
        boss_approach_target = None    # (미사용, 호환성)
        boss_approach_retries = 0      # (미사용, 호환성)
        boss_approach_cooldown = 0     # (미사용, 호환성)
        _boss_chasing = False          # 보스 추적 중 (반복 간 유지)
        _chase_tx = 0                  # 추적 목표 X
        _chase_ty = 0                  # 추적 목표 Y
        _boss_chase_miss = 0           # 추적 중 보스 미감지 연속 횟수
        patrol_skip_count = 0          # 순찰 스킵 카운트 (로컬)


        # 탈출 스킬 설정
        escape_skill_enabled = getattr(self._config, 'escape_skill_enabled', False)
        escape_skill_key = getattr(self._config, 'escape_skill_key', 'z') or 'z'
        escape_skill_stuck_threshold = 10  # 우회 시도 충분히 한 뒤 발동
        escape_skill_direction_count = getattr(self._config, 'escape_skill_direction_count', 5)
        escape_skill_wait_after = getattr(self._config, 'escape_skill_wait_after', 0.5)
        escape_skill_cooldown = getattr(self._config, 'escape_skill_cooldown', 10.0)
        last_escape_time = 0

        # 상시 스킬 설정
        auto_skill_enabled = getattr(self._config, 'auto_skill_enabled', False)
        auto_skill_key = getattr(self._config, 'auto_skill_key', '') or ''
        auto_skill_cooldown = getattr(self._config, 'auto_skill_cooldown', 5.0)
        # last_auto_skill_time 제거됨 (쿨타임 타이머 미사용)
        auto_skill_cd_image = getattr(self._config, 'auto_skill_cooldown_image', '') or ''
        if auto_skill_cd_image and not Path(auto_skill_cd_image).exists():
            auto_skill_cd_image = ''  # 파일 없으면 타이머 폴백
        auto_skill_cd_region = getattr(self._config, 'auto_skill_cd_region', None)  # [x1,y1,x2,y2]
        # 쿨타임 이미지 미리 로드 (매번 imread 방지)
        _auto_skill_cd_tmpl = None
        if auto_skill_cd_image:
            try:
                import cv2 as _ascv_init
                _auto_skill_cd_tmpl = _ascv_init.imread(auto_skill_cd_image)
            except Exception:
                pass
        if auto_skill_enabled and auto_skill_key:
            _as_mode = "이미지" if _auto_skill_cd_tmpl is not None else f"타이머({auto_skill_cooldown}초)"
            self.after(0, lambda k=auto_skill_key, m=_as_mode:
                self._append_log(f"🔄 상시스킬: 키={k}, 쿨타임={m}"))

        # 보스 스킬 설정
        boss_skill_enabled = getattr(self._config, 'boss_skill_enabled', False)
        boss_skill_key = getattr(self._config, 'boss_skill_key', '') or ''
        boss_skill_cooldown = getattr(self._config, 'boss_skill_cooldown', 3.0)
        last_boss_skill_time = 0

        # A* 경로탐색 사용
        from ..player.simple_pathfinder import SimplePathfinder
        pathfinder = SimplePathfinder(self._game_map)
        current_path = []  # 현재 따라갈 경로
        path_index = 0     # 경로에서 현재 위치
        path_pos_index = {}  # 경로 위치 → 인덱스 역매핑 (O(1) 조회)
        recent_positions = []  # 최근 방문 위치 (반복 감지용)

        def _rebuild_path_index():
            """경로 변경 시 역매핑 재구축 (중복 좌표는 첫 등장만 유지)"""
            nonlocal path_pos_index
            path_pos_index = {}
            for i, pos in enumerate(current_path):
                if pos not in path_pos_index:
                    path_pos_index[pos] = i

        def press_key(direction):
            """방향키 누르기"""
            if self._stop_event.is_set():
                return
            _t_pk = time.time()
            key = self._config.move_keys.get(direction, direction)
            if smooth_move:
                self._smooth_key_input_ui([key], interval)
            else:
                # PAUSE=0 강제 (rule_executor/action_player가 0.1~0.3 설정 → press() 1회당 0.2~0.6초 낭비 방지)
                _orig_pause = pyautogui.PAUSE
                pyautogui.PAUSE = 0
                try:
                    pyautogui.press(key)
                finally:
                    pyautogui.PAUSE = _orig_pause
                self._key_press_count += 1
                # 중단 가능한 sleep
                sleep_until = time.time() + interval
                while time.time() < sleep_until:
                    if self._stop_event.is_set():
                        return
                    time.sleep(0.02)
            _t_pk_ms = int((time.time() - _t_pk) * 1000)
            logger.info(f"[타이밍] press_key: {_t_pk_ms}ms interval={interval} smooth={smooth_move}")

        def find_path_direction(cx, cy, tx, ty):
            """
            A* 경로탐색 + 자동 탐색으로 다음 방향 결정
            1. 알려진 경로로 탐색
            2. 미탐색 영역 포함해서 탐색
            3. 스마트 탐색 (미시도 방향 우선)
            4. 비벽 방향 재시도 (백트래킹)
            5. 최후 직진
            """
            nonlocal current_path, path_index, path_pos_index, explored_from, unknown_path_fails, _is_backtrack_dir

            _is_backtrack_dir = False
            current_pos = (cx, cy)
            target_pos = (tx, ty)

            # 경로가 있고 현재 위치가 경로 상에 있으면 따라가기
            if current_path and path_index < len(current_path):
                idx = path_pos_index.get(current_pos)
                if idx is not None:
                    path_index = idx
                    if path_index + 1 < len(current_path):
                        next_pos = current_path[path_index + 1]
                        # 다음 타일이 벽/소프트블록/출발지이면 캐시 무효화 → A* 재계산
                        _is_start = (self._game_map.start_pos is not None and next_pos == self._game_map.start_pos)
                        if use_map and (_is_start or
                                        self._game_map.is_blocked(next_pos[0], next_pos[1]) or
                                        self._game_map.is_soft_blocked(next_pos[0], next_pos[1])):
                            current_path = None
                            path_index = 0
                            path_pos_index = {}
                        else:
                            dx = next_pos[0] - cx
                            dy = next_pos[1] - cy
                            for d, (ddx, ddy) in DIRECTIONS_4.items():
                                if ddx == dx and ddy == dy:
                                    remaining = len(current_path) - path_index - 1
                                    if _ui_update_ok:
                                        self.after(0, lambda d2=d, r=remaining:
                                            self._append_log(f"🚶 경로추적 {d2} (잔여:{r}칸)"))
                                    return d

            _manhattan = abs(cx - tx) + abs(cy - ty)
            _skip_to_explore = False  # 최단경로 모드 A* 실패 시 True → 1차~2차 건너뜀

            # ── 맵핑테스트 최단경로 전용 모드 ──────────────────────────
            # 맵 데이터 충분(50타일+) OR 출발좌표 없는 맵핑테스트:
            # allow_unknown A*로 최단 경로 (캐릭터가 맵 밖이어도 미지 영역 통과)
            # 벽 발견 → 맵 갱신 → 다음 호출에서 A* 재계산 → 자연 수렴
            # 재생/전체테스트/부분실행은 _is_mapping_test=False → 절대 진입 안 함
            if _is_mapping_test and (not full_mapping_exploring or _mt_has_map):
                _sp_result = pathfinder.find_path(
                    current_pos, target_pos, allow_unknown=True,
                    stop_event=self._stop_event,
                    max_iterations=max(_manhattan * 30, 20000),
                    unknown_cost=3)  # 미탐색 비용 3배 → 알려진 경로 우선
                if self._stop_event.is_set():
                    return None
                if _sp_result.found and _sp_result.directions:
                    current_path = _sp_result.path
                    path_index = 0
                    _rebuild_path_index()
                    if _ui_update_ok:
                        self.after(0, lambda l=len(_sp_result.path):
                            self._append_log(f"🛤️ 최단경로: {l}칸"))
                    return _sp_result.directions[0]
                # A* 실패 (사방 벽 등 극단적 경우) → 1차~2차 건너뛰고 3차로 직행
                _skip_to_explore = True

            # 1차~2차: 일반 모드 경로 탐색 (최단경로 맵핑 모드에서는 건너뜀)
            if not _skip_to_explore:
                # 1차: 알려진 이동가능 경로만 (soft_blocked 제외 — 깨끗한 우회)
                # allow_unknown=False → 탐색 범위가 알려진 타일로 한정되므로 max_iterations 불필요
                result_clean = pathfinder.find_path(current_pos, target_pos, allow_unknown=False,
                                              stop_event=self._stop_event, allow_soft_blocked=False)
                if self._stop_event.is_set():
                    return None
                time.sleep(0)  # GIL 해제 (Stage1 후)

                _max_clean = max(int(_manhattan * 5), 100)
                # 알려진 경로가 맨해튼 대비 과도하게 길면 미탐색 직선 경로가 나을 수 있음
                _known_too_long = (result_clean.found and len(result_clean.path) > max(_manhattan * 3, 30))

                if result_clean.found and len(result_clean.directions) > 0 and len(result_clean.path) <= _max_clean and not _known_too_long:
                    # 깨끗한 경로가 합리적 길이 → 바로 사용
                    current_path = result_clean.path
                    path_index = 0
                    _rebuild_path_index()
                    unknown_path_fails = 0
                    if _ui_update_ok:
                        self.after(0, lambda l=len(result_clean.path): self._append_log(f"🛤️ 경로 발견: {l}칸"))
                    return result_clean.directions[0]

                time.sleep(0)  # GIL 해제 (Stage1.5 전)
                # 1.5차: soft_blocked 포함 (우회로 없거나 1차 경로가 너무 길 때)
                result = pathfinder.find_path(current_pos, target_pos, allow_unknown=False,
                                              stop_event=self._stop_event, allow_soft_blocked=True)
                if self._stop_event.is_set():
                    return None
                # 1.5차 결과 중 best known path 선택
                _best_known = None
                if result.found and len(result.directions) > 0:
                    if result_clean.found and len(result.path) >= len(result_clean.path):
                        _best_known = result_clean
                    else:
                        _best_known = result
                elif result_clean.found and len(result_clean.directions) > 0:
                    _best_known = result_clean

                # 2차: 미탐색 영역 포함 경로 시도
                # 맵핑테스트/전체맵핑에서는 사용 안 함 (사용자 설정 벽 주변 미탐색 영역 잘못 진입 방지)
                # 조건: (1) known 경로 없음, (2) known 경로가 맨해튼 대비 과도하게 김, (3) 연속 실패 < 3
                _try_unknown = (not _is_mapping_mode and unknown_path_fails < 3 and
                                (_best_known is None or len(_best_known.path) > max(_manhattan * 3, 30)))
                _result_unknown = None
                if _try_unknown:
                    time.sleep(0)  # GIL 해제 (Stage2 전)
                    _result_unknown = pathfinder.find_path(current_pos, target_pos, allow_unknown=True,
                                                           stop_event=self._stop_event,
                                                           max_iterations=max(_manhattan * 30, 20000),
                                                           unknown_cost=3)  # 미탐색 비용 3배 → 알려진 경로 우선
                    if self._stop_event.is_set():
                        return None
                    if _result_unknown.found and len(_result_unknown.directions) > 0:
                        _max_path = max(int(_manhattan * 5), 100)
                        if len(_result_unknown.path) > _max_path:
                            if _ui_update_ok:
                                self.after(0, lambda l=len(_result_unknown.path), m=_max_path:
                                    self._append_log(f"⚠️ 우회 {l}칸 > 최대 {m}칸 → 스킵"))
                            unknown_path_fails += 1
                            _result_unknown = None
                    else:
                        _result_unknown = None

                # 최종 선택: known vs unknown 중 짧은 경로
                if _best_known and _result_unknown:
                    if len(_result_unknown.path) < len(_best_known.path):
                        # 미탐색 경로가 더 짧음 → 직선으로 시도
                        current_path = _result_unknown.path
                        path_index = 0
                        _rebuild_path_index()
                        if _ui_update_ok:
                            self.after(0, lambda l=len(_result_unknown.path), kl=len(_best_known.path):
                                self._append_log(f"🔍 직선 경로: {l}칸 (기존 우회 {kl}칸)"))
                        return _result_unknown.directions[0]
                    else:
                        # 알려진 경로가 더 짧거나 같음
                        current_path = _best_known.path
                        path_index = 0
                        _rebuild_path_index()
                        unknown_path_fails = 0
                        _label = "임시벽 통과" if _best_known is result else "경로"
                        if _ui_update_ok:
                            self.after(0, lambda l=len(_best_known.path), lb=_label: self._append_log(f"🛤️ {lb} 발견: {l}칸"))
                        return _best_known.directions[0]
                elif _best_known:
                    current_path = _best_known.path
                    path_index = 0
                    _rebuild_path_index()
                    unknown_path_fails = 0
                    if _ui_update_ok:
                        self.after(0, lambda l=len(_best_known.path): self._append_log(f"🛤️ 경로 발견: {l}칸"))
                    return _best_known.directions[0]
                elif _result_unknown:
                    current_path = _result_unknown.path
                    path_index = 0
                    _rebuild_path_index()
                    if _ui_update_ok:
                        self.after(0, lambda l=len(_result_unknown.path): self._append_log(f"🔍 탐색 경로: {l}칸 (미지 영역 포함)"))
                    return _result_unknown.directions[0]

            # 3차: 스마트 탐색 — 현재 위치에서 아직 안 가본 방향 시도
            _start_pos = self._game_map.start_pos
            tried = explored_from.get(current_pos, set())
            candidates = []
            for d, (ddx, ddy) in DIRECTIONS_4.items():
                nx, ny = cx + ddx, cy + ddy
                if d not in tried and not self._game_map.is_blocked(nx, ny):
                    if _start_pos is not None and (nx, ny) == _start_pos:
                        continue  # 출발지 회피
                    dist = abs(nx - tx) + abs(ny - ty)
                    candidates.append((dist, d))

            if candidates:
                candidates.sort()
                chosen = candidates[0][1]
                tried.add(chosen)
                explored_from[current_pos] = tried
                if _ui_update_ok:
                    self.after(0, lambda d=chosen: self._append_log(f"🔍 탐색: {d}"))
                return chosen

            # 4차: 이미 시도했지만 벽이 아닌 방향 재시도 (백트래킹)
            _is_backtrack_dir = True
            # 최근 방문 위치 회피 + 반복 감지 시 벽 해제
            recent_positions.append((cx, cy))
            if len(recent_positions) > 20:
                recent_positions.pop(0)

            # 반복 감지: 최근 10회 중 같은 위치 5회 이상 → 인접 벽 해제
            if len(recent_positions) >= 10:
                from collections import Counter
                pos_counts = Counter(recent_positions[-10:])
                most_common_count = pos_counts.most_common(1)[0][1]
                if most_common_count >= 5:
                    unblocked = 0
                    for d2, (ddx2, ddy2) in DIRECTIONS_4.items():
                        nb = (cx + ddx2, cy + ddy2)
                        if nb in _portal_protected:
                            continue  # 포탈 좌표 보호
                        if self._game_map.is_blocked(nb[0], nb[1]):
                            self._game_map.mark_passable(nb[0], nb[1])
                            unblocked += 1
                        elif self._game_map.is_soft_blocked(nb[0], nb[1]):
                            self._game_map.mark_passable(nb[0], nb[1])
                            unblocked += 1
                    if unblocked > 0:
                        if _ui_update_ok:
                            self.after(0, lambda u=unblocked, cx2=cx, cy2=cy:
                                self._append_log(f"🔓 반복감지({cx2},{cy2}) → {u}개 인접벽 해제"))
                        recent_positions.clear()
                        # 현재 위치의 explored_from 초기화 (해제된 벽 방향 재시도 허용)
                        explored_from.pop(current_pos, None)
                        current_path = []
                        path_index = 0
                        path_pos_index = {}
                        # return None 제거: 벽 해제 후 아래 백트래킹으로 즉시 방향 결정

            # 최근 방문 위치를 피해서 백트래킹
            recent_set = set(recent_positions[-6:])
            tried_dirs = explored_from.get((cx, cy), set())
            backtrack_candidates = []
            for d, (ddx, ddy) in DIRECTIONS_4.items():
                nx, ny = cx + ddx, cy + ddy
                if _start_pos is not None and (nx, ny) == _start_pos:
                    continue  # 출발지 회피
                if not self._game_map.is_blocked(nx, ny) and not self._game_map.is_soft_blocked(nx, ny):
                    backtrack_candidates.append((d, (nx, ny)))
            # 맵핑테스트: 목표 방향 우선 정렬 (먼 곳 탐색 방지, 전체맵핑 중엔 비활성)
            if _is_mapping_test and not full_mapping_exploring and backtrack_candidates:
                backtrack_candidates.sort(key=lambda x: abs(x[1][0] - tx) + abs(x[1][1] - ty))
            if backtrack_candidates:
                # 이미 실패한 방향 제외 + 최근 방문 안 한 곳 우선
                for d, pos in backtrack_candidates:
                    if d not in tried_dirs and pos not in recent_set and d != last_dir:
                        if _ui_update_ok:
                            self.after(0, lambda d2=d: self._append_log(f"↩ 되돌아가기 ({d2})"))
                        return d
                # 실패 방향 제외만
                for d, pos in backtrack_candidates:
                    if d not in tried_dirs and d != last_dir:
                        if _ui_update_ok:
                            self.after(0, lambda d2=d: self._append_log(f"↩ 되돌아가기 ({d2})"))
                        return d
                # 실패 방향뿐이면 최근 방문 안 한 곳이라도
                for d, pos in backtrack_candidates:
                    if pos not in recent_set:
                        if _ui_update_ok:
                            self.after(0, lambda d2=d: self._append_log(f"↩ 되돌아가기 ({d2})"))
                        return d
                # 전부 실패 방향뿐이면 어쩔 수 없이 사용
                if _ui_update_ok:
                    self.after(0, lambda d2=backtrack_candidates[0][0]: self._append_log(f"↩ 되돌아가기 ({d2})"))
                return backtrack_candidates[0][0]

            # 5차: 사방이 벽 (진짜 막다른 길) — 방향 순환 시도
            if _ui_update_ok:
                self.after(0, lambda: self._append_log("⚠️ 막다른 길!"))
            dirs = list(DIRECTIONS_4.keys())  # [up, down, left, right]
            # 직전 실패 방향 회피
            for offset in range(1, 5):
                d = dirs[(iteration + offset) % 4]
                if d != last_dir:
                    return d
            return dirs[iteration % 4]

        frontier_cooldown = set()  # 최근 도달/무효화된 프런티어 (재선택 방지)
        frontier_cooldown_iter = 0  # 쿨다운 리셋 기준 반복

        def _find_nearest_frontier(cx, cy, max_radius=150, explore_center=None, explore_radius=0):
            """가장 가까운 frontier 찾기 (BFS - 가까운 것부터 탐색)
            explore_center/explore_radius: 근처맵핑 시 미탐색 이웃도 반경 내여야 프론티어 판정
            """
            from collections import deque
            _delta_to_dir = {(0, -1): "up", (0, 1): "down", (-1, 0): "left", (1, 0): "right"}
            _start_pos = self._game_map.start_pos
            visited = {(cx, cy)}
            queue = deque([(cx, cy, 0)])
            _fnf_iter = 0
            while queue:
                _fnf_iter += 1
                if _fnf_iter % 100 == 0:
                    time.sleep(0)  # GIL 해제
                px, py, dist = queue.popleft()
                if dist > max_radius:  # 최대 탐색 거리
                    break
                # frontier 체크 (explore_unreachable은 프런티어 반환만 스킵, 이웃 확장은 허용)
                if (px, py) not in explore_unreachable:
                    if (px, py) in self._game_map.passable:
                        if (px, py) not in frontier_cooldown:
                            _ef = explored_from.get((px, py), set())
                            for ddx, ddy in [(0, -1), (0, 1), (-1, 0), (1, 0)]:
                                nx, ny = px + ddx, py + ddy
                                if not self._game_map.is_known(nx, ny):
                                    # 근처맵핑: 미탐색 이웃이 반경 밖이면 프론티어 판정 제외
                                    if explore_center is not None:
                                        _ed = max(abs(nx - explore_center[0]), abs(ny - explore_center[1]))
                                        if _ed > explore_radius:
                                            continue
                                    # 이미 시도+실패한 방향은 frontier 판정에서 제외
                                    if _delta_to_dir.get((ddx, ddy)) in _ef:
                                        continue
                                    return (px, py)
                # 인접 타일 탐색 (passable만, start_pos 제외 — A* 일관성)
                for ddx, ddy in [(0, -1), (0, 1), (-1, 0), (1, 0)]:
                    nx, ny = px + ddx, py + ddy
                    if (nx, ny) not in visited and (nx, ny) in self._game_map.passable:
                        if _start_pos is None or (nx, ny) != _start_pos:
                            visited.add((nx, ny))
                            queue.append((nx, ny, dist + 1))
            # 쿨다운 때문에 못 찾았으면 쿨다운 무시하고 재시도
            if frontier_cooldown:
                frontier_cooldown.clear()
                return _find_nearest_frontier(cx, cy, max_radius=max_radius, explore_center=explore_center, explore_radius=explore_radius)
            return None

        try:
            # 게임 창 전환 대기 — stop_event 오염 방지 위해 직전 재클리어
            if self._stop_event.is_set():
                logger.warning("[좌표모드] 2초 대기 전 stop_event가 이미 set → 재클리어")
                self._stop_event.clear()
            # stop_event.wait 사용 → 중지 즉시 반응 (time.sleep 대신)
            if self._stop_event.wait(2.0):
                self.after(0, lambda: self._append_log("⚠️ 2초 대기 중 중지 감지"))
                self.after(0, self._stop_execution)
                return

            iteration = 0
            max_iterations = 50000 if _is_mapping_mode else 5000
            coord_fail_count = 0
            max_coord_fails = 50  # 연속 50회 실패 시 중단
            while not self._stop_event.is_set() and iteration < max_iterations:
                iteration += 1
                _ui_update_ok = (iteration % 10 == 0)  # UI 업데이트는 10회당 1번 (Tkinter 큐 폭주 방지)
                _t_iter_start = time.time()

                # 중지 확인 (좌표 읽기 전 즉시 체크)
                if self._stop_event.is_set():
                    break

                # 좌표 읽기 (stop_event 전달 → 중지 시 즉시 반환)
                _t_ocr_start = time.time()
                current_x, current_y = matcher.read_both_coordinates(
                    self._config.coord_x_region,
                    self._config.coord_y_region,
                    "X", "Y",
                    stop_event=self._stop_event
                )
                _t_ocr_ms = int((time.time() - _t_ocr_start) * 1000)

                # 좌표 읽기 후 중지 재확인
                if self._stop_event.is_set():
                    break

                if current_x is None or current_y is None:
                    coord_fail_count += 1
                    if coord_fail_count >= max_coord_fails:
                        self.after(0, lambda: self._append_log(f"❌ 좌표 읽기 연속 {max_coord_fails}회 실패 → 중단"))
                        self.after(0, self._stop_execution)
                        return
                    if coord_fail_count % 5 == 1:
                        self.after(0, lambda c=coord_fail_count: self._append_log(f"⚠️ 좌표 읽기 실패 ({c}/{max_coord_fails})"))
                    if _ui_update_ok:
                        self.after(0, lambda: self._status_label.configure(text="좌표 읽기 실패"))
                    self._stop_event.wait(0.1)
                    continue

                coord_fail_count = 0  # 성공 시 리셋
                # 좌표를 정수로 확실하게 변환
                current_x = int(current_x)
                current_y = int(current_y)

                # 좌표 범위 검증 (OCR 오독 필터링)
                # 1) 절대 범위: 500 초과 무시
                if abs(current_x) > 500 or abs(current_y) > 500:
                    if iteration % 10 == 1:
                        self.after(0, lambda cx=current_x, cy=current_y:
                            self._append_log(f"⚠️ 좌표 범위 초과 무시: ({cx},{cy})"))
                    self._stop_event.wait(0.1)
                    continue
                # 2) 이전 좌표 대비 점프 거리: 30칸 이상이면 OCR 오독 의심
                if portal_grace > 0:
                    portal_grace -= 1
                    _jump_reject_coord = None
                    _jump_reject_count = 0
                    # 포탈 전환 후 좌표 안정화: 로딩 중 잘못된 좌표로 출발지 등록됐으면 교정
                    if _pending_start_pos is not None and mapping_on and use_map:
                        dist_from_pending = abs(current_x - _pending_start_pos[0]) + abs(current_y - _pending_start_pos[1])
                        if dist_from_pending >= 5:
                            old_sp = _pending_start_pos
                            _pending_start_pos = (current_x, current_y)
                            self._game_map.start_pos = _pending_start_pos
                            self._game_map.mark_blocked(current_x, current_y)
                            # start_pos 업데이트 후 이전 위치를 passable로 변경 (미탐색 상태 방지)
                            if old_sp not in _portal_protected:
                                self._game_map.mark_passable(old_sp[0], old_sp[1])
                            self.after(0, lambda ox=old_sp[0], oy=old_sp[1], cx=current_x, cy=current_y:
                                self._append_log(f"🔄 출발지 교정: ({ox},{oy})→({cx},{cy})"))
                    # portal_grace 만료 → 교정 종료
                    if portal_grace == 0:
                        _pending_start_pos = None
                elif prev_x is not None:
                    jump = abs(current_x - prev_x) + abs(current_y - prev_y)
                    if jump >= 30:
                        # 목표 근접(1칸 이내)에서 점프 → 포탈 전환, 차단 안 함
                        # 도착좌표가 여러 개면 어느 하나라도 근접하면 허용 (포탈 감지와 동일 로직)
                        was_near = abs(prev_x - target_x) + abs(prev_y - target_y) <= 1
                        if not was_near:
                            _jf_re = all_targets[target_idx][7] if len(all_targets[target_idx]) > 7 else []
                            if _jf_re:
                                was_near = any(abs(prev_x - ex) + abs(prev_y - ey) <= 1 for ex, ey in _jf_re)
                        # 포탈 전환 중이면 허용 (mark_portal_entry, boss_pre_teleport, 목표 근접)
                        if not mark_portal_entry and not boss_pre_teleport and not was_near:
                            # 맵핑테스트/전체맵핑: 30칸+ 점프도 포탈로 처리 (벽 등록 + 중지)
                            if _is_mapping_mode and mapping_on and use_map:
                                if last_dir:
                                    _pdx, _pdy = DIRECTIONS_4.get(last_dir, (0, 0))
                                    _portal_tile = (prev_x + _pdx, prev_y + _pdy)
                                    self._game_map.mark_blocked(_portal_tile[0], _portal_tile[1])
                                    _portal_protected.add(_portal_tile)
                                    self.after(0, lambda pt=_portal_tile, cx=current_x, cy=current_y, px=prev_x, py=prev_y:
                                        self._append_log(f"🚪 맵핑 중 포탈 감지: ({px},{py})→({cx},{cy}) — {pt} 벽 등록"))
                                # 맵핑 중 포탈: 벽 등록 후 탐색 계속 (중지 안 함)
                                self._game_map.mark_passable(current_x, current_y)
                                current_path = []
                                stuck_count = 0
                                last_dir = None
                                prev_x, prev_y = current_x, current_y  # 포탈 후 prev 갱신 (재감지 방지)
                                self._stop_event.wait(0.05)
                                continue
                            # 비정상 좌표 점프: portal_grace/mark_portal_entry로 처리 안 된 점프
                            if (current_x, current_y) == _jump_reject_coord:
                                _jump_reject_count += 1
                                if _jump_reject_count >= 3:
                                    # 동일 좌표 3회 반복 = 실제 좌표 변경이지만 예상된 포탈 아님
                                    # → 맵 오염 방지를 위해 실행 중지
                                    self.after(0, lambda cx=current_x, cy=current_y, px=prev_x, py=prev_y, j=jump:
                                        self._append_log(f"🛑 비정상 좌표 점프: ({px},{py})→({cx},{cy}) 거리={j} — 실행 중지"))
                                    self._stop_event.set()
                                    break
                            else:
                                _jump_reject_coord = (current_x, current_y)
                                _jump_reject_count = 1
                            self.after(0, lambda cx=current_x, cy=current_y, px=prev_x, py=prev_y, j=jump:
                                self._append_log(f"⚠️ 좌표 점프 무시: ({px},{py})→({cx},{cy}) 거리={j}"))
                            self._stop_event.wait(0.1)
                            continue
                    elif (full_mapping_exploring or _local_explore_phase) and jump >= 5:
                        # 맵핑 중 5칸+ 점프 = 포탈 밟음 → 포탈 타일 벽 등록 후 탐색 계속
                        if last_dir and mapping_on and use_map:
                            _pdx, _pdy = DIRECTIONS_4.get(last_dir, (0, 0))
                            _portal_tile = (prev_x + _pdx, prev_y + _pdy)
                            self._game_map.mark_blocked(_portal_tile[0], _portal_tile[1])
                            _portal_protected.add(_portal_tile)
                            self.after(0, lambda pt=_portal_tile, cx=current_x, cy=current_y, px=prev_x, py=prev_y:
                                self._append_log(f"🚪 맵핑 중 포탈 감지: ({px},{py})→({cx},{cy}) — {pt} 벽 등록"))
                        # 맵핑 중 포탈: 벽 등록 후 탐색 계속 (중지 안 함)
                        self._game_map.mark_passable(current_x, current_y)
                        current_path = []
                        stuck_count = 0
                        last_dir = None
                        prev_x, prev_y = current_x, current_y  # 포탈 후 prev 갱신 (재감지 방지)
                        self._stop_event.wait(0.05)
                        continue
                    else:
                        _jump_reject_coord = None
                        _jump_reject_count = 0

                # 구간 전환 후 첫 좌표 → 출발지 + 벽 등록 (되돌아가기 방지)
                # 즉시 등록하되, 좌표가 나중에 바뀌면 start_pos도 갱신됨
                if mark_portal_entry and mapping_on and use_map:
                    mark_portal_entry = False
                    # 맵핑테스트 + 출발좌표 없음 = 랜덤출발 → 출발지 벽 등록 안 함
                    _mt_no_starts = _is_mapping_test and not (len(all_targets[target_idx]) > 8 and all_targets[target_idx][8])
                    _pending_start_pos = (current_x, current_y)
                    if _mt_no_starts:
                        self._game_map.start_pos = None
                        self._game_map.mark_passable(current_x, current_y)
                        self.after(0, lambda cx=current_x, cy=current_y:
                            self._append_log(f"🚪 출발지 등록 (벽 없음, 랜덤출발): ({cx},{cy})"))
                    else:
                        self._game_map.start_pos = _pending_start_pos
                        self._game_map.mark_blocked(current_x, current_y)
                        self.after(0, lambda cx=current_x, cy=current_y:
                            self._append_log(f"🚪 출발지 등록 + 벽: ({cx},{cy})"))
                    # 맵핑테스트: 전체맵핑 설정 (start_pos 해제 + 도착지/출발지 벽)
                    if full_mapping_exploring:
                        # start_pos 먼저 해제 → mark_passable이 start_pos 보호에 걸리지 않음
                        self._game_map.start_pos = None
                        self._game_map.mark_passable(current_x, current_y)
                        _pe_x, _pe_y = target_x, target_y
                        if _pe_x is not None and _pe_y is not None and (_pe_x, _pe_y) != (current_x, current_y):
                            if _is_mapping_test:
                                self._game_map.mark_passable(_pe_x, _pe_y)
                            else:
                                self._game_map.mark_blocked(_pe_x, _pe_y)
                            _portal_protected.add((_pe_x, _pe_y))
                        _pe_routes = all_targets[target_idx][7] if len(all_targets[target_idx]) > 7 else []
                        for _pex, _pey in _pe_routes:
                            if (_pex, _pey) != (current_x, current_y):
                                if _is_mapping_test:
                                    self._game_map.mark_passable(_pex, _pey)
                                else:
                                    self._game_map.mark_blocked(_pex, _pey)
                                _portal_protected.add((_pex, _pey))
                        # 출발좌표(출입구 포탈): 진입 타일은 passable 유지 (탐색 후 복귀 가능)
                        # 다른 출발좌표만 blocked 등록
                        _pe_starts = all_targets[target_idx][8] if len(all_targets[target_idx]) > 8 else []
                        for _psx, _psy in _pe_starts:
                            if (_psx, _psy) != (current_x, current_y):
                                self._game_map.mark_blocked(_psx, _psy)
                            _portal_protected.add((_psx, _psy))
                        self.after(0, lambda cx=current_x, cy=current_y:
                            self._append_log(f"📍 전체맵핑 시작: ({cx},{cy})"))
                    # 사용자 지정 벽좌표 등록
                    _route_walls = all_targets[target_idx][10] if len(all_targets[target_idx]) > 10 else []
                    for _wx, _wy in _route_walls:
                        self._game_map.mark_blocked(_wx, _wy)
                        _portal_protected.add((_wx, _wy))
                    if _route_walls:
                        self.after(0, lambda n=len(_route_walls): self._append_log(f"🧱 벽좌표 {n}개 등록"))

                # 첫 반복: 시작 위치 등록 (1회만 실행)
                if prev_x is None and mapping_on and use_map and not getattr(self, '_start_registered', False):
                    self._start_registered = True
                    # 맵핑테스트 + 출발좌표 없음 = 랜덤출발 → 출발지 벽 등록 안 함
                    _mt_no_starts_init = _is_mapping_test and not (len(all_targets[target_idx]) > 8 and all_targets[target_idx][8])
                    # 출발지 저장
                    if _mt_no_starts_init:
                        self._game_map.start_pos = None
                        self._game_map.mark_passable(current_x, current_y)
                    elif full_mapping_exploring:
                        # 전체맵핑: entry tile passable + start_pos=None
                        # → A*/BFS가 entry tile 경유 가능 → 인접 벽(위/왼쪽) 탐지 가능
                        self._game_map.start_pos = None
                        self._game_map.mark_passable(current_x, current_y)
                    else:
                        self._game_map.start_pos = (current_x, current_y)
                    # 전체맵핑: entry tile passable 유지, portal_protected로 보호
                    # → A*/BFS가 entry tile 경유 가능 → 인접 벽 탐지 가능
                    if full_mapping_exploring and not _mt_no_starts_init:
                        # entry tile은 passable 유지 → portal_protected로만 보호
                        _portal_protected.add((current_x, current_y))
                        # 도착지(포탈 출구) — 맵핑테스트는 passable 유지 (인접 벽 탐지 필요)
                        _end_x, _end_y = target_x, target_y
                        if _end_x is not None and _end_y is not None and (_end_x, _end_y) != (current_x, current_y):
                            if _is_mapping_test:
                                # 맵핑테스트: passable 유지 + portal_protected (벽 탐지 허용)
                                self._game_map.mark_passable(_end_x, _end_y)
                            else:
                                self._game_map.mark_blocked(_end_x, _end_y)
                            _portal_protected.add((_end_x, _end_y))
                            self.after(0, lambda ex=_end_x, ey=_end_y, mt=_is_mapping_test:
                                self._append_log(f"{'📍' if mt else '🚫'} 도착지 {'보호' if mt else '벽'}: ({ex},{ey})"))
                        # 도착좌표가 여러 개면 처리
                        _end_routes = all_targets[target_idx][7] if len(all_targets[target_idx]) > 7 else []
                        for _ex, _ey in _end_routes:
                            if (_ex, _ey) != (current_x, current_y):
                                if _is_mapping_test:
                                    self._game_map.mark_passable(_ex, _ey)
                                else:
                                    self._game_map.mark_blocked(_ex, _ey)
                                _portal_protected.add((_ex, _ey))
                        # 출발좌표(출입구 포탈): entry tile은 passable 유지, 나머지만 벽 등록
                        _start_routes = all_targets[target_idx][8] if len(all_targets[target_idx]) > 8 else []
                        for _sx, _sy in _start_routes:
                            if (_sx, _sy) != (current_x, current_y):
                                self._game_map.mark_blocked(_sx, _sy)
                            _portal_protected.add((_sx, _sy))
                        # 4방향 모두 벽이면(갇힘) 인접 벽 해제, 아니면 기존 벽 유지
                        blocked_neighbors = sum(
                            1 for d, (ddx, ddy) in DIRECTIONS_4.items()
                            if self._game_map.is_blocked(current_x + ddx, current_y + ddy))
                        unblocked = 0
                        if blocked_neighbors >= 4:
                            for d, (ddx, ddy) in DIRECTIONS_4.items():
                                nx, ny = current_x + ddx, current_y + ddy
                                if (nx, ny) in _portal_protected:
                                    continue  # 포탈 좌표 보호
                                if self._game_map.is_blocked(nx, ny):
                                    self._game_map.mark_passable(nx, ny)
                                    unblocked += 1
                        self.after(0, lambda cx=current_x, cy=current_y, ub=unblocked:
                            self._append_log(f"📍 전체맵핑 시작: ({cx},{cy})" + (f" [갇힘→인접벽 {ub}개 해제]" if ub else "")))
                    # 보스 던전이면 출발지=포탈이므로 벽 등록 (되돌아가기 방지)
                    elif all_targets[target_idx][3]:
                        self._game_map.mark_blocked(current_x, current_y)
                        self.after(0, lambda cx=current_x, cy=current_y:
                            self._append_log(f"🚪 출발지(포탈) 벽 등록: ({cx},{cy})"))
                    else:
                        self._game_map.mark_passable(current_x, current_y)
                        self.after(0, lambda cx=current_x, cy=current_y:
                            self._append_log(f"📍 시작 위치: ({cx},{cy})"))
                    # 근처 전체맵핑 시작 위치 기록
                    if _local_explore_phase and _local_explore_center is None:
                        _local_explore_center = (current_x, current_y)
                    # 사용자 지정 벽좌표 등록 (첫 구간)
                    _route_walls_init = all_targets[target_idx][10] if len(all_targets[target_idx]) > 10 else []
                    for _wx, _wy in _route_walls_init:
                        self._game_map.mark_blocked(_wx, _wy)
                        _portal_protected.add((_wx, _wy))
                    if _route_walls_init:
                        self.after(0, lambda n=len(_route_walls_init): self._append_log(f"🧱 벽좌표 {n}개 등록"))

                # 보스 던전 경유지는 좌표 기반 도착 체크 건너뜀 (좌표가 0,0)
                # 전체맵핑 프런티어 탐색 중이면 보스던전 핸들러 사용
                # 맵핑테스트: 맵 데이터 50타일+ 이면 최단경로 A* 모드 (캐릭터 위치 무관)
                _mt_has_map = _is_mapping_test and len(self._game_map.passable) >= 50
                # 맵핑테스트: 탐색 완료 후(full_mapping/local_explore 둘 다 False)에는
                # 보스 구간이어도 is_boss_dungeon=False → 도착 체크 활성화
                _mt_explore_done = _is_mapping_test and not full_mapping_exploring and not _local_explore_phase
                is_boss_dungeon = (all_targets[target_idx][3] and not _mt_explore_done) or _local_explore_phase or full_mapping_exploring
                # 보스 경유지면 auto-save 차단 (맵핑테스트에서는 항상 허용 — 맵 데이터 유실 방지)
                self._boss_segment_active = all_targets[target_idx][3] and not _is_mapping_test

                # 도착 체크 (정확한 좌표 일치 — 포탈은 정확 좌표에서만 작동)
                # 도착좌표가 여러 개면 어느 하나라도 밟으면 도착 처리
                _cur_route_ends = all_targets[target_idx][7] if len(all_targets[target_idx]) > 7 else []
                if _cur_route_ends:
                    _arrived = any(current_x == ex and current_y == ey for ex, ey in _cur_route_ends)
                else:
                    _arrived = (abs(current_x - target_x) + abs(current_y - target_y) == 0)
                if _arrived and not is_boss_dungeon and portal_grace <= 0:
                    # 맵핑 모드: 목표 도달 시 처리
                    if getattr(self, '_is_mapping', False):
                        mapping_mode = getattr(self, '_mapping_mode', 'path')
                        if mapping_mode == "path":
                            # 경로맵핑: 목표 도달 시 즉시 완료
                            if use_map:
                                self._game_map.end_pos = (current_x, current_y)
                            self.after(0, lambda cx=current_x, cy=current_y:
                                self._append_log(f"🎯 경로맵핑 완료! ({cx},{cy})"))
                            return
                        elif not _mt_explore_done and not _segment_map_locked:
                            # 전체맵핑: 목표 도달해도 계속 탐색 (프런티어 없을 때까지)
                            # — 이미 전체맵핑 완료(_mt_explore_done) 또는 잠금 맵이면 건너뜀
                            self.after(0, lambda cx=current_x, cy=current_y:
                                self._append_log(f"📍 목표 도달 ({cx},{cy}) - 전체맵핑 계속..."))
                            # 목표를 도달했으므로 탐색 모드로 전환 (is_boss_dungeon처럼 동작)
                            is_boss_dungeon = True  # 전체맵핑은 보스 던전처럼 프런티어 탐색
                            full_mapping_exploring = True  # 다음 반복에서도 보스던전 핸들러 진입
                            _is_mapping_mode = _is_mapping_test or full_mapping_exploring
                            # start_pos 해제 (A*가 출발지를 벽으로 취급하는 문제 방지)
                            if use_map:
                                self._game_map.start_pos = None
                                # 도착지 벽 등록 — 탐색 중 포탈 진입 방지
                                _et_x, _et_y = target_x, target_y
                                if _et_x is not None and _et_y is not None and (_et_x, _et_y) != (current_x, current_y):
                                    if _is_mapping_test:
                                        self._game_map.mark_passable(_et_x, _et_y)
                                    else:
                                        self._game_map.mark_blocked(_et_x, _et_y)
                                    _portal_protected.add((_et_x, _et_y))
                                _end_rts = all_targets[target_idx][7] if len(all_targets[target_idx]) > 7 else []
                                for _erx, _ery in _end_rts:
                                    if (_erx, _ery) != (current_x, current_y):
                                        if _is_mapping_test:
                                            self._game_map.mark_passable(_erx, _ery)
                                        else:
                                            self._game_map.mark_blocked(_erx, _ery)
                                        _portal_protected.add((_erx, _ery))
                            prev_x, prev_y = current_x, current_y
                            time.sleep(0.05)
                            continue  # target_idx 증가 건너뛰고 프런티어 탐색 시작
                    # 도착 키 입력
                    _arr_keys = all_targets[target_idx][6] if len(all_targets[target_idx]) > 6 else []
                    if _arr_keys and not self._stop_event.is_set():
                        self._exec_arrival_keys(_arr_keys)
                        _kn = ",".join(kd.get("key","") for kd in _arr_keys)
                        self.after(0, lambda k=_kn, cx=current_x, cy=current_y:
                            self._append_log(f"🔑 도착 키 입력: {k} ({cx},{cy})"))
                    target_idx += 1
                    if target_idx >= len(all_targets):
                        self.after(0, lambda: self._append_log(f"🎯 도착! ({current_x},{current_y})"))
                        self.after(0, self._on_arrival)
                        return
                    else:
                        # 구간 맵 전환 (중지 요청 시 건너뜀)
                        if self._stop_event.is_set():
                            break
                        # 포탈 도착 → 잠시 대기 (텔레포트 전 추가 이동 방지)
                        self._stop_event.wait(0.5)
                        # 현재 맵 도착지 저장 (전환 전)
                        if use_map and not _no_save:
                            self._game_map.end_pos = (current_x, current_y)
                        if not self._switch_segment_map(target_idx, skip_save=_no_save):
                            self.after(0, lambda: self._append_log("⚠️ 맵 구간 전환 실패"))
                        pathfinder = SimplePathfinder(self._game_map)  # 새 맵으로 pathfinder 갱신
                        self._map_pathfinder = pathfinder
                        target_x, target_y = _pick_target(target_idx)
                        seg_name = all_targets[target_idx][2]
                        prev_seg = all_targets[target_idx - 1][2]
                        self.after(0, lambda tx=target_x, ty=target_y, sn=seg_name: self._append_log(f"▶ 다음: ({tx},{ty}) [{sn}]"))
                        # 테스트 실행 모드: 경유지 도착 알림 (비블로킹) — 부분실행/재생에서는 표시 안 함
                        if not getattr(self, '_is_mapping', False) and not getattr(self, '_auto_run', False):
                            self.after(0, lambda ps=prev_seg, sn=seg_name, ti=target_idx, total=len(all_targets):
                                self._show_notification(
                                    "경유지 도착",
                                    f"'{ps}' 도착! ({ti}/{total})\n\n"
                                    f"다음 목표: '{sn}'"))
                        stuck_count = 0
                        total_stuck_count = 0
                        current_path = []
                        path_index = 0
                        path_pos_index = {}
                        explored_from = {}
                        unknown_path_fails = 0
                        last_dir = None
                        recent_positions.clear()
                        _portal_protected.clear()
                        # 보스 던전 상태 리셋
                        boss_mode = "exploring"
                        boss_patrol = None
                        explore_target = None
                        explore_target_tries = 0
                        explore_unreachable = set()
                        frontier_cooldown.clear()
                        boss_no_frontier_count = 0
                        boss_approach_steps = 0
                        boss_appr_miss = 0
                        _boss_chasing = False
                        _boss_chase_miss = 0
                        patrol_skip_count = 0
                        explored_from = {}  # 구간 전환 → 이전 구간 방향 기록 클리어
                        _mt_seg_has_starts = bool(all_targets[target_idx][8] if len(all_targets[target_idx]) > 8 else [])
                        _segment_map_locked = all_targets[target_idx][9] if len(all_targets[target_idx]) > 9 else False
                        full_mapping_exploring = (_is_mapping_test and _mt_seg_has_starts and not _segment_map_locked)  # 구간 전환 → 출발좌표 있으면 전체맵핑
                        _is_mapping_mode = _is_mapping_test or full_mapping_exploring
                        _local_explore_phase = _is_mapping_test and not _mt_seg_has_starts and not _segment_map_locked  # 출발좌표 없으면 근처맵핑 (잠금맵 제외)
                        _local_explore_center = None
                        # _local_explore_phase는 full_mapping_exploring과 독립 (근처맵핑 ≠ 전체맵핑)
                        # 보스 던전 전환: 텔레포트 전까지 맵 기록 중지
                        # (이전 맵 좌표가 보스 던전 맵에 오염되는 것 방지)
                        if all_targets[target_idx][3]:
                            boss_pre_teleport = True
                            mapping_on = False
                            mark_portal_entry = False
                            self.after(0, lambda: self._append_log("⏳ 보스 던전 포탈 대기..."))
                        else:
                            mapping_on = not _no_save
                            mark_portal_entry = True
                        if _segment_map_locked:
                            mapping_on = False
                        portal_grace = 30  # 구간 전환 후 점프 필터 비활성 (던전 로딩 대기)
                        prev_x, prev_y = current_x, current_y
                        time.sleep(0.05)
                        continue

                # 포탈 감지: 경유지 근접(1칸) 상태에서 좌표 점프 시 다음 경유지로 전환
                # (정확 좌표 밟자마자 텔레포트 → 코드가 목표 좌표를 못 읽을 수 있음)
                if prev_x is not None:
                    jump_dist = abs(current_x - prev_x) + abs(current_y - prev_y)
                    # 도착좌표가 여러 개면 어느 하나라도 근접하면 포탈 감지
                    _portal_re = all_targets[target_idx][7] if len(all_targets[target_idx]) > 7 else []
                    if _portal_re:
                        was_near_target = any(abs(prev_x - ex) + abs(prev_y - ey) <= 1 for ex, ey in _portal_re)
                    else:
                        was_near_target = abs(prev_x - target_x) + abs(prev_y - target_y) <= 1
                    portal_threshold = max(8, int(interval / 0.02) + 5) if smooth_move else 5
                    # 포탈 인접(was_near_target) 시 threshold를 3으로 낮춤
                    # (복사-2 입구(6,2)→1굴(9,3) 점프거리=4 < 8 → 미감지 버그 수정)
                    if was_near_target:
                        portal_threshold = 3
                    # 마지막 경유지에서 포탈 감지 → 도착 처리 (싱글 테스트 포함)
                    # 전체맵핑/보스던전 탐색 중에는 포탈 감지 비활성화 (오탐 방지)
                    if jump_dist >= portal_threshold and was_near_target and not is_boss_dungeon and target_idx == len(all_targets) - 1:
                        # 맵 도착지 저장 (포탈 직전 좌표)
                        if use_map and not _no_save:
                            self._game_map.end_pos = (prev_x, prev_y)
                        # 도착 키 입력
                        _arr_keys = all_targets[target_idx][6] if len(all_targets[target_idx]) > 6 else []
                        if _arr_keys and not self._stop_event.is_set():
                            self._exec_arrival_keys(_arr_keys)
                            _kn = ",".join(kd.get("key","") for kd in _arr_keys)
                            self.after(0, lambda k=_kn: self._append_log(f"🔑 도착 키 입력: {k}"))
                        self.after(0, lambda jd=jump_dist, px=prev_x, py=prev_y:
                            self._append_log(f"🌀 포탈 감지 → 도착! (점프 {jd}칸, ({px},{py}))"))
                        self.after(0, self._on_arrival)
                        return
                    elif jump_dist >= portal_threshold and was_near_target and not is_boss_dungeon and target_idx < len(all_targets) - 1:
                        # 도착 키 입력
                        _arr_keys = all_targets[target_idx][6] if len(all_targets[target_idx]) > 6 else []
                        if _arr_keys and not self._stop_event.is_set():
                            self._exec_arrival_keys(_arr_keys)
                            _kn = ",".join(kd.get("key","") for kd in _arr_keys)
                            self.after(0, lambda k=_kn: self._append_log(f"🔑 도착 키 입력: {k}"))
                        # 구간 맵 전환 (현재 맵 저장 → 다음 구간 맵 로드)
                        if self._stop_event.is_set():
                            break
                        # 현재 맵 도착지 저장 (전환 전) - 포탈 직전 좌표
                        _cur_is_boss_p = all_targets[target_idx][3] if len(all_targets[target_idx]) > 3 else False
                        if use_map and (not _cur_is_boss_p or _is_mapping_test) and not _no_save:
                            self._game_map.end_pos = (prev_x, prev_y)
                        next_segment = target_idx + 1
                        self._boss_segment_active = False
                        # 맵핑테스트: (0,0) 경유지도 맵 데이터 저장 (유실 방지)
                        if not self._switch_segment_map(next_segment, skip_save=(_cur_is_boss_p and not _is_mapping_test) or _no_save):
                            self.after(0, lambda: self._append_log("⚠️ 맵 구간 전환 실패"))
                        pathfinder = SimplePathfinder(self._game_map)  # 새 맵으로 pathfinder 갱신
                        self._map_pathfinder = pathfinder
                        target_idx += 1
                        target_x, target_y = _pick_target(target_idx)
                        seg_name = all_targets[target_idx][2]
                        prev_seg = all_targets[target_idx - 1][2]
                        self.after(0, lambda jd=jump_dist, tx=target_x, ty=target_y, sn=seg_name:
                            self._append_log(f"🌀 포탈 감지! (점프 {jd}칸) → {sn} 목표: ({tx},{ty})"))
                        # 테스트 실행 모드: 포탈 도착 알림 (비블로킹) — 부분실행/재생에서는 표시 안 함
                        if not getattr(self, '_is_mapping', False) and not getattr(self, '_auto_run', False):
                            self.after(0, lambda ps=prev_seg, sn=seg_name, ti=target_idx, total=len(all_targets):
                                self._show_notification(
                                    "포탈 감지",
                                    f"'{ps}' -> '{sn}' 이동! ({ti}/{total})\n\n"
                                    f"다음 목표: '{sn}'"))
                        stuck_count = 0
                        total_stuck_count = 0
                        current_path = []
                        path_index = 0
                        path_pos_index = {}
                        explored_from = {}
                        unknown_path_fails = 0
                        last_dir = None
                        recent_positions.clear()
                        # 보스 던전 상태 리셋
                        boss_mode = "exploring"
                        boss_patrol = None
                        explore_target = None
                        explore_target_tries = 0
                        explore_unreachable = set()
                        _portal_protected.clear()
                        frontier_cooldown.clear()
                        boss_no_frontier_count = 0
                        boss_approach_steps = 0
                        boss_appr_miss = 0
                        _boss_chasing = False
                        _boss_chase_miss = 0
                        patrol_skip_count = 0
                        explored_from = {}  # 구간 전환 → 이전 구간 방향 기록 클리어
                        _mt_seg_has_starts = bool(all_targets[target_idx][8] if len(all_targets[target_idx]) > 8 else [])
                        _segment_map_locked = all_targets[target_idx][9] if len(all_targets[target_idx]) > 9 else False
                        full_mapping_exploring = (_is_mapping_test and _mt_seg_has_starts and not _segment_map_locked)  # 구간 전환 → 출발좌표 있으면 전체맵핑
                        _is_mapping_mode = _is_mapping_test or full_mapping_exploring
                        _local_explore_phase = _is_mapping_test and not _mt_seg_has_starts and not _segment_map_locked  # 출발좌표 없으면 근처맵핑 (잠금맵 제외)
                        _local_explore_center = None
                        # _local_explore_phase는 full_mapping_exploring과 독립 (근처맵핑 ≠ 전체맵핑)
                        # 보스 던전 전환: 포탈 점프로 이미 텔레포트된 상태
                        _next_is_boss = all_targets[target_idx][3] if len(all_targets[target_idx]) > 3 else False
                        if _next_is_boss:
                            # 포탈 점프 감지 = 이미 보스 던전에 도착 → 맵 기록 중지 (오염 방지)
                            # 단, 맵핑테스트에서는 기록 유지 (탐색이 목적)
                            boss_pre_teleport = False
                            mapping_on = _is_mapping_mode
                            mark_portal_entry = False
                        else:
                            boss_pre_teleport = False
                            mapping_on = not _no_save
                            mark_portal_entry = True
                        if _segment_map_locked:
                            mapping_on = False
                        # 던전 로딩 대기 후 좌표 재측정
                        self._stop_event.wait(2.0)
                        start_x, start_y = matcher.read_both_coordinates(
                            self._config.coord_x_region,
                            self._config.coord_y_region,
                            stop_event=self._stop_event
                        )
                        if start_x is not None:
                            current_x, current_y = int(start_x), int(start_y)
                        # 도착 좌표 = 입구 포탈 → 출발지(보라색) + 벽 등록 (되돌아가기 방지)
                        if start_x is not None and mapping_on and use_map:
                            self._game_map.start_pos = (current_x, current_y)
                            self._game_map.mark_blocked(current_x, current_y)
                            _pending_start_pos = (current_x, current_y)
                            self.after(0, lambda cx=current_x, cy=current_y:
                                self._append_log(f"🚪 출발지 등록 + 벽: ({cx},{cy})"))
                        # 보스 던전: 출구/입구 포탈 벽 등록 + 보호 (A* 경유 방지)
                        if _next_is_boss:
                            _jp_starts = all_targets[target_idx][8] if len(all_targets[target_idx]) > 8 else []
                            for _sx, _sy in _jp_starts:
                                self._game_map.mark_blocked(_sx, _sy)
                                _portal_protected.add((_sx, _sy))
                            _jp_ends = all_targets[target_idx][7] if len(all_targets[target_idx]) > 7 else []
                            for _ex, _ey in _jp_ends:
                                self._game_map.mark_blocked(_ex, _ey)
                                _portal_protected.add((_ex, _ey))
                        portal_grace = 30  # 구간 전환 후 점프 필터 비활성 (던전 로딩 대기)
                        prev_x, prev_y = current_x, current_y
                        continue

                # 이동 성공/실패 판정
                if prev_x is not None:
                    moved = (prev_x != current_x or prev_y != current_y)

                    if moved:
                        # 이동 성공 → 현재 위치 이동가능으로 기록
                        if not self._stop_event.is_set() and _ui_update_ok:
                            self.after(0, lambda px=prev_x, py=prev_y, cx=current_x, cy=current_y, d=last_dir:
                                self._append_log(f"✅ ({px},{py})→({cx},{cy}) {d}"))
                        if mapping_on and use_map:
                            self._game_map.mark_passable(current_x, current_y)
                        if use_map:
                            self._game_map.clear_soft_blocked(current_x, current_y)
                            # 왔던 방향의 반대를 explored_from에 기록 (뒤로가기는 마지막에 시도)
                            if last_dir:
                                new_pos = (current_x, current_y)
                                opposite = {"up": "down", "down": "up", "left": "right", "right": "left"}
                                tried = explored_from.get(new_pos, set())
                                tried.add(opposite[last_dir])
                                explored_from[new_pos] = tried
                                # explored_from 메모리 제한 (500개 초과 시 오래된 250개 삭제, 현재 위치 보호)
                                if len(explored_from) > 500:
                                    keys_to_remove = [k for k in list(explored_from.keys())[:250] if k != new_pos]
                                    for k in keys_to_remove:
                                        del explored_from[k]
                            # 30칸마다 자동 저장 (디바운스, 중지 시 생략)
                            passable_count = len(self._game_map.passable)
                            if not _no_save and passable_count % 30 == 0 and not self._map_save_lock.locked() and not self._stop_event.is_set():
                                if _ui_update_ok:
                                    self.after(0, lambda pc=passable_count: self._append_log(f"💾 자동저장 ({pc}타일)"))
                                threading.Thread(target=self._auto_save_map, daemon=True).start()
                        stuck_count = 0
                        total_stuck_count = 0
                        unknown_path_fails = 0  # 이동 성공 → A* unknown 신뢰 복구
                    else:
                        # 이동 실패
                        stuck_count += 1
                        # portal_grace 중에는 total_stuck 누적 안 함
                        # (게임 로딩 중 키 입력 불가 → 거짓 정체 방지)
                        if portal_grace <= 0:
                            total_stuck_count += 1
                        unknown_path_fails += 1  # A* unknown 경로 불신 누적
                        if not self._stop_event.is_set() and _ui_update_ok:
                            self.after(0, lambda px=prev_x, py=prev_y, d=last_dir, sc=stuck_count:
                                self._append_log(f"❌ ({px},{py}) {d} 실패 (정체:{sc})"))
                        # 실패한 방향 기록: 벽 확정 전에는 기록하지 않음 (재시도로 stuck_count 3 도달 보장)
                        # 벽 확정(stuck_count>=3) 후 아래에서 기록
                        _is_mapping_mode = _is_mapping_test or full_mapping_exploring
                        if not _is_mapping_mode and last_dir and prev_x is not None:
                            fail_pos = (prev_x, prev_y)
                            tried = explored_from.get(fail_pos, set())
                            tried.add(last_dir)
                            explored_from[fail_pos] = tried
                        _wall_threshold = 3 if _is_mapping_mode else 2
                        if stuck_count >= _wall_threshold and last_dir and portal_grace <= 0:
                            if use_map:
                                ddx, ddy = DIRECTIONS_4.get(last_dir, (0, 0))
                                wall_x = prev_x + ddx
                                wall_y = prev_y + ddy

                                if _segment_map_locked:
                                    # 잠금맵: 맵 파일 수정 없이 임시벽으로 회피 (메모리만, 디스크 저장 안 함)
                                    if not self._game_map.is_blocked(wall_x, wall_y):
                                        self._game_map.mark_soft_blocked(wall_x, wall_y, allow_promote=False)
                                elif mapping_on:
                                    # 맵 기록 모드: 벽 등록
                                    if boss_mode == "patrolling":
                                        if not self._game_map.is_blocked(wall_x, wall_y):
                                            self._game_map.mark_soft_blocked(wall_x, wall_y)
                                            if _ui_update_ok:
                                                self.after(0, lambda wx=wall_x, wy=wall_y:
                                                    self._append_log(f"🚧 임시벽: x{wx}y{wy} (몬스터?)"))
                                    else:
                                        if not self._game_map.is_blocked(wall_x, wall_y):
                                            if _is_mapping_mode:
                                                # 맵핑테스트/전체맵핑: 임시벽 없이 전부 영구벽
                                                self._game_map.mark_blocked(wall_x, wall_y)
                                                if _ui_update_ok:
                                                    self.after(0, lambda wx=wall_x, wy=wall_y:
                                                        self._append_log(f"🧱 벽: x{wx}y{wy}"))
                                            elif self._game_map.is_soft_blocked(wall_x, wall_y):
                                                # 일반 탐색: 재실패 → count +3 (몬스터 가능, tick으로 복구)
                                                for _ in range(3):
                                                    self._game_map.mark_soft_blocked(wall_x, wall_y)
                                                if _ui_update_ok:
                                                    self.after(0, lambda wx=wall_x, wy=wall_y:
                                                        self._append_log(f"🚧 임시벽 강화: x{wx}y{wy} (재실패)"))
                                            elif self._game_map.is_passable(wall_x, wall_y):
                                                self._game_map.mark_soft_blocked(wall_x, wall_y)
                                                if _ui_update_ok:
                                                    self.after(0, lambda wx=wall_x, wy=wall_y:
                                                        self._append_log(f"🚧 임시벽: x{wx}y{wy} (기존 경로)"))
                                            else:
                                                self._game_map.mark_blocked(wall_x, wall_y)
                                                if _ui_update_ok:
                                                    self.after(0, lambda wx=wall_x, wy=wall_y:
                                                        self._append_log(f"🧱 벽 확정: x{wx}y{wy}"))
                                    # 벽 발견시 저장 (디바운스, 중지 시 생략)
                                    if not _no_save and not self._map_save_lock.locked() and not self._stop_event.is_set():
                                        threading.Thread(target=self._auto_save_map, daemon=True).start()
                                else:
                                    # 부분실행/전체테스트: 임시벽만 (디스크 저장 안 함, 영구벽 승격 안 함)
                                    if not self._game_map.is_blocked(wall_x, wall_y):
                                        self._game_map.mark_soft_blocked(wall_x, wall_y, allow_promote=False)

                                # 벽이 현재 프런티어 목표이면 즉시 무효화
                                if explore_target == (wall_x, wall_y):
                                    frontier_cooldown.add(explore_target)
                                    explore_target = None
                                    explore_target_tries = 0

                                # 경로 재계산 필요
                                current_path = []
                                path_index = 0
                                path_pos_index = {}
                                pathfinder.invalidate_path()
                                # 미지 영역 경로 신뢰도 감소 (벽 발견 = 미지 영역 경로 실패)
                                # 3회 누적 시 2차(미지 영역) 탐색 비활성 → 로컬 탐색으로 전환
                                unknown_path_fails = min(unknown_path_fails + 1, 3)
                                if _ui_update_ok:
                                    self.after(0, lambda: self._append_log("🔄 벽 발견 → 경로 재계산"))
                                # 맵핑테스트: 벽 확정 후 explored_from 기록 (재시도 보장 후)
                                if _is_mapping_mode and last_dir and prev_x is not None:
                                    fail_pos = (prev_x, prev_y)
                                    tried = explored_from.get(fail_pos, set())
                                    tried.add(last_dir)
                                    explored_from[fail_pos] = tried
                            stuck_count = 0
                            # ※ total_stuck_count는 리셋하지 않음 — 벽 등록 후에도
                            # 계속 정체되면 장기정체 복구(>=8)가 발동해야 함
                            prev_x, prev_y = current_x, current_y  # 현재 위치로 갱신 (출발지 재등록 방지)
                            last_dir = None
                            # continue 제거: OCR 재읽기 건너뛰고 바로 경로 재계산 → 즉시 이동

                        # 탈출 스킬 체크 (장기정체 복구보다 먼저 — total_stuck 리셋 전)
                        # 좌표가 안 움직이면 발동 (total_stuck_count = 연속 이동실패 횟수)
                        if (escape_skill_enabled and
                            total_stuck_count >= escape_skill_stuck_threshold and
                            time.time() - last_escape_time >= escape_skill_cooldown):

                            self.after(0, lambda tc=total_stuck_count:
                                self._append_log(f"🚀 탈출 스킬 발동! (연속 정체 {tc}회)"))

                            try:
                                pyautogui.keyDown(escape_skill_key)
                                try:
                                    time.sleep(0.05)
                                finally:
                                    pyautogui.keyUp(escape_skill_key)
                            except Exception as e:
                                logger.error(f"[좌표모드] 탈출 스킬 키 입력 실패: {e}")
                                last_escape_time = time.time()  # 실패해도 쿨다운 적용 (tight loop 방지)
                                prev_x, prev_y = None, None  # 스킬 실패해도 위치 리셋 (stale position 방지)
                                time.sleep(0.05)
                                continue
                            self._stop_event.wait(0.3)

                            dx = target_x - current_x
                            dy = target_y - current_y
                            if dx == 0 and dy == 0:
                                dir_name = last_dir or "right"
                            elif abs(dx) >= abs(dy):
                                dir_name = "right" if dx > 0 else "left"
                            else:
                                dir_name = "down" if dy > 0 else "up"
                            direction_key = self._config.move_keys.get(dir_name, dir_name)

                            for _ in range(escape_skill_direction_count):
                                pyautogui.press(direction_key)
                                time.sleep(0.05)

                            self._stop_event.wait(escape_skill_wait_after)

                            last_escape_time = time.time()
                            prev_x, prev_y = None, None
                            stuck_count = 0
                            total_stuck_count = 0
                            current_path = []
                            path_index = 0
                            path_pos_index = {}
                            explored_from = {}
                            unknown_path_fails = 0
                            time.sleep(0.05)
                            continue

                        # 장기 정체 복구: 인접 벽 해제 (잘못된 벽 등록 자동 수정)
                        # 몬스터 등 임시 장애물이 영구벽으로 잘못 등록된 경우 복구
                        # ※ 직전에 실패한 방향의 벽은 해제하지 않음 (진짜 벽 보호)
                        if total_stuck_count >= 8 and use_map:
                            unblocked = 0
                            fail_wall = None
                            if last_dir:
                                fdx, fdy = DIRECTIONS_4.get(last_dir, (0, 0))
                                fail_wall = (current_x + fdx, current_y + fdy)
                            _tried_here = explored_from.get((current_x, current_y), set())
                            for d, (ddx, ddy) in DIRECTIONS_4.items():
                                nx, ny = current_x + ddx, current_y + ddy
                                if (nx, ny) == fail_wall:
                                    continue  # 직전 실패 방향 벽은 유지
                                if d in _tried_here:
                                    continue  # 이미 실패한 방향 벽도 유지
                                if (nx, ny) in _portal_protected:
                                    continue  # 포탈 좌표 보호
                                if self._game_map.is_blocked(nx, ny):
                                    self._game_map.mark_passable(nx, ny)
                                    unblocked += 1
                            if unblocked > 0:
                                self.after(0, lambda u=unblocked, cx=current_x, cy=current_y:
                                    self._append_log(f"🔓 장기정체({cx},{cy}) → {u}개 인접벽 해제 (실패방향 제외)"))
                            total_stuck_count = 0
                            current_path = []
                            path_index = 0
                            path_pos_index = {}
                            pathfinder.invalidate_path()
                            # explored_from 유지 (확인된 벽 방향 기억 — 해제 후 재실패 루프 방지)

                # soft_blocked 자동 감소 (10회마다) — 맵핑테스트/전체맵핑에서는 비활성 (벽 유지 필수), 잠금맵은 활성
                if use_map and (not _is_mapping_mode or _segment_map_locked):
                    tick_counter += 1
                    if tick_counter >= 10:
                        self._game_map.tick()
                        tick_counter = 0

                # 상시 스킬 체크
                _t_skill = time.time()
                if auto_skill_enabled and auto_skill_key and not self._stop_event.is_set():
                    _use_skill = False
                    if _auto_skill_cd_tmpl is not None:
                        # 이미지 기반: OCR 스크린샷 재사용 (추가 스크린샷 불필요)
                        try:
                            import cv2 as _ascv
                            import numpy as _asnp
                            _as_pil = getattr(matcher, '_last_screenshot', None)
                            if _as_pil is not None:
                                if auto_skill_cd_region and len(auto_skill_cd_region) == 4:
                                    _rx1, _ry1, _rx2, _ry2 = auto_skill_cd_region
                                    _as_cropped = _as_pil.crop((_rx1, _ry1, _rx2, _ry2))
                                    _as_scr = _ascv.cvtColor(_asnp.array(_as_cropped), _ascv.COLOR_RGB2BGR)
                                else:
                                    _as_scr = _ascv.cvtColor(_asnp.array(_as_pil), _ascv.COLOR_RGB2BGR)
                                _as_res = _ascv.matchTemplate(_as_scr, _auto_skill_cd_tmpl, _ascv.TM_CCOEFF_NORMED)
                                _, _as_max_val, _, _ = _ascv.minMaxLoc(_as_res)
                                if _as_max_val < 0.8:
                                    _use_skill = True
                        except Exception:
                            _use_skill = True
                    else:
                        # 이미지 미설정: 매 반복 사용
                        _use_skill = True
                    if _use_skill:
                        try:
                            _orig_pause = pyautogui.PAUSE
                            pyautogui.PAUSE = 0
                            try:
                                pyautogui.keyDown(auto_skill_key)
                                time.sleep(0.015)
                                pyautogui.keyUp(auto_skill_key)
                            finally:
                                pyautogui.PAUSE = _orig_pause
                            self._key_press_count += 1
                        except Exception:
                            pass
                _t_skill_ms = int((time.time() - _t_skill) * 1000)
                if _t_skill_ms > 10:
                    logger.info(f"[타이밍] 상시스킬: {_t_skill_ms}ms")
                    if _ui_update_ok:
                        self.after(0, lambda ms=_t_skill_ms: self._append_log(f"⏱ 상시스킬: {ms}ms"))

                # ── 보스 던전 핸들러 ──
                if is_boss_dungeon:
                    current_pos_tuple = (current_x, current_y)

                    # 모드 상태 주기적 로그 (30회마다)
                    if iteration % 30 == 1:
                        pc = len(self._game_map.passable) if use_map else 0
                        self.after(0, lambda m=boss_mode, p=pc, cx=current_x, cy=current_y:
                            self._append_log(f"📊 보스던전 [{m}] ({cx},{cy}) 맵:{p}칸"))

                    # 텔레포트 대기: 구간 전환 후 아직 이전 맵에 있음 → 맵 기록 중지 상태
                    if boss_pre_teleport:
                        if prev_x is not None:
                            jump_dist = abs(current_x - prev_x) + abs(current_y - prev_y)
                            teleport_threshold = max(8, int(interval / 0.02) + 5) if smooth_move else 5
                            if jump_dist >= teleport_threshold:
                                # 텔레포트 완료! 맵 기록 재개
                                boss_pre_teleport = False
                                mapping_on = not _no_save
                                _segment_map_locked = all_targets[target_idx][9] if len(all_targets[target_idx]) > 9 else False
                                if _segment_map_locked:
                                    mapping_on = False
                                else:
                                    self._game_map.mark_blocked(current_x, current_y)
                                # 출구 포탈(route_starts) 벽 등록 + 보호 (A* 경유 방지)
                                _boss_starts = all_targets[target_idx][8] if len(all_targets[target_idx]) > 8 else []
                                for _sx, _sy in _boss_starts:
                                    self._game_map.mark_blocked(_sx, _sy)
                                    _portal_protected.add((_sx, _sy))
                                # 도착 포탈(route_ends) 벽 등록 + 보호
                                _boss_ends = all_targets[target_idx][7] if len(all_targets[target_idx]) > 7 else []
                                for _ex, _ey in _boss_ends:
                                    self._game_map.mark_blocked(_ex, _ey)
                                    _portal_protected.add((_ex, _ey))
                                self.after(0, lambda cx=current_x, cy=current_y, ns=len(_boss_starts), ne=len(_boss_ends):
                                    self._append_log(f"🚪 보스 던전 입장! 입구 벽 등록: ({cx},{cy})" +
                                                     (f" [포탈보호: 출구{ns}+입구{ne}]" if ns or ne else "")))
                                prev_x, prev_y = current_x, current_y
                                time.sleep(0.05)
                                continue
                        # 아직 텔레포트 안됨 → 이동 시도만 (맵 기록 없음)
                        prev_x, prev_y = current_x, current_y
                        self._stop_event.wait(0.3)  # 이동 대기 (0.3초)
                        continue

                    # 포탈 탈출 감지: 탐색 중 좌표 점프 → 포탈 타일 벽 등록 + 복귀
                    # 전체맵핑 모드: 포탈 탈출 감지 비활성화 (OCR 오독으로 인한 오판 방지)
                    if prev_x is not None and not full_mapping_exploring:
                        jump_dist = abs(current_x - prev_x) + abs(current_y - prev_y)
                        # smooth_move: interval초 동안 키 반복 → 최대 이동 거리 = interval / 0.02
                        if smooth_move:
                            max_normal_move = max(8, int(interval / 0.02) + 5)
                        else:
                            max_normal_move = 5
                        if jump_dist >= max_normal_move:
                            # 직전 위치가 포탈이었음 → 벽으로 등록
                            if mapping_on and use_map:
                                self._game_map.mark_blocked(prev_x, prev_y)
                                self.after(0, lambda px=prev_x, py=prev_y:
                                    self._append_log(f"🚪 포탈 탈출 감지! ({px},{py}) 벽 등록"))
                                if not _no_save:
                                    threading.Thread(target=self._auto_save_map, daemon=True).start()
                            # 맵핑 모드: 포탈 나감 → 중단
                            if getattr(self, '_is_mapping', False):
                                self.after(0, lambda: self._append_log("⚠️ 포탈로 나감 → 맵핑 중단 (재시작하면 포탈 회피)"))
                                return
                            # 테스트 모드: 다음 경유지로 전환
                            prev_x, prev_y = current_x, current_y
                            time.sleep(0.05)
                            continue

                    # 보스 던전: 현재 위치를 이동가능으로 보장
                    # (단, 포탈 입구로 등록된 벽은 유지 — 되돌아가기 방지)
                    if mapping_on and use_map and not self._game_map.is_passable(current_x, current_y) \
                            and not self._game_map.is_blocked(current_x, current_y):
                        self._game_map.mark_passable(current_x, current_y)

                    # 맵 이미 탐색 완료 → 즉시 순찰/완료 (테스트 모드에서 재탐색 방지)
                    if boss_mode == "exploring" and boss_patrol is None and len(self._game_map.passable) >= 50:
                        _fnf_ec = _local_explore_center if _local_explore_phase else None
                        _fnf_er = _local_explore_radius if _local_explore_phase else 0
                        if self._game_map.is_fully_explored() or _find_nearest_frontier(current_x, current_y, explore_center=_fnf_ec, explore_radius=_fnf_er) is None:
                            boss_no_frontier_count = 10  # 아래 완료 체크로 즉시 진입

                    # 순찰 좌표 등록됨 + 맵 충분히 탐색됨 → 순찰 모드
                    # 맵이 거의 비어있으면(passable < 50) 탐색 모드 유지 (먼저 맵 채우기)
                    # 맵핑테스트 전체맵핑 중에는 순찰 전환 스킵
                    if boss_mode == "exploring" and boss_patrol is None:
                        if self._game_map.patrol_points and len(self._game_map.passable) >= 50:
                            from ..player.map_patroller import MapPatroller
                            boss_patrol = MapPatroller(self._game_map)
                            boss_patrol.start(current_pos_tuple)
                            boss_mode = "patrolling"
                            last_dir = None
                            stuck_count = 0
                            total_stuck_count = 0
                            boss_no_frontier_count = 0
                            # 이전 탐색 경로/상태 초기화 (잘못된 방향 방지)
                            current_path = []
                            path_index = 0
                            path_pos_index = {}
                            explored_from = {}
                            unknown_path_fails = 0
                            explore_target = None
                            explore_target_tries = 0
                            patrol_skip_count = 0
                            # 순찰 시작 시 현재 위치 인접 벽 해제 (잘못된 벽 때문에 출발 못하는 문제 방지)
                            # 단, 포탈 입구(start_pos)는 유지
                            portal_pos = getattr(self._game_map, 'start_pos', None)
                            unblocked_start = 0
                            for d, (ddx, ddy) in DIRECTIONS_4.items():
                                nx, ny = current_x + ddx, current_y + ddy
                                if (nx, ny) in _portal_protected:
                                    continue  # 포탈 좌표 보호
                                if (nx, ny) != portal_pos and self._game_map.is_blocked(nx, ny):
                                    self._game_map.mark_passable(nx, ny)
                                    unblocked_start += 1
                            pc = len(self._game_map.patrol_points)
                            first_pt = self._game_map.patrol_points[0]
                            self.after(0, lambda: self._append_log(f"{'='*30}"))
                            self.after(0, lambda pc=pc, fp=first_pt, ub=unblocked_start: self._append_log(
                                f"🔍 순찰모드 시작! {pc}개 순찰좌표 순회 (1번: {fp})" + (f" [인접벽 {ub}개 해제]" if ub else "")))
                            self.after(0, lambda: self._append_log(f"{'='*30}"))

                    if boss_mode == "exploring":
                        # Phase 2 경로맵핑: 프런티어 대신 실제 목표로 직행
                        # 맵 데이터 있으면(passable>=50) 부분실행/전체테스트도 직행
                        _has_mapped = len(self._game_map.passable) >= 50
                        _phase2_direct = (not _local_explore_phase and not full_mapping_exploring
                                           and (_is_mapping_test or _has_mapped))
                        if _phase2_direct:
                            direction = find_path_direction(current_x, current_y, target_x, target_y)
                        else:
                            # 인접 미탐색 타일이 있으면 직접 진입
                            # 고정 순서 대신 매 반복마다 시작 방향을 회전 (같은 방향만 반복 방지)
                            direction = None
                            dir_list = list(DIRECTIONS_4.items())
                            rotate_offset = iteration % len(dir_list)
                            rotated_dirs = dir_list[rotate_offset:] + dir_list[:rotate_offset]
                            unknown_candidates = []
                            _explored_here = explored_from.get((current_x, current_y), set())
                            for d, (ddx, ddy) in rotated_dirs:
                                if d in _explored_here:
                                    continue  # 이미 시도+실패한 방향 스킵 (진동 방지)
                                nx, ny = current_x + ddx, current_y + ddy
                                if not self._game_map.is_known(nx, ny):
                                    # 근처맵핑 모드: 반경 밖이면 미탐색이어도 무시
                                    if _local_explore_phase and _local_explore_center is not None:
                                        _le_dist = max(abs(nx - _local_explore_center[0]), abs(ny - _local_explore_center[1]))
                                        if _le_dist > _local_explore_radius:
                                            continue
                                    unknown_candidates.append(d)
                            if unknown_candidates:
                                # 직전 방향이 아닌 것 우선, 없으면 아무거나
                                direction = None
                                for d in unknown_candidates:
                                    if d != last_dir:
                                        direction = d
                                        break
                                if direction is None:
                                    direction = unknown_candidates[0]
                                explore_target = None
                                boss_no_frontier_count = 0
                                if iteration % 5 == 0:
                                    self.after(0, lambda cx=current_x, cy=current_y, d=direction, uc=unknown_candidates:
                                        self._append_log(f"🗺️ [탐색] ({cx},{cy}) → {d} (미탐색:{uc})"))

                        # 없으면 가장 가까운 frontier까지 기존 A* 알고리즘으로 이동
                        if direction is None and not _phase2_direct:
                            # 프런티어 쿨다운 주기 리셋 (20회마다)
                            if iteration - frontier_cooldown_iter > 20:
                                frontier_cooldown.clear()
                                frontier_cooldown_iter = iteration

                            # 기존 목표 유효성 검증
                            if explore_target is not None:
                                et = explore_target
                                still_ok = et != (current_x, current_y)
                                # 목표가 벽이면 즉시 무효화
                                if still_ok:
                                    still_ok = not self._game_map.is_blocked(et[0], et[1])
                                if still_ok:
                                    still_ok = any(
                                        not self._game_map.is_known(et[0]+ddx, et[1]+ddy)
                                        for ddx, ddy in [(0,-1),(0,1),(-1,0),(1,0)]
                                    )
                                if not still_ok:
                                    frontier_cooldown.add(explore_target)
                                    explore_target = None
                                    explore_target_tries = 0

                            if explore_target is None:
                                _fr = _local_explore_radius if _local_explore_phase else 150
                                _ec = _local_explore_center if _local_explore_phase else None
                                _er = _local_explore_radius if _local_explore_phase else 0
                                explore_target = _find_nearest_frontier(current_x, current_y, max_radius=_fr, explore_center=_ec, explore_radius=_er)
                                # 근처맵핑 모드: 반경 내 프런티어가 반경 밖이면 무시
                                if explore_target and _local_explore_phase and _local_explore_center is not None:
                                    _et_dist = max(abs(explore_target[0] - _local_explore_center[0]), abs(explore_target[1] - _local_explore_center[1]))
                                    if _et_dist > _local_explore_radius:
                                        explore_target = None
                                explore_target_tries = 0
                                if explore_target:
                                    self.after(0, lambda et=explore_target, cx=current_x, cy=current_y:
                                        self._append_log(f"🗺️ [탐색] 프런티어 목표: {et} (현재:{cx},{cy})"))

                            if explore_target:
                                boss_no_frontier_count = 0
                                direction = find_path_direction(
                                    current_x, current_y,
                                    explore_target[0], explore_target[1])
                                if direction is None:
                                    explore_target_tries += 3  # 방향 없음 = 빠르게 포기
                                elif _is_backtrack_dir:
                                    explore_target_tries += 1  # 백트래킹 = A* 실패
                                else:
                                    explore_target_tries = 0   # 정상 경로 = 리셋
                                if explore_target_tries >= 15:
                                    explore_unreachable.add(explore_target)
                                    self.after(0, lambda et=explore_target, t=explore_target_tries:
                                        self._append_log(f"🚫 프런티어 {et} 도달 불가 ({t}회) → 스킵"))
                                    explore_target = None
                                    explore_target_tries = 0
                            else:
                                # 프런티어 없음 — 연속 미발견 카운트
                                boss_no_frontier_count += 1

                                # 근처맵핑 완료 → 최단 경유지 선택 후 경로맵핑
                                if _local_explore_phase and boss_no_frontier_count >= 3:
                                    _local_explore_phase = False
                                    full_mapping_exploring = False
                                    _is_mapping_mode = _is_mapping_test or full_mapping_exploring
                                    boss_no_frontier_count = 0
                                    explore_target = None
                                    explore_unreachable.clear()
                                    frontier_cooldown.clear()
                                    current_path = []
                                    path_index = 0
                                    path_pos_index = {}
                                    explored_from = {}
                                    last_dir = None
                                    stuck_count = 0
                                    _tiles = len(self._game_map.passable)
                                    # 도착좌표 passable 등록 (A* 도달 가능하도록)
                                    if target_x is not None and target_y is not None:
                                        self._game_map.mark_passable(target_x, target_y)
                                    _cur_re = all_targets[target_idx][7] if len(all_targets[target_idx]) > 7 else []
                                    for _mte_x, _mte_y in _cur_re:
                                        self._game_map.mark_passable(_mte_x, _mte_y)
                                    # 도착좌표 여러 개면 최단거리 선택
                                    if _cur_re:
                                        pathfinder.clear()
                                        _best_end = None
                                        _best_cost = float('inf')
                                        for _mte_x, _mte_y in _cur_re:
                                            _mt_r = pathfinder.find_path(
                                                (current_x, current_y), (_mte_x, _mte_y),
                                                allow_unknown=True, stop_event=self._stop_event,
                                                max_iterations=20000)
                                            time.sleep(0)  # GIL 해제
                                            if _mt_r.found and _mt_r.cost < _best_cost:
                                                _best_cost = _mt_r.cost
                                                _best_end = (_mte_x, _mte_y)
                                        if _best_end:
                                            target_x, target_y = _best_end
                                    self.after(0, lambda t=_tiles, tx=target_x, ty=target_y:
                                        self._append_log(f"✅ 근처맵핑 완료 ({t}칸) → ({tx},{ty}) 최단경로"))
                                    self._stop_event.wait(0.05)
                                    continue

                                # 30회 이상 프런티어 미발견 → 인접 벽 해제 (거짓 벽 복구)
                                if boss_no_frontier_count >= 30:
                                    unblocked = 0
                                    for (px, py) in list(self._game_map.passable):
                                        for ddx, ddy in [(0,-1),(0,1),(-1,0),(1,0)]:
                                            nb = (px+ddx, py+ddy)
                                            if nb in _portal_protected:
                                                continue  # 포탈 좌표 보호
                                            if nb in self._game_map.blocked:
                                                self._game_map.mark_passable(nb[0], nb[1])
                                                unblocked += 1
                                        if unblocked >= 5:
                                            break
                                    if unblocked > 0:
                                        self.after(0, lambda u=unblocked:
                                            self._append_log(f"🔓 프런티어 없음 → {u}개 인접벽 해제"))
                                        boss_no_frontier_count = 0
                                        explore_unreachable.clear()  # 벽 해제 후 재시도
                                        frontier_cooldown.clear()
                                    else:
                                        # 해제할 벽도 없음 → 강제 탐색 완료
                                        boss_no_frontier_count = 10  # 아래 완료 체크로 fall-through

                                # 5회 이상 → 벽 아닌 방향이라도 시도 (새 타일 발견 기대)
                                elif boss_no_frontier_count >= 5:
                                    for d, (ddx, ddy) in DIRECTIONS_4.items():
                                        nx, ny = current_x + ddx, current_y + ddy
                                        if not self._game_map.is_blocked(nx, ny):
                                            direction = d
                                            break

                                _fnf_ec2 = _local_explore_center if _local_explore_phase else None
                                _fnf_er2 = _local_explore_radius if _local_explore_phase else 0
                                if boss_no_frontier_count >= 10 and (
                                        self._game_map.is_fully_explored()
                                        or _find_nearest_frontier(current_x, current_y, explore_center=_fnf_ec2, explore_radius=_fnf_er2) is None):
                                    # 프런티어 없지만 미탐색 남음 + unreachable 있음 → 클리어 후 재시도
                                    if not self._game_map.is_fully_explored() and explore_unreachable:
                                        if _ui_update_ok:
                                            self.after(0, lambda n=len(explore_unreachable):
                                                self._append_log(f"🔄 미탐색 남음 → {n}개 unreachable 클리어 후 재탐색"))
                                        explore_unreachable.clear()
                                        frontier_cooldown.clear()
                                        boss_no_frontier_count = 0
                                        self._stop_event.wait(0.05)
                                        continue
                                    # 순찰 좌표가 있으면 순찰 모드 전환 (맵핑테스트 전체맵핑 중에는 스킵)
                                    if self._game_map.patrol_points and not full_mapping_exploring and not _local_explore_phase:
                                        from ..player.map_patroller import MapPatroller
                                        boss_patrol = MapPatroller(self._game_map)
                                        boss_patrol.start(current_pos_tuple)
                                        boss_mode = "patrolling"
                                        last_dir = None
                                        stuck_count = 0
                                        total_stuck_count = 0
                                        boss_no_frontier_count = 0
                                        current_path = []
                                        path_index = 0
                                        path_pos_index = {}
                                        explored_from = {}
                                        unknown_path_fails = 0
                                        explore_target = None
                                        explore_target_tries = 0
                                        patrol_skip_count = 0
                                        pc = len(self._game_map.patrol_points)
                                        self.after(0, lambda: self._append_log(f"{'='*30}"))
                                        self.after(0, lambda pc=pc: self._append_log(f"🔍 탐색 완료 → 순찰모드! {pc}개 좌표 순회"))
                                        self.after(0, lambda: self._append_log(f"{'='*30}"))
                                    # 순찰 좌표 없거나, 맵핑테스트 전체맵핑 → 다음 경유지
                                    if not self._game_map.patrol_points or full_mapping_exploring or _local_explore_phase:
                                        # 맵핑 모드: 탐색 완료 시 즉시 종료
                                        if getattr(self, '_is_mapping', False) and not _is_mapping_test:
                                            mapping_mode = getattr(self, '_mapping_mode', 'path')
                                            mode_label = "전체맵핑" if mapping_mode == "full" else "맵 탐색"
                                            stats = self._game_map.get_statistics()
                                            tile_count = stats.get('total_tiles', 0)
                                            self.after(0, lambda ml=mode_label: self._append_log(f"✅ {ml} 완료!"))
                                            self.after(0, lambda ml=mode_label, tc=tile_count:
                                                self._show_notification(
                                                    f"{ml} 완료",
                                                    f"{ml}이 완료되었습니다.\n총 {tc}개 타일 탐색",
                                                    duration=10000))
                                            return
                                        # 맵핑테스트: 전체맵핑 완료 → 경유지 이동 모드 전환
                                        if _is_mapping_test and full_mapping_exploring:
                                            _mt_stats = self._game_map.get_statistics()
                                            self.after(0, lambda tc=_mt_stats.get('total_tiles', 0):
                                                self._append_log(f"✅ 전체맵핑 완료 ({tc}타일) → 경유지 이동"))
                                            full_mapping_exploring = False
                                            _is_mapping_mode = _is_mapping_test or full_mapping_exploring
                                            _local_explore_phase = False  # 근처맵핑 상태도 리셋
                                            # 전체맵핑 완료 → 도착지/출구 포탈 벽 해제 (경유지 이동 가능하도록)
                                            # 단, 입구 포탈(start_routes)은 유지 (되돌아가면 맵 오염)
                                            _start_routes_keep = set()
                                            _sr_reblock = all_targets[target_idx][8] if len(all_targets[target_idx]) > 8 else []
                                            for _sx, _sy in _sr_reblock:
                                                _start_routes_keep.add((_sx, _sy))
                                            _pp_tiles = set(_portal_protected)
                                            _portal_protected.clear()
                                            for _pp in _pp_tiles:
                                                if _pp in _start_routes_keep:
                                                    _portal_protected.add(_pp)  # 입구는 보호 유지
                                                    continue
                                                self._game_map.mark_passable(_pp[0], _pp[1])
                                            # 입구 포탈 start_pos 재설정 (A* 회피용)
                                            if _sr_reblock:
                                                self._game_map.start_pos = tuple(_sr_reblock[0])
                                                self._game_map.mark_blocked(_sr_reblock[0][0], _sr_reblock[0][1])
                                            # 도착지 벽 해제 (이동할 수 있도록)
                                            if target_x is not None and target_y is not None:
                                                self._game_map.mark_passable(target_x, target_y)
                                            _cur_re = all_targets[target_idx][7] if len(all_targets[target_idx]) > 7 else []
                                            for _mte_x, _mte_y in _cur_re:
                                                self._game_map.mark_passable(_mte_x, _mte_y)
                                            # 도착좌표 여러 개면 최단거리 선택
                                            if _cur_re:
                                                _best_end = None
                                                _best_cost = float('inf')
                                                for _mte_x, _mte_y in _cur_re:
                                                    _mt_r = pathfinder.find_path(
                                                        (current_x, current_y), (_mte_x, _mte_y),
                                                        allow_unknown=False, stop_event=self._stop_event)
                                                    time.sleep(0)  # GIL 해제
                                                    if _mt_r.found and _mt_r.cost < _best_cost:
                                                        _best_cost = _mt_r.cost
                                                        _best_end = (_mte_x, _mte_y)
                                                if _best_end:
                                                    target_x, target_y = _best_end
                                                    self.after(0, lambda tx=target_x, ty=target_y, bc=_best_cost:
                                                        self._append_log(f"🎯 최단 경유지: ({tx},{ty}) (거리:{bc})"))
                                            # 맵 저장
                                            if not self._map_save_lock.locked() and not self._stop_event.is_set():
                                                threading.Thread(target=self._auto_save_map, daemon=True).start()
                                            # 보스 던전 구간 또는 (0,0) 미도달 목표: 맵핑 완료 즉시 다음 경유지 또는 테스트 완료
                                            _cur_re_fm = all_targets[target_idx][7] if len(all_targets[target_idx]) > 7 else []
                                            _unreachable_target = (all_targets[target_idx][0] == 0 and all_targets[target_idx][1] == 0 and not _cur_re_fm)
                                            if all_targets[target_idx][3] or _unreachable_target:
                                                # (0,0) 미도달 목표: 도착 키 입력 (보스 구간은 별도 처리)
                                                if _unreachable_target and not all_targets[target_idx][3]:
                                                    _arr_keys_fm = all_targets[target_idx][6] if len(all_targets[target_idx]) > 6 else []
                                                    if _arr_keys_fm and not self._stop_event.is_set():
                                                        self._stop_event.wait(1.0)
                                                        if not self._stop_event.is_set():
                                                            self._exec_arrival_keys(_arr_keys_fm)
                                                            _kn_fm = ",".join(kd.get("key","") for kd in _arr_keys_fm)
                                                            self.after(0, lambda k=_kn_fm: self._append_log(f"🔑 도착 키 입력: {k}"))
                                                _prev_boss3 = all_targets[target_idx][3]
                                                target_idx += 1
                                                if target_idx >= len(all_targets):
                                                    self._boss_segment_active = False
                                                    self.after(0, lambda cx=current_x, cy=current_y:
                                                        self._append_log(f"🎯 맵핑테스트 완료! ({cx},{cy})"))
                                                    self.after(0, self._on_arrival)
                                                    return
                                                if self._stop_event.is_set():
                                                    break
                                                self._boss_segment_active = False
                                                # 맵핑테스트: (0,0) 경유지도 맵 데이터 저장 (유실 방지)
                                                _skip_save3 = (_prev_boss3 and not _is_mapping_test) or _no_save
                                                if not self._switch_segment_map(target_idx, skip_save=_skip_save3):
                                                    self.after(0, lambda: self._append_log("⚠️ 맵 구간 전환 실패"))
                                                pathfinder = SimplePathfinder(self._game_map)
                                                self._map_pathfinder = pathfinder
                                                target_x, target_y = _pick_target(target_idx)
                                                _seg_name = all_targets[target_idx][2]
                                                self.after(0, lambda sn=_seg_name, tx=target_x, ty=target_y:
                                                    self._append_log(f"▶ 다음: ({tx},{ty}) [{sn}]"))
                                                # 다음 구간 상태 초기화
                                                boss_mode = "exploring"
                                                boss_patrol = None
                                                boss_no_frontier_count = 0
                                                explore_target = None
                                                explore_target_tries = 0
                                                explore_unreachable = set()
                                                _portal_protected.clear()
                                                frontier_cooldown.clear()
                                                stuck_count = 0
                                                total_stuck_count = 0
                                                current_path = []
                                                path_index = 0
                                                path_pos_index = {}
                                                explored_from = {}
                                                unknown_path_fails = 0
                                                last_dir = None
                                                recent_positions.clear()
                                                _boss_chasing = False
                                                _boss_chase_miss = 0
                                                boss_approach_steps = 0
                                                boss_appr_miss = 0
                                                patrol_skip_count = 0
                                                _mt_seg_has_starts = bool(all_targets[target_idx][8] if len(all_targets[target_idx]) > 8 else [])
                                                _segment_map_locked = all_targets[target_idx][9] if len(all_targets[target_idx]) > 9 else False
                                                full_mapping_exploring = (_is_mapping_test and _mt_seg_has_starts and not _segment_map_locked)
                                                _is_mapping_mode = _is_mapping_test or full_mapping_exploring
                                                _local_explore_phase = _is_mapping_test and not _mt_seg_has_starts and not _segment_map_locked
                                                _local_explore_center = None
                                                _next_is_boss3 = all_targets[target_idx][3] if len(all_targets[target_idx]) > 3 else False
                                                is_boss_dungeon = _next_is_boss3 or _local_explore_phase or full_mapping_exploring
                                                if _next_is_boss3:
                                                    boss_pre_teleport = True
                                                    mapping_on = False
                                                    mark_portal_entry = False
                                                else:
                                                    boss_pre_teleport = False
                                                    mapping_on = not _no_save
                                                    mark_portal_entry = True
                                                if _segment_map_locked:
                                                    mapping_on = False
                                                portal_grace = 30
                                                prev_x, prev_y = current_x, current_y
                                                time.sleep(0.05)
                                                continue
                                            # 비보스 구간: 도착지로 이동 모드 전환
                                            boss_mode = "exploring"
                                            boss_no_frontier_count = 0
                                            explore_target = None
                                            explore_target_tries = 0
                                            explore_unreachable = set()
                                            frontier_cooldown.clear()
                                            current_path = []
                                            path_index = 0
                                            path_pos_index = {}
                                            explored_from = {}
                                            unknown_path_fails = 0
                                            last_dir = None
                                            recent_positions.clear()
                                            _boss_chasing = False
                                            _boss_chase_miss = 0
                                            boss_approach_steps = 0
                                            boss_appr_miss = 0
                                            prev_x, prev_y = current_x, current_y
                                            time.sleep(0.05)
                                            continue
                                        self.after(0, lambda: self._append_log("✅ 맵 탐색 완료 → 다음 경유지"))
                                        # 다음 경유지로 전환
                                        _prev_boss3 = all_targets[target_idx][3] if len(all_targets[target_idx]) > 3 else False
                                        target_idx += 1
                                        if target_idx >= len(all_targets):
                                            self._boss_segment_active = False  # 리셋 (auto_save 차단 해제)
                                            self.after(0, self._on_arrival)
                                            return
                                        if self._stop_event.is_set():
                                            break
                                        self._boss_segment_active = False
                                        # 맵핑테스트: (0,0) 경유지도 맵 데이터 저장 (유실 방지)
                                        _skip_save3b = (_prev_boss3 and not _is_mapping_test) or _no_save
                                        if not self._switch_segment_map(target_idx, skip_save=_skip_save3b):
                                            self.after(0, lambda: self._append_log("⚠️ 맵 구간 전환 실패"))
                                        pathfinder = SimplePathfinder(self._game_map)
                                        self._map_pathfinder = pathfinder
                                        target_x, target_y = _pick_target(target_idx)
                                        boss_mode = "exploring"
                                        boss_patrol = None
                                        explore_target = None
                                        explore_target_tries = 0
                                        explore_unreachable = set()
                                        _portal_protected.clear()
                                        frontier_cooldown.clear()
                                        stuck_count = 0
                                        total_stuck_count = 0
                                        current_path = []
                                        path_index = 0
                                        path_pos_index = {}
                                        explored_from = {}
                                        unknown_path_fails = 0
                                        last_dir = None
                                        recent_positions.clear()
                                        _boss_chasing = False
                                        _boss_chase_miss = 0
                                        boss_approach_steps = 0
                                        boss_appr_miss = 0
                                        patrol_skip_count = 0
                                        _mt_seg_has_starts = bool(all_targets[target_idx][8] if len(all_targets[target_idx]) > 8 else [])
                                        _segment_map_locked = all_targets[target_idx][9] if len(all_targets[target_idx]) > 9 else False
                                        full_mapping_exploring = (_is_mapping_test and _mt_seg_has_starts and not _segment_map_locked)
                                        _is_mapping_mode = _is_mapping_test or full_mapping_exploring
                                        _local_explore_phase = _is_mapping_test and not _mt_seg_has_starts and not _segment_map_locked
                                        _local_explore_center = None
                                        # _local_explore_phase는 full_mapping_exploring과 독립 (근처맵핑 ≠ 전체맵핑)
                                        # 다음 구간 보스 여부에 따라 상태 설정
                                        _next_is_boss3 = all_targets[target_idx][3] if len(all_targets[target_idx]) > 3 else False
                                        is_boss_dungeon = _next_is_boss3
                                        if _next_is_boss3:
                                            boss_pre_teleport = True
                                            mapping_on = False
                                            mark_portal_entry = False
                                        else:
                                            boss_pre_teleport = False
                                            mapping_on = not _no_save
                                            mark_portal_entry = True
                                        if _segment_map_locked:
                                            mapping_on = False
                                        portal_grace = 30  # 구간 전환 후 점프 필터 비활성 (던전 로딩 대기)
                                        prev_x, prev_y = current_x, current_y
                                        boss_no_frontier_count = 0
                                        time.sleep(0.05)
                                        continue

                    elif boss_mode == "approaching":
                        # ── 보스 근접: 스킬 사용 + 보스 이미지 추적 ──
                        # 보스 스킬 사용 (쿨타임 체크)
                        if boss_skill_enabled and boss_skill_key:
                            _now = time.time()
                            if _now - last_boss_skill_time >= boss_skill_cooldown:
                                try:
                                    pyautogui.keyDown(boss_skill_key)
                                    try:
                                        time.sleep(0.05)
                                    finally:
                                        pyautogui.keyUp(boss_skill_key)
                                    self._key_press_count += 1
                                    last_boss_skill_time = _now
                                    self.after(0, lambda cx=current_x, cy=current_y, k=boss_skill_key:
                                        self._append_log(f"⚔️ 보스 스킬! ({cx},{cy}) 키={k}"))
                                except Exception as _e:
                                    logger.error(f"[좌표모드] 보스 스킬 키 입력 실패: {_e}")

                        # 보스 이미지 재확인 (2회당 1번 — 홀수 반복은 스킵)
                        _appr_boss_visible = False
                        _appr_detection_attempted = False
                        if iteration % 2 == 0 and not self._stop_event.is_set():
                            _appr_detection_attempted = True
                            try:
                                import numpy as _np3
                                import cv2 as _cv23
                                from ..analyzer.template_matcher import TemplateMatcher as _TM3
                                _boss_ip3 = all_targets[target_idx][4] if len(all_targets[target_idx]) > 4 else None
                                _char_ip3 = all_targets[target_idx][5] if len(all_targets[target_idx]) > 5 else None
                                if _boss_ip3:
                                    _ss3 = getattr(matcher, '_last_screenshot', None)
                                    if _ss3 is None:
                                        import pyautogui as _pag3
                                        _ss3 = _pag3.screenshot()
                                    _scr3 = _np3.array(_ss3)
                                    _scr3 = _cv23.cvtColor(_scr3, _cv23.COLOR_RGB2BGR)
                                    _m3 = _TM3()
                                    _r3 = _m3.match_binary(_scr3, _boss_ip3, threshold=0.8)
                                    time.sleep(0)  # GIL 해제
                                    if _r3.found:
                                        _appr_boss_visible = True
                                        # 거리 재계산 → 멀어졌으면 다시 추적
                                        _sh3, _sw3 = _scr3.shape[:2]
                                        _cpx3, _cpy3 = _sw3 // 2, _sh3 // 2
                                        _char_det3 = False
                                        if _char_ip3:
                                            try:
                                                _cr3 = _m3.match_binary(_scr3, _char_ip3, threshold=0.8)
                                                time.sleep(0)  # GIL 해제
                                                if _cr3.found:
                                                    _cpx3, _cpy3 = _cr3.center_x, _cr3.center_y
                                                    _char_det3 = True
                                            except Exception:
                                                pass
                                        _dx3 = _r3.center_x - _cpx3
                                        _dy3 = _r3.center_y - _cpy3
                                        _td3 = abs(round(_dy3 / 44))  # X offset 무시 (UI NAME 고정)
                                        # approaching 중 보스 방향으로 이동하기 위해 저장
                                        self._appr_dy3 = round(_dy3 / 44) if _char_det3 else None
                                        # 캐릭터 미감지 시 거리 신뢰 불가 → 거리 판정 건너뜀 (보스 감지만 유지)
                                        if not _char_det3:
                                            _td3 = 0  # 거리 판정 안 함 (approaching 유지)
                                        if _td3 > 20:
                                            # UI 오탐 (고정 UI 이름 요소) → 보스 사라진 것으로 처리
                                            _appr_boss_visible = False
                                            self.after(0, lambda cx=current_x, cy=current_y, td=_td3:
                                                self._append_log(f"✅ 보스 사라짐 ({cx},{cy}) 거리={td}타일 — UI 오탐"))
                                        elif _td3 > 2:
                                            # 보스가 멀어짐 (2타일 초과) → 다시 추적
                                            _chase_tx = current_x  # X offset 무시 (UI NAME 고정)
                                            _chase_ty = current_y + round(_dy3 / 44)
                                            # start_pos 밟지 않도록 확인
                                            _td3_sp = self._game_map.start_pos
                                            if _td3_sp is not None and (_chase_tx, _chase_ty) == _td3_sp:
                                                _td3_dy_sign = 1 if round(_dy3 / 44) >= 0 else -1
                                                _chase_ty += _td3_dy_sign
                                            _boss_chasing = True
                                            _boss_chase_miss = 0
                                            boss_approach_steps = 0
                                            boss_appr_miss = 0
                                            stuck_count = 0
                                            total_stuck_count = 0
                                            last_dir = None
                                            prev_x, prev_y = current_x, current_y
                                            current_path = []
                                            path_index = 0
                                            path_pos_index = {}
                                            pathfinder.invalidate_path()
                                            self._appr_dy3 = None  # approaching 상태 정리
                                            boss_mode = "patrolling"
                                            # 순찰 재시작 (stale is_completed 방지 → 거짓 구간전환 차단)
                                            if boss_patrol is not None:
                                                boss_patrol.start((current_x, current_y))
                                            self.after(0, lambda cx=current_x, cy=current_y, td=_td3:
                                                self._append_log(f"🏃 보스 멀어짐 ({cx},{cy}) 거리={td}타일 → 재추적+스킬"))
                                            self._stop_event.wait(0.05)
                                            continue
                            except Exception:
                                pass

                        if _appr_boss_visible:
                            boss_appr_miss = 0  # 감지 성공 → 미스 리셋
                            # 보스 방향 이동: Y 미정렬이면 Y방향, Y 정렬이면 X축 지그재그 스캔
                            # ※ 이동 전 반드시 맵(is_passable) + start_pos 확인
                            _appr_dy_val = getattr(self, '_appr_dy3', None)
                            _appr_start = self._game_map.start_pos
                            if _appr_dy_val is not None and abs(_appr_dy_val) > 0:
                                # Y 미정렬 → Y방향 이동
                                _appr_move_dir = "down" if _appr_dy_val > 0 else "up"
                                _appr_ndy = 1 if _appr_dy_val > 0 else -1
                                _appr_nx, _appr_ny = current_x, current_y + _appr_ndy
                                # 맵 확인: passable이고 start_pos가 아닌 경우만 이동
                                if self._game_map.is_passable(_appr_nx, _appr_ny) and (_appr_start is None or (_appr_nx, _appr_ny) != _appr_start):
                                    self._appr_x_step = 0
                                    try:
                                        _mk = self._config.move_keys.get(_appr_move_dir, _appr_move_dir)
                                        pyautogui.press(_mk)
                                        self._key_press_count += 1
                                    except Exception:
                                        pass
                                # else: 벽 또는 출발지 → 이동 안 함 (스킬만 유지)
                            else:
                                # Y 정렬됨 (dy==0) or 거리 불명 → X축 좌우 지그재그 스캔
                                if not hasattr(self, '_appr_x_step'):
                                    self._appr_x_step = 0
                                    self._appr_x_dir = 1
                                    self._appr_x_max = 2
                                self._appr_x_step += 1
                                if self._appr_x_step > self._appr_x_max:
                                    self._appr_x_dir *= -1  # 방향 전환
                                    self._appr_x_step = 1
                                    self._appr_x_max = min(self._appr_x_max + 1, 6)  # 범위 확대 (최대 6)
                                _appr_ndx = self._appr_x_dir
                                _appr_nx, _appr_ny = current_x + _appr_ndx, current_y
                                # 맵 확인: passable이고 start_pos가 아닌 경우만 이동
                                if self._game_map.is_passable(_appr_nx, _appr_ny) and (_appr_start is None or (_appr_nx, _appr_ny) != _appr_start):
                                    _appr_move_dir = "right" if self._appr_x_dir > 0 else "left"
                                    try:
                                        _mk = self._config.move_keys.get(_appr_move_dir, _appr_move_dir)
                                        pyautogui.press(_mk)
                                        self._key_press_count += 1
                                    except Exception:
                                        pass
                                else:
                                    # 벽 또는 출발지 → 즉시 방향 전환
                                    self._appr_x_dir *= -1
                            if _ui_update_ok:
                                self.after(0, lambda cx=current_x, cy=current_y:
                                    self._update_status("보스스킬", f"({cx},{cy})", "", "전투중", "⚔️"))
                        else:
                            if _appr_detection_attempted:
                                boss_appr_miss += 1
                            if boss_appr_miss < 10:
                                # 아직 미스 부족 → 계속 스킬 쓰면서 대기
                                prev_x, prev_y = current_x, current_y
                                stuck_count = 0
                                total_stuck_count = 0
                                self._stop_event.wait(0.1)
                                continue
                            boss_appr_miss = 0
                            # 10회 연속 미감지 → 보스 처치 판정
                            _arr_keys = all_targets[target_idx][6] if len(all_targets[target_idx]) > 6 else []
                            self.after(0, lambda cx=current_x, cy=current_y, nk=len(_arr_keys):
                                self._append_log(f"✅ 보스 처치! ({cx},{cy}) → 다음 경유지 (키{nk}개)"))
                            logger.info(f"[좌표모드] 보스 처치 → 도착키 {len(_arr_keys)}개: {_arr_keys}")
                            # 보스 사망 애니메이션 대기
                            self._stop_event.wait(2.0)
                            # 도착 키 입력
                            if _arr_keys and not self._stop_event.is_set():
                                self._exec_arrival_keys(_arr_keys)
                                _kn = ",".join(kd.get("key","") for kd in _arr_keys)
                                self.after(0, lambda k=_kn: self._append_log(f"🔑 도착 키 입력: {k}"))
                            _prev_idx = target_idx
                            target_idx += 1
                            if target_idx >= len(all_targets):
                                self._boss_segment_active = False  # 리셋 (auto_save 차단 해제)
                                self.after(0, lambda: self._append_log(f"🎯 전체 완료!"))
                                self.after(0, self._on_arrival)
                                return
                            if self._stop_event.is_set():
                                break
                            self._stop_event.wait(0.5)
                            # 보스 경유지(0,0)에서 나갈 때는 맵 저장 안 함 (오염 방지)
                            # 맵핑테스트에서는 항상 저장 (맵 데이터 유실 방지)
                            _prev_is_boss = all_targets[_prev_idx][3] if len(all_targets[_prev_idx]) > 3 else False
                            if use_map and (not _prev_is_boss or _is_mapping_test) and not _no_save:
                                self._game_map.end_pos = (current_x, current_y)
                            self._boss_segment_active = False
                            if not self._switch_segment_map(target_idx, skip_save=(_prev_is_boss and not _is_mapping_test) or _no_save):
                                self.after(0, lambda: self._append_log("⚠️ 맵 구간 전환 실패"))
                            pathfinder = SimplePathfinder(self._game_map)
                            self._map_pathfinder = pathfinder
                            target_x, target_y = _pick_target(target_idx)
                            seg_name = all_targets[target_idx][2]
                            self.after(0, lambda sn=seg_name, ti=target_idx, total=len(all_targets):
                                self._append_log(f"▶ 다음 경유지: [{sn}] ({ti}/{total})"))
                            stuck_count = 0
                            total_stuck_count = 0
                            current_path = []
                            path_index = 0
                            path_pos_index = {}
                            explored_from = {}
                            unknown_path_fails = 0
                            last_dir = None
                            recent_positions.clear()
                            boss_mode = "exploring"
                            boss_patrol = None
                            explore_target = None
                            explore_target_tries = 0
                            explore_unreachable = set()
                            _portal_protected.clear()
                            frontier_cooldown.clear()
                            boss_no_frontier_count = 0
                            boss_approach_steps = 0
                            boss_appr_miss = 0
                            _boss_chasing = False
                            _boss_chase_miss = 0
                            patrol_skip_count = 0
                            _mt_seg_has_starts = bool(all_targets[target_idx][8] if len(all_targets[target_idx]) > 8 else [])
                            _segment_map_locked = all_targets[target_idx][9] if len(all_targets[target_idx]) > 9 else False
                            full_mapping_exploring = (_is_mapping_test and _mt_seg_has_starts and not _segment_map_locked)
                            _is_mapping_mode = _is_mapping_test or full_mapping_exploring
                            _local_explore_phase = _is_mapping_test and not _mt_seg_has_starts and not _segment_map_locked
                            _local_explore_center = None
                            # _local_explore_phase는 full_mapping_exploring과 독립 (근처맵핑 ≠ 전체맵핑)
                            is_boss_dungeon = all_targets[target_idx][3]
                            if is_boss_dungeon:
                                boss_pre_teleport = True
                                mapping_on = False
                                mark_portal_entry = False
                            else:
                                mapping_on = not _no_save
                                mark_portal_entry = True
                            if _segment_map_locked:
                                mapping_on = False
                            portal_grace = 30
                            prev_x, prev_y = current_x, current_y
                            time.sleep(0.05)
                            continue

                        prev_x, prev_y = None, None
                        stuck_count = 0
                        total_stuck_count = 0
                        self._stop_event.wait(0.1)
                        continue

                    elif boss_mode == "patrolling":
                        # ── 순찰: 등록된 순찰좌표를 순서대로 A* 이동 ──

                        # ── 보스 이미지 검색 (5회마다) ──
                        _boss_found = False
                        _check_boss_image = (iteration % 5 == 0)
                        if _check_boss_image:
                            boss_img_path = all_targets[target_idx][4] if len(all_targets[target_idx]) > 4 else None
                            char_img_path = all_targets[target_idx][5] if len(all_targets[target_idx]) > 5 else None
                            if boss_img_path:
                                try:
                                    import numpy as _np
                                    import cv2 as _cv2
                                    from ..analyzer.template_matcher import TemplateMatcher as _TM
                                    # 좌표 읽기 시 캡처한 스크린샷 재사용 (이중 캡처 방지)
                                    _ss = getattr(matcher, '_last_screenshot', None)
                                    if _ss is None:
                                        import pyautogui as _pag
                                        _ss = _pag.screenshot()
                                    _screen = _np.array(_ss)
                                    _screen = _cv2.cvtColor(_screen, _cv2.COLOR_RGB2BGR)
                                    _m = _TM()
                                    _res = _m.match_binary(_screen, boss_img_path, threshold=0.8)
                                    time.sleep(0)  # GIL 해제
                                    if _res.found:
                                        _boss_found = True
                                        boss_approach_steps += 1
                                        scr_h, scr_w = _screen.shape[:2]

                                        # 캐릭터 스프라이트 감지 (있으면), 없으면 화면 중앙 폴백
                                        char_pixel_x, char_pixel_y = scr_w // 2, scr_h // 2
                                        _char_detected = False
                                        if char_img_path:
                                            try:
                                                _char_res = _m.match_binary(_screen, char_img_path, threshold=0.8)
                                                time.sleep(0)  # GIL 해제
                                                if _char_res.found:
                                                    char_pixel_x = _char_res.center_x
                                                    char_pixel_y = _char_res.center_y
                                                    _char_detected = True
                                            except Exception:
                                                pass  # 캐릭터 미감지 시 화면 중앙 폴백

                                        # X, Y 픽셀 차이 (보스 - 캐릭터)
                                        dx_pixel = _res.center_x - char_pixel_x
                                        dy_pixel = _res.center_y - char_pixel_y

                                        # 타일 거리 계산 (Y만 사용 — X는 UI NAME 고정 위치로 항상 -947px)
                                        TILE_PX = 44
                                        TILE_PY = 44
                                        est_dx_tiles = round(dx_pixel / TILE_PX)
                                        est_dy_tiles = round(dy_pixel / TILE_PY)
                                        tile_dist = abs(est_dy_tiles)  # X offset 무시 (UI 고정)

                                        # 20타일 초과 → UI 요소 오탐, 무시
                                        if tile_dist > 20:
                                            self.after(0, lambda dx=dx_pixel, dy=dy_pixel, td=tile_dist:
                                                self._append_log(f"⚠️ 보스 감지 무시 (거리={td}타일, dx={dx}px) — UI 오탐 추정"))
                                            _boss_found = False  # UI 오탐 → 감지 아님 처리

                                        # 1타일 이내 → approaching (캐릭터 미감지 시 화면중앙 폴백으로 거리 오차 → chasing으로만 전환)
                                        elif tile_dist <= 1 and _char_detected:
                                            _boss_chase_miss = 0
                                            _boss_chasing = False
                                            boss_mode = "approaching"
                                            boss_appr_miss = 0
                                            stuck_count = 0
                                            total_stuck_count = 0
                                            last_dir = None
                                            current_path = []
                                            path_index = 0
                                            path_pos_index = {}
                                            pathfinder.invalidate_path()
                                            # X축 스캔 상태 초기화 (좌우 교대 탐색용)
                                            self._appr_x_step = 0
                                            self._appr_x_dir = 1  # 1=right, -1=left
                                            self._appr_x_max = 2  # 한 방향 최대 스텝
                                            # 즉시 보스 스킬 사용
                                            if boss_skill_enabled and boss_skill_key:
                                                try:
                                                    pyautogui.keyDown(boss_skill_key)
                                                    try:
                                                        time.sleep(0.05)
                                                    finally:
                                                        pyautogui.keyUp(boss_skill_key)
                                                    self._key_press_count += 1
                                                    last_boss_skill_time = time.time()
                                                except Exception:
                                                    pass
                                            self.after(0, lambda conf=_res.confidence, s=boss_approach_steps,
                                                       cx=current_x, cy=current_y, dx=dx_pixel, dy=dy_pixel,
                                                       td=tile_dist:
                                                self._append_log(f"🎯 보스 근접! ({cx},{cy}) dx={dx}px dy={dy}px 거리={td}타일 {s}회 → 접근 완료 (신뢰도={conf:.1%})"))
                                            prev_x, prev_y = current_x, current_y
                                            time.sleep(0.05)
                                            continue

                                        else:
                                            # ── 보스 추적: 추정 위치로 직행 (Y축 + X축 스캔) ──
                                            _boss_chase_miss = 0
                                            _boss_chasing = True
                                            stuck_count = 0
                                            total_stuck_count = 0
                                            _chase_ty = current_y + est_dy_tiles
                                            # X는 UI NAME 고정이라 오프셋 못 씀 → 현재 X 유지
                                            # (Y 도달 후 recheck에서 X 탐색)
                                            _chase_tx = current_x
                                            # 통과 가능 타일 찾기 (start_pos 제외)
                                            _chase_sp = self._game_map.start_pos
                                            _chase_is_sp = (_chase_sp is not None and (_chase_tx, _chase_ty) == _chase_sp)
                                            if not self._game_map.is_passable(_chase_tx, _chase_ty) or _chase_is_sp:
                                                _found_chase = False
                                                for _r in range(1, 6):
                                                    for _ddx, _ddy in [(0,0),(1,0),(-1,0),(0,1),(0,-1),(1,1),(-1,1),(1,-1),(-1,-1)]:
                                                        _cx = _chase_tx + _ddx * _r
                                                        _cy = _chase_ty + _ddy * _r
                                                        if (self._game_map.is_passable(_cx, _cy)
                                                                and (_chase_sp is None or (_cx, _cy) != _chase_sp)):
                                                            _chase_tx, _chase_ty = _cx, _cy
                                                            _found_chase = True
                                                            break
                                                    if _found_chase:
                                                        break
                                            if _ui_update_ok:
                                                self.after(0, lambda conf=_res.confidence, s=boss_approach_steps,
                                                           cx=current_x, cy=current_y,
                                                           dx=dx_pixel, dy=dy_pixel,
                                                           tx=_chase_tx, ty=_chase_ty,
                                                           td=tile_dist:
                                                    self._append_log(f"🎯 보스 추적! dx={dx}px dy={dy}px ({cx},{cy})→({tx},{ty}) 거리={td}타일 ({s}회)"))
                                except Exception as _e:
                                    logger.debug(f"[순찰] 보스 이미지 검색 오류: {_e}")

                        # 보스 미감지 시 카운트 감소
                        if not _boss_found:
                            if boss_approach_steps > 0 and iteration % 20 == 0:
                                boss_approach_steps = max(0, boss_approach_steps - 1)

                        # ── 순찰 1바퀴 완료 체크 → 다음 경유지 ──
                        if not _boss_chasing and boss_patrol is not None and boss_patrol.is_completed:
                            self.after(0, lambda cx=current_x, cy=current_y:
                                self._append_log(f"🔄 순찰 1바퀴 완료 ({cx},{cy}) 보스 없음 → 다음 경유지"))
                            # 이동 애니메이션 대기 후 도착 키 입력
                            _arr_keys = all_targets[target_idx][6] if len(all_targets[target_idx]) > 6 else []
                            if _arr_keys and not self._stop_event.is_set():
                                self._stop_event.wait(2.0)
                                if not self._stop_event.is_set():
                                    self._exec_arrival_keys(_arr_keys)
                                    _kn = ",".join(kd.get("key","") for kd in _arr_keys)
                                    self.after(0, lambda k=_kn: self._append_log(f"🔑 도착 키 입력: {k}"))
                            _prev_idx2 = target_idx
                            target_idx += 1
                            if target_idx >= len(all_targets):
                                self._boss_segment_active = False  # 리셋 (auto_save 차단 해제)
                                self.after(0, lambda: self._append_log(f"🎯 전체 완료!"))
                                self.after(0, self._on_arrival)
                                return
                            if self._stop_event.is_set():
                                break
                            self._stop_event.wait(0.5)
                            # 보스 경유지(0,0)에서 나갈 때는 맵 저장 안 함 (오염 방지)
                            # 맵핑테스트에서는 항상 저장 (맵 데이터 유실 방지)
                            _prev_is_boss2 = all_targets[_prev_idx2][3] if len(all_targets[_prev_idx2]) > 3 else False
                            if use_map and (not _prev_is_boss2 or _is_mapping_test) and not _no_save:
                                self._game_map.end_pos = (current_x, current_y)
                            self._boss_segment_active = False
                            if not self._switch_segment_map(target_idx, skip_save=(_prev_is_boss2 and not _is_mapping_test) or _no_save):
                                self.after(0, lambda: self._append_log("⚠️ 맵 구간 전환 실패"))
                            pathfinder = SimplePathfinder(self._game_map)
                            self._map_pathfinder = pathfinder
                            target_x, target_y = _pick_target(target_idx)
                            seg_name = all_targets[target_idx][2]
                            prev_seg = all_targets[target_idx - 1][2]
                            self.after(0, lambda sn=seg_name, ti=target_idx, total=len(all_targets):
                                self._append_log(f"▶ 다음 경유지: [{sn}] ({ti}/{total})"))
                            stuck_count = 0
                            total_stuck_count = 0
                            current_path = []
                            path_index = 0
                            path_pos_index = {}
                            explored_from = {}
                            unknown_path_fails = 0
                            last_dir = None
                            recent_positions.clear()
                            boss_mode = "exploring"
                            boss_patrol = None
                            explore_target = None
                            explore_target_tries = 0
                            explore_unreachable = set()
                            _portal_protected.clear()
                            frontier_cooldown.clear()
                            boss_no_frontier_count = 0
                            boss_approach_steps = 0
                            boss_appr_miss = 0
                            _boss_chasing = False
                            _boss_chase_miss = 0
                            patrol_skip_count = 0
                            _mt_seg_has_starts = bool(all_targets[target_idx][8] if len(all_targets[target_idx]) > 8 else [])
                            _segment_map_locked = all_targets[target_idx][9] if len(all_targets[target_idx]) > 9 else False
                            full_mapping_exploring = (_is_mapping_test and _mt_seg_has_starts and not _segment_map_locked)
                            _is_mapping_mode = _is_mapping_test or full_mapping_exploring
                            _local_explore_phase = _is_mapping_test and not _mt_seg_has_starts and not _segment_map_locked
                            _local_explore_center = None
                            # _local_explore_phase는 full_mapping_exploring과 독립 (근처맵핑 ≠ 전체맵핑)
                            is_boss_dungeon = all_targets[target_idx][3]
                            if is_boss_dungeon:
                                boss_pre_teleport = True
                                mapping_on = False
                                mark_portal_entry = False
                            else:
                                mapping_on = not _no_save
                                mark_portal_entry = True
                            if _segment_map_locked:
                                mapping_on = False
                            portal_grace = 30
                            prev_x, prev_y = current_x, current_y
                            time.sleep(0.05)
                            continue

                        # ── 목표 결정: 보스 추적 우선, 없으면 순찰 ──
                        if _boss_chasing:
                            _move_target = (_chase_tx, _chase_ty)
                        else:
                            _move_target_raw = boss_patrol.get_next_target(current_pos_tuple)
                            if _move_target_raw is None:
                                self.after(0, lambda: self._append_log("⚠️ 순찰 좌표 없음 → 탐색 복귀"))
                                boss_mode = "exploring"
                                boss_patrol = None
                                _boss_chasing = False
                                _boss_chase_miss = 0
                                stuck_count = 0
                                total_stuck_count = 0
                                last_dir = None
                                current_path = []
                                path_index = 0
                                path_pos_index = {}
                                explored_from = {}
                                unknown_path_fails = 0
                                boss_no_frontier_count = 0
                                frontier_cooldown.clear()
                                explore_unreachable = set()
                                boss_approach_steps = 0
                                boss_appr_miss = 0
                                patrol_skip_count = 0
                                explore_target = None
                                explore_target_tries = 0
                                prev_x, prev_y = current_x, current_y
                                self._stop_event.wait(0.05)
                                continue
                            _move_target = (_move_target_raw[0], _move_target_raw[1])

                        # A* 경로 계산 (캐시 활용 — 목표/경로 변경 시에만 재계산)
                        # 보스 추적 시 allow_unknown=True (미탐색 영역의 보스도 추적 가능)
                        pathfinder.set_goal(_move_target)
                        direction = pathfinder.get_next_direction(
                            current_pos_tuple,
                            allow_unknown=_boss_chasing,
                            stop_event=self._stop_event,
                            max_iterations=5000)
                        if direction and not _boss_chasing:
                            patrol_skip_count = 0

                        if direction and not self._stop_event.is_set() and _ui_update_ok:
                            _remaining = len(pathfinder.get_remaining_path())
                            if _boss_chasing:
                                self.after(0, lambda cx=current_x, cy=current_y, d=direction,
                                           tx=_move_target[0], ty=_move_target[1], pl=_remaining:
                                    self._append_log(f"🏃 추적 ({cx},{cy})→({tx},{ty}) {d} ({pl}칸)"))
                            else:
                                self.after(0, lambda cx=current_x, cy=current_y, d=direction,
                                           tx=_move_target[0], ty=_move_target[1], pl=_remaining:
                                    self._append_log(f"🚶 순찰 ({cx},{cy})→({tx},{ty}) {d} ({pl}칸)"))

                        if direction is None:
                            if _boss_chasing and not self._stop_event.is_set():
                                # 추적 중 갇힘 → 주변 임시벽 자동 해제
                                if use_map:
                                    _cleared = 0
                                    for _cd, (_cddx, _cddy) in DIRECTIONS_4.items():
                                        _cnx, _cny = current_x + _cddx, current_y + _cddy
                                        if self._game_map.is_soft_blocked(_cnx, _cny):
                                            self._game_map.clear_soft_blocked(_cnx, _cny)
                                            _cleared += 1
                                    if _cleared > 0:
                                        self.after(0, lambda cx=current_x, cy=current_y, c=_cleared:
                                            self._append_log(f"🔓 추적 갇힘({cx},{cy}) → 임시벽 {c}개 해제"))
                                        current_path = []
                                        path_index = 0
                                        path_pos_index = {}
                                        pathfinder.invalidate_path()
                                        prev_x, prev_y = current_x, current_y
                                        time.sleep(0.05)
                                        continue  # 임시벽 해제 후 이동 재시도 (재감지 전에)
                                # 추적 목표 도달/실패 → 즉시 보스 재감지
                                _rechk_found = False
                                try:
                                    import pyautogui as _pag2
                                    import numpy as _np2
                                    import cv2 as _cv22
                                    from ..analyzer.template_matcher import TemplateMatcher as _TM2
                                    _boss_ip = all_targets[target_idx][4] if len(all_targets[target_idx]) > 4 else None
                                    _char_ip = all_targets[target_idx][5] if len(all_targets[target_idx]) > 5 else None
                                    if _boss_ip and not self._stop_event.is_set():
                                        _ss2 = _pag2.screenshot()
                                        _scr2 = _np2.array(_ss2)
                                        _scr2 = _cv22.cvtColor(_scr2, _cv22.COLOR_RGB2BGR)
                                        _m2 = _TM2()
                                        _r2 = _m2.match_binary(_scr2, _boss_ip, threshold=0.8)
                                        time.sleep(0)  # GIL 해제
                                        if _r2.found:
                                            _rechk_found = True
                                            _sh2, _sw2 = _scr2.shape[:2]
                                            _cpx2, _cpy2 = _sw2 // 2, _sh2 // 2
                                            _char_det2 = False
                                            if _char_ip:
                                                try:
                                                    _cr2 = _m2.match_binary(_scr2, _char_ip, threshold=0.8)
                                                    time.sleep(0)  # GIL 해제
                                                    if _cr2.found:
                                                        _cpx2, _cpy2 = _cr2.center_x, _cr2.center_y
                                                        _char_det2 = True
                                                except Exception:
                                                    pass
                                            _dx2 = _r2.center_x - _cpx2
                                            _dy2 = _r2.center_y - _cpy2
                                            _edx2 = round(_dx2 / 44)
                                            _edy2 = round(_dy2 / 44)
                                            _td2 = abs(_edy2)  # X offset 무시 (UI NAME 고정)
                                            if _td2 > 20:
                                                # UI 오탐 — 재감지 무시
                                                _rechk_found = False
                                            elif _td2 <= 1 and _char_det2:
                                                # 추적 목표 근처에 도착 + 캐릭터 감지 확인 → approaching 진입
                                                _at_target = (abs(current_x - _move_target[0]) + abs(current_y - _move_target[1])) <= 2
                                                if _at_target:
                                                    _boss_chase_miss = 0
                                                    _boss_chasing = False
                                                    boss_mode = "approaching"
                                                    boss_appr_miss = 0
                                                    # X축 스캔 상태 초기화 (좌우 교대 탐색용)
                                                    self._appr_x_step = 0
                                                    self._appr_x_dir = 1  # 1=right, -1=left
                                                    self._appr_x_max = 2  # 한 방향 최대 스텝
                                                    if boss_skill_enabled and boss_skill_key:
                                                        try:
                                                            pyautogui.keyDown(boss_skill_key)
                                                            try:
                                                                time.sleep(0.05)
                                                            finally:
                                                                pyautogui.keyUp(boss_skill_key)
                                                            self._key_press_count += 1
                                                            last_boss_skill_time = time.time()
                                                        except Exception:
                                                            pass
                                                    self.after(0, lambda cx=current_x, cy=current_y, td=_td2, dy=_dy2:
                                                        self._append_log(f"🎯 추적→접근! ({cx},{cy}) 거리={td}타일 → approaching"))
                                                    prev_x, prev_y = current_x, current_y
                                                    time.sleep(0.05)
                                                    continue
                                                else:
                                                    # Y 근접이지만 보스가 X축으로 떨어져 있음 → 좌우 스캔
                                                    # X offset을 모르므로 좌/우 교대 탐색
                                                    if not hasattr(self, '_boss_x_scan_dir'):
                                                        self._boss_x_scan_dir = 1  # 초기: 오른쪽
                                                    _found_detour = False
                                                    _scan_dx = self._boss_x_scan_dir  # 현재 스캔 방향
                                                    _boss_dir_y = 1 if _edy2 >= 0 else -1
                                                    # 좌우 우선 탐색 (X축 스캔이 핵심)
                                                    _scan_start = self._game_map.start_pos
                                                    for _dr in range(1, 8):
                                                        for _ddx, _ddy in [(_scan_dx, 0), (-_scan_dx, 0),
                                                                           (_scan_dx, _boss_dir_y), (-_scan_dx, _boss_dir_y),
                                                                           (0, _boss_dir_y)]:
                                                            _dtx = current_x + _ddx * _dr
                                                            _dty = current_y + _ddy * (_dr if _ddy != 0 else 0)
                                                            # passable + 현재위치 아님 + start_pos(출발지) 아님
                                                            if (self._game_map.is_passable(_dtx, _dty)
                                                                    and (_dtx, _dty) != (current_x, current_y)
                                                                    and (_scan_start is None or (_dtx, _dty) != _scan_start)):
                                                                _chase_tx, _chase_ty = _dtx, _dty
                                                                _found_detour = True
                                                                break
                                                        if _found_detour:
                                                            break
                                                    if _found_detour:
                                                        _boss_chase_miss = 0
                                                        # 다음번엔 반대 방향 스캔
                                                        self._boss_x_scan_dir *= -1
                                                        self.after(0, lambda cx=current_x, cy=current_y,
                                                                   tx=_chase_tx, ty=_chase_ty, td=_td2, dy=_dy2:
                                                            self._append_log(f"🔄 Y근접({td}타일) X탐색 → ({tx},{ty})"))
                                                    else:
                                                        # 우회 불가 → 미감지 처리 (miss 누적 → 15회 시 순찰 복귀)
                                                        _rechk_found = False
                                                        self._boss_x_scan_dir *= -1  # 방향 전환
                                                        self.after(0, lambda cx=current_x, cy=current_y, td=_td2, dy=_dy2:
                                                            self._append_log(f"⚠️ Y근접({td}타일) X탐색 실패+우회불가"))
                                            else:
                                                _chase_tx = current_x  # X offset 무시 (UI NAME 고정)
                                                _chase_ty = current_y + _edy2
                                                # start_pos 밟지 않도록 확인
                                                _rchk_sp = self._game_map.start_pos
                                                if _rchk_sp is not None and (_chase_tx, _chase_ty) == _rchk_sp:
                                                    # start_pos면 Y방향으로 1타일 더 이동
                                                    _rchk_dy_sign = 1 if _edy2 >= 0 else -1
                                                    _chase_ty += _rchk_dy_sign
                                                _boss_chase_miss = 0
                                                self.after(0, lambda cx=current_x, cy=current_y,
                                                       tx=_chase_tx, ty=_chase_ty, td=_td2:
                                                self._append_log(f"🔄 재감지! ({cx},{cy})→({tx},{ty}) 거리={td}타일"))
                                except Exception:
                                    pass
                                if not _rechk_found and not self._stop_event.is_set():
                                    _boss_chase_miss += 1
                                    if _boss_chase_miss >= 15:
                                        _boss_chasing = False
                                        _boss_chase_miss = 0
                                        # 현재 위치에서 가장 가까운 순찰 포인트부터 재시작
                                        if boss_patrol is not None:
                                            boss_patrol.start((current_x, current_y))
                                        self.after(0, lambda cx=current_x, cy=current_y:
                                            self._append_log(f"🔍 보스 미감지 → 순찰 복귀 (({cx},{cy})부터)"))
                                    else:
                                        if _ui_update_ok:
                                            self.after(0, lambda tx=_move_target[0], ty=_move_target[1], m=_boss_chase_miss:
                                                self._append_log(f"⚠️ 추적 ({tx},{ty}) 재감지 실패 ({m}/15)"))
                                        self._stop_event.wait(0.05)
                            else:
                                boss_patrol.skip_current_target()
                                patrol_skip_count += 1
                                self.after(0, lambda tx=_move_target[0], ty=_move_target[1]:
                                    self._append_log(f"⚠️ 순찰 ({tx},{ty}) 도달 불가 → 스킵"))
                                patrol_total = len(self._game_map.patrol_points)
                                skip_threshold = min(patrol_total, 15)
                                if patrol_skip_count >= skip_threshold and patrol_total > 0:
                                    boss_mode = "exploring"
                                    boss_patrol = None
                                    _boss_chasing = False
                                    _boss_chase_miss = 0
                                    last_dir = None
                                    stuck_count = 0
                                    total_stuck_count = 0
                                    patrol_skip_count = 0
                                    explore_target = None
                                    explore_target_tries = 0
                                    explore_unreachable = set()
                                    frontier_cooldown.clear()
                                    explored_from = {}
                                    unknown_path_fails = 0
                                    boss_no_frontier_count = 0
                                    boss_approach_steps = 0
                                    boss_appr_miss = 0
                                    self.after(0, lambda ps=patrol_skip_count:
                                        self._append_log(f"🔄 순찰 {ps}개 연속 도달불가 → 탐색모드 복귀"))
                            current_path = []
                            path_index = 0
                            path_pos_index = {}
                            pathfinder.invalidate_path()
                            prev_x, prev_y = current_x, current_y
                            self._stop_event.wait(0.05)
                            continue

                        if not self._stop_event.is_set() and _ui_update_ok:
                            if _boss_chasing:
                                self.after(0, lambda cx=current_x, cy=current_y, d=direction,
                                           tx=_move_target[0], ty=_move_target[1]:
                                    self._update_status("보스추적", f"({cx},{cy})",
                                        f"→({tx},{ty})", f"{d}", "🎯"))
                            else:
                                progress = boss_patrol.get_progress()
                                self.after(0, lambda cx=current_x, cy=current_y, p=progress, d=direction,
                                           tx=_move_target[0], ty=_move_target[1]:
                                    self._update_status("순찰중", f"({cx},{cy})",
                                        f"→({tx},{ty})",
                                        f"{p['visited']}/{p['total']} → {d}", "🔍"))

                    if direction is None:
                        if not self._stop_event.is_set() and _ui_update_ok:
                            self.after(0, lambda cx=current_x, cy=current_y, m=boss_mode:
                                self._append_log(f"⏸ ({cx},{cy}) 보스던전 방향 없음 ({m})"))
                        # 방향 없음 연속 → 인접 벽 해제 복구 (exploring 모드 갇힘 방지)
                        stuck_count += 1
                        if stuck_count >= 10 and mapping_on and use_map:
                            _unblocked = 0
                            for _d, (_ddx, _ddy) in DIRECTIONS_4.items():
                                _nx, _ny = current_x + _ddx, current_y + _ddy
                                if (_nx, _ny) in _portal_protected:
                                    continue  # 포탈 좌표 보호
                                if self._game_map.is_blocked(_nx, _ny):
                                    self._game_map.mark_passable(_nx, _ny)
                                    _unblocked += 1
                            if _unblocked > 0:
                                self.after(0, lambda u=_unblocked, cx=current_x, cy=current_y:
                                    self._append_log(f"🔓 방향없음 복구 ({cx},{cy}) → {u}개 인접벽 해제"))
                            stuck_count = 0
                            total_stuck_count = 0
                            explored_from.pop((current_x, current_y), None)
                            current_path = []
                            path_index = 0
                            path_pos_index = {}
                        _t_iter_total = int((time.time() - _t_iter_start) * 1000)
                        _timing_msg = f"⏱ #{iteration} OCR:{_t_ocr_ms} sk:{_t_skill_ms} 총:{_t_iter_total}ms ({current_x},{current_y}) 방향없음[{boss_mode}]"
                        logger.info(f"[타이밍] {_timing_msg}")
                        if _ui_update_ok:
                            self.after(0, lambda m=_timing_msg: self._append_log(m))
                        self._stop_event.wait(0.1)
                        # prev를 현재 위치로 → 다음 반복에서 이동 판정 정상 동작
                        prev_x, prev_y = current_x, current_y
                        last_dir = None
                        continue

                    if self._stop_event.is_set():
                        break

                    # 보스던전 방향 로그 (10회마다)
                    if iteration % 10 == 0 and not self._stop_event.is_set():
                        self.after(0, lambda cx=current_x, cy=current_y, d=direction, m=boss_mode:
                            self._append_log(f"🎮 [{m}] ({cx},{cy}) → {d}"))

                    _t_path_ms = int((time.time() - _t_iter_start) * 1000) - _t_ocr_ms
                    _t_key_start = time.time()
                    press_key(direction)
                    _t_key_ms = int((time.time() - _t_key_start) * 1000)
                    _t_iter_total = int((time.time() - _t_iter_start) * 1000)
                    _timing_msg = f"⏱ #{iteration} OCR:{_t_ocr_ms} sk:{_t_skill_ms} A*:{_t_path_ms} key:{_t_key_ms} iv:{int(interval*1000)} 총:{_t_iter_total}ms ({current_x},{current_y})→{direction} [{boss_mode}]"
                    logger.info(f"[타이밍] {_timing_msg}")
                    if _ui_update_ok:
                        self.after(0, lambda m=_timing_msg: self._append_log(m))
                    last_dir = direction
                    prev_x, prev_y = current_x, current_y
                    continue
                # ── 보스 던전 핸들러 끝 ──

                # A* 경로탐색으로 방향 결정
                _t_path_start = time.time()
                direction = find_path_direction(current_x, current_y, target_x, target_y)
                _t_path_ms = int((time.time() - _t_path_start) * 1000)

                # A* 중 중지 요청 발생 시 즉시 루프 탈출
                if self._stop_event.is_set():
                    break

                if direction is None:
                    _t_iter_total = int((time.time() - _t_iter_start) * 1000)
                    _timing_msg = f"⏱ #{iteration} OCR:{_t_ocr_ms} A*:{_t_path_ms} 총:{_t_iter_total}ms ({current_x},{current_y}) 방향없음"
                    logger.info(f"[타이밍] {_timing_msg}")
                    if _ui_update_ok:
                        self.after(0, lambda m=_timing_msg: self._append_log(m))
                    if not self._stop_event.is_set() and _ui_update_ok:
                        self.after(0, lambda cx=current_x, cy=current_y:
                            self._append_log(f"⏸ ({cx},{cy}) 방향 없음"))
                    self._stop_event.wait(0.1)
                    prev_x, prev_y = current_x, current_y
                    continue

                # UI 업데이트
                # 도착좌표가 여러 개면 가장 가까운 도착좌표 기준으로 거리 계산
                _cur_re_dist = all_targets[target_idx][7] if len(all_targets[target_idx]) > 7 else []
                if _cur_re_dist:
                    dist = min(abs(ex - current_x) + abs(ey - current_y) for ex, ey in _cur_re_dist)
                else:
                    dist = abs(target_x - current_x) + abs(target_y - current_y)
                is_final = (target_idx == len(all_targets) - 1)
                slow_approach_dist = 2  # 모든 경유지 N칸 전부터 감속 (포탈 감지용)
                is_slow = dist <= slow_approach_dist

                if not self._stop_event.is_set() and _ui_update_ok:
                    symbols = {"up": "↑", "down": "↓", "left": "←", "right": "→"}
                    seg_name = all_targets[target_idx][2]
                    mode_text = "🐢 감속접근" if is_slow else "이동중"
                    progress = f"[{target_idx+1}/{len(all_targets)}]"
                    self.after(0, lambda cx=current_x, cy=current_y, d=dist, s=symbols.get(direction, direction), tx=target_x, ty=target_y, m=mode_text, p=progress:
                        self._update_status(m, f"({cx},{cy})", f"({tx},{ty})", f"{d}칸 {p}", s))

                # 경유지 근접 시 감속 (포탈 등 좌표 변경 대비)
                if is_slow:
                    if not self._stop_event.is_set() and _ui_update_ok:
                        self.after(0, lambda cx=current_x, cy=current_y, d=dist:
                            self._append_log(f"🐢 감속접근 ({cx},{cy}) 거리:{d}칸"))
                    self._stop_event.wait(0.05)

                if self._stop_event.is_set():
                    break

                # 방향키 입력 직전 도착 여부 재확인 (이동 중 도착 가능)
                if dist <= 2:
                    _t_recheck = time.time()
                    check_x, check_y = matcher.read_both_coordinates(
                        self._config.coord_x_region,
                        self._config.coord_y_region,
                        stop_event=self._stop_event
                    )
                    _t_recheck_ms = int((time.time()-_t_recheck)*1000)
                    logger.info(f"[타이밍] #{iteration} 도착재확인OCR: {_t_recheck_ms}ms")
                    if _ui_update_ok:
                        self.after(0, lambda m=_t_recheck_ms, i=iteration: self._append_log(f"⏱ #{i} 재확인OCR:{m}ms"))
                    if check_x is not None:
                        _cx, _cy = int(check_x), int(check_y)
                        # 도착좌표가 여러 개면 어느 하나라도 일치하면 도착
                        if _cur_re_dist:
                            _check_arrived = any(_cx == ex and _cy == ey for ex, ey in _cur_re_dist)
                        else:
                            _check_arrived = (abs(_cx - target_x) + abs(_cy - target_y) == 0)
                        if _check_arrived:
                            # 이미 도착! 방향키 누르지 않고 루프로
                            current_x, current_y = _cx, _cy
                            time.sleep(0.05)
                            continue

                # 이동 (포탈 근접 시 짧은 탭으로 오버슈트 방지)
                _t_key_start = time.time()
                if dist <= 1:
                    # 포탈 1칸 전: smooth_move 무시, 짧은 탭 1회만
                    key = self._config.move_keys.get(direction, direction)
                    _orig_pause = pyautogui.PAUSE
                    pyautogui.PAUSE = 0
                    try:
                        pyautogui.press(key)
                    finally:
                        pyautogui.PAUSE = _orig_pause
                    self._key_press_count += 1
                    if _ui_update_ok:
                        self.after(0, lambda: self._key_count_label.configure(text=f"{self._key_press_count}회"))
                    self._stop_event.wait(0.2)  # 포탈 전환 안정화 대기
                else:
                    press_key(direction)
                _t_key_ms = int((time.time() - _t_key_start) * 1000)
                last_dir = direction
                prev_x, prev_y = current_x, current_y

                if is_slow and dist > 1:
                    self._stop_event.wait(0.05)

                # 타이밍 로그 (매 반복 — UI + 파일)
                _t_iter_total = int((time.time() - _t_iter_start) * 1000)
                _timing_msg = f"⏱ #{iteration} OCR:{_t_ocr_ms} sk:{_t_skill_ms} A*:{_t_path_ms} key:{_t_key_ms} iv:{int(interval*1000)} 총:{_t_iter_total}ms ({current_x},{current_y})→{direction} d={dist}"
                logger.info(f"[타이밍] {_timing_msg}")
                if _ui_update_ok:
                    self.after(0, lambda m=_timing_msg: self._append_log(m))

        except Exception as e:
            logger.error(f"[좌표모드] 오류: {e}")
            self.after(0, lambda err=str(e): self._append_log(f"⚠️ 오류: {err}"))
        finally:
            # keyboard.remove_hotkey를 별도 스레드에서 실행 (join 없이 — 블로킹 방지)
            def _remove_hotkey():
                try:
                    keyboard.remove_hotkey(_escape_hotkey_id)
                except Exception:
                    pass
            threading.Thread(target=_remove_hotkey, daemon=True).start()

            try:
                self.after(0, self._stop_execution)
            except Exception:
                pass

    # pyautogui.PAUSE 전역 상태 보호용 락
    _pyautogui_lock = threading.Lock()

    def _smooth_key_input_ui(self, keys, interval):
        """부드러운 키 입력 (UI용)"""
        import pyautogui

        with self._pyautogui_lock:
            original_pause = pyautogui.PAUSE
            pyautogui.PAUSE = 0
            try:
                start_time = time.time()
                while time.time() - start_time < interval and not self._stop_event.is_set():
                    self._key_press_count += 1
                    for key in keys:
                        pyautogui.keyDown(key)
                    time.sleep(0.015)  # 15ms 누르기
                    for key in keys:
                        pyautogui.keyUp(key)
                    time.sleep(0.005)  # 5ms 간격
            finally:
                pyautogui.PAUSE = original_pause
        try:
            self.after(0, lambda: self._key_count_label.configure(text=f"{self._key_press_count}회"))
        except (tk.TclError, RuntimeError):
            pass

    def _update_status(self, status, char, target, dist, direction, dx=None, dy=None):
        self._status_label.configure(text=f"상태: {status}")
        self._char_pos_label.configure(text=char)
        self._target_pos_label.configure(text=target)
        self._distance_label.configure(text=dist)
        self._direction_label.configure(text=direction)
        self._key_count_label.configure(text=f"{self._key_press_count}회")

        # 로그에 상세 정보 추가
        if dx is not None and dy is not None:
            log_msg = f"[{status}] 캐릭터{char} → 목표{target} | dx={dx:+d} dy={dy:+d} | 거리={dist} | 방향={direction}"
            self._append_log(log_msg)

    _log_line_counter = 0

    def _append_log(self, message: str, force: bool = False):
        """로그 텍스트에 메시지 추가 (최대 500줄 제한)"""
        try:
            if not force and self._stop_event.is_set():
                return
            from datetime import datetime
            timestamp = datetime.now().strftime("%H:%M:%S")
            self._log_text.configure(state="normal")
            self._log_text.insert("end", f"[{timestamp}] {message}\n")
            # 줄 수 체크를 20회마다만 수행 (위젯 연산 절약)
            self._log_line_counter += 1
            if self._log_line_counter >= 20:
                self._log_line_counter = 0
                line_count = int(self._log_text.index("end-1c").split(".")[0])
                if line_count > 500:
                    self._log_text.delete("1.0", f"{line_count - 500}.0")
            self._log_text.see("end")
            self._log_text.configure(state="disabled")
        except Exception as e:
            logger.debug(f"무시된 예외: {e}")

    def _clear_log(self):
        """로그 초기화"""
        try:
            self._log_text.configure(state="normal")
            self._log_text.delete("1.0", "end")
            self._log_text.configure(state="disabled")
            self._key_press_count = 0
            self._key_count_label.configure(text="0회")
        except Exception as e:
            logger.debug(f"무시된 예외: {e}")

    def _show_notification(self, title: str, message: str, duration: int = 3000):
        """비블로킹 알림 (duration ms 후 자동 닫힘, 메인스레드 블로킹 없음)"""
        try:
            popup = ctk.CTkToplevel(self)
            popup.title(title)
            popup.geometry("350x150")
            popup.attributes("-topmost", True)
            popup.resizable(False, False)

            label = ctk.CTkLabel(popup, text=message, wraplength=300,
                                 font=ctk.CTkFont(size=14))
            label.pack(expand=True, padx=20, pady=20)

            close_btn = ctk.CTkButton(popup, text="확인", width=80,
                                       command=popup.destroy)
            close_btn.pack(pady=(0, 10))

            popup.after(duration, popup.destroy)
        except Exception:
            pass

    def _on_arrival(self):
        if not self._is_running:
            return  # 중복 호출 방지
        self._completed_normally = True  # 정상 도착 플래그
        self._is_running = False
        self._stop_event.set()

        # --- 일반 완료 처리 ---
        self._run_btn.configure(text="▶ 전체 테스트", fg_color="#a3be8c")
        if hasattr(self, '_mapping_test_btn'):
            self._mapping_test_btn.configure(text="▶ 전체맵핑테스트", fg_color="#5e81ac")
        self._status_label.configure(text="상태: 도달!", text_color="#a3be8c")
        self._append_log(f"🎯 목표 도달 완료! 총 키입력: {self._key_press_count}회")
        # 개별 테스트 카드 버튼 복원
        if getattr(self, '_single_waypoint_mode', False):
            single_idx = getattr(self, '_single_waypoint_idx', -1)
            if getattr(self, '_is_mapping_test', False) and single_idx >= 0:
                self._update_card_mapping_btn(single_idx, False)
            self._single_waypoint_mode = False
        self._is_mapping_test = False
        # 도착 시 맵 데이터 저장 (일반 테스트에서는 건너뜀)
        if not getattr(self, '_no_save_mode', False):
            if hasattr(self, '_game_map') and self._game_map:
                stats = self._game_map.get_statistics()
                if stats['total_tiles'] > 0:
                    threading.Thread(target=self._auto_save_map, daemon=True).start()
        self._show_notification("완료", f"목표 도달!\n총 키 입력 횟수: {self._key_press_count}회", duration=10000)

    def _build_coordinate_ui(self):
        """좌표 기반 모드 UI 구성"""
        # === 경유지 설정 (목표 좌표 통합) ===
        waypoint_frame = ctk.CTkFrame(self._coord_mode_frame, fg_color=COLORS["bg_card"], corner_radius=10)
        waypoint_frame.pack(fill="x", pady=(0, 8))
        waypoint_inner = ctk.CTkFrame(waypoint_frame, fg_color="transparent")
        waypoint_inner.pack(fill="x", padx=12, pady=10)

        ctk.CTkLabel(waypoint_inner, text="📍 경유지 목록",
                     font=ctk.CTkFont(size=12, weight="bold")).pack(anchor="w")
        ctk.CTkLabel(waypoint_inner, text="경유지를 순서대로 이동합니다.",
                     font=ctk.CTkFont(size=10), text_color=COLORS["text_secondary"]).pack(anchor="w", pady=(2, 8))

        # 경유지 추가 입력 (이름만)
        wp_add_row = ctk.CTkFrame(waypoint_inner, fg_color="transparent")
        wp_add_row.pack(fill="x", pady=(0, 6))
        self._wp_name_var = ctk.StringVar(value="")
        ctk.CTkEntry(wp_add_row, textvariable=self._wp_name_var, width=180, height=32,
                     placeholder_text="경유지 이름 (예: 1굴)",
                     font=ctk.CTkFont(size=12)).pack(side="left", padx=(0, 8))
        ctk.CTkButton(wp_add_row, text="+ 추가", width=70, height=32,
                      fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
                      font=ctk.CTkFont(size=12, weight="bold"),
                      command=self._add_waypoint).pack(side="left", padx=(0, 4))
        ctk.CTkButton(wp_add_row, text="📋 붙여넣기", width=90, height=32,
                      fg_color="#3b4252", hover_color="#5e81ac",
                      text_color="#88c0d0",
                      border_width=1, border_color="#5e81ac",
                      font=ctk.CTkFont(size=12),
                      command=self._paste_waypoint).pack(side="left", padx=(0, 8))
        self._run_btn = ctk.CTkButton(wp_add_row, text="▶ 전체 테스트", width=100, height=32,
                                       fg_color="#a3be8c", command=self._toggle_execution)
        self._run_btn.pack(side="left")
        self._mapping_test_btn = ctk.CTkButton(wp_add_row, text="▶ 전체맵핑테스트", width=120, height=32,
                                                fg_color="#5e81ac", command=self._toggle_mapping_test)
        self._mapping_test_btn.pack(side="left", padx=(4, 0))
        ctk.CTkButton(wp_add_row, text="전체 맵 초기화", width=100, height=32,
                      fg_color="#4c566a", hover_color="#bf616a",
                      font=ctk.CTkFont(size=12),
                      command=self._clear_all_maps).pack(side="left", padx=(4, 0))

        # 모두 접기/펼치기 버튼
        wp_collapse_row = ctk.CTkFrame(waypoint_inner, fg_color="transparent")
        wp_collapse_row.pack(fill="x", pady=(0, 4))
        self._wp_collapse_all_btn = ctk.CTkButton(wp_collapse_row, text="모두 펼치기", width=90, height=26,
                      font=ctk.CTkFont(size=11),
                      fg_color=COLORS["bg_card_hover"], hover_color=COLORS["bg_card"],
                      text_color=COLORS["text_secondary"],
                      command=self._toggle_all_cards)
        self._wp_collapse_all_btn.pack(side="left")
        self._wp_all_collapsed = True  # 기본 접힌 상태

        # 경유지 리스트 표시
        self._waypoint_list_frame = ctk.CTkFrame(waypoint_inner, fg_color="transparent")
        self._waypoint_list_frame.pack(fill="x", pady=(4, 0))
        self._waypoint_labels = []
        self._refresh_waypoint_list()

        # 저장 버튼
        self._save_btn = ctk.CTkButton(waypoint_inner, text="💾 설정 저장", height=36,
                                        fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
                                        font=ctk.CTkFont(size=12, weight="bold"),
                                        command=self._save_and_feedback)
        self._save_btn.pack(fill="x", pady=(8, 0))

        # === OCR 영역 설정 ===
        ocr_frame = ctk.CTkFrame(self._coord_mode_frame, fg_color=COLORS["bg_card"], corner_radius=10)
        ocr_frame.pack(fill="x", pady=(0, 8))
        ocr_inner = ctk.CTkFrame(ocr_frame, fg_color="transparent")
        ocr_inner.pack(fill="x", padx=12, pady=10)

        ctk.CTkLabel(ocr_inner, text="📍 좌표 읽기 영역",
                     font=ctk.CTkFont(size=12, weight="bold")).pack(anchor="w")
        ctk.CTkLabel(ocr_inner, text="화면에서 X, Y 좌표가 표시되는 영역을 각각 선택하세요",
                     font=ctk.CTkFont(size=10), text_color=COLORS["text_secondary"]).pack(anchor="w", pady=(2, 8))

        # X 좌표 영역
        x_row = ctk.CTkFrame(ocr_inner, fg_color="transparent")
        x_row.pack(fill="x", pady=(0, 6))
        ctk.CTkLabel(x_row, text="X 좌표 영역:", width=85).pack(side="left")
        self._coord_x_label = ctk.CTkLabel(x_row, text=self._format_coord_region(getattr(self._config, 'coord_x_region', None)),
                                           width=180, anchor="w")
        self._coord_x_label.pack(side="left", padx=5)
        ctk.CTkButton(x_row, text="영역 선택", width=70, height=26, fg_color="#5e81ac",
                      command=lambda: self._select_coord_region("x")).pack(side="left", padx=2)
        ctk.CTkButton(x_row, text="테스트", width=50, height=26, fg_color="#a3be8c",
                      command=lambda: self._test_coord_region("x")).pack(side="left", padx=2)
        self._coord_x_result = ctk.CTkLabel(x_row, text="", font=ctk.CTkFont(size=10), width=80)
        self._coord_x_result.pack(side="left", padx=5)

        # Y 좌표 영역
        y_row = ctk.CTkFrame(ocr_inner, fg_color="transparent")
        y_row.pack(fill="x", pady=(0, 0))
        ctk.CTkLabel(y_row, text="Y 좌표 영역:", width=85).pack(side="left")
        self._coord_y_label = ctk.CTkLabel(y_row, text=self._format_coord_region(getattr(self._config, 'coord_y_region', None)),
                                           width=180, anchor="w")
        self._coord_y_label.pack(side="left", padx=5)
        ctk.CTkButton(y_row, text="영역 선택", width=70, height=26, fg_color="#5e81ac",
                      command=lambda: self._select_coord_region("y")).pack(side="left", padx=2)
        ctk.CTkButton(y_row, text="테스트", width=50, height=26, fg_color="#a3be8c",
                      command=lambda: self._test_coord_region("y")).pack(side="left", padx=2)
        self._coord_y_result = ctk.CTkLabel(y_row, text="", font=ctk.CTkFont(size=10), width=80)
        self._coord_y_result.pack(side="left", padx=5)

        # === 실시간 좌표 모니터링 ===
        realtime_frame = ctk.CTkFrame(self._coord_mode_frame, fg_color=COLORS["bg_card"], corner_radius=10)
        realtime_frame.pack(fill="x", pady=(0, 8))
        realtime_inner = ctk.CTkFrame(realtime_frame, fg_color="transparent")
        realtime_inner.pack(fill="x", padx=12, pady=10)

        ctk.CTkLabel(realtime_inner, text="📡 실시간 좌표",
                     font=ctk.CTkFont(size=12, weight="bold")).pack(anchor="w")

        coord_display = ctk.CTkFrame(realtime_inner, fg_color="transparent")
        coord_display.pack(fill="x", pady=(8, 0))

        # 현재 좌표 표시 (큰 글씨)
        self._realtime_x_label = ctk.CTkLabel(coord_display, text="X: ---",
                                               font=ctk.CTkFont(size=20, weight="bold"), width=120)
        self._realtime_x_label.pack(side="left", padx=(0, 20))
        self._realtime_y_label = ctk.CTkLabel(coord_display, text="Y: ---",
                                               font=ctk.CTkFont(size=20, weight="bold"), width=120)
        self._realtime_y_label.pack(side="left", padx=(0, 20))

        # 모니터링 시작/중지 버튼
        self._monitoring = False
        self._monitor_btn = ctk.CTkButton(coord_display, text="▶ 모니터링 시작", width=120, height=32,
                                          fg_color="#5e81ac", command=self._toggle_coordinate_monitor)
        self._monitor_btn.pack(side="left", padx=10)

        # 인식 상태 (디버깅용)
        self._ocr_raw_label = ctk.CTkLabel(realtime_inner, text="인식 상태: -",
                                            font=ctk.CTkFont(size=10), text_color=COLORS["text_secondary"])
        self._ocr_raw_label.pack(anchor="w", pady=(5, 0))

        # === 이미지 테스트 ===
        imgtest_frame = ctk.CTkFrame(self._coord_mode_frame, fg_color=COLORS["bg_card"], corner_radius=10)
        imgtest_frame.pack(fill="x", pady=(0, 8))
        imgtest_inner = ctk.CTkFrame(imgtest_frame, fg_color="transparent")
        imgtest_inner.pack(fill="x", padx=12, pady=10)

        ctk.CTkLabel(imgtest_inner, text="🔍 이미지 테스트",
                     font=ctk.CTkFont(size=12, weight="bold")).pack(anchor="w")
        ctk.CTkLabel(imgtest_inner, text="이진화 매칭으로 배경 무관 이미지 검색",
                     font=ctk.CTkFont(size=10), text_color=COLORS["text_secondary"]).pack(anchor="w")

        imgtest_row = ctk.CTkFrame(imgtest_inner, fg_color="transparent")
        imgtest_row.pack(fill="x", pady=(8, 0))

        self._imgtest_path_label = ctk.CTkLabel(imgtest_row, text="이미지: 없음",
                                                 font=ctk.CTkFont(size=11), width=200, anchor="w")
        self._imgtest_path_label.pack(side="left")

        ctk.CTkButton(imgtest_row, text="선택", width=50, height=28,
                      font=ctk.CTkFont(size=11),
                      fg_color="#3a5a3a", hover_color="#4a7a4a",
                      command=self._imgtest_select).pack(side="left", padx=(5, 0))

        ctk.CTkButton(imgtest_row, text="테스트", width=60, height=28,
                      font=ctk.CTkFont(size=11),
                      fg_color="#5e81ac", hover_color="#6e91bc",
                      command=self._imgtest_run).pack(side="left", padx=(5, 0))

        # 결과 + 이진화 미리보기 (한 줄)
        imgtest_result_row = ctk.CTkFrame(imgtest_inner, fg_color="transparent")
        imgtest_result_row.pack(fill="x", pady=(5, 0))

        self._imgtest_result_label = ctk.CTkLabel(imgtest_result_row, text="결과: -",
                                                    font=ctk.CTkFont(size=11), text_color=COLORS["text_secondary"])
        self._imgtest_result_label.pack(side="left")

        # 이진화 미리보기 이미지 라벨 (테스트 시 표시)
        self._imgtest_preview_label = ctk.CTkLabel(imgtest_result_row, text="")
        self._imgtest_preview_label.pack(side="left", padx=(10, 0))
        self._imgtest_preview_image = None  # CTkImage 참조 유지

        self._imgtest_image_path = None

        # === 숫자 템플릿 설정 (OCR 대신 사용) ===
        template_frame = ctk.CTkFrame(self._coord_mode_frame, fg_color=COLORS["bg_card"], corner_radius=10)
        template_frame.pack(fill="x", pady=(0, 8))
        template_inner = ctk.CTkFrame(template_frame, fg_color="transparent")
        template_inner.pack(fill="x", padx=12, pady=10)

        ctk.CTkLabel(template_inner, text="🔢 숫자 템플릿 (정확도 향상)",
                     font=ctk.CTkFont(size=12, weight="bold")).pack(anchor="w")
        ctk.CTkLabel(template_inner, text="게임 폰트의 숫자 0~9를 캡처하면 인식률이 높아집니다",
                     font=ctk.CTkFont(size=10), text_color=COLORS["text_secondary"]).pack(anchor="w", pady=(2, 8))

        # 템플릿 상태 표시
        self._template_status_label = ctk.CTkLabel(template_inner, text="템플릿: 확인 중...",
                                                    font=ctk.CTkFont(size=11))
        self._template_status_label.pack(anchor="w", pady=(0, 5))

        # 숫자 버튼들 (0~9)
        digit_row = ctk.CTkFrame(template_inner, fg_color="transparent")
        digit_row.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(digit_row, text="캡처:", width=40).pack(side="left")
        self._digit_buttons = {}
        for i in range(10):
            btn = ctk.CTkButton(digit_row, text=str(i), width=30, height=28,
                               fg_color=COLORS["bg_card_hover"],
                               command=lambda d=str(i): self._capture_digit_template(d))
            btn.pack(side="left", padx=2)
            self._digit_buttons[str(i)] = btn

        # 저장/불러오기 버튼
        btn_row = ctk.CTkFrame(template_inner, fg_color="transparent")
        btn_row.pack(fill="x")
        ctk.CTkButton(btn_row, text="템플릿 저장", width=90, height=28,
                      fg_color="#a3be8c", command=self._save_digit_templates).pack(side="left", padx=(0, 5))
        ctk.CTkButton(btn_row, text="상태 새로고침", width=90, height=28,
                      fg_color=COLORS["bg_card_hover"], command=self._refresh_template_status).pack(side="left")

        # 초기 상태 확인
        self.after(100, self._refresh_template_status)

        # 맵핑 시스템 초기화
        self._init_mapping_system()

    def _init_mapping_system(self):
        """맵핑 시스템 초기화"""
        import os
        from ..player.game_map import GameMap
        from ..player.simple_pathfinder import SimplePathfinder
        from ..player.map_explorer import MapExplorer

        self._current_segment_idx = 0
        _seg0_name = self._get_segment_display_name(0)
        self._game_map = GameMap(name=f"{self._config.name or 'autosave'}_{_seg0_name}")
        self._map_pathfinder = SimplePathfinder(self._game_map)
        self._map_explorer = MapExplorer(self._game_map)
        self._is_mapping = False
        self._mapping_mode = "path"  # 'path' (경로맵핑) 또는 'full' (전체맵핑)

        # 첫 번째 경유지 맵 로드 시도
        map_path = self._get_segment_map_name(0)
        seg_name = self._get_segment_display_name(0)
        if os.path.exists(map_path):
            self._game_map.load_and_merge(map_path)
            stats = self._game_map.get_statistics()
            logger.info(f"[맵핑] '{seg_name}' 맵 로드: {map_path} ({stats['total_tiles']}개 타일)")
            self._update_mapping_status()
        else:
            # 하위호환: 기존 단일 맵 파일 또는 '시작' 맵이 있으면 사용
            map_name = self._config.name or "autosave"
            map_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "maps")
            for fallback in [f"{map_name}_시작_map.json", f"{map_name}_map.json"]:
                old_map_path = os.path.join(map_dir, fallback)
                if os.path.exists(old_map_path):
                    self._game_map.load_and_merge(old_map_path)
                    stats = self._game_map.get_statistics()
                    logger.info(f"[맵핑] 호환 맵 로드: {old_map_path} ({stats['total_tiles']}개 타일)")
                    self._update_mapping_status()
                    break

    def _toggle_mapping(self):
        """맵핑 시작/중지 토글"""
        if not self._is_mapping:
            self._start_mapping()
        else:
            self._stop_mapping()

    def _start_mapping(self):
        """맵핑 시작 - 좌표 기반 모드로 목표까지 이동하면서 맵 기록"""
        from tkinter import messagebox

        # 좌표 모드 설정 확인
        if not hasattr(self._config, 'coord_x_region') or not self._config.coord_x_region:
            messagebox.showerror("오류", "X 좌표 영역을 먼저 설정하세요")
            return
        if not hasattr(self._config, 'coord_y_region') or not self._config.coord_y_region:
            messagebox.showerror("오류", "Y 좌표 영역을 먼저 설정하세요")
            return

        # 경유지 확인 (최소 1개 필요)
        waypoints = getattr(self._config, 'waypoints', []) or []
        if not waypoints:
            messagebox.showerror("오류", "경유지를 최소 1개 추가하세요")
            return
        # 현재 구간의 경유지 좌표 확인
        seg_idx = getattr(self, '_current_segment_idx', 0)
        if seg_idx < len(waypoints):
            wp = waypoints[seg_idx]
            target_x, target_y = int(wp[0]), int(wp[1])
        elif seg_idx == 0 and waypoints:
            target_x, target_y = int(waypoints[0][0]), int(waypoints[0][1])
        else:
            target_x, target_y = 0, 0

        # 설정 적용
        self._apply_settings()

        # 좌표 모드 강제 설정
        self._config.navigation_mode = 'coordinate'
        self._config.mapping_enabled = True

        # 실행 상태 설정
        self._is_mapping = True
        self._is_running = True
        self._stop_event.clear()
        self._key_press_count = 0

        self._run_btn.configure(text="■ 중지", fg_color="#bf616a")
        mapping_mode = getattr(self, '_mapping_mode', 'path')
        mode_label = "경로맵핑" if mapping_mode == "path" else "전체맵핑"
        self._status_label.configure(text=f"상태: {mode_label}중", text_color="#a3be8c")

        # 기존 맵 정보 표시
        stats = self._game_map.get_statistics()
        logger.info(f"[맵핑] {mode_label} 시작 - 목표: ({target_x}, {target_y}), 기존 맵: {stats['total_tiles']}개 타일")

        # 좌표 기반 루프 실행
        threading.Thread(target=self._run_mapping_loop, daemon=True).start()

    def _run_mapping_loop(self):
        """맵핑 루프 - 좌표 기반 이동하면서 맵 기록"""
        try:
            # 구간 전환 + 백업을 워커 스레드에서 수행 (메인 스레드 블로킹 방지)
            target_idx = getattr(self, '_mapping_target_idx', None)
            if target_idx is not None:
                if target_idx != getattr(self, '_current_segment_idx', -1):
                    self._switch_segment_map(target_idx)
                else:
                    self._current_segment_idx = target_idx
                self._mapping_target_idx = None
            self._backup_map()
            if self._stop_event.is_set():
                return
            self._run_coordinate_loop()
        finally:
            # 수동 중지가 아닐 때만 완료 처리
            if self._is_mapping:
                self.after(0, self._on_mapping_complete)

    def _on_mapping_complete(self):
        """맵핑 완료 처리 (목표 도달 시)"""
        if not self._is_mapping:
            return  # 이미 중지됨

        seg_idx = getattr(self, '_current_segment_idx', 0)
        self._is_mapping = False

        # 버튼 상태만 configure
        self.after(0, lambda: self._update_card_mapping_btn(seg_idx, False))
        self.after(0, lambda: self._update_card_map_badge(seg_idx))

        # 맵 자동 저장 (별도 스레드)
        threading.Thread(target=self._auto_save_map, daemon=True).start()

        stats = self._game_map.get_statistics()
        logger.info(f"[맵핑] 맵핑 완료 - {stats['total_tiles']}개 타일")

    def _is_segment_map_locked(self, segment_idx: int) -> bool:
        """해당 구간의 맵 잠금 상태 확인"""
        waypoints = getattr(self._config, 'waypoints', []) or []
        if 0 <= segment_idx < len(waypoints):
            wp = waypoints[segment_idx]
            if len(wp) >= 4 and isinstance(wp[3], dict):
                return bool(wp[3].get('map_locked', False))
        return False

    def _auto_save_map(self) -> str:
        """맵 자동 저장 (스레드 안전, 메모리 데이터를 직접 저장, 롤링 백업 포함)"""
        # 보스 경유지 중에는 맵 저장 차단 (오염 방지)
        if getattr(self, '_boss_segment_active', False):
            try:
                self.after(0, lambda: self._append_log("⚠️ 맵 저장 건너뜀: 보스 경유지 활성"))
            except (tk.TclError, RuntimeError):
                pass
            return ""
        # 맵 잠금 구간에서는 저장 차단
        if self._is_segment_map_locked(getattr(self, '_current_segment_idx', 0)):
            return ""
        # 빈 맵 저장 방지 (구간 전환 직후 기존 파일 덮어쓰기 방지)
        if not self._game_map.passable:
            try:
                self.after(0, lambda: self._append_log("⚠️ 맵 저장 건너뜀: passable 비어있음"))
            except (tk.TclError, RuntimeError):
                pass
            return ""
        import os, shutil
        acquired = self._map_save_lock.acquire(timeout=0.5)
        if not acquired:
            try:
                self.after(0, lambda: self._append_log("⚠️ 맵 저장 건너뜀: 락 획득 실패"))
            except (tk.TclError, RuntimeError):
                pass
            logger.warning("[맵핑] 맵 저장 락 획득 실패 (0.5초 타임아웃)")
            return ""
        if not self._game_map.passable:
            self._map_save_lock.release()
            return ""
        try:
            segment_idx = getattr(self, '_current_segment_idx', 0)
            seg_name = self._get_segment_display_name(segment_idx)
            map_path = self._get_segment_map_name(segment_idx)
            from pathlib import Path
            Path(map_path).parent.mkdir(parents=True, exist_ok=True)
            # 롤링 백업: 기존 파일 → .bak1 → .bak2 → .bak3 (최대 3개 유지)
            if os.path.exists(map_path):
                try:
                    bak3 = map_path + ".bak3"
                    bak2 = map_path + ".bak2"
                    bak1 = map_path + ".bak1"
                    if os.path.exists(bak3):
                        os.remove(bak3)
                    if os.path.exists(bak2):
                        shutil.move(bak2, bak3)
                    if os.path.exists(bak1):
                        shutil.move(bak1, bak2)
                    shutil.copy2(map_path, bak1)
                except Exception as _bk_e:
                    logger.debug(f"[맵핑] 롤링 백업 실패 (무시): {_bk_e}")
            _p_count = len(self._game_map.passable)
            self._game_map.save(map_path)
            logger.info(f"[맵핑] '{seg_name}' 맵 저장: {map_path}")
            try:
                self.after(0, lambda sn=seg_name, pc=_p_count, mp=map_path:
                    self._append_log(f"💾 맵 저장: '{sn}' ({pc}타일) → {mp}"))
            except (tk.TclError, RuntimeError):
                pass
            return map_path
        finally:
            self._map_save_lock.release()

    def _backup_map(self):
        """맵핑 전 백업 저장 (현재 선택된 구간 맵)"""
        import os
        import shutil
        segment_idx = getattr(self, '_current_segment_idx', 0)
        seg_name = self._get_segment_display_name(segment_idx)
        map_path = self._get_segment_map_name(segment_idx)
        backup_path = map_path.replace("_map.json", "_map.backup.json")

        if os.path.exists(map_path):
            shutil.copy2(map_path, backup_path)
            stats = self._game_map.get_statistics()
            logger.info(f"[맵핑] '{seg_name}' 백업 저장: {backup_path} ({stats['total_tiles']}개 타일)")
            self._has_map_backup = True
        else:
            self._has_map_backup = False

    def _restore_map(self):
        """맵 백업에서 복원 (현재 선택된 구간 맵)"""
        import os
        from tkinter import messagebox

        segment_idx = getattr(self, '_current_segment_idx', 0)
        seg_name = self._get_segment_display_name(segment_idx)
        map_path = self._get_segment_map_name(segment_idx)
        backup_path = map_path.replace("_map.json", "_map.backup.json")

        if not os.path.exists(backup_path):
            messagebox.showwarning("되돌리기", f"'{seg_name}' 맵의 백업 파일이 없습니다.\n맵핑을 한 번도 하지 않았거나 백업이 삭제되었습니다.")
            return

        if not messagebox.askyesno("되돌리기", f"'{seg_name}' 맵을 맵핑 전 상태로 되돌리시겠습니까?\n현재 맵 데이터가 백업으로 대체됩니다."):
            return

        # 복원
        from ..player.game_map import GameMap
        from ..player.simple_pathfinder import SimplePathfinder
        from ..player.map_explorer import MapExplorer
        self._game_map = GameMap(name=self._config.name or "autosave")
        if self._game_map.load(backup_path):
            self._map_pathfinder = SimplePathfinder(self._game_map)
            self._map_explorer = MapExplorer(self._game_map)
            self._game_map.save(map_path)

            stats = self._game_map.get_statistics()
            self._update_mapping_status()
            messagebox.showinfo("되돌리기", f"'{seg_name}' 복원 완료!\n이동가능: {stats['passable_tiles']}개\n벽: {stats['blocked_tiles']}개\n임시벽: {stats.get('soft_blocked_tiles', 0)}개")
            logger.info(f"[맵핑] '{seg_name}' 백업에서 복원: {stats['total_tiles']}개 타일")
        else:
            messagebox.showerror("되돌리기", "백업 파일 로드 실패")

    def _switch_segment_map(self, new_segment_idx: int, skip_save: bool = False) -> bool:
        """구간 맵 전환 (현재 맵 저장 → 새 구간 맵 로드 → pathfinder 갱신). 성공 시 True."""
        import os
        from ..player.game_map import GameMap
        from ..player.simple_pathfinder import SimplePathfinder
        from ..player.map_explorer import MapExplorer

        # 중지 요청 시 맵 전환 생략 (lock 대기 방지)
        if self._stop_event.is_set():
            return False

        # 맵 전환은 save lock 안에서 수행 (백그라운드 auto-save와 경합 방지)
        acquired = self._map_save_lock.acquire(timeout=1.0)
        if not acquired:
            logger.error("[맵핑] 맵 전환 락 획득 실패 (1초 타임아웃)")
            return False
        try:
            # 현재 맵 저장 (skip_save=True면 건너뜀 — 보스 경유지 등 오염 방지)
            # 맵 잠금 구간도 저장 건너뜀
            if not skip_save and not self._is_segment_map_locked(getattr(self, '_current_segment_idx', 0)):
                try:
                    import shutil as _shutil_sw
                    segment_idx = getattr(self, '_current_segment_idx', 0)
                    map_path = self._get_segment_map_name(segment_idx)
                    from pathlib import Path
                    Path(map_path).parent.mkdir(parents=True, exist_ok=True)
                    # 롤링 백업
                    if os.path.exists(map_path):
                        try:
                            for _bi in range(3, 1, -1):
                                _src = map_path + f".bak{_bi-1}"
                                _dst = map_path + f".bak{_bi}"
                                if os.path.exists(_src):
                                    if os.path.exists(_dst):
                                        os.remove(_dst)
                                    _shutil_sw.move(_src, _dst)
                            _shutil_sw.copy2(map_path, map_path + ".bak1")
                        except Exception:
                            pass
                    self._game_map.save(map_path)
                    logger.info(f"[맵핑] '{self._get_segment_display_name(segment_idx)}' 맵 저장: {map_path}")
                except Exception as e:
                    logger.error(f"[맵핑] 현재 맵 저장 실패: {e}")
            else:
                logger.info(f"[맵핑] 보스 경유지 → 맵 저장 건너뜀 (오염 방지)")

            # 새 구간으로 전환
            old_name = self._get_segment_display_name(getattr(self, '_current_segment_idx', 0))
            self._current_segment_idx = new_segment_idx
            self._start_registered = False  # 새 구간에서 start_pos 재등록 필요
            new_name = self._get_segment_display_name(new_segment_idx)
            self._game_map = GameMap(name=f"{self._config.name or 'autosave'}_{new_name}")
            self._map_pathfinder = SimplePathfinder(self._game_map)
            self._map_explorer = MapExplorer(self._game_map)

            # 새 구간 맵 로드 (lock 안에서 수행 — auto_save가 빈 맵을 덮어쓰는 경합 방지)
            map_path = self._get_segment_map_name(new_segment_idx)
            if os.path.exists(map_path):
                self._game_map.load_and_merge(map_path)
                stats = self._game_map.get_statistics()
                logger.info(f"[맵핑] '{old_name}'→'{new_name}' 전환, 맵 로드: {stats['total_tiles']}개 타일")
            else:
                logger.info(f"[맵핑] '{old_name}'→'{new_name}' 전환, 새 맵 생성")
            return True
        finally:
            self._map_save_lock.release()

    def _stop_mapping(self):
        """맵핑 중지 - 즉시 반환, 절대 블로킹 없음"""
        # 1. 플래그만 설정하고 즉시 반환
        self._stop_event.set()
        self._is_mapping = False
        self._is_running = False

        # 2. UI는 after로 (메인스레드 안전)
        self.after(1, lambda: self._run_btn.configure(text="▶ 전체 테스트", fg_color="#a3be8c"))
        self.after(1, lambda: self._status_label.configure(text="상태: 중지됨", text_color="#d08770"))

        # 3. 저장은 별도 스레드
        def do_save():
            try:
                if hasattr(self, '_game_map') and self._game_map.passable:
                    self._auto_save_map()
            except Exception as e:
                logger.debug(f"무시된 예외: {e}")
            # 배지 갱신: 파일 I/O를 여기서 수행 (백그라운드), UI 업데이트만 메인스레드로
            self._refresh_badges_async()
        threading.Thread(target=do_save, daemon=True).start()

    def _update_mapping_status(self):
        """맵 상태 업데이트"""
        pass  # 카드 배지는 _update_card_map_badge()로 개별 업데이트

    def _show_map_preview(self, game_map=None, seg_idx=None):
        """맵 미리보기 - 그래픽 맵"""
        from tkinter import messagebox

        if game_map is None:
            game_map = self._game_map

        stats = game_map.get_statistics()
        self._append_log(f"🗺️ 맵 열기: 이동={stats['passable_tiles']}, 벽={stats['blocked_tiles']}, 임시벽={stats['soft_blocked_tiles']}")

        try:
            from ..player.map_canvas import MapWindow
        except Exception as e:
            self._append_log(f"❌ 맵 모듈 로드 실패: {e}")
            messagebox.showerror("오류", f"맵 모듈 로드 실패:\n{e}")
            return

        # 경유지 좌표 (출발지 = 이전 경유지, 도착지 = 현재 경유지)
        target_pos = None
        start_pos = None
        try:
            if seg_idx is None:
                seg_idx = getattr(self, '_current_segment_idx', 0)
            waypoints = getattr(self._config, 'waypoints', []) or []
            # 도착지: 현재 구간의 경유지 좌표
            if seg_idx < len(waypoints):
                wp = waypoints[seg_idx]
                if len(wp) >= 2 and (int(wp[0]) != 0 or int(wp[1]) != 0):
                    target_pos = (int(wp[0]), int(wp[1]))
            # 출발지: 이전 구간의 경유지 좌표 (현재 맵에 존재할 때만 표시)
            # 포탈 이동 시 좌표계가 달라지므로: 현재 경유지가 0,0(보스)이면 표시 안함
            if seg_idx > 0 and seg_idx - 1 < len(waypoints) and seg_idx < len(waypoints):
                cur_wp = waypoints[seg_idx]
                is_boss = len(cur_wp) >= 2 and int(cur_wp[0]) == 0 and int(cur_wp[1]) == 0
                if not is_boss:
                    prev_wp = waypoints[seg_idx - 1]
                    if len(prev_wp) >= 2 and (int(prev_wp[0]) != 0 or int(prev_wp[1]) != 0):
                        sp = (int(prev_wp[0]), int(prev_wp[1]))
                        if game_map.is_known(sp[0], sp[1]):
                            start_pos = sp
        except Exception as e:
            logger.debug(f"무시된 예외: {e}")

        # 경로 좌표 추출 (route_starts/route_ends에서 박스 리스트)
        route_starts = []
        route_ends = []
        route_walls = []
        try:
            if seg_idx is not None and seg_idx < len(waypoints):
                wp = waypoints[seg_idx]
                if len(wp) >= 4 and isinstance(wp[3], dict):
                    for s in wp[3].get("route_starts", []):
                        sx, sy = s.get("x"), s.get("y")
                        if sx is not None and sy is not None:
                            route_starts.append((int(sx), int(sy)))
                    for e in wp[3].get("route_ends", []):
                        ex, ey = e.get("x"), e.get("y")
                        if ex is not None and ey is not None and (int(ex) != 0 or int(ey) != 0):
                            if e.get("enabled", True):
                                route_ends.append((int(ex), int(ey)))
                            else:
                                route_walls.append((int(ex), int(ey)))
                    for w in wp[3].get("route_walls", []):
                        wx, wy = w.get("x"), w.get("y")
                        if wx is not None and wy is not None and (int(wx) != 0 or int(wy) != 0):
                            route_walls.append((int(wx), int(wy)))
        except Exception:
            pass

        # 사용자 지정 좌표가 있으면 기존 자동 출발/도착 제거
        if route_starts:
            start_pos = None
        if route_ends:
            target_pos = None

        # 그래픽 맵 창 열기
        try:
            # 항상 경유지 표시 이름 사용 (파일 내부 이름 대신)
            map_name = self._get_segment_display_name(seg_idx) if seg_idx is not None else getattr(game_map, 'name', '맵')
            # 벽 편집 시 자동 저장 콜백
            _seg = seg_idx if seg_idx is not None else getattr(self, '_current_segment_idx', 0)
            _map_ref = game_map
            def _save_edited_map():
                try:
                    _path = self._get_segment_map_name(_seg)
                    _map_ref.save(_path)
                except Exception as _e:
                    logger.error(f"[맵핑] 벽 편집 저장 실패: {_e}")
            # 초기화 콜백: 잠금맵이 아닐 때만 제공
            _clear_cb = None
            if not self._is_segment_map_locked(_seg):
                def _clear_map(_s=_seg, _mr=_map_ref):
                    import os as _os
                    from ..player.game_map import GameMap as _GM
                    from ..player.simple_pathfinder import SimplePathfinder as _SP
                    from ..player.map_explorer import MapExplorer as _ME
                    _sn = self._get_segment_display_name(_s)
                    # map_file 공유 경로 먼저 해제 (원본 파일 보호)
                    _wps = getattr(self._config, 'waypoints', []) or []
                    if _s < len(_wps):
                        _wp = _wps[_s]
                        if isinstance(_wp, (list, tuple)) and len(_wp) >= 4 and isinstance(_wp[3], dict):
                            _wp[3].pop('map_file', None)
                    # map_file 해제 후 호출 → 경유지 자체 경로 반환 (원본 아님)
                    _mp = self._get_segment_map_name(_s)
                    if _os.path.exists(_mp):
                        _os.remove(_mp)
                    _bk = _mp.replace("_map.json", "_map.backup.json")
                    if _os.path.exists(_bk):
                        _os.remove(_bk)
                    new_map = _GM(name=f"{self._config.name or 'autosave'}_{_sn}")
                    if _s == getattr(self, '_current_segment_idx', -1):
                        self._game_map = new_map
                        self._map_pathfinder = _SP(new_map)
                        self._map_explorer = _ME(new_map)
                    self._update_card_map_badge(_s)
                    self._append_log(f"🗑️ '{_sn}' 맵 초기화 완료", force=True)
                    logger.info(f"[맵핑] '{_sn}' 맵 초기화 완료")
                _clear_cb = _clear_map
            map_window = MapWindow(self, game_map, title=map_name,
                                  restore_callback=self._restore_map,
                                  save_callback=_save_edited_map,
                                  clear_callback=_clear_cb)
            map_window.update_positions(target=target_pos, start=start_pos,
                                        route_starts=route_starts, route_ends=route_ends,
                                        route_walls=route_walls)
            self._append_log(f"🗺️ 맵 창 열림 ({map_name})")
        except Exception as e:
            self._append_log(f"❌ 맵 창 열기 실패: {e}")
            logger.error(f"[맵핑] 맵 미리보기 창 열기 실패: {e}")
            messagebox.showerror("맵 미리보기 오류", f"맵 창을 열 수 없습니다:\n{e}")

    def _show_map_statistics(self):
        """맵 통계 표시"""
        stats = self._game_map.get_statistics()
        from tkinter import messagebox
        msg = f"""맵 이름: {stats['name']}
총 타일: {stats['total_tiles']}
이동 가능: {stats['passable_tiles']}
장애물: {stats['blocked_tiles']}
임시벽: {stats.get('soft_blocked_tiles', 0)}"""

        bounds = stats['bounds']
        if stats['total_tiles'] > 0:
            msg += f"\n범위: X({bounds['min_x']}~{bounds['max_x']}), Y({bounds['min_y']}~{bounds['max_y']})"

        messagebox.showinfo("맵 통계", msg)

    def _add_waypoint(self):
        """경유지 추가 (카드 1개만 생성, 새로고침 없음)"""
        if not hasattr(self._config, 'waypoints') or self._config.waypoints is None:
            self._config.waypoints = []

        name = self._wp_name_var.get().strip()
        if not name:
            name = f"경유지{len(self._config.waypoints) + 1}"

        reserved = {"시작", "최종"}
        existing = set()
        for wp in self._config.waypoints:
            if len(wp) >= 3 and wp[2]:
                existing.add(wp[2])
        if name in reserved or name in existing:
            from tkinter import messagebox
            messagebox.showwarning("이름 중복", f"'{name}'은(는) 이미 사용 중입니다.\n다른 이름을 입력하세요.")
            return

        # 빈 안내 라벨 제거
        if hasattr(self, '_wp_empty_label') and self._wp_empty_label:
            try:
                self._wp_empty_label.destroy()
            except Exception:
                pass
            self._wp_empty_label = None

        wp = [0, 0, name, {}]
        self._config.waypoints.append(wp)
        self._wp_name_var.set("")

        new_card_idx = len(self._config.waypoints) - 1
        self._build_single_card(new_card_idx, wp)

        logger.info(f"[좌표모드] 경유지 추가: {name} (좌표 미설정)")

    def _remove_waypoint(self, index: int):
        """경유지 삭제 (해당 카드만 제거, 나머지 번호 갱신)"""
        if not hasattr(self._config, 'waypoints') or not self._config.waypoints:
            return
        if not (0 <= index < len(self._config.waypoints)):
            return

        waypoints = self._config.waypoints
        removed_wp = waypoints[index]
        removed_name = removed_wp[2] if len(removed_wp) >= 3 else ""
        cards = getattr(self, '_wp_cards', [])

        # 부모 카드 삭제 → 자식들 ungroup
        if removed_name:
            for wp in waypoints:
                cfg = wp[3] if len(wp) >= 4 and isinstance(wp[3], dict) else None
                if cfg and cfg.get('group_parent') == removed_name:
                    cfg.pop('group_parent', None)

        # 자식 카드 삭제 → 부모의 group_ui 갱신은 reindex 후 처리

        removed = waypoints.pop(index)

        # 카드 프레임 제거
        if 0 <= index < len(cards):
            cards[index]['card'].destroy()
            cards.pop(index)

        # 나머지 카드 번호 + command 갱신
        self._reindex_cards(index)

        # 그룹 UI 갱신 (부모 삭제 시 자식 다시 표시, 자식 삭제 시 부모 배지 갱신)
        self._apply_grouping()

        # 최종 인덱스 보정
        final_idx = self._get_final_idx()
        for i, c in enumerate(cards):
            is_f = (i == final_idx)
            c['card'].configure(
                border_width=2 if is_f else 1,
                border_color=COLORS["accent_orange"] if is_f else COLORS["border"])
            if 'final_btn' in c:
                c['final_btn'].configure(
                    text="★ 최종" if is_f else "☆ 최종",
                    font=ctk.CTkFont(size=11, weight="bold") if is_f else ctk.CTkFont(size=11),
                    fg_color=COLORS["accent_orange"] if is_f else "transparent",
                    text_color="#ffffff" if is_f else COLORS["text_secondary"],
                    border_width=0 if is_f else 1,
                    border_color=COLORS["accent_orange"] if is_f else COLORS["border"])

        # 카드가 0개면 빈 안내 표시
        if not self._config.waypoints:
            if self._wp_empty_label:
                try:
                    self._wp_empty_label.destroy()
                except Exception:
                    pass
            self._wp_empty_label = ctk.CTkFrame(self._waypoint_list_frame, fg_color=COLORS["bg_card_hover"], corner_radius=8)
            self._wp_empty_label.pack(fill="x", pady=2)
            ctk.CTkLabel(self._wp_empty_label, text="경유지를 추가하세요. 순서대로 이동합니다.",
                         font=ctk.CTkFont(size=11), text_color=COLORS["text_secondary"]).pack(pady=12)

        logger.info(f"[좌표모드] 경유지 삭제: {removed}")

    def _copy_waypoint(self, index: int):
        """경유지를 클립보드에 복사 (자식 포함)"""
        try:
            import copy
            waypoints = getattr(self._config, 'waypoints', []) or []
            if not (0 <= index < len(waypoints)):
                logger.warning(f"[좌표모드] 복사 실패: index={index}, len={len(waypoints)}")
                return

            src_wp = waypoints[index]
            src_name = src_wp[2] if len(src_wp) >= 3 and src_wp[2] else f"경유지{index+1}"

            # 부모 + 자식 모두 deep copy해서 클립보드에 저장
            clipboard_data = [copy.deepcopy(src_wp)]
            # 맵 파일 경로도 수집 (붙여넣기 시 복사용)
            map_paths = [self._get_segment_map_name(index)]
            # 자식 인덱스 수집 (group_parent == 부모 이름)
            child_indices = []
            if src_name:
                for ci, wp in enumerate(waypoints):
                    if ci == index:
                        continue
                    cfg = wp[3] if len(wp) >= 4 and isinstance(wp[3], dict) else None
                    if cfg and cfg.get('group_parent') == src_name:
                        child_indices.append(ci)
            for ci in child_indices:
                clipboard_data.append(copy.deepcopy(waypoints[ci]))
                map_paths.append(self._get_segment_map_name(ci))

            set_waypoint_clipboard({"waypoints": clipboard_data, "map_paths": map_paths})

            # 저장 확인
            verify = get_waypoint_clipboard()
            logger.info(f"[좌표모드] 클립보드 확인: {len(verify) if verify else 0}개")

            # 복사 성공 피드백: 버튼 색상 변경
            cards = getattr(self, '_wp_cards', [])
            if 0 <= index < len(cards):
                btn = cards[index].get('copy_btn')
                if btn:
                    btn.configure(text="복사됨!", fg_color="#a3be8c", text_color="#1a1a1a")
                    self.after(1500, lambda b=btn: b.configure(
                        text="복사", fg_color="#3b4252", text_color="#a3be8c"))

            child_msg = f" + 자식 {len(child_indices)}개" if child_indices else ""
            logger.info(f"[좌표모드] 경유지 클립보드 복사: {src_name}{child_msg} ({len(clipboard_data)}개)")
            self.after(0, lambda n=src_name, cm=child_msg: self._append_log(f"📋 복사완료: {n}{cm}", force=True))
        except Exception as e:
            logger.error(f"[좌표모드] 복사 에러: {e}")
            import traceback
            logger.error(traceback.format_exc())
            self.after(0, lambda err=str(e): self._append_log(f"❌ 복사 실패: {err}", force=True))

    def _paste_waypoint(self):
        """클립보드의 경유지를 붙여넣기 (끝에 추가, 맵 파일도 복사)"""
        import copy, os, shutil
        raw = get_waypoint_clipboard()
        if not raw:
            from tkinter import messagebox
            messagebox.showinfo("붙여넣기", "복사된 경유지가 없습니다.\n경유지 카드의 [복사] 버튼으로 먼저 복사하세요.")
            return

        # 클립보드 형식: {"waypoints": [...], "map_paths": [...]}
        if isinstance(raw, dict):
            clipboard_data = raw.get("waypoints", [])
            src_map_paths = raw.get("map_paths", [])
        else:
            clipboard_data = raw  # 하위호환 (리스트 직접)
            src_map_paths = []

        if not clipboard_data:
            return

        if not hasattr(self._config, 'waypoints') or self._config.waypoints is None:
            self._config.waypoints = []
        waypoints = self._config.waypoints

        # 빈 안내 라벨 제거
        if hasattr(self, '_wp_empty_label') and self._wp_empty_label:
            try:
                self._wp_empty_label.destroy()
            except Exception:
                pass
            self._wp_empty_label = None

        # 기존 이름 세트 (중복 방지용)
        def _unique_name(base):
            existing = {wp[2] for wp in waypoints if len(wp) >= 3 and wp[2]}
            if base not in existing:
                return base
            candidate = f"{base}(복사)"
            n = 2
            while candidate in existing:
                candidate = f"{base}(복사{n})"
                n += 1
            return candidate

        # 부모 (첫 번째) 처리
        new_parent = copy.deepcopy(clipboard_data[0])
        old_parent_name = new_parent[2] if len(new_parent) >= 3 else ""
        new_parent_name = _unique_name(old_parent_name or "경유지")
        if len(new_parent) >= 3:
            new_parent[2] = new_parent_name
        else:
            new_parent.append(new_parent_name)
        if len(new_parent) >= 4 and isinstance(new_parent[3], dict):
            new_parent[3].pop('group_parent', None)

        all_new = [new_parent]

        # 자식 처리 (그룹 해제 — 독립 경유지로 붙여넣기)
        for child_data in clipboard_data[1:]:
            new_child = copy.deepcopy(child_data)
            child_old_name = new_child[2] if len(new_child) >= 3 else ""
            new_child_name = _unique_name(child_old_name or "경유지")
            if len(new_child) >= 3:
                new_child[2] = new_child_name
            else:
                new_child.append(new_child_name)
            if len(new_child) < 4:
                new_child.append({})
            if not isinstance(new_child[3], dict):
                new_child[3] = {}
            new_child[3].pop('group_parent', None)
            all_new.append(new_child)

        # 끝에 추가
        insert_idx = len(waypoints)
        for wp in all_new:
            waypoints.append(wp)
            self._build_single_card(len(waypoints) - 1, wp)

        # 맵 파일 처리: map_file 공유면 스킵, 아니면 복사
        map_copied = 0
        for offset, wp in enumerate(all_new):
            # map_file 설정된 경유지는 이미 공유 참조 → 파일 복사 불필요
            cfg = wp[3] if len(wp) >= 4 and isinstance(wp[3], dict) else None
            if cfg and cfg.get('map_file'):
                map_copied += 1  # 공유 맵 카운트
                logger.info(f"[좌표모드] 맵 공유 유지: {os.path.basename(cfg['map_file'])}")
                continue
            new_idx = insert_idx + offset
            dst_path = self._get_segment_map_name(new_idx)
            if offset < len(src_map_paths):
                src_path = src_map_paths[offset]
                if src_path and os.path.exists(src_path) and not os.path.exists(dst_path):
                    try:
                        os.makedirs(os.path.dirname(dst_path), exist_ok=True)
                        shutil.copy2(src_path, dst_path)
                        map_copied += 1
                        # 맵 복사 시 자동 잠금 (오염 방지)
                        if len(wp) < 4:
                            wp.append({})
                        if not isinstance(wp[3], dict):
                            wp[3] = {}
                        wp[3]['map_locked'] = True
                        logger.info(f"[좌표모드] 맵 복사+잠금: {os.path.basename(src_path)} → {os.path.basename(dst_path)}")
                    except Exception as e:
                        logger.error(f"[좌표모드] 맵 복사 실패: {e}")

        self._reindex_cards(insert_idx)
        self._apply_grouping()

        # 맵 배지 갱신
        for offset in range(len(all_new)):
            self._update_card_map_badge(insert_idx + offset)

        child_msg = f" + 자식 {len(clipboard_data) - 1}개" if len(clipboard_data) > 1 else ""
        map_msg = f" (맵 {map_copied}개 복사)" if map_copied else ""
        logger.info(f"[좌표모드] 경유지 붙여넣기: {new_parent_name}{child_msg}{map_msg}")
        self.after(0, lambda n=new_parent_name, cm=child_msg, mm=map_msg:
            self._append_log(f"📋 붙여넣기: {n}{cm}{mm}", force=True))

    def _rename_waypoint(self, index: int):
        """경유지 이름 변경 (인라인 입력)"""
        waypoints = getattr(self._config, 'waypoints', []) or []
        cards = getattr(self, '_wp_cards', [])
        if not (0 <= index < len(waypoints)) or not (0 <= index < len(cards)):
            return

        wp = waypoints[index]
        old_name = wp[2] if len(wp) >= 3 and wp[2] else f"경유지{index+1}"
        name_label = cards[index]['name_label']

        # 라벨을 엔트리로 교체
        entry = ctk.CTkEntry(cards[index]['row1'], width=120, height=24,
                             font=ctk.CTkFont(size=13, weight="bold"))
        entry.insert(0, old_name)
        entry.select_range(0, "end")
        name_label.pack_forget()
        entry.pack(side="left", padx=(4, 0), after=cards[index]['num_label'])
        entry.focus_set()

        def _confirm(event=None):
            new_name = entry.get().strip()
            if not new_name:
                new_name = old_name

            # 중복 검사
            reserved = {"시작", "최종"}
            existing = {w[2] for j, w in enumerate(waypoints) if j != index and len(w) >= 3 and w[2]}
            if new_name in reserved or new_name in existing:
                from tkinter import messagebox
                messagebox.showwarning("이름 중복", f"'{new_name}'은(는) 이미 사용 중입니다.")
                entry.focus_set()
                return

            # 자식들의 group_parent도 갱신
            if new_name != old_name:
                for w in waypoints:
                    cfg = w[3] if len(w) >= 4 and isinstance(w[3], dict) else None
                    if cfg and cfg.get('group_parent') == old_name:
                        cfg['group_parent'] = new_name

            # 데이터 업데이트
            if len(wp) >= 3:
                wp[2] = new_name
            else:
                wp.append(new_name)

            # UI 복원
            entry.destroy()
            name_label.configure(text=new_name)
            name_label.pack(side="left", padx=(4, 0), after=cards[index]['num_label'])
            logger.info(f"[좌표모드] 이름 변경: {old_name} → {new_name}")

        def _cancel(event=None):
            entry.destroy()
            name_label.pack(side="left", padx=(4, 0), after=cards[index]['num_label'])

        entry.bind("<Return>", _confirm)
        entry.bind("<Escape>", _cancel)
        entry.bind("<FocusOut>", _confirm)

    def _move_waypoint(self, index: int, direction: int):
        """경유지 순서 이동 (direction: -1=위, +1=아래)"""
        waypoints = getattr(self._config, 'waypoints', []) or []
        new_idx = index + direction
        if not (0 <= index < len(waypoints)) or not (0 <= new_idx < len(waypoints)):
            return

        # 맵 파일 경유지 순서와 동기화 (인덱스 기반 파일명)
        try:
            import os, shutil
            old_map_a = self._get_segment_map_name(index)
            old_map_b = self._get_segment_map_name(new_idx)
            if old_map_a and old_map_b and os.path.exists(old_map_a) and os.path.exists(old_map_b):
                tmp = old_map_a + ".tmp"
                shutil.move(old_map_a, tmp)
                shutil.move(old_map_b, old_map_a)
                shutil.move(tmp, old_map_b)
            elif old_map_a and os.path.exists(old_map_a) and old_map_b:
                shutil.move(old_map_a, old_map_b)
            elif old_map_b and os.path.exists(old_map_b) and old_map_a:
                shutil.move(old_map_b, old_map_a)
        except Exception as e:
            logger.warning(f"[경유지이동] 맵 파일 교환 실패: {e}")

        # 데이터 스왑
        waypoints[index], waypoints[new_idx] = waypoints[new_idx], waypoints[index]

        # 카드 스왑
        cards = self._wp_cards
        cards[index], cards[new_idx] = cards[new_idx], cards[index]

        # pack 순서 재정렬
        for c in cards:
            c['card'].pack_forget()
        for c in cards:
            c['card'].pack(fill="x", pady=(0, 3))

        # 번호 + command 갱신
        start = min(index, new_idx)
        self._reindex_cards(start)
        self._apply_grouping()

        # 최종 인덱스 표시 갱신
        final_idx = self._get_final_idx()
        for i, c in enumerate(cards):
            is_f = (i == final_idx)
            c['card'].configure(
                border_width=2 if is_f else 1,
                border_color=COLORS["accent_orange"] if is_f else COLORS["border"])
            if 'final_btn' in c:
                c['final_btn'].configure(
                    text="★ 최종" if is_f else "☆ 최종",
                    font=ctk.CTkFont(size=11, weight="bold") if is_f else ctk.CTkFont(size=11),
                    fg_color=COLORS["accent_orange"] if is_f else "transparent",
                    text_color="#ffffff" if is_f else COLORS["text_secondary"],
                    border_width=0 if is_f else 1,
                    border_color=COLORS["accent_orange"] if is_f else COLORS["border"])

    def _clear_waypoints(self):
        """모든 경유지 삭제 (각 카드 프레임만 제거)"""
        self._config.waypoints = []
        cards = getattr(self, '_wp_cards', [])
        for c in cards:
            c['card'].destroy()
        self._wp_cards = []

        self._wp_empty_label = ctk.CTkFrame(self._waypoint_list_frame, fg_color=COLORS["bg_card_hover"], corner_radius=8)
        self._wp_empty_label.pack(fill="x", pady=2)
        ctk.CTkLabel(self._wp_empty_label, text="경유지를 추가하세요. 순서대로 이동합니다.",
                     font=ctk.CTkFont(size=11), text_color=COLORS["text_secondary"]).pack(pady=12)
        logger.info("[좌표모드] 모든 경유지 삭제")

    def _press_direction_key(self, direction: str):
        """방향키 입력"""
        # 방향 → 키 매핑
        key_map = {
            "up": self._config.move_keys.get("up", "up"),
            "down": self._config.move_keys.get("down", "down"),
            "left": self._config.move_keys.get("left", "left"),
            "right": self._config.move_keys.get("right", "right"),
            "up_left": [self._config.move_keys.get("up", "up"), self._config.move_keys.get("left", "left")],
            "up_right": [self._config.move_keys.get("up", "up"), self._config.move_keys.get("right", "right")],
            "down_left": [self._config.move_keys.get("down", "down"), self._config.move_keys.get("left", "left")],
            "down_right": [self._config.move_keys.get("down", "down"), self._config.move_keys.get("right", "right")],
        }

        keys = key_map.get(direction)
        if not keys:
            return

        try:
            import pyautogui

            if isinstance(keys, list):
                # 대각선: 두 키 동시 누름
                for key in keys:
                    pyautogui.keyDown(key)
                time.sleep(0.05)
                for key in keys:
                    pyautogui.keyUp(key)
            else:
                # 단일 키
                pyautogui.press(keys)

        except Exception as e:
            logger.error(f"[미니맵] 키 입력 오류: {e}")

    def _get_segment_map_name(self, segment_idx: int) -> str:
        """구간별 맵 파일명 생성 — rule_id 기반 독립 경로.

        원칙: 항상 자기 rule_id prefix 경로만 반환.
        map_file은 초기 복사 소스로만 사용하고 직접 반환하지 않음.
        → 삭제/저장이 다른 특화모드 파일에 절대 영향 안 감.
        """
        import os, shutil
        waypoints = getattr(self._config, 'waypoints', []) or []
        base = self._config.name or "autosave"
        seg_name = self._get_segment_display_name(segment_idx)
        map_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "maps")
        # rule_id 접두사 (rule_abc12345 → abc12345)
        _rid = getattr(self, '_config_rule_id', None) or ""
        _rid_prefix = _rid.replace("rule_", "")[:8] + "_" if _rid else ""
        # 보스 경유지(0,0) 판별
        is_boss = False
        if segment_idx < len(waypoints):
            wp = waypoints[segment_idx]
            if isinstance(wp, (list, tuple)) and len(wp) >= 2:
                if int(wp[0]) == 0 and int(wp[1]) == 0:
                    is_boss = True
        # 파일명 = {rule_id}_{번호}_{경유지이름}_map.json
        if is_boss:
            own_path = os.path.join(map_dir, f"{_rid_prefix}{segment_idx:02d}_{seg_name}_boss_map.json")
        else:
            own_path = os.path.join(map_dir, f"{_rid_prefix}{segment_idx:02d}_{seg_name}_map.json")
        # 자기 파일이 없으면 → 소스에서 복사 (원본 절대 건드리지 않음)
        if not os.path.exists(own_path):
            # 1순위: map_file (다른 특화모드에서 복사해온 소스)
            source = None
            if segment_idx < len(waypoints):
                wp = waypoints[segment_idx]
                if isinstance(wp, (list, tuple)) and len(wp) >= 4 and isinstance(wp[3], dict):
                    mf = wp[3].get('map_file')
                    if mf and os.path.exists(mf):
                        source = mf
            # 2순위: 이전 형식 파일 마이그레이션
            if not source:
                has_dup = False
                for i, w in enumerate(waypoints):
                    if i != segment_idx:
                        other = w[2] if isinstance(w, (list, tuple)) and len(w) >= 3 and w[2] else f"경유지{i+1}"
                        if other == seg_name:
                            has_dup = True
                            break
                if not has_dup:
                    if is_boss:
                        old_candidates = [
                            os.path.join(map_dir, f"{segment_idx:02d}_{seg_name}_boss_map.json"),
                            os.path.join(map_dir, f"{base}_{seg_name}_map.json"),
                        ]
                    else:
                        old_candidates = [
                            os.path.join(map_dir, f"{segment_idx:02d}_{seg_name}_map.json"),
                            os.path.join(map_dir, f"{base}_{seg_name}_map.json"),
                        ]
                    for old_path in old_candidates:
                        if os.path.exists(old_path):
                            source = old_path
                            break
            if source:
                try:
                    shutil.copy2(source, own_path)
                    logger.info(f"[맵] 복사: {os.path.basename(source)} → {os.path.basename(own_path)}")
                except Exception as e:
                    logger.error(f"[맵] 복사 실패: {e}")
        return own_path

    def _get_segment_display_name(self, segment_idx: int) -> str:
        """구간 인덱스에 해당하는 표시 이름 반환 (경유지 인덱스와 1:1 대응)"""
        waypoints = getattr(self._config, 'waypoints', []) or []
        if segment_idx < len(waypoints):
            wp = waypoints[segment_idx]
            return wp[2] if len(wp) >= 3 and wp[2] else f"경유지{segment_idx+1}"
        return f"경유지{segment_idx+1}"

    def _get_segment_map_tiles(self, segment_idx: int) -> int:
        """구간 맵의 타일 수 반환"""
        import os, json
        map_path = self._get_segment_map_name(segment_idx)
        if not os.path.exists(map_path):
            return -1  # 미탐색
        try:
            with open(map_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return len(data.get("passable", [])) + len(data.get("blocked", []))
        except Exception:
            return -1

    def _refresh_waypoint_list(self):
        """경유지 리스트 초기 빌드 (최초 1회만 호출)"""
        for widget in self._waypoint_list_frame.winfo_children():
            widget.destroy()
        self._wp_cards = []  # 위젯 참조 저장

        waypoints = getattr(self._config, 'waypoints', []) or []
        if not waypoints:
            self._wp_empty_label = ctk.CTkFrame(self._waypoint_list_frame, fg_color=COLORS["bg_card_hover"], corner_radius=8)
            self._wp_empty_label.pack(fill="x", pady=2)
            ctk.CTkLabel(self._wp_empty_label, text="경유지를 추가하세요. 순서대로 이동합니다.",
                         font=ctk.CTkFont(size=11), text_color=COLORS["text_secondary"]).pack(pady=12)
            return

        for i, wp in enumerate(waypoints):
            self._build_single_card(i, wp)
        # 저장된 그룹 상태 적용 (자식 카드 숨김)
        self._apply_grouping()

    def _get_final_idx(self) -> int:
        """최종 목표 인덱스 반환"""
        waypoints = getattr(self._config, 'waypoints', []) or []
        final_idx = getattr(self._config, 'final_waypoint_idx', -1)
        if final_idx < 0 or final_idx >= len(waypoints):
            final_idx = len(waypoints) - 1
        return final_idx

    def _build_single_card(self, i: int, wp):
        """경유지 카드 1개 빌드, 위젯 참조를 self._wp_cards에 저장"""
        wp_name = wp[2] if len(wp) >= 3 and wp[2] else f"경유지{i+1}"
        wp_x = wp[0] if len(wp) >= 1 else 0
        wp_y = wp[1] if len(wp) >= 2 else 0
        _wp_cfg = wp[3] if len(wp) >= 4 and isinstance(wp[3], dict) else None
        tiles = self._get_segment_map_tiles(i)
        has_map = tiles >= 0
        map_tag = f"{tiles}칸" if has_map else "미탐색"
        badge_color = COLORS["accent"] if has_map else COLORS["bg_card"]
        badge_text_color = "#ffffff" if has_map else COLORS["text_muted"]
        has_coord = not (wp_x == 0 and wp_y == 0)

        card = ctk.CTkFrame(self._waypoint_list_frame,
                            fg_color=COLORS["bg_card_hover"], corner_radius=8,
                            border_width=1, border_color=COLORS["border"])
        card.pack(fill="x", pady=(0, 3))

        # 1행
        row1 = ctk.CTkFrame(card, fg_color="transparent")
        row1.pack(fill="x", padx=8, pady=(6, 0))

        # 드래그 핸들 (≡)
        drag_handle = ctk.CTkLabel(row1, text="≡", width=16, height=22,
                     font=ctk.CTkFont(size=14),
                     text_color=COLORS["text_muted"], cursor="fleur")
        drag_handle.pack(side="left", padx=(0, 2))
        drag_handle.bind("<ButtonPress-1>", lambda e, idx=i: self._on_drag_start(e, idx))
        drag_handle.bind("<B1-Motion>", self._on_drag_motion)
        drag_handle.bind("<ButtonRelease-1>", lambda e, idx=i: self._on_drag_end(e, idx))

        num_label = ctk.CTkLabel(row1, text=f"{i+1}.",
                     font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=COLORS["accent_blue"], width=22)
        num_label.pack(side="left")
        name_label = ctk.CTkLabel(row1, text=wp_name,
                     font=ctk.CTkFont(size=13, weight="bold"),
                     cursor="hand2")
        name_label.pack(side="left", padx=(4, 0))
        name_label.bind("<Button-1>", lambda e, idx=i: self._rename_waypoint(idx))

        # 맵 상태 배지
        map_badge = ctk.CTkFrame(row1, fg_color=badge_color, corner_radius=4)
        map_badge.pack(side="left", padx=(8, 0))
        map_badge_label = ctk.CTkLabel(map_badge, text=f" {map_tag} ",
                     font=ctk.CTkFont(size=10), text_color=badge_text_color)
        map_badge_label.pack(padx=4, pady=1)

        # 맵 잠금 토글 버튼
        _is_locked = bool(_wp_cfg.get('map_locked')) if _wp_cfg else False
        map_lock_btn = ctk.CTkButton(row1, text="🔒" if _is_locked else "🔓",
                      width=28, height=24, font=ctk.CTkFont(size=13),
                      fg_color="#d08770" if _is_locked else "transparent",
                      hover_color="#bf616a" if _is_locked else "#4c566a",
                      text_color="#ffffff" if _is_locked else COLORS["text_muted"],
                      border_width=1, border_color="#d08770" if _is_locked else COLORS["border"],
                      command=lambda idx=i: self._toggle_map_lock(idx))
        map_lock_btn.pack(side="left", padx=(4, 0))

        # 그룹 펼치기/접기 버튼 (자식 있을 때만 표시) — 테스트 버튼 옆
        group_btn = ctk.CTkButton(row1, text="▶ 펼치기", width=80, height=24,
                      font=ctk.CTkFont(size=11, weight="bold"),
                      fg_color="#4c566a", hover_color="#5e6a82",
                      text_color="#d8dee9", corner_radius=6,
                      command=lambda idx=i: self._toggle_group(idx))
        # 초기에는 pack 안 함 (자식 생기면 _apply_grouping에서 pack)
        child_count_label = ctk.CTkLabel(row1, text="",
                     font=ctk.CTkFont(size=10, weight="bold"),
                     text_color="#ebcb8b", width=30)
        # 초기에는 pack 안 함

        # 해체 버튼 (자식일 때만 표시) — 그룹에서 빼기
        ungroup_btn = ctk.CTkButton(row1, text="↩ 해체", width=55, height=24,
                      font=ctk.CTkFont(size=10),
                      fg_color="#3b3024", hover_color="#5a4838",
                      text_color="#ebcb8b", corner_radius=6,
                      border_width=1, border_color="#5a4838",
                      command=lambda idx=i: self._ungroup_card(idx))
        # 초기에는 pack 안 함 (자식이면 _apply_grouping에서 pack)

        # 삭제 버튼
        del_btn = ctk.CTkButton(row1, text="✕", width=26, height=24,
                      font=ctk.CTkFont(size=11),
                      fg_color="transparent", hover_color=COLORS["accent_red"],
                      text_color=COLORS["text_muted"],
                      command=lambda idx=i: self._remove_waypoint(idx))
        del_btn.pack(side="right")

        # 순서 이동 버튼
        move_down_btn = ctk.CTkButton(row1, text="▼", width=24, height=24,
                      font=ctk.CTkFont(size=11),
                      fg_color="transparent", hover_color="#5e81ac",
                      text_color=COLORS["text_muted"],
                      command=lambda idx=i: self._move_waypoint(idx, 1))
        move_down_btn.pack(side="right")
        move_up_btn = ctk.CTkButton(row1, text="▲", width=24, height=24,
                      font=ctk.CTkFont(size=11),
                      fg_color="transparent", hover_color="#5e81ac",
                      text_color=COLORS["text_muted"],
                      command=lambda idx=i: self._move_waypoint(idx, -1))
        move_up_btn.pack(side="right")

        # 붙여넣기 버튼
        paste_btn = ctk.CTkButton(row1, text="붙여넣기", width=60, height=24,
                      font=ctk.CTkFont(size=10),
                      fg_color="#3b4252", hover_color="#5e81ac",
                      text_color="#88c0d0",
                      border_width=1, border_color="#5e81ac",
                      command=self._paste_waypoint)
        paste_btn.pack(side="right", padx=(0, 2))

        # 복사 버튼
        copy_btn = ctk.CTkButton(row1, text="복사", width=45, height=24,
                      font=ctk.CTkFont(size=10),
                      fg_color="#3b4252", hover_color="#a3be8c",
                      text_color="#a3be8c",
                      border_width=1, border_color="#a3be8c",
                      command=lambda idx=i: self._copy_waypoint(idx))
        copy_btn.pack(side="right", padx=(0, 2))

        # 2행: 경로 좌표 (출발/도착 각각 독립 추가)
        route_frame = ctk.CTkFrame(card, fg_color="transparent")
        route_frame.pack(fill="x", padx=8, pady=(2, 2))
        self._build_route_rows(route_frame, i, wp)
        # wp_idx 저장 (경로추가 버튼에서 사용)
        route_frame._wp_idx = i

        # 3행: 맵핑 버튼들
        _btn_w = 72
        _btn_h = 26
        _btn_font = ctk.CTkFont(size=11)
        _btn_pad = (0, 3)

        row3 = ctk.CTkFrame(card, fg_color="transparent")
        row3.pack(fill="x", padx=8, pady=(0, 2))

        single_mapping_btn = ctk.CTkButton(row3, text="맵핑테스트", width=_btn_w, height=_btn_h,
                          font=ctk.CTkFont(size=11, weight="bold"),
                          fg_color="#1a3526", hover_color=COLORS["accent"],
                          text_color="#4ae168", border_width=1, border_color="#2d5a3d",
                          command=lambda idx=i: self._run_single_mapping_test(idx))
        single_mapping_btn.pack(side="left", padx=_btn_pad)

        show_btn = ctk.CTkButton(row3, text="미리보기", width=_btn_w, height=_btn_h,
                      font=_btn_font,
                      fg_color="#1a2b3d", hover_color="#2a4a6b",
                      text_color="#7ec8ff", border_width=1, border_color="#2a4a6b",
                      command=lambda idx=i: self._show_map_for(idx))
        show_btn.pack(side="left", padx=_btn_pad)

        clear_btn = None  # 초기화 버튼 제거됨

        add_route_start_btn = ctk.CTkButton(row3, text="+출발", width=48, height=_btn_h,
                      font=ctk.CTkFont(size=10),
                      fg_color="#2d1a3d", hover_color="#4a2060",
                      text_color="#c8a0e8", border_width=1, border_color="#4a2060",
                      command=lambda idx=i: self._add_route_start(idx))
        add_route_start_btn.pack(side="left", padx=(6, 2))
        add_route_end_btn = ctk.CTkButton(row3, text="+도착", width=48, height=_btn_h,
                      font=ctk.CTkFont(size=10),
                      fg_color="#1a2d1a", hover_color="#204a20",
                      text_color="#a0e8a0", border_width=1, border_color="#204a20",
                      command=lambda idx=i: self._add_route_end(idx))
        add_route_end_btn.pack(side="left", padx=(0, 2))
        add_route_wall_btn = ctk.CTkButton(row3, text="+벽", width=38, height=_btn_h,
                      font=ctk.CTkFont(size=10),
                      fg_color="#3d1a1a", hover_color="#602020",
                      text_color="#e8a0a0", border_width=1, border_color="#602020",
                      command=lambda idx=i: self._add_route_wall(idx))
        add_route_wall_btn.pack(side="left", padx=(0, 3))

        # 4행: 맵 저장/불러오기 + 이미지 버튼
        row4 = ctk.CTkFrame(card, fg_color="transparent")
        row4.pack(fill="x", padx=8, pady=(0, 1))

        load_map_btn = ctk.CTkButton(row4, text="맵불러오기", width=_btn_w, height=_btn_h,
                      font=_btn_font,
                      fg_color="#251a35", hover_color="#3d2a55",
                      text_color="#c8a8e8", border_width=1, border_color="#3d2a55",
                      command=lambda idx=i: self._load_map_for(idx))
        load_map_btn.pack(side="left", padx=_btn_pad)

        add_char_img_btn = ctk.CTkButton(row4, text="캐릭터", width=_btn_w, height=_btn_h,
                      font=_btn_font,
                      fg_color="#1a2535", hover_color="#2a4555",
                      text_color="#a0c8e8", border_width=1, border_color="#2a4555",
                      command=lambda idx=i: self._add_character_image(idx))
        add_char_img_btn.pack(side="left", padx=_btn_pad)

        add_boss_img_btn = ctk.CTkButton(row4, text="보스", width=_btn_w, height=_btn_h,
                      font=_btn_font,
                      fg_color="#1a3525", hover_color="#2a5540",
                      text_color="#90e8a0", border_width=1, border_color="#2a5540",
                      command=lambda idx=i: self._add_boss_image(idx))
        add_boss_img_btn.pack(side="left", padx=_btn_pad)

        _arr_keys = _wp_cfg.get("arrival_keys", []) if _wp_cfg else []
        if not _arr_keys and _wp_cfg and _wp_cfg.get("arrival_key"):
            _arr_keys = [{"key": k.strip()} for k in _wp_cfg["arrival_key"].split(",") if k.strip()]
        arr_key_btn = ctk.CTkButton(row4, text="키", width=_btn_w, height=_btn_h,
                      font=_btn_font,
                      fg_color="#2a2518", hover_color="#4a3828",
                      text_color="#e8d090", border_width=1, border_color="#4a3828",
                      command=lambda idx=i: self._edit_arrival_key(idx))
        arr_key_btn.pack(side="left", padx=_btn_pad)

        # 5행: 이미지 상태 표시 (설정된 이미지가 있을 때만)
        wp_img_cfg = wp[3] if len(wp) >= 4 and isinstance(wp[3], dict) else None
        char_img_name = ""
        if wp_img_cfg and wp_img_cfg.get("character_image"):
            char_img_name = Path(wp_img_cfg["character_image"]).stem[:10]
        boss_img_name = ""
        if wp_img_cfg and wp_img_cfg.get("target_image"):
            boss_img_name = Path(wp_img_cfg["target_image"]).stem[:10]

        row5 = ctk.CTkFrame(card, fg_color="transparent")
        if char_img_name or boss_img_name or _arr_keys:
            row5.pack(fill="x", padx=8, pady=(0, 2))

        char_img_status = ctk.CTkLabel(row5,
                     text=f"캐릭터: {char_img_name}" if char_img_name else "",
                     font=ctk.CTkFont(size=10), text_color="#a0c8e8",
                     cursor="hand2" if char_img_name else "")
        if char_img_name:
            char_img_status.pack(side="left", padx=(0, 2))
            char_img_status.bind("<Button-1>", lambda e, idx=i: self._edit_character_image(idx))

        del_char_img_btn = ctk.CTkButton(row5, text="✕", width=20, height=20,
                      font=ctk.CTkFont(size=9),
                      fg_color="transparent", hover_color="#5a2020",
                      text_color="#e06060", border_width=0,
                      command=lambda idx=i: self._delete_character_image(idx))
        if char_img_name:
            del_char_img_btn.pack(side="left", padx=(0, 8))

        boss_img_status = ctk.CTkLabel(row5,
                     text=f"보스: {boss_img_name}" if boss_img_name else "",
                     font=ctk.CTkFont(size=10), text_color="#90e8a0",
                     cursor="hand2" if boss_img_name else "")
        if boss_img_name:
            boss_img_status.pack(side="left", padx=(0, 2))
            boss_img_status.bind("<Button-1>", lambda e, idx=i: self._edit_waypoint_image(idx))

        del_boss_img_btn = ctk.CTkButton(row5, text="✕", width=20, height=20,
                      font=ctk.CTkFont(size=9),
                      fg_color="transparent", hover_color="#5a2020",
                      text_color="#e06060", border_width=0,
                      command=lambda idx=i: self._delete_waypoint_image(idx))
        if boss_img_name:
            del_boss_img_btn.pack(side="left")

        if _arr_keys:
            _kn = ",".join(kd.get("key", "?") for kd in _arr_keys)
            ctk.CTkLabel(row5, text=f"키: {_kn}",
                         font=ctk.CTkFont(size=10), text_color="#e8d090").pack(side="left", padx=(8, 0))

        # 위젯 참조 저장 (모든 버튼 포함)
        self._wp_cards.append({
            'card': card,
            'row1': row1,
            'num_label': num_label,
            'name_label': name_label,
            'map_badge': map_badge,
            'map_badge_label': map_badge_label,
            'map_lock_btn': map_lock_btn,
            'drag_handle': drag_handle,
            'group_btn': group_btn,
            'child_count_label': child_count_label,
            'ungroup_btn': ungroup_btn,
            'group_expanded': False,
            'route_frame': route_frame,
            'single_mapping_btn': single_mapping_btn,
            'del_btn': del_btn,
            'move_up_btn': move_up_btn,
            'move_down_btn': move_down_btn,
            'copy_btn': copy_btn,
            'paste_btn': paste_btn,
            'test_btn': None,
            'show_btn': show_btn,
            'clear_btn': clear_btn,
            'load_map_btn': load_map_btn,
            'add_char_img_btn': add_char_img_btn,
            'char_img_status': char_img_status,
            'del_char_img_btn': del_char_img_btn,
            'add_boss_img_btn': add_boss_img_btn,
            'boss_img_status': boss_img_status,
            'del_boss_img_btn': del_boss_img_btn,
            'arr_key_btn': arr_key_btn,
            'add_route_start_btn': add_route_start_btn,
            'add_route_end_btn': add_route_end_btn,
            'add_route_wall_btn': add_route_wall_btn,
        })

    # ── 드래그&드롭 그룹핑 ──

    def _on_drag_start(self, event, idx):
        """드래그 시작"""
        self._drag_idx = idx
        self._drag_start_x = event.x_root
        self._drag_start_y = event.y_root
        self._drag_active = False
        self._drag_target_idx = None

    def _on_drag_motion(self, event):
        """드래그 중 — 드롭 대상 하이라이트"""
        if not hasattr(self, '_drag_idx') or self._drag_idx is None:
            return
        # 최소 10px 이동해야 드래그 활성화
        if not self._drag_active:
            dx = abs(event.x_root - self._drag_start_x)
            dy = abs(event.y_root - self._drag_start_y)
            if dx + dy < 10:
                return
            self._drag_active = True
            # 드래그 중인 카드 시각적 표시
            cards = getattr(self, '_wp_cards', [])
            if 0 <= self._drag_idx < len(cards):
                cards[self._drag_idx]['card'].configure(border_color="#88ccff", border_width=2)

        # 이전 하이라이트 해제
        cards = getattr(self, '_wp_cards', [])
        if self._drag_target_idx is not None and 0 <= self._drag_target_idx < len(cards):
            _prev = cards[self._drag_target_idx]
            _prev['card'].configure(border_color=COLORS["border"], border_width=1)
            self._drag_target_idx = None

        # 마우스 아래 카드 찾기
        target = self._find_card_at_pos(event.x_root, event.y_root)
        if target is not None and target != self._drag_idx and 0 <= target < len(cards):
            self._drag_target_idx = target
            cards[target]['card'].configure(border_color="#ebcb8b", border_width=2)

    def _on_drag_end(self, event, idx):
        """드래그 종료 — 그룹핑 실행"""
        cards = getattr(self, '_wp_cards', [])
        drag_idx = getattr(self, '_drag_idx', None)
        was_active = getattr(self, '_drag_active', False)

        # 시각적 표시 복원
        if drag_idx is not None and 0 <= drag_idx < len(cards):
            final_idx = self._get_final_idx()
            is_f = (drag_idx == final_idx)
            cards[drag_idx]['card'].configure(
                border_color=COLORS["accent_orange"] if is_f else COLORS["border"],
                border_width=2 if is_f else 1)
        if self._drag_target_idx is not None and 0 <= self._drag_target_idx < len(cards):
            t_final = (self._drag_target_idx == self._get_final_idx())
            cards[self._drag_target_idx]['card'].configure(
                border_color=COLORS["accent_orange"] if t_final else COLORS["border"],
                border_width=2 if t_final else 1)

        # 유효한 드롭이면 그룹핑
        if was_active and self._drag_target_idx is not None and drag_idx is not None:
            if self._drag_target_idx != drag_idx:
                self._group_card(drag_idx, self._drag_target_idx)

        self._drag_idx = None
        self._drag_active = False
        self._drag_target_idx = None

    def _find_card_at_pos(self, x_root, y_root):
        """마우스 위치에 있는 카드 인덱스 반환 (보이는 카드만)"""
        cards = getattr(self, '_wp_cards', [])
        for i, cd in enumerate(cards):
            card = cd['card']
            try:
                if not card.winfo_ismapped():
                    continue
                cx = card.winfo_rootx()
                cy = card.winfo_rooty()
                cw = card.winfo_width()
                ch = card.winfo_height()
                if cx <= x_root <= cx + cw and cy <= y_root <= cy + ch:
                    return i
            except Exception:
                continue
        return None

    # ── 그룹 관리 ──

    def _get_children(self, parent_idx):
        """parent_idx의 자식 인덱스 리스트 반환 (인덱스 순)"""
        waypoints = getattr(self._config, 'waypoints', []) or []
        if not (0 <= parent_idx < len(waypoints)):
            return []
        parent_wp = waypoints[parent_idx]
        parent_name = parent_wp[2] if len(parent_wp) >= 3 else ""
        if not parent_name:
            return []
        children = []
        for i, wp in enumerate(waypoints):
            if i == parent_idx:
                continue
            cfg = wp[3] if len(wp) >= 4 and isinstance(wp[3], dict) else None
            if cfg and cfg.get('group_parent') == parent_name:
                children.append(i)
        return sorted(children)

    def _group_card(self, child_idx, parent_idx):
        """child_idx 카드를 parent_idx 그룹에 넣기"""
        waypoints = getattr(self._config, 'waypoints', []) or []
        cards = getattr(self, '_wp_cards', [])
        if not (0 <= child_idx < len(waypoints) and 0 <= parent_idx < len(waypoints)):
            return
        if child_idx == parent_idx:
            return
        # 순환 방지: parent가 child의 자식이면 불가
        child_wp = waypoints[child_idx]
        parent_wp = waypoints[parent_idx]
        child_name = child_wp[2] if len(child_wp) >= 3 else ""
        parent_name = parent_wp[2] if len(parent_wp) >= 3 else ""
        if not parent_name:
            return
        # 이미 같은 부모에 속해 있으면 무시
        child_cfg_chk = child_wp[3] if len(child_wp) >= 4 and isinstance(child_wp[3], dict) else None
        if child_cfg_chk and child_cfg_chk.get('group_parent') == parent_name:
            return
        # parent가 이미 다른 그룹의 자식인지 체크 (2단 그룹 방지)
        parent_cfg = parent_wp[3] if len(parent_wp) >= 4 and isinstance(parent_wp[3], dict) else None
        if parent_cfg and parent_cfg.get('group_parent'):
            return  # 자식 카드 안에 넣기 불가
        # child에 group_parent 설정
        if len(child_wp) < 4:
            child_wp.append({})
        if not isinstance(child_wp[3], dict):
            child_wp[3] = {}
        # 이미 다른 부모가 있으면 이전 부모 갱신
        old_parent_name = child_wp[3].get('group_parent')
        child_wp[3]['group_parent'] = parent_name
        # child 카드 숨김
        if 0 <= child_idx < len(cards):
            cards[child_idx]['card'].pack_forget()
        # 부모 그룹 버튼/배지 갱신
        self._update_group_ui(parent_idx)
        # 이전 부모가 있었으면 갱신
        if old_parent_name and old_parent_name != parent_name:
            old_parent_idx = self._find_wp_by_name(old_parent_name)
            if old_parent_idx is not None:
                self._update_group_ui(old_parent_idx)

    def _ungroup_card(self, child_idx):
        """child_idx 카드를 그룹에서 빼기"""
        waypoints = getattr(self._config, 'waypoints', []) or []
        cards = getattr(self, '_wp_cards', [])
        if not (0 <= child_idx < len(waypoints)):
            return
        child_wp = waypoints[child_idx]
        cfg = child_wp[3] if len(child_wp) >= 4 and isinstance(child_wp[3], dict) else None
        if not cfg or not cfg.get('group_parent'):
            return
        parent_name = cfg.pop('group_parent', None)
        # 전체 카드를 올바른 순서로 재정렬 (pack 순서 보장)
        if 0 <= child_idx < len(cards):
            for j in range(len(cards)):
                cards[j]['card'].pack_forget()
            for j in range(len(cards)):
                cards[j]['card'].pack(fill="x", pady=(0, 3))
            self._apply_grouping()
        # 이전 부모 갱신
        if parent_name:
            parent_idx = self._find_wp_by_name(parent_name)
            if parent_idx is not None:
                self._update_group_ui(parent_idx)

    def _toggle_map_lock(self, idx):
        """경유지 맵 잠금 토글"""
        waypoints = getattr(self._config, 'waypoints', []) or []
        if not (0 <= idx < len(waypoints)):
            return
        wp = waypoints[idx]
        cfg = self._ensure_wp_config(wp)
        locked = not cfg.get('map_locked', False)
        cfg['map_locked'] = locked
        cards = getattr(self, '_wp_cards', [])
        if 0 <= idx < len(cards):
            btn = cards[idx].get('map_lock_btn')
            if btn:
                btn.configure(
                    text="🔒" if locked else "🔓",
                    fg_color="#d08770" if locked else "transparent",
                    hover_color="#bf616a" if locked else "#4c566a",
                    text_color="#ffffff" if locked else COLORS["text_muted"],
                    border_color="#d08770" if locked else COLORS["border"])
        self._save_config()
        seg_name = wp[2] if len(wp) >= 3 else f"경유지{idx+1}"
        self._append_log(f"{'🔒 맵 잠금' if locked else '🔓 맵 잠금 해제'}: {seg_name}")

    def _toggle_group(self, parent_idx):
        """부모 카드의 자식 그룹 펼치기/접기"""
        cards = getattr(self, '_wp_cards', [])
        if not (0 <= parent_idx < len(cards)):
            return
        children = self._get_children(parent_idx)
        if not children:
            return
        n = len(children)
        card_data = cards[parent_idx]
        if card_data.get('group_expanded', False):
            # 접기
            for ci in children:
                if 0 <= ci < len(cards):
                    cards[ci]['card'].pack_forget()
            card_data['group_btn'].configure(text=f"▶ 펼치기 ({n})")
            card_data['group_expanded'] = False
        else:
            # 펼치기: 부모 바로 뒤에 인덱스 순으로 pack
            after_w = card_data['card']
            for ci in children:
                if 0 <= ci < len(cards):
                    cards[ci]['card'].pack(fill="x", pady=(0, 3), padx=(16, 0), after=after_w)
                    after_w = cards[ci]['card']
            card_data['group_btn'].configure(text=f"▼ 접기 ({n})")
            card_data['group_expanded'] = True

    def _update_group_ui(self, parent_idx):
        """부모 카드의 그룹 버튼/배지 갱신"""
        cards = getattr(self, '_wp_cards', [])
        if not (0 <= parent_idx < len(cards)):
            return
        children = self._get_children(parent_idx)
        cd = cards[parent_idx]
        if children:
            n = len(children)
            is_expanded = cd.get('group_expanded', False)
            btn_text = f"▼ 접기 ({n})" if is_expanded else f"▶ 펼치기 ({n})"
            # 그룹 버튼 표시 (test_btn 옆)
            if not cd['group_btn'].winfo_ismapped():
                cd['group_btn'].pack(side="left", padx=(6, 0))
            cd['group_btn'].configure(text=btn_text)
            # child_count_label은 사용 안 함 (버튼 텍스트에 포함)
            cd['child_count_label'].pack_forget()
        else:
            # 그룹 버튼 숨김
            cd['group_btn'].pack_forget()
            cd['child_count_label'].pack_forget()
            cd['child_count_label'].configure(text="")
            cd['group_expanded'] = False

    def _find_wp_by_name(self, name):
        """경유지 이름으로 인덱스 찾기"""
        waypoints = getattr(self._config, 'waypoints', []) or []
        for i, wp in enumerate(waypoints):
            wp_name = wp[2] if len(wp) >= 3 else ""
            if wp_name == name:
                return i
        return None

    def _apply_grouping(self):
        """그룹 상태 적용 (자식 숨김/해체버튼, 부모 버튼 표시)"""
        waypoints = getattr(self._config, 'waypoints', []) or []
        cards = getattr(self, '_wp_cards', [])
        # 모든 카드의 해체 버튼 초기화
        for j in range(len(cards)):
            cards[j]['ungroup_btn'].pack_forget()
        # 자식 카드 숨기기 + 해체 버튼 준비
        for i, wp in enumerate(waypoints):
            cfg = wp[3] if len(wp) >= 4 and isinstance(wp[3], dict) else None
            if cfg and cfg.get('group_parent'):
                if 0 <= i < len(cards):
                    cards[i]['card'].pack_forget()
                    if not cards[i]['ungroup_btn'].winfo_ismapped():
                        cards[i]['ungroup_btn'].pack(side="left", padx=(6, 0))
        # 부모 카드에 그룹 버튼 표시 + 펼쳐진 그룹은 자식 다시 pack
        for i in range(len(waypoints)):
            children = self._get_children(i)
            if children:
                self._update_group_ui(i)
                if 0 <= i < len(cards) and cards[i].get('group_expanded', False):
                    after_w = cards[i]['card']
                    for ci in children:
                        if 0 <= ci < len(cards):
                            cards[ci]['card'].pack(fill="x", pady=(0, 3), padx=(16, 0), after=after_w)
                            after_w = cards[ci]['card']

    def _toggle_all_cards(self):
        """모든 그룹 펼치기/접기"""
        cards = getattr(self, '_wp_cards', [])
        waypoints = getattr(self._config, 'waypoints', []) or []
        if not cards:
            return
        if self._wp_all_collapsed:
            # 모두 펼치기: 모든 자식 카드 표시
            for i in range(len(waypoints)):
                children = self._get_children(i)
                if children and 0 <= i < len(cards):
                    after_w = cards[i]['card']
                    for ci in children:
                        if 0 <= ci < len(cards):
                            cards[ci]['card'].pack(fill="x", pady=(0, 3), padx=(16, 0),
                                                   after=after_w)
                            after_w = cards[ci]['card']
                    cards[i]['group_btn'].configure(text="▼")
                    cards[i]['group_expanded'] = True
            self._wp_all_collapsed = False
            self._wp_collapse_all_btn.configure(text="모두 접기")
        else:
            # 모두 접기: 모든 자식 카드 숨김
            for i, wp in enumerate(waypoints):
                cfg = wp[3] if len(wp) >= 4 and isinstance(wp[3], dict) else None
                if cfg and cfg.get('group_parent'):
                    if 0 <= i < len(cards):
                        cards[i]['card'].pack_forget()
            for i in range(len(cards)):
                cards[i]['group_expanded'] = False
                if cards[i]['group_btn'].winfo_ismapped():
                    cards[i]['group_btn'].configure(text="▶")
            self._wp_all_collapsed = True
            self._wp_collapse_all_btn.configure(text="모두 펼치기")

    # ── 개별 위젯 업데이트 (새로고침 없음) ──

    def _update_card_map_badge(self, idx: int):
        """맵 배지만 업데이트 (1개 카드)"""
        cards = getattr(self, '_wp_cards', [])
        if 0 <= idx < len(cards):
            tiles = self._get_segment_map_tiles(idx)
            has_map = tiles >= 0
            map_tag = f"{tiles}칸" if has_map else "미탐색"
            badge_color = COLORS["accent"] if has_map else COLORS["bg_card"]
            badge_text_color = "#ffffff" if has_map else COLORS["text_muted"]
            cards[idx]['map_badge'].configure(fg_color=badge_color)
            cards[idx]['map_badge_label'].configure(text=f" {map_tag} ", text_color=badge_text_color)

    def _add_boss_image(self, idx: int):
        """경유지에 보스 이미지 추가 (파일 선택 → templates 복사 → wp[3] 저장)"""
        from tkinter import filedialog
        import shutil
        import uuid

        waypoints = getattr(self._config, 'waypoints', []) or []
        if idx >= len(waypoints):
            return

        image_path = filedialog.askopenfilename(
            title="보스 이미지 선택",
            filetypes=[
                ("이미지 파일", "*.png *.jpg *.jpeg *.bmp *.gif"),
                ("모든 파일", "*.*"),
            ],
            initialdir=str(DATA_DIR / "templates"),
        )
        if not image_path:
            return

        try:
            templates_dir = DATA_DIR / "templates"
            templates_dir.mkdir(parents=True, exist_ok=True)
            new_ext = Path(image_path).suffix
            new_filename = f"wp_img_{uuid.uuid4().hex[:8]}{new_ext}"
            dest_path = templates_dir / new_filename
            shutil.copy2(image_path, dest_path)

            # wp[3]에 보스 이미지 설정 저장
            wp = waypoints[idx]
            if len(wp) < 4:
                img_cfg = {
                    "target_image": str(dest_path),
                    "target_images": [],
                    "confidence": 0.65,
                    "search_radius": 0,
                    "action_x": None,
                    "action_y": None,
                    "move_mouse_before_search": False,
                }
                wp.append(img_cfg)
            elif not isinstance(wp[3], dict):
                img_cfg = {
                    "target_image": str(dest_path),
                    "target_images": [],
                    "confidence": 0.65,
                    "search_radius": 0,
                    "action_x": None,
                    "action_y": None,
                    "move_mouse_before_search": False,
                }
                wp[3] = img_cfg
            else:
                wp[3]["target_image"] = str(dest_path)
                wp[3].setdefault("target_images", [])
                wp[3].setdefault("confidence", 0.65)
                wp[3].setdefault("search_radius", 0)
                wp[3].setdefault("action_x", None)
                wp[3].setdefault("action_y", None)
                wp[3].setdefault("move_mouse_before_search", False)

            self._save_config()
            self._update_card_image_status(idx)
            logger.info(f"[좌표모드] 경유지{idx+1} 보스 이미지 추가: {dest_path}")

            # ImageCropDialog 열기 (편집 가능)
            self._edit_waypoint_image(idx)
        except Exception as e:
            logger.error(f"[좌표모드] 보스 이미지 추가 실패: {e}")

    def _add_character_image(self, idx: int):
        """경유지에 캐릭터 이미지 추가 (파일 선택 → templates 복사 → wp[3]["character_image"] 저장)"""
        from tkinter import filedialog
        import shutil
        import uuid

        waypoints = getattr(self._config, 'waypoints', []) or []
        if idx >= len(waypoints):
            return

        image_path = filedialog.askopenfilename(
            title="캐릭터 이미지 선택",
            filetypes=[
                ("이미지 파일", "*.png *.jpg *.jpeg *.bmp *.gif"),
                ("모든 파일", "*.*"),
            ],
            initialdir=str(DATA_DIR / "templates"),
        )
        if not image_path:
            return

        try:
            templates_dir = DATA_DIR / "templates"
            templates_dir.mkdir(parents=True, exist_ok=True)
            new_ext = Path(image_path).suffix
            new_filename = f"wp_char_{uuid.uuid4().hex[:8]}{new_ext}"
            dest_path = templates_dir / new_filename
            shutil.copy2(image_path, dest_path)

            # wp[3]에 캐릭터 이미지 저장
            wp = waypoints[idx]
            if len(wp) < 4:
                wp.append({"character_image": str(dest_path)})
            elif not isinstance(wp[3], dict):
                wp[3] = {"character_image": str(dest_path)}
            else:
                wp[3]["character_image"] = str(dest_path)

            self._save_config()
            self._update_card_image_status(idx)
            logger.info(f"[좌표모드] 경유지{idx+1} 캐릭터 이미지 추가: {dest_path}")

            # ImageCropDialog 열기 (편집 가능)
            self._edit_character_image(idx)
        except Exception as e:
            logger.error(f"[좌표모드] 캐릭터 이미지 추가 실패: {e}")

    def _edit_waypoint_image(self, idx: int):
        """경유지 이미지 편집 — ImageCropDialog 열기"""
        waypoints = getattr(self._config, 'waypoints', []) or []
        if idx >= len(waypoints):
            return
        wp = waypoints[idx]
        if len(wp) < 4 or not isinstance(wp[3], dict):
            return
        img_cfg = wp[3]
        img_path = img_cfg.get("target_image", "")
        if not img_path or not Path(img_path).exists():
            return

        # 임시 AutomationRule 생성 (ImageCropDialog가 rule 파라미터로 사용)
        temp_rule = AutomationRule(
            rule_id=f"wp_{idx}",
            action_type="click",
            target_image=img_path,
            target_images=list(img_cfg.get("target_images", [])),
            confidence=img_cfg.get("confidence", 0.65),
            search_radius=img_cfg.get("search_radius", 0),
            action_x=img_cfg.get("action_x"),
            action_y=img_cfg.get("action_y"),
            move_mouse_before_search=img_cfg.get("move_mouse_before_search", False),
        )

        def on_crop(new_path):
            img_cfg["target_image"] = new_path
            self._sync_rule_to_wp_image(temp_rule, idx)
            self._update_card_image_status(idx)

        def on_delete():
            if len(wp) >= 4 and isinstance(wp[3], dict):
                for key in ["target_image", "target_images", "confidence",
                            "search_radius", "action_x", "action_y",
                            "move_mouse_before_search"]:
                    wp[3].pop(key, None)
                if not wp[3]:
                    wp[3] = None
                    while len(wp) > 3 and wp[-1] is None:
                        wp.pop()
            self._save_config()
            self._update_card_image_status(idx)

        def on_change(new_path):
            img_cfg["target_image"] = new_path
            temp_rule.target_image = new_path
            self._save_config()
            self._update_card_image_status(idx)

        def on_search_radius_change():
            self._sync_rule_to_wp_image(temp_rule, idx)

        dialog = ImageCropDialog(
            self,
            img_path,
            on_crop=on_crop,
            on_delete=on_delete,
            on_change=on_change,
            rule=temp_rule,
            on_search_radius_change=on_search_radius_change,
        )
        # 다이얼로그 닫힌 후 최종 동기화
        dialog.wait_window()
        self._sync_rule_to_wp_image(temp_rule, idx)

    def _sync_rule_to_wp_image(self, rule, idx: int):
        """AutomationRule 설정 → wp[3] dict로 동기화"""
        waypoints = getattr(self._config, 'waypoints', []) or []
        if idx >= len(waypoints):
            return
        wp = waypoints[idx]
        if len(wp) < 4 or not isinstance(wp[3], dict):
            return
        cfg = wp[3]
        cfg["target_image"] = rule.target_image
        cfg["target_images"] = list(rule.target_images) if rule.target_images else []
        cfg["confidence"] = rule.confidence
        cfg["search_radius"] = rule.search_radius
        cfg["action_x"] = rule.action_x
        cfg["action_y"] = rule.action_y
        cfg["move_mouse_before_search"] = rule.move_mouse_before_search
        self._save_config()

    def _edit_character_image(self, idx: int):
        """캐릭터 이미지 편집 — ImageCropDialog 열기"""
        waypoints = getattr(self._config, 'waypoints', []) or []
        if idx >= len(waypoints):
            return
        wp = waypoints[idx]
        if len(wp) < 4 or not isinstance(wp[3], dict):
            return
        img_cfg = wp[3]
        img_path = img_cfg.get("character_image", "")
        if not img_path or not Path(img_path).exists():
            return

        # 임시 AutomationRule 생성 (ImageCropDialog용)
        temp_rule = AutomationRule(
            rule_id=f"wp_char_{idx}",
            action_type="click",
            target_image=img_path,
            confidence=0.65,
        )

        def on_crop(new_path):
            img_cfg["character_image"] = new_path
            self._save_config()
            self._update_card_image_status(idx)

        def on_delete():
            img_cfg.pop("character_image", None)
            if not img_cfg:
                wp[3] = None
                while len(wp) > 3 and wp[-1] is None:
                    wp.pop()
            self._save_config()
            self._update_card_image_status(idx)

        def on_change(new_path):
            img_cfg["character_image"] = new_path
            temp_rule.target_image = new_path
            self._save_config()
            self._update_card_image_status(idx)

        dialog = ImageCropDialog(
            self,
            img_path,
            on_crop=on_crop,
            on_delete=on_delete,
            on_change=on_change,
            rule=temp_rule,
        )
        dialog.wait_window()
        # 최종 동기화: character_image 경로 업데이트
        if len(wp) >= 4 and isinstance(wp[3], dict):
            wp[3]["character_image"] = temp_rule.target_image
            self._save_config()

    def _delete_waypoint_image(self, idx: int):
        """경유지 보스 이미지 삭제"""
        waypoints = getattr(self._config, 'waypoints', []) or []
        if idx >= len(waypoints):
            return
        wp = waypoints[idx]
        if len(wp) >= 4 and isinstance(wp[3], dict):
            # 보스 이미지 관련 필드만 삭제
            for key in ["target_image", "target_images", "confidence",
                        "search_radius", "action_x", "action_y",
                        "move_mouse_before_search"]:
                wp[3].pop(key, None)
            # dict가 비었으면 wp[3] 제거
            if not wp[3]:
                wp[3] = None
                while len(wp) > 3 and wp[-1] is None:
                    wp.pop()
            self._save_config()
            self._update_card_image_status(idx)
            logger.info(f"[좌표모드] 경유지{idx+1} 보스 이미지 삭제")

    def _delete_character_image(self, idx: int):
        """경유지 캐릭터 이미지 삭제"""
        waypoints = getattr(self._config, 'waypoints', []) or []
        if idx >= len(waypoints):
            return
        wp = waypoints[idx]
        if len(wp) >= 4 and isinstance(wp[3], dict):
            wp[3].pop("character_image", None)
            # dict가 비었으면 wp[3] 제거
            if not wp[3]:
                wp[3] = None
                while len(wp) > 3 and wp[-1] is None:
                    wp.pop()
            self._save_config()
            self._update_card_image_status(idx)
            logger.info(f"[좌표모드] 경유지{idx+1} 캐릭터 이미지 삭제")

    def _update_card_image_status(self, idx: int):
        """카드 이미지 상태 라벨 + 삭제 버튼 갱신 (캐릭터/보스 이미지 각각)"""
        cards = getattr(self, '_wp_cards', [])
        if not (0 <= idx < len(cards)):
            return
        waypoints = getattr(self._config, 'waypoints', []) or []
        if idx >= len(waypoints):
            return
        wp = waypoints[idx]
        img_cfg = wp[3] if len(wp) >= 4 and isinstance(wp[3], dict) else None

        # 캐릭터 이미지 상태 갱신
        char_img_name = ""
        if img_cfg and img_cfg.get("character_image"):
            char_img_name = Path(img_cfg["character_image"]).stem[:10]
        char_label = cards[idx]['char_img_status']
        del_char_btn = cards[idx]['del_char_img_btn']
        if char_img_name:
            char_label.configure(text=f" {char_img_name}", cursor="hand2")
            char_label.unbind("<Button-1>")
            char_label.bind("<Button-1>", lambda e, i=idx: self._edit_character_image(i))
            del_char_btn.pack(side="left", padx=(1, 0))
        else:
            char_label.configure(text="", cursor="")
            char_label.unbind("<Button-1>")
            del_char_btn.pack_forget()

        # 보스 이미지 상태 갱신
        boss_img_name = ""
        if img_cfg and img_cfg.get("target_image"):
            boss_img_name = Path(img_cfg["target_image"]).stem[:10]
        boss_label = cards[idx]['boss_img_status']
        del_boss_btn = cards[idx]['del_boss_img_btn']
        if boss_img_name:
            boss_label.configure(text=f" {boss_img_name}", cursor="hand2")
            boss_label.unbind("<Button-1>")
            boss_label.bind("<Button-1>", lambda e, i=idx: self._edit_waypoint_image(i))
            del_boss_btn.pack(side="left", padx=(1, 0))
        else:
            boss_label.configure(text="", cursor="")
            boss_label.unbind("<Button-1>")
            del_boss_btn.pack_forget()

    def _refresh_badges_async(self):
        """배지 갱신 (파일 I/O는 현재 스레드, UI 업데이트만 메인스레드로)
        반드시 백그라운드 스레드에서 호출할 것!"""
        try:
            cards = getattr(self, '_wp_cards', [])
            # 파일 I/O를 백그라운드에서 수행 → 결과만 수집
            badge_data = []
            for i in range(len(cards)):
                tiles = self._get_segment_map_tiles(i)
                has_map = tiles >= 0
                map_tag = f"{tiles}칸" if has_map else "미탐색"
                badge_color = COLORS["accent"] if has_map else COLORS["bg_card"]
                badge_text_color = "#ffffff" if has_map else COLORS["text_muted"]
                badge_data.append((i, map_tag, badge_color, badge_text_color))
            # UI 업데이트만 메인스레드에서 (파일 I/O 없음 → 즉시 완료)
            def _apply_badges(data=badge_data):
                try:
                    for idx, tag, bg, fg in data:
                        if idx < len(cards):
                            cards[idx]['map_badge'].configure(fg_color=bg)
                            cards[idx]['map_badge_label'].configure(text=f" {tag} ", text_color=fg)
                except Exception:
                    pass
            try:
                self.after(0, _apply_badges)
            except (tk.TclError, RuntimeError):
                pass
        except Exception:
            pass

    def _update_card_mapping_btn(self, idx: int, is_mapping: bool, mapping_type: str = "path"):
        """맵핑 버튼 상태만 업데이트 (1개 카드)"""
        cards = getattr(self, '_wp_cards', [])
        if 0 <= idx < len(cards):
            btn = cards[idx]['single_mapping_btn']
            if is_mapping:
                btn.configure(text="⏹ 중지",
                    fg_color=COLORS["accent_red"], hover_color="#d63a31",
                    text_color="#ffffff", border_width=0)
            else:
                btn.configure(text="맵핑테스트", state="normal",
                    fg_color="#1a3526", hover_color=COLORS["accent"],
                    text_color="#4ae168", border_width=1, border_color="#2d5a3d")

    def _reindex_cards(self, from_idx: int = 0):
        """카드 번호 + 모든 버튼 command 갱신 (구조 변경 후)"""
        cards = getattr(self, '_wp_cards', [])
        for i in range(from_idx, len(cards)):
            cards[i]['num_label'].configure(text=f"{i+1}.")
            cards[i]['del_btn'].configure(command=lambda idx=i: self._remove_waypoint(idx))
            cards[i]['move_up_btn'].configure(command=lambda idx=i: self._move_waypoint(idx, -1))
            cards[i]['move_down_btn'].configure(command=lambda idx=i: self._move_waypoint(idx, 1))
            cards[i]['copy_btn'].configure(command=lambda idx=i: self._copy_waypoint(idx))
            cards[i]['single_mapping_btn'].configure(command=lambda idx=i: self._run_single_mapping_test(idx))
            cards[i]['show_btn'].configure(command=lambda idx=i: self._show_map_for(idx))
            cards[i]['load_map_btn'].configure(command=lambda idx=i: self._load_map_for(idx))
            cards[i]['add_char_img_btn'].configure(command=lambda idx=i: self._add_character_image(idx))
            cards[i]['del_char_img_btn'].configure(command=lambda idx=i: self._delete_character_image(idx))
            cards[i]['add_boss_img_btn'].configure(command=lambda idx=i: self._add_boss_image(idx))
            cards[i]['del_boss_img_btn'].configure(command=lambda idx=i: self._delete_waypoint_image(idx))
            cards[i]['group_btn'].configure(command=lambda idx=i: self._toggle_group(idx))
            cards[i]['ungroup_btn'].configure(command=lambda idx=i: self._ungroup_card(idx))
            # test_btn 제거됨 — 맵핑테스트만 사용
            cards[i]['arr_key_btn'].configure(command=lambda idx=i: self._edit_arrival_key(idx))
            cards[i]['add_route_start_btn'].configure(command=lambda idx=i: self._add_route_start(idx))
            cards[i]['add_route_end_btn'].configure(command=lambda idx=i: self._add_route_end(idx))
            cards[i]['add_route_wall_btn'].configure(command=lambda idx=i: self._add_route_wall(idx))
            cards[i]['map_lock_btn'].configure(command=lambda idx=i: self._toggle_map_lock(idx))
            # 이름 클릭 바인딩 갱신
            cards[i]['name_label'].bind("<Button-1>", lambda e, idx=i: self._rename_waypoint(idx))
            # 드래그 핸들 바인딩 갱신
            dh = cards[i]['drag_handle']
            dh.bind("<ButtonPress-1>", lambda e, idx=i: self._on_drag_start(e, idx))
            dh.bind("<B1-Motion>", self._on_drag_motion)
            dh.bind("<ButtonRelease-1>", lambda e, idx=i: self._on_drag_end(e, idx))

    def _start_path_mapping_for(self, idx: int):
        """특정 경유지 경로맵핑 시작/중지 (출발→목표 직선 경로)"""
        if getattr(self, '_is_mapping', False):
            old_seg = getattr(self, '_current_segment_idx', -1)
            self._stop_mapping()
            self._update_card_mapping_btn(old_seg, False)
            return
        # 경로맵핑 모드 설정
        self._mapping_target_idx = idx
        self._mapping_mode = "path"  # 경로맵핑
        self._update_card_mapping_btn(idx, True, "path")
        self._start_mapping()

    def _start_full_mapping_for(self, idx: int):
        """특정 경유지 전체맵핑 시작/중지 (프런티어 탐색)"""
        if getattr(self, '_is_mapping', False):
            old_seg = getattr(self, '_current_segment_idx', -1)
            self._stop_mapping()
            self._update_card_mapping_btn(old_seg, False)
            return
        # 전체맵핑 모드 설정
        self._mapping_target_idx = idx
        self._mapping_mode = "full"  # 전체맵핑
        self._update_card_mapping_btn(idx, True, "full")
        self._start_mapping()

    def _show_map_for(self, idx: int):
        """특정 경유지 맵 보기 (읽기 전용 — 현재 맵 상태에 영향 없음)"""
        import os
        try:
            seg_name = self._get_segment_display_name(idx)
            self._append_log(f"🗺️ '{seg_name}' 맵 보기 요청")

            # 현재 활성 구간이면 메모리의 game_map 사용, 아니면 파일에서 임시 로드
            if idx == getattr(self, '_current_segment_idx', -1):
                view_map = self._game_map
            else:
                from ..player.game_map import GameMap
                map_path = self._get_segment_map_name(idx)
                view_map = GameMap(name=seg_name)
                if os.path.exists(map_path):
                    view_map.load(map_path)

            p = len(view_map.passable)
            b = len(view_map.blocked)
            if p + b > 0:
                bounds = view_map.get_bounds()
                self._append_log(f"🗺️ 맵 데이터: 이동={p}, 벽={b}, X({bounds['min_x']}~{bounds['max_x']}), Y({bounds['min_y']}~{bounds['max_y']})")
            else:
                self._append_log(f"🗺️ 맵 데이터: 이동={p}, 벽={b}")
            if p == 0 and b == 0 and not view_map.soft_blocked:
                from tkinter import messagebox
                self._append_log(f"⚠️ '{seg_name}' 맵 비어있음")
                messagebox.showinfo(
                    "맵 데이터 없음",
                    f"'{seg_name}' 맵 데이터가 없습니다.\n\n"
                    f"맵핑을 실행하거나, '맵 불러오기'로\n"
                    f"다른 맵 파일을 불러와주세요."
                )
                return
            self._show_map_preview(view_map, seg_idx=idx)
        except Exception as e:
            self._append_log(f"❌ 맵 보기 오류: {e}")
            import traceback
            logger.error(f"[맵핑] 맵 보기 오류: {traceback.format_exc()}")

    def _restore_map_for(self, idx: int):
        """특정 경유지 맵 되돌리기"""
        if getattr(self, '_is_mapping', False) or self._is_running:
            self._append_log("⚠️ 실행 중에는 맵 되돌리기 불가")
            return
        if idx != getattr(self, '_current_segment_idx', -1):
            if not self._switch_segment_map(idx):
                self._append_log("⚠️ 맵 구간 전환 실패")
                return
        self._restore_map()
        self._update_card_map_badge(idx)

    def _clear_all_maps(self):
        """모든 경유지 맵 한번에 초기화 (잠금 해제 포함)"""
        if getattr(self, '_is_mapping', False) or self._is_running:
            self._append_log("⚠️ 실행 중에는 맵 초기화 불가")
            return
        import os
        from tkinter import messagebox
        from ..player.game_map import GameMap
        from ..player.simple_pathfinder import SimplePathfinder
        from ..player.map_explorer import MapExplorer

        waypoints = getattr(self._config, 'waypoints', []) or []
        if not waypoints:
            self._append_log("⚠️ 경유지가 없습니다")
            return

        # 잠금된 경유지 목록 확인
        locked_names = []
        for i in range(len(waypoints)):
            if self._is_segment_map_locked(i):
                locked_names.append(self._get_segment_display_name(i))

        msg = f"전체 {len(waypoints)}개 경유지의 맵 데이터를 모두 삭제합니다.\n되돌릴 수 없습니다."
        if locked_names:
            msg += f"\n\n잠금된 맵 {len(locked_names)}개도 잠금 해제 후 초기화됩니다:\n" + ", ".join(locked_names[:5])
            if len(locked_names) > 5:
                msg += f" 외 {len(locked_names) - 5}개"
        if not messagebox.askyesno("전체 맵 초기화", msg):
            return

        cleared = 0
        for i in range(len(waypoints)):
            seg_name = self._get_segment_display_name(i)
            # 잠금 해제
            if self._is_segment_map_locked(i):
                wp = waypoints[i]
                if isinstance(wp, (list, tuple)) and len(wp) >= 4 and isinstance(wp[3], dict):
                    wp[3]['map_locked'] = False
                cards = getattr(self, '_wp_cards', [])
                if 0 <= i < len(cards):
                    btn = cards[i].get('map_lock_btn')
                    if btn:
                        btn.configure(text="🔓", fg_color="transparent",
                                      hover_color="#4c566a", text_color=COLORS["text_muted"],
                                      border_color=COLORS["border"])

            # map_file 공유 경로 해제 (원본 파일 보호)
            wp = waypoints[i]
            if isinstance(wp, (list, tuple)) and len(wp) >= 4 and isinstance(wp[3], dict):
                wp[3].pop('map_file', None)

            # 자체 맵 파일 삭제 — 모든 경로 패턴 검색
            map_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "maps")
            _rid = getattr(self, '_config_rule_id', None) or ""
            _rid_short = _rid.replace("rule_", "")[:8]
            base = self._config.name or "autosave"
            # 가능한 모든 파일명 패턴
            _patterns = [
                f"{_rid_short + '_' if _rid_short else ''}{i:02d}_{seg_name}_map.json",
                f"{_rid_short + '_' if _rid_short else ''}{i:02d}_{seg_name}_boss_map.json",
                f"{i:02d}_{seg_name}_map.json",
                f"{i:02d}_{seg_name}_boss_map.json",
                f"{base}_{seg_name}_map.json",
                f"{base}_{i:02d}_{seg_name}_map.json",
                f"{base}_{i}_{seg_name}_map.json",
            ]
            for pat in _patterns:
                for suffix in ["", ".backup.json", ".bak1", ".bak2", ".bak3"]:
                    if suffix in (".bak1", ".bak2", ".bak3"):
                        fp = os.path.join(map_dir, pat + suffix)
                    elif suffix == ".backup.json":
                        fp = os.path.join(map_dir, pat.replace("_map.json", "_map.backup.json").replace("_boss_map.json", "_boss_map.backup.json"))
                    else:
                        fp = os.path.join(map_dir, pat)
                    if os.path.exists(fp):
                        os.remove(fp)

            # 메모리 맵도 무조건 리셋 (현재 구간 아니어도)
            if i == getattr(self, '_current_segment_idx', -1):
                self._game_map = GameMap(name=f"{self._config.name or 'autosave'}_{seg_name}")
                self._map_pathfinder = SimplePathfinder(self._game_map)
                self._map_explorer = MapExplorer(self._game_map)

            self._update_card_map_badge(i)
            cleared += 1

        self._append_log(f"🗑️ 전체 맵 초기화 완료 ({cleared}개 경유지)", force=True)
        logger.info(f"[맵핑] 전체 맵 초기화: {cleared}개 경유지")

    def _clear_map_for(self, idx: int):
        """특정 경유지 맵 초기화"""
        if getattr(self, '_is_mapping', False) or self._is_running:
            self._append_log("⚠️ 실행 중에는 맵 초기화 불가")
            return
        import os
        from tkinter import messagebox
        from ..player.game_map import GameMap
        from ..player.simple_pathfinder import SimplePathfinder
        from ..player.map_explorer import MapExplorer

        # 잠금된 맵이면 경고
        if self._is_segment_map_locked(idx):
            seg_name = self._get_segment_display_name(idx)
            if not messagebox.askyesno("잠금된 맵", f"'{seg_name}' 맵이 잠금 상태입니다.\n잠금을 해제하고 초기화하시겠습니까?"):
                return
            # 잠금 해제
            waypoints = getattr(self._config, 'waypoints', []) or []
            if idx < len(waypoints) and len(waypoints[idx]) >= 4 and isinstance(waypoints[idx][3], dict):
                waypoints[idx][3]['map_locked'] = False
            cards = getattr(self, '_wp_cards', [])
            if 0 <= idx < len(cards):
                btn = cards[idx].get('map_lock_btn')
                if btn:
                    btn.configure(text="🔓", fg_color="transparent",
                                  hover_color="#4c566a", text_color=COLORS["text_muted"],
                                  border_color=COLORS["border"])

        seg_name = self._get_segment_display_name(idx)
        if not messagebox.askyesno("맵 초기화", f"'{seg_name}' 맵 데이터를 모두 삭제하시겠습니까?\n되돌릴 수 없습니다."):
            return

        if idx != getattr(self, '_current_segment_idx', -1):
            if not self._switch_segment_map(idx):
                self._append_log("⚠️ 맵 구간 전환 실패")
                return

        # map_file 공유 경로 먼저 해제 (원본 파일 보호)
        waypoints = getattr(self._config, 'waypoints', []) or []
        if idx < len(waypoints):
            wp = waypoints[idx]
            if isinstance(wp, (list, tuple)) and len(wp) >= 4 and isinstance(wp[3], dict):
                wp[3].pop('map_file', None)

        # map_file 해제 후 호출 → 경유지 자체 경로 반환 (원본 아님)
        map_path = self._get_segment_map_name(idx)
        if os.path.exists(map_path):
            os.remove(map_path)
        backup_path = map_path.replace("_map.json", "_map.backup.json")
        if os.path.exists(backup_path):
            os.remove(backup_path)

        self._game_map = GameMap(name=f"{self._config.name or 'autosave'}_{seg_name}")
        self._map_pathfinder = SimplePathfinder(self._game_map)
        self._map_explorer = MapExplorer(self._game_map)

        self._update_card_map_badge(idx)
        self._append_log(f"🗑️ '{seg_name}' 맵 초기화 완료", force=True)
        logger.info(f"[맵핑] '{seg_name}' 맵 초기화 완료")

    def _save_map_for(self, idx: int):
        """특정 경유지 맵을 파일로 저장"""
        if getattr(self, '_is_mapping', False) or self._is_running:
            self._append_log("⚠️ 실행 중에는 맵 저장 불가")
            return
        import os
        from tkinter import filedialog

        if idx != getattr(self, '_current_segment_idx', -1):
            if not self._switch_segment_map(idx):
                self._append_log("⚠️ 맵 구간 전환 실패")
                return

        seg_name = self._get_segment_display_name(idx)
        map_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "maps")
        os.makedirs(map_dir, exist_ok=True)
        filepath = filedialog.asksaveasfilename(
            initialdir=map_dir,
            initialfile=f"{seg_name}_map.json",
            defaultextension=".json",
            filetypes=[("JSON 파일", "*.json")],
            title=f"'{seg_name}' 맵 저장"
        )
        if filepath:
            self._game_map.save(filepath)
            logger.info(f"[맵핑] '{seg_name}' 맵 파일 저장: {filepath}")
            from tkinter import messagebox
            messagebox.showinfo("저장 완료", f"'{seg_name}' 맵이 저장되었습니다.\n{filepath}")

    def _load_map_for(self, idx: int):
        """특정 경유지에 맵 파일 불러오기 (병합)"""
        if getattr(self, '_is_mapping', False) or self._is_running:
            self._append_log("⚠️ 실행 중에는 맵 불러오기 불가")
            return
        import os
        from tkinter import filedialog, messagebox

        try:
            seg_name = self._get_segment_display_name(idx)
            self._append_log(f"📂 '{seg_name}' 맵 불러오기 시작")

            if idx != getattr(self, '_current_segment_idx', -1):
                if not self._switch_segment_map(idx):
                    self._append_log("⚠️ 맵 구간 전환 실패")
                    return

            map_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "maps")
            os.makedirs(map_dir, exist_ok=True)
            filepath = filedialog.askopenfilename(
                initialdir=map_dir,
                filetypes=[("JSON 파일", "*.json")],
                title=f"'{seg_name}'에 맵 불러오기"
            )
            if not filepath:
                self._append_log("📂 파일 선택 취소")
                return

            self._append_log(f"📂 파일 선택: {os.path.basename(filepath)}")
            # 기존 맵 병합이 아닌 교체 (선택 파일로 완전 교체)
            from ..player.game_map import GameMap
            from ..player.simple_pathfinder import SimplePathfinder
            from ..player.map_explorer import MapExplorer
            self._game_map = GameMap(name=f"{self._config.name or 'autosave'}_{seg_name}")
            self._map_pathfinder = SimplePathfinder(self._game_map)
            self._map_explorer = MapExplorer(self._game_map)
            if self._game_map.load(filepath):
                passable_count = len(self._game_map.passable)
                blocked_count = len(self._game_map.blocked)
                total_tiles = passable_count + blocked_count

                self._append_log(f"📂 로드 완료: 이동={passable_count}, 벽={blocked_count}, 합계={total_tiles}")

                if total_tiles == 0:
                    self._append_log(f"⚠️ 선택한 파일에 맵 데이터 없음")
                    messagebox.showwarning(
                        "맵 데이터 없음",
                        f"선택한 파일에 맵 데이터가 없습니다.\n\n"
                        f"파일: {os.path.basename(filepath)}\n"
                        f"이동가능: {passable_count}개, 벽: {blocked_count}개"
                    )
                    return

                # 공유 맵 파일 경로 저장 (복사 안 함 — 원본 직접 참조)
                waypoints = getattr(self._config, 'waypoints', []) or []
                wp = waypoints[idx] if idx < len(waypoints) else None
                if wp:
                    if len(wp) < 4:
                        wp.append({})
                    if not isinstance(wp[3], dict):
                        wp[3] = {}
                    wp[3]['map_file'] = filepath
                self._update_card_map_badge(idx)
                logger.info(f"[맵핑] '{seg_name}' 맵 공유 로드: {filepath} "
                           f"(이동가능={passable_count}, 벽={blocked_count})")
                # 맵 미리보기 자동 표시
                self._show_map_preview()
            else:
                self._append_log(f"❌ 맵 파일 로드 실패")
                messagebox.showerror("오류", "맵 파일을 불러올 수 없습니다.")
        except Exception as e:
            self._append_log(f"❌ 맵 불러오기 오류: {e}")
            logger.error(f"[맵핑] 맵 불러오기 오류: {e}")
            try:
                messagebox.showerror("오류", f"맵 불러오기 실패:\n{e}")
            except Exception:
                pass

    def _save_and_feedback(self):
        """설정 저장 + 시각적 피드백"""
        self._apply_settings()
        self._save_config()
        if hasattr(self, '_save_btn'):
            self._save_btn.configure(text="✓ 저장됨", fg_color=COLORS["success"])
            self.after(1200, lambda: self._save_btn.configure(
                text="💾 설정 저장", fg_color=COLORS["accent"]))

    def _update_waypoint_coord(self, index: int, field: int, value: str):
        """경유지 좌표 inline 편집 반영 (field: 0=x, 1=y)"""
        waypoints = getattr(self._config, 'waypoints', []) or []
        if 0 <= index < len(waypoints):
            try:
                val = int(value) if value.strip() else 0
                waypoints[index][field] = val
            except ValueError:
                pass

    # ── 경로 좌표 (출발/도착 독립 관리) ──────────────────────

    def _ensure_wp_config(self, wp):
        """wp[3] config dict가 없으면 생성"""
        if len(wp) < 4:
            wp.append({})
        if not isinstance(wp[3], dict):
            wp[3] = {}
        return wp[3]

    def _get_route_starts(self, wp) -> list:
        """출발좌표 리스트 반환"""
        cfg = self._ensure_wp_config(wp)
        if "route_starts" not in cfg:
            cfg["route_starts"] = []
        return cfg["route_starts"]

    def _get_route_ends(self, wp) -> list:
        """도착좌표 리스트 반환 (하위호환: 기존 wp[0],wp[1]에서 마이그레이션)"""
        cfg = self._ensure_wp_config(wp)
        if "route_ends" not in cfg:
            end_x = wp[0] if len(wp) >= 1 else None
            end_y = wp[1] if len(wp) >= 2 else None
            has = end_x is not None and end_y is not None
            if has:
                cfg["route_ends"] = [{"x": end_x, "y": end_y}]
            else:
                cfg["route_ends"] = []
        return cfg["route_ends"]

    def _get_route_walls(self, wp) -> list:
        """벽좌표 리스트 반환"""
        cfg = self._ensure_wp_config(wp)
        if "route_walls" not in cfg:
            cfg["route_walls"] = []
        return cfg["route_walls"]

    def _sync_wp_from_route_ends(self, wp_idx):
        """route_ends[0] → wp[0], wp[1] 동기화"""
        waypoints = getattr(self._config, 'waypoints', []) or []
        if 0 <= wp_idx < len(waypoints):
            wp = waypoints[wp_idx]
            ends = self._get_route_ends(wp)
            if ends:
                wp[0] = ends[0].get("x", 0) or 0
                wp[1] = ends[0].get("y", 0) or 0
            else:
                wp[0] = 0
                wp[1] = 0

    def _build_route_rows(self, parent, wp_idx, wp):
        """경로 좌표 전체 빌드 (출발 리스트 + 도착 리스트 + 벽 리스트)"""
        starts = self._get_route_starts(wp)
        ends = self._get_route_ends(wp)
        walls = self._get_route_walls(wp)

        _ew = 55
        _eh = 24
        _font = ctk.CTkFont(size=11)
        _lbl_font = ctk.CTkFont(size=12, weight="bold")

        # 출발 좌표들
        for s_idx, coord in enumerate(starts):
            row = ctk.CTkFrame(parent, fg_color="transparent")
            row.pack(fill="x", pady=(1, 0))
            ctk.CTkLabel(row, text="출발", font=_lbl_font,
                         text_color="#9b59b6", width=30).pack(side="left")
            x_var = ctk.StringVar(value=str(coord.get("x", "")) if coord.get("x") is not None else "")
            x_e = ctk.CTkEntry(row, textvariable=x_var, width=_ew, height=_eh,
                               font=_font, placeholder_text="X",
                               fg_color=COLORS["bg_card"], border_color=COLORS["border"])
            x_e.pack(side="left", padx=(2, 0))
            x_e.bind("<FocusOut>", lambda e, wi=wp_idx, si=s_idx, v=x_var:
                     self._update_route_start(wi, si, "x", v.get()))
            y_var = ctk.StringVar(value=str(coord.get("y", "")) if coord.get("y") is not None else "")
            y_e = ctk.CTkEntry(row, textvariable=y_var, width=_ew, height=_eh,
                               font=_font, placeholder_text="Y",
                               fg_color=COLORS["bg_card"], border_color=COLORS["border"])
            y_e.pack(side="left", padx=(2, 0))
            y_e.bind("<FocusOut>", lambda e, wi=wp_idx, si=s_idx, v=y_var:
                     self._update_route_start(wi, si, "y", v.get()))
            ctk.CTkButton(row, text="✕", width=22, height=_eh,
                          font=ctk.CTkFont(size=10),
                          fg_color="transparent", hover_color=COLORS["accent_red"],
                          text_color=COLORS["text_muted"],
                          command=lambda wi=wp_idx, si=s_idx: self._remove_route_start(wi, si)
                          ).pack(side="left", padx=(2, 0))

        # 도착 좌표들
        for e_idx, coord in enumerate(ends):
            _is_enabled = coord.get("enabled", True)
            row = ctk.CTkFrame(parent, fg_color="transparent")
            row.pack(fill="x", pady=(1, 0))
            ctk.CTkLabel(row, text="도착", font=_lbl_font,
                         text_color="#50c878" if _is_enabled else "#666666",
                         width=30).pack(side="left")
            x_var = ctk.StringVar(value=str(coord.get("x", "")) if coord.get("x") is not None else "")
            x_e = ctk.CTkEntry(row, textvariable=x_var, width=_ew, height=_eh,
                               font=_font, placeholder_text="X",
                               fg_color=COLORS["bg_card"], border_color=COLORS["border"],
                               state="normal" if _is_enabled else "disabled")
            x_e.pack(side="left", padx=(2, 0))
            x_e.bind("<FocusOut>", lambda e, wi=wp_idx, ei=e_idx, v=x_var:
                     self._update_route_end(wi, ei, "x", v.get()))
            y_var = ctk.StringVar(value=str(coord.get("y", "")) if coord.get("y") is not None else "")
            y_e = ctk.CTkEntry(row, textvariable=y_var, width=_ew, height=_eh,
                               font=_font, placeholder_text="Y",
                               fg_color=COLORS["bg_card"], border_color=COLORS["border"],
                               state="normal" if _is_enabled else "disabled")
            y_e.pack(side="left", padx=(2, 0))
            y_e.bind("<FocusOut>", lambda e, wi=wp_idx, ei=e_idx, v=y_var:
                     self._update_route_end(wi, ei, "y", v.get()))
            # ON/OFF 토글 버튼
            ctk.CTkButton(row, text="ON" if _is_enabled else "OFF", width=30, height=_eh,
                          font=ctk.CTkFont(size=9, weight="bold"),
                          fg_color="#1a3526" if _is_enabled else "#3a1a1a",
                          hover_color="#2d5a3d" if _is_enabled else "#5a2d2d",
                          text_color="#4ae168" if _is_enabled else "#e05050",
                          command=lambda wi=wp_idx, ei=e_idx: self._toggle_route_end_enabled(wi, ei)
                          ).pack(side="left", padx=(2, 0))
            ctk.CTkButton(row, text="✕", width=22, height=_eh,
                          font=ctk.CTkFont(size=10),
                          fg_color="transparent", hover_color=COLORS["accent_red"],
                          text_color=COLORS["text_muted"],
                          command=lambda wi=wp_idx, ei=e_idx: self._remove_route_end(wi, ei)
                          ).pack(side="left", padx=(2, 0))

        # 벽 좌표들
        for w_idx, coord in enumerate(walls):
            row = ctk.CTkFrame(parent, fg_color="transparent")
            row.pack(fill="x", pady=(1, 0))
            ctk.CTkLabel(row, text="벽", font=_lbl_font,
                         text_color="#e05050", width=30).pack(side="left")
            x_var = ctk.StringVar(value=str(coord.get("x", "")) if coord.get("x") is not None else "")
            x_e = ctk.CTkEntry(row, textvariable=x_var, width=_ew, height=_eh,
                               font=_font, placeholder_text="X",
                               fg_color=COLORS["bg_card"], border_color=COLORS["border"])
            x_e.pack(side="left", padx=(2, 0))
            x_e.bind("<FocusOut>", lambda e, wi=wp_idx, wi2=w_idx, v=x_var:
                     self._update_route_wall(wi, wi2, "x", v.get()))
            y_var = ctk.StringVar(value=str(coord.get("y", "")) if coord.get("y") is not None else "")
            y_e = ctk.CTkEntry(row, textvariable=y_var, width=_ew, height=_eh,
                               font=_font, placeholder_text="Y",
                               fg_color=COLORS["bg_card"], border_color=COLORS["border"])
            y_e.pack(side="left", padx=(2, 0))
            y_e.bind("<FocusOut>", lambda e, wi=wp_idx, wi2=w_idx, v=y_var:
                     self._update_route_wall(wi, wi2, "y", v.get()))
            ctk.CTkButton(row, text="✕", width=22, height=_eh,
                          font=ctk.CTkFont(size=10),
                          fg_color="transparent", hover_color=COLORS["accent_red"],
                          text_color=COLORS["text_muted"],
                          command=lambda wi=wp_idx, wi2=w_idx: self._remove_route_wall(wi, wi2)
                          ).pack(side="left", padx=(2, 0))

    def _add_route_start(self, wp_idx):
        """출발좌표 추가"""
        waypoints = getattr(self._config, 'waypoints', []) or []
        if 0 <= wp_idx < len(waypoints):
            starts = self._get_route_starts(waypoints[wp_idx])
            starts.append({"x": 0, "y": 0})
            self._save_config()
            self._rebuild_card(wp_idx)

    def _add_route_end(self, wp_idx):
        """도착좌표 추가"""
        waypoints = getattr(self._config, 'waypoints', []) or []
        if 0 <= wp_idx < len(waypoints):
            ends = self._get_route_ends(waypoints[wp_idx])
            ends.append({"x": 0, "y": 0})
            self._save_config()
            self._rebuild_card(wp_idx)

    def _remove_route_start(self, wp_idx, s_idx):
        """출발좌표 삭제"""
        waypoints = getattr(self._config, 'waypoints', []) or []
        if 0 <= wp_idx < len(waypoints):
            starts = self._get_route_starts(waypoints[wp_idx])
            if 0 <= s_idx < len(starts):
                starts.pop(s_idx)
                self._save_config()
                self._rebuild_card(wp_idx)

    def _remove_route_end(self, wp_idx, e_idx):
        """도착좌표 삭제"""
        waypoints = getattr(self._config, 'waypoints', []) or []
        if 0 <= wp_idx < len(waypoints):
            ends = self._get_route_ends(waypoints[wp_idx])
            if 0 <= e_idx < len(ends):
                ends.pop(e_idx)
                self._sync_wp_from_route_ends(wp_idx)
                self._save_config()
                self._rebuild_card(wp_idx)

    def _update_route_start(self, wp_idx, s_idx, field, value):
        """출발좌표 업데이트 (field: x/y)"""
        waypoints = getattr(self._config, 'waypoints', []) or []
        if 0 <= wp_idx < len(waypoints):
            starts = self._get_route_starts(waypoints[wp_idx])
            if 0 <= s_idx < len(starts):
                try:
                    starts[s_idx][field] = int(value) if value.strip() else 0
                    self._save_config()
                except ValueError:
                    pass

    def _update_route_end(self, wp_idx, e_idx, field, value):
        """도착좌표 업데이트 (field: x/y)"""
        waypoints = getattr(self._config, 'waypoints', []) or []
        if 0 <= wp_idx < len(waypoints):
            ends = self._get_route_ends(waypoints[wp_idx])
            if 0 <= e_idx < len(ends):
                try:
                    ends[e_idx][field] = int(value) if value.strip() else 0
                    self._save_config()
                except ValueError:
                    pass
                if e_idx == 0:
                    self._sync_wp_from_route_ends(wp_idx)

    def _toggle_route_end_enabled(self, wp_idx, e_idx):
        """도착좌표 ON/OFF 토글 (OFF 시 이동/맵핑에서 제외, 벽으로 처리)"""
        waypoints = getattr(self._config, 'waypoints', []) or []
        if 0 <= wp_idx < len(waypoints):
            ends = self._get_route_ends(waypoints[wp_idx])
            if 0 <= e_idx < len(ends):
                ends[e_idx]["enabled"] = not ends[e_idx].get("enabled", True)
                self._save_config()
                self._rebuild_card(wp_idx)

    def _add_route_wall(self, wp_idx):
        """벽좌표 추가"""
        waypoints = getattr(self._config, 'waypoints', []) or []
        if 0 <= wp_idx < len(waypoints):
            walls = self._get_route_walls(waypoints[wp_idx])
            walls.append({"x": 0, "y": 0})
            self._save_config()
            self._rebuild_card(wp_idx)

    def _remove_route_wall(self, wp_idx, w_idx):
        """벽좌표 삭제"""
        waypoints = getattr(self._config, 'waypoints', []) or []
        if 0 <= wp_idx < len(waypoints):
            walls = self._get_route_walls(waypoints[wp_idx])
            if 0 <= w_idx < len(walls):
                walls.pop(w_idx)
                self._save_config()
                self._rebuild_card(wp_idx)

    def _update_route_wall(self, wp_idx, w_idx, field, value):
        """벽좌표 업데이트 (field: x/y)"""
        waypoints = getattr(self._config, 'waypoints', []) or []
        if 0 <= wp_idx < len(waypoints):
            walls = self._get_route_walls(waypoints[wp_idx])
            if 0 <= w_idx < len(walls):
                try:
                    walls[w_idx][field] = int(value) if value.strip() else 0
                    self._save_config()
                except ValueError:
                    pass

    def _rebuild_card(self, wp_idx):
        """특정 경유지 카드의 경로 섹션만 재빌드"""
        waypoints = getattr(self._config, 'waypoints', []) or []
        cards = getattr(self, '_wp_cards', [])
        if not (0 <= wp_idx < len(waypoints)) or not (0 <= wp_idx < len(cards)):
            return
        route_frame = cards[wp_idx].get('route_frame')
        if route_frame is None:
            return
        # 기존 경로 행 전부 삭제
        for child in route_frame.winfo_children():
            child.destroy()
        # 경로 행 재빌드
        wp = waypoints[wp_idx]
        self._build_route_rows(route_frame, wp_idx, wp)

    def _exec_arrival_keys(self, keys_list: list):
        """도착 키 리스트 실행 (반복, 대기시간, 랜덤 지원)"""
        import pyautogui
        import random as _rnd
        # 키 입력 전 0.5초 대기 (도착 직후 안정화)
        self._stop_event.wait(0.5)
        if self._stop_event.is_set():
            return
        for kd in keys_list:
            if self._stop_event.is_set():
                break
            key_name = kd.get("key", "")
            if not key_name:
                continue
            repeat_count = max(1, kd.get("repeat_count", 1))
            wait_after = kd.get("wait_after", 0.3)
            wait_random = kd.get("wait_random", False)
            wait_random_range = kd.get("wait_random_range", 0.3)
            repeat_delay = kd.get("repeat_delay", 0.5)
            repeat_delay_random = kd.get("repeat_delay_random", False)
            repeat_delay_random_range = kd.get("repeat_delay_random_range", 0.3)
            for rep in range(repeat_count):
                if self._stop_event.is_set():
                    break
                try:
                    # keyDown + hold + keyUp (게임이 즉시 press를 씹는 경우 방지)
                    pyautogui.keyDown(key_name)
                    time.sleep(0.05)
                    pyautogui.keyUp(key_name)
                    self._key_press_count += 1
                except Exception as _ke:
                    logger.error(f"[도착키] '{key_name}' 입력 오류: {_ke}")
                # 반복 사이 대기
                if rep < repeat_count - 1:
                    d = repeat_delay
                    if repeat_delay_random:
                        d += _rnd.uniform(-repeat_delay_random_range, repeat_delay_random_range)
                        d = max(0, d)
                    if d > 0:
                        self._stop_event.wait(d)
            # 키 사이 대기
            w = wait_after
            if wait_random:
                w += _rnd.uniform(-wait_random_range, wait_random_range)
                w = max(0, w)
            if w > 0:
                self._stop_event.wait(w)

    def _edit_arrival_key(self, index: int):
        """도착 키 설정 팝업 — 액션 추가 키입력과 동일한 옵션"""
        try:
            self.__edit_arrival_key_impl(index)
        except Exception as e:
            logger.error(f"[도착키] 팝업 오류: {e}", exc_info=True)
            from tkinter import messagebox
            messagebox.showerror("오류", f"도착 키 설정 오류:\n{e}")

    def __edit_arrival_key_impl(self, index: int):
        waypoints = getattr(self._config, 'waypoints', []) or []
        if index < 0 or index >= len(waypoints):
            return
        wp = waypoints[index]
        if len(wp) < 4 or not isinstance(wp[3], dict):
            while len(wp) < 3:
                wp.append(None)
            if len(wp) < 4:
                wp.append({})
            elif not isinstance(wp[3], dict):
                wp[3] = {}
        wp_cfg = wp[3]
        # 기존 단일 키 문자열 → 리스트 마이그레이션
        if "arrival_key" in wp_cfg and "arrival_keys" not in wp_cfg:
            old = wp_cfg.pop("arrival_key", "")
            if old:
                wp_cfg["arrival_keys"] = [{"key": k.strip(), "wait_after": 0.3,
                    "wait_random": False, "wait_random_range": 0.3,
                    "repeat_count": 1, "repeat_delay": 0.5,
                    "repeat_delay_random": False, "repeat_delay_random_range": 0.3}
                    for k in old.split(",") if k.strip()]
        keys_list = wp_cfg.get("arrival_keys", [])

        dlg = ctk.CTkToplevel(self)
        dlg.title(f"도착 키 설정 — 경유지 {index + 1}")
        dlg.geometry("380x450")
        dlg.resizable(False, True)
        dlg.configure(fg_color=COLORS["bg_dark"])
        dlg.transient(self)
        dlg.after(100, lambda: dlg.grab_set())
        dlg.update_idletasks()
        _dx = (dlg.winfo_screenwidth() - 380) // 2
        _dy = (dlg.winfo_screenheight() - 450) // 2
        dlg.geometry(f"+{_dx}+{_dy}")
        dlg.lift()
        dlg.focus_force()

        # 로컬 복사본
        local_keys = [dict(k) for k in keys_list]

        # 스크롤 영역
        list_frame = ctk.CTkScrollableFrame(dlg, fg_color=COLORS["bg_dark"],
                                            height=300)
        list_frame.pack(fill="both", expand=True, padx=10, pady=(10, 5))

        def refresh_list():
            for w in list_frame.winfo_children():
                w.destroy()
            if not local_keys:
                ctk.CTkLabel(list_frame, text="키가 없습니다. 아래에서 추가하세요.",
                             font=ctk.CTkFont(size=11),
                             text_color=COLORS["text_secondary"]).pack(pady=20)
                return
            for ki, kd in enumerate(local_keys):
                row = ctk.CTkFrame(list_frame, fg_color=COLORS["bg_card"],
                                   corner_radius=6, height=36)
                row.pack(fill="x", padx=4, pady=2)
                row.pack_propagate(False)
                # 키 이름
                ctk.CTkLabel(row, text=kd.get("key", "?").upper(),
                             font=ctk.CTkFont(size=12, weight="bold"),
                             text_color=COLORS["accent"], width=60).pack(side="left", padx=(8, 4))
                # 대기시간
                _wr = kd.get("wait_random", False)
                _wt = kd.get("wait_after", 0.3)
                ctk.CTkButton(row, text=f"{_wt:.1f}s{'*' if _wr else ''}",
                    width=46, height=24, font=ctk.CTkFont(size=10),
                    fg_color=COLORS["success"] if _wr else "#0d1f17",
                    hover_color="#27ae60",
                    text_color="white" if _wr else COLORS["text_secondary"],
                    corner_radius=4,
                    command=lambda k=ki: edit_wait(k)).pack(side="left", padx=2)
                # 반복횟수
                _rc = kd.get("repeat_count", 1)
                ctk.CTkButton(row, text=f"x{_rc}",
                    width=34, height=24, font=ctk.CTkFont(size=10),
                    fg_color=COLORS["accent_blue"] if _rc > 1 else "#0d1f17",
                    hover_color="#2563eb",
                    text_color="white" if _rc > 1 else COLORS["text_secondary"],
                    corner_radius=4,
                    command=lambda k=ki: edit_repeat(k)).pack(side="left", padx=2)
                # 삭제
                ctk.CTkButton(row, text="✕", width=24, height=24,
                    font=ctk.CTkFont(size=10),
                    fg_color="transparent", hover_color="#5a2020",
                    text_color="#e06060",
                    command=lambda k=ki: del_key(k)).pack(side="right", padx=(0, 6))
                # 순서 이동
                if ki > 0:
                    ctk.CTkButton(row, text="▲", width=22, height=24,
                        font=ctk.CTkFont(size=9), fg_color="transparent",
                        hover_color=COLORS["bg_card_hover"],
                        text_color=COLORS["text_secondary"],
                        command=lambda k=ki: move_key(k, -1)).pack(side="right")
                if ki < len(local_keys) - 1:
                    ctk.CTkButton(row, text="▼", width=22, height=24,
                        font=ctk.CTkFont(size=9), fg_color="transparent",
                        hover_color=COLORS["bg_card_hover"],
                        text_color=COLORS["text_secondary"],
                        command=lambda k=ki: move_key(k, 1)).pack(side="right")

        def add_key():
            kid = KeyInputDialog(dlg)
            captured = kid.get_key()
            if captured:
                local_keys.append({"key": captured.lower().strip(),
                    "wait_after": 0.3, "wait_random": False,
                    "wait_random_range": 0.3, "repeat_count": 1,
                    "repeat_delay": 0.5, "repeat_delay_random": False,
                    "repeat_delay_random_range": 0.3})
                refresh_list()

        def del_key(ki):
            if 0 <= ki < len(local_keys):
                local_keys.pop(ki)
                refresh_list()

        def move_key(ki, direction):
            ni = ki + direction
            if 0 <= ni < len(local_keys):
                local_keys[ki], local_keys[ni] = local_keys[ni], local_keys[ki]
                refresh_list()

        def edit_wait(ki):
            kd = local_keys[ki]
            wd = ctk.CTkToplevel(dlg)
            wd.title("대기시간 설정")
            wd.geometry("300x280")
            wd.transient(dlg); wd.grab_set()
            wd.configure(fg_color=COLORS["bg_dark"])

            ctk.CTkLabel(wd, text="대기시간 (초):", font=ctk.CTkFont(size=12)).pack(pady=(15, 5))
            we = ctk.CTkEntry(wd, width=100)
            we.insert(0, str(kd.get("wait_after", 0.3)))
            we.pack()

            rv = ctk.BooleanVar(value=kd.get("wait_random", False))
            ctk.CTkCheckBox(wd, text="랜덤 적용", variable=rv).pack(pady=10)

            ctk.CTkLabel(wd, text="랜덤 범위 (±초):", font=ctk.CTkFont(size=12)).pack(pady=(5, 5))
            re = ctk.CTkEntry(wd, width=100)
            re.insert(0, str(kd.get("wait_random_range", 0.3)))
            re.pack()

            def sw():
                try:
                    kd["wait_after"] = float(we.get())
                    kd["wait_random"] = rv.get()
                    kd["wait_random_range"] = float(re.get())
                    wd.destroy()
                    refresh_list()
                except ValueError:
                    pass

            bf = ctk.CTkFrame(wd, fg_color="transparent")
            bf.pack(pady=20)
            ctk.CTkButton(bf, text="저장", width=100, height=36,
                          command=sw).pack(side="left", padx=10)
            ctk.CTkButton(bf, text="취소", width=100, height=36,
                          fg_color=COLORS["bg_card"],
                          command=wd.destroy).pack(side="left", padx=10)

        def edit_repeat(ki):
            kd = local_keys[ki]
            rd = ctk.CTkToplevel(dlg)
            rd.title("반복 설정")
            rd.geometry("350x420")
            rd.resizable(False, False)
            rd.configure(fg_color=COLORS["bg_dark"])
            rd.transient(dlg); rd.grab_set()

            mf = ctk.CTkFrame(rd, fg_color="transparent")
            mf.pack(fill="both", expand=True, padx=20, pady=15)

            ctk.CTkLabel(mf, text="반복 횟수",
                         font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", pady=(0, 5))
            ce = ctk.CTkEntry(mf, width=200, height=38, font=ctk.CTkFont(size=14))
            ce.insert(0, str(kd.get("repeat_count", 1)))
            ce.pack(anchor="w")
            ctk.CTkLabel(mf, text="1 = 1회 실행, 2 = 2회 반복...",
                         font=ctk.CTkFont(size=11),
                         text_color=COLORS["text_secondary"]).pack(anchor="w", pady=(5, 0))

            ctk.CTkLabel(mf, text="반복 대기시간 (초)",
                         font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", pady=(15, 5))
            de = ctk.CTkEntry(mf, width=200, height=38, font=ctk.CTkFont(size=14))
            de.insert(0, f"{kd.get('repeat_delay', 0.5):.2f}")
            de.pack(anchor="w")
            ctk.CTkLabel(mf, text="반복 사이의 대기시간",
                         font=ctk.CTkFont(size=11),
                         text_color=COLORS["text_secondary"]).pack(anchor="w", pady=(5, 0))

            drv = ctk.BooleanVar(value=kd.get("repeat_delay_random", False))
            ctk.CTkCheckBox(mf, text="랜덤시간 활성화", variable=drv,
                            font=ctk.CTkFont(size=13)).pack(anchor="w", pady=(10, 5))

            drf = ctk.CTkFrame(mf, fg_color="transparent")
            drf.pack(anchor="w", pady=5)
            ctk.CTkLabel(drf, text="±범위:", font=ctk.CTkFont(size=12)).pack(side="left")
            dre = ctk.CTkEntry(drf, width=100, height=32, font=ctk.CTkFont(size=13))
            dre.insert(0, f"{kd.get('repeat_delay_random_range', 0.3):.2f}")
            dre.pack(side="left", padx=(5, 10))
            ctk.CTkLabel(drf, text="초", font=ctk.CTkFont(size=12),
                         text_color=COLORS["text_secondary"]).pack(side="left")

            def sr():
                try:
                    c = int(ce.get().strip())
                    d = float(de.get().strip().replace(',', '.'))
                    dr = float(dre.get().strip().replace(',', '.'))
                    if c < 1 or d < 0 or dr < 0:
                        return
                    kd["repeat_count"] = c
                    kd["repeat_delay"] = d
                    kd["repeat_delay_random"] = drv.get()
                    kd["repeat_delay_random_range"] = dr
                    rd.destroy()
                    refresh_list()
                except ValueError:
                    pass

            bf = ctk.CTkFrame(rd, fg_color="transparent")
            bf.pack(pady=10)
            ctk.CTkButton(bf, text="저장", width=100, height=36,
                          font=ctk.CTkFont(size=13, weight="bold"),
                          command=sr).pack(side="left", padx=8)
            ctk.CTkButton(bf, text="취소", width=100, height=36,
                          font=ctk.CTkFont(size=13, weight="bold"),
                          fg_color=COLORS["bg_card"],
                          command=rd.destroy).pack(side="left", padx=8)

        refresh_list()

        # 하단 버튼
        bot = ctk.CTkFrame(dlg, fg_color="transparent")
        bot.pack(fill="x", padx=10, pady=(5, 10))

        ctk.CTkButton(bot, text="+ 키 추가", width=100, height=32,
                      font=ctk.CTkFont(size=12, weight="bold"),
                      fg_color="#2a2518", hover_color="#4a3828",
                      text_color="#e8d090",
                      command=add_key).pack(side="left", padx=4)

        def save_all():
            wp_cfg["arrival_keys"] = local_keys
            wp_cfg.pop("arrival_key", None)  # 구버전 키 제거
            self._save_config()
            dlg.destroy()

        ctk.CTkButton(bot, text="저장", width=80, height=32,
                      font=ctk.CTkFont(size=12, weight="bold"),
                      fg_color=COLORS["accent_blue"],
                      command=save_all).pack(side="right", padx=4)
        ctk.CTkButton(bot, text="취소", width=80, height=32,
                      font=ctk.CTkFont(size=12, weight="bold"),
                      fg_color=COLORS["bg_card"],
                      command=dlg.destroy).pack(side="right", padx=4)

    def _format_coord_region(self, region) -> str:
        """좌표 영역을 텍스트로 포맷"""
        if region and len(region) == 4:
            return f"({region[0]},{region[1]}) ~ ({region[2]},{region[3]})"
        return "미설정"

    def _select_coord_region(self, coord_type: str):
        """좌표 OCR 영역 선택"""
        from .analyzer_view import ScreenRegionSelector

        existing_region = getattr(self._config, f'coord_{coord_type}_region', None)

        def on_region_select(x1, y1, x2, y2):
            region = [x1, y1, x2, y2]
            if coord_type == "x":
                self._config.coord_x_region = region
                self._coord_x_label.configure(text=self._format_coord_region(region))
            else:
                self._config.coord_y_region = region
                self._coord_y_label.configure(text=self._format_coord_region(region))
            logger.info(f"[좌표모드] {coord_type.upper()} 영역 설정: {region}")
            self._save_config()

        def on_cancel():
            pass

        ScreenRegionSelector(self, on_region_select, on_cancel, existing_region=existing_region)

    def _test_coord_region(self, coord_type: str):
        """좌표 영역 테스트 (템플릿 매칭)"""
        region = getattr(self._config, f'coord_{coord_type}_region', None)
        result_label = self._coord_x_result if coord_type == "x" else self._coord_y_result

        if not region or len(region) != 4:
            result_label.configure(text="영역 미설정", text_color="#bf616a")
            return

        result_label.configure(text="읽는중...", text_color=COLORS["text_secondary"])
        self.update()

        def run_test():
            try:
                from ..utils.digit_templates import get_digit_matcher
                matcher = get_digit_matcher()

                if not matcher.has_all_templates():
                    missing = matcher.get_missing_digits()
                    self.after(0, lambda: result_label.configure(text=f"템플릿 미완성", text_color="#ebcb8b"))
                    return

                value = matcher.read_number_from_region(region)
                if value is not None:
                    self.after(0, lambda: result_label.configure(text=f"✓ {value}", text_color="#a3be8c"))
                else:
                    self.after(0, lambda: result_label.configure(text="실패", text_color="#bf616a"))
            except Exception as e:
                self.after(0, lambda: result_label.configure(text="오류", text_color="#bf616a"))

        threading.Thread(target=run_test, daemon=True).start()

    def _refresh_template_status(self):
        """템플릿 상태 새로고침"""
        try:
            from ..utils.digit_templates import get_digit_matcher
            matcher = get_digit_matcher()
            missing = matcher.get_missing_digits()

            if not missing:
                self._template_status_label.configure(
                    text="✓ 모든 숫자(0~9) 템플릿 준비됨",
                    text_color="#a3be8c"
                )
                # 모든 버튼 녹색으로
                for digit, btn in self._digit_buttons.items():
                    btn.configure(fg_color="#a3be8c")
            else:
                self._template_status_label.configure(
                    text=f"미설정: {', '.join(missing)}",
                    text_color="#ebcb8b"
                )
                # 있는 건 녹색, 없는 건 회색
                for digit, btn in self._digit_buttons.items():
                    if digit in missing:
                        btn.configure(fg_color=COLORS["bg_card_hover"])
                    else:
                        btn.configure(fg_color="#a3be8c")
        except Exception as e:
            self._template_status_label.configure(
                text=f"오류: {e}",
                text_color="#bf616a"
            )

    def _capture_digit_template(self, digit: str):
        """숫자 템플릿 캡처 - 확대 모드"""
        # 먼저 대략적인 영역 선택 후 확대해서 정밀 선택
        from .analyzer_view import ScreenRegionSelector

        def on_region_select(x1, y1, x2, y2):
            try:
                # 확대 크롭 다이얼로그 표시
                self._show_zoomed_crop_dialog(digit, [x1, y1, x2, y2])
            except Exception as e:
                logger.error(f"[템플릿] 캡처 오류: {e}")

        def on_cancel():
            pass

        from tkinter import messagebox
        messagebox.showinfo("숫자 캡처", f"숫자 '{digit}'가 포함된 영역을 대략적으로 선택하세요.\n\n다음 화면에서 확대해서 정밀하게 크롭합니다.")
        ScreenRegionSelector(self, on_region_select, on_cancel)

    def _show_zoomed_crop_dialog(self, digit: str, region: list):
        """확대해서 정밀 크롭하는 다이얼로그"""
        from PIL import ImageTk, Image
        import tkinter as tk

        # 선택한 영역 캡처
        screenshot = _safe_grab()
        if screenshot is None:
            from tkinter import messagebox
            messagebox.showerror("오류", "스크린샷 캡처 실패 (타임아웃)")
            return
        x1, y1, x2, y2 = region
        cropped = screenshot.crop((x1, y1, x2, y2))

        # 확대 비율
        ZOOM = 4
        zoomed_width = cropped.width * ZOOM
        zoomed_height = cropped.height * ZOOM
        zoomed_img = cropped.resize((zoomed_width, zoomed_height), Image.NEAREST)

        # 다이얼로그 생성
        dialog = ctk.CTkToplevel(self)
        dialog.title(f"숫자 '{digit}' 정밀 크롭 (4배 확대)")
        dialog.geometry(f"{max(zoomed_width + 40, 400)}x{zoomed_height + 150}")
        dialog.transient(self)
        dialog.grab_set()

        ctk.CTkLabel(dialog, text=f"숫자 '{digit}'만 드래그해서 선택하세요",
                     font=ctk.CTkFont(size=12, weight="bold")).pack(pady=10)

        # 캔버스 (드래그로 영역 선택)
        canvas_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        canvas_frame.pack(pady=5)

        canvas = tk.Canvas(canvas_frame, width=zoomed_width, height=zoomed_height,
                          bg="black", highlightthickness=1, highlightbackground="gray")
        canvas.pack()

        # 이미지 표시
        photo = ImageTk.PhotoImage(zoomed_img)
        canvas.create_image(0, 0, anchor="nw", image=photo)
        canvas.image = photo  # 참조 유지

        # 드래그 선택 변수
        start_x, start_y = [0], [0]
        rect_id = [None]
        selected_region = [None]

        def on_press(event):
            start_x[0] = event.x
            start_y[0] = event.y
            if rect_id[0]:
                canvas.delete(rect_id[0])

        def on_drag(event):
            if rect_id[0]:
                canvas.delete(rect_id[0])
            rect_id[0] = canvas.create_rectangle(
                start_x[0], start_y[0], event.x, event.y,
                outline="red", width=2
            )

        # 현재 선택 박스 좌표 (확대 좌표 기준)
        box_coords = [0, 0, 0, 0]  # [zx1, zy1, zx2, zy2]

        def update_selection():
            """선택 영역 업데이트 및 실제 좌표 계산"""
            zx1, zy1, zx2, zy2 = box_coords

            # 실제 좌표 계산
            real_x1 = x1 + zx1 // ZOOM
            real_y1 = y1 + zy1 // ZOOM
            real_x2 = x1 + zx2 // ZOOM
            real_y2 = y1 + zy2 // ZOOM

            selected_region[0] = [real_x1, real_y1, real_x2, real_y2]

            # 선택 크기 표시
            w = real_x2 - real_x1
            h = real_y2 - real_y1
            size_label.configure(text=f"선택 크기: {w} x {h} px  |  방향키로 1px 이동")

        def on_release(event):
            # 확대된 좌표 저장
            box_coords[0] = min(start_x[0], event.x)
            box_coords[1] = min(start_y[0], event.y)
            box_coords[2] = max(start_x[0], event.x)
            box_coords[3] = max(start_y[0], event.y)
            update_selection()

        def move_box(dx, dy):
            """박스 이동 (확대 좌표 기준으로 ZOOM 픽셀 = 실제 1픽셀)"""
            if rect_id[0] is None:
                return

            # 경계 체크
            new_x1 = box_coords[0] + dx * ZOOM
            new_y1 = box_coords[1] + dy * ZOOM
            new_x2 = box_coords[2] + dx * ZOOM
            new_y2 = box_coords[3] + dy * ZOOM

            if new_x1 >= 0 and new_x2 <= zoomed_width and new_y1 >= 0 and new_y2 <= zoomed_height:
                box_coords[0] = new_x1
                box_coords[1] = new_y1
                box_coords[2] = new_x2
                box_coords[3] = new_y2

                # 사각형 다시 그리기
                canvas.delete(rect_id[0])
                rect_id[0] = canvas.create_rectangle(
                    box_coords[0], box_coords[1], box_coords[2], box_coords[3],
                    outline="red", width=2
                )
                update_selection()

        def on_key(event):
            if event.keysym == "Left":
                move_box(-1, 0)
            elif event.keysym == "Right":
                move_box(1, 0)
            elif event.keysym == "Up":
                move_box(0, -1)
            elif event.keysym == "Down":
                move_box(0, 1)

        canvas.bind("<ButtonPress-1>", on_press)
        canvas.bind("<B1-Motion>", on_drag)
        canvas.bind("<ButtonRelease-1>", on_release)

        # 방향키 바인딩
        dialog.bind("<Left>", on_key)
        dialog.bind("<Right>", on_key)
        dialog.bind("<Up>", on_key)
        dialog.bind("<Down>", on_key)
        dialog.focus_set()  # 키 입력 받기 위해 포커스

        # 선택 크기 표시
        size_label = ctk.CTkLabel(dialog, text="영역을 드래그하세요",
                                   font=ctk.CTkFont(size=10), text_color=COLORS["text_secondary"])
        size_label.pack(pady=5)

        # 버튼
        btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_frame.pack(pady=10)

        def save_directly():
            """바로 저장"""
            if not selected_region[0]:
                from tkinter import messagebox
                messagebox.showwarning("선택 필요", "숫자 영역을 드래그해서 선택하세요.")
                return

            try:
                from ..utils.digit_templates import get_digit_matcher
                matcher = get_digit_matcher()
                if matcher.capture_digit_template(digit, selected_region[0]):
                    matcher.save_templates()  # JSON 파일에 저장
                    self._digit_buttons[digit].configure(fg_color="#a3be8c")
                    logger.info(f"[템플릿] 숫자 '{digit}' 저장 완료 (JSON)")
                    self._refresh_template_status()
                    from tkinter import messagebox
                    messagebox.showinfo("저장 완료", f"숫자 '{digit}' 템플릿이 저장되었습니다.")
                    dialog.destroy()
                else:
                    from tkinter import messagebox
                    messagebox.showerror("저장 실패", "템플릿 저장에 실패했습니다.")
            except Exception as e:
                from tkinter import messagebox
                messagebox.showerror("오류", f"저장 중 오류: {e}")

        def preview():
            """미리보기 후 저장"""
            if selected_region[0]:
                dialog.destroy()
                self._show_template_preview(digit, selected_region[0])
            else:
                from tkinter import messagebox
                messagebox.showwarning("선택 필요", "숫자 영역을 드래그해서 선택하세요.")

        def cancel():
            dialog.destroy()

        ctk.CTkButton(btn_frame, text="💾 저장", width=80, fg_color="#a3be8c",
                      command=save_directly).pack(side="left", padx=5)
        ctk.CTkButton(btn_frame, text="👁 미리보기", width=80, fg_color="#5e81ac",
                      command=preview).pack(side="left", padx=5)
        ctk.CTkButton(btn_frame, text="✕ 취소", width=80, fg_color="#bf616a",
                      command=cancel).pack(side="left", padx=5)

    def _show_template_preview(self, digit: str, region: list):
        """템플릿 미리보기 다이얼로그"""
        from PIL import ImageTk
        import cv2

        # 스크린샷 캡처
        screenshot = _safe_grab()
        if screenshot is None:
            from tkinter import messagebox
            messagebox.showerror("오류", "스크린샷 캡처 실패 (타임아웃)")
            return
        x1, y1, x2, y2 = region
        cropped = screenshot.crop((x1, y1, x2, y2))

        # 이진화 처리 (실제 저장되는 형태)
        import numpy as np
        cropped_np = np.array(cropped)
        if len(cropped_np.shape) == 3:
            gray = cv2.cvtColor(cropped_np, cv2.COLOR_RGB2GRAY)
        else:
            gray = cropped_np
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

        # 미리보기 다이얼로그
        preview_dialog = ctk.CTkToplevel(self)
        preview_dialog.title(f"숫자 '{digit}' 템플릿 미리보기")
        preview_dialog.geometry("450x500")
        preview_dialog.transient(self)
        preview_dialog.grab_set()
        preview_dialog.resizable(True, True)

        # 제목
        ctk.CTkLabel(preview_dialog, text=f"숫자 '{digit}' 미리보기",
                     font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(15, 10))

        # 원본 이미지
        ctk.CTkLabel(preview_dialog, text="▼ 원본 이미지 (5배 확대):",
                     font=ctk.CTkFont(weight="bold")).pack(pady=(10, 5))

        from PIL import Image
        orig_enlarged = cropped.resize((cropped.width * 5, cropped.height * 5), Image.NEAREST)
        orig_photo = ImageTk.PhotoImage(orig_enlarged)
        orig_label = ctk.CTkLabel(preview_dialog, image=orig_photo, text="")
        orig_label.image = orig_photo
        orig_label.pack(pady=5)

        # 처리된 이미지 (이진화)
        ctk.CTkLabel(preview_dialog, text="▼ 처리된 이미지 (저장되는 형태):",
                     font=ctk.CTkFont(weight="bold")).pack(pady=(15, 5))

        binary_pil = Image.fromarray(binary)
        binary_enlarged = binary_pil.resize((binary_pil.width * 5, binary_pil.height * 5), Image.NEAREST)
        binary_photo = ImageTk.PhotoImage(binary_enlarged)
        binary_label = ctk.CTkLabel(preview_dialog, image=binary_photo, text="")
        binary_label.image = binary_photo
        binary_label.pack(pady=5)

        # 크기 정보
        ctk.CTkLabel(preview_dialog, text=f"크기: {cropped.width} x {cropped.height} px",
                     font=ctk.CTkFont(size=11), text_color=COLORS["text_secondary"]).pack(pady=10)

        # ========== 버튼 (크게, 명확하게) ==========
        btn_frame = ctk.CTkFrame(preview_dialog, fg_color="transparent")
        btn_frame.pack(pady=20)

        def confirm():
            from ..utils.digit_templates import get_digit_matcher
            matcher = get_digit_matcher()
            if matcher.capture_digit_template(digit, region):
                matcher.save_templates()  # JSON 파일에 저장
                self._digit_buttons[digit].configure(fg_color="#a3be8c")
                logger.info(f"[템플릿] 숫자 '{digit}' 저장 완료 (JSON)")
                self._refresh_template_status()
                from tkinter import messagebox
                messagebox.showinfo("저장 완료", f"숫자 '{digit}' 템플릿이 저장되었습니다!")
            preview_dialog.destroy()

        def retry():
            preview_dialog.destroy()
            self._capture_digit_template(digit)

        ctk.CTkButton(btn_frame, text="💾 저장", width=100, height=40,
                      font=ctk.CTkFont(size=14, weight="bold"),
                      fg_color="#a3be8c", command=confirm).pack(side="left", padx=10)
        ctk.CTkButton(btn_frame, text="↻ 다시", width=100, height=40,
                      font=ctk.CTkFont(size=14),
                      fg_color="#ebcb8b", command=retry).pack(side="left", padx=10)
        ctk.CTkButton(btn_frame, text="✕ 취소", width=100, height=40,
                      font=ctk.CTkFont(size=14),
                      fg_color="#bf616a", command=preview_dialog.destroy).pack(side="left", padx=10)

    def _save_digit_templates(self):
        """템플릿 저장"""
        try:
            from ..utils.digit_templates import get_digit_matcher
            matcher = get_digit_matcher()
            if matcher.save_templates():
                from tkinter import messagebox
                messagebox.showinfo("저장 완료", "숫자 템플릿이 저장되었습니다.")
            else:
                from tkinter import messagebox
                messagebox.showerror("저장 실패", "템플릿 저장에 실패했습니다.")
        except Exception as e:
            from tkinter import messagebox
            messagebox.showerror("오류", f"저장 중 오류: {e}")

    def _show_imgtest_preview(self, ctk_img):
        """이진화 미리보기 이미지 표시"""
        self._imgtest_preview_image = ctk_img  # 참조 유지 (GC 방지)
        self._imgtest_preview_label.configure(image=ctk_img)

    def _imgtest_select(self):
        """이미지 테스트 — 파일 선택"""
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            title="테스트할 이미지 선택",
            filetypes=[("이미지 파일", "*.png *.jpg *.jpeg *.bmp"), ("모든 파일", "*.*")],
            initialdir=str(DATA_DIR / "templates"),
        )
        if path:
            self._imgtest_image_path = path
            name = Path(path).name
            self._imgtest_path_label.configure(text=f"이미지: {name}")
            self._imgtest_result_label.configure(text="결과: 대기 중")

    def _imgtest_run(self):
        """이미지 테스트 — 이진화 미리보기 + 화면 캡처 매칭"""
        if not self._imgtest_image_path:
            self._imgtest_result_label.configure(text="결과: 이미지를 먼저 선택하세요")
            return

        self._imgtest_result_label.configure(text="결과: 검색 중...")
        self.update_idletasks()

        import threading
        def _run():
            try:
                import pyautogui
                import numpy as np
                import cv2
                from PIL import Image as PILImage
                from ..analyzer.template_matcher import TemplateMatcher

                # 템플릿 이진화 미리보기 생성
                tmpl_arr = np.fromfile(self._imgtest_image_path, np.uint8)
                tmpl_img = cv2.imdecode(tmpl_arr, cv2.IMREAD_COLOR)
                if tmpl_img is not None:
                    tmpl_gray = cv2.cvtColor(tmpl_img, cv2.COLOR_BGR2GRAY)
                    _, tmpl_bin = cv2.threshold(tmpl_gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
                    if np.mean(tmpl_bin) > 127:
                        tmpl_bin = cv2.bitwise_not(tmpl_bin)
                    kernel = np.ones((2, 2), np.uint8)
                    tmpl_bin = cv2.dilate(tmpl_bin, kernel, iterations=1)

                    # 미리보기용 리사이즈 (높이 40px 기준)
                    h, w = tmpl_bin.shape[:2]
                    preview_h = 40
                    preview_w = max(1, int(w * preview_h / h))
                    preview = cv2.resize(tmpl_bin, (preview_w, preview_h), interpolation=cv2.INTER_NEAREST)

                    # cv2 → PIL → CTkImage
                    pil_preview = PILImage.fromarray(preview)
                    ctk_img = ctk.CTkImage(light_image=pil_preview, dark_image=pil_preview,
                                           size=(preview_w, preview_h))
                    try:
                        self.after(0, lambda img=ctk_img: self._show_imgtest_preview(img))
                    except (tk.TclError, RuntimeError):
                        pass

                # 전체 화면 캡처
                screenshot = pyautogui.screenshot()
                screen = np.array(screenshot)
                screen = cv2.cvtColor(screen, cv2.COLOR_RGB2BGR)

                matcher = TemplateMatcher()
                result = matcher.match_binary(screen, self._imgtest_image_path, threshold=0.8)

                if result.found:
                    text = f"발견! 인식률={result.confidence:.1%} 위치=({result.center_x},{result.center_y})"
                    color = "#50c878"
                else:
                    text = f"미발견 (인식률={result.confidence:.1%})"
                    color = "#e05050"

                try:
                    self.after(0, lambda t=text, c=color: self._imgtest_result_label.configure(text=t, text_color=c))
                except (tk.TclError, RuntimeError):
                    pass

            except Exception as e:
                try:
                    self.after(0, lambda err=str(e): self._imgtest_result_label.configure(
                        text=f"결과: 오류 - {err}", text_color="#e05050"))
                except (tk.TclError, RuntimeError):
                    pass

        threading.Thread(target=_run, daemon=True).start()

    def _toggle_coordinate_monitor(self):
        """실시간 좌표 모니터링 시작/중지"""
        if self._monitoring:
            # 중지
            self._monitoring = False
            self._monitor_btn.configure(text="▶ 모니터링 시작", fg_color="#5e81ac")
        else:
            # 시작 (중복 방지: 버튼 즉시 비활성화)
            self._monitoring = True
            self._monitor_btn.configure(text="■ 모니터링 중지", fg_color="#bf616a", state="disabled")
            self.after(300, lambda: self._monitor_btn.configure(state="normal"))
            threading.Thread(target=self._run_coordinate_monitor, daemon=True).start()

    def _run_coordinate_monitor(self):
        """실시간 좌표 읽기 (템플릿 전용)"""
        from ..utils.digit_templates import get_digit_matcher
        from PIL import ImageGrab

        matcher = get_digit_matcher()

        # 템플릿 완성 여부 확인
        if not matcher.has_all_templates():
            missing = matcher.get_missing_digits()
            try:
                self.after(0, lambda: self._append_log(f"템플릿 미완성: {missing}"))
                self._monitoring = False
                self.after(0, lambda: self._monitor_btn.configure(text="▶ 모니터링 시작", fg_color="#5e81ac"))
            except (tk.TclError, RuntimeError):
                self._monitoring = False
            return

        logger.info("[좌표모니터] 모니터링 시작 (템플릿)")
        try:
            self.after(0, lambda: self._append_log("모니터링 시작 (템플릿)"))
        except (tk.TclError, RuntimeError):
            pass

        while self._monitoring:
            try:
                x_region = getattr(self._config, 'coord_x_region', None)
                y_region = getattr(self._config, 'coord_y_region', None)

                x_val, y_val = None, None

                # 스크린샷 한 번만 캡처
                screenshot = _safe_grab()
                if screenshot is None:
                    time.sleep(1)
                    continue

                # X 좌표 읽기
                if x_region and len(x_region) == 4:
                    x_val = matcher.read_number_from_region(x_region, screenshot)

                # Y 좌표 읽기
                if y_region and len(y_region) == 4:
                    y_val = matcher.read_number_from_region(y_region, screenshot)

                # UI 라벨 업데이트 (로그 출력 제거 - 라벨에서 이미 표시됨)
                x_text = f"X: {x_val}" if x_val is not None else "X: 인식실패"
                y_text = f"Y: {y_val}" if y_val is not None else "Y: 인식실패"
                x_color = "#a3be8c" if x_val is not None else "#bf616a"
                y_color = "#a3be8c" if y_val is not None else "#bf616a"
                status_text = f"인식 상태: 템플릿 | X={x_val}, Y={y_val}"

                try:
                    self.after(0, lambda xt=x_text, xc=x_color: self._realtime_x_label.configure(text=xt, text_color=xc))
                    self.after(0, lambda yt=y_text, yc=y_color: self._realtime_y_label.configure(text=yt, text_color=yc))
                    self.after(0, lambda st=status_text: self._ocr_raw_label.configure(text=st))
                except (tk.TclError, RuntimeError):
                    pass

            except Exception as e:
                logger.error(f"[좌표모니터] 오류: {e}")
                try:
                    self.after(0, lambda err=str(e): self._append_log(f"오류: {err}"))
                except (tk.TclError, RuntimeError):
                    pass

            # 0.1초씩 5회 대기 (중지 시 즉시 반응)
            for _ in range(5):
                if not self._monitoring:
                    break
                time.sleep(0.1)

        try:
            self.after(0, lambda: self._append_log("모니터링 중지"))
        except (tk.TclError, RuntimeError):
            pass

    def _apply_settings(self):
        # 이름은 액션 우클릭 메뉴에서 설정 (다른 액션과 동일하게)
        self._config.navigation_mode = self._nav_mode_var.get()
        try:
            self._config.analysis_interval = float(self._interval_var.get())
        except (ValueError, TypeError):
            self._config.analysis_interval = 0.1
        try:
            self._config.arrival_threshold = int(self._threshold_var.get())
        except (ValueError, TypeError):
            self._config.arrival_threshold = 30
        self._config.smooth_move = True  # 항상 키홀드 ON
        # 이동 스킬
        self._config.move_skill_enabled = self._move_skill_enabled_var.get()
        self._config.move_skill_key = self._skill_key_var.get().strip()
        try:
            self._config.move_skill_cooldown = float(self._move_skill_cd_var.get())
        except (ValueError, TypeError):
            self._config.move_skill_cooldown = 5.0
        # 상시 스킬
        self._config.auto_skill_enabled = self._auto_skill_enabled_var.get()
        self._config.auto_skill_key = self._auto_skill_key_var.get().strip()
        try:
            self._config.auto_skill_cooldown = float(self._auto_skill_cd_var.get())
        except (ValueError, TypeError):
            self._config.auto_skill_cooldown = 5.0
        # 탈출 스킬
        self._config.escape_skill_enabled = self._escape_skill_enabled_var.get()
        self._config.escape_skill_key = self._escape_skill_key_var.get().strip()
        try:
            self._config.escape_skill_cooldown = float(self._escape_skill_cd_var.get())
        except (ValueError, TypeError):
            self._config.escape_skill_cooldown = 10.0
        # 보스 스킬
        self._config.boss_skill_enabled = self._boss_skill_enabled_var.get()
        self._config.boss_skill_key = self._boss_skill_key_var.get().strip()
        try:
            self._config.boss_skill_cooldown = float(self._boss_skill_cd_var.get())
        except (ValueError, TypeError):
            self._config.boss_skill_cooldown = 3.0
        for k, v in self._key_vars.items():
            self._config.move_keys[k] = v.get()

        # 좌표 모드: 경유지에서 최종 목표 인덱스 저장
        # (target_x/y는 하위호환용으로 유지하되 경유지 기반으로 설정)

    def _save_config(self):
        """설정 저장 (JSON 파일만, messagebox 없음)"""
        import time as _time
        _save_start = _time.time()
        self._config.enabled = True
        self._apply_settings()

        # 맵 데이터도 저장
        if hasattr(self, '_game_map') and self._game_map:
            try:
                map_path = self._auto_save_map()
                logger.info(f"[특화모드] 설정 저장 시 맵도 저장: {map_path}")
            except Exception as e:
                logger.error(f"[특화모드] 맵 저장 실패: {e}")

        # 액션 목록에 게임모드 규칙 추가/업데이트
        display_name = self._config.name if self._config.name else "특화모드"

        if self._config_rule_id:
            # 기존 특화모드 편집: 해당 rule 찾아서 업데이트
            game_rule = None
            for rule in self._plan.initial_rules:
                if rule.rule_id == self._config_rule_id:
                    game_rule = rule
                    break
            if game_rule:
                game_rule.target_image = self._config.character_image
                game_rule.confidence = self._config.confidence
                if display_name:
                    game_rule.description = display_name
            self._plan.game_modes[self._config_rule_id] = self._config
        else:
            # 새 특화모드: 새 rule 생성
            game_rule = AutomationRule(
                rule_type="fixed_sequence",
                action_type="game_mode",
                description=display_name,
                target_image=self._config.character_image,
                confidence=self._config.confidence,
            )
            self._plan.initial_rules.append(game_rule)
            self._config_rule_id = game_rule.rule_id
            self._plan.game_modes[game_rule.rule_id] = self._config

        # JSON 파일에 직접 저장 (messagebox 없이)
        try:
            self._plan.modified = True
            PLANS_DIR.mkdir(parents=True, exist_ok=True)
            plan_file = PLANS_DIR / f"{self._plan.plan_id}.json"
            with open(plan_file, 'w', encoding='utf-8') as f:
                json.dump(self._plan.to_dict(), f, ensure_ascii=False, indent=2)
            logger.info(f"[특화모드] 설정 저장: {plan_file}")
        except Exception as e:
            logger.error(f"[특화모드] 설정 저장 실패: {e}")
        # 경유지 제외한 설정을 기본값으로 저장 (새 플랜에서 자동 적용)
        self._save_game_mode_defaults()

        _save_elapsed = _time.time() - _save_start
        if _save_elapsed > 0.5:
            logger.warning(f"[특화모드] 설정 저장 느림: {_save_elapsed:.1f}초")

    def _save_game_mode_defaults(self):
        """경유지 제외한 특화모드 설정을 기본값 파일로 저장"""
        try:
            defaults = self._config.to_dict()
            # 플랜별 고유 필드 제거 (경유지는 직접 설정)
            for key in ("name", "enabled", "waypoints", "target_x", "target_y",
                        "final_waypoint_idx"):
                defaults.pop(key, None)
            defaults_file = DATA_DIR / "game_mode_defaults.json"
            with open(defaults_file, 'w', encoding='utf-8') as f:
                json.dump(defaults, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"[특화모드] 기본값 저장 실패: {e}")

    def _save_config_with_msg(self):
        """저장 버튼 클릭 시: 설정 저장 + messagebox 표시"""
        self._save_config()

        # messagebox를 GameModeDialog에 parenting (뒤에 숨기지 않음)
        from tkinter import messagebox
        messagebox.showinfo("저장 완료", "설정이 저장되었습니다.", parent=self)

        # UI 갱신
        if self._refresh_callback:
            try:
                self._refresh_callback()
            except Exception as e:
                logger.error(f"[특화모드] 리프레시 콜백 실패: {e}")

    def _restore_and_focus(self):
        """저장된 위치 복원 + 포커스 (화면 밖 → 제자리)"""
        self._load_window_geometry()
        self.lift()
        self.focus_force()
        self.attributes('-topmost', True)
        self.after(100, lambda: self.attributes('-topmost', False))

    def _load_window_geometry(self):
        """저장된 창 위치/크기 불러오기"""
        import json
        config_file = DATA_DIR / "window_positions.json"
        default_w, default_h = 1400, 900

        try:
            if config_file.exists():
                with open(config_file, 'r', encoding='utf-8') as f:
                    positions = json.load(f)
                if 'game_mode_dialog' in positions:
                    geo = positions['game_mode_dialog']
                    x, y, w, h = geo.get('x', 0), geo.get('y', 0), geo.get('w', default_w), geo.get('h', default_h)
                    w = max(w, 1200)
                    h = max(h, 800)
                    # 다중 모니터 지원: x는 음수(왼쪽 모니터) 허용, y는 -100까지 허용
                    screen_w = self.winfo_screenwidth()
                    screen_h = self.winfo_screenheight()
                    if -3000 <= x <= screen_w + 2000 and -100 <= y <= screen_h + 1000:
                        self.geometry(f"{w}x{h}+{x}+{y}")
                        logger.info(f"[특화모드] 창 위치 복원: {w}x{h}+{x}+{y}")
                        return
                    else:
                        logger.warning(f"[특화모드] 저장된 위치가 화면 밖: x={x}, y={y}")
        except Exception as e:
            logger.error(f"[특화모드] 창 위치 로드 실패: {e}")

        # 저장된 위치 없음/실패 → 화면 중앙에 기본 크기로 배치
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        x = max(0, (screen_w - default_w) // 2)
        y = max(0, (screen_h - default_h) // 2)
        self.geometry(f"{default_w}x{default_h}+{x}+{y}")

    def _save_window_geometry(self):
        """창 위치/크기 저장"""
        import json
        config_file = DATA_DIR / "window_positions.json"

        try:
            # geometry 문자열 파싱 (winfo_x/y보다 정확 — 창 프레임 위치 포함)
            geo_str = self.geometry()  # "WxH+X+Y" 형식
            import re
            m = re.match(r'(\d+)x(\d+)\+(-?\d+)\+(-?\d+)', geo_str)
            if not m:
                logger.warning(f"[특화모드] geometry 파싱 실패: {geo_str}")
                return

            w, h, x, y = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))

            if w < 100 or h < 100:
                logger.warning(f"[특화모드] 창 크기 유효하지 않음: {w}x{h}")
                return

            positions = {}
            if config_file.exists():
                with open(config_file, 'r', encoding='utf-8') as f:
                    positions = json.load(f)

            positions['game_mode_dialog'] = {'x': x, 'y': y, 'w': w, 'h': h}

            config_file.parent.mkdir(parents=True, exist_ok=True)
            with open(config_file, 'w', encoding='utf-8') as f:
                json.dump(positions, f, indent=2)
            logger.info(f"[특화모드] 창 위치 저장: {w}x{h}+{x}+{y}")
        except Exception as e:
            logger.error(f"[특화모드] 창 위치 저장 실패: {e}")

    def _on_close(self):
        # 창 위치 저장
        self._save_window_geometry()

        # 모니터링 중지
        self._monitoring = False

        _was_running = self._is_running
        if _was_running:
            self._stop_execution()

        # 워커 스레드 종료 대기 (최대 2초)
        _rt = getattr(self, '_run_thread', None)
        if _rt is not None and _rt.is_alive():
            _rt.join(timeout=2.0)

        # 맵 데이터 저장 (실행 중이 아니었을 때만 — 실행 중이면 do_save가 처리)
        if not _was_running and hasattr(self, '_game_map') and self._game_map:
            stats = self._game_map.get_statistics()
            if stats['total_tiles'] > 0:
                try:
                    map_path = self._auto_save_map()
                    logger.info(f"[특화모드] 닫기 시 맵 저장: {map_path} ({stats['total_tiles']}개 타일)")
                except Exception as e:
                    logger.error(f"[특화모드] 닫기 시 맵 저장 실패: {e}")

        # 닫기 전 액션 목록 갱신 (저장 후 반영 보장)
        if self._refresh_callback:
            try:
                self._refresh_callback()
            except Exception as e:
                logger.error(f"[특화모드] 닫기 시 리프레시 실패: {e}")

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
        """썸네일 표시 - 캐시 사용, 비동기 로딩"""
        image_path = action.target_image

        if image_path and Path(image_path).exists():
            try:
                # 캐시된 썸네일 확인
                target_size = (60, 60)
                ctk_image = get_cached_thumbnail(image_path, target_size)

                if ctk_image is not None:
                    # 캐시 히트 — 즉시 표시
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

                # 캐시 미스 — 플레이스홀더 표시 후 백그라운드 로딩
                placeholder = ctk.CTkLabel(
                    parent, text="📷", font=ctk.CTkFont(size=18),
                    text_color=COLORS["text_muted"],
                )
                placeholder.pack(expand=True)

                def _load_thumb(path=image_path, a=action, ph=placeholder, pr=parent):
                    try:
                        img_arr = np.fromfile(path, np.uint8)
                        img = cv2.imdecode(img_arr, cv2.IMREAD_COLOR)
                        if img is None:
                            return
                        img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                        h, w = img_rgb.shape[:2]
                        scale = min(60 / w, 60 / h)
                        new_w, new_h = int(w * scale), int(h * scale)
                        resized = cv2.resize(img_rgb, (new_w, new_h))
                        pil_image = Image.fromarray(resized)

                        def _apply():
                            try:
                                ctk_img = ctk.CTkImage(light_image=pil_image, dark_image=pil_image, size=(new_w, new_h))
                                set_cached_thumbnail(path, target_size, ctk_img)
                                ph.destroy()
                                thumb_btn = ctk.CTkButton(
                                    pr, image=ctk_img, text="",
                                    width=68, height=68,
                                    fg_color="transparent",
                                    hover_color=COLORS["bg_card_hover"],
                                    corner_radius=4,
                                    command=lambda p=path, act=a: self._open_image_editor(p, act),
                                )
                                thumb_btn.pack(expand=True)
                                self._thumbnail_refs.append(ctk_img)
                            except (tk.TclError, RuntimeError):
                                pass

                        self.after(0, _apply)
                    except (IOError, OSError, ValueError):
                        pass

                threading.Thread(target=_load_thumb, daemon=True).start()
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
            old_path = action.target_image
            action.target_image = new_path
            self._modified = True
            needs_refresh[0] = True
            if old_path:
                invalidate_thumbnail_cache(old_path)
            invalidate_thumbnail_cache(new_path)
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

        # 클립보드 액션과 모든 자식 수집
        all_actions = collect_all_actions(clipboard)
        monitor_actions = []

        for act in all_actions:
            ma = _convert_action_to_monitor_dict(act)
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
        # Action은 .text/.keys/.x/.y, AutomationRule은 .action_text/.action_keys/.action_x/.action_y
        _is_rule = isinstance(clipboard, AutomationRule)
        action_type = clipboard.action_type
        monitor_action = None

        _clip_text = clipboard.action_text if _is_rule else clipboard.text
        _clip_keys = clipboard.action_keys if _is_rule else clipboard.keys
        _clip_x = clipboard.action_x if _is_rule else clipboard.x
        _clip_y = clipboard.action_y if _is_rule else clipboard.y

        if action_type == "type":
            monitor_action = {"type": "텍스트 입력", "text": _clip_text or ""}
        elif action_type in ["hotkey", "key_press"]:
            monitor_action = {"type": "키 입력", "keys": _clip_keys or []}
        elif action_type in ["click", "double_click", "right_click"]:
            if getattr(clipboard, 'target_image', None):
                monitor_action = {"type": "이미지 클릭", "image": clipboard.target_image, "click_type": action_type}
            else:
                monitor_action = {"type": "마우스 클릭", "x": _clip_x, "y": _clip_y, "click_type": action_type}
        elif action_type == "scroll":
            monitor_action = {"type": "스크롤", "amount": getattr(clipboard, 'scroll_amount', 0)}
        elif action_type == "drag":
            monitor_action = {
                "type": "드래그",
                "from_x": _clip_x,
                "from_y": _clip_y,
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
            screenshot = _safe_grab()
            if screenshot is None:
                messagebox.showerror("오류", "스크린샷 캡처 실패 (타임아웃)")
                return
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
            self._sequence.actions.remove(dragged)
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
        if hasattr(self, '_batch_render_id') and self._batch_render_id:
            try:
                self.after_cancel(self._batch_render_id)
            except (tk.TclError, ValueError):
                pass
            self._batch_render_id = None
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
        self._plan_lock_cache: dict = {}  # plan_id → locked 캐시
        self._load_generation: int = 0  # async/sync 로드 경쟁 방지 카운터

        self._setup_ui()
        self._setup_callbacks()
        self.after(0, self._deferred_load)  # UI 렌더 후 데이터 로드

    def _deferred_load(self):
        """UI 표시 후 데이터 로드 (after(0) 콜백)"""
        self._load_sequences_async()

    def _load_sequences_async(self):
        """시퀀스 + 플랜을 백그라운드에서 로드"""
        self._load_generation += 1
        current_gen = self._load_generation

        def _load():
            sequences = self._db.get_all_sequences()
            plans = []
            templates_dir = DATA_DIR / "templates"
            if PLANS_DIR.exists():
                for plan_file in PLANS_DIR.glob("*.json"):
                    try:
                        with open(plan_file, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        plan = AutomationPlan.from_dict(data, templates_dir=templates_dir)
                        if plan.user_verified:
                            plans.append(plan)
                    except Exception as e:
                        logger.error(f"자동화 계획 로드 실패 ({plan_file}): {e}")
            plans.sort(key=lambda p: p.created_at, reverse=True)
            # plan_id → locked 캐시도 백그라운드에서 구축 (UI 차단 방지)
            lock_cache = {}
            for plan in plans:
                recording = self._db.get_recording_by_plan_id(plan.plan_id)
                lock_cache[plan.plan_id] = recording.locked if recording else False
            self.after(0, lambda: self._apply_loaded_data(sequences, plans, current_gen, lock_cache))

        threading.Thread(target=_load, daemon=True).start()

    def _apply_loaded_data(self, sequences, plans, generation=None, lock_cache=None):
        """로드 완료 후 UI 갱신"""
        # generation이 현재보다 오래된 경우 무시
        if generation is not None and generation < self._load_generation:
            return
        self._sequences = sequences
        self._automation_plans = plans
        self._plan_lock_cache = lock_cache if lock_cache is not None else {}
        self._render_sequence_list()

    def _render_sequence_list(self):
        """시퀀스 + 플랜 위젯 렌더링"""
        for widget in self._sequence_frame.winfo_children():
            widget.destroy()

        self._selected_item_widget = None

        has_items = False

        if self._automation_plans:
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
        """재생 목록 로드 (비동기 — UI 차단 방지)"""
        self._load_sequences_async()

    def _create_plan_item(self, plan: AutomationPlan) -> None:
        """자동화 계획 항목 생성"""
        # 연관된 녹화의 잠금 상태 확인 (캐시 사용 — 루프 내 DB 쿼리 방지)
        is_locked = self._plan_lock_cache.get(plan.plan_id, False)

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
        # 다이얼로그 닫힌 후 목록 새로고침 (이름/규칙 변경 반영)
        self._load_sequences()

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

        try:
            repeat_count = (
                0 if self._infinite_var.get() else max(1, int(self._repeat_var.get() or 1))
            )
        except (ValueError, TypeError):
            repeat_count = 1

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
            if not plan_file.exists():
                return None
            # File size check to avoid loading huge files on UI thread
            file_size = plan_file.stat().st_size
            if file_size > 5_000_000:  # 5MB 이상이면 비동기 로드 권장
                logger.warning(f"플랜 파일이 크므로 비동기 로드 권장: {file_size/1024:.0f}KB")
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
        self._load_sequences_async()

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
