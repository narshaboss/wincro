"""
WinCro 분석 화면 모듈

프리미엄 카드 기반 UI 디자인
"""

import customtkinter as ctk
import tkinter as tk
from tkinter import filedialog, Canvas
from typing import Optional, Callable
from pathlib import Path
import threading
import cv2
import numpy as np
import json
from PIL import Image, ImageTk

from ..utils.logger import get_logger
from ..utils.config import DATA_DIR
from ..utils.window_position import setup_window_position

PLANS_DIR = DATA_DIR / "plans"
from ..analyzer import get_video_analyzer, AnalysisProgress
from ..analyzer.automation_models import AutomationPlan, AutomationRule
from ..database import get_db, Recording, Sequence
from .main_window import BaseView, COLORS

logger = get_logger(__name__)

# 썸네일 캐시 (성능 최적화)
_thumbnail_cache = {}  # {cache_key: CTkImage}
_thumbnail_cache_lock = threading.Lock()
MAX_THUMBNAIL_CACHE = 100


def get_cached_thumbnail(image_path: str, size: tuple):
    """캐시된 썸네일 가져오기"""
    cache_key = f"{image_path}_{size[0]}x{size[1]}"
    with _thumbnail_cache_lock:
        return _thumbnail_cache.get(cache_key)


def set_cached_thumbnail(image_path: str, size: tuple, ctk_image):
    """썸네일 캐시에 저장"""
    cache_key = f"{image_path}_{size[0]}x{size[1]}"
    with _thumbnail_cache_lock:
        if len(_thumbnail_cache) >= MAX_THUMBNAIL_CACHE:
            oldest_key = next(iter(_thumbnail_cache))
            del _thumbnail_cache[oldest_key]
        _thumbnail_cache[cache_key] = ctk_image


class ScreenRegionSelector(tk.Toplevel):
    """화면에서 영역을 선택하기 위한 전체화면 오버레이 (tkinter 기반)"""

    def __init__(self, parent, on_select: Callable[[int, int, int, int], None], on_cancel: Callable[[], None] = None, existing_region: list = None):
        # tkinter.Toplevel 사용 (CTkToplevel보다 이벤트 처리가 안정적)
        super().__init__(parent)

        self._on_select = on_select
        self._on_cancel = on_cancel
        self._existing_region = existing_region  # [x1, y1, x2, y2] 또는 None
        self._start_x = 0
        self._start_y = 0
        self._rect_id = None
        self._selecting = False
        self._destroyed = False

        # 전체화면 설정
        self.attributes("-fullscreen", True)
        self.attributes("-topmost", True)
        self.attributes("-alpha", 0.3)  # 반투명
        self.configure(bg="black")
        self.overrideredirect(True)

        # 화면 크기
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()

        # 캔버스 생성
        self._canvas = Canvas(
            self,
            width=screen_w,
            height=screen_h,
            bg="gray20",
            highlightthickness=0,
            cursor="crosshair"
        )
        self._canvas.pack(fill="both", expand=True)

        # 기존 범위가 있으면 빨간색으로 표시
        if existing_region and len(existing_region) == 4:
            ex1, ey1, ex2, ey2 = existing_region
            # 빨간색 테두리 (기존 범위) - 단일 사각형
            self._canvas.create_rectangle(
                ex1, ey1, ex2, ey2,
                outline="#ff0000", width=3, fill="", dash=(8, 4)
            )
            # 기존 범위 라벨
            self._canvas.create_text(
                ex1, ey1 - 20,
                text=f"기존 범위: ({ex1}, {ey1}) ~ ({ex2}, {ey2})",
                font=("맑은 고딕", 13, "bold"),
                fill="#ff0000",
                anchor="sw"
            )
            # 안내 텍스트 (기존 범위가 있을 때)
            self._canvas.create_text(
                screen_w // 2, 50,
                text="마우스로 드래그하여 새 검색 영역을 선택하세요 (ESC: 취소)",
                font=("맑은 고딕", 16, "bold"),
                fill="white"
            )
            self._canvas.create_text(
                screen_w // 2, 80,
                text="빨간색 점선 = 기존 범위  |  파란색 실선 = 새 범위",
                font=("맑은 고딕", 12),
                fill="#aaaaaa"
            )
        else:
            # 안내 텍스트
            self._canvas.create_text(
                screen_w // 2, 50,
                text="마우스로 드래그하여 검색 영역을 선택하세요 (ESC: 취소)",
                font=("맑은 고딕", 16, "bold"),
                fill="white"
            )

        # 이벤트 바인딩
        self._canvas.bind("<ButtonPress-1>", self._on_mouse_down)
        self._canvas.bind("<B1-Motion>", self._on_mouse_drag)
        self._canvas.bind("<ButtonRelease-1>", self._on_mouse_up)
        self.bind("<Escape>", self._on_escape)
        self.bind("<Key>", self._on_key)  # 모든 키 이벤트

        # 포커스 및 이벤트 grab
        self.after(100, self._setup_grab)

    def _setup_grab(self):
        """지연 후 grab 설정"""
        if self._destroyed:
            return
        try:
            self.focus_force()
            self._canvas.focus_set()
            self.grab_set_global()  # 전역 이벤트 grab
            self.lift()
        except tk.TclError:
            pass

    def _on_key(self, event):
        """키 이벤트 처리"""
        if event.keysym == "Escape":
            self._on_escape(event)

    def _on_mouse_down(self, event):
        """마우스 버튼 누름"""
        self._start_x = event.x_root  # 전역 좌표 사용
        self._start_y = event.y_root
        self._selecting = True
        if self._rect_id:
            self._canvas.delete(self._rect_id)

    def _on_mouse_drag(self, event):
        """마우스 드래그"""
        if not self._selecting:
            return
        if self._rect_id:
            self._canvas.delete(self._rect_id)
        self._rect_id = self._canvas.create_rectangle(
            self._start_x, self._start_y, event.x_root, event.y_root,
            outline="#58a6ff", width=3, fill=""
        )

    def _on_mouse_up(self, event):
        """마우스 버튼 놓음"""
        if not self._selecting:
            return
        self._selecting = False

        x1, y1 = min(self._start_x, event.x_root), min(self._start_y, event.y_root)
        x2, y2 = max(self._start_x, event.x_root), max(self._start_y, event.y_root)

        # 최소 크기 체크
        if abs(x2 - x1) > 10 and abs(y2 - y1) > 10:
            self._close()
            if self._on_select:
                self._on_select(x1, y1, x2, y2)
        else:
            # 너무 작으면 다시 선택
            if self._rect_id:
                self._canvas.delete(self._rect_id)

    def _on_escape(self, event):
        """ESC 키로 취소"""
        self._close()
        if self._on_cancel:
            self._on_cancel()

    def _close(self):
        """안전하게 창 닫기"""
        if self._destroyed:
            return
        self._destroyed = True
        try:
            self.grab_release()
        except tk.TclError:
            pass
        try:
            self.destroy()
        except tk.TclError:
            pass


