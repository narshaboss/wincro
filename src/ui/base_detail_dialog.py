"""
WinCro 기본 상세 다이얼로그

PlanDetailDialog와 SequenceDetailDialog의 공통 기능을 추출한 베이스 클래스입니다.
"""

import copy
import random
from abc import abstractmethod
from typing import Optional, Union, Any

import customtkinter as ctk

from ..utils.logger import get_logger
from .theme import COLORS
from .constants import (
    collect_all_actions, assign_new_ids,
    get_action_clipboard, set_action_clipboard,
)

logger = get_logger(__name__)


class BaseDetailDialog(ctk.CTkToplevel):
    """PlanDetailDialog와 SequenceDetailDialog의 공통 베이스 클래스"""

    # --- Abstract Properties: 서브클래스에서 반드시 구현 ---

    @property
    @abstractmethod
    def _root_items(self) -> list:
        """기본 루트 아이템 리스트 (initial_rules 또는 actions)"""
        ...

    @property
    @abstractmethod
    def _all_root_lists(self) -> list:
        """모든 루트 아이템 리스트들 (Plan은 [initial, monitoring], Sequence는 [actions])"""
        ...

    @property
    @abstractmethod
    def _widgets_dict(self) -> dict:
        """위젯 딕셔너리 (_rule_widgets 또는 _action_widgets)"""
        ...

    @property
    @abstractmethod
    def _selected_item(self):
        """현재 선택된 아이템"""
        ...

    @_selected_item.setter
    @abstractmethod
    def _selected_item(self, value):
        ...

    @property
    @abstractmethod
    def _drag_item_key(self) -> str:
        """드래그 데이터의 아이템 키 ('rule' 또는 'action')"""
        ...

    # --- Abstract Methods ---

    @abstractmethod
    def _get_item_id(self, item) -> Union[str, int]:
        """아이템의 고유 ID 반환"""
        ...

    @abstractmethod
    def _find_parent_item(self, target) -> Optional[Any]:
        """대상 아이템의 부모를 찾아 반환"""
        ...

    @abstractmethod
    def _refresh_action_list(self):
        """액션 리스트 UI 갱신"""
        ...

    @abstractmethod
    def _save(self):
        """변경사항 저장"""
        ...

    # --- Shared Methods ---

    def _init_collapsed_items(self):
        """자식이 있는 아이템을 접힌 상태로 초기화"""
        def add_collapsed(items):
            for item in items:
                if item.children:
                    self._collapsed_items.add(self._get_item_id(item))
                    add_collapsed(item.children)
        for item_list in self._all_root_lists:
            if item_list:
                add_collapsed(item_list)

    def _copy_item(self, item):
        """아이템 복사 (하위 포함)"""
        set_action_clipboard(copy.deepcopy(item))
        item_id = self._get_item_id(item)
        logger.info(f"아이템 복사됨: {item_id} (하위 {len(item.children)}개 포함)")

    def _delete_item(self, item):
        """아이템 삭제"""
        for root_list in self._all_root_lists:
            if item in root_list:
                root_list.remove(item)
                self._refresh_action_list()
                return

        parent = self._find_parent_item(item)
        if parent and item in parent.children:
            parent.children.remove(item)
            self._refresh_action_list()

    def _move_item_up(self, item):
        """아이템을 위로 이동"""
        parent = self._find_parent_item(item)
        if parent:
            siblings = parent.children
        else:
            siblings = self._find_item_root_list(item)
            if siblings is None:
                return

        idx = siblings.index(item) if item in siblings else -1
        if idx > 0:
            siblings[idx], siblings[idx - 1] = siblings[idx - 1], siblings[idx]
            self._refresh_action_list()

    def _move_item_down(self, item):
        """아이템을 아래로 이동"""
        parent = self._find_parent_item(item)
        if parent:
            siblings = parent.children
        else:
            siblings = self._find_item_root_list(item)
            if siblings is None:
                return

        idx = siblings.index(item) if item in siblings else -1
        if 0 <= idx < len(siblings) - 1:
            siblings[idx], siblings[idx + 1] = siblings[idx + 1], siblings[idx]
            self._refresh_action_list()

    def _find_item_root_list(self, item) -> Optional[list]:
        """아이템이 포함된 루트 리스트를 찾아 반환"""
        for root_list in self._all_root_lists:
            if item in root_list:
                return root_list
        return None

    def _select_item(self, item):
        """아이템 선택"""
        prev = self._selected_item
        self._selected_item = item

        widgets = self._widgets_dict
        item_id = self._get_item_id(item)

        # 이전 선택 해제
        if prev is not None:
            prev_id = self._get_item_id(prev)
            if prev_id in widgets:
                try:
                    widgets[prev_id].configure(
                        border_color=COLORS["bg_sidebar"],
                        border_width=1,
                    )
                except Exception:
                    pass

        # 새 선택 표시
        if item_id in widgets:
            try:
                widgets[item_id].configure(
                    border_color=COLORS["accent"],
                    border_width=2,
                )
            except Exception:
                pass

    def _detach_item(self, item):
        """아이템을 부모에서 분리하여 루트로 이동"""
        parent = self._find_parent_item(item)
        if not parent:
            return

        parent.children.remove(item)
        item.parent_id = None
        self._root_items.append(item)
        self._refresh_action_list()

    def _randomize_all_delays(self):
        """모든 대기시간 랜덤화 토글"""
        def check_random(items):
            for item in items:
                if getattr(item, 'wait_random', False):
                    return True
                if item.children and check_random(item.children):
                    return True
            return False

        is_random = check_random(self._root_items)

        def toggle_random(items, enable):
            for item in items:
                item.wait_random = enable
                if enable:
                    original_wait = getattr(item, 'wait_after', 1.0) or 1.0
                    item.wait_random_min = max(0.1, round(original_wait * 0.7, 1))
                    item.wait_random_max = round(original_wait * 1.3, 1)
                if item.children:
                    toggle_random(item.children, enable)

        toggle_random(self._root_items, not is_random)
        self._refresh_action_list()

    def _toggle_all_collapse(self):
        """전체 접기/펼치기 토글"""
        if self._all_collapsed:
            self._collapsed_items.clear()
            self._all_collapsed = False
        else:
            for item in self._root_items:
                if item.children:
                    self._collapsed_items.add(self._get_item_id(item))
                    self._collect_parent_ids(item)
            self._all_collapsed = True
        self._refresh_action_list()

    def _collect_parent_ids(self, item):
        """아이템의 자식 중 자식이 있는 것들의 ID를 수집하여 collapsed_items에 추가"""
        for child in item.children:
            if child.children:
                self._collapsed_items.add(self._get_item_id(child))
                self._collect_parent_ids(child)

    def _toggle_all_children(self):
        """전체 아이템을 하나의 부모 아래로 넣기/해제 토글"""
        root = self._root_items
        if len(root) < 2:
            return

        first = root[0]
        if first.children:
            # 자식이 있으면 해제 (1단계 자식만 루트로 이동)
            for child in list(first.children):
                child.parent_id = None
                root.append(child)
            first.children = []
            self._collapsed_items.discard(self._get_item_id(first))
        else:
            # 자식이 없으면 나머지를 first의 자식으로
            remaining = root[1:]
            for item in remaining:
                item.parent_id = self._get_item_id(first)
                first.children.append(item)

            # root 리스트를 first만 남김
            root.clear()
            root.append(first)
            self._collapsed_items.discard(self._get_item_id(first))

        self._refresh_action_list()

    def _flatten_children(self):
        """선택된 아이템의 자식들을 루트로 펼치기"""
        selected = self._selected_item
        if selected is None:
            return

        if not selected.children:
            return

        root = self._root_items
        if selected not in root:
            return

        idx = root.index(selected)
        children = list(selected.children)
        selected.children = []

        for i, child in enumerate(children):
            child.parent_id = None
            root.insert(idx + 1 + i, child)

        self._collapsed_items.discard(self._get_item_id(selected))
        self._refresh_action_list()

    # --- Drag and Drop ---

    def _on_drag_start(self, event, item, widget):
        """드래그 시작"""
        self._drag_data[self._drag_item_key] = item
        self._drag_data["widget"] = widget
        self._drag_data["start_y"] = event.y_root

    def _on_drag_motion(self, event):
        """드래그 이동"""
        dragged = self._drag_data.get(self._drag_item_key)
        if not dragged:
            return

        dy = event.y_root - self._drag_data["start_y"]
        if abs(dy) < 5:
            return

        widgets = self._widgets_dict
        target = None
        min_dist = float('inf')

        for wid, widget_data in widgets.items():
            try:
                w = widget_data if not isinstance(widget_data, dict) else widget_data.get("widget")
                if w is None:
                    continue
                wy = w.winfo_rooty()
                wh = w.winfo_height()
                center_y = wy + wh // 2
                dist = abs(event.y_root - center_y)

                if dist < min_dist:
                    min_dist = dist
                    # widget_data에서 아이템 찾기
                    if isinstance(widget_data, dict):
                        target = widget_data.get(self._drag_item_key)
                    else:
                        target = None
            except Exception:
                continue

        self._drop_target = target

        # 시각적 피드백
        for wid, widget_data in widgets.items():
            try:
                w = widget_data if not isinstance(widget_data, dict) else widget_data.get("widget")
                if w is None:
                    continue
                item_in_data = widget_data.get(self._drag_item_key) if isinstance(widget_data, dict) else None
                if item_in_data is not None and target is not None and self._get_item_id(item_in_data) == self._get_item_id(target):
                    w.configure(border_color=COLORS["accent"], border_width=2)
                else:
                    w.configure(border_color=COLORS["bg_sidebar"], border_width=1)
            except Exception:
                continue

    def _is_ancestor(self, potential_ancestor, target) -> bool:
        """potential_ancestor가 target의 조상인지 확인"""
        if not potential_ancestor.children:
            return False
        for child in potential_ancestor.children:
            if self._get_item_id(child) == self._get_item_id(target):
                return True
            if self._is_ancestor(child, target):
                return True
        return False
