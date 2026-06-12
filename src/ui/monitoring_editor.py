"""
WinCro 모니터링 모드 편집기

player_view.py의 _edit_monitoring_mode 메서드를 별도 클래스로 추출한 모듈입니다.
"""

import time
import threading
import shutil
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk
import tkinter as tk
import cv2
import numpy as np
from PIL import Image

from ..utils.logger import get_logger
from ..utils.config import DATA_DIR
from .theme import COLORS, IOS_FONTS, IOS_METRICS
from .constants import (
    ACTION_NAMES_SHORT,
    get_action_clipboard, collect_all_actions,
)
from .analyzer_view import get_cached_thumbnail, set_cached_thumbnail, submit_thumbnail_task

logger = get_logger(__name__)


class MonitoringModeEditor(ctk.CTkToplevel):
    """모니터링 모드 설정 다이얼로그

    PlanDetailDialog의 _edit_monitoring_mode를 독립된 CTkToplevel 클래스로 추출.
    """

    def __init__(self, owner, rule, plan_rules):
        """
        Args:
            owner: 부모 위젯 (PlanDetailDialog)
            rule: 편집 대상 AutomationRule
            plan_rules: plan.initial_rules 리스트
        """
        super().__init__(owner)
        self._owner = owner
        self._rule = rule
        self._plan_rules = plan_rules
        self.was_saved = False

        # State
        self._watches_data = []
        self._watch_widgets = []
        self._watch_row3_containers = {}
        self._watch_collapsed = {}
        self._action_options = []
        self._watch_scroll = None
        self._is_monitoring_var = None
        self._monitor_action_clipboard = None
        self._watch_action_card_widgets = {}
        self._watch_action_count_labels = {}
        self._watch_render_after_id = None
        self._watch_render_generation = 0
        self._watch_render_batch_size = 12
        self._font_cache = {}

        self._setup_dialog()
        self._init_data()
        self._build_ui()

    def destroy(self):
        self._cancel_watch_list_render_batch()
        super().destroy()

    def _font(self, size, weight=None):
        """Reuse CTkFont objects in frequently rebuilt watch rows."""
        key = (size, weight or "")
        cached = self._font_cache.get(key)
        if cached is None:
            kwargs = {"family": IOS_FONTS["family"], "size": size}
            if weight:
                kwargs["weight"] = weight
            cached = ctk.CTkFont(**kwargs)
            self._font_cache[key] = cached
        return cached

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    def _setup_dialog(self):
        """다이얼로그 기본 설정"""
        logger.info(
            f"[모니터링 편집] rule_id={self._rule.rule_id}, "
            f"object_id={id(self._rule)}, 현재상태={self._rule.is_monitoring_mode}"
        )
        self.title("모니터링 모드 설정")
        self.geometry("680x850")
        self.resizable(False, False)
        self.configure(fg_color=COLORS["bg_dark"])
        self.transient(self._owner)
        self.grab_set()

        self.update_idletasks()
        x = (self.winfo_screenwidth() - 680) // 2
        y = (self.winfo_screenheight() - 850) // 2
        self.geometry(f"+{x}+{y}")

    def _init_data(self):
        """감시 목록 데이터 및 액션 옵션 초기화"""
        rule = self._rule

        # 감시 목록 데이터 로드
        for w in getattr(rule, 'monitoring_watches', []):
            monitor_actions = w.get("monitor_actions", [])
            if not monitor_actions and w.get("monitor_action"):
                monitor_actions = [w.get("monitor_action")]
            self._watches_data.append({
                "image": w.get("image"),
                "goto_index": w.get("goto_index", 0),
                "search_region": w.get("search_region"),
                "monitor_actions": monitor_actions,
                "confidence": w.get("confidence", 0.65),  # 감시 이미지별 인식률
                "condition_image": w.get("condition_image"),  # 점프 조건 이미지
                "condition_search_region": w.get("condition_search_region"),  # 조건 이미지 검색 범위
                "condition_confidence": w.get("condition_confidence", 0.80),  # 조건 이미지 인식률
            })

        # 부모 액션 목록 (드롭다운용)
        self._action_options = [("점프 안 함", -1)]
        action_names = ACTION_NAMES_SHORT
        for i, r in enumerate(self._plan_rules):
            action_type = action_names.get(r.action_type, r.action_type or "동작")
            desc = r.description[:15] + "..." if r.description and len(r.description) > 15 else (r.description or "")
            children_count = f" (+{len(r.children)})" if r.children else ""
            display_text = f"{i + 1}. {action_type}{children_count}"
            if desc:
                display_text += f" - {desc}"
            self._action_options.append((display_text, i))

    def _build_ui(self):
        """UI 구성"""
        main_frame = ctk.CTkFrame(self, fg_color="transparent")
        main_frame.pack(fill="both", expand=True, padx=20, pady=15)

        # 모니터링 모드 활성화 체크박스
        self._is_monitoring_var = ctk.BooleanVar(
            value=getattr(self._rule, 'is_monitoring_mode', False)
        )

        header_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        header_frame.pack(fill="x", pady=(0, 15))

        ctk.CTkCheckBox(
            header_frame,
            text="모니터링 모드 활성화",
            variable=self._is_monitoring_var,
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=COLORS["success"],
            fg_color=COLORS["success"],
            hover_color=COLORS["green_hover"],
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
        watch_frame = ctk.CTkFrame(main_frame, fg_color=COLORS["bg_card"], corner_radius=IOS_METRICS["control_radius"])
        watch_frame.pack(fill="both", expand=True, pady=10)

        ctk.CTkLabel(
            watch_frame,
            text="감시 목록 (우클릭으로 모니터링 액션 붙여넣기)",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(anchor="w", padx=10, pady=(10, 5))

        self._watch_scroll = ctk.CTkScrollableFrame(watch_frame, fg_color="transparent", height=420)
        self._watch_scroll.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        self._refresh_watch_list()

        # 감시 추가 버튼
        ctk.CTkButton(
            watch_frame, text="+ 감시 추가", width=100, height=30,
            fg_color=COLORS["success"], hover_color=COLORS["green_hover"],
            command=self._add_watch,
        ).pack(pady=(0, 10))

        # 설명
        ctk.CTkLabel(
            main_frame,
            text="※ 선택한 액션에 하위 액션이 있으면 함께 실행됩니다",
            font=self._font(10),
            text_color=COLORS["text_muted"],
        ).pack(anchor="w", pady=(5, 0))

        # 저장/취소 버튼
        bottom_frame = ctk.CTkFrame(self, fg_color="transparent")
        bottom_frame.pack(pady=15)

        ctk.CTkButton(
            bottom_frame, text="저장", width=120, height=40,
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color=COLORS["success"], hover_color=COLORS["green_hover"],
            command=self._save,
        ).pack(side="left", padx=10)

        ctk.CTkButton(
            bottom_frame, text="취소", width=120, height=40,
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color=COLORS["bg_card"], hover_color=COLORS["bg_card_hover"],
            text_color=COLORS["text_secondary"],
            command=self.destroy,
        ).pack(side="left", padx=10)

    # ------------------------------------------------------------------
    # Helper methods
    # ------------------------------------------------------------------

    @staticmethod
    def _get_action_display_short(action):
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
            return f"이미지:{img}", COLORS["scroll_purple"]
        return "?", COLORS["text_muted"]

    @staticmethod
    def _load_thumbnail(path, size=(40, 40)):
        """이미지 썸네일 로드 (한글 경로 지원, 글로벌 캐시 사용)"""
        if path and Path(path).exists():
            # 글로벌 캐시 확인
            cached = get_cached_thumbnail(path, size)
            if cached is not None:
                return cached
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
                    ctk_img = ctk.CTkImage(light_image=pil_img, dark_image=pil_img, size=(new_w, new_h))
                    # 글로벌 캐시에 저장
                    set_cached_thumbnail(path, size, ctk_img)
                    return ctk_img
            except Exception:
                pass
        return None

    def _schedule_watch_thumbnail(self, label, path, size=(28, 28)):
        """감시 목록 썸네일을 UI 스레드 밖에서 디코딩한다."""
        source = str(path or "")
        label._thumb_source = (source, size)
        if not source or not Path(source).exists():
            label.configure(image=None, text="?", font=self._font(12), text_color=COLORS["text_muted"])
            return

        cached = get_cached_thumbnail(source, size)
        if cached is not None:
            label.configure(image=cached, text="")
            label._thumb_img = cached
            return

        label.configure(image=None, text="IMG", font=self._font(10), text_color=COLORS["text_muted"])

        def load_thumbnail(path=source, target_size=size, target_label=label):
            try:
                img_arr = np.fromfile(path, np.uint8)
                img = cv2.imdecode(img_arr, cv2.IMREAD_COLOR)
                if img is None:
                    raise ValueError("image decode failed")
                img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                h, w = img_rgb.shape[:2]
                scale = min(target_size[0] / w, target_size[1] / h)
                new_w, new_h = max(1, int(w * scale)), max(1, int(h * scale))
                resized = cv2.resize(img_rgb, (new_w, new_h))
                pil_img = Image.fromarray(resized)

                def apply_thumbnail():
                    try:
                        if not target_label.winfo_exists():
                            return
                        if getattr(target_label, "_thumb_source", None) != (path, target_size):
                            return
                        ctk_img = ctk.CTkImage(light_image=pil_img, dark_image=pil_img, size=(new_w, new_h))
                        set_cached_thumbnail(path, target_size, ctk_img)
                        target_label.configure(image=ctk_img, text="")
                        target_label._thumb_img = ctk_img
                    except (tk.TclError, RuntimeError):
                        pass

                self.after(0, apply_thumbnail)
            except Exception as exc:
                logger.warning(f"watch thumbnail load failed: {path} - {exc}")

                def show_error():
                    try:
                        if target_label.winfo_exists() and getattr(target_label, "_thumb_source", None) == (path, target_size):
                            target_label.configure(image=None, text="?", text_color=COLORS["text_muted"])
                    except (tk.TclError, RuntimeError):
                        pass

                self.after(0, show_error)

        submit_thumbnail_task(load_thumbnail)

    @staticmethod
    def _convert_rule_to_monitor_action(act):
        """AutomationRule을 monitor_action 딕셔너리로 변환"""
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

        if ma:
            ma["wait_after"] = getattr(act, 'wait_after', 0.5)
            ma["wait_random"] = getattr(act, 'wait_random', False)
            ma["wait_random_range"] = getattr(act, 'wait_random_range', 0.3)
            ma["alternate_mouse_route"] = getattr(act, 'alternate_mouse_route', False)
            ma["click_until_image_disappears"] = getattr(act, 'click_until_image_disappears', False)
            ma["click_until_image_disappears_delay"] = getattr(
                act,
                'click_until_image_disappears_delay',
                getattr(act, 'repeat_delay', 0.5),
            )
            ma["repeat_count"] = getattr(act, 'repeat_count', 1)
            ma["repeat_delay"] = getattr(act, 'repeat_delay', 0.5)
            ma["repeat_delay_random"] = getattr(act, 'repeat_delay_random', False)
            ma["repeat_delay_random_range"] = getattr(act, 'repeat_delay_random_range', 0.3)
            ma["typing_random"] = getattr(act, 'typing_random', False)
            ma["typing_delay"] = getattr(act, 'typing_delay', 0.1)
            ma["typing_delay_range"] = getattr(act, 'typing_delay_range', 0.05)
            ma["search_radius"] = getattr(act, 'search_radius', 0)
            ma["search_region"] = getattr(act, 'search_region', None)
        return ma

    # ------------------------------------------------------------------
    # Refresh monitor actions area for a single watch
    # ------------------------------------------------------------------

    def _refresh_monitor_actions(self, watch_idx):
        """특정 watch의 모니터링 액션 영역만 업데이트"""
        if watch_idx not in self._watch_row3_containers:
            return
        if watch_idx >= len(self._watches_data):
            return

        row3 = self._watch_row3_containers[watch_idx]
        self._watch_action_card_widgets.pop(watch_idx, None)
        self._watch_action_count_labels.pop(watch_idx, None)
        for child in row3.winfo_children():
            child.destroy()

        current_actions = self._watches_data[watch_idx].get("monitor_actions", [])
        if not current_actions:
            return
        self._watch_action_card_widgets[watch_idx] = []

        # 헤더
        header_frame = ctk.CTkFrame(row3, fg_color="transparent")
        header_frame.pack(fill="x", padx=(30, 0), pady=(5, 5))

        count_label = ctk.CTkLabel(
            header_frame, text=f"모니터링 액션 ({len(current_actions)}개)",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=COLORS["success"],
        )
        count_label.pack(side="left")
        self._watch_action_count_labels[watch_idx] = count_label

        # 붙여넣기 버튼
        import copy
        def paste_action(i=watch_idx):
            clipboard = getattr(self, '_monitor_action_clipboard', None)
            if clipboard is None:
                messagebox.showinfo("클립보드 비어있음", "먼저 모니터링 액션을 복사(CP)하세요.")
                return
            if i < len(self._watches_data):
                if "monitor_actions" not in self._watches_data[i]:
                    self._watches_data[i]["monitor_actions"] = []
                self._watches_data[i]["monitor_actions"].append(copy.deepcopy(clipboard))
                logger.info(f"[모니터링] 액션 붙여넣기: {clipboard.get('type', '알수없음')}")
                if not self._append_monitor_action_card(i):
                    self._refresh_monitor_actions(i)

        ctk.CTkButton(
            header_frame, text="붙여넣기", width=60, height=20,
            font=ctk.CTkFont(size=10),
            fg_color=COLORS["scroll_purple"], hover_color=COLORS["search_radius_purple_hover"],
            text_color=COLORS["text_primary"], corner_radius=IOS_METRICS["control_radius_small"],
            command=paste_action,
        ).pack(side="left", padx=(8, 0))

        # 각 모니터링 액션을 카드 형태로 표시
        for ai, action in enumerate(current_actions):
            card = self._build_action_card(row3, watch_idx, ai, action)
            self._watch_action_card_widgets[watch_idx].append(card)

    def _append_monitor_action_card(self, watch_idx):
        """Append one monitor action card without rebuilding the whole expanded watch."""
        if watch_idx not in self._watch_row3_containers or watch_idx >= len(self._watches_data):
            return False

        actions = self._watches_data[watch_idx].get("monitor_actions", [])
        if not actions:
            return False

        # When the first action is added the header/paste controls do not exist yet.
        if len(actions) == 1 or watch_idx not in self._watch_action_card_widgets:
            return False

        row3 = self._watch_row3_containers[watch_idx]
        card = self._build_action_card(row3, watch_idx, len(actions) - 1, actions[-1])
        self._watch_action_card_widgets[watch_idx].append(card)

        count_label = self._watch_action_count_labels.get(watch_idx)
        if count_label is not None:
            count_label.configure(text=f"모니터링 액션 ({len(actions)}개)")
        return True

    def _refresh_monitor_action_card(self, watch_idx, action_idx):
        """Replace one monitor action card when the action count/order is unchanged."""
        if watch_idx not in self._watch_row3_containers or watch_idx >= len(self._watches_data):
            return False

        actions = self._watches_data[watch_idx].get("monitor_actions", [])
        cards = self._watch_action_card_widgets.get(watch_idx)
        if not cards or action_idx < 0 or action_idx >= len(actions) or action_idx >= len(cards):
            return False

        before_widget = cards[action_idx + 1] if action_idx + 1 < len(cards) else None
        try:
            if before_widget is not None and not before_widget.winfo_exists():
                before_widget = None
            cards[action_idx].destroy()
        except (tk.TclError, RuntimeError):
            return False

        row3 = self._watch_row3_containers[watch_idx]
        cards[action_idx] = self._build_action_card(
            row3,
            watch_idx,
            action_idx,
            actions[action_idx],
            before_widget=before_widget,
        )
        return True

    def _build_action_card(self, parent, watch_idx, ai, action, before_widget=None):
        """단일 모니터링 액션 카드 UI 생성"""
        ma_type = action.get("type", "알 수 없음")
        ma_detail = ""

        type_colors = {
            "텍스트 입력": COLORS["success"],
            "키 입력": COLORS["accent_orange"],
            "마우스 클릭": COLORS["accent_blue"],
            "이미지 클릭": COLORS["scroll_purple"],
            "스크롤": COLORS["scroll_purple"],
            "드래그": COLORS["warning"],
        }
        ma_color = type_colors.get(ma_type, COLORS["text_muted"])

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
        action_card = ctk.CTkFrame(parent, fg_color=COLORS["bg_glass"], corner_radius=IOS_METRICS["control_radius_small"])
        if before_widget is not None:
            action_card.pack(fill="x", padx=(30, 0), pady=2, before=before_widget)
        else:
            action_card.pack(fill="x", padx=(30, 0), pady=2)

        card_inner = ctk.CTkFrame(action_card, fg_color="transparent")
        card_inner.pack(fill="x", padx=10, pady=6)

        # 번호
        ctk.CTkLabel(
            card_inner, text=f"{ai + 1}.",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COLORS["success"], width=24,
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
        self._build_action_card_buttons(card_inner, watch_idx, ai, action)
        return action_card

    def _build_action_card_buttons(self, card_inner, watch_idx, ai, action):
        """액션 카드의 버튼들 생성"""
        ma_type = action.get("type", "알 수 없음")

        # 삭제 버튼
        def delete_action(i=watch_idx, a=ai):
            if i < len(self._watches_data) and "monitor_actions" in self._watches_data[i]:
                actions = self._watches_data[i]["monitor_actions"]
                if 0 <= a < len(actions):
                    actions.pop(a)
                    self._refresh_monitor_actions(i)

        ctk.CTkButton(
            card_inner, text="✕", width=26, height=22,
            font=ctk.CTkFont(size=10),
            fg_color=COLORS["danger"], hover_color=COLORS["danger_hover"],
            text_color=COLORS["text_primary"], corner_radius=IOS_METRICS["control_radius_small"],
            command=delete_action,
        ).pack(side="right", padx=(2, 0))

        # 복사 버튼
        import copy
        def copy_action(i=watch_idx, a=ai):
            if i < len(self._watches_data) and "monitor_actions" in self._watches_data[i]:
                actions = self._watches_data[i]["monitor_actions"]
                if 0 <= a < len(actions):
                    self._monitor_action_clipboard = copy.deepcopy(actions[a])
                    logger.info(f"[모니터링] 액션 복사: {actions[a].get('type', '알수없음')}")

        ctk.CTkButton(
            card_inner, text="CP", width=26, height=22,
            font=ctk.CTkFont(size=10),
            fg_color=COLORS["scroll_purple"], hover_color=COLORS["search_radius_purple_hover"],
            text_color=COLORS["text_primary"], corner_radius=IOS_METRICS["control_radius_small"],
            command=copy_action,
        ).pack(side="right", padx=(2, 0))

        # 실행/정지 토글 버튼
        play_btn_ref = [None]
        stop_flag_ref = [False]
        running_ref = [False]

        def toggle_play(i=watch_idx, a=ai):
            logger.debug(f"[모니터링 테스트] 버튼 클릭: 감시{i+1}, 액션{a+1}, running={running_ref[0]}")

            if running_ref[0]:
                logger.info("[모니터링 테스트] 정지 요청")
                stop_flag_ref[0] = True
                return

            if i >= len(self._watches_data) or "monitor_actions" not in self._watches_data[i]:
                logger.warning(f"[모니터링 테스트] 유효하지 않은 인덱스: i={i}, len={len(self._watches_data)}")
                return

            all_actions_to_run = []
            current_watch_actions = self._watches_data[i].get("monitor_actions", [])
            logger.debug(f"[모니터링 테스트] 감시{i+1} 액션 수: {len(current_watch_actions)}, 시작: {a+1}번")
            all_actions_to_run.extend(current_watch_actions[a:])

            logger.info(f"[모니터링 테스트] 실행 대상: 감시{i+1}의 {a+1}~{len(current_watch_actions)}번 액션 ({len(all_actions_to_run)}개)")

            if not all_actions_to_run:
                logger.warning("[모니터링 테스트] 실행할 액션 없음")
                return

            for idx, act in enumerate(all_actions_to_run):
                act_type = act.get('type', '?')
                act_detail = ""
                if act_type == "텍스트 입력":
                    act_detail = f'"{act.get("text", "")[:20]}"'
                elif act_type == "키 입력":
                    act_detail = f'{act.get("keys", [])}'
                elif act_type == "마우스 클릭":
                    act_detail = f'({act.get("x")}, {act.get("y")})'
                elif act_type == "이미지 클릭":
                    act_detail = f'{Path(act.get("image", "")).name if act.get("image") else "?"}'
                logger.debug(f"[모니터링 테스트] 대기열[{idx+1}]: {act_type} {act_detail}")

            from ..player.rule_executor import get_rule_executor
            executor = get_rule_executor()

            running_ref[0] = True
            stop_flag_ref[0] = False
            if play_btn_ref[0]:
                play_btn_ref[0].configure(text="■", fg_color=COLORS["success"], hover_color=COLORS["green_hover"])

            def run_actions():
                total = len(all_actions_to_run)
                logger.info(f"[모니터링 테스트] ▶ 시작: {total}개 액션")
                success_count = 0
                fail_count = 0

                try:
                    for idx, act in enumerate(all_actions_to_run):
                        if stop_flag_ref[0]:
                            logger.info(f"[모니터링 테스트] ■ 중지됨 ({idx}/{total})")
                            break

                        action_type = act.get('type', '알수없음')
                        repeat_count = act.get('repeat_count', 1)
                        wait_after = act.get('wait_after', 0.3)

                        detail = ""
                        if action_type == "텍스트 입력":
                            detail = f' "{act.get("text", "")[:15]}..."' if len(act.get("text", "")) > 15 else f' "{act.get("text", "")}"'
                        elif action_type == "키 입력":
                            detail = f' [{"+".join(act.get("keys", []))}]'
                        elif action_type == "마우스 클릭":
                            detail = f' ({act.get("x")}, {act.get("y")})'
                        elif action_type == "이미지 클릭":
                            img_name = Path(act.get("image", "")).name if act.get("image") else "?"
                            detail = f' {img_name}'

                        logger.info(f"[모니터링 테스트] [{idx+1}/{total}] {action_type}{detail}")

                        try:
                            for rep in range(repeat_count):
                                if stop_flag_ref[0]:
                                    break
                                if repeat_count > 1:
                                    logger.debug(f"[모니터링 테스트]   반복 {rep+1}/{repeat_count}")

                                result = executor.test_single_monitor_action(act)
                                if result[0]:
                                    logger.debug(f"[모니터링 테스트]   ✓ 성공: {result[1]}")
                                else:
                                    logger.warning(f"[모니터링 테스트]   ✗ 실패: {result[1]}")

                                if rep < repeat_count - 1:
                                    time.sleep(act.get('repeat_delay', 0.5))

                            success_count += 1

                            if not stop_flag_ref[0] and wait_after > 0:
                                logger.debug(f"[모니터링 테스트]   대기: {wait_after}초")
                                time.sleep(wait_after)

                        except Exception as e:
                            logger.error(f"[모니터링 테스트]   ✗ 예외: {e}")
                            fail_count += 1

                    logger.info(f"[모니터링 테스트] ★ 완료 (성공: {success_count}, 실패: {fail_count})")

                except Exception as e:
                    logger.error(f"[모니터링 테스트] 전체 오류: {e}")
                finally:
                    running_ref[0] = False
                    stop_flag_ref[0] = False
                    try:
                        if play_btn_ref[0]:
                            self.after(0, lambda: play_btn_ref[0].configure(text="▶", fg_color=COLORS["accent_orange"], hover_color=COLORS["confidence_amber_hover"]))
                    except (tk.TclError, RuntimeError, AttributeError) as e:
                        logger.debug(f"[모니터링 테스트] 버튼 복원 실패: {e}")

            threading.Thread(target=run_actions, daemon=True).start()

        play_btn = ctk.CTkButton(
            card_inner, text="▶", width=26, height=22,
            font=ctk.CTkFont(size=10),
            fg_color=COLORS["accent_orange"], hover_color=COLORS["confidence_amber_hover"],
            text_color=COLORS["text_primary"], corner_radius=IOS_METRICS["control_radius_small"],
            command=toggle_play,
        )
        play_btn.pack(side="right", padx=(2, 0))
        play_btn_ref[0] = play_btn

        # 위/아래 이동 버튼
        move_frame = ctk.CTkFrame(card_inner, fg_color=COLORS["bg_elevated"], corner_radius=IOS_METRICS["control_radius_small"])
        move_frame.pack(side="right", padx=(2, 0))

        def move_up(i=watch_idx, a=ai):
            if i < len(self._watches_data) and "monitor_actions" in self._watches_data[i]:
                actions = self._watches_data[i]["monitor_actions"]
                if 0 < a < len(actions):
                    actions[a], actions[a-1] = actions[a-1], actions[a]
                    self._refresh_monitor_actions(i)

        def move_down(i=watch_idx, a=ai):
            if i < len(self._watches_data) and "monitor_actions" in self._watches_data[i]:
                actions = self._watches_data[i]["monitor_actions"]
                if 0 <= a < len(actions) - 1:
                    actions[a], actions[a+1] = actions[a+1], actions[a]
                    self._refresh_monitor_actions(i)

        ctk.CTkButton(
            move_frame, text="▲", width=22, height=18,
            font=ctk.CTkFont(size=10),
            fg_color="transparent", hover_color=COLORS["accent_blue"],
            text_color=COLORS["text_secondary"], corner_radius=IOS_METRICS["control_radius_small"],
            command=move_up,
        ).pack(side="top")

        ctk.CTkButton(
            move_frame, text="▼", width=22, height=18,
            font=ctk.CTkFont(size=10),
            fg_color="transparent", hover_color=COLORS["accent_blue"],
            text_color=COLORS["text_secondary"], corner_radius=IOS_METRICS["control_radius_small"],
            command=move_down,
        ).pack(side="top")

        # 반복 횟수 버튼
        repeat_count = action.get("repeat_count", 1)

        def edit_repeat(i=watch_idx, a=ai):
            if i >= len(self._watches_data) or "monitor_actions" not in self._watches_data[i]:
                return
            actions = self._watches_data[i]["monitor_actions"]
            if a < 0 or a >= len(actions):
                return
            act = actions[a]

            dlg = ctk.CTkToplevel(self)
            dlg.title("반복 설정")
            dlg.geometry("350x420")
            dlg.resizable(False, False)
            dlg.configure(fg_color=COLORS["bg_dark"])
            dlg.transient(self)
            dlg.grab_set()

            dlg.update_idletasks()
            dx = (dlg.winfo_screenwidth() - 350) // 2
            dy = (dlg.winfo_screenheight() - 420) // 2
            dlg.geometry(f"+{dx}+{dy}")

            main_frame = ctk.CTkFrame(dlg, fg_color="transparent")
            main_frame.pack(fill="both", expand=True, padx=20, pady=15)

            # 반복 횟수
            ctk.CTkLabel(main_frame, text="반복 횟수",
                         font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", pady=(0, 5))

            count_entry = ctk.CTkEntry(main_frame, width=200, height=38, font=ctk.CTkFont(size=14))
            count_entry.insert(0, str(act.get("repeat_count", 1)))
            count_entry.pack(anchor="w")

            ctk.CTkLabel(main_frame, text="1 = 1회 실행, 2 = 2회 반복...",
                         font=ctk.CTkFont(size=11), text_color=COLORS["text_secondary"]).pack(anchor="w", pady=(5, 0))

            # 반복 대기시간
            ctk.CTkLabel(main_frame, text="반복 대기시간 (초)",
                         font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", pady=(15, 5))

            delay_entry = ctk.CTkEntry(main_frame, width=200, height=38, font=ctk.CTkFont(size=14))
            delay_entry.insert(0, f"{act.get('repeat_delay', 0.5):.2f}")
            delay_entry.pack(anchor="w")

            ctk.CTkLabel(main_frame, text="반복 사이의 대기시간",
                         font=ctk.CTkFont(size=11), text_color=COLORS["text_secondary"]).pack(anchor="w", pady=(5, 0))

            # 랜덤 대기시간
            delay_random_var = ctk.BooleanVar(value=act.get("repeat_delay_random", False))
            ctk.CTkCheckBox(main_frame, text="랜덤시간 활성화", variable=delay_random_var,
                            font=ctk.CTkFont(size=13)).pack(anchor="w", pady=(10, 5))

            delay_range_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
            delay_range_frame.pack(anchor="w", pady=5)

            ctk.CTkLabel(delay_range_frame, text="±범위:", font=ctk.CTkFont(size=12)).pack(side="left")
            delay_range_entry = ctk.CTkEntry(delay_range_frame, width=100, height=32, font=ctk.CTkFont(size=13))
            delay_range_entry.insert(0, f"{act.get('repeat_delay_random_range', 0.3):.2f}")
            delay_range_entry.pack(side="left", padx=(5, 10))
            ctk.CTkLabel(delay_range_frame, text="초", font=ctk.CTkFont(size=12),
                         text_color=COLORS["text_secondary"]).pack(side="left")

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
                    act["repeat_count"] = count
                    act["repeat_delay"] = delay
                    act["repeat_delay_random"] = delay_random_var.get()
                    act["repeat_delay_random_range"] = delay_range
                    if not self._refresh_monitor_action_card(i, a):
                        self._refresh_monitor_actions(i)
                    dlg.destroy()
                except ValueError:
                    from tkinter import messagebox
                    messagebox.showerror("오류", "숫자를 입력하세요")

            btn_frame = ctk.CTkFrame(dlg, fg_color="transparent")
            btn_frame.pack(pady=10)
            ctk.CTkButton(btn_frame, text="저장", width=100, height=36,
                          font=ctk.CTkFont(size=13, weight="bold"), command=save).pack(side="left", padx=8)
            ctk.CTkButton(btn_frame, text="취소", width=100, height=36,
                          font=ctk.CTkFont(size=13, weight="bold"), fg_color=COLORS["bg_card"],
                          command=dlg.destroy).pack(side="left", padx=8)

        ctk.CTkButton(
            card_inner, text=f"x{repeat_count}", width=32, height=22,
            font=ctk.CTkFont(size=10),
            fg_color=COLORS["accent_blue"] if repeat_count > 1 else COLORS["bg_elevated"],
            hover_color=COLORS["hover_blue"],
            text_color=COLORS["text_primary"] if repeat_count > 1 else COLORS["text_secondary"],
            corner_radius=IOS_METRICS["control_radius_small"],
            command=edit_repeat,
        ).pack(side="right", padx=(2, 0))

        # 대기시간 버튼
        wait_after = action.get("wait_after", 0.5)
        wait_random = action.get("wait_random", False)

        def edit_wait(i=watch_idx, a=ai):
            if i >= len(self._watches_data) or "monitor_actions" not in self._watches_data[i]:
                return
            actions = self._watches_data[i]["monitor_actions"]
            if a < 0 or a >= len(actions):
                return
            act = actions[a]

            wait_dlg = ctk.CTkToplevel(self)
            wait_dlg.title("대기시간 설정")
            wait_dlg.geometry("300x280")
            wait_dlg.transient(self)
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
                    if not self._refresh_monitor_action_card(i, a):
                        self._refresh_monitor_actions(i)
                    wait_dlg.destroy()
                except ValueError:
                    pass

            btn_frame = ctk.CTkFrame(wait_dlg, fg_color="transparent")
            btn_frame.pack(pady=20)

            ctk.CTkButton(
                btn_frame, text="저장", width=100, height=36,
                font=ctk.CTkFont(size=13, weight="bold"),
                fg_color=COLORS["accent_blue"], hover_color=COLORS["hover_blue"],
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
            fg_color=COLORS["success"] if wait_random else COLORS["bg_elevated"],
            hover_color=COLORS["green_hover"],
            text_color=COLORS["text_primary"] if wait_random else COLORS["text_secondary"],
            corner_radius=IOS_METRICS["control_radius_small"],
            command=edit_wait,
        ).pack(side="right", padx=(2, 0))

        # 이미지 클릭일 때만 범위 설정 + 이미지 변경 버튼
        if ma_type == "이미지 클릭":
            self._build_image_action_buttons(card_inner, watch_idx, ai, action)

    def _build_image_action_buttons(self, card_inner, watch_idx, ai, action):
        """이미지 클릭 액션용 추가 버튼 (범위 설정, 이미지 변경)"""
        search_region = action.get("search_region")
        has_region = search_region is not None

        def edit_region(i=watch_idx, a=ai):
            act = self._watches_data[i]["monitor_actions"][a]
            cur_region = act.get("search_region")

            region_dlg = ctk.CTkToplevel(self)
            region_dlg.title("검색 범위 설정")
            region_dlg.geometry("350x180")
            region_dlg.transient(self)
            region_dlg.grab_set()
            region_dlg.configure(fg_color=COLORS["bg_dark"])

            ctk.CTkLabel(
                region_dlg, text="이미지 검색 범위 설정",
                font=ctk.CTkFont(size=14, weight="bold")
            ).pack(pady=(15, 10))

            region_text = f"현재: ({cur_region[0]}, {cur_region[1]}) ~ ({cur_region[2]}, {cur_region[3]})" if cur_region else "현재: 전체화면"
            region_label = ctk.CTkLabel(region_dlg, text=region_text, font=ctk.CTkFont(size=11))
            region_label.pack(pady=5)

            btn_frame = ctk.CTkFrame(region_dlg, fg_color="transparent")
            btn_frame.pack(pady=15)

            def select_region_drag(existing=cur_region):
                region_dlg.withdraw()
                self.withdraw()

                def on_region_select(x1, y1, x2, y2):
                    act["search_region"] = [x1, y1, x2, y2]
                    if not self._refresh_monitor_action_card(i, a):
                        self._refresh_monitor_actions(i)
                    self.deiconify()
                    self.grab_set()
                    region_dlg.destroy()

                def on_cancel():
                    self.deiconify()
                    self.grab_set()
                    region_dlg.deiconify()
                    region_dlg.grab_set()

                from src.ui.analyzer_view import ScreenRegionSelector
                self._owner.after(100, lambda: ScreenRegionSelector(
                    self._owner, on_region_select, on_cancel, existing_region=existing
                ))

            ctk.CTkButton(
                btn_frame, text="드래그로 지정", width=100, height=32,
                fg_color=COLORS["accent_blue"], hover_color=COLORS["hover_blue"],
                command=select_region_drag,
            ).pack(side="left", padx=5)

            def clear_region():
                act["search_region"] = None
                if not self._refresh_monitor_action_card(i, a):
                    self._refresh_monitor_actions(i)
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
            fg_color=COLORS["scroll_purple"] if has_region else COLORS["bg_elevated"],
            hover_color=COLORS["search_radius_purple_hover"],
            text_color=COLORS["text_primary"] if has_region else COLORS["text_secondary"],
            corner_radius=IOS_METRICS["control_radius_small"],
            command=edit_region,
        ).pack(side="right", padx=(2, 0))

        # 이미지 변경 버튼
        def change_image(i=watch_idx, a=ai):
            act = self._watches_data[i]["monitor_actions"][a]
            current_img = act.get("image", "")

            templates_dir = DATA_DIR / "templates"
            templates_dir.mkdir(parents=True, exist_ok=True)

            initial_dir = str(Path(current_img).parent) if current_img and Path(current_img).exists() else str(templates_dir)

            new_path = filedialog.askopenfilename(
                title="이미지 선택",
                initialdir=initial_dir,
                filetypes=[("이미지 파일", "*.png *.jpg *.jpeg *.bmp")],
            )
            if new_path:
                old_image = act.get("image")
                if old_image:
                    try:
                        from ..analyzer.enhanced_matcher import get_enhanced_matcher
                        get_enhanced_matcher().invalidate_template(old_image)
                    except Exception:
                        pass

                act["image"] = new_path

                try:
                    from ..analyzer.enhanced_matcher import get_enhanced_matcher
                    get_enhanced_matcher().invalidate_template(new_path)
                except Exception:
                    pass

                if not self._refresh_monitor_action_card(i, a):
                    self._refresh_monitor_actions(i)
                logger.info(f"[모니터링 액션] 이미지 변경: {Path(new_path).name}")

        has_image = bool(action.get("image"))
        ctk.CTkButton(
            card_inner, text="IMG" if has_image else "IMG?", width=36, height=22,
            font=ctk.CTkFont(size=9),
            fg_color=COLORS["scroll_purple"] if has_image else COLORS["danger"],
            hover_color=COLORS["search_radius_purple_hover"] if has_image else COLORS["danger_hover"],
            text_color=COLORS["text_primary"],
            corner_radius=IOS_METRICS["control_radius_small"],
            command=change_image,
        ).pack(side="right", padx=(2, 0))

    # ------------------------------------------------------------------
    # Watch list refresh
    # ------------------------------------------------------------------

    def _refresh_watch_list(self):
        """감시 목록 UI 갱신"""
        self._cancel_watch_list_render_batch()
        for w in self._watch_widgets:
            if w is None:
                continue
            try:
                w.destroy()
            except (tk.TclError, RuntimeError):
                pass
        self._watch_widgets = [None] * len(self._watches_data)
        self._watch_row3_containers.clear()
        self._watch_action_card_widgets.clear()
        self._watch_action_count_labels.clear()

        self._render_watch_list_batch(0, self._watch_render_generation)

    def _cancel_watch_list_render_batch(self):
        self._watch_render_generation += 1
        after_id = getattr(self, "_watch_render_after_id", None)
        self._watch_render_after_id = None
        if after_id:
            try:
                self.after_cancel(after_id)
            except (tk.TclError, RuntimeError):
                pass

    def _schedule_watch_list_render_batch(self, callback):
        after_id = None

        def run_callback():
            if self._watch_render_after_id == after_id:
                self._watch_render_after_id = None
            callback()

        after_id = self.after(1, run_callback)
        self._watch_render_after_id = after_id

    def _render_watch_list_batch(self, start: int, generation: int):
        if generation != self._watch_render_generation:
            return
        if self._watch_scroll is None:
            return
        end = min(len(self._watches_data), start + self._watch_render_batch_size)
        for idx in range(start, end):
            self._watch_widgets[idx] = self._create_watch_item(idx, self._watches_data[idx])

        if end >= len(self._watches_data):
            self._watch_render_after_id = None
            return

        try:
            self._schedule_watch_list_render_batch(
                lambda: self._render_watch_list_batch(end, generation)
            )
        except (tk.TclError, RuntimeError):
            self._watch_render_after_id = None

    def _create_watch_item(self, idx: int, watch: dict, before_widget=None):
        """감시 항목 1개를 생성한다. 단일 행 갱신에서도 재사용한다."""
        if idx not in self._watch_collapsed:
            self._watch_collapsed[idx] = True if watch.get("image") else False

        is_collapsed = self._watch_collapsed.get(idx, True)

        item_frame = ctk.CTkFrame(
            self._watch_scroll,
            fg_color=COLORS["bg_glass"],
            corner_radius=IOS_METRICS["control_radius"],
        )
        if before_widget is not None:
            item_frame.pack(fill="x", pady=3, before=before_widget)
        else:
            item_frame.pack(fill="x", pady=3)

        self._build_watch_header(item_frame, idx, watch, is_collapsed)

        if not is_collapsed:
            self._build_watch_detail(item_frame, idx, watch)

        # 우클릭 메뉴
        self._bind_context_menu(item_frame, idx)
        return item_frame

    def _refresh_watch_item(self, idx: int) -> None:
        """목록 전체를 다시 그리지 않고 특정 감시 항목만 교체한다."""
        if idx < 0 or idx >= len(self._watches_data):
            return
        if idx >= len(self._watch_widgets):
            self._refresh_watch_list()
            return

        old_widget = self._watch_widgets[idx]
        if old_widget is None:
            return
        before_widget = self._watch_widgets[idx + 1] if idx + 1 < len(self._watch_widgets) else None
        self._watch_row3_containers.pop(idx, None)
        self._watch_action_card_widgets.pop(idx, None)
        self._watch_action_count_labels.pop(idx, None)

        try:
            old_widget.destroy()
        except (tk.TclError, RuntimeError):
            self._refresh_watch_list()
            return

        try:
            if before_widget is not None and not before_widget.winfo_exists():
                before_widget = None
        except (tk.TclError, RuntimeError):
            before_widget = None

        self._watch_widgets[idx] = self._create_watch_item(idx, self._watches_data[idx], before_widget=before_widget)

    def _build_watch_header(self, item_frame, idx, watch, is_collapsed):
        """감시 항목 헤더 줄 생성"""
        header_row = ctk.CTkFrame(item_frame, fg_color="transparent")
        header_row.pack(fill="x", padx=8, pady=(6, 3))

        # 접기/펼치기
        def toggle_collapse(i=idx):
            self._watch_collapsed[i] = not self._watch_collapsed.get(i, True)
            self._refresh_watch_item(i)

        ctk.CTkButton(
            header_row, text="▶" if is_collapsed else "▼", width=24, height=24,
            font=self._font(10),
            fg_color="transparent", hover_color=COLORS["bg_card"],
            text_color=COLORS["text_muted"],
            command=toggle_collapse,
        ).pack(side="left", padx=(0, 4))

        # 번호
        ctk.CTkLabel(
            header_row, text=f"{idx + 1}.",
            font=self._font(13, "bold"),
            text_color=COLORS["text_primary"],
            width=22,
        ).pack(side="left")

        # 썸네일
        thumb_frame = ctk.CTkFrame(
            header_row,
            fg_color=COLORS["bg_card"],
            width=32,
            height=32,
            corner_radius=IOS_METRICS["control_radius_small"],
        )
        thumb_frame.pack(side="left", padx=(4, 8))
        thumb_frame.pack_propagate(False)

        thumb_label = ctk.CTkLabel(thumb_frame, text="", width=28, height=28)
        thumb_label.pack(expand=True)

        self._schedule_watch_thumbnail(thumb_label, watch.get("image"), size=(28, 28))

        # 요약 정보
        monitor_actions = watch.get("monitor_actions", [])
        current_goto = watch.get("goto_index", 0)

        parts = ["감시"]
        if len(monitor_actions) > 0:
            parts.append(f"액션{len(monitor_actions)}개 실행")
        if current_goto >= 0 and current_goto < len(self._action_options):
            parts.append(f"액션{current_goto + 1} 점프")
            parts.append("복귀")

        summary_text = " - ".join(parts)

        ctk.CTkLabel(
            header_row, text=summary_text,
            font=self._font(11),
            text_color=COLORS["text_secondary"],
            anchor="w",
        ).pack(side="left", fill="x", expand=True)

        # 감시 재생 버튼
        self._build_watch_play_button(header_row, idx)

        # 삭제 버튼
        def delete_watch(i=idx):
            self._watches_data.pop(i)
            new_collapsed = {}
            for k, v in self._watch_collapsed.items():
                if k < i:
                    new_collapsed[k] = v
                elif k > i:
                    new_collapsed[k - 1] = v
            self._watch_collapsed.clear()
            self._watch_collapsed.update(new_collapsed)
            self._refresh_watch_list()

        ctk.CTkButton(
            header_row, text="✕", width=28, height=24,
            font=self._font(11),
            fg_color=COLORS["error"], hover_color=COLORS["danger_hover"],
            command=delete_watch,
        ).pack(side="right")

    def _build_watch_play_button(self, header_row, idx):
        """감시 항목 재생 버튼 생성"""
        watch_play_btn_ref = [None]
        watch_running_ref = [False]
        watch_stop_flag_ref = [False]

        def toggle_watch_play(i=idx):
            logger.debug(f"[감시 테스트] 버튼 클릭: 감시{i+1}, running={watch_running_ref[0]}")

            if watch_running_ref[0]:
                logger.info("[감시 테스트] 정지 요청")
                watch_stop_flag_ref[0] = True
                return

            if i >= len(self._watches_data):
                logger.warning(f"[감시 테스트] 유효하지 않은 인덱스: i={i}")
                return

            watch = self._watches_data[i]
            monitor_acts = watch.get("monitor_actions", [])
            goto_idx = watch.get("goto_index", 0)

            logger.info(f"[감시 테스트] 감시{i+1}: 모니터링 액션 {len(monitor_acts)}개 + 점프액션{goto_idx+1}")

            from ..player.rule_executor import get_rule_executor
            executor = get_rule_executor()

            watch_running_ref[0] = True
            watch_stop_flag_ref[0] = False
            if watch_play_btn_ref[0]:
                watch_play_btn_ref[0].configure(text="■", fg_color=COLORS["success"], hover_color=COLORS["green_hover"])

            def run_watch():
                try:
                    logger.info(f"[감시 테스트] ▶ 모니터링 액션 시작 ({len(monitor_acts)}개)")
                    for action_idx, act in enumerate(monitor_acts):
                        if watch_stop_flag_ref[0]:
                            logger.info(f"[감시 테스트] ■ 중지됨 (모니터링 {action_idx}/{len(monitor_acts)})")
                            return

                        action_type = act.get('type', '알수없음')
                        repeat_count = act.get('repeat_count', 1)
                        wait_after = act.get('wait_after', 0.3)

                        logger.info(f"[감시 테스트] 모니터링 [{action_idx+1}/{len(monitor_acts)}] {action_type}")

                        try:
                            for rep in range(repeat_count):
                                if watch_stop_flag_ref[0]:
                                    break
                                result = executor.test_single_monitor_action(act)
                                if result[0]:
                                    logger.debug(f"[감시 테스트]   ✓ 성공: {result[1]}")
                                else:
                                    logger.warning(f"[감시 테스트]   ✗ 실패: {result[1]}")
                                if rep < repeat_count - 1:
                                    time.sleep(act.get('repeat_delay', 0.5))

                            if not watch_stop_flag_ref[0] and wait_after > 0:
                                time.sleep(wait_after)

                        except Exception as e:
                            logger.error(f"[감시 테스트]   ✗ 예외: {e}")

                    # 점프 액션 실행
                    if not watch_stop_flag_ref[0] and 0 <= goto_idx < len(self._plan_rules):
                        jump_rule = self._plan_rules[goto_idx]
                        action_type_name = {
                            "click": "클릭", "double_click": "더블클릭", "right_click": "우클릭",
                            "type": "입력", "hotkey": "단축키", "key_press": "키", "scroll": "스크롤", "drag": "드래그",
                        }.get(jump_rule.action_type, jump_rule.action_type or "동작")
                        logger.info(f"[감시 테스트] ▶ 점프 액션 실행: 액션{goto_idx+1} ({action_type_name})")

                        try:
                            success, msgs = executor.test_rule_with_children(jump_rule, stop_flag=watch_stop_flag_ref)
                            msg = "; ".join(msgs) if msgs else "완료"
                            if success:
                                logger.info(f"[감시 테스트]   ✓ 점프 액션 성공 (자식 포함): {msg}")
                            else:
                                logger.warning(f"[감시 테스트]   ✗ 점프 액션 실패: {msg}")
                        except Exception as e:
                            logger.error(f"[감시 테스트]   ✗ 점프 액션 예외: {e}")

                    logger.info(f"[감시 테스트] ★ 감시{i+1} 테스트 완료")

                except Exception as e:
                    logger.error(f"[감시 테스트] 전체 오류: {e}")
                finally:
                    watch_running_ref[0] = False
                    watch_stop_flag_ref[0] = False
                    try:
                        if watch_play_btn_ref[0]:
                            self.after(0, lambda: watch_play_btn_ref[0].configure(text="▶", fg_color=COLORS["accent_orange"], hover_color=COLORS["confidence_amber_hover"]))
                    except (tk.TclError, RuntimeError, AttributeError) as e:
                        logger.debug(f"[감시 테스트] 버튼 복원 실패: {e}")

            threading.Thread(target=run_watch, daemon=True).start()

        watch_play_btn = ctk.CTkButton(
            header_row, text="▶", width=26, height=24,
            font=ctk.CTkFont(size=10),
            fg_color=COLORS["accent_orange"], hover_color=COLORS["confidence_amber_hover"],
            text_color=COLORS["text_primary"], corner_radius=IOS_METRICS["control_radius_small"],
            command=toggle_watch_play,
        )
        watch_play_btn.pack(side="right", padx=(0, 5))
        watch_play_btn_ref[0] = watch_play_btn

    def _build_watch_detail(self, item_frame, idx, watch):
        """감시 항목 상세 설정 (펼쳤을 때)"""
        detail_frame = ctk.CTkFrame(item_frame, fg_color="transparent")
        detail_frame.pack(fill="x", padx=12, pady=(0, 8))

        # 첫 줄: 이미지 선택 + goto 드롭다운
        row1 = ctk.CTkFrame(detail_frame, fg_color="transparent")
        row1.pack(fill="x", pady=(0, 5))

        def select_watch_image(i=idx):
            templates_dir = DATA_DIR / "templates"
            templates_dir.mkdir(parents=True, exist_ok=True)
            path = filedialog.askopenfilename(
                title="감시 이미지 선택",
                initialdir=str(templates_dir),
                filetypes=[("이미지 파일", "*.png *.jpg *.jpeg *.bmp")],
            )
            if path:
                self._watches_data[i]["image"] = path
                self._refresh_watch_item(i)

        ctk.CTkLabel(
            row1, text="이미지:",
            font=self._font(11),
            text_color=COLORS["text_muted"],
        ).pack(side="left", padx=(0, 5))

        img_name = Path(watch["image"]).name[:20] + "..." if watch.get("image") and len(Path(watch["image"]).name) > 20 else (Path(watch["image"]).name if watch.get("image") else "없음")
        ctk.CTkLabel(
            row1, text=img_name,
            font=self._font(11),
            text_color=COLORS["accent"] if watch.get("image") else COLORS["text_muted"],
        ).pack(side="left", padx=(0, 8))

        ctk.CTkButton(
            row1, text="선택", width=45, height=24,
            font=self._font(11),
            fg_color=COLORS["accent_blue"], hover_color=COLORS["hover_blue"],
            command=select_watch_image,
        ).pack(side="left", padx=(0, 10))

        # 점프 조건 버튼
        condition_image = watch.get("condition_image")
        condition_text = "조건" if not condition_image else "조건✓"
        condition_color = COLORS["bg_card_hover"] if not condition_image else COLORS["confidence_amber"]

        condition_btn = ctk.CTkButton(
            row1, text=condition_text, width=50, height=24,
            font=self._font(11),
            fg_color=condition_color,
            hover_color=COLORS["confidence_amber_hover"] if condition_image else COLORS["bg_card"],
            command=lambda i=idx: self._edit_condition(i),
        )
        condition_btn.pack(side="left", padx=(0, 10))
        # 버튼 참조 저장 (나중에 업데이트용)
        watch["_condition_btn"] = condition_btn

        # 점프할 액션 선택 (드롭다운)
        ctk.CTkLabel(
            row1, text="→",
            font=self._font(12, "bold"),
            text_color=COLORS["text_secondary"],
        ).pack(side="left", padx=(0, 5))

        option_texts = [opt[0] for opt in self._action_options]
        current_goto = watch.get("goto_index", 0)
        current_display = "점프 안 함"
        for opt_text, opt_idx in self._action_options:
            if opt_idx == current_goto:
                current_display = opt_text
                break

        goto_combo = ctk.CTkComboBox(
            row1,
            values=option_texts,
            width=180,
            height=26,
            font=self._font(11),
            dropdown_font=self._font(10),
            state="readonly",
        )
        goto_combo.pack(side="left")
        if current_display:
            goto_combo.set(current_display)

        def on_goto_select(choice, i=idx):
            for opt_text, opt_idx in self._action_options:
                if opt_text == choice:
                    self._watches_data[i]["goto_index"] = opt_idx
                    break

        goto_combo.configure(command=lambda choice, i=idx: on_goto_select(choice, i))

        # 두 번째 줄: 검색 범위 설정
        row2 = ctk.CTkFrame(detail_frame, fg_color="transparent")
        row2.pack(fill="x", pady=(0, 5))

        ctk.CTkLabel(
            row2, text="검색범위:",
            font=self._font(11),
            text_color=COLORS["text_muted"],
        ).pack(side="left", padx=(0, 5))

        region = watch.get("search_region")
        region_text = f"({region[0]}, {region[1]}) ~ ({region[2]}, {region[3]})" if region else "전체화면"

        region_label = ctk.CTkLabel(
            row2, text=region_text,
            font=self._font(11),
            text_color=COLORS["accent"] if region else COLORS["text_secondary"],
            width=150,
            anchor="w",
        )
        region_label.pack(side="left", padx=(0, 8))

        def select_region(i=idx, rlbl=region_label):
            self.withdraw()
            self.after(100, lambda: self._open_region_selector(i, rlbl))

        ctk.CTkButton(
            row2, text="범위 지정", width=65, height=24,
            font=self._font(10),
            fg_color=COLORS["accent_blue"], hover_color=COLORS["hover_blue"],
            command=select_region,
        ).pack(side="left", padx=(0, 5))

        def clear_region(i=idx, rlbl=region_label):
            self._watches_data[i]["search_region"] = None
            rlbl.configure(text="전체화면", text_color=COLORS["text_secondary"])

        ctk.CTkButton(
            row2, text="초기화", width=45, height=24,
            font=self._font(10),
            fg_color=COLORS["bg_card"], hover_color=COLORS["bg_card_hover"],
            text_color=COLORS["text_secondary"],
            command=clear_region,
        ).pack(side="left")

        # 인식률 설정 줄
        row_conf = ctk.CTkFrame(detail_frame, fg_color="transparent")
        row_conf.pack(fill="x", pady=(0, 5))

        ctk.CTkLabel(
            row_conf, text="인식률:",
            font=self._font(11),
            text_color=COLORS["text_muted"],
        ).pack(side="left", padx=(0, 5))

        watch_conf = watch.get("confidence", 0.65)
        conf_label = ctk.CTkLabel(
            row_conf, text=f"{int(watch_conf * 100)}%",
            font=self._font(11),
            text_color=COLORS["accent"],
            width=35,
        )
        conf_label.pack(side="left", padx=(0, 5))

        def on_conf_change(val, i=idx, lbl=conf_label):
            self._watches_data[i]["confidence"] = float(val)
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

        # 키보드 좌우 화살표로 1%씩 조절
        def on_conf_key(event, slider=conf_slider, i=idx, lbl=conf_label):
            current = slider.get()
            if event.keysym == "Left":
                new_val = max(0.3, current - 0.01)
            elif event.keysym == "Right":
                new_val = min(1.0, current + 0.01)
            else:
                return
            slider.set(new_val)
            self._watches_data[i]["confidence"] = new_val
            lbl.configure(text=f"{int(new_val * 100)}%")

        conf_slider.bind("<Left>", on_conf_key)
        conf_slider.bind("<Right>", on_conf_key)

        # 모니터링 액션 목록
        row3 = ctk.CTkFrame(detail_frame, fg_color="transparent")
        row3.pack(fill="x", pady=(0, 5))
        self._watch_row3_containers[idx] = row3
        self._refresh_monitor_actions(idx)

    def _bind_context_menu(self, item_frame, idx):
        """우클릭 메뉴 바인딩"""

        def show_context_menu(event, i=idx):
            menu = tk.Menu(item_frame, tearoff=0)

            def paste_monitor_action():
                clipboard = get_action_clipboard()
                if clipboard is None:
                    messagebox.showinfo("클립보드 비어있음", "먼저 계획수정에서 액션을 복사하세요.")
                    return

                all_actions = collect_all_actions(clipboard)
                monitor_actions = []
                for act in all_actions:
                    ma = self._convert_rule_to_monitor_action(act)
                    if ma:
                        monitor_actions.append(ma)

                if monitor_actions:
                    if "monitor_actions" not in self._watches_data[i]:
                        self._watches_data[i]["monitor_actions"] = []
                    self._watches_data[i]["monitor_actions"].extend(monitor_actions)
                    if self._watch_collapsed.get(i, True):
                        self._watch_collapsed[i] = False
                        self._refresh_watch_item(i)
                    elif len(monitor_actions) == 1 and self._append_monitor_action_card(i):
                        pass
                    else:
                        self._refresh_monitor_actions(i)
                else:
                    messagebox.showwarning("변환 실패", "이 액션은 모니터링 액션으로 변환할 수 없습니다.")

            def clear_all_actions():
                self._watches_data[i]["monitor_actions"] = []
                self._refresh_watch_item(i)

            menu.add_command(label="모니터링 액션 붙여넣기", command=paste_monitor_action)
            if self._watches_data[i].get("monitor_actions"):
                menu.add_separator()
                menu.add_command(label="모니터링 액션 전체 삭제", command=clear_all_actions)

            menu.tk_popup(event.x_root, event.y_root)

        item_frame.bind("<Button-3>", show_context_menu)
        for child in item_frame.winfo_children():
            child.bind("<Button-3>", lambda e, i=idx: show_context_menu(e, i))

    # ------------------------------------------------------------------
    # Region selector
    # ------------------------------------------------------------------

    def _open_region_selector(self, watch_index, region_label):
        """드래그로 검색 범위 선택"""
        from src.ui.analyzer_view import ScreenRegionSelector

        existing_region = self._watches_data[watch_index].get("search_region")

        def on_region_select(x1, y1, x2, y2):
            self._watches_data[watch_index]["search_region"] = [x1, y1, x2, y2]
            region_label.configure(
                text=f"({x1}, {y1}) ~ ({x2}, {y2})",
                text_color=COLORS["accent"]
            )
            self.deiconify()
            self.grab_set()
            self.focus_force()

        def on_cancel():
            self.deiconify()
            self.grab_set()
            self.focus_force()

        ScreenRegionSelector(self._owner, on_region_select, on_cancel, existing_region=existing_region)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _add_watch(self):
        """감시 항목 추가"""
        new_idx = len(self._watches_data)
        self._watches_data.append({"image": None, "goto_index": 0, "search_region": None, "monitor_actions": [], "confidence": 0.65, "condition_image": None, "condition_search_region": None, "condition_confidence": 0.80})
        self._watch_collapsed[new_idx] = False
        self._refresh_watch_list()

    def _edit_condition(self, watch_idx: int):
        """점프 조건 이미지 편집"""
        if watch_idx >= len(self._watches_data):
            return

        watch = self._watches_data[watch_idx]
        current_condition = watch.get("condition_image")
        current_region = watch.get("condition_search_region")
        current_confidence = watch.get("condition_confidence", 0.80)

        # 조건 설정 다이얼로그
        dialog = ctk.CTkToplevel(self)
        dialog.title("점프 조건 설정")
        dialog.geometry("400x420")
        dialog.resizable(False, False)
        dialog.transient(self)
        dialog.grab_set()

        # 화면 중앙
        dialog.update_idletasks()
        x = (dialog.winfo_screenwidth() - 400) // 2
        y = (dialog.winfo_screenheight() - 420) // 2
        dialog.geometry(f"+{x}+{y}")

        main = ctk.CTkFrame(dialog, fg_color="transparent")
        main.pack(fill="both", expand=True, padx=20, pady=15)

        ctk.CTkLabel(
            main, text="점프 조건 이미지",
            font=ctk.CTkFont(size=14, weight="bold"),
        ).pack(anchor="w")

        ctk.CTkLabel(
            main, text="이 이미지가 화면에 없어야 점프 액션이 실행됩니다.\n조건 없음 선택 시 항상 점프합니다.",
            font=ctk.CTkFont(size=11),
            text_color=COLORS["text_secondary"],
            justify="left",
        ).pack(anchor="w", pady=(5, 15))

        # 현재 조건 이미지 표시
        preview_frame = ctk.CTkFrame(main, fg_color=COLORS["bg_card"], corner_radius=IOS_METRICS["control_radius"])
        preview_frame.pack(fill="x", pady=(0, 15))

        preview_inner = ctk.CTkFrame(preview_frame, fg_color="transparent")
        preview_inner.pack(fill="x", padx=15, pady=10)

        # 이미지 미리보기
        preview_label = ctk.CTkLabel(
            preview_inner, text="없음", width=60, height=60,
            fg_color=COLORS["bg_card_hover"], corner_radius=IOS_METRICS["control_radius_small"],
        )
        preview_label.pack(side="left")

        # 이미지 이름
        img_name = Path(current_condition).name if current_condition else "조건 없음"
        name_label = ctk.CTkLabel(
            preview_inner, text=img_name,
            font=ctk.CTkFont(size=11),
            text_color=COLORS["text_primary"] if current_condition else COLORS["text_muted"],
        )
        name_label.pack(side="left", padx=(15, 0))

        # 썸네일 로드
        if current_condition and Path(current_condition).exists():
            thumb = self._load_thumbnail(current_condition, size=(60, 60))
            if thumb:
                preview_label.configure(image=thumb, text="")
                preview_label._thumb_ref = thumb

        # 선택된 이미지 경로 저장
        selected_path = [current_condition]

        def select_image():
            path = filedialog.askopenfilename(filetypes=[("이미지", "*.png *.jpg *.bmp")])
            if path:
                dest = DATA_DIR / "templates" / f"condition_{Path(path).name}"
                dest.parent.mkdir(parents=True, exist_ok=True)
                if not dest.exists():
                    shutil.copy(path, dest)
                selected_path[0] = str(dest)
                name_label.configure(text=Path(dest).name, text_color=COLORS["text_primary"])
                thumb = self._load_thumbnail(str(dest), size=(60, 60))
                if thumb:
                    preview_label.configure(image=thumb, text="")
                    preview_label._thumb_ref = thumb

        def clear_condition():
            selected_path[0] = None
            name_label.configure(text="조건 없음", text_color=COLORS["text_muted"])
            preview_label.configure(image=None, text="없음")

        # 버튼들
        btn_frame = ctk.CTkFrame(main, fg_color="transparent")
        btn_frame.pack(fill="x", pady=(0, 10))

        ctk.CTkButton(
            btn_frame, text="이미지 선택", width=100, height=30,
            fg_color=COLORS["accent_blue"], hover_color=COLORS["hover_blue"],
            command=select_image,
        ).pack(side="left", padx=(0, 10))

        ctk.CTkButton(
            btn_frame, text="조건 없음", width=100, height=30,
            fg_color=COLORS["bg_card_hover"], hover_color=COLORS["bg_card"],
            text_color=COLORS["text_secondary"],
            command=clear_condition,
        ).pack(side="left")

        # 검색 범위 설정
        region_frame = ctk.CTkFrame(main, fg_color=COLORS["bg_card"], corner_radius=IOS_METRICS["control_radius"])
        region_frame.pack(fill="x", pady=(10, 0))

        region_inner = ctk.CTkFrame(region_frame, fg_color="transparent")
        region_inner.pack(fill="x", padx=15, pady=10)

        ctk.CTkLabel(
            region_inner, text="검색 범위:",
            font=ctk.CTkFont(size=11),
            text_color=COLORS["text_muted"],
        ).pack(side="left")

        # 검색 범위 상태 저장
        selected_region = [current_region]

        region_text = f"({current_region[0]}, {current_region[1]}) ~ ({current_region[2]}, {current_region[3]})" if current_region else "전체 화면"
        region_label = ctk.CTkLabel(
            region_inner, text=region_text,
            font=ctk.CTkFont(size=11),
            text_color=COLORS["accent"] if current_region else COLORS["text_secondary"],
            width=150, anchor="w",
        )
        region_label.pack(side="left", padx=(10, 10))

        def select_region():
            dialog.withdraw()
            self.withdraw()
            self.after(100, lambda: _open_condition_region_selector())

        def _open_condition_region_selector():
            from .analyzer_view import ScreenRegionSelector

            def on_region_select(x1, y1, x2, y2):
                selected_region[0] = [x1, y1, x2, y2]
                region_label.configure(
                    text=f"({x1}, {y1}) ~ ({x2}, {y2})",
                    text_color=COLORS["accent"]
                )
                self.deiconify()
                dialog.deiconify()
                dialog.grab_set()
                dialog.focus_force()

            def on_cancel():
                self.deiconify()
                dialog.deiconify()
                dialog.grab_set()
                dialog.focus_force()

            ScreenRegionSelector(self, on_region_select, on_cancel, existing_region=selected_region[0])

        def clear_region():
            selected_region[0] = None
            region_label.configure(text="전체 화면", text_color=COLORS["text_secondary"])

        ctk.CTkButton(
            region_inner, text="범위 지정", width=70, height=24,
            font=ctk.CTkFont(size=10),
            fg_color=COLORS["accent_blue"], hover_color=COLORS["hover_blue"],
            command=select_region,
        ).pack(side="left", padx=(0, 5))

        ctk.CTkButton(
            region_inner, text="초기화", width=50, height=24,
            font=ctk.CTkFont(size=10),
            fg_color=COLORS["bg_card_hover"], hover_color=COLORS["bg_card"],
            text_color=COLORS["text_secondary"],
            command=clear_region,
        ).pack(side="left")

        # 인식률 설정
        conf_frame = ctk.CTkFrame(main, fg_color=COLORS["bg_card"], corner_radius=IOS_METRICS["control_radius"])
        conf_frame.pack(fill="x", pady=(10, 0))

        conf_inner = ctk.CTkFrame(conf_frame, fg_color="transparent")
        conf_inner.pack(fill="x", padx=15, pady=10)

        ctk.CTkLabel(
            conf_inner, text="인식률:",
            font=ctk.CTkFont(size=11),
            text_color=COLORS["text_muted"],
        ).pack(side="left")

        conf_var = ctk.DoubleVar(value=current_confidence * 100)
        conf_slider = ctk.CTkSlider(
            conf_inner, from_=30, to=100, number_of_steps=70,
            variable=conf_var, width=150,
        )
        conf_slider.pack(side="left", padx=(10, 10))

        conf_label = ctk.CTkLabel(
            conf_inner, text=f"{int(current_confidence * 100)}%",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COLORS["accent"],
            width=45,
        )
        conf_label.pack(side="left")

        def update_conf_label(*args):
            conf_label.configure(text=f"{int(conf_var.get())}%")
        conf_var.trace_add("write", update_conf_label)

        # 키보드 좌우 화살표로 1%씩 조절
        def on_cond_conf_key(event):
            current = conf_var.get()
            if event.keysym == "Left":
                conf_var.set(max(30, current - 1))
            elif event.keysym == "Right":
                conf_var.set(min(100, current + 1))

        conf_slider.bind("<Left>", on_cond_conf_key)
        conf_slider.bind("<Right>", on_cond_conf_key)

        # 저장/취소 버튼
        bottom_frame = ctk.CTkFrame(main, fg_color="transparent")
        bottom_frame.pack(fill="x", pady=(15, 0))

        def save_condition():
            self._watches_data[watch_idx]["condition_image"] = selected_path[0]
            self._watches_data[watch_idx]["condition_search_region"] = selected_region[0]
            self._watches_data[watch_idx]["condition_confidence"] = conf_var.get() / 100.0
            # 버튼 텍스트 업데이트
            btn = watch.get("_condition_btn")
            if btn:
                if selected_path[0]:
                    btn.configure(text="조건✓", fg_color=COLORS["confidence_amber"], hover_color=COLORS["confidence_amber_hover"])
                else:
                    btn.configure(text="조건", fg_color=COLORS["bg_card_hover"], hover_color=COLORS["bg_card"])
            logger.info(f"[모니터링] 점프 조건 설정: {selected_path[0]}, 범위: {selected_region[0]}, 인식률: {conf_var.get():.0f}%")
            dialog.destroy()

        ctk.CTkButton(
            bottom_frame, text="저장", width=80, height=32,
            fg_color=COLORS["success"], hover_color=COLORS["green_hover"],
            command=save_condition,
        ).pack(side="left", padx=(0, 10))

        ctk.CTkButton(
            bottom_frame, text="취소", width=80, height=32,
            fg_color=COLORS["bg_card"], hover_color=COLORS["bg_card_hover"],
            text_color=COLORS["text_secondary"],
            command=dialog.destroy,
        ).pack(side="left")

    def _save(self):
        """저장"""
        rule = self._rule

        valid_watches = []
        total_monitor_actions = 0
        for w in self._watches_data:
            if w.get("image"):
                monitor_actions = w.get("monitor_actions", [])
                total_monitor_actions += len(monitor_actions)
                valid_watches.append({
                    "image": w["image"],
                    "goto_index": w.get("goto_index", 0),
                    "search_region": w.get("search_region"),
                    "monitor_actions": monitor_actions,
                    "confidence": w.get("confidence", 0.65),  # 감시 이미지별 인식률
                    "condition_image": w.get("condition_image"),  # 점프 조건 이미지
                    "condition_search_region": w.get("condition_search_region"),  # 조건 이미지 검색 범위
                    "condition_confidence": w.get("condition_confidence", 0.80),  # 조건 이미지 인식률
                })

        has_checkbox = self._is_monitoring_var.get()
        has_watches = len(valid_watches) > 0
        has_actions = total_monitor_actions > 0

        logger.info(f"[모니터링 저장] checkbox={has_checkbox}, watches={len(valid_watches)}, actions={total_monitor_actions}")

        rule.is_monitoring_mode = False
        rule.monitoring_watches = []

        if has_checkbox and has_watches and has_actions:
            rule.is_monitoring_mode = True
            rule.monitoring_watches = valid_watches
            logger.info(f"[모니터링] 활성화됨 - {total_monitor_actions}개 액션")
        else:
            logger.info(f"[모니터링] 비활성화됨 - 조건 미충족 (checkbox={has_checkbox}, watches={has_watches}, actions={has_actions})")

        logger.info(f"[모니터링 최종] is_monitoring_mode={rule.is_monitoring_mode}")

        self.was_saved = True
        self.destroy()