class ImageCropDialog(ctk.CTkToplevel):
    """이미지 확대/축소 및 크롭 다이얼로그 - 전체화면 캡쳐 지원"""

    def __init__(
        self,
        parent,
        image_path: str,
        on_crop: Optional[Callable[[str], None]] = None,
        on_delete: Optional[Callable[[], None]] = None,
        on_change: Optional[Callable[[str], None]] = None,
        rule: Optional[AutomationRule] = None,
        on_search_radius_change: Optional[Callable[[], None]] = None,
        image_list: list = None,  # 이미지가 있는 규칙/액션 목록 (내비게이션용)
        current_index: int = -1,  # 현재 이미지 인덱스
    ):
        super().__init__(parent)

        self._parent = parent
        self._image_path = image_path
        self._on_crop = on_crop
        self._on_delete = on_delete
        self._on_change = on_change
        self._rule = rule  # 검색 범위 설정용
        self._on_search_radius_change = on_search_radius_change
        self._original_image = None
        self._display_image = None
        self._photo_image = None
        self._cropped_preview = None
        self._cropped_photo = None
        self._scale = 1.0
        self._min_scale = 0.1
        self._max_scale = 3.0
        self._crop_coords = None  # 크롭 좌표 저장

        # 이미지 내비게이션
        self._image_list = image_list or []
        self._current_index = current_index

        # 크롭 선택 영역
        self._start_x = 0
        self._start_y = 0
        self._rect_id = None
        self._selecting = False

        # 팬(이동) 관련
        self._pan_start_x = 0
        self._pan_start_y = 0
        self._panning = False

        # 캔버스 스크롤 위치
        self._canvas_offset_x = 0
        self._canvas_offset_y = 0

        # 타이틀에 파일명 표시
        from pathlib import Path
        filename = Path(image_path).name
        self.title(f"이미지 편집: {filename}")
        self.configure(fg_color=COLORS["bg_dark"])

        # 모달 설정
        self.transient(parent)
        self.grab_set()

        self._load_image()
        self._setup_ui()

        # 키보드 이벤트 바인딩 (화살표 키로 이미지 전환)
        self.bind("<Left>", lambda e: self._navigate_image(-1))
        self.bind("<Right>", lambda e: self._navigate_image(1))

    def _load_image(self):
        """이미지 로드"""
        try:
            # 한글 경로 지원
            img_array = np.fromfile(self._image_path, np.uint8)
            self._original_image = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
            if self._original_image is not None:
                self._original_image = cv2.cvtColor(self._original_image, cv2.COLOR_BGR2RGB)
        except Exception as e:
            logger.error(f"이미지 로드 실패: {e}")

    def _setup_ui(self):
        """UI 구성"""
        if self._original_image is None:
            ctk.CTkLabel(
                self,
                text="이미지를 불러올 수 없습니다",
                font=ctk.CTkFont(size=14),
                text_color=COLORS["error"],
            ).pack(expand=True, pady=50)
            return

        h, w = self._original_image.shape[:2]

        # 캔버스 표시 영역 (고정 크기)
        self._canvas_width = 800
        self._canvas_height = 500

        # 초기 스케일 계산 (이미지가 캔버스에 맞게)
        self._scale = min(self._canvas_width / w, self._canvas_height / h, 1.0)
        self._min_scale = min(0.1, self._scale * 0.5)
        self._max_scale = 3.0

        # 창 크기 설정
        win_w = self._canvas_width + 280
        win_h = self._canvas_height + 220  # 버튼 영역 확보
        self.geometry(f"{win_w}x{win_h}")
        self.minsize(win_w, win_h)  # 최소 크기 설정

        # 창 위치 복원 및 자동 저장
        self.update_idletasks()
        setup_window_position(self, "ImageCropDialog")

        # 파일명 표시 (크게, 초록색)
        filename = Path(self._image_path).name
        self._filename_label = ctk.CTkLabel(
            self,
            text=f"📁 {filename}",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=COLORS["accent"],
        )
        self._filename_label.pack(pady=(15, 5))

        # 상단: 안내 문구 + 줌 컨트롤
        top_frame = ctk.CTkFrame(self, fg_color="transparent")
        top_frame.pack(fill="x", padx=20, pady=(5, 5))

        ctk.CTkLabel(
            top_frame,
            text="마우스 휠: 확대/축소  |  좌클릭 드래그: 영역 선택  |  우클릭 드래그: 이동",
            font=ctk.CTkFont(size=11),
            text_color=COLORS["text_muted"],
        ).pack(side="left")

        # 줌 컨트롤
        zoom_frame = ctk.CTkFrame(top_frame, fg_color="transparent")
        zoom_frame.pack(side="right")

        ctk.CTkButton(
            zoom_frame,
            text="-",
            width=30,
            height=28,
            command=lambda: self._zoom(-0.1),
            fg_color=COLORS["bg_card"],
            hover_color=COLORS["bg_card_hover"],
        ).pack(side="left", padx=2)

        self._zoom_label = ctk.CTkLabel(
            zoom_frame,
            text=f"{int(self._scale * 100)}%",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COLORS["accent_blue"],
            width=60,
        )
        self._zoom_label.pack(side="left", padx=5)

        ctk.CTkButton(
            zoom_frame,
            text="+",
            width=30,
            height=28,
            command=lambda: self._zoom(0.1),
            fg_color=COLORS["bg_card"],
            hover_color=COLORS["bg_card_hover"],
        ).pack(side="left", padx=2)

        ctk.CTkButton(
            zoom_frame,
            text="맞춤",
            width=50,
            height=28,
            command=self._fit_to_canvas,
            fg_color=COLORS["bg_card"],
            hover_color=COLORS["bg_card_hover"],
        ).pack(side="left", padx=(10, 2))

        ctk.CTkButton(
            zoom_frame,
            text="100%",
            width=50,
            height=28,
            command=lambda: self._set_zoom(1.0),
            fg_color=COLORS["bg_card"],
            hover_color=COLORS["bg_card_hover"],
        ).pack(side="left", padx=2)

        # 이미지 내비게이션 (여러 이미지가 있을 때만 표시)
        if len(self._image_list) > 1 and self._current_index >= 0:
            nav_frame = ctk.CTkFrame(self, fg_color="transparent")
            nav_frame.pack(fill="x", padx=20, pady=(5, 0))

            # 이전 버튼
            self._prev_btn = ctk.CTkButton(
                nav_frame,
                text="◀ 이전",
                width=80,
                height=30,
                command=lambda: self._navigate_image(-1),
                fg_color=COLORS["accent_blue"],
                hover_color="#2563eb",
                font=ctk.CTkFont(size=12, weight="bold"),
            )
            self._prev_btn.pack(side="left", padx=5)

            # 현재 위치 표시
            self._nav_label = ctk.CTkLabel(
                nav_frame,
                text=f"{self._current_index + 1} / {len(self._image_list)}",
                font=ctk.CTkFont(size=13, weight="bold"),
                text_color=COLORS["text_primary"],
            )
            self._nav_label.pack(side="left", padx=20)

            # 다음 버튼
            self._next_btn = ctk.CTkButton(
                nav_frame,
                text="다음 ▶",
                width=80,
                height=30,
                command=lambda: self._navigate_image(1),
                fg_color=COLORS["accent_blue"],
                hover_color="#2563eb",
                font=ctk.CTkFont(size=12, weight="bold"),
            )
            self._next_btn.pack(side="left", padx=5)

            # 키보드 안내
            ctk.CTkLabel(
                nav_frame,
                text="(← → 키로 이동)",
                font=ctk.CTkFont(size=10),
                text_color=COLORS["text_muted"],
            ).pack(side="left", padx=10)

            # 버튼 상태 업데이트
            self._update_nav_buttons()

        # 버튼 프레임 (하단에 배치)
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(side="bottom", pady=15)

        self._save_btn = ctk.CTkButton(
            btn_frame,
            text="크롭 저장",
            command=self._save_crop,
            width=100,
            height=40,
            fg_color=COLORS["success"],
            hover_color="#2ea44f",
            text_color="white",
            font=ctk.CTkFont(size=13, weight="bold"),
        )
        self._save_btn.pack(side="left", padx=5)

        # 이미지 변경 버튼
        ctk.CTkButton(
            btn_frame,
            text="이미지 변경",
            command=self._change_image,
            width=100,
            height=40,
            fg_color=COLORS["accent_blue"],
            hover_color="#2563eb",
            text_color="white",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(side="left", padx=5)

        # 이미지 삭제 버튼
        ctk.CTkButton(
            btn_frame,
            text="이미지 삭제",
            command=self._delete_image,
            width=100,
            height=40,
            fg_color=COLORS["error"],
            hover_color="#dc2626",
            text_color="white",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(side="left", padx=5)

        # 멀티이미지 버튼 (rule이 있을 때만)
        if self._rule is not None:
            alt_count = len(self._rule.target_images) if self._rule.target_images else 0
            alt_text = f"멀티이미지 ({alt_count})" if alt_count > 0 else "멀티이미지 추가"
            self._alt_image_btn = ctk.CTkButton(
                btn_frame,
                text=alt_text,
                command=self._manage_alt_images,
                width=120,
                height=40,
                fg_color=COLORS["accent_orange"] if alt_count > 0 else COLORS["bg_card"],
                hover_color="#ea580c" if alt_count > 0 else COLORS["bg_card_hover"],
                text_color="white" if alt_count > 0 else COLORS["text_secondary"],
                font=ctk.CTkFont(size=13, weight="bold"),
            )
            self._alt_image_btn.pack(side="left", padx=5)

        # 검색 범위 설정 버튼 (rule이 있을 때만)
        if self._rule is not None:
            search_radius = getattr(self._rule, 'search_radius', 0)
            radius_text = f"검색범위: {search_radius}px" if search_radius > 0 else "검색범위: 전체"
            self._search_radius_btn = ctk.CTkButton(
                btn_frame,
                text=radius_text,
                command=self._set_search_radius,
                width=130,
                height=40,
                fg_color="#8b5cf6" if search_radius > 0 else COLORS["bg_card"],
                hover_color="#7c3aed" if search_radius > 0 else COLORS["bg_card_hover"],
                text_color="white" if search_radius > 0 else COLORS["text_secondary"],
                font=ctk.CTkFont(size=13, weight="bold"),
            )
            self._search_radius_btn.pack(side="left", padx=5)

            # 인식률 설정 버튼
            conf_value = getattr(self._rule, 'confidence', 0.65) or 0.65
            conf_pct = int(conf_value * 100)
            self._confidence_btn = ctk.CTkButton(
                btn_frame,
                text=f"인식률: {conf_pct}%",
                command=self._set_confidence,
                width=100,
                height=40,
                fg_color="#f59e0b" if conf_pct != 65 else COLORS["bg_card"],
                hover_color="#d97706" if conf_pct != 65 else COLORS["bg_card_hover"],
                text_color="white" if conf_pct != 65 else COLORS["text_secondary"],
                font=ctk.CTkFont(size=13, weight="bold"),
            )
            self._confidence_btn.pack(side="left", padx=5)

            # 마우스 이동 체크박스
            self._move_mouse_var = ctk.BooleanVar(value=getattr(self._rule, 'move_mouse_before_search', False))
            self._move_mouse_cb = ctk.CTkCheckBox(
                btn_frame,
                text="커서 숨김",
                variable=self._move_mouse_var,
                command=self._on_move_mouse_changed,
                font=ctk.CTkFont(size=12),
                width=90,
                height=40,
                checkbox_width=20,
                checkbox_height=20,
            )
            self._move_mouse_cb.pack(side="left", padx=5)

        ctk.CTkButton(
            btn_frame,
            text="취소",
            command=self.destroy,
            width=80,
            height=40,
            fg_color=COLORS["bg_card"],
            hover_color=COLORS["bg_card_hover"],
            text_color=COLORS["text_secondary"],
        ).pack(side="left", padx=5)

        # 메인 컨텐츠 (이미지 + 미리보기)
        content_frame = ctk.CTkFrame(self, fg_color="transparent")
        content_frame.pack(padx=20, pady=10, fill="both", expand=True)

        # 왼쪽: 원본 이미지
        left_frame = ctk.CTkFrame(content_frame, fg_color=COLORS["bg_card"], corner_radius=8)
        left_frame.pack(side="left", fill="both", expand=True)

        # 이미지 정보
        self._info_label = ctk.CTkLabel(
            left_frame,
            text=f"원본: {w} x {h} px",
            font=ctk.CTkFont(size=11),
            text_color=COLORS["text_secondary"],
        )
        self._info_label.pack(pady=(8, 5))

        # 캔버스 (tkinter Canvas 사용)
        self._canvas = Canvas(
            left_frame,
            width=self._canvas_width,
            height=self._canvas_height,
            bg="#1a1b26",
            highlightthickness=1,
            highlightbackground=COLORS["border"],
        )
        self._canvas.pack(padx=10, pady=(0, 10))

        # 이미지 표시
        self._update_canvas_image()

        # 마우스 이벤트 바인딩
        self._canvas.bind("<ButtonPress-1>", self._on_mouse_down)
        self._canvas.bind("<B1-Motion>", self._on_mouse_drag)
        self._canvas.bind("<ButtonRelease-1>", self._on_mouse_up)
        self._canvas.bind("<ButtonPress-3>", self._on_pan_start)
        self._canvas.bind("<B3-Motion>", self._on_pan_drag)
        self._canvas.bind("<ButtonRelease-3>", self._on_pan_end)
        self._canvas.bind("<MouseWheel>", self._on_mouse_wheel)

        # 오른쪽: 미리보기 영역
        right_frame = ctk.CTkFrame(content_frame, fg_color=COLORS["bg_card"], corner_radius=8, width=200)
        right_frame.pack(side="right", fill="y", padx=(15, 0))
        right_frame.pack_propagate(False)

        ctk.CTkLabel(
            right_frame,
            text="크롭 미리보기",
            font=ctk.CTkFont(size=11),
            text_color=COLORS["text_secondary"],
        ).pack(pady=(8, 5))

        # 미리보기 캔버스
        self._preview_canvas = Canvas(
            right_frame,
            width=180,
            height=180,
            bg="#1a1b26",
            highlightthickness=1,
            highlightbackground=COLORS["border"],
        )
        self._preview_canvas.pack(padx=10, pady=5)

        self._preview_label = ctk.CTkLabel(
            right_frame,
            text="영역을 선택하세요",
            font=ctk.CTkFont(size=10),
            text_color=COLORS["text_muted"],
        )
        self._preview_label.pack(pady=5)

    def _update_canvas_image(self):
        """현재 스케일로 캔버스에 이미지 표시"""
        if self._original_image is None:
            return

        h, w = self._original_image.shape[:2]
        display_w = int(w * self._scale)
        display_h = int(h * self._scale)

        # 스케일된 이미지 생성
        self._display_image = cv2.resize(self._original_image, (display_w, display_h))
        pil_image = Image.fromarray(self._display_image)
        self._photo_image = ImageTk.PhotoImage(pil_image)

        # 캔버스 초기화 및 이미지 표시
        self._canvas.delete("all")

        # 이미지를 캔버스 중앙에 배치 (오프셋 적용)
        img_x = (self._canvas_width - display_w) // 2 + self._canvas_offset_x
        img_y = (self._canvas_height - display_h) // 2 + self._canvas_offset_y

        self._img_canvas_x = img_x
        self._img_canvas_y = img_y
        self._canvas.create_image(img_x, img_y, anchor="nw", image=self._photo_image, tags="image")

        # 줌 레이블 업데이트
        if hasattr(self, '_zoom_label'):
            self._zoom_label.configure(text=f"{int(self._scale * 100)}%")

    def _zoom(self, delta: float):
        """줌 조정"""
        new_scale = self._scale + delta
        self._set_zoom(new_scale)

    def _set_zoom(self, scale: float):
        """특정 줌 레벨로 설정"""
        self._scale = max(self._min_scale, min(self._max_scale, scale))
        self._update_canvas_image()

    def _fit_to_canvas(self):
        """캔버스에 맞춤"""
        if self._original_image is None:
            return
        h, w = self._original_image.shape[:2]
        self._scale = min(self._canvas_width / w, self._canvas_height / h)
        self._canvas_offset_x = 0
        self._canvas_offset_y = 0
        self._update_canvas_image()

    def _on_mouse_wheel(self, event):
        """마우스 휠 줌"""
        if event.delta > 0:
            self._zoom(0.1)
        else:
            self._zoom(-0.1)

    def _on_pan_start(self, event):
        """팬 시작 (우클릭)"""
        self._pan_start_x = event.x
        self._pan_start_y = event.y
        self._panning = True

    def _on_pan_drag(self, event):
        """팬 드래그"""
        if not self._panning:
            return
        dx = event.x - self._pan_start_x
        dy = event.y - self._pan_start_y
        self._canvas_offset_x += dx
        self._canvas_offset_y += dy
        self._pan_start_x = event.x
        self._pan_start_y = event.y
        self._update_canvas_image()

    def _on_pan_end(self, event):
        """팬 종료"""
        self._panning = False

    def _on_mouse_down(self, event):
        """마우스 버튼 누름"""
        self._start_x = event.x
        self._start_y = event.y
        self._selecting = True

        # 기존 사각형 삭제
        if self._rect_id:
            self._canvas.delete(self._rect_id)

    def _on_mouse_drag(self, event):
        """마우스 드래그"""
        if not self._selecting:
            return

        # 기존 사각형 삭제
        if self._rect_id:
            self._canvas.delete(self._rect_id)

        # 새 사각형 그리기
        self._rect_id = self._canvas.create_rectangle(
            self._start_x, self._start_y, event.x, event.y,
            outline="#58a6ff", width=2
        )

        # 실시간 미리보기 업데이트
        self._update_preview(event.x, event.y)

    def _on_mouse_up(self, event):
        """마우스 버튼 놓음"""
        self._selecting = False
        self._end_x = event.x
        self._end_y = event.y
        self._update_preview(event.x, event.y)

    def _update_preview(self, end_x: int, end_y: int):
        """미리보기 업데이트"""
        if self._original_image is None:
            return

        # 좌표 정규화
        x1 = min(self._start_x, end_x)
        y1 = min(self._start_y, end_y)
        x2 = max(self._start_x, end_x)
        y2 = max(self._start_y, end_y)

        # 최소 크기 확인
        if x2 - x1 < 5 or y2 - y1 < 5:
            return

        # 캔버스 좌표에서 이미지 좌표로 변환 (오프셋 고려)
        img_x = getattr(self, '_img_canvas_x', 0)
        img_y = getattr(self, '_img_canvas_y', 0)

        # 이미지 영역 내의 좌표로 변환
        rel_x1 = x1 - img_x
        rel_y1 = y1 - img_y
        rel_x2 = x2 - img_x
        rel_y2 = y2 - img_y

        # 원본 이미지 좌표로 변환 (스케일 적용)
        orig_x1 = int(rel_x1 / self._scale)
        orig_y1 = int(rel_y1 / self._scale)
        orig_x2 = int(rel_x2 / self._scale)
        orig_y2 = int(rel_y2 / self._scale)

        # 좌표 순서 정규화 (음수 방지)
        if orig_x1 > orig_x2:
            orig_x1, orig_x2 = orig_x2, orig_x1
        if orig_y1 > orig_y2:
            orig_y1, orig_y2 = orig_y2, orig_y1

        # 경계 체크
        h, w = self._original_image.shape[:2]
        orig_x1 = max(0, min(orig_x1, w))
        orig_y1 = max(0, min(orig_y1, h))
        orig_x2 = max(0, min(orig_x2, w))
        orig_y2 = max(0, min(orig_y2, h))

        # 크롭된 이미지 추출
        cropped = self._original_image[orig_y1:orig_y2, orig_x1:orig_x2]

        if cropped.size == 0:
            return

        # 크롭 좌표 저장
        self._crop_coords = (orig_x1, orig_y1, orig_x2, orig_y2)

        # 미리보기 크기에 맞게 리사이즈
        crop_h, crop_w = cropped.shape[:2]
        # Division by zero 방지
        if crop_w <= 0 or crop_h <= 0:
            return
        preview_scale = min(180 / crop_w, 180 / crop_h)
        preview_w = int(crop_w * preview_scale)
        preview_h = int(crop_h * preview_scale)

        preview_resized = cv2.resize(cropped, (preview_w, preview_h))
        pil_preview = Image.fromarray(preview_resized)
        self._cropped_photo = ImageTk.PhotoImage(pil_preview)

        # 미리보기 캔버스 업데이트
        self._preview_canvas.delete("all")
        # 중앙에 배치
        offset_x = (180 - preview_w) // 2
        offset_y = (180 - preview_h) // 2
        self._preview_canvas.create_image(offset_x, offset_y, anchor="nw", image=self._cropped_photo)

        # 크기 정보 표시
        self._preview_label.configure(
            text=f"{orig_x2 - orig_x1} x {orig_y2 - orig_y1} px",
            text_color=COLORS["text_primary"]
        )

        # 저장 버튼 활성화
        self._save_btn.configure(state="normal")

    def _save_crop(self):
        """크롭 저장 - 원본 유지하고 새 파일로 저장 + 마스크 자동 생성"""
        from tkinter import messagebox
        import os
        import uuid

        if self._crop_coords is None:
            messagebox.showwarning("알림", "먼저 영역을 선택하세요.\n마우스로 드래그하여 크롭할 영역을 선택하세요.")
            return

        if self._original_image is None:
            messagebox.showerror("오류", "이미지를 불러올 수 없습니다.")
            self.destroy()
            return

        orig_x1, orig_y1, orig_x2, orig_y2 = self._crop_coords
        crop_w = orig_x2 - orig_x1
        crop_h = orig_y2 - orig_y1
        orig_h, orig_w = self._original_image.shape[:2]

        logger.info(f"[크롭] 원본 크기: {orig_w}x{orig_h}")
        logger.info(f"[크롭] 크롭 좌표: ({orig_x1}, {orig_y1}) ~ ({orig_x2}, {orig_y2})")
        logger.info(f"[크롭] 크롭 크기: {crop_w}x{crop_h}")

        # 크롭
        cropped = self._original_image[orig_y1:orig_y2, orig_x1:orig_x2]

        if cropped.size == 0:
            messagebox.showwarning("알림", "크롭된 영역이 비어있습니다.\n다시 선택하세요.")
            return

        try:
            # BGR로 변환
            cropped_bgr = cv2.cvtColor(cropped, cv2.COLOR_RGB2BGR)

            # 새 파일명 생성 (원본 유지)
            original_path = Path(self._image_path)
            stem = original_path.stem
            suffix = original_path.suffix or ".png"
            parent = original_path.parent

            # 크롭 파일명: 원본이름_crop_랜덤.확장자
            new_filename = f"{stem}_crop_{uuid.uuid4().hex[:6]}{suffix}"
            new_path = parent / new_filename

            # 새 파일로 저장 (원본은 그대로 유지)
            success = cv2.imwrite(str(new_path), cropped_bgr)

            if success:
                new_size = os.path.getsize(str(new_path)) if new_path.exists() else 0
                logger.info(f"[크롭] 새 파일 저장: {new_path}")
                logger.info(f"[크롭] 원본 유지: {self._image_path}")
                logger.info(f"[크롭] 크롭 크기: {crop_w}x{crop_h}, 파일크기: {new_size} bytes")

                # 마스크 자동 생성 (배경 변화에 강건한 매칭용)
                try:
                    from ..analyzer.template_matcher import generate_text_mask
                    mask = generate_text_mask(cropped_bgr)
                    # 마스크 파일명: 이미지이름_mask.확장자 (로드 패턴과 일치)
                    crop_stem = new_path.stem  # e.g., "screenshot_crop_abc123"
                    mask_path = parent / f"{crop_stem}_mask{suffix}"
                    cv2.imwrite(str(mask_path), mask)
                    logger.info(f"[크롭] 마스크 생성: {mask_path}")
                except Exception as me:
                    logger.warning(f"[크롭] 마스크 생성 실패 (무시): {me}")

                if self._on_crop:
                    self._on_crop(str(new_path))
                self.destroy()
            else:
                logger.error(f"[크롭] cv2.imwrite 실패: {new_path}")
                messagebox.showerror("오류", f"이미지 저장에 실패했습니다.\n경로: {new_path}")

        except Exception as e:
            logger.error(f"크롭 저장 오류: {e}")
            messagebox.showerror("오류", f"저장 중 오류가 발생했습니다.\n{e}")

    def _delete_image(self):
        """이미지 삭제"""
        from tkinter import messagebox

        if messagebox.askyesno("이미지 삭제", "이미지를 삭제하시겠습니까?\n원본 파일도 함께 삭제됩니다."):
            try:
                # 파일 삭제
                image_path = Path(self._image_path)
                if image_path.exists():
                    image_path.unlink()
                    logger.info(f"이미지 파일 삭제: {self._image_path}")

                # 콜백 호출
                if self._on_delete:
                    self._on_delete()

                self.destroy()
            except Exception as e:
                logger.error(f"이미지 삭제 실패: {e}")
                messagebox.showerror("오류", f"이미지 삭제 실패: {e}")

    def _change_image(self):
        """이미지 변경"""
        import shutil
        import uuid
        from ..utils.config import DATA_DIR

        # 기본 경로: templates 폴더 (기존 이미지 재사용 쉽게)
        templates_dir = DATA_DIR / "templates"
        templates_dir.mkdir(parents=True, exist_ok=True)
        initial_dir = templates_dir if templates_dir.exists() else Path(self._image_path).parent

        new_path = filedialog.askopenfilename(
            title="새 이미지 선택",
            initialdir=str(initial_dir),
            filetypes=[
                ("이미지 파일", "*.png *.jpg *.jpeg *.bmp *.gif"),
                ("모든 파일", "*.*"),
            ],
        )

        if not new_path:
            return

        try:
            new_path = Path(new_path)

            # 선택한 파일이 이미 templates 폴더에 있으면 그대로 사용
            if new_path.parent.resolve() == templates_dir.resolve():
                dest_path = new_path
                logger.info(f"기존 템플릿 이미지 사용: {dest_path.name}")
            else:
                # 외부 파일이면 templates 폴더에 복사
                new_ext = new_path.suffix
                new_filename = f"img_{uuid.uuid4().hex[:8]}{new_ext}"
                dest_path = templates_dir / new_filename
                shutil.copy2(new_path, dest_path)
                logger.info(f"이미지 변경 완료: {new_path} -> {dest_path}")

            # 콜백 호출 (새 경로 전달)
            if self._on_change:
                self._on_change(str(dest_path))

            self.destroy()
        except Exception as e:
            from tkinter import messagebox
            logger.error(f"이미지 변경 실패: {e}")
            messagebox.showerror("오류", f"이미지 변경 실패: {e}")

    def _set_search_radius(self):
        """검색 범위 설정 - 화면에서 직접 영역 선택"""
        if self._rule is None:
            return

        from tkinter import messagebox

        # 현재 다이얼로그 숨기기
        self.withdraw()

        def on_region_select(x1, y1, x2, y2):
            """영역 선택 완료"""
            # 선택 영역의 중심점과 반경 계산
            center_x = (x1 + x2) // 2
            center_y = (y1 + y2) // 2
            width = x2 - x1
            height = y2 - y1

            # 반경은 가로/세로 중 큰 값의 절반
            radius = max(width, height) // 2

            # rule에 저장
            self._rule.search_radius = radius
            # action_x/y도 업데이트 (검색 중심점)
            self._rule.action_x = center_x
            self._rule.action_y = center_y

            logger.info(f"검색 범위 설정: 중심({center_x}, {center_y}), 반경 {radius}px")

            # 다이얼로그 다시 표시
            self.deiconify()
            self.grab_set()

            # 버튼 텍스트 업데이트
            if hasattr(self, '_search_radius_btn'):
                self._search_radius_btn.configure(
                    text=f"검색범위: {radius}px",
                    fg_color="#8b5cf6",
                    hover_color="#7c3aed",
                    text_color="white"
                )

            # 콜백 호출
            if self._on_search_radius_change:
                self._on_search_radius_change()

            messagebox.showinfo(
                "설정 완료",
                f"검색 범위가 설정되었습니다.\n\n"
                f"중심점: ({center_x}, {center_y})\n"
                f"검색 반경: {radius}px"
            )

        def on_cancel():
            """선택 취소"""
            self.deiconify()
            self.grab_set()

        # 기존 검색 범위 계산
        existing_region = None
        if self._rule and self._rule.search_radius and self._rule.action_x and self._rule.action_y:
            cx, cy, r = self._rule.action_x, self._rule.action_y, self._rule.search_radius
            existing_region = [cx - r, cy - r, cx + r, cy + r]

        # 잠시 후 영역 선택 시작 (다이얼로그 숨김 완료 후)
        self.after(100, lambda: ScreenRegionSelector(self, on_region_select, on_cancel, existing_region=existing_region))

    def _on_move_mouse_changed(self):
        """마우스 이동 옵션 변경"""
        if self._rule is not None:
            self._rule.move_mouse_before_search = self._move_mouse_var.get()
            logger.info(f"검색 전 마우스 이동: {'활성화' if self._rule.move_mouse_before_search else '비활성화'}")
            if self._on_search_radius_change:
                self._on_search_radius_change()

    def _set_confidence(self):
        """인식률 설정 다이얼로그"""
        if self._rule is None:
            return

        from tkinter import messagebox

        # 인식률 설정 다이얼로그
        conf_dialog = ctk.CTkToplevel(self)
        conf_dialog.title("이미지 인식률 설정")
        conf_dialog.geometry("350x200")
        conf_dialog.resizable(False, False)
        conf_dialog.configure(fg_color=COLORS["bg_dark"])
        conf_dialog.transient(self)
        conf_dialog.grab_set()

        # 중앙 배치
        conf_dialog.update_idletasks()
        x = self.winfo_x() + (self.winfo_width() - 350) // 2
        y = self.winfo_y() + (self.winfo_height() - 200) // 2
        conf_dialog.geometry(f"+{x}+{y}")

        main_frame = ctk.CTkFrame(conf_dialog, fg_color="transparent")
        main_frame.pack(fill="both", expand=True, padx=20, pady=15)

        ctk.CTkLabel(
            main_frame,
            text="이미지 인식률",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(anchor="w", pady=(0, 5))

        ctk.CTkLabel(
            main_frame,
            text="낮을수록 유연하게, 높을수록 정확하게 인식",
            font=ctk.CTkFont(size=11),
            text_color=COLORS["text_muted"],
        ).pack(anchor="w", pady=(0, 10))

        current_conf = getattr(self._rule, 'confidence', 0.65) or 0.65
        conf_var = ctk.DoubleVar(value=current_conf * 100)

        slider_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        slider_frame.pack(fill="x", pady=10)

        conf_slider = ctk.CTkSlider(
            slider_frame,
            from_=30,
            to=95,
            variable=conf_var,
            width=220,
            height=16,
            fg_color=COLORS["bg_card"],
            progress_color="#f59e0b",
            button_color=COLORS["text_primary"],
            button_hover_color="#f59e0b",
        )
        conf_slider.pack(side="left", padx=(0, 10))

        conf_label = ctk.CTkLabel(
            slider_frame,
            text=f"{int(conf_var.get())}%",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color="#f59e0b",
            width=50,
        )
        conf_label.pack(side="left")

        def update_label(*args):
            conf_label.configure(text=f"{int(conf_var.get())}%")

        conf_var.trace_add("write", update_label)

        def save_conf():
            self._rule.confidence = conf_var.get() / 100.0
            conf_pct = int(conf_var.get())
            logger.info(f"인식률 설정: {conf_pct}%")

            # 버튼 텍스트 업데이트
            if hasattr(self, '_confidence_btn'):
                self._confidence_btn.configure(
                    text=f"인식률: {conf_pct}%",
                    fg_color="#f59e0b" if conf_pct != 65 else COLORS["bg_card"],
                    hover_color="#d97706" if conf_pct != 65 else COLORS["bg_card_hover"],
                    text_color="white" if conf_pct != 65 else COLORS["text_secondary"],
                )

            if self._on_search_radius_change:
                self._on_search_radius_change()

            conf_dialog.destroy()

        btn_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        btn_frame.pack(fill="x", pady=(15, 0))

        ctk.CTkButton(
            btn_frame,
            text="저장",
            width=100,
            height=36,
            fg_color="#f59e0b",
            hover_color="#d97706",
            command=save_conf,
        ).pack(side="left", padx=(0, 10))

        ctk.CTkButton(
            btn_frame,
            text="취소",
            width=100,
            height=36,
            fg_color=COLORS["bg_card"],
            hover_color=COLORS["bg_card_hover"],
            text_color=COLORS["text_secondary"],
            command=conf_dialog.destroy,
        ).pack(side="left")

    def _navigate_image(self, direction: int):
        """이미지 내비게이션 (direction: -1=이전, 1=다음)"""
        if not self._image_list or self._current_index < 0:
            return

        new_index = self._current_index + direction

        # 범위 체크
        if new_index < 0 or new_index >= len(self._image_list):
            return

        # 새 이미지 정보 가져오기
        item = self._image_list[new_index]

        # 이미지 경로 추출 (AutomationRule 또는 Action 객체)
        if hasattr(item, 'target_image'):
            new_image_path = item.target_image
            new_rule = item if hasattr(item, 'rule_type') else None
        elif hasattr(item, 'image_path'):
            new_image_path = item.image_path
            new_rule = None
        else:
            return

        if not new_image_path or not Path(new_image_path).exists():
            return

        # 현재 인덱스 업데이트
        self._current_index = new_index
        self._image_path = new_image_path
        self._rule = new_rule

        # 크롭 상태 초기화
        self._crop_coords = None
        self._canvas_offset_x = 0
        self._canvas_offset_y = 0

        # 이미지 다시 로드
        self._load_image()

        # UI 업데이트
        if self._original_image is not None:
            h, w = self._original_image.shape[:2]
            self._scale = min(self._canvas_width / w, self._canvas_height / h, 1.0)
            self._update_canvas_image()

            # 정보 레이블 업데이트
            if hasattr(self, '_info_label'):
                self._info_label.configure(text=f"원본: {w} x {h} px")

        # 파일명 업데이트
        filename = Path(self._image_path).name
        self.title(f"이미지 편집: {filename}")
        if hasattr(self, '_filename_label'):
            self._filename_label.configure(text=f"📁 {filename}")

        # 미리보기 초기화
        if hasattr(self, '_preview_canvas'):
            self._preview_canvas.delete("all")
        if hasattr(self, '_preview_label'):
            self._preview_label.configure(text="영역을 선택하세요", text_color=COLORS["text_muted"])

        # 내비게이션 버튼 상태 업데이트
        self._update_nav_buttons()

        logger.info(f"이미지 전환: {new_index + 1}/{len(self._image_list)} - {filename}")

    def _update_nav_buttons(self):
        """내비게이션 버튼 상태 업데이트"""
        if not hasattr(self, '_prev_btn') or not hasattr(self, '_next_btn'):
            return

        # 이전 버튼 상태
        if self._current_index <= 0:
            self._prev_btn.configure(state="disabled", fg_color=COLORS["bg_card"])
        else:
            self._prev_btn.configure(state="normal", fg_color=COLORS["accent_blue"])

        # 다음 버튼 상태
        if self._current_index >= len(self._image_list) - 1:
            self._next_btn.configure(state="disabled", fg_color=COLORS["bg_card"])
        else:
            self._next_btn.configure(state="normal", fg_color=COLORS["accent_blue"])

        # 현재 위치 표시 업데이트
        if hasattr(self, '_nav_label'):
            self._nav_label.configure(text=f"{self._current_index + 1} / {len(self._image_list)}")

    def _manage_alt_images(self):
        """멀티이미지 관리 다이얼로그"""
        if self._rule is None:
            return

        dialog = AltImageDialog(self, self._rule, self._on_search_radius_change)
        self.wait_window(dialog)

        # 버튼 텍스트 업데이트
        if hasattr(self, '_alt_image_btn'):
            alt_count = len(self._rule.target_images) if self._rule.target_images else 0
            alt_text = f"멀티이미지 ({alt_count})" if alt_count > 0 else "멀티이미지 추가"
            self._alt_image_btn.configure(
                text=alt_text,
                fg_color=COLORS["accent_orange"] if alt_count > 0 else COLORS["bg_card"],
                hover_color="#ea580c" if alt_count > 0 else COLORS["bg_card_hover"],
                text_color="white" if alt_count > 0 else COLORS["text_secondary"],
            )


class AltImageDialog(ctk.CTkToplevel):
    """멀티이미지 관리 다이얼로그"""

    def __init__(self, parent, rule: AutomationRule, on_change: Optional[Callable] = None):
        super().__init__(parent)

        self._rule = rule
        self._on_change = on_change
        self._thumbnail_refs = []

        self.title("멀티이미지 관리")
        self.geometry("500x400")
        self.configure(fg_color=COLORS["bg_dark"])
        self.transient(parent)
        self.grab_set()

        # 창 위치 복원 및 자동 저장
        self.update_idletasks()
        setup_window_position(self, "AltImageDialog")

        self._setup_ui()

    def _setup_ui(self):
        """UI 구성"""
        # 안내 문구
        ctk.CTkLabel(
            self,
            text="멀티이미지 (OR 조건)",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(pady=(15, 5))

        ctk.CTkLabel(
            self,
            text="실행 시 기본 이미지 또는 멀티이미지 중 하나라도 찾으면 클릭합니다",
            font=ctk.CTkFont(size=11),
            text_color=COLORS["text_muted"],
        ).pack(pady=(0, 15))

        # 이미지 목록 프레임
        list_frame = ctk.CTkScrollableFrame(
            self,
            fg_color=COLORS["bg_card"],
            corner_radius=8,
            height=220,
        )
        list_frame.pack(fill="x", padx=20, pady=(0, 15))

        self._list_frame = list_frame
        self._refresh_list()

        # 버튼 프레임
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=20, pady=(0, 15))

        ctk.CTkButton(
            btn_frame,
            text="이미지 추가",
            command=self._add_image,
            width=120,
            height=36,
            fg_color=COLORS["success"],
            hover_color="#45a049",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(side="left", padx=5)

        ctk.CTkButton(
            btn_frame,
            text="스크린샷 추가",
            command=self._capture_screenshot,
            width=120,
            height=36,
            fg_color=COLORS["accent_blue"],
            hover_color="#2563eb",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(side="left", padx=5)

        ctk.CTkButton(
            btn_frame,
            text="닫기",
            command=self.destroy,
            width=80,
            height=36,
            fg_color=COLORS["bg_card"],
            hover_color=COLORS["bg_card_hover"],
            text_color=COLORS["text_secondary"],
        ).pack(side="right", padx=5)

    def _refresh_list(self):
        """이미지 목록 새로고침"""
        self._thumbnail_refs.clear()

        for widget in self._list_frame.winfo_children():
            widget.destroy()

        if not self._rule.target_images:
            ctk.CTkLabel(
                self._list_frame,
                text="등록된 멀티이미지가 없습니다",
                font=ctk.CTkFont(size=12),
                text_color=COLORS["text_muted"],
            ).pack(pady=20)
            return

        for i, img_path in enumerate(self._rule.target_images):
            self._create_image_row(i, img_path)

    def _create_image_row(self, index: int, img_path: str):
        """이미지 행 생성"""
        row = ctk.CTkFrame(self._list_frame, fg_color=COLORS["bg_dark"], corner_radius=6)
        row.pack(fill="x", padx=10, pady=5)

        # 썸네일
        thumb_frame = ctk.CTkFrame(row, fg_color=COLORS["bg_card"], width=50, height=50, corner_radius=4)
        thumb_frame.pack(side="left", padx=10, pady=8)
        thumb_frame.pack_propagate(False)

        if Path(img_path).exists():
            try:
                # 캐시 확인 (50x50 고정 크기)
                cached = get_cached_thumbnail(img_path, (50, 50))
                if cached is not None:
                    ctk_image = cached
                else:
                    # 한글 경로 지원
                    img_arr = np.fromfile(img_path, np.uint8)
                    img = cv2.imdecode(img_arr, cv2.IMREAD_COLOR)
                    if img is not None:
                        img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                        h, w = img_rgb.shape[:2]
                        scale = min(50 / w, 50 / h)
                        new_w, new_h = int(w * scale), int(h * scale)
                        resized = cv2.resize(img_rgb, (new_w, new_h))
                        pil_image = Image.fromarray(resized)
                        ctk_image = ctk.CTkImage(light_image=pil_image, dark_image=pil_image, size=(new_w, new_h))
                        set_cached_thumbnail(img_path, (50, 50), ctk_image)
                    else:
                        ctk_image = None

                if ctk_image is not None:
                    ctk.CTkLabel(thumb_frame, image=ctk_image, text="").pack(expand=True)
                    self._thumbnail_refs.append(ctk_image)
            except Exception:
                pass

        # 파일명
        filename = Path(img_path).name
        ctk.CTkLabel(
            row,
            text=f"{index + 1}. {filename}",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["text_primary"],
        ).pack(side="left", padx=10)

        # 삭제 버튼
        ctk.CTkButton(
            row,
            text="삭제",
            command=lambda p=img_path: self._remove_image(p),
            width=60,
            height=28,
            fg_color=COLORS["error"],
            hover_color="#dc2626",
            text_color="white",
            font=ctk.CTkFont(size=11),
        ).pack(side="right", padx=10, pady=8)

    def _add_image(self):
        """파일에서 이미지 추가"""
        from ..utils.config import DATA_DIR
        import shutil
        import uuid

        templates_dir = DATA_DIR / "templates"
        templates_dir.mkdir(parents=True, exist_ok=True)

        file_path = filedialog.askopenfilename(
            title="멀티이미지 선택",
            initialdir=str(templates_dir),
            filetypes=[
                ("이미지 파일", "*.png *.jpg *.jpeg *.bmp"),
                ("모든 파일", "*.*"),
            ],
        )

        if not file_path:
            return

        try:
            src_path = Path(file_path)

            # templates 폴더에 있으면 그대로 사용
            if src_path.parent.resolve() == templates_dir.resolve():
                dest_path = src_path
            else:
                # 외부 파일이면 복사
                new_filename = f"alt_{uuid.uuid4().hex[:8]}{src_path.suffix}"
                dest_path = templates_dir / new_filename
                shutil.copy2(src_path, dest_path)

            # 중복 체크
            if str(dest_path) not in self._rule.target_images:
                self._rule.target_images.append(str(dest_path))
                logger.info(f"멀티이미지 추가: {dest_path}")

                if self._on_change:
                    self._on_change()

            self._refresh_list()
        except Exception as e:
            logger.error(f"이미지 추가 실패: {e}")
            from tkinter import messagebox
            messagebox.showerror("오류", f"이미지 추가 실패: {e}")

    def _capture_screenshot(self):
        """스크린샷으로 멀티이미지 추가"""
        from ..utils.config import DATA_DIR
        import uuid

        self.withdraw()

        def on_region_select(x1, y1, x2, y2):
            """영역 선택 완료"""
            try:
                import mss
                templates_dir = DATA_DIR / "templates"
                templates_dir.mkdir(parents=True, exist_ok=True)

                # 스크린샷 캡처
                with mss.mss() as sct:
                    monitor = {"left": x1, "top": y1, "width": x2 - x1, "height": y2 - y1}
                    screenshot = sct.grab(monitor)
                    img = np.array(screenshot)
                    img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)

                # 저장
                filename = f"alt_{uuid.uuid4().hex[:8]}.png"
                filepath = templates_dir / filename
                cv2.imwrite(str(filepath), img)

                # 추가
                self._rule.target_images.append(str(filepath))
                logger.info(f"멀티이미지 캡처: {filepath}")

                if self._on_change:
                    self._on_change()

                self._refresh_list()
            except Exception as e:
                logger.error(f"스크린샷 캡처 실패: {e}")
            finally:
                self.deiconify()
                self.grab_set()

        def on_cancel():
            self.deiconify()
            self.grab_set()

        self.after(100, lambda: ScreenRegionSelector(self, on_region_select, on_cancel))

    def _remove_image(self, img_path: str):
        """이미지 삭제"""
        if img_path in self._rule.target_images:
            self._rule.target_images.remove(img_path)
            logger.info(f"멀티이미지 삭제: {img_path}")

            if self._on_change:
                self._on_change()

            self._refresh_list()


class AutomationPlanDialog(ctk.CTkToplevel):
    """자동화 분석 결과 다이얼로그"""

    def __init__(self, parent, plan: AutomationPlan):
        super().__init__(parent)

        self._plan = plan
        self._result = False
        self._thumbnail_refs = []
        self._collapsed_items = set()  # 접힌 항목 ID
        self._all_collapsed = True  # 기본: 접힌 상태
        self._scrollable = None

        # 자식이 있는 규칙은 기본적으로 접힌 상태로 시작
        self._init_collapsed_items()

        self.title("분석 결과 확인")
        self.geometry("950x750")
        self.resizable(True, True)
        self.configure(fg_color=COLORS["bg_dark"])

        self.transient(parent)
        self.grab_set()

        # 창 위치 복원 및 자동 저장
        self.update_idletasks()
        setup_window_position(self, "AutomationPlanDialog")

        self._setup_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)

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
        # 하단 버튼
        btn_frame = ctk.CTkFrame(self, fg_color=COLORS["bg_card"], height=70)
        btn_frame.pack(side="bottom", fill="x")
        btn_frame.pack_propagate(False)

        btn_content = ctk.CTkFrame(btn_frame, fg_color="transparent")
        btn_content.pack(expand=True)

        ctk.CTkButton(
            btn_content,
            text="✓  승인하고 저장",
            command=self._on_approve,
            width=180,
            height=45,
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            font=ctk.CTkFont(size=14, weight="bold"),
            corner_radius=8,
        ).pack(side="left", padx=15)

        ctk.CTkButton(
            btn_content,
            text="취소",
            command=self._on_cancel,
            width=100,
            height=45,
            fg_color=COLORS["bg_dark"],
            hover_color=COLORS["bg_card_hover"],
            text_color=COLORS["text_secondary"],
            corner_radius=8,
        ).pack(side="left", padx=15)

        # 메인 영역
        main = ctk.CTkFrame(self, fg_color="transparent")
        main.pack(fill="both", expand=True, padx=25, pady=25)

        # 헤더
        header_row = ctk.CTkFrame(main, fg_color="transparent")
        header_row.pack(fill="x")

        ctk.CTkLabel(
            header_row,
            text="녹화된 동작 목록",
            font=ctk.CTkFont(size=22, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(side="left")

        # 모두 접기/펼치기 버튼
        self._collapse_btn = ctk.CTkButton(
            header_row,
            text="모두 펼치기",
            command=self._toggle_all_collapse,
            width=90,
            height=28,
            fg_color=COLORS["bg_card"],
            hover_color=COLORS["bg_card_hover"],
            text_color=COLORS["text_secondary"],
            font=ctk.CTkFont(size=11),
            corner_radius=6,
        )
        self._collapse_btn.pack(side="right")

        # 전체 규칙 수 계산 (자식 포함)
        total_count = self._count_all_rules(self._plan.initial_rules + self._plan.monitoring_rules)
        ctk.CTkLabel(
            main,
            text=f"총 {total_count}개의 동작이 감지되었습니다. 확인 후 승인하세요.",
            font=ctk.CTkFont(size=13),
            text_color=COLORS["text_secondary"],
        ).pack(anchor="w", pady=(5, 20))

        # 동작 목록
        self._scrollable = ctk.CTkScrollableFrame(
            main,
            fg_color=COLORS["bg_card"],
            corner_radius=12,
            scrollbar_button_color=COLORS["bg_card_hover"],
        )
        self._scrollable.pack(fill="both", expand=True)

        self._refresh_action_list()

    def _count_all_rules(self, rules) -> int:
        """모든 규칙 수 계산 (자식 포함)"""
        count = 0
        for rule in rules:
            count += 1
            if rule.children:
                count += self._count_all_rules(rule.children)
        return count

    def _toggle_all_collapse(self):
        """모든 액션 접기/펼치기"""
        if self._all_collapsed:
            # 모두 펼치기
            self._collapsed_items.clear()
            self._all_collapsed = False
            self._collapse_btn.configure(text="모두 접기")
        else:
            # 모두 접기
            self._init_collapsed_items()
            self._all_collapsed = True
            self._collapse_btn.configure(text="모두 펼치기")
        self._refresh_action_list()

    def _toggle_item_collapse(self, rule_id: str):
        """개별 액션 접기/펼치기"""
        if rule_id in self._collapsed_items:
            self._collapsed_items.remove(rule_id)
        else:
            self._collapsed_items.add(rule_id)
        self._refresh_action_list()

    def _refresh_action_list(self):
        """액션 목록 새로고침"""
        # 썸네일 참조 정리
        self._thumbnail_refs.clear()

        # 기존 위젯 삭제
        for widget in self._scrollable.winfo_children():
            widget.destroy()

        # 규칙 렌더링 (자식 포함)
        counter = [0]  # 전체 번호 매기기용
        self._render_rules(self._scrollable, self._plan.initial_rules, counter, depth=0)
        self._render_rules(self._scrollable, self._plan.monitoring_rules, counter, depth=0)

    def _render_rules(self, parent, rules, counter, depth=0):
        """규칙 목록 렌더링 (계층 구조 지원)"""
        for rule in rules:
            counter[0] += 1
            self._create_action_item(parent, counter[0], rule, depth)

            # 자식 규칙 렌더링 (접히지 않은 경우)
            if rule.children and rule.rule_id not in self._collapsed_items:
                self._render_rules(parent, rule.children, counter, depth + 1)

    def _create_action_item(self, parent, index: int, rule: AutomationRule, depth: int = 0):
        """동작 항목 생성"""
        # 깊이에 따른 들여쓰기 계산
        indent = depth * 25
        left_pad = 10 + indent

        # 자식이 있으면 다른 배경색
        bg_color = COLORS["bg_dark"] if depth == 0 else "#252535"

        item = ctk.CTkFrame(parent, fg_color=bg_color, corner_radius=8)
        item.pack(fill="x", pady=3, padx=(left_pad, 10))

        content = ctk.CTkFrame(item, fg_color="transparent")
        content.pack(fill="x", padx=12, pady=8)

        # 접기/펼치기 버튼 (자식이 있는 경우)
        if rule.children:
            is_collapsed = rule.rule_id in self._collapsed_items
            toggle_text = "▶" if is_collapsed else "▼"
            child_count = self._count_all_rules(rule.children)
            ctk.CTkButton(
                content,
                text=f"{toggle_text} {child_count}",
                command=lambda r=rule: self._toggle_item_collapse(r.rule_id),
                width=45,
                height=24,
                fg_color=COLORS["bg_card"],
                hover_color=COLORS["bg_card_hover"],
                text_color=COLORS["text_muted"],
                font=ctk.CTkFont(size=11),
                corner_radius=4,
            ).pack(side="left", padx=(0, 8))

        # 썸네일
        thumb = ctk.CTkFrame(content, fg_color=COLORS["bg_card"], width=60, height=60, corner_radius=8)
        thumb.pack(side="left", padx=(0, 12))
        thumb.pack_propagate(False)
        self._display_thumbnail(thumb, rule)

        # 정보
        info = ctk.CTkFrame(content, fg_color="transparent")
        info.pack(side="left", fill="both", expand=True)

        # 번호 + 동작 유형
        row1 = ctk.CTkFrame(info, fg_color="transparent")
        row1.pack(fill="x")

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

        ctk.CTkLabel(
            row1,
            text=f"{index}",
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color=color,
            text_color="white",
            corner_radius=4,
            width=28,
            height=24,
        ).pack(side="left", padx=(0, 10))

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
        ctk.CTkLabel(
            row1,
            text=action_names.get(rule.action_type, rule.action_type or "동작"),
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=color,
        ).pack(side="left")

        # 상세 정보
        row2 = ctk.CTkFrame(info, fg_color="transparent")
        row2.pack(fill="x", pady=(5, 0))

        details = []
        if rule.action_type == "drag" and rule.action_x is not None and rule.action_y is not None:
            # 드래그: 시작 → 끝 좌표 표시
            if rule.drag_to_x is not None and rule.drag_to_y is not None:
                coord_text = f"({rule.action_x}, {rule.action_y}) → ({rule.drag_to_x}, {rule.drag_to_y})"
                # 드래그 소요 시간이 있으면 표시
                drag_dur = getattr(rule, 'drag_duration', None)
                if drag_dur and drag_dur > 0:
                    coord_text += f" ({drag_dur:.2f}초)"
                details.append(coord_text)
            else:
                details.append(f"시작: ({rule.action_x}, {rule.action_y})")
        elif rule.action_x is not None and rule.action_y is not None:
            details.append(f"위치: ({rule.action_x}, {rule.action_y})")
        if rule.action_text:
            text_preview = rule.action_text[:25] + "..." if len(rule.action_text) > 25 else rule.action_text
            details.append(f'입력: "{text_preview}"')
        if rule.action_keys:
            details.append(f"키: {' + '.join(rule.action_keys)}")

        if details:
            ctk.CTkLabel(
                row2,
                text="  |  ".join(details),
                font=ctk.CTkFont(size=12),
                text_color=COLORS["text_secondary"],
            ).pack(side="left")

        # 대기 시간
        if rule.wait_after and rule.wait_after > 0.3:
            ctk.CTkLabel(
                row2,
                text=f"대기 {rule.wait_after:.1f}초",
                font=ctk.CTkFont(size=11),
                text_color=COLORS["warning"],
            ).pack(side="right")

    def _display_thumbnail(self, parent, rule: AutomationRule):
        """썸네일 표시 (클릭 시 확대/크롭)"""
        image_path = rule.target_image

        # 경로 검증: 보안 검증만 수행 (TOCTOU 버그 방지를 위해 존재 여부는 검증 안함)
        if image_path and self._is_valid_image_path(image_path):
            try:
                # 캐시 확인 (60x60 고정 크기)
                cached = get_cached_thumbnail(image_path, (60, 60))
                if cached is not None:
                    ctk_image = cached
                    new_w, new_h = 60, 60
                else:
                    # 파일 읽기 시도 - 한글 경로 지원
                    img_arr = np.fromfile(image_path, np.uint8)
                    img = cv2.imdecode(img_arr, cv2.IMREAD_COLOR)
                    if img is None:
                        raise ValueError("Image load failed")
                    img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                    h, w = img_rgb.shape[:2]
                    scale = min(60 / w, 60 / h)
                    new_w, new_h = int(w * scale), int(h * scale)
                    resized = cv2.resize(img_rgb, (new_w, new_h))
                    pil_image = Image.fromarray(resized)
                    ctk_image = ctk.CTkImage(light_image=pil_image, dark_image=pil_image, size=(new_w, new_h))
                    set_cached_thumbnail(image_path, (60, 60), ctk_image)

                # 클릭 가능한 버튼으로 생성
                thumb_btn = ctk.CTkButton(
                    parent,
                    image=ctk_image,
                    text="",
                    width=new_w + 10,
                    height=new_h + 10,
                    fg_color="transparent",
                    hover_color=COLORS["bg_card_hover"],
                    corner_radius=4,
                    command=lambda p=image_path, r=rule: self._open_image_editor(p, r),
                )
                thumb_btn.pack(expand=True)
                self._thumbnail_refs.append(ctk_image)
                return
            except Exception as e:
                logger.warning(f"썸네일 로드 실패: {image_path} - {e}")

        icons = {"click": "🖱", "type": "⌨", "hotkey": "⌨", "scroll": "📜", "drag": "↔"}
        ctk.CTkLabel(
            parent,
            text=icons.get(rule.action_type, "📋"),
            font=ctk.CTkFont(size=24),
            text_color=COLORS["text_muted"],
        ).pack(expand=True)

    def _open_image_editor(self, image_path: str, rule: AutomationRule):
        """이미지 편집기 열기"""
        def on_crop_complete(new_path: str):
            # 크롭 완료 후 썸네일 캐시 무효화
            from .player_view import invalidate_thumbnail_cache
            invalidate_thumbnail_cache(new_path)
            logger.info(f"이미지 크롭 완료: {new_path}")

        dialog = ImageCropDialog(self, image_path, on_crop=on_crop_complete)
        self.wait_window(dialog)

    def _on_approve(self):
        self._result = True
        self._cleanup_resources()
        self.destroy()

    def _on_cancel(self):
        self._result = False
        self._cleanup_resources()
        self.destroy()

    def _cleanup_resources(self):
        """다이얼로그 리소스 정리"""
        # 썸네일 이미지 참조 해제 (메모리 누수 방지)
        self._thumbnail_refs.clear()

    def _is_valid_image_path(self, path: str) -> bool:
        """이미지 경로 유효성 검증 (보안 검증만, 존재 여부는 읽기 시점에 확인)

        Note: TOCTOU 버그 방지를 위해 파일 존재 여부는 검증하지 않음.
        실제 파일 읽기는 cv2.imread에서 수행하며, None 반환으로 처리.
        """
        if not path:
            return False
        try:
            p = Path(path)
            # 절대 경로인지 확인
            if not p.is_absolute():
                return False
            # 경로 탐색 공격 방지 (.. 포함 여부)
            if ".." in str(p):
                return False
            # 이미지 확장자 확인
            valid_extensions = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"}
            if p.suffix.lower() not in valid_extensions:
                return False
            return True
        except (ValueError, TypeError, OSError):
            return False

    def get_result(self) -> bool:
        return self._result

    def get_plan(self) -> AutomationPlan:
        return self._plan


class AnalyzerView(BaseView):
    """분석 화면 - 프리미엄 디자인"""

    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)

        self._video_analyzer = get_video_analyzer()
        self._db = get_db()

        self._selected_video: Optional[str] = None
        self._selected_input_log: Optional[str] = None
        self._is_analyzing = False
        self._analyze_lock = threading.Lock()  # 다중 호출 방지용 Lock
        self._selected_item_widget = None  # 선택된 항목 위젯

        self._automation_plan_result: Optional[bool] = None
        self._automation_plan_event = threading.Event()
        self._last_automation_plan: Optional[AutomationPlan] = None

        self._plan_modified_cache = {}  # plan_id → modified 캐시
        self._plan_lock_cache = {}  # plan_id → locked 캐시
        self._plans_load_generation: int = 0  # async/sync 플랜 로드 경쟁 방지

        self._setup_ui()
        self.after(0, self._deferred_load)  # UI 렌더 후 데이터 로드

    def _deferred_load(self):
        """UI 표시 후 데이터 로드 (after(0) 콜백)"""
        self._load_recordings()  # 내부에서 _build_plan_modified_cache() 호출
        self._load_plans_async()

    def _build_plan_modified_cache(self):
        """플랜 파일의 modified 상태를 미리 캐시 (recording item에서 파일 I/O 방지)"""
        self._plan_modified_cache = {}
        if PLANS_DIR.exists():
            for plan_file in PLANS_DIR.glob("*.json"):
                try:
                    with open(plan_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    plan_id = data.get("plan_id", plan_file.stem)
                    self._plan_modified_cache[plan_id] = data.get("modified", False)
                except Exception:
                    pass

    def _load_plans_async(self):
        """플랜 목록을 백그라운드 스레드에서 로드"""
        self._plans_load_generation += 1
        current_gen = self._plans_load_generation

        def _load():
            plans = []
            templates_dir = DATA_DIR / "templates"
            if PLANS_DIR.exists():
                for plan_file in PLANS_DIR.glob("*.json"):
                    try:
                        with open(plan_file, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        if not isinstance(data, dict):
                            continue
                        plan = AutomationPlan.from_dict(data, templates_dir=templates_dir)
                        plans.append(plan)
                    except json.JSONDecodeError as e:
                        logger.error(f"계획 JSON 파싱 실패: {plan_file} - {e}")
                    except KeyError as e:
                        logger.error(f"계획 필수 필드 누락: {plan_file} - 키: {e}")
                    except (TypeError, ValueError) as e:
                        logger.error(f"계획 데이터 형식 오류: {plan_file} - {e}")
                    except Exception as e:
                        logger.error(f"계획 로드 실패: {plan_file} - {e}")
            self.after(0, lambda: self._apply_plans(plans, current_gen))

        threading.Thread(target=_load, daemon=True).start()

    def _apply_plans(self, plans, generation=None):
        """백그라운드에서 로드된 플랜을 UI에 적용"""
        # generation이 현재보다 오래된 경우 무시 (sync 로드가 이미 최신 데이터 적용)
        if generation is not None and generation < self._plans_load_generation:
            return
        for widget in self._plans_scroll.winfo_children():
            widget.destroy()

        if not plans:
            ctk.CTkLabel(
                self._plans_scroll,
                text="분석된 재생이 없습니다\n녹화를 분석하세요",
                font=ctk.CTkFont(size=12),
                text_color=COLORS["text_muted"],
                justify="center",
            ).pack(pady=30)
            return

        # plan_id → locked 캐시 구축 (루프 내 DB 쿼리 방지)
        self._plan_lock_cache = {}
        for plan in plans:
            recording = self._db.get_recording_by_plan_id(plan.plan_id)
            self._plan_lock_cache[plan.plan_id] = recording.locked if recording else False

        for plan in plans:
            self._create_plan_item(plan)

    def _setup_ui(self):
        # 스크롤 가능한 메인 컨테이너 (로그 패널 확장 시 축소 가능)
        self._scroll_frame = ctk.CTkScrollableFrame(
            self,
            fg_color="transparent",
        )
        self._scroll_frame.pack(fill="both", expand=True)

        # 그리드 컨테이너
        main_frame = ctk.CTkFrame(self._scroll_frame, fg_color="transparent")
        main_frame.pack(fill="both", expand=True, padx=8, pady=8)

        # 그리드 설정 (2행 2열, 균등 비율)
        main_frame.grid_columnconfigure(0, weight=1, uniform="col")
        main_frame.grid_columnconfigure(1, weight=1, uniform="col")
        main_frame.grid_rowconfigure(0, weight=1)
        main_frame.grid_rowconfigure(1, weight=1)

        # 좌상단: 녹화 목록
        recordings_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        recordings_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 4), pady=(0, 4))
        self._setup_recordings_card(recordings_frame)

        # 우상단: 분석 실행
        analyze_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        analyze_frame.grid(row=0, column=1, sticky="nsew", padx=(4, 0), pady=(0, 4))
        self._setup_analyze_card(analyze_frame)

        # 좌하단: 분석된 재생 목록
        plans_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        plans_frame.grid(row=1, column=0, sticky="nsew", padx=(0, 4), pady=(4, 0))
        self._setup_plans_card(plans_frame)

        # 우하단: 분석 결과
        result_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        result_frame.grid(row=1, column=1, sticky="nsew", padx=(4, 0), pady=(4, 0))
        self._setup_result_card(result_frame)

    def _setup_analyze_card(self, parent):
        """분석 카드"""
        card = self.create_card(parent, title="분석 실행")
        card.pack(fill="both", expand=True)

        content = ctk.CTkFrame(card, fg_color="transparent")
        content.pack(fill="x", padx=20, pady=15)

        # 계획 이름
        self.create_label(content, "자동화 계획 이름", style="body").pack(anchor="w")

        self._plan_name_entry = ctk.CTkEntry(
            content,
            placeholder_text="자동 생성됨",
            height=38,
            fg_color=COLORS["bg_dark"],
            border_color=COLORS["border"],
            text_color=COLORS["text_primary"],
        )
        self._plan_name_entry.pack(fill="x", pady=(5, 15))

        # 버튼
        btn_row = ctk.CTkFrame(content, fg_color="transparent")
        btn_row.pack(fill="x")

        self._analyze_btn = self.create_button(
            btn_row,
            text="🔍  동작 분석 시작",
            command=self._on_analyze,
            style="primary",
            width=180,
            height=44,
        )
        self._analyze_btn.pack(side="left", padx=(0, 10))

        self._cancel_btn = self.create_button(
            btn_row,
            text="취소",
            command=self._on_cancel,
            style="secondary",
            width=80,
            height=44,
        )
        self._cancel_btn.pack(side="left")
        self._cancel_btn.configure(state="disabled")

        # 진행 상태
        progress_frame = ctk.CTkFrame(content, fg_color="transparent")
        progress_frame.pack(fill="x", pady=(15, 0))

        self._progress_label = ctk.CTkLabel(
            progress_frame,
            text="대기 중",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["text_muted"],
        )
        self._progress_label.pack(anchor="w")

        self._progress_bar = ctk.CTkProgressBar(
            progress_frame,
            fg_color=COLORS["bg_dark"],
            progress_color=COLORS["accent"],
            height=6,
        )
        self._progress_bar.pack(fill="x", pady=(5, 0))
        self._progress_bar.set(0)

    def _setup_result_card(self, parent):
        """결과 카드"""
        card = self.create_card(parent, title="분석 결과")
        card.pack(fill="both", expand=True)

        content = ctk.CTkFrame(card, fg_color="transparent")
        content.pack(fill="x", padx=20, pady=15)

        self._result_status = ctk.CTkLabel(
            content,
            text="분석 결과가 여기에 표시됩니다",
            font=ctk.CTkFont(size=13),
            text_color=COLORS["text_muted"],
        )
        self._result_status.pack(anchor="w")

    def _setup_plans_card(self, parent):
        """분석된 재생 목록 카드"""
        card = self.create_card(parent, title="분석된 재생 목록")
        card.pack(fill="both", expand=True)

        header = ctk.CTkFrame(card, fg_color="transparent")
        header.pack(fill="x", padx=15, pady=(10, 5))

        self.create_button(
            header,
            text="새로고침",
            command=self._load_plans,
            style="ghost",
            width=70,
            height=26,
        ).pack(side="right")

        # 미사용 이미지 정리 버튼
        ctk.CTkButton(
            header,
            text="🗑 미사용 이미지 정리",
            font=ctk.CTkFont(size=12),
            fg_color=COLORS["bg_card"],
            hover_color=COLORS["bg_card_hover"],
            text_color=COLORS["text_primary"],
            width=140,
            height=26,
            corner_radius=6,
            command=self._cleanup_unused_images,
        ).pack(side="right", padx=(0, 8))

        self._plans_scroll = ctk.CTkScrollableFrame(
            card,
            fg_color="transparent",
            scrollbar_button_color=COLORS["bg_card_hover"],
        )
        self._plans_scroll.pack(fill="both", expand=True, padx=10, pady=(0, 10))

    def _load_plans(self):
        """분석된 재생 목록 로드"""
        self._plans_load_generation += 1  # 진행 중인 async 로드 무효화
        for widget in self._plans_scroll.winfo_children():
            widget.destroy()

        plans = []
        templates_dir = DATA_DIR / "templates"
        if PLANS_DIR.exists():
            for plan_file in PLANS_DIR.glob("*.json"):
                try:
                    with open(plan_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    # 필수 필드 존재 여부 확인
                    if not isinstance(data, dict):
                        logger.warning(f"잘못된 계획 형식: {plan_file}")
                        continue
                    plan = AutomationPlan.from_dict(data, templates_dir=templates_dir)
                    plans.append(plan)
                except json.JSONDecodeError as e:
                    logger.error(f"계획 JSON 파싱 실패: {plan_file} - {e}")
                except KeyError as e:
                    logger.error(f"계획 필수 필드 누락: {plan_file} - 키: {e}")
                except (TypeError, ValueError) as e:
                    logger.error(f"계획 데이터 형식 오류: {plan_file} - {e}")
                except Exception as e:
                    logger.error(f"계획 로드 실패: {plan_file} - {e}")

        if not plans:
            ctk.CTkLabel(
                self._plans_scroll,
                text="분석된 재생이 없습니다\n녹화를 분석하세요",
                font=ctk.CTkFont(size=12),
                text_color=COLORS["text_muted"],
                justify="center",
            ).pack(pady=30)
            return

        # plan_id → locked 캐시 갱신
        self._plan_lock_cache = {}
        for plan in plans:
            recording = self._db.get_recording_by_plan_id(plan.plan_id)
            self._plan_lock_cache[plan.plan_id] = recording.locked if recording else False

        for plan in plans:
            self._create_plan_item(plan)

    def _create_plan_item(self, plan: AutomationPlan):
        """분석된 재생 항목 생성"""
        # 연관된 녹화의 잠금 상태 확인 (캐시 사용 — 루프 내 DB 쿼리 방지)
        is_locked = self._plan_lock_cache.get(plan.plan_id, False)

        item = ctk.CTkFrame(
            self._plans_scroll,
            fg_color=COLORS["bg_dark"],
            corner_radius=8,
        )
        item.pack(fill="x", pady=3)

        content = ctk.CTkFrame(item, fg_color="transparent")
        content.pack(fill="x", padx=10, pady=8)

        # 잠금 아이콘 (잠겨있으면 표시)
        if is_locked:
            ctk.CTkLabel(
                content,
                text="🔒",
                font=ctk.CTkFont(size=14, weight="bold"),
                text_color="#ff6b6b",
            ).pack(side="left", padx=(0, 5))

        # 이름
        name_label = ctk.CTkLabel(
            content,
            text=plan.name,
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COLORS["text_primary"],
            anchor="w",
        )
        name_label.pack(side="left", fill="x", expand=True)

        # 동작 수
        all_rules = plan.initial_rules + plan.monitoring_rules
        count_label = ctk.CTkLabel(
            content,
            text=f"{len(all_rules)}개 동작",
            font=ctk.CTkFont(size=10),
            text_color=COLORS["text_muted"],
        )
        count_label.pack(side="left", padx=(10, 5))

        # 삭제 버튼 (빨간색)
        ctk.CTkButton(
            content,
            text="삭제",
            command=lambda p=plan: self._delete_plan(p),
            width=40,
            height=20,
            fg_color=COLORS["error"],
            hover_color="#dc2626",
            text_color="white",
            font=ctk.CTkFont(size=11),
            corner_radius=4,
        ).pack(side="right")

        # 수정 버튼
        ctk.CTkButton(
            content,
            text="수정",
            command=lambda p=plan: self._edit_plan(p),
            width=40,
            height=20,
            fg_color=COLORS["accent_blue"],
            hover_color="#2563eb",
            text_color="white",
            font=ctk.CTkFont(size=11),
            corner_radius=4,
        ).pack(side="right", padx=(0, 3))

    def _edit_plan(self, plan: AutomationPlan):
        """재생 수정"""
        from .player_view import PlanDetailDialog
        dialog = PlanDetailDialog(self, plan)
        self.wait_window(dialog)
        self._load_recordings()  # 녹화 목록도 새로고침 (이름 동기화)
        self._load_plans()

    def _delete_plan(self, plan: AutomationPlan):
        """재생 삭제"""
        from tkinter import messagebox

        # 연관된 녹화의 잠금 상태 확인
        recording = self._db.get_recording_by_plan_id(plan.plan_id)
        if recording and recording.locked:
            messagebox.showwarning(
                "삭제 불가",
                f"'{plan.name}'은(는) 잠금 상태입니다.\n\n"
                "삭제하려면 먼저 🔓 해제 버튼을 눌러 잠금을 해제하세요."
            )
            return

        if not messagebox.askyesno("재생 삭제", f"'{plan.name}'을(를) 삭제하시겠습니까?"):
            return

        try:
            plan_file = PLANS_DIR / f"{plan.plan_id}.json"
            if plan_file.exists():
                plan_file.unlink()
                logger.info(f"재생 삭제: {plan.name}")

            # 모든 뷰 새로고침 (실행 뷰에도 반영)
            main_window = self.winfo_toplevel()
            if hasattr(main_window, 'refresh_all_views'):
                main_window.refresh_all_views()
            else:
                self._load_plans()
        except Exception as e:
            logger.error(f"재생 삭제 실패: {e}")
            messagebox.showerror("오류", f"삭제 실패: {e}")

    def _setup_recordings_card(self, parent):
        """녹화 목록 카드"""
        card = self.create_card(parent, title="녹화 목록")
        card.pack(fill="both", expand=True)

        header = ctk.CTkFrame(card, fg_color="transparent")
        header.pack(fill="x", padx=15, pady=(10, 5))

        self.create_button(
            header,
            text="새로고침",
            command=self._load_recordings,
            style="ghost",
            width=70,
            height=26,
        ).pack(side="right")

        self._recordings_scroll = ctk.CTkScrollableFrame(
            card,
            fg_color="transparent",
            scrollbar_button_color=COLORS["bg_card_hover"],
        )
        self._recordings_scroll.pack(fill="both", expand=True, padx=10, pady=(0, 10))

    def _load_recordings(self):
        """녹화 목록 로드"""
        self._build_plan_modified_cache()  # 캐시 갱신

        for widget in self._recordings_scroll.winfo_children():
            widget.destroy()

        self._selected_item_widget = None  # 선택 상태 초기화

        recordings = self._db.get_all_recordings()

        if not recordings:
            ctk.CTkLabel(
                self._recordings_scroll,
                text="녹화 파일이 없습니다\n먼저 녹화를 진행하세요",
                font=ctk.CTkFont(size=12),
                text_color=COLORS["text_muted"],
                justify="center",
            ).pack(pady=30)
            return

        for recording in recordings:
            self._create_recording_item(recording)

    def _create_recording_item(self, recording: Recording):
        """녹화 항목 생성"""
        item = ctk.CTkFrame(
            self._recordings_scroll,
            fg_color=COLORS["bg_dark"],
            corner_radius=8,
            cursor="hand2",
        )
        item.pack(fill="x", pady=3)

        # 클릭으로 선택 기능
        def on_click(event, r=recording, w=item):
            self._select_recording_item(r, w)

        # 항목 전체에 클릭 이벤트 바인딩
        item.bind("<Button-1>", on_click)

        # 상단: 이름 + 상태
        top_row = ctk.CTkFrame(item, fg_color="transparent")
        top_row.pack(fill="x", padx=10, pady=(8, 2))
        top_row.bind("<Button-1>", on_click)

        # 잠금 아이콘 (녹화가 잠겨있으면 표시)
        if recording.locked:
            lock_label = ctk.CTkLabel(
                top_row,
                text="🔒",
                font=ctk.CTkFont(size=16, weight="bold"),
                text_color="#ff6b6b",  # 빨간색 계열로 눈에 띄게
                cursor="hand2",
            )
            lock_label.pack(side="left", padx=(0, 5))
            lock_label.bind("<Button-1>", on_click)

        name_label = ctk.CTkLabel(
            top_row,
            text=recording.name,
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COLORS["text_primary"],
            anchor="w",
            cursor="hand2",
        )
        name_label.pack(side="left", fill="x", expand=True)
        name_label.bind("<Button-1>", on_click)

        # ai_analyzed 또는 automation_plan_id로 분석 완료 여부 확인
        is_analyzed = recording.ai_analyzed or recording.automation_plan_id
        is_modified = False

        # 수정 여부 확인 (캐시 사용 — 파일 I/O 방지)
        if recording.automation_plan_id:
            is_modified = self._plan_modified_cache.get(recording.automation_plan_id, False)

        if is_modified:
            status = "🔒 수정됨"
            status_color = COLORS["accent_blue"]
        elif is_analyzed:
            status = "✅ 분석완료"
            status_color = COLORS["success"]
        else:
            status = "⏳ 미분석"
            status_color = COLORS["warning"]

        status_label = ctk.CTkLabel(
            top_row,
            text=status,
            font=ctk.CTkFont(size=10),
            text_color=status_color,
            cursor="hand2",
        )
        status_label.pack(side="right")
        status_label.bind("<Button-1>", on_click)

        # 하단: 버튼들 (오른쪽 정렬)
        btn_row = ctk.CTkFrame(item, fg_color="transparent")
        btn_row.pack(fill="x", padx=10, pady=(2, 8))
        btn_row.bind("<Button-1>", on_click)

        # 삭제 버튼
        ctk.CTkButton(
            btn_row,
            text="삭제",
            command=lambda r=recording: self._delete_recording(r),
            width=50,
            height=26,
            fg_color=COLORS["bg_card"],
            hover_color=COLORS["bg_card_hover"],
            text_color=COLORS["text_secondary"],
            font=ctk.CTkFont(size=12),
            corner_radius=4,
        ).pack(side="right")

        # 잠금/해제 버튼
        lock_text = "🔓 해제" if recording.locked else "🔒 잠금"
        lock_color = "#dc3545" if recording.locked else COLORS["bg_card"]  # 잠금 시 빨간색
        ctk.CTkButton(
            btn_row,
            text=lock_text,
            command=lambda r=recording: self._toggle_recording_lock(r),
            width=70,
            height=28,
            fg_color=lock_color,
            hover_color="#c82333" if recording.locked else COLORS["bg_card_hover"],
            text_color="white" if recording.locked else COLORS["text_secondary"],
            font=ctk.CTkFont(size=13, weight="bold"),
            corner_radius=4,
        ).pack(side="right", padx=(0, 5))

        # 수정은 '분석된 재생 목록'에서만 가능 (녹화 목록에서는 수정 버튼 제거)

    def _select_recording_item(self, recording: Recording, widget):
        """녹화 항목 선택 (클릭으로)"""
        # 이전 선택 해제
        if self._selected_item_widget:
            self._selected_item_widget.configure(fg_color=COLORS["bg_dark"])

        # 새 항목 선택 (초록색 배경)
        widget.configure(fg_color=COLORS["accent"])
        self._selected_item_widget = widget

        # 녹화 정보 설정
        self._selected_video = recording.video_path
        self._selected_input_log = recording.input_log_path
        self._plan_name_entry.delete(0, "end")
        self._plan_name_entry.insert(0, f"{recording.name}_자동화")

    def _show_recording_detail(self, recording: Recording):
        """녹화 상세보기 - 재생 또는 자동화 계획"""
        # 재생가 있으면 재생 상세보기
        if recording.sequence_id:
            sequence = self._db.get_sequence(recording.sequence_id)
            if sequence:
                from .player_view import SequenceDetailDialog
                dialog = SequenceDetailDialog(self, sequence, self._db)
                self.wait_window(dialog)
                return

        # 자동화 계획이 있으면 계획 상세보기
        if recording.automation_plan_id:
            self._show_plan_detail(recording)

    def _show_plan_detail(self, recording: Recording):
        """분석된 계획 상세보기"""
        if not recording.automation_plan_id:
            return

        plan_file = PLANS_DIR / f"{recording.automation_plan_id}.json"
        if not plan_file.exists():
            from tkinter import messagebox
            messagebox.showwarning("계획 없음", "저장된 자동화 계획을 찾을 수 없습니다.")
            return

        try:
            with open(plan_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            templates_dir = DATA_DIR / "templates"
            plan = AutomationPlan.from_dict(data, templates_dir=templates_dir)

            # PlanDetailDialog 열기 (player_view에서 import)
            from .player_view import PlanDetailDialog
            dialog = PlanDetailDialog(self, plan)
            self.wait_window(dialog)
            self._load_recordings()  # 녹화 목록 새로고침 (이름 동기화)
            self._load_plans()  # 분석된 재생 목록도 새로고침
        except Exception as e:
            logger.error(f"계획 로드 실패: {e}")
            from tkinter import messagebox
            messagebox.showerror("오류", f"계획을 불러올 수 없습니다:\n{e}")

    def _toggle_recording_lock(self, recording: Recording):
        """녹화 잠금/해제 토글"""
        from tkinter import messagebox

        if not recording.id:
            return

        # 잠금 해제 시 확인
        if recording.locked:
            if not messagebox.askyesno(
                "잠금 해제",
                f"'{recording.name}'의 잠금을 해제하시겠습니까?\n\n"
                "잠금 해제 후 삭제가 가능해집니다."
            ):
                return

        new_locked = not recording.locked
        self._db.update_recording_locked(recording.id, new_locked)
        self._load_recordings()  # 목록 새로고침
        self._load_plans()  # 분석된 재생 목록도 새로고침

    def _delete_recording(self, recording: Recording):
        """분석 목록에서 녹화 삭제 (원본 파일도 함께 삭제)"""
        from tkinter import messagebox

        # 잠금 체크
        if recording.locked:
            messagebox.showwarning(
                "삭제 불가",
                f"'{recording.name}'은(는) 잠금 상태입니다.\n\n"
                "삭제하려면 먼저 🔓해제 버튼을 눌러 잠금을 해제하세요."
            )
            return

        # 삭제 확인
        msg = f"'{recording.name}'을(를) 삭제하시겠습니까?\n\n"
        msg += "원본 녹화 파일이 삭제됩니다.\n"
        msg += "(분석된 재생 목록은 유지됩니다)"

        if not messagebox.askyesno("삭제", msg):
            return

        # 선택된 녹화가 삭제되는 경우 선택 해제
        if self._selected_video == recording.video_path:
            self._selected_video = None
            self._selected_input_log = None
            self._selected_item_widget = None

        # DB에서 녹화 삭제 (원본 파일도 함께 삭제)
        try:
            self._db.delete_recording(recording.id, delete_files=True)
            logger.info(f"녹화 삭제 완료 (파일 포함): {recording.name}")
        except Exception as e:
            logger.error(f"녹화 삭제 실패: {e}")
            messagebox.showerror("오류", f"삭제 실패: {e}")
            return

        # 양쪽 목록 새로고침
        self._load_recordings()
        self._load_plans()

    def _on_analyze(self):
        """분석 시작"""
        try:
            if not self._selected_video:
                self._progress_label.configure(text="녹화를 먼저 선택하세요", text_color=COLORS["warning"])
                return

            # 수정된 녹화는 재분석 불가
            try:
                recording = self._db.get_recording_by_video_path(self._selected_video)
                if recording and recording.automation_plan_id:
                    plan_file = PLANS_DIR / f"{recording.automation_plan_id}.json"
                    if plan_file.exists():
                        try:
                            with open(plan_file, "r", encoding="utf-8") as f:
                                plan_data = json.load(f)
                            if plan_data.get("modified", False):
                                self._progress_label.configure(
                                    text="⚠️ 수정된 녹화는 재분석할 수 없습니다",
                                    text_color=COLORS["error"]
                                )
                                return
                        except Exception as e:
                            logger.warning(f"계획 파일 확인 실패: {e}")
            except Exception as e:
                logger.warning(f"녹화 정보 확인 실패: {e}")

            # 다중 호출 방지 (Lock 사용)
            with self._analyze_lock:
                if self._is_analyzing:
                    return
                self._is_analyzing = True
        except Exception as e:
            logger.error(f"분석 시작 오류: {e}")
            return

        plan_name = self._plan_name_entry.get().strip()
        if not plan_name:
            plan_name = f"자동화_{Path(self._selected_video).stem}"

        self._analyze_btn.configure(state="disabled")
        self._cancel_btn.configure(state="normal")
        self._progress_label.configure(text="🔍 분석 준비 중... (입력 로그 확인)", text_color=COLORS["text_secondary"])

        self._video_analyzer.set_callbacks(
            on_progress=self._on_progress,
            on_automation_plan_ready=self._on_automation_plan_ready,
        )

        self._video_analyzer.analyze_for_automation_async(
            video_path=self._selected_video,
            input_log_path=self._selected_input_log,
            plan_name=plan_name,
            on_complete=self._on_analysis_complete,
        )

    def _on_cancel(self):
        try:
            self._video_analyzer.cancel()
            with self._analyze_lock:
                self._is_analyzing = False
            self._analyze_btn.configure(state="normal")
            self._cancel_btn.configure(state="disabled")
            self._progress_label.configure(text="취소됨", text_color=COLORS["warning"])
        except Exception as e:
            logger.error(f"취소 오류: {e}")

    def _on_progress(self, progress: AnalysisProgress):
        self.after(0, lambda: self._update_progress(progress))

    def _update_progress(self, progress: AnalysisProgress):
        self._progress_label.configure(text=progress.message)
        self._progress_bar.set(progress.progress_percent / 100)

    def _on_automation_plan_ready(self, plan: AutomationPlan) -> bool:
        try:
            self._automation_plan_event.clear()
            self._automation_plan_result = None
            self._dialog_scheduled = False

            def show_dialog_wrapper():
                try:
                    self._dialog_scheduled = True
                    self._show_automation_plan_dialog(plan)
                except Exception as e:
                    logger.error(f"다이얼로그 표시 오류: {e}")
                    self._automation_plan_event.set()

            self.after(0, show_dialog_wrapper)

            # 다이얼로그 완료 대기 (설정에서 타임아웃 값 가져오기)
            from ..utils.config import get_config
            timeout_seconds = get_config().analyzer.dialog_timeout_seconds
            if not self._automation_plan_event.wait(timeout=timeout_seconds):
                # 타임아웃 발생
                if not self._dialog_scheduled:
                    logger.error("자동화 계획 대화상자가 스케줄되지 않음 - UI 스레드 문제 가능성")
                else:
                    logger.warning(f"자동화 계획 대화상자 응답 타임아웃 ({timeout_seconds}초)")
                return False

            return self._automation_plan_result if self._automation_plan_result is not None else False
        except Exception as e:
            logger.error(f"_on_automation_plan_ready 오류: {e}")
            return False

    def _show_automation_plan_dialog(self, plan: AutomationPlan):
        try:
            dialog = AutomationPlanDialog(self, plan)
            dialog.grab_set()  # 모달 설정
            self.wait_window(dialog)
            self._automation_plan_result = dialog.get_result()
            if self._automation_plan_result:
                self._last_automation_plan = dialog.get_plan()
        except Exception as e:
            logger.error(f"다이얼로그 오류: {e}")
            self._automation_plan_result = False
        finally:
            try:
                self._automation_plan_event.set()
            except:
                pass

    def _on_analysis_complete(self, plan: Optional[AutomationPlan]):
        self.after(0, lambda: self._show_analysis_result(plan))

    def _show_analysis_result(self, plan: Optional[AutomationPlan]):
        with self._analyze_lock:
            self._is_analyzing = False
        self._analyze_btn.configure(state="normal")
        self._cancel_btn.configure(state="disabled")

        if plan is not None:
            self._progress_label.configure(text="✅ 분석 완료", text_color=COLORS["success"])
            self._progress_bar.set(1)

            all_rules = plan.initial_rules + plan.monitoring_rules
            status = "✅ 승인됨" if plan.user_verified else "⏳ 미승인"
            status_color = COLORS["success"] if plan.user_verified else COLORS["warning"]

            self._result_status.configure(
                text=f"계획: {plan.name}\n동작 수: {len(all_rules)}개\n상태: {status}",
                text_color=status_color
            )

            self._last_automation_plan = plan
            logger.info(f"자동화 계획 완료: {plan.name}")

            if plan.user_verified:
                self._save_automation_plan(plan)

                if self._selected_video:
                    recording = self._db.get_recording_by_video_path(self._selected_video)
                    if recording and recording.id:
                        self._db.update_recording_ai_analyzed(recording.id, plan.plan_id)
        else:
            self._progress_label.configure(text="❌ 분석 실패", text_color=COLORS["error"])
            self._result_status.configure(
                text="분석에 실패했습니다.\n\n가능한 원인:\n• 입력 로그 파일이 없음\n• 영상 파일 손상\n• 녹화 중 동작 없음\n\n해결: 녹화를 다시 시도해보세요.",
                text_color=COLORS["error"]
            )

        self._load_recordings()
        self._load_plans()

    def _save_automation_plan(self, plan: AutomationPlan) -> bool:
        try:
            PLANS_DIR.mkdir(parents=True, exist_ok=True)
            file_path = PLANS_DIR / f"{plan.plan_id}.json"

            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(plan.to_dict(), f, ensure_ascii=False, indent=2)
            logger.info(f"자동화 계획 저장: {file_path}")
            return True
        except Exception as e:
            logger.error(f"저장 실패: {e}")
            return False

    def get_last_automation_plan(self) -> Optional[AutomationPlan]:
        return self._last_automation_plan

    def _cleanup_unused_images(self) -> None:
        """미사용 이미지 파일 정리 (스캔은 백그라운드, UI는 메인 스레드)"""
        from tkinter import messagebox
        import threading

        templates_dir = DATA_DIR / "templates"
        if not templates_dir.exists():
            messagebox.showinfo("정리 완료", "templates 폴더가 없습니다.")
            return

        def _scan_and_cleanup():
            """백그라운드에서 이미지 스캔 후 메인 스레드에서 확인/삭제"""
            # 1. 모든 플랜에서 사용 중인 이미지 경로 수집
            used_images = set()

            def collect_images_from_rules(rules):
                for rule in rules:
                    if rule.target_image:
                        used_images.add(Path(rule.target_image).resolve())
                    if rule.trigger_image:
                        used_images.add(Path(rule.trigger_image).resolve())
                    if hasattr(rule, 'target_images') and rule.target_images:
                        for img_path in rule.target_images:
                            if img_path:
                                used_images.add(Path(img_path).resolve())
                    if rule.children:
                        collect_images_from_rules(rule.children)

            if PLANS_DIR.exists():
                for plan_file in PLANS_DIR.glob("*.json"):
                    try:
                        with open(plan_file, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        plan = AutomationPlan.from_dict(data, templates_dir=templates_dir)
                        collect_images_from_rules(plan.initial_rules)
                        collect_images_from_rules(plan.monitoring_rules)
                    except Exception as e:
                        logger.error(f"플랜 로드 실패 ({plan_file}): {e}")

            # 2. templates 폴더의 모든 이미지 파일 수집
            all_images = set()
            for ext in ["*.png", "*.jpg", "*.jpeg", "*.bmp", "*.gif"]:
                for img_file in templates_dir.glob(ext):
                    all_images.add(img_file.resolve())

            # 3. 미사용 이미지 찾기
            unused_images = all_images - used_images

            # 4. 메인 스레드에서 확인 UI 표시 및 삭제
            def _confirm_and_delete():
                if not unused_images:
                    messagebox.showinfo("정리 완료", "삭제할 미사용 이미지가 없습니다.")
                    return

                confirm = messagebox.askyesno(
                    "이미지 정리",
                    f"미사용 이미지 {len(unused_images)}개를 삭제하시겠습니까?\n\n"
                    f"전체 이미지: {len(all_images)}개\n"
                    f"사용 중: {len(used_images)}개\n"
                    f"삭제 대상: {len(unused_images)}개"
                )

                if not confirm:
                    return

                # 삭제도 백그라운드에서 수행
                def _do_delete():
                    deleted_count = 0
                    for img_path in unused_images:
                        try:
                            img_path.unlink()
                            deleted_count += 1
                            logger.info(f"미사용 이미지 삭제: {img_path}")
                        except Exception as e:
                            logger.error(f"이미지 삭제 실패 ({img_path}): {e}")

                    final_count = deleted_count
                    def _show_result():
                        messagebox.showinfo("정리 완료", f"미사용 이미지 {final_count}개를 삭제했습니다.")
                    try:
                        self.after(0, _show_result)
                    except (tk.TclError, RuntimeError):
                        pass
                    logger.info(f"이미지 정리 완료: {deleted_count}개 삭제")

                threading.Thread(target=_do_delete, daemon=True).start()

            try:
                self.after(0, _confirm_and_delete)
            except (tk.TclError, RuntimeError):
                pass

        threading.Thread(target=_scan_and_cleanup, daemon=True).start()

    def refresh(self):
        """뷰 새로고침"""
        self._load_recordings()  # 내부에서 _build_plan_modified_cache() 호출
        self._load_plans_async()

    def cleanup(self):
        if self._is_analyzing:
            self._video_analyzer.cancel()
