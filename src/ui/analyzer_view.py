"""
WinCro 분석 화면 모듈

프리미엄 카드 기반 UI 디자인
"""

import customtkinter as ctk
import tkinter as tk
from tkinter import filedialog, Canvas
from typing import Optional, Callable
from pathlib import Path
import os
import re
import queue
import threading
import time
import cv2
import numpy as np
import json
from PIL import Image, ImageTk

from ..utils.logger import get_logger
from ..utils.config import DATA_DIR, get_config, save_config
from ..utils.json_utils import load_json_file
from ..utils.window_position import setup_window_position

PLANS_DIR = DATA_DIR / "plans"
from ..analyzer import get_video_analyzer, AnalysisProgress
from ..analyzer.automation_models import AutomationPlan, AutomationRule
from ..database import get_db, Recording, Sequence
from .main_window import BaseView, COLORS
from .theme import IOS_FONTS, IOS_METRICS
from .text_overflow import truncate_ui_text
from .image_crop_utils import (
    auto_extract_foreground_mask,
    fit_image_to_box,
    get_sidecar_mask_path,
    load_sidecar_mask,
    normalize_binary_mask,
)
from .ui_batcher import UiCallbackDispatcher
from .virtual_scroll import VirtualScrollFrame

logger = get_logger(__name__)

IMAGE_FILE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"}
VIDEO_FILE_EXTS = {".mp4", ".avi", ".mov", ".mkv", ".webm"}
MEDIA_FILE_EXTS = IMAGE_FILE_EXTS | VIDEO_FILE_EXTS
IMAGE_FILE_PATTERNS = "*.png *.jpg *.jpeg *.bmp *.gif *.webp"
VIDEO_FILE_PATTERNS = "*.mp4 *.avi *.mov *.mkv *.webm"
MEDIA_FILE_PATTERNS = f"{IMAGE_FILE_PATTERNS} {VIDEO_FILE_PATTERNS}"
UI_ASSETS_DIR = Path(__file__).with_name("assets")
VIDEO_PLAY_ICON_FILE = UI_ASSETS_DIR / "bootstrap_play_circle_fill.png"

# 썸네일 캐시 (성능 최적화)
_thumbnail_cache = {}  # {cache_key: CTkImage}
_thumbnail_cache_lock = threading.Lock()
MAX_THUMBNAIL_CACHE = 100
_thumbnail_task_queue = queue.Queue()
_thumbnail_worker_lock = threading.Lock()
_thumbnail_workers_started = False
_THUMBNAIL_WORKER_COUNT = 4


def submit_thumbnail_task(task):
    """Run thumbnail decoding on a small daemon worker pool instead of one thread per image."""
    global _thumbnail_workers_started
    if not _thumbnail_workers_started:
        with _thumbnail_worker_lock:
            if not _thumbnail_workers_started:
                for index in range(_THUMBNAIL_WORKER_COUNT):
                    worker = threading.Thread(
                        target=_thumbnail_worker_loop,
                        name=f"wincro-thumbnail-{index + 1}",
                        daemon=True,
                    )
                    worker.start()
                _thumbnail_workers_started = True
    _thumbnail_task_queue.put(task)


def _thumbnail_worker_loop():
    while True:
        task = _thumbnail_task_queue.get()
        try:
            task()
        except Exception as exc:
            logger.warning(f"Thumbnail worker error: {exc}")
        finally:
            _thumbnail_task_queue.task_done()


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


def invalidate_thumbnail_cache(image_path: str):
    """변경된 이미지 경로의 썸네일 캐시를 제거합니다."""
    if not image_path:
        return
    with _thumbnail_cache_lock:
        keys_to_remove = [key for key in _thumbnail_cache if image_path in key]
        for key in keys_to_remove:
            del _thumbnail_cache[key]


_WINDOWS_RESERVED_FILENAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def sanitize_template_filename(raw_name: str, fallback_suffix: str = ".png") -> Optional[str]:
    """Return a safe template filename while preserving Korean characters."""
    name = (raw_name or "").strip()
    if not name:
        return None

    name = Path(name).name
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    if not name:
        return None

    path = Path(name)
    suffix = path.suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg", ".bmp", ".gif"}:
        stem = path.stem
    else:
        stem = name
        suffix = fallback_suffix if fallback_suffix.startswith(".") else f".{fallback_suffix}"

    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", stem).strip(" .")
    if not stem or stem.upper() in _WINDOWS_RESERVED_FILENAMES:
        return None
    return f"{stem}{suffix}"


def sanitize_template_media_filename(raw_name: str, fallback_suffix: str = ".mp4") -> Optional[str]:
    """Return a safe template media filename for cropped video outputs."""
    name = (raw_name or "").strip()
    if not name:
        return None

    name = Path(name).name
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    if not name:
        return None

    path = Path(name)
    suffix = path.suffix.lower()
    if suffix in VIDEO_FILE_EXTS:
        stem = path.stem
    else:
        stem = name
        suffix = fallback_suffix if fallback_suffix.startswith(".") else f".{fallback_suffix}"

    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", stem).strip(" .")
    if not stem or stem.upper() in _WINDOWS_RESERVED_FILENAMES:
        return None
    return f"{stem}{suffix}"


def unique_template_path(directory: Path, filename: str) -> Path:
    """Return a non-destructive path that does not collide with image or sidecar mask files."""
    directory.mkdir(parents=True, exist_ok=True)
    candidate = directory / filename
    if not candidate.exists() and not get_sidecar_mask_path(candidate).exists():
        return candidate

    stem = candidate.stem
    suffix = candidate.suffix or ".png"
    for index in range(2, 10000):
        next_candidate = directory / f"{stem}_{index}{suffix}"
        if not next_candidate.exists() and not get_sidecar_mask_path(next_candidate).exists():
            return next_candidate
    return directory / f"{stem}_{int(time.time())}{suffix}"


def write_image_file(path: Path, image: np.ndarray) -> bool:
    """Write OpenCV image arrays with Unicode path support on Windows."""
    suffix = path.suffix or ".png"
    ok, encoded = cv2.imencode(suffix, image)
    if not ok:
        return False
    encoded.tofile(str(path))
    return True


def is_video_media_path(path: str | Path | None) -> bool:
    if not path:
        return False
    try:
        return Path(path).suffix.lower() in VIDEO_FILE_EXTS
    except (TypeError, ValueError):
        return False


def is_supported_media_path(path: str | Path | None) -> bool:
    if not path:
        return False
    try:
        return Path(path).suffix.lower() in MEDIA_FILE_EXTS
    except (TypeError, ValueError):
        return False


def list_template_media_paths(directory: str | Path | None = None) -> list[Path]:
    """Return template images/videos by modified time, bypassing native file dialog filters."""
    base_dir = Path(directory) if directory else DATA_DIR / "templates"
    if not base_dir.exists():
        return []

    media_paths = []
    try:
        for path in base_dir.iterdir():
            if path.is_file() and is_supported_media_path(path):
                media_paths.append(path)
    except OSError as exc:
        logger.warning(f"템플릿 미디어 목록 읽기 실패: {base_dir} ({exc})")
        return []

    media_paths.sort(key=lambda p: (p.stat().st_mtime if p.exists() else 0, p.name), reverse=True)
    return media_paths


class TemplateMediaSelectDialog(ctk.CTkToplevel):
    """Simple template media picker that always shows videos as well as images."""

    def __init__(self, parent, *, title: str = "템플릿 이미지/동영상 선택", directory: str | Path | None = None):
        super().__init__(parent)
        self.title(title)
        self.geometry("780x560")
        self.minsize(620, 420)
        self.transient(parent)
        self.grab_set()

        self.result: str | None = None
        self._all_paths = list_template_media_paths(directory)
        self._visible_paths = list(self._all_paths)
        self._preview_image = None

        root = ctk.CTkFrame(self, fg_color=COLORS["bg_content"])
        root.pack(fill="both", expand=True, padx=14, pady=14)

        header = ctk.CTkFrame(root, fg_color="transparent")
        header.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(
            header,
            text="템플릿 이미지/동영상",
            font=ctk.CTkFont(family=IOS_FONTS["family"], size=18, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(side="left")
        ctk.CTkLabel(
            header,
            text=f"{len(self._all_paths)}개",
            font=ctk.CTkFont(family=IOS_FONTS["family"], size=12, weight="bold"),
            text_color=COLORS["accent_text"],
        ).pack(side="right")

        self._search_var = tk.StringVar()
        search = ctk.CTkEntry(
            root,
            textvariable=self._search_var,
            placeholder_text="파일명 검색",
            height=34,
        )
        search.pack(fill="x", pady=(0, 10))
        self._search_var.trace_add("write", lambda *_: self._refresh_list())

        body = ctk.CTkFrame(root, fg_color="transparent")
        body.pack(fill="both", expand=True)
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=0)
        body.grid_rowconfigure(0, weight=1)

        self._listbox = tk.Listbox(
            body,
            height=18,
            exportselection=False,
            bg=COLORS["bg_log"],
            fg=COLORS["text_primary"],
            selectbackground=COLORS["accent_blue"],
            selectforeground=COLORS["text_on_accent"],
            activestyle="none",
            font=("Malgun Gothic", 10),
            highlightthickness=IOS_METRICS["canvas_border_width"],
            highlightbackground=COLORS["image_canvas_border"],
        )
        self._listbox.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        self._listbox.bind("<<ListboxSelect>>", lambda _e: self._update_preview())
        self._listbox.bind("<Double-Button-1>", lambda _e: self._confirm())
        self._listbox.bind("<Return>", lambda _e: self._confirm())

        preview_frame = ctk.CTkFrame(
            body,
            fg_color=COLORS["bg_card"],
            corner_radius=IOS_METRICS["card_radius_compact"],
            width=210,
        )
        preview_frame.grid(row=0, column=1, sticky="ns")
        preview_frame.grid_propagate(False)
        self._preview_label = ctk.CTkLabel(
            preview_frame,
            text="미리보기",
            text_color=COLORS["text_muted"],
        )
        self._preview_label.pack(fill="both", expand=True, padx=10, pady=10)
        self._detail_label = ctk.CTkLabel(
            preview_frame,
            text="",
            text_color=COLORS["text_secondary"],
            font=ctk.CTkFont(family=IOS_FONTS["family"], size=11),
            wraplength=180,
            justify="center",
        )
        self._detail_label.pack(fill="x", padx=10, pady=(0, 10))

        buttons = ctk.CTkFrame(root, fg_color="transparent")
        buttons.pack(fill="x", pady=(12, 0))
        ctk.CTkButton(
            buttons,
            text="취소",
            command=self.destroy,
            width=90,
            height=34,
            fg_color=COLORS["bg_elevated"],
            hover_color=COLORS["bg_card_hover"],
        ).pack(side="right", padx=(8, 0))
        ctk.CTkButton(
            buttons,
            text="선택",
            command=self._confirm,
            width=110,
            height=34,
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
        ).pack(side="right")

        self._refresh_list()
        if self._visible_paths:
            self._listbox.selection_set(0)
            self._listbox.activate(0)
            self._update_preview()
        self.after(50, search.focus_set)

    def _refresh_list(self):
        keyword = self._search_var.get().strip().lower()
        if keyword:
            self._visible_paths = [path for path in self._all_paths if keyword in path.name.lower()]
        else:
            self._visible_paths = list(self._all_paths)

        self._listbox.delete(0, "end")
        for path in self._visible_paths:
            kind = "영상" if is_video_media_path(path) else "이미지"
            try:
                size_kb = max(1, path.stat().st_size // 1024)
            except OSError:
                size_kb = 0
            self._listbox.insert("end", f"[{kind}] {path.name}  ({size_kb}KB)")
        if self._visible_paths:
            self._listbox.selection_set(0)
            self._listbox.activate(0)
            self._update_preview()
        else:
            self._preview_image = None
            self._preview_label.configure(image=None, text="없음")
            self._detail_label.configure(text="")

    def _selected_path(self) -> Path | None:
        selection = self._listbox.curselection()
        if not selection:
            return None
        index = int(selection[0])
        if not (0 <= index < len(self._visible_paths)):
            return None
        return self._visible_paths[index]

    def _update_preview(self):
        path = self._selected_path()
        if path is None:
            return
        try:
            if is_video_media_path(path):
                cap = cv2.VideoCapture(str(path))
                try:
                    ok, frame = cap.read()
                    if not ok or frame is None:
                        raise RuntimeError("video frame read failed")
                    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                finally:
                    cap.release()
            else:
                arr = np.fromfile(str(path), np.uint8)
                img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                if img is None:
                    raise RuntimeError("image read failed")
                rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)

            h, w = rgb.shape[:2]
            scale = min(180 / max(1, w), 180 / max(1, h), 1.0)
            new_w = max(1, int(w * scale))
            new_h = max(1, int(h * scale))
            resized = cv2.resize(rgb, (new_w, new_h), interpolation=cv2.INTER_AREA)
            pil = Image.fromarray(resized)
            self._preview_image = ctk.CTkImage(light_image=pil, dark_image=pil, size=(new_w, new_h))
            self._preview_label.configure(image=self._preview_image, text="")
            kind = "동영상" if is_video_media_path(path) else "이미지"
            self._detail_label.configure(text=f"{kind}\n{path.name}\n{w} x {h}px")
        except Exception:
            self._preview_image = None
            self._preview_label.configure(image=None, text="미리보기 실패")
            self._detail_label.configure(text=path.name)

    def _confirm(self):
        path = self._selected_path()
        if path is None:
            return
        self.result = str(path)
        self.destroy()


def select_template_media_file(parent, *, title: str = "템플릿 이미지/동영상 선택", directory: str | Path | None = None) -> str | None:
    dialog = TemplateMediaSelectDialog(parent, title=title, directory=directory)
    parent.wait_window(dialog)
    return dialog.result


def get_template_media_settings_path(media_path: str | Path) -> Path:
    path = Path(media_path)
    return path.with_name(f"{path.name}.wincro.json")


class TemplateMediaSettings:
    """Image-editor-compatible settings for loose template media files."""

    def __init__(self, media_path: str):
        self.target_image = str(media_path)
        self.confidence = 0.65
        self.verify_image_color = False
        self.verify_image_brightness = False
        self.search_radius = 0
        self.search_region = None
        self.move_mouse_before_search = False
        self.action_x = None
        self.action_y = None
        self._settings_path = get_template_media_settings_path(media_path)
        self._load()

    def _load(self) -> None:
        try:
            if not self._settings_path.exists():
                return
            data = json.loads(self._settings_path.read_text(encoding="utf-8-sig"))
            self.confidence = float(data.get("confidence", self.confidence) or self.confidence)
            self.verify_image_color = bool(data.get("verify_image_color", False))
            self.verify_image_brightness = bool(data.get("verify_image_brightness", False))
            self.search_region = data.get("search_region")
            self.search_radius = int(data.get("search_radius", 0) or 0)
            self.move_mouse_before_search = bool(data.get("move_mouse_before_search", False))
            self.action_x = data.get("action_x")
            self.action_y = data.get("action_y")
        except Exception as exc:
            logger.warning(f"템플릿 미디어 설정 로드 실패: {self._settings_path} ({exc})")

    def save(self) -> None:
        try:
            self._settings_path = get_template_media_settings_path(self.target_image)
            data = {
                "target_image": Path(self.target_image).name,
                "confidence": self.confidence,
                "verify_image_color": self.verify_image_color,
                "verify_image_brightness": self.verify_image_brightness,
                "search_region": self.search_region,
                "search_radius": self.search_radius,
                "move_mouse_before_search": self.move_mouse_before_search,
                "action_x": self.action_x,
                "action_y": self.action_y,
            }
            self._settings_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.warning(f"템플릿 미디어 설정 저장 실패: {self._settings_path} ({exc})")


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
        self.configure(bg=COLORS["overlay_dim"])
        self.overrideredirect(True)

        # 화면 크기
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()

        # 캔버스 생성
        self._canvas = Canvas(
            self,
            width=screen_w,
            height=screen_h,
            bg=COLORS["image_canvas_bg"],
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
                outline=COLORS["error"], width=3, fill="", dash=(8, 4)
            )
            # 기존 범위 라벨
            self._canvas.create_text(
                ex1, ey1 - 20,
                text=f"기존 범위: ({ex1}, {ey1}) ~ ({ex2}, {ey2})",
                font=("맑은 고딕", 13, "bold"),
                fill=COLORS["error"],
                anchor="sw"
            )
            # 안내 텍스트 (기존 범위가 있을 때)
            self._canvas.create_text(
                screen_w // 2, 50,
                text="마우스로 드래그하여 새 검색 영역을 선택하세요 (ESC: 취소)",
                font=("맑은 고딕", 16, "bold"),
                fill=COLORS["overlay_text"]
            )
            self._canvas.create_text(
                screen_w // 2, 80,
                text="빨간색 점선 = 기존 범위  |  파란색 실선 = 새 범위",
                font=("맑은 고딕", 12),
                fill=COLORS["text_secondary"]
            )
        else:
            # 안내 텍스트
            self._canvas.create_text(
                screen_w // 2, 50,
                text="마우스로 드래그하여 검색 영역을 선택하세요 (ESC: 취소)",
                font=("맑은 고딕", 16, "bold"),
                fill=COLORS["overlay_text"]
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
            outline=COLORS["info"], width=3, fill=""
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
        self._is_video = is_video_media_path(image_path)
        self._video_frame_count = 0
        self._video_fps = 0.0
        self._video_duration_sec = 0.0
        self._video_capture = None
        self._video_play_after_id = None
        self._video_playing = False
        self._video_frame_interval_ms = 100
        self._video_current_frame_index = 0
        self._video_play_btn = None
        self._video_stop_btn = None
        self._video_overlay_btn = None
        self._video_overlay_photo = None
        self._video_progress_canvas = None
        self._video_time_label = None
        self._cropped_preview = None
        self._cropped_photo = None
        self._scale = 1.0
        self._min_scale = 0.1
        self._max_scale = 8.0
        self._initial_zoom_cap = 5.0
        self._crop_coords = None  # 크롭 좌표 저장
        self._crop_mask = None
        self._crop_mask_needs_refresh = False
        self._full_image_mask = None
        self._background_cutout_var = ctk.BooleanVar(value=False)
        self._crop_filename_var = tk.StringVar(value="")
        self._crop_filename_entry = None
        self._crop_filename_hint_label = None
        self._edit_mode = "select"

        # 이미지 내비게이션
        self._image_list = image_list or []
        self._current_index = current_index

        # 크롭 선택 영역
        self._start_x = 0
        self._start_y = 0
        self._rect_id = None
        self._selecting = False
        self._mouse_down_had_crop = False
        self._overlay_photo = None

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
        self.title(f"{'동영상' if self._is_video else '이미지'} 편집: {filename}")
        self.configure(fg_color=COLORS["bg_content"])

        # 모달 설정
        self.transient(parent)
        self.grab_set()

        self._load_image()
        self._setup_ui()

        # 키보드 이벤트 바인딩 (화살표 키로 이미지 전환)
        self._bind_crop_keyboard_controls()

    def destroy(self):
        self._stop_video_preview()
        self._release_video_capture()
        try:
            super().destroy()
        except tk.TclError:
            pass

    def _release_video_capture(self):
        cap = getattr(self, "_video_capture", None)
        if cap is not None:
            try:
                cap.release()
            except Exception:
                pass
        self._video_capture = None

    def _stop_video_preview(self):
        after_id = getattr(self, "_video_play_after_id", None)
        if after_id is not None:
            try:
                self.after_cancel(after_id)
            except Exception:
                pass
        self._video_play_after_id = None
        self._video_playing = False
        self._update_video_control_state()

    def _toggle_video_preview(self):
        if self._video_playing:
            self._stop_video_preview()
        else:
            self._resume_video_preview()

    def _stop_video_from_button(self):
        self._stop_video_preview()

    def _resume_video_preview(self):
        if not self._is_video or self._original_image is None:
            return
        if self._video_capture is None:
            cap = cv2.VideoCapture(str(self._image_path))
            if not cap.isOpened():
                logger.warning(f"동영상 미리보기 캡처 열기 실패: {self._image_path}")
                return
            self._video_capture = cap
            try:
                self._video_capture.set(cv2.CAP_PROP_POS_FRAMES, self._video_current_frame_index)
            except Exception:
                pass
        self._video_playing = True
        self._update_video_control_state()
        self._schedule_video_preview_frame(0)

    def _restart_video_preview(self):
        if not self._is_video:
            return
        self._stop_video_preview()
        if self._video_capture is None:
            cap = cv2.VideoCapture(str(self._image_path))
            if not cap.isOpened():
                logger.warning(f"동영상 미리보기 캡처 열기 실패: {self._image_path}")
                return
            self._video_capture = cap
        try:
            self._video_capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
        except Exception:
            pass
        self._video_current_frame_index = 0
        try:
            ok, frame = self._video_capture.read()
            if ok and frame is not None:
                self._original_image = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                self._full_image_mask = None
                self._video_capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
                self._update_canvas_image()
                if self._crop_coords is not None:
                    self._refresh_preview()
        except Exception as exc:
            logger.warning(f"동영상 첫 프레임 복구 실패: {exc}")
        self._video_playing = False
        self._update_video_control_state()

    def _format_video_time_text(self) -> str:
        fps = self._video_fps if self._video_fps > 0 else 0.0
        current_sec = (self._video_current_frame_index / fps) if fps > 0 else 0.0
        duration_sec = self._video_duration_sec if self._video_duration_sec > 0 else 0.0
        percent = int(round(self._video_progress_ratio() * 100))
        if duration_sec > 0:
            return f"{percent}%  {current_sec:.1f}s / {duration_sec:.1f}s"
        return f"{percent}%  {current_sec:.1f}s"

    def _video_progress_ratio(self) -> float:
        if self._video_frame_count > 1:
            return max(0.0, min(1.0, self._video_current_frame_index / (self._video_frame_count - 1)))
        if self._video_duration_sec > 0 and self._video_fps > 0:
            current_sec = self._video_current_frame_index / self._video_fps
            return max(0.0, min(1.0, current_sec / self._video_duration_sec))
        return 0.0

    def _update_video_progress_bar(self):
        if self._video_progress_canvas is None:
            return
        try:
            canvas = self._video_progress_canvas
            rendered_width = int(canvas.winfo_width() or 0)
            rendered_height = int(canvas.winfo_height() or 0)
            config_width = int(canvas.cget("width"))
            config_height = int(canvas.cget("height"))
            width = max(1, rendered_width if rendered_width > 1 else config_width)
            height = max(1, rendered_height if rendered_height > 1 else config_height)
            fill_w = int(width * self._video_progress_ratio())
            canvas.delete("all")
            canvas.create_rectangle(0, 0, width, height, fill=COLORS["bg_card"], outline="")
            if fill_w > 0:
                canvas.create_rectangle(0, 0, fill_w, height, fill=COLORS["accent_blue"], outline="")
            canvas.create_rectangle(
                0,
                0,
                width - 1,
                height - 1,
                outline=COLORS["image_canvas_border"],
                width=IOS_METRICS["canvas_border_width"],
            )
        except tk.TclError:
            pass

    def _update_video_control_state(self):
        if self._video_play_btn is not None:
            try:
                self._video_play_btn.configure(text="재생")
            except tk.TclError:
                pass
        if self._video_stop_btn is not None:
            try:
                self._video_stop_btn.configure(text="중지")
            except tk.TclError:
                pass
        if self._video_time_label is not None:
            try:
                self._video_time_label.configure(text=self._format_video_time_text())
            except tk.TclError:
                pass
        self._update_video_progress_bar()
        if self._video_overlay_btn is not None:
            try:
                self._canvas.itemconfigure("video_overlay", state="hidden" if self._video_playing else "normal")
            except (tk.TclError, AttributeError):
                pass

    def _load_video_play_overlay_photo(self, size: int = 76):
        if self._video_overlay_photo is not None:
            return self._video_overlay_photo
        if not VIDEO_PLAY_ICON_FILE.exists():
            logger.warning("Bootstrap play icon asset missing: %s", VIDEO_PLAY_ICON_FILE)
            return None
        try:
            with Image.open(VIDEO_PLAY_ICON_FILE) as image:
                icon = image.convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)
            self._video_overlay_photo = ImageTk.PhotoImage(icon)
            return self._video_overlay_photo
        except Exception as exc:
            logger.warning("Bootstrap play icon asset load failed: %s", exc)
            return None

    def _draw_video_overlay_play_button(self):
        if not self._is_video or not hasattr(self, "_canvas"):
            return
        if self._video_playing:
            return
        center_x = self._canvas_width // 2
        center_y = self._canvas_height // 2
        photo = self._load_video_play_overlay_photo()
        if photo is None:
            return
        play_id = self._canvas.create_image(
            center_x,
            center_y,
            image=photo,
            anchor="center",
            tags=("video_overlay",),
        )
        self._video_overlay_btn = play_id
        self._canvas.tag_bind(play_id, "<Button-1>", lambda _event: self._resume_video_preview())
        self._canvas.tag_bind("video_overlay", "<Enter>", lambda _event: self._canvas.configure(cursor="hand2"))
        self._canvas.tag_bind("video_overlay", "<Leave>", lambda _event: self._canvas.configure(cursor="crosshair"))

    def _start_video_preview(self):
        if not self._is_video or self._original_image is None:
            return
        if not hasattr(self, "_canvas") or not hasattr(self, "_preview_canvas"):
            return
        self._stop_video_preview()
        if self._video_capture is None:
            cap = cv2.VideoCapture(str(self._image_path))
            if not cap.isOpened():
                logger.warning(f"동영상 미리보기 캡처 열기 실패: {self._image_path}")
                return
            self._video_capture = cap
        try:
            self._video_capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
        except Exception:
            pass
        self._video_current_frame_index = 0
        self._video_playing = True
        self._update_video_control_state()
        self._schedule_video_preview_frame(0)

    def _schedule_video_preview_frame(self, delay_ms: Optional[int] = None):
        if not self._video_playing:
            return
        delay = self._video_frame_interval_ms if delay_ms is None else max(0, int(delay_ms))
        self._video_play_after_id = self.after(delay, self._advance_video_preview_frame)

    def _advance_video_preview_frame(self):
        self._video_play_after_id = None
        if not self._video_playing or not self._is_video or self._video_capture is None:
            return
        if not self.winfo_exists():
            return

        try:
            ok, frame = self._video_capture.read()
            if not ok or frame is None:
                self._video_capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
                ok, frame = self._video_capture.read()
            if ok and frame is not None:
                frame_pos = int(self._video_capture.get(cv2.CAP_PROP_POS_FRAMES) or 0)
                self._video_current_frame_index = max(0, frame_pos - 1)
                self._original_image = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                self._full_image_mask = None
                self._update_canvas_image()
                if self._crop_coords is not None:
                    self._refresh_preview()
                self._update_video_control_state()
        except Exception as exc:
            logger.warning(f"동영상 미리보기 프레임 갱신 실패: {exc}")
            self._stop_video_preview()
            return

        self._schedule_video_preview_frame()

    def _load_image(self):
        """이미지 로드"""
        try:
            # 한글 경로 지원
            self._stop_video_preview()
            self._release_video_capture()
            self._is_video = is_video_media_path(self._image_path)
            self._video_frame_count = 0
            self._video_fps = 0.0
            self._video_duration_sec = 0.0
            self._video_frame_interval_ms = 100
            if self._is_video:
                cap = cv2.VideoCapture(str(self._image_path))
                if not cap.isOpened():
                    logger.error(f"동영상 로드 실패: {self._image_path}")
                    return
                try:
                    self._video_frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
                    self._video_fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
                    if self._video_frame_count > 0 and self._video_fps > 0:
                        self._video_duration_sec = self._video_frame_count / self._video_fps
                    preview_fps = min(max(self._video_fps or 10.0, 1.0), 12.0)
                    self._video_frame_interval_ms = max(80, int(1000 / preview_fps))
                    ok, frame = cap.read()
                    if not ok or frame is None:
                        logger.error(f"동영상 첫 프레임 로드 실패: {self._image_path}")
                        return
                    self._original_image = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    self._full_image_mask = None
                    self._crop_mask = None
                finally:
                    cap.release()
                return

            img_array = np.fromfile(self._image_path, np.uint8)
            self._original_image = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
            if self._original_image is not None:
                self._original_image = cv2.cvtColor(self._original_image, cv2.COLOR_BGR2RGB)
                h, w = self._original_image.shape[:2]
                self._full_image_mask = load_sidecar_mask(self._image_path, (h, w))
                if self._full_image_mask is not None:
                    self._crop_coords = (0, 0, w, h)
                    self._crop_mask = self._full_image_mask.copy()
                else:
                    self._crop_mask = None
        except Exception as e:
            logger.error(f"이미지 로드 실패: {e}")

    def _configure_image_view_metrics(self, image_width: int, image_height: int) -> None:
        screen_w = max(1024, int(self.winfo_screenwidth() or 1024))
        screen_h = max(768, int(self.winfo_screenheight() or 768))

        side_reserved = 360 if self._is_video else 300
        canvas_w = min(1180, max(760, screen_w - side_reserved))
        canvas_h = min(720, max(500, screen_h - 280))
        win_w = min(screen_w - 32, canvas_w + side_reserved)
        win_h = min(screen_h - 32, canvas_h + 220)

        self._canvas_width = max(640, win_w - side_reserved)
        self._canvas_height = max(420, win_h - 220)
        self._max_scale = 8.0
        self._initial_zoom_cap = 5.0
        self._reset_image_scale(image_width, image_height)

    def _fit_canvas_scale(self, image_width: int, image_height: int, cap: float | None = None) -> float:
        if image_width <= 0 or image_height <= 0:
            return 1.0
        fit_scale = min(self._canvas_width / image_width, self._canvas_height / image_height)
        if cap is not None:
            fit_scale = min(fit_scale, cap)
        return max(0.05, fit_scale)

    def _reset_image_scale(self, image_width: int, image_height: int) -> None:
        self._scale = self._fit_canvas_scale(image_width, image_height, cap=self._initial_zoom_cap)
        self._min_scale = max(0.05, min(0.25, self._scale * 0.5))

    def _format_image_info_text(self) -> str:
        if self._original_image is None:
            return ""
        h, w = self._original_image.shape[:2]
        if self._is_video:
            duration = f"{self._video_duration_sec:.1f}s" if self._video_duration_sec > 0 else "길이 미상"
            fps = f"{self._video_fps:.1f}fps" if self._video_fps > 0 else "fps 미상"
            return f"동영상: {w} x {h} px  |  {duration} / {fps}  |  표시: {int(self._scale * 100)}%"
        return f"원본: {w} x {h} px  |  표시: {int(self._scale * 100)}%"

    def _update_image_info_label(self) -> None:
        if hasattr(self, "_info_label"):
            self._info_label.configure(text=self._format_image_info_text())

    def _bind_crop_keyboard_controls(self):
        for sequence in (
            "<Left>", "<Right>", "<Up>", "<Down>",
            "<Shift-Left>", "<Shift-Right>", "<Shift-Up>", "<Shift-Down>",
        ):
            self.bind(sequence, self._on_crop_arrow_key)

    def _focus_crop_canvas(self):
        if not hasattr(self, "_canvas"):
            return
        try:
            self._canvas.focus_set()
        except Exception:
            pass

    def _on_crop_arrow_key(self, event):
        if self._crop_coords is None or (
            event.keysym in ("Left", "Right") and self._is_full_image_crop_selection()
        ):
            if event.keysym == "Left":
                self._navigate_image(-1)
                return "break"
            if event.keysym == "Right":
                self._navigate_image(1)
                return "break"
            return "break"

        step = 10 if (getattr(event, "state", 0) & 0x0001) else 1
        moves = {
            "Left": (-step, 0),
            "Right": (step, 0),
            "Up": (0, -step),
            "Down": (0, step),
        }
        dx, dy = moves.get(event.keysym, (0, 0))
        if dx or dy:
            self._move_crop_selection(dx, dy)
        return "break"

    def _is_full_image_crop_selection(self) -> bool:
        if self._original_image is None or self._crop_coords is None:
            return False
        height, width = self._original_image.shape[:2]
        x1, y1, x2, y2 = self._crop_coords
        return int(x1) <= 0 and int(y1) <= 0 and int(x2) >= width and int(y2) >= height

    def _move_crop_selection(self, dx: int, dy: int) -> bool:
        if self._original_image is None or self._crop_coords is None:
            return False

        x1, y1, x2, y2 = self._crop_coords
        height, width = self._original_image.shape[:2]
        crop_w = max(1, x2 - x1)
        crop_h = max(1, y2 - y1)

        max_x1 = max(0, width - crop_w)
        max_y1 = max(0, height - crop_h)
        new_x1 = max(0, min(max_x1, x1 + int(dx)))
        new_y1 = max(0, min(max_y1, y1 + int(dy)))
        new_coords = (new_x1, new_y1, new_x1 + crop_w, new_y1 + crop_h)

        if new_coords == self._crop_coords:
            return False

        self._set_crop_selection(new_coords, refresh_mask=False)
        return True

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

        self._configure_image_view_metrics(w, h)

        # 창 크기 설정
        win_w = self._canvas_width + (360 if self._is_video else 300)
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
            text=f"📁 {truncate_ui_text(filename, 58)}",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=COLORS["accent_text"],
        )
        self._filename_label.pack(pady=(15, 5))

        # 상단: 안내 문구 + 줌 컨트롤
        top_frame = ctk.CTkFrame(self, fg_color="transparent")
        top_frame.pack(fill="x", padx=20, pady=(5, 5))

        self._guide_label = ctk.CTkLabel(
            top_frame,
            text="좌클릭 드래그: 영역 선택  |  우클릭 드래그: 이동  |  휠: 확대/축소",
            font=ctk.CTkFont(size=11),
            text_color=COLORS["text_muted"],
        )
        self._guide_label.pack(side="left")
        self._crop_key_hint_label = ctk.CTkLabel(
            top_frame,
            text="선택 후 방향키=1px 이동, Shift+방향키=10px 이동",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=COLORS["accent_blue_text"],
        )
        self._crop_key_hint_label.pack(side="left", padx=(14, 0))

        # 줌 컨트롤
        zoom_frame = ctk.CTkFrame(top_frame, fg_color="transparent")
        zoom_frame.pack(side="right")

        ctk.CTkButton(
            zoom_frame,
            text="-",
            width=30,
            height=28,
            command=lambda: self._zoom(-0.1),
            fg_color=COLORS["bg_elevated"],
            hover_color=COLORS["bg_card_hover"],
            corner_radius=IOS_METRICS["control_radius_small"],
        ).pack(side="left", padx=2)

        self._zoom_label = ctk.CTkLabel(
            zoom_frame,
            text=f"{int(self._scale * 100)}%",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COLORS["accent_blue_text"],
            width=60,
        )
        self._zoom_label.pack(side="left", padx=5)

        ctk.CTkButton(
            zoom_frame,
            text="+",
            width=30,
            height=28,
            command=lambda: self._zoom(0.1),
            fg_color=COLORS["bg_elevated"],
            hover_color=COLORS["bg_card_hover"],
            corner_radius=IOS_METRICS["control_radius_small"],
        ).pack(side="left", padx=2)

        ctk.CTkButton(
            zoom_frame,
            text="맞춤",
            width=50,
            height=28,
            command=self._fit_to_canvas,
            fg_color=COLORS["bg_elevated"],
            hover_color=COLORS["bg_card_hover"],
            corner_radius=IOS_METRICS["pill_radius"],
        ).pack(side="left", padx=(10, 2))

        ctk.CTkButton(
            zoom_frame,
            text="100%",
            width=50,
            height=28,
            command=lambda: self._set_zoom(1.0),
            fg_color=COLORS["bg_elevated"],
            hover_color=COLORS["bg_card_hover"],
            corner_radius=IOS_METRICS["pill_radius"],
        ).pack(side="left", padx=2)

        mode_frame = ctk.CTkFrame(self, fg_color="transparent")
        mode_frame.pack(fill="x", padx=20, pady=(0, 5))

        self._select_mode_btn = ctk.CTkButton(
            mode_frame,
            text="영역 선택",
            width=90,
            height=30,
            command=lambda: self._set_edit_mode("select"),
            fg_color=COLORS["accent_blue"],
            hover_color=COLORS["hover_blue"],
            font=ctk.CTkFont(size=12, weight="bold"),
            corner_radius=IOS_METRICS["pill_radius"],
        )
        self._select_mode_btn.pack(side="left", padx=4)

        self._background_cutout_cb = ctk.CTkCheckBox(
            mode_frame,
            text="배경제거 이미지따기",
            variable=self._background_cutout_var,
            command=self._on_background_cutout_changed,
            font=ctk.CTkFont(size=12, weight="bold"),
            width=150,
            height=30,
            checkbox_width=18,
            checkbox_height=18,
        )
        self._background_cutout_cb.pack(side="left", padx=(8, 4))
        if self._is_video:
            self._background_cutout_var.set(False)
            self._background_cutout_cb.configure(
                text="배경제거(이미지 전용)",
                state="disabled",
                text_color=COLORS["text_muted"],
            )

        # 이미지 내비게이션 (여러 이미지가 있을 때만 표시)
        if len(self._image_list) > 1 and self._current_index >= 0:
            nav_frame = ctk.CTkFrame(self, fg_color="transparent")
            nav_frame.pack(fill="x", padx=20, pady=(5, 0))

            # 이전 버튼
            self._prev_btn = ctk.CTkButton(
                nav_frame,
                text="< 이전",
                width=80,
                height=30,
                command=lambda: self._navigate_image(-1),
                fg_color=COLORS["accent_blue"],
                hover_color=COLORS["hover_blue"],
                font=ctk.CTkFont(size=12, weight="bold"),
                corner_radius=IOS_METRICS["pill_radius"],
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
                text="다음 >",
                width=80,
                height=30,
                command=lambda: self._navigate_image(1),
                fg_color=COLORS["accent_blue"],
                hover_color=COLORS["hover_blue"],
                font=ctk.CTkFont(size=12, weight="bold"),
                corner_radius=IOS_METRICS["pill_radius"],
            )
            self._next_btn.pack(side="left", padx=5)

            # 키보드 안내
            ctk.CTkLabel(
                nav_frame,
                text="(좌/우 방향키로 이동)",
                font=ctk.CTkFont(size=11, weight="bold"),
                text_color=COLORS["text_primary"],
            ).pack(side="left", padx=10)

            # 버튼 상태 업데이트
            self._update_nav_buttons()

        # 하단 액션 카드: 파일명/주요 버튼/옵션을 분리해 긴 텍스트가 버튼을 밀지 않게 한다.
        bottom_panel = ctk.CTkFrame(
            self,
            fg_color=COLORS["bg_glass"],
            corner_radius=IOS_METRICS["card_radius_compact"],
            border_width=IOS_METRICS["card_border_width"],
            border_color=COLORS["border"],
        )
        bottom_panel.pack(side="bottom", fill="x", padx=20, pady=(4, 14))

        name_frame = ctk.CTkFrame(bottom_panel, fg_color="transparent")
        name_frame.pack(fill="x", padx=14, pady=(10, 4))
        name_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            name_frame,
            text="저장 전 이름",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COLORS["text_primary"],
            width=92,
            anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=(0, 10))

        self._crop_filename_entry = ctk.CTkEntry(
            name_frame,
            textvariable=self._crop_filename_var,
            placeholder_text=self._crop_filename_placeholder(),
            height=34,
            font=ctk.CTkFont(size=12, weight="bold"),
            state="normal" if self._crop_coords is not None else "disabled",
        )
        self._crop_filename_entry.grid(row=0, column=1, sticky="ew", padx=(0, 12))
        self._crop_filename_entry.bind("<Return>", lambda _event: self._save_crop())

        self._crop_filename_hint_label = ctk.CTkLabel(
            name_frame,
            text="크롭 후 입력하면 templates에 해당 이름으로 저장",
            font=ctk.CTkFont(size=10),
            text_color=COLORS["text_muted"],
            width=230,
            anchor="w",
        )
        self._crop_filename_hint_label.grid(row=0, column=2, sticky="w")
        self._update_crop_filename_state()

        btn_frame = ctk.CTkFrame(bottom_panel, fg_color="transparent")
        btn_frame.pack(fill="x", padx=14, pady=(4, 10))

        primary_btn_row = ctk.CTkFrame(btn_frame, fg_color="transparent")
        primary_btn_row.pack(anchor="center", pady=(0, 6))

        option_btn_row = ctk.CTkFrame(btn_frame, fg_color="transparent")
        option_btn_row.pack(anchor="center")

        self._save_btn = ctk.CTkButton(
            primary_btn_row,
            text="영상 크롭 저장" if self._is_video else "크롭 저장",
            command=self._save_crop,
            width=120 if self._is_video else 100,
            height=40,
            state="disabled" if self._crop_coords is None else "normal",
            fg_color=COLORS["success"],
            hover_color=COLORS["green_hover"],
            text_color=COLORS["text_on_accent"],
            font=ctk.CTkFont(size=13, weight="bold"),
            corner_radius=IOS_METRICS["pill_radius"],
        )
        self._save_btn.pack(side="left", padx=5)

        # 이미지 변경 버튼
        ctk.CTkButton(
            primary_btn_row,
            text="이미지 변경",
            command=self._change_image,
            width=100,
            height=40,
            fg_color=COLORS["accent_blue"],
            hover_color=COLORS["hover_blue"],
            text_color=COLORS["text_on_accent"],
            font=ctk.CTkFont(size=13, weight="bold"),
            corner_radius=IOS_METRICS["pill_radius"],
        ).pack(side="left", padx=5)

        # 이미지 삭제 버튼
        ctk.CTkButton(
            primary_btn_row,
            text="이미지 삭제",
            command=self._delete_image,
            width=100,
            height=40,
            fg_color=COLORS["error"],
            hover_color=COLORS["danger_hover"],
            text_color=COLORS["text_on_accent"],
            font=ctk.CTkFont(size=13, weight="bold"),
            corner_radius=IOS_METRICS["pill_radius"],
        ).pack(side="left", padx=5)

        ctk.CTkButton(
            primary_btn_row,
            text="취소",
            command=self.destroy,
            width=80,
            height=40,
            fg_color=COLORS["bg_elevated"],
            hover_color=COLORS["bg_card_hover"],
            text_color=COLORS["text_secondary"],
            font=ctk.CTkFont(size=13, weight="bold"),
            corner_radius=IOS_METRICS["pill_radius"],
        ).pack(side="left", padx=5)

        # 멀티이미지 버튼 (rule이 있을 때만)
        if self._rule is not None and hasattr(self._rule, 'target_images'):
            alt_count = len(getattr(self._rule, 'target_images', []) or [])
            alt_text = f"멀티이미지 ({alt_count})" if alt_count > 0 else "멀티이미지 추가"
            self._alt_image_btn = ctk.CTkButton(
                option_btn_row,
                text=alt_text,
                command=self._manage_alt_images,
                width=120,
                height=40,
                fg_color=COLORS["accent_orange"] if alt_count > 0 else COLORS["bg_card"],
                hover_color=COLORS["confidence_amber_hover"] if alt_count > 0 else COLORS["bg_card_hover"],
                text_color=COLORS["text_on_accent"] if alt_count > 0 else COLORS["text_secondary"],
                font=ctk.CTkFont(size=13, weight="bold"),
                corner_radius=IOS_METRICS["pill_radius"],
            )
            self._alt_image_btn.pack(side="left", padx=5)

        # 검색 범위 설정 버튼 (rule이 있을 때만)
        if self._rule is not None:
            radius_text, has_region = self._search_button_state()
            self._search_radius_btn = ctk.CTkButton(
                option_btn_row,
                text=radius_text,
                command=self._show_search_region_options,
                width=130,
                height=40,
                fg_color=COLORS["search_radius_purple"] if has_region else COLORS["bg_card"],
                hover_color=COLORS["search_radius_purple_hover"] if has_region else COLORS["bg_card_hover"],
                text_color=COLORS["text_on_accent"] if has_region else COLORS["text_secondary"],
                font=ctk.CTkFont(size=13, weight="bold"),
                corner_radius=IOS_METRICS["pill_radius"],
            )
            self._search_radius_btn.pack(side="left", padx=5)

            # 인식률 설정 버튼
            conf_value = getattr(self._rule, 'confidence', 0.65) or 0.65
            conf_pct = int(conf_value * 100)
            has_extra_verify = bool(
                getattr(self._rule, "verify_image_color", False)
                or getattr(self._rule, "verify_image_brightness", False)
            )
            self._confidence_btn = ctk.CTkButton(
                option_btn_row,
                text=f"인식률: {conf_pct}%",
                command=self._set_confidence,
                width=100,
                height=40,
                fg_color=COLORS["confidence_amber"] if conf_pct != 65 or has_extra_verify else COLORS["bg_card"],
                hover_color=COLORS["confidence_amber_hover"] if conf_pct != 65 or has_extra_verify else COLORS["bg_card_hover"],
                text_color=COLORS["text_on_accent"] if conf_pct != 65 or has_extra_verify else COLORS["text_secondary"],
                font=ctk.CTkFont(size=13, weight="bold"),
                corner_radius=IOS_METRICS["pill_radius"],
            )
            self._confidence_btn.pack(side="left", padx=5)

            # 마우스 이동 체크박스
            self._move_mouse_var = ctk.BooleanVar(value=getattr(self._rule, 'move_mouse_before_search', False))
            self._move_mouse_cb = ctk.CTkCheckBox(
                option_btn_row,
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

        # 메인 컨텐츠 (이미지 + 미리보기)
        content_frame = ctk.CTkFrame(self, fg_color="transparent")
        content_frame.pack(padx=20, pady=10, fill="both", expand=True)

        # 왼쪽: 원본 이미지
        left_frame = ctk.CTkFrame(
            content_frame,
            fg_color=COLORS["bg_glass"],
            corner_radius=IOS_METRICS["card_radius_compact"],
            border_width=IOS_METRICS["card_border_width"],
            border_color=COLORS["border"],
        )
        left_frame.pack(side="left", fill="both", expand=True)

        # 이미지 정보
        self._info_label = ctk.CTkLabel(
            left_frame,
            text=self._format_image_info_text(),
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COLORS["text_primary"],
        )
        self._info_label.pack(pady=(8, 5))

        # 캔버스 (tkinter Canvas 사용)
        self._canvas = Canvas(
            left_frame,
            width=self._canvas_width,
            height=self._canvas_height,
            bg=COLORS["bg_log"],
            highlightthickness=IOS_METRICS["canvas_border_width"],
            highlightbackground=COLORS["image_canvas_border"],
            takefocus=1,
        )
        self._canvas.pack(padx=10, pady=(0, 10))

        # 이미지 표시
        self._update_canvas_image()

        # 마우스 이벤트 바인딩
        self._canvas.bind("<ButtonPress-1>", self._on_mouse_down)
        self._canvas.bind("<B1-Motion>", self._on_mouse_drag)
        self._canvas.bind("<ButtonRelease-1>", self._on_mouse_up)
        self._canvas.bind("<Left>", self._on_crop_arrow_key)
        self._canvas.bind("<Right>", self._on_crop_arrow_key)
        self._canvas.bind("<Up>", self._on_crop_arrow_key)
        self._canvas.bind("<Down>", self._on_crop_arrow_key)
        self._canvas.bind("<Shift-Left>", self._on_crop_arrow_key)
        self._canvas.bind("<Shift-Right>", self._on_crop_arrow_key)
        self._canvas.bind("<Shift-Up>", self._on_crop_arrow_key)
        self._canvas.bind("<Shift-Down>", self._on_crop_arrow_key)
        self._canvas.bind("<ButtonPress-3>", self._on_pan_start)
        self._canvas.bind("<B3-Motion>", self._on_pan_drag)
        self._canvas.bind("<ButtonRelease-3>", self._on_pan_end)
        self._canvas.bind("<MouseWheel>", self._on_mouse_wheel)

        # 오른쪽: 미리보기 영역
        right_frame = ctk.CTkFrame(
            content_frame,
            fg_color=COLORS["bg_glass"],
            corner_radius=IOS_METRICS["card_radius_compact"],
            border_width=IOS_METRICS["card_border_width"],
            border_color=COLORS["border"],
            width=230 if self._is_video else 200,
        )
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
            bg=COLORS["bg_log"],
            highlightthickness=IOS_METRICS["canvas_border_width"],
            highlightbackground=COLORS["image_canvas_border"],
        )
        self._preview_canvas.pack(padx=10, pady=5)

        self._preview_label = ctk.CTkLabel(
            right_frame,
            text="영역을 선택하세요",
            font=ctk.CTkFont(size=10),
            text_color=COLORS["text_muted"],
        )
        self._preview_label.pack(pady=5)

        if self._is_video:
            video_control_frame = ctk.CTkFrame(right_frame, fg_color="transparent")
            video_control_frame.pack(fill="x", padx=14, pady=(4, 6))
            self._video_play_btn = ctk.CTkButton(
                video_control_frame,
                text="재생",
                width=190,
                height=34,
                command=self._resume_video_preview,
                fg_color=COLORS["accent_blue"],
                hover_color=COLORS["hover_blue"],
                text_color=COLORS["text_on_accent"],
                border_width=IOS_METRICS["card_border_width"],
                border_color=COLORS["button_border"],
                font=ctk.CTkFont(size=13, weight="bold"),
                corner_radius=IOS_METRICS["pill_radius"],
            )
            self._video_play_btn.pack(fill="x", pady=(0, 6))
            self._video_stop_btn = ctk.CTkButton(
                video_control_frame,
                text="중지",
                width=190,
                height=34,
                command=self._stop_video_from_button,
                fg_color=COLORS["danger"],
                hover_color=COLORS["danger_hover"],
                text_color=COLORS["text_on_accent"],
                border_width=IOS_METRICS["card_border_width"],
                border_color=COLORS["button_border"],
                font=ctk.CTkFont(size=13, weight="bold"),
                corner_radius=IOS_METRICS["pill_radius"],
            )
            self._video_stop_btn.pack(fill="x", pady=(0, 6))
            ctk.CTkButton(
                video_control_frame,
                text="처음으로",
                width=190,
                height=34,
                command=self._restart_video_preview,
                fg_color=COLORS["confidence_amber"],
                hover_color=COLORS["confidence_amber_hover"],
                text_color=COLORS["text_on_accent"],
                border_width=IOS_METRICS["card_border_width"],
                border_color=COLORS["button_border"],
                font=ctk.CTkFont(size=13, weight="bold"),
                corner_radius=IOS_METRICS["pill_radius"],
            ).pack(fill="x", pady=(0, 8))
            self._video_time_label = ctk.CTkLabel(
                video_control_frame,
                text=self._format_video_time_text(),
                font=ctk.CTkFont(size=11, weight="bold"),
                text_color=COLORS["accent_blue_text"],
            )
            self._video_time_label.pack(pady=(0, 4))
            self._video_progress_canvas = Canvas(
                video_control_frame,
                width=190,
                height=14,
                bg=COLORS["bg_card"],
                highlightthickness=0,
            )
            self._video_progress_canvas.pack(fill="x", pady=(0, 2))
            self._update_video_progress_bar()

        self._set_edit_mode("select")
        if self._crop_coords is not None:
            self._refresh_preview()
            self._save_btn.configure(state="normal")
        self.after(100, self._focus_crop_canvas)

    def _update_canvas_image(self):
        """현재 스케일로 캔버스에 이미지 표시"""
        if self._original_image is None:
            return

        h, w = self._original_image.shape[:2]
        display_w = max(1, int(w * self._scale))
        display_h = max(1, int(h * self._scale))

        # 스케일된 이미지 생성
        self._display_image = cv2.resize(self._original_image, (display_w, display_h))
        display_image = self._display_image.copy()

        pil_image = Image.fromarray(display_image)
        self._photo_image = ImageTk.PhotoImage(pil_image)

        # 캔버스 초기화 및 이미지 표시
        self._canvas.delete("all")

        # 이미지를 캔버스 중앙에 배치 (오프셋 적용)
        img_x = (self._canvas_width - display_w) // 2 + self._canvas_offset_x
        img_y = (self._canvas_height - display_h) // 2 + self._canvas_offset_y

        self._img_canvas_x = img_x
        self._img_canvas_y = img_y
        self._canvas.create_image(img_x, img_y, anchor="nw", image=self._photo_image, tags="image")

        if self._crop_coords is not None:
            crop_x1, crop_y1, crop_x2, crop_y2 = self._crop_coords
            rect_x1 = img_x + int(crop_x1 * self._scale)
            rect_y1 = img_y + int(crop_y1 * self._scale)
            rect_x2 = img_x + int(crop_x2 * self._scale)
            rect_y2 = img_y + int(crop_y2 * self._scale)
            self._rect_id = self._canvas.create_rectangle(
                rect_x1,
                rect_y1,
                rect_x2,
                rect_y2,
                outline=COLORS["info"],
                width=2,
                dash=(6, 4),
            )

        # 줌 레이블 업데이트
        if hasattr(self, '_zoom_label'):
            self._zoom_label.configure(text=f"{int(self._scale * 100)}%")
        self._update_image_info_label()
        self._draw_video_overlay_play_button()

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
        self._scale = self._fit_canvas_scale(w, h, cap=self._max_scale)
        self._canvas_offset_x = 0
        self._canvas_offset_y = 0
        self._update_canvas_image()

    def _set_edit_mode(self, mode: str):
        self._edit_mode = "select"
        if hasattr(self, "_select_mode_btn"):
            self._select_mode_btn.configure(fg_color=COLORS["accent_blue"], text_color=COLORS["text_on_accent"])
        if hasattr(self, "_guide_label"):
            self._guide_label.configure(text="좌클릭 드래그: 영역 선택  |  우클릭 드래그: 이동  |  휠: 확대/축소")

    def _background_cutout_enabled(self) -> bool:
        try:
            return bool(self._background_cutout_var.get())
        except Exception:
            return False

    def _on_background_cutout_changed(self):
        if self._crop_coords is not None:
            if self._background_cutout_enabled():
                self._crop_mask_needs_refresh = True
            self._ensure_current_crop_mask(refresh_view=False)
            self._refresh_preview()

    @staticmethod
    def _compose_cutout_preview_rgb(image_rgb: np.ndarray, mask: np.ndarray) -> np.ndarray:
        preview = image_rgb.copy()
        if preview.size == 0:
            return preview
        normalized = normalize_binary_mask(mask, preview.shape[:2])
        height, width = preview.shape[:2]
        tile = 8
        yy, xx = np.indices((height, width))
        checker = ((yy // tile + xx // tile) % 2).astype(bool)
        checker_bg = np.where(checker[..., None], 232, 172).astype(np.uint8)
        preview[normalized == 0] = checker_bg[normalized == 0]
        return preview

    def _canvas_to_original_point(self, canvas_x: int, canvas_y: int) -> Optional[tuple[int, int]]:
        if self._original_image is None:
            return None
        img_x = getattr(self, "_img_canvas_x", 0)
        img_y = getattr(self, "_img_canvas_y", 0)
        rel_x = canvas_x - img_x
        rel_y = canvas_y - img_y
        if rel_x < 0 or rel_y < 0:
            return None
        orig_x = int(rel_x / self._scale)
        orig_y = int(rel_y / self._scale)
        h, w = self._original_image.shape[:2]
        if orig_x < 0 or orig_y < 0 or orig_x >= w or orig_y >= h:
            return None
        return orig_x, orig_y

    def _canvas_rect_to_crop_coords(self, end_x: int, end_y: int) -> Optional[tuple[int, int, int, int]]:
        if self._original_image is None:
            return None

        x1 = min(self._start_x, end_x)
        y1 = min(self._start_y, end_y)
        x2 = max(self._start_x, end_x)
        y2 = max(self._start_y, end_y)
        if x2 - x1 < 5 or y2 - y1 < 5:
            return None

        p1 = self._canvas_to_original_point(x1, y1)
        p2 = self._canvas_to_original_point(x2, y2)
        if p1 is None and p2 is None:
            return None

        h, w = self._original_image.shape[:2]
        if p1 is None:
            p1 = (0, 0)
        if p2 is None:
            p2 = (w - 1, h - 1)

        orig_x1, orig_y1 = p1
        orig_x2, orig_y2 = p2
        orig_x1 = max(0, min(orig_x1, w - 1))
        orig_y1 = max(0, min(orig_y1, h - 1))
        orig_x2 = max(0, min(orig_x2, w - 1))
        orig_y2 = max(0, min(orig_y2, h - 1))
        if orig_x1 > orig_x2:
            orig_x1, orig_x2 = orig_x2, orig_x1
        if orig_y1 > orig_y2:
            orig_y1, orig_y2 = orig_y2, orig_y1
        orig_x2 = min(w, orig_x2 + 1)
        orig_y2 = min(h, orig_y2 + 1)
        if orig_x2 - orig_x1 < 2 or orig_y2 - orig_y1 < 2:
            return None
        return orig_x1, orig_y1, orig_x2, orig_y2

    def _set_crop_selection(self, coords: tuple[int, int, int, int], *, refresh_mask: bool = True):
        if self._original_image is None:
            return
        self._crop_coords = coords
        x1, y1, x2, y2 = coords
        cropped = self._original_image[y1:y2, x1:x2].copy()
        if refresh_mask:
            self._crop_mask = auto_extract_foreground_mask(cropped)
            self._crop_mask_needs_refresh = False
        else:
            self._crop_mask = normalize_binary_mask(self._crop_mask, cropped.shape[:2])
            self._crop_mask_needs_refresh = True
        self._save_btn.configure(state="normal")
        self._update_crop_filename_state()
        self._refresh_preview()
        self._update_canvas_image()

    def _clear_crop_selection(self):
        """Clear the current crop rectangle without changing the loaded media."""
        self._crop_coords = None
        self._crop_mask = None
        self._crop_mask_needs_refresh = False
        self._selecting = False
        if self._rect_id:
            try:
                self._canvas.delete(self._rect_id)
            except tk.TclError:
                pass
            self._rect_id = None
        if hasattr(self, "_save_btn"):
            self._save_btn.configure(state="disabled")
        if hasattr(self, "_crop_filename_var"):
            self._crop_filename_var.set("")
        self._update_crop_filename_state()
        if hasattr(self, "_preview_canvas"):
            self._preview_canvas.delete("all")
        if hasattr(self, "_preview_label"):
            self._preview_label.configure(text="영역을 선택하세요", text_color=COLORS["text_muted"])
        self._update_canvas_image()

    def _crop_filename_placeholder(self) -> str:
        stem = Path(self._image_path).stem or "template"
        if self._is_video:
            return f"비우면 자동 저장: {stem}_crop_랜덤.mp4"
        return f"비우면 자동 저장: {stem}_crop_랜덤"

    def _update_crop_filename_state(self):
        ready = self._crop_coords is not None
        if self._crop_filename_entry is not None:
            self._crop_filename_entry.configure(
                state="normal" if ready else "disabled",
                placeholder_text=self._crop_filename_placeholder(),
                border_color=COLORS["success"] if ready else COLORS["border"],
            )
        if self._crop_filename_hint_label is not None:
            self._crop_filename_hint_label.configure(
                text="이름 입력 후 저장하면 즉시 해당 파일명으로 저장"
                if ready
                else "크롭 영역을 먼저 선택하세요",
                text_color=COLORS["success_text"] if ready else COLORS["text_muted"],
            )

    def _reset_crop_filename(self):
        self._crop_filename_var.set("")
        self._update_crop_filename_state()

    def _ensure_current_crop_mask(self, *, refresh_view: bool = False):
        if self._original_image is None or self._crop_coords is None:
            return
        if self._crop_mask is not None and not self._crop_mask_needs_refresh:
            return
        x1, y1, x2, y2 = self._crop_coords
        cropped = self._original_image[y1:y2, x1:x2].copy()
        if cropped.size == 0:
            return
        self._crop_mask = auto_extract_foreground_mask(cropped)
        self._crop_mask_needs_refresh = False
        if refresh_view:
            self._refresh_preview()
            self._update_canvas_image()

    def _refresh_preview(self):
        if self._original_image is None or self._crop_coords is None:
            return
        x1, y1, x2, y2 = self._crop_coords
        cropped = self._original_image[y1:y2, x1:x2].copy()
        crop_mask = normalize_binary_mask(self._crop_mask, cropped.shape[:2])
        if cropped.size == 0 or crop_mask.size == 0:
            return
        preview_source = self._compose_cutout_preview_rgb(cropped, crop_mask) if self._background_cutout_enabled() else cropped
        preview_resized, _ = fit_image_to_box(preview_source, 180, 180)
        pil_preview = Image.fromarray(preview_resized)
        self._cropped_photo = ImageTk.PhotoImage(pil_preview)
        self._preview_canvas.delete("all")
        offset_x = (180 - preview_resized.shape[1]) // 2
        offset_y = (180 - preview_resized.shape[0]) // 2
        self._preview_canvas.create_image(offset_x, offset_y, anchor="nw", image=self._cropped_photo)
        self._preview_label.configure(
            text=f"{cropped.shape[1]} x {cropped.shape[0]} px",
            text_color=COLORS["text_primary"],
        )

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
        try:
            self._canvas.focus_set()
        except Exception:
            pass
        self._mouse_down_had_crop = self._crop_coords is not None
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
            outline=COLORS["info"], width=2
        )

        # 실시간 미리보기 업데이트
        self._update_preview(event.x, event.y)

    def _on_mouse_up(self, event):
        """마우스 버튼 놓음"""
        self._selecting = False
        self._end_x = event.x
        self._end_y = event.y
        if self._mouse_down_had_crop and abs(event.x - self._start_x) < 5 and abs(event.y - self._start_y) < 5:
            self._clear_crop_selection()
            return
        self._update_preview(event.x, event.y)

    def _update_preview(self, end_x: int, end_y: int):
        """미리보기 업데이트"""
        coords = self._canvas_rect_to_crop_coords(end_x, end_y)
        if coords is None:
            return
        self._set_crop_selection(coords)

    def _save_crop(self):
        """크롭 저장 - 원본 유지하고 새 파일 + 자유형 마스크를 함께 저장"""
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

        if self._is_video:
            self._save_video_crop()
            return

        orig_x1, orig_y1, orig_x2, orig_y2 = self._crop_coords
        crop_w = orig_x2 - orig_x1
        crop_h = orig_y2 - orig_y1
        orig_h, orig_w = self._original_image.shape[:2]

        logger.info(f"[크롭] 원본 크기: {orig_w}x{orig_h}")
        logger.info(f"[크롭] 크롭 좌표: ({orig_x1}, {orig_y1}) ~ ({orig_x2}, {orig_y2})")
        logger.info(f"[크롭] 크롭 크기: {crop_w}x{crop_h}")

        # 크롭 + 현재 자유형 마스크 추출
        x1, y1, x2, y2 = self._crop_coords
        cropped = self._original_image[y1:y2, x1:x2].copy()
        cutout_enabled = self._background_cutout_enabled()
        if cutout_enabled:
            self._crop_mask = auto_extract_foreground_mask(cropped)
            self._crop_mask_needs_refresh = False
        self._ensure_current_crop_mask()
        crop_mask = normalize_binary_mask(self._crop_mask, cropped.shape[:2])

        if cropped.size == 0 or crop_mask.size == 0:
            messagebox.showwarning("알림", "크롭된 영역이 비어있습니다.\n다시 선택하세요.")
            return

        try:
            # BGR로 변환
            cropped_bgr = cv2.cvtColor(cropped, cv2.COLOR_RGB2BGR)

            # 새 파일명 생성 (원본 유지)
            original_path = Path(self._image_path)
            stem = original_path.stem
            suffix = ".png" if cutout_enabled else (original_path.suffix or ".png")
            custom_filename = sanitize_template_filename(self._crop_filename_var.get(), suffix)
            if self._crop_filename_var.get().strip() and custom_filename is None:
                messagebox.showwarning("알림", "저장 이름으로 사용할 수 없는 파일명입니다.")
                return

            if custom_filename:
                new_path = unique_template_path(DATA_DIR / "templates", custom_filename)
                logger.info(f"[크롭] 사용자 지정 파일명 적용: {new_path.name}")
            else:
                parent = original_path.parent
                new_filename = f"{stem}_crop_{uuid.uuid4().hex[:6]}{suffix}"
                new_path = unique_template_path(parent, new_filename)

            mask_path = get_sidecar_mask_path(new_path)

            # 새 파일과 자유형 마스크를 함께 저장
            if cutout_enabled:
                cropped_bgra = cv2.cvtColor(cropped, cv2.COLOR_RGB2BGRA)
                cropped_bgra[:, :, 3] = crop_mask
                success = write_image_file(new_path, cropped_bgra)
            else:
                success = write_image_file(new_path, cropped_bgr)
            mask_success = write_image_file(mask_path, crop_mask)

            if success and mask_success:
                new_size = os.path.getsize(str(new_path)) if new_path.exists() else 0
                logger.info(f"[크롭] 새 파일 저장: {new_path}")
                logger.info(f"[크롭] 마스크 저장: {mask_path}")
                if cutout_enabled:
                    logger.info("[크롭] 배경제거 이미지따기 저장 적용")
                logger.info(f"[크롭] 원본 후처리 대기: {self._image_path}")
                logger.info(f"[크롭] 크롭 크기: {crop_w}x{crop_h}, 파일크기: {new_size} bytes")

                old_path = self._set_current_rule_image(str(new_path))
                if old_path:
                    try:
                        invalidate_thumbnail_cache(old_path)
                    except Exception:
                        pass
                try:
                    invalidate_thumbnail_cache(str(new_path))
                except Exception:
                    pass
                self._invoke_image_callback(self._on_crop, str(new_path), self._rule, old_path)
                self.destroy()
            else:
                logger.error(f"[크롭] 저장 실패: image={new_path}, mask={mask_path}, image_ok={success}, mask_ok={mask_success}")
                messagebox.showerror("오류", f"이미지 또는 마스크 저장에 실패했습니다.\n경로: {new_path}")

        except Exception as e:
            logger.error(f"크롭 저장 오류: {e}")
            messagebox.showerror("오류", f"저장 중 오류가 발생했습니다.\n{e}")

    def _save_video_crop(self):
        """Save a cropped copy of the current video using the selected rectangle."""
        from tkinter import messagebox
        import uuid
        import shutil

        if self._crop_coords is None or self._original_image is None:
            messagebox.showwarning("알림", "먼저 영상에서 크롭할 영역을 선택하세요.")
            return

        x1, y1, x2, y2 = [int(v) for v in self._crop_coords]
        crop_w = max(1, x2 - x1)
        crop_h = max(1, y2 - y1)
        if crop_w < 2 or crop_h < 2:
            messagebox.showwarning("알림", "크롭 영역이 너무 작습니다.")
            return

        original_path = Path(self._image_path)
        custom_filename = sanitize_template_media_filename(self._crop_filename_var.get(), ".mp4")
        if self._crop_filename_var.get().strip() and custom_filename is None:
            messagebox.showwarning("알림", "저장 이름으로 사용할 수 없는 파일명입니다.")
            return

        if custom_filename:
            new_path = unique_template_path(DATA_DIR / "templates", custom_filename)
        else:
            new_filename = f"{original_path.stem}_crop_{uuid.uuid4().hex[:6]}.mp4"
            new_path = unique_template_path(DATA_DIR / "templates", new_filename)

        tmp_path = new_path.with_name(f"_tmp_video_crop_{uuid.uuid4().hex[:10]}.mp4")
        cap = None
        writer = None
        frame_count = 0
        saved_successfully = False
        try:
            self._stop_video_preview()
            self.configure(cursor="watch")
            self.update_idletasks()

            cap = cv2.VideoCapture(str(original_path))
            if not cap.isOpened():
                raise RuntimeError(f"동영상을 열 수 없습니다: {original_path}")

            fps = float(cap.get(cv2.CAP_PROP_FPS) or self._video_fps or 15.0)
            if fps <= 0:
                fps = 15.0
            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
            writer = cv2.VideoWriter(str(tmp_path), fourcc, fps, (crop_w, crop_h))
            if not writer.isOpened():
                raise RuntimeError(f"동영상 저장 파일을 열 수 없습니다: {tmp_path}")

            while True:
                ok, frame = cap.read()
                if not ok or frame is None:
                    break
                h, w = frame.shape[:2]
                sx1 = max(0, min(w - 1, x1))
                sy1 = max(0, min(h - 1, y1))
                sx2 = max(sx1 + 1, min(w, x2))
                sy2 = max(sy1 + 1, min(h, y2))
                cropped = frame[sy1:sy2, sx1:sx2]
                if cropped.shape[1] != crop_w or cropped.shape[0] != crop_h:
                    cropped = cv2.resize(cropped, (crop_w, crop_h), interpolation=cv2.INTER_AREA)
                writer.write(cropped)
                frame_count += 1

            if frame_count <= 0:
                raise RuntimeError("저장할 영상 프레임이 없습니다")

            if writer is not None:
                writer.release()
                writer = None
            if cap is not None:
                cap.release()
                cap = None

            if new_path.exists():
                new_path.unlink()
            try:
                tmp_path.replace(new_path)
            except OSError:
                shutil.move(str(tmp_path), str(new_path))

            logger.info(
                f"[동영상 크롭] 저장 완료: {new_path} "
                f"crop=({x1},{y1})~({x2},{y2}) size={crop_w}x{crop_h} frames={frame_count}"
            )

            old_path = self._set_current_rule_image(str(new_path))
            if hasattr(self._rule, "save"):
                self._rule.save()
            if old_path:
                try:
                    invalidate_thumbnail_cache(old_path)
                except Exception:
                    pass
            invalidate_thumbnail_cache(str(new_path))
            self._invoke_image_callback(self._on_crop, str(new_path), self._rule, old_path)
            saved_successfully = True
            self.destroy()
        except Exception as exc:
            logger.error(f"동영상 크롭 저장 실패: {exc}", exc_info=True)
            messagebox.showerror("오류", f"동영상 크롭 저장 실패:\n{exc}")
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except OSError:
                pass
        finally:
            if writer is not None:
                try:
                    writer.release()
                except Exception:
                    pass
            if cap is not None:
                try:
                    cap.release()
                except Exception:
                    pass
            try:
                self.configure(cursor="")
            except tk.TclError:
                pass
            if not saved_successfully and self._is_video:
                self._video_playing = False
                self._update_video_control_state()

    def _delete_image(self):
        """이미지 삭제"""
        from tkinter import messagebox

        if messagebox.askyesno("이미지 삭제", "이미지를 삭제하시겠습니까?\n원본 파일도 함께 삭제됩니다."):
            try:
                # 파일 삭제
                image_path = Path(self._image_path)
                mask_path = get_sidecar_mask_path(image_path)
                if image_path.exists():
                    image_path.unlink()
                    logger.info(f"이미지 파일 삭제: {self._image_path}")
                if mask_path.exists():
                    mask_path.unlink()
                    logger.info(f"마스크 파일 삭제: {mask_path}")
                settings_path = get_template_media_settings_path(image_path)
                if settings_path.exists():
                    settings_path.unlink()
                    logger.info(f"템플릿 미디어 설정 삭제: {settings_path}")

                old_path = self._set_current_rule_image(None)
                if old_path:
                    try:
                        invalidate_thumbnail_cache(old_path)
                    except Exception:
                        pass

                # 콜백 호출
                self._invoke_image_callback(self._on_delete, self._rule, old_path)

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

        media_filetypes = [
            ("모든 파일", "*.*"),
            ("이미지/동영상 파일", "*.*"),
            ("이미지 파일", IMAGE_FILE_PATTERNS),
            ("동영상 파일", VIDEO_FILE_PATTERNS),
        ]

        new_path = filedialog.askopenfilename(
            title="이미지/동영상 선택",
            initialdir=str(initial_dir),
            filetypes=media_filetypes,
        )

        if not new_path:
            return

        try:
            new_path = Path(new_path)
            if not is_supported_media_path(new_path):
                from tkinter import messagebox
                messagebox.showwarning("알림", "지원하지 않는 파일 형식입니다.")
                return
            if not (isinstance(self._rule, TemplateMediaSettings) or self._is_video) and is_video_media_path(new_path):
                from tkinter import messagebox
                messagebox.showwarning("알림", "액션 이미지는 동영상 파일을 대상으로 사용할 수 없습니다.")
                return

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

            old_path = self._set_current_rule_image(str(dest_path))
            self._reset_crop_filename()
            if old_path:
                try:
                    invalidate_thumbnail_cache(old_path)
                except Exception:
                    pass
            try:
                invalidate_thumbnail_cache(str(dest_path))
            except Exception:
                pass

            # 콜백 호출 (새 경로 전달)
            self._invoke_image_callback(self._on_change, str(dest_path), self._rule, old_path)

            self.destroy()
        except Exception as e:
            from tkinter import messagebox
            logger.error(f"이미지 변경 실패: {e}")
            messagebox.showerror("오류", f"이미지 변경 실패: {e}")

    def _invoke_image_callback(self, callback, *args):
        """Call image callbacks with the richest supported signature."""
        if callback is None:
            return
        try:
            import inspect
            sig = inspect.signature(callback)
            accepts_varargs = any(p.kind == p.VAR_POSITIONAL for p in sig.parameters.values())
            if accepts_varargs:
                callback(*args)
                return
            positional = [
                p for p in sig.parameters.values()
                if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
            ]
            callback(*args[:len(positional)])
        except (TypeError, ValueError):
            # Fallback for callables without inspectable signatures.
            for count in range(len(args), -1, -1):
                try:
                    callback(*args[:count])
                    return
                except TypeError:
                    continue
            raise

    def _set_current_rule_image(self, image_path: Optional[str]) -> Optional[str]:
        old_path = self._image_path
        if self._rule is not None and hasattr(self._rule, 'target_image'):
            old_path = getattr(self._rule, 'target_image', old_path)
            self._rule.target_image = image_path
            if hasattr(self._rule, "save"):
                self._rule.save()
        self._image_path = image_path or ""
        return old_path

    def _rule_search_center(self):
        if self._rule is None:
            return None, None
        if hasattr(self._rule, 'action_x') or hasattr(self._rule, 'action_y'):
            return getattr(self._rule, 'action_x', None), getattr(self._rule, 'action_y', None)
        return getattr(self._rule, 'x', None), getattr(self._rule, 'y', None)

    def _set_rule_search_center(self, x: int, y: int):
        if self._rule is None:
            return
        if hasattr(self._rule, 'action_x') or hasattr(self._rule, 'action_y'):
            self._rule.action_x = x
            self._rule.action_y = y
        elif hasattr(self._rule, 'x') or hasattr(self._rule, 'y'):
            self._rule.x = x
            self._rule.y = y

    @staticmethod
    def _normalize_search_region_value(region):
        if not isinstance(region, (list, tuple)) or len(region) != 4:
            return None
        try:
            x1, y1, x2, y2 = [int(v) for v in region]
        except (TypeError, ValueError):
            return None
        left, right = sorted((x1, x2))
        top, bottom = sorted((y1, y2))
        if right <= left or bottom <= top:
            return None
        return [left, top, right, bottom]

    @staticmethod
    def _same_search_region(a, b) -> bool:
        return ImageCropDialog._normalize_search_region_value(a) == ImageCropDialog._normalize_search_region_value(b)

    def _current_search_region(self):
        if self._rule is None:
            return None
        search_region = self._normalize_search_region_value(getattr(self._rule, 'search_region', None))
        if search_region:
            return search_region
        search_radius = getattr(self._rule, 'search_radius', 0) or 0
        if search_radius <= 0:
            return None
        cx, cy = self._rule_search_center()
        if cx is None or cy is None:
            return None
        try:
            r = int(search_radius)
            return self._normalize_search_region_value([int(cx) - r, int(cy) - r, int(cx) + r, int(cy) + r])
        except (TypeError, ValueError):
            return None

    def _saved_search_region(self, slot: str):
        key = f"image_search_region_{slot}"
        try:
            return self._normalize_search_region_value(getattr(get_config().player, key, None))
        except Exception:
            return None

    def _save_search_region_preset(self, slot: str, region) -> bool:
        normalized = self._normalize_search_region_value(region)
        if normalized is None:
            return False
        key = f"image_search_region_{slot}"
        try:
            setattr(get_config().player, key, normalized)
            if not save_config():
                logger.warning(f"검색범위 {slot.upper()}영역 저장 실패")
                return False
            return True
        except Exception as exc:
            logger.warning(f"검색범위 {slot.upper()}영역 저장 오류: {exc}")
            return False

    def _search_region_label(self, region) -> str:
        normalized = self._normalize_search_region_value(region)
        if not normalized:
            return "미설정"
        x1, y1, x2, y2 = normalized
        return f"({x1}, {y1}) ~ ({x2}, {y2})  {x2 - x1}x{y2 - y1}"

    def _search_region_source_name(self, region) -> str:
        normalized = self._normalize_search_region_value(region)
        if not normalized:
            return "전체"
        if self._same_search_region(normalized, self._saved_search_region("a")):
            return "A영역"
        if self._same_search_region(normalized, self._saved_search_region("b")):
            return "B영역"
        return "자유영역"

    def _apply_search_region_to_rule(self, region, source_label: str = "검색범위") -> bool:
        normalized = self._normalize_search_region_value(region)
        if self._rule is None or normalized is None:
            return False
        x1, y1, x2, y2 = normalized
        self._rule.search_region = normalized

        # 기존 실행부/구버전 데이터와의 호환을 위해 중심점과 반경도 같이 갱신한다.
        center_x = (x1 + x2) // 2
        center_y = (y1 + y2) // 2
        self._set_rule_search_center(center_x, center_y)
        self._rule.search_radius = max(x2 - x1, y2 - y1) // 2

        logger.info(f"{source_label} 적용: ({x1}, {y1}) ~ ({x2}, {y2})")
        if hasattr(self._rule, "save"):
            self._rule.save()
        self._refresh_rule_setting_controls()
        self._invoke_image_callback(self._on_search_radius_change, self._rule)
        return True

    def _search_button_state(self):
        search_region = getattr(self._rule, 'search_region', None) if self._rule is not None else None
        search_radius = getattr(self._rule, 'search_radius', 0) if self._rule is not None else 0
        has_region = search_region is not None or search_radius > 0
        normalized = self._normalize_search_region_value(search_region)
        if normalized:
            w, h = normalized[2] - normalized[0], normalized[3] - normalized[1]
            source_name = self._search_region_source_name(normalized)
            text = f"검색범위: {source_name} {w}x{h}"
        elif search_radius > 0:
            text = f"검색범위: {search_radius}px"
        else:
            text = "검색범위: 전체"
        return text, has_region

    def _confidence_button_state(self):
        conf_value = getattr(self._rule, 'confidence', 0.65) if self._rule is not None else 0.65
        conf_value = conf_value or 0.65
        conf_pct = int(conf_value * 100)
        return f"인식률: {conf_pct}%", conf_pct

    def _refresh_rule_setting_controls(self):
        if hasattr(self, '_alt_image_btn'):
            if self._rule is not None and hasattr(self._rule, 'target_images'):
                alt_count = len(getattr(self._rule, 'target_images', []) or [])
                self._alt_image_btn.configure(
                    text=f"멀티이미지 ({alt_count})" if alt_count > 0 else "멀티이미지 추가",
                    state="normal",
                    fg_color=COLORS["accent_orange"] if alt_count > 0 else COLORS["bg_card"],
                    hover_color=COLORS["confidence_amber_hover"] if alt_count > 0 else COLORS["bg_card_hover"],
                    text_color=COLORS["text_on_accent"] if alt_count > 0 else COLORS["text_secondary"],
                )
            else:
                self._alt_image_btn.configure(
                    text="멀티이미지 없음",
                    state="disabled",
                    fg_color=COLORS["bg_card"],
                    hover_color=COLORS["bg_card_hover"],
                    text_color=COLORS["text_muted"],
                )

        if hasattr(self, '_search_radius_btn'):
            text, has_region = self._search_button_state()
            self._search_radius_btn.configure(
                text=text,
                state="normal" if self._rule is not None else "disabled",
                fg_color=COLORS["search_radius_purple"] if has_region else COLORS["bg_card"],
                hover_color=COLORS["search_radius_purple_hover"] if has_region else COLORS["bg_card_hover"],
                text_color=COLORS["text_on_accent"] if has_region else COLORS["text_secondary"],
            )

        if hasattr(self, '_confidence_btn'):
            text, conf_pct = self._confidence_button_state()
            has_extra_verify = bool(
                getattr(self._rule, "verify_image_color", False)
                or getattr(self._rule, "verify_image_brightness", False)
            )
            self._confidence_btn.configure(
                text=text,
                state="normal" if self._rule is not None else "disabled",
                fg_color=COLORS["confidence_amber"] if conf_pct != 65 or has_extra_verify else COLORS["bg_card"],
                hover_color=COLORS["confidence_amber_hover"] if conf_pct != 65 or has_extra_verify else COLORS["bg_card_hover"],
                text_color=COLORS["text_on_accent"] if conf_pct != 65 or has_extra_verify else COLORS["text_secondary"],
            )

        if hasattr(self, '_move_mouse_var'):
            self._move_mouse_var.set(bool(getattr(self._rule, 'move_mouse_before_search', False)))

    def _show_search_region_options(self):
        """검색범위 버튼: A/B 공용 영역 또는 자유영역을 선택한다."""
        if self._rule is None:
            return

        dialog = ctk.CTkToplevel(self)
        dialog.title("검색범위 선택")
        dialog.geometry("520x360")
        dialog.resizable(False, False)
        dialog.configure(fg_color=COLORS["bg_content"])
        dialog.transient(self)
        dialog.grab_set()

        dialog.update_idletasks()
        x = self.winfo_x() + max(0, (self.winfo_width() - 520) // 2)
        y = self.winfo_y() + max(0, (self.winfo_height() - 360) // 2)
        dialog.geometry(f"+{x}+{y}")

        main = ctk.CTkFrame(dialog, fg_color="transparent")
        main.pack(fill="both", expand=True, padx=18, pady=16)

        ctk.CTkLabel(
            main,
            text="검색범위 적용 방식",
            font=ctk.CTkFont(size=17, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(anchor="w", pady=(0, 4))
        ctk.CTkLabel(
            main,
            text="A/B 영역은 공용 프리셋으로 저장되고, 자유영역은 현재 이미지 액션에만 적용됩니다.",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["text_secondary"],
        ).pack(anchor="w", pady=(0, 12))

        def close_then(callback):
            try:
                dialog.grab_release()
            except tk.TclError:
                pass
            try:
                dialog.destroy()
            except tk.TclError:
                pass
            callback()

        def build_preset_row(slot: str, title: str, color: str):
            saved_region = self._saved_search_region(slot)
            row = ctk.CTkFrame(
                main,
                fg_color=COLORS["bg_glass"],
                corner_radius=IOS_METRICS["card_radius_compact"],
                border_width=IOS_METRICS["card_border_width"],
                border_color=color if saved_region else COLORS["border"],
            )
            row.pack(fill="x", pady=5)

            text_col = ctk.CTkFrame(row, fg_color="transparent")
            text_col.pack(side="left", fill="x", expand=True, padx=12, pady=10)
            ctk.CTkLabel(
                text_col,
                text=title,
                font=ctk.CTkFont(size=14, weight="bold"),
                text_color=color,
            ).pack(anchor="w")
            ctk.CTkLabel(
                text_col,
                text=self._search_region_label(saved_region),
                font=ctk.CTkFont(size=11),
                text_color=COLORS["text_secondary"] if saved_region else COLORS["text_muted"],
            ).pack(anchor="w", pady=(3, 0))

            if saved_region:
                ctk.CTkButton(
                    row,
                    text="적용",
                    width=70,
                    height=32,
                    fg_color=color,
                    hover_color=COLORS["accent_hover"],
                    text_color=COLORS["text_on_accent"],
                    font=ctk.CTkFont(size=12, weight="bold"),
                    corner_radius=IOS_METRICS["pill_radius"],
                    command=lambda r=saved_region, label=title: close_then(
                        lambda: self._apply_search_region_to_rule(r, label)
                    ),
                ).pack(side="right", padx=(0, 8))
                set_text = "다시설정"
            else:
                set_text = "설정"

            ctk.CTkButton(
                row,
                text=set_text,
                width=80,
                height=32,
                fg_color=COLORS["bg_elevated"],
                hover_color=COLORS["bg_card_hover"],
                text_color=COLORS["text_primary"],
                font=ctk.CTkFont(size=12, weight="bold"),
                corner_radius=IOS_METRICS["pill_radius"],
                command=lambda s=slot, label=title: close_then(
                    lambda: self._start_search_region_selection(preset_slot=s, source_label=label)
                ),
            ).pack(side="right", padx=(0, 8))

        build_preset_row("a", "A영역", COLORS["accent_blue"])
        build_preset_row("b", "B영역", COLORS["accent_orange"])

        free_row = ctk.CTkFrame(
            main,
            fg_color=COLORS["bg_glass"],
            corner_radius=IOS_METRICS["card_radius_compact"],
            border_width=IOS_METRICS["card_border_width"],
            border_color=COLORS["search_radius_purple"],
        )
        free_row.pack(fill="x", pady=5)

        free_text = ctk.CTkFrame(free_row, fg_color="transparent")
        free_text.pack(side="left", fill="x", expand=True, padx=12, pady=10)
        ctk.CTkLabel(
            free_text,
            text="자유영역",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=COLORS["search_radius_purple"],
        ).pack(anchor="w")
        ctk.CTkLabel(
            free_text,
            text="현재 이미지 액션에만 새 영역을 지정합니다.",
            font=ctk.CTkFont(size=11),
            text_color=COLORS["text_secondary"],
        ).pack(anchor="w", pady=(3, 0))

        ctk.CTkButton(
            free_row,
            text="선택",
            width=80,
            height=32,
            fg_color=COLORS["search_radius_purple"],
            hover_color=COLORS["search_radius_purple_hover"],
            text_color=COLORS["text_on_accent"],
            font=ctk.CTkFont(size=12, weight="bold"),
            corner_radius=IOS_METRICS["pill_radius"],
            command=lambda: close_then(
                lambda: self._start_search_region_selection(preset_slot=None, source_label="자유영역")
            ),
        ).pack(side="right", padx=(0, 8))

        ctk.CTkButton(
            main,
            text="닫기",
            width=90,
            height=34,
            fg_color=COLORS["bg_elevated"],
            hover_color=COLORS["bg_card_hover"],
            text_color=COLORS["text_primary"],
            font=ctk.CTkFont(size=12, weight="bold"),
            corner_radius=IOS_METRICS["pill_radius"],
            command=dialog.destroy,
        ).pack(anchor="e", pady=(10, 0))

    def _start_search_region_selection(self, preset_slot: str | None = None, source_label: str = "자유영역"):
        """화면에서 영역을 선택하고, 필요하면 A/B 프리셋에도 저장한다."""
        if self._rule is None:
            return

        from tkinter import messagebox

        self.withdraw()

        def on_region_select(x1, y1, x2, y2):
            region = self._normalize_search_region_value([x1, y1, x2, y2])
            if region is None:
                self.deiconify()
                self.grab_set()
                return
            if preset_slot:
                self._save_search_region_preset(preset_slot, region)

            self.deiconify()
            self.grab_set()

            self._apply_search_region_to_rule(region, source_label)
            x1, y1, x2, y2 = region
            w, h = x2 - x1, y2 - y1

            messagebox.showinfo(
                "설정 완료",
                f"{source_label}이 적용되었습니다.\n\n"
                f"영역: ({x1}, {y1}) ~ ({x2}, {y2})\n"
                f"크기: {w} x {h}px"
            )

        def on_cancel():
            self.deiconify()
            self.grab_set()

        existing_region = self._saved_search_region(preset_slot) if preset_slot else self._current_search_region()
        if existing_region is None:
            existing_region = self._current_search_region()

        self.after(100, lambda: ScreenRegionSelector(self, on_region_select, on_cancel, existing_region=existing_region))

    def _set_search_radius(self):
        """기존 호출 호환용: 자유영역 선택으로 연결한다."""
        self._start_search_region_selection(preset_slot=None, source_label="자유영역")

    def _on_move_mouse_changed(self):
        """마우스 이동 옵션 변경"""
        if self._rule is not None:
            self._rule.move_mouse_before_search = self._move_mouse_var.get()
            if hasattr(self._rule, "save"):
                self._rule.save()
            logger.info(f"검색 전 마우스 이동: {'활성화' if self._rule.move_mouse_before_search else '비활성화'}")
            self._invoke_image_callback(self._on_search_radius_change, self._rule)

    def _set_confidence(self):
        """인식률 설정 다이얼로그"""
        if self._rule is None:
            return

        from tkinter import messagebox

        # 인식률 설정 다이얼로그
        conf_dialog = ctk.CTkToplevel(self)
        conf_dialog.title("이미지 인식률 설정")
        conf_dialog.geometry("390x285")
        conf_dialog.resizable(False, False)
        conf_dialog.configure(fg_color=COLORS["bg_dark"])
        conf_dialog.transient(self)
        conf_dialog.grab_set()

        # 중앙 배치
        conf_dialog.update_idletasks()
        x = self.winfo_x() + (self.winfo_width() - 390) // 2
        y = self.winfo_y() + (self.winfo_height() - 285) // 2
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
            fg_color=COLORS["bg_elevated"],
            progress_color=COLORS["confidence_amber"],
            button_color=COLORS["text_primary"],
            button_hover_color=COLORS["confidence_amber"],
        )
        conf_slider.pack(side="left", padx=(0, 10))

        conf_label = ctk.CTkLabel(
            slider_frame,
            text=f"{int(conf_var.get())}%",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=COLORS["confidence_amber"],
            width=50,
        )
        conf_label.pack(side="left")

        def update_label(*args):
            conf_label.configure(text=f"{int(conf_var.get())}%")

        conf_var.trace_add("write", update_label)

        def adjust_confidence(delta: int):
            current = int(round(conf_var.get()))
            conf_var.set(max(30, min(95, current + delta)))

        def on_conf_key(event):
            key = getattr(event, "keysym", "")
            if key == "Left":
                adjust_confidence(-1)
                return "break"
            if key == "Right":
                adjust_confidence(1)
                return "break"
            return None

        conf_slider.bind("<Left>", on_conf_key)
        conf_slider.bind("<Right>", on_conf_key)
        conf_dialog.bind("<Left>", on_conf_key)
        conf_dialog.bind("<Right>", on_conf_key)
        conf_dialog.after(100, conf_slider.focus_set)

        verify_color_var = ctk.BooleanVar(value=bool(getattr(self._rule, "verify_image_color", False)))
        verify_brightness_var = ctk.BooleanVar(value=bool(getattr(self._rule, "verify_image_brightness", False)))

        verify_frame = ctk.CTkFrame(
            main_frame,
            fg_color=COLORS["bg_glass"],
            corner_radius=IOS_METRICS["card_radius_compact"],
            border_width=IOS_METRICS["card_border_width"],
            border_color=COLORS["border"],
        )
        verify_frame.pack(fill="x", pady=(6, 0))

        ctk.CTkLabel(
            verify_frame,
            text="추가 확인",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(anchor="w", padx=12, pady=(9, 2))

        verify_row = ctk.CTkFrame(verify_frame, fg_color="transparent")
        verify_row.pack(fill="x", padx=10, pady=(0, 9))

        ctk.CTkCheckBox(
            verify_row,
            text="색상 확인",
            variable=verify_color_var,
            font=ctk.CTkFont(size=12, weight="bold"),
            checkbox_width=20,
            checkbox_height=20,
        ).pack(side="left", padx=(0, 14))

        ctk.CTkCheckBox(
            verify_row,
            text="밝기 확인",
            variable=verify_brightness_var,
            font=ctk.CTkFont(size=12, weight="bold"),
            checkbox_width=20,
            checkbox_height=20,
        ).pack(side="left")

        def save_conf():
            self._rule.confidence = conf_var.get() / 100.0
            self._rule.verify_image_color = bool(verify_color_var.get())
            self._rule.verify_image_brightness = bool(verify_brightness_var.get())
            if hasattr(self._rule, "save"):
                self._rule.save()
            conf_pct = int(conf_var.get())
            logger.info(
                f"인식률 설정: {conf_pct}% "
                f"color_verify={self._rule.verify_image_color} "
                f"brightness_verify={self._rule.verify_image_brightness}"
            )

            # 버튼 텍스트 업데이트
            if hasattr(self, '_confidence_btn'):
                has_extra_verify = self._rule.verify_image_color or self._rule.verify_image_brightness
                self._confidence_btn.configure(
                    text=f"인식률: {conf_pct}%",
                    fg_color=COLORS["confidence_amber"] if conf_pct != 65 or has_extra_verify else COLORS["bg_card"],
                    hover_color=COLORS["confidence_amber_hover"] if conf_pct != 65 or has_extra_verify else COLORS["bg_card_hover"],
                    text_color=COLORS["text_on_accent"] if conf_pct != 65 or has_extra_verify else COLORS["text_secondary"],
                )

            self._invoke_image_callback(self._on_search_radius_change, self._rule)

            conf_dialog.destroy()

        btn_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        btn_frame.pack(fill="x", pady=(15, 0))

        ctk.CTkButton(
            btn_frame,
            text="저장",
            width=100,
            height=36,
            fg_color=COLORS["confidence_amber"],
            hover_color=COLORS["confidence_amber_hover"],
            corner_radius=IOS_METRICS["pill_radius"],
            command=save_conf,
        ).pack(side="left", padx=(0, 10))

        ctk.CTkButton(
            btn_frame,
            text="취소",
            width=100,
            height=36,
            fg_color=COLORS["bg_elevated"],
            hover_color=COLORS["bg_card_hover"],
            text_color=COLORS["text_secondary"],
            corner_radius=IOS_METRICS["pill_radius"],
            command=conf_dialog.destroy,
        ).pack(side="left")

    @staticmethod
    def _navigation_media_from_item(item):
        """Return (path, rule/settings) for action-backed images or loose template media."""
        if isinstance(item, dict):
            path = item.get("path") or item.get("target_image") or item.get("image_path")
            return path, item.get("rule")
        if isinstance(item, (str, Path)):
            return str(item), None
        if hasattr(item, "target_image"):
            return getattr(item, "target_image", None), item
        if hasattr(item, "image_path"):
            return getattr(item, "image_path", None), None
        return None, None

    def _navigate_image(self, direction: int):
        """이미지 내비게이션 (direction: -1=이전, 1=다음)"""
        if not self._image_list or self._current_index < 0:
            return

        step = 1 if direction > 0 else -1
        new_index = self._current_index
        new_image_path = None
        new_rule = None

        while True:
            new_index += step

            # 범위 체크
            if new_index < 0 or new_index >= len(self._image_list):
                return

            # 새 이미지 정보 가져오기
            item = self._image_list[new_index]

            new_image_path, new_rule = self._navigation_media_from_item(item)
            if not new_image_path:
                continue

            if not is_supported_media_path(new_image_path):
                continue

            if not Path(new_image_path).exists():
                logger.warning(f"이미지 내비게이션 건너뜀: 파일 없음 index={new_index + 1} path={new_image_path}")
                continue

            break

        # 현재 인덱스 업데이트
        self._current_index = new_index
        self._image_path = new_image_path
        self._rule = new_rule
        self._crop_filename_var.set("")

        # 크롭 상태 초기화
        self._crop_coords = None
        self._crop_mask = None
        self._crop_mask_needs_refresh = False
        self._full_image_mask = None
        self._canvas_offset_x = 0
        self._canvas_offset_y = 0

        # 이미지 다시 로드
        self._load_image()

        if hasattr(self, "_background_cutout_cb"):
            if self._is_video:
                self._background_cutout_var.set(False)
                self._background_cutout_cb.configure(
                    text="배경제거(이미지 전용)",
                    state="disabled",
                    text_color=COLORS["text_muted"],
                )
            else:
                self._background_cutout_cb.configure(
                    text="배경제거 이미지따기",
                    state="normal",
                    text_color=COLORS["text_primary"],
                )
        if hasattr(self, "_save_btn"):
            self._save_btn.configure(text="영상 크롭 저장" if self._is_video else "크롭 저장")

        # UI 업데이트
        if self._original_image is not None:
            h, w = self._original_image.shape[:2]
            self._reset_image_scale(w, h)
            self._update_canvas_image()

        # 파일명 업데이트
        filename = Path(self._image_path).name
        self.title(f"{'동영상' if self._is_video else '이미지'} 편집: {filename}")
        if hasattr(self, '_filename_label'):
            self._filename_label.configure(text=f"📁 {truncate_ui_text(filename, 58)}")

        # 미리보기 초기화
        if hasattr(self, '_preview_canvas'):
            self._preview_canvas.delete("all")
        if hasattr(self, '_preview_label') and self._crop_coords is None:
            self._preview_label.configure(text="영역을 선택하세요", text_color=COLORS["text_muted"])
        if self._crop_coords is not None:
            self._refresh_preview()
            self._save_btn.configure(state="normal")
        self._update_crop_filename_state()

        # 내비게이션 버튼 상태 업데이트
        self._update_nav_buttons()
        self._refresh_rule_setting_controls()
        self.after(10, self._focus_crop_canvas)

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
        if self._rule is None or not hasattr(self._rule, 'target_images'):
            return

        dialog = AltImageDialog(self, self._rule, self._on_search_radius_change)
        self.wait_window(dialog)

        # 버튼 텍스트 업데이트
        self._invoke_image_callback(self._on_search_radius_change, self._rule)
        self._refresh_rule_setting_controls()


class AltImageDialog(ctk.CTkToplevel):
    """멀티이미지 관리 다이얼로그"""

    def __init__(self, parent, rule: AutomationRule, on_change: Optional[Callable] = None):
        super().__init__(parent)

        self._rule = rule
        self._on_change = on_change
        self._thumbnail_refs = []

        self.title("멀티이미지 관리")
        self.geometry("500x400")
        self.configure(fg_color=COLORS["bg_content"])
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
        list_frame = VirtualScrollFrame(
            self,
            item_height=72,
            buffer_count=4,
            fg_color=COLORS["bg_glass"],
            corner_radius=IOS_METRICS["card_radius_compact"],
            border_width=IOS_METRICS["card_border_width"],
            border_color=COLORS["border"],
            height=220,
        )
        list_frame.set_render_callback(self._render_image_row)
        list_frame.pack(fill="x", padx=20, pady=(0, 15))

        self._list_frame = list_frame
        self._list_empty_label = ctk.CTkLabel(
            self,
            text="등록된 멀티이미지가 없습니다",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["text_muted"],
        )
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
            hover_color=COLORS["green_hover"],
            font=ctk.CTkFont(size=13, weight="bold"),
            corner_radius=IOS_METRICS["pill_radius"],
        ).pack(side="left", padx=5)

        ctk.CTkButton(
            btn_frame,
            text="스크린샷 추가",
            command=self._capture_screenshot,
            width=120,
            height=36,
            fg_color=COLORS["accent_blue"],
            hover_color=COLORS["hover_blue"],
            font=ctk.CTkFont(size=13, weight="bold"),
            corner_radius=IOS_METRICS["pill_radius"],
        ).pack(side="left", padx=5)

        ctk.CTkButton(
            btn_frame,
            text="닫기",
            command=self.destroy,
            width=80,
            height=36,
            fg_color=COLORS["bg_elevated"],
            hover_color=COLORS["bg_card_hover"],
            text_color=COLORS["text_secondary"],
            corner_radius=IOS_METRICS["pill_radius"],
        ).pack(side="right", padx=5)

    def _refresh_list(self):
        """이미지 목록 새로고침"""
        self._thumbnail_refs.clear()
        if not self._rule.target_images:
            self._list_frame.pack_forget()
            self._list_empty_label.pack(pady=(0, 15))
            return

        self._list_empty_label.pack_forget()
        self._list_frame.pack(fill="x", padx=20, pady=(0, 15))
        self._list_frame.set_items(list(enumerate(self._rule.target_images)), preserve_scroll=True)

    def _render_image_row(self, parent, item_data, _index: int):
        index, img_path = item_data
        return self._create_image_row(index, img_path, parent=parent)

    def _create_image_row(self, index: int, img_path: str, parent=None):
        """이미지 행 생성"""
        row = ctk.CTkFrame(
            parent or self._list_frame,
            fg_color=COLORS["bg_elevated"],
            corner_radius=IOS_METRICS["control_radius"],
            height=66,
        )
        row.pack_propagate(False)
        if parent is None:
            row.pack(fill="x", padx=10, pady=5)

        # 썸네일
        thumb_frame = ctk.CTkFrame(
            row,
            fg_color=COLORS["bg_elevated"],
            width=50,
            height=50,
            corner_radius=IOS_METRICS["control_radius_small"],
        )
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
            text=f"{index + 1}. {truncate_ui_text(filename, 48)}",
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
            hover_color=COLORS["danger_hover"],
            text_color=COLORS["text_on_accent"],
            font=ctk.CTkFont(size=11),
            corner_radius=IOS_METRICS["pill_radius"],
        ).pack(side="right", padx=10, pady=8)
        return row

    def _add_image(self):
        """파일에서 이미지 추가"""
        from ..utils.config import DATA_DIR
        import shutil
        import uuid

        templates_dir = DATA_DIR / "templates"
        templates_dir.mkdir(parents=True, exist_ok=True)

        file_path = filedialog.askopenfilename(
            title="멀티이미지/동영상 선택",
            initialdir=str(templates_dir),
            filetypes=[
                ("모든 파일", "*.*"),
                ("이미지/동영상 파일", "*.*"),
                ("이미지 파일", IMAGE_FILE_PATTERNS),
                ("동영상 파일", VIDEO_FILE_PATTERNS),
            ],
        )

        if not file_path:
            return

        try:
            src_path = Path(file_path)
            if is_video_media_path(src_path):
                self._open_video_media_editor(src_path)
                return
            if not is_supported_media_path(src_path):
                from tkinter import messagebox
                messagebox.showwarning("알림", "지원하지 않는 파일 형식입니다.")
                return

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

    def _open_video_media_editor(self, video_path: Path):
        """동영상은 멀티이미지 매칭 목록에 직접 넣지 않고 편집창으로 연다."""
        from ..utils.config import DATA_DIR
        import shutil
        import uuid

        templates_dir = DATA_DIR / "templates"
        templates_dir.mkdir(parents=True, exist_ok=True)
        video_path = Path(video_path)
        if video_path.parent.resolve() != templates_dir.resolve():
            dest_path = templates_dir / f"video_{uuid.uuid4().hex[:8]}{video_path.suffix}"
            shutil.copy2(video_path, dest_path)
            video_path = dest_path

        media_paths = []
        if templates_dir.exists():
            for path in templates_dir.iterdir():
                if path.is_file() and is_supported_media_path(path):
                    media_paths.append(path)
        media_paths.sort(key=lambda p: (p.stat().st_mtime if p.exists() else 0, p.name), reverse=True)
        try:
            current_index = next(
                idx for idx, path in enumerate(media_paths)
                if path.resolve() == video_path.resolve()
            )
        except (StopIteration, OSError):
            media_paths.insert(0, video_path)
            current_index = 0

        nav_items = [{"path": str(path), "rule": TemplateMediaSettings(path)} for path in media_paths]
        settings = nav_items[current_index]["rule"] if nav_items else TemplateMediaSettings(video_path)

        def mark_changed(*args):
            saved = False
            for arg in args:
                if hasattr(arg, "save"):
                    arg.save()
                    saved = True
                    break
            if not saved and hasattr(settings, "save"):
                settings.save()
            if self._on_change:
                self._on_change()

        dialog = ImageCropDialog(
            self,
            str(video_path),
            on_crop=mark_changed,
            on_delete=mark_changed,
            on_change=mark_changed,
            rule=settings,
            on_search_radius_change=mark_changed,
            image_list=nav_items,
            current_index=current_index,
        )
        self.wait_window(dialog)

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
        self._action_widgets = {}
        self._collapsible_rule_ids = set()
        self._visible_rule_items = []
        self._render_batch_after_id = None
        self._render_batch_after_ids = set()
        self._render_batch_generation = 0
        self._render_batch_size = 24
        self._rule_descendant_count_cache = {}
        self._last_collapse_btn_text = None
        self._font_cache = {}

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
        self._collapsed_items.clear()
        self._collapsible_rule_ids.clear()

        def add_collapsed(rules):
            for rule in rules:
                if rule.children:
                    self._collapsible_rule_ids.add(rule.rule_id)
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
            font=self._font(14, "bold"),
            corner_radius=IOS_METRICS["pill_radius"],
        ).pack(side="left", padx=15)

        ctk.CTkButton(
            btn_content,
            text="취소",
            command=self._on_cancel,
            width=100,
            height=45,
            fg_color=COLORS["bg_elevated"],
            hover_color=COLORS["bg_card_hover"],
            text_color=COLORS["text_secondary"],
            corner_radius=IOS_METRICS["pill_radius"],
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
            font=self._font(22, "bold"),
            text_color=COLORS["text_primary"],
        ).pack(side="left")

        # 모두 접기/펼치기 버튼
        self._collapse_btn = ctk.CTkButton(
            header_row,
            text="모두 펼치기",
            command=self._toggle_all_collapse,
            width=90,
            height=28,
            fg_color=COLORS["bg_elevated"],
            hover_color=COLORS["bg_card_hover"],
            text_color=COLORS["text_secondary"],
            font=self._font(11),
            corner_radius=IOS_METRICS["pill_radius"],
        )
        self._collapse_btn.pack(side="right")

        # 전체 규칙 수 계산 (자식 포함)
        total_count = self._count_all_rules(self._plan.initial_rules + self._plan.monitoring_rules)
        ctk.CTkLabel(
            main,
            text=f"총 {total_count}개의 동작이 감지되었습니다. 확인 후 승인하세요.",
            font=self._font(13),
            text_color=COLORS["text_secondary"],
        ).pack(anchor="w", pady=(5, 20))

        # 동작 목록
        self._scrollable = VirtualScrollFrame(
            main,
            item_height=92,
            buffer_count=5,
            fg_color=COLORS["bg_glass"],
            corner_radius=IOS_METRICS["card_radius_compact"],
        )
        self._scrollable.set_render_callback(self._render_virtual_action_item)
        self._scrollable.set_destroy_callback(self._on_virtual_action_item_destroyed)
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

    def _font(self, size, weight=None):
        """Reuse CTkFont objects while rebuilding virtualized action rows."""
        key = (size, weight or "")
        cached = self._font_cache.get(key)
        if cached is None:
            kwargs = {"family": IOS_FONTS["family"], "size": size}
            if weight:
                kwargs["weight"] = weight
            cached = ctk.CTkFont(**kwargs)
            self._font_cache[key] = cached
        return cached

    def _count_rule_descendants(self, rule: AutomationRule) -> int:
        """Collapse badge count is stable in this dialog, so cache recursive counts."""
        if not rule or not rule.children:
            return 0
        cached = self._rule_descendant_count_cache.get(rule.rule_id)
        if cached is not None:
            return cached
        count = self._count_all_rules(rule.children)
        self._rule_descendant_count_cache[rule.rule_id] = count
        return count

    def _set_collapse_button_text(self, text: str) -> None:
        if self._last_collapse_btn_text == text:
            return
        self._last_collapse_btn_text = text
        self._collapse_btn.configure(text=text)

    def _toggle_all_collapse(self):
        """모든 액션 접기/펼치기"""
        if self._all_collapsed:
            # 모두 펼치기는 루트만 즉시 열고, 중첩 자식은 기존 lazy-collapse를 유지한다.
            # 깊은 트리를 한 번에 렌더링하면 대형 재생목록에서 UI가 멈춘다.
            for rule in self._iter_top_level_collapsible_rules():
                self._collapsed_items.discard(rule.rule_id)
            self._all_collapsed = False
            self._set_collapse_button_text("모두 접기")
        else:
            # 모두 접기
            self._init_collapsed_items()
            self._all_collapsed = True
            self._set_collapse_button_text("모두 펼치기")
        self._apply_collapse_state()

    def _iter_top_level_collapsible_rules(self):
        """Yield only top-level rules that have children."""
        for rules in (self._plan.initial_rules, self._plan.monitoring_rules):
            for rule in rules:
                if rule.children:
                    yield rule

    def _toggle_item_collapse(self, rule_id: str):
        """개별 액션 접기/펼치기.

        보이는 행만 다시 계산해서 가상 리스트에 넘긴다. 실제 위젯은 현재 화면
        범위만 유지되므로 대형 재생목록에서도 접기/펼치기 반응이 일정하다.
        """
        if rule_id in self._collapsed_items:
            self._collapsed_items.discard(rule_id)
        else:
            self._collapsed_items.add(rule_id)
        self._refresh_action_list(preserve_scroll=True)
        self._sync_all_collapsed_state()

    def _refresh_action_list(self, preserve_scroll: bool = False):
        """액션 목록 새로고침"""
        self._cancel_action_list_render_batch()
        is_virtual = isinstance(self._scrollable, VirtualScrollFrame)
        if is_virtual and not preserve_scroll:
            self._scrollable.set_items([], preserve_scroll=False)
        if not is_virtual or not preserve_scroll:
            # 보존 갱신에서는 VirtualScrollFrame이 기존 visible 위젯을 재사용할 수 있다.
            # 이때 매핑을 먼저 비우면 재사용된 행의 토글/썸네일 상태 연결이 끊긴다.
            self._thumbnail_refs.clear()
            self._action_widgets = {}
        self._collapsible_rule_ids.clear()

        items = self._build_visible_rule_render_items()
        self._visible_rule_items = items
        if is_virtual:
            self._scrollable.set_items(items, preserve_scroll=preserve_scroll)
        else:
            for widget in self._scrollable.winfo_children():
                widget.destroy()
            self._render_action_list_batch(items, start=0, generation=self._render_batch_generation)
        self._sync_all_collapsed_state()

    def _build_visible_rule_render_items(self):
        items = []
        for rules in (self._plan.initial_rules, self._plan.monitoring_rules):
            self._collect_visible_rule_items(rules, items, depth=0, prefix="", parent_id=None)
        return items

    def _collect_visible_rule_items(
        self,
        rules,
        items,
        depth: int = 0,
        prefix: str = "",
        parent_id: Optional[str] = None,
    ) -> None:
        for index, rule in enumerate(rules, start=1):
            label = f"{prefix}-{index}" if prefix else str(index)
            items.append({
                "rule": rule,
                "depth": depth,
                "index_label": label,
                "parent_id": parent_id,
            })
            if rule.children:
                self._collapsible_rule_ids.add(rule.rule_id)
                if rule.rule_id not in self._collapsed_items:
                    self._collect_visible_rule_items(
                        rule.children,
                        items,
                        depth=depth + 1,
                        prefix=label,
                        parent_id=rule.rule_id,
                    )

    def _render_virtual_action_item(self, parent, item_data, _index: int):
        rule = item_data.get("rule")
        if rule is None:
            return None
        return self._create_action_item(
            parent,
            str(item_data.get("index_label") or ""),
            rule,
            int(item_data.get("depth") or 0),
            manage_geometry=False,
            render_inline_children=False,
        )

    def _on_virtual_action_item_destroyed(self, item_data, _index: int, widget) -> None:
        rule = item_data.get("rule") if isinstance(item_data, dict) else None
        rule_id = getattr(rule, "rule_id", None)
        if not rule_id:
            return
        widget_data = self._action_widgets.get(rule_id)
        if widget_data and widget_data.get("wrapper") is widget:
            self._action_widgets.pop(rule_id, None)

    def _cancel_action_list_render_batch(self):
        self._render_batch_generation += 1
        after_ids = set(getattr(self, "_render_batch_after_ids", set()))
        after_id = getattr(self, "_render_batch_after_id", None)
        if after_id:
            after_ids.add(after_id)
        self._render_batch_after_id = None
        self._render_batch_after_ids.clear()
        for after_id in after_ids:
            try:
                self.after_cancel(after_id)
            except (tk.TclError, RuntimeError):
                pass

    def _schedule_action_list_render_batch(self, callback):
        after_id = None

        def run_callback():
            self._render_batch_after_ids.discard(after_id)
            if self._render_batch_after_id == after_id:
                self._render_batch_after_id = None
            callback()

        after_id = self.after(1, run_callback)
        self._render_batch_after_ids.add(after_id)
        self._render_batch_after_id = after_id
        return after_id

    def _build_root_rule_render_items(self):
        items = []
        for rules in (self._plan.initial_rules, self._plan.monitoring_rules):
            items.extend(self._build_rule_render_items(rules, depth=0, prefix=""))
        return items

    def _build_rule_render_items(self, rules, depth=0, prefix: str = ""):
        items = []
        for index, rule in enumerate(rules, start=1):
            label = f"{prefix}-{index}" if prefix else str(index)
            items.append((rule, depth, label))
        return items

    def _unpack_rule_render_item(self, item):
        if isinstance(item, dict):
            return (
                item.get("rule"),
                int(item.get("depth") or 0),
                str(item.get("index_label") or ""),
            )
        return item

    def _render_action_list_batch(self, items, start: int, generation: int, batch_size: Optional[int] = None):
        if generation != self._render_batch_generation:
            return
        if self._scrollable is None:
            return
        batch_size = batch_size or self._render_batch_size
        end = min(len(items), start + batch_size)
        for item in items[start:end]:
            rule, depth, label = self._unpack_rule_render_item(item)
            if rule is None:
                continue
            self._create_action_item(self._scrollable, label, rule, depth)

        if end >= len(items):
            self._render_batch_after_id = None
            self._sync_all_collapsed_state()
            return

        try:
            self._schedule_action_list_render_batch(
                lambda: self._render_action_list_batch(items, end, generation, batch_size=batch_size)
            )
        except (tk.TclError, RuntimeError):
            pass

    def _render_rule_children_batch(self, rule_id: str, parent, items, start: int, generation: int, batch_size: Optional[int] = None):
        if generation != self._render_batch_generation:
            return
        widget_data = self._action_widgets.get(rule_id)
        if not widget_data:
            return
        batch_size = batch_size or self._render_batch_size
        end = min(len(items), start + batch_size)
        for item in items[start:end]:
            rule, depth, label = self._unpack_rule_render_item(item)
            if rule is None:
                continue
            self._create_action_item(parent, label, rule, depth)

        if end >= len(items):
            widget_data["children_rendered"] = True
            widget_data["children_rendering"] = False
            self._sync_all_collapsed_state()
            return

        widget_data["children_rendering"] = True
        try:
            self._schedule_action_list_render_batch(
                lambda: self._render_rule_children_batch(
                    rule_id,
                    parent,
                    items,
                    end,
                    generation,
                    batch_size=batch_size,
                )
            )
        except (tk.TclError, RuntimeError):
            widget_data["children_rendering"] = False

    def _render_rules(self, parent, rules, depth=0, prefix: str = ""):
        """규칙 목록 렌더링 (계층 구조 지원)"""
        for index, rule in enumerate(rules, start=1):
            label = f"{prefix}-{index}" if prefix else str(index)
            self._create_action_item(parent, label, rule, depth)

    def _ensure_children_rendered(self, rule_id: str) -> None:
        widget_data = self._action_widgets.get(rule_id)
        if not widget_data or widget_data.get("children_rendered") or widget_data.get("children_rendering"):
            return
        rule = widget_data.get("rule")
        container = widget_data.get("children_container")
        if not rule or container is None:
            return
        items = self._build_rule_render_items(
            rule.children,
            depth=widget_data.get("depth", 0) + 1,
            prefix=str(widget_data.get("index_label", "")),
        )
        widget_data["children_rendering"] = True
        self._render_rule_children_batch(rule_id, container, items, 0, self._render_batch_generation)

    def _apply_rule_collapse_state(self, rule_id: str) -> None:
        widget_data = self._action_widgets.get(rule_id)
        if not widget_data:
            return
        container = widget_data.get("children_container")
        if container is None:
            if isinstance(self._scrollable, VirtualScrollFrame):
                self._refresh_action_list(preserve_scroll=True)
            return

        is_collapsed = rule_id in self._collapsed_items
        self._update_rule_toggle_button(rule_id)
        if is_collapsed:
            container.pack_forget()
            return

        self._ensure_children_rendered(rule_id)
        if not container.winfo_ismapped():
            container.pack(fill="x")

    def _apply_collapse_state(self) -> None:
        if isinstance(self._scrollable, VirtualScrollFrame):
            self._refresh_action_list(preserve_scroll=True)
            return
        processed = set()
        while True:
            pending = [rule_id for rule_id in self._action_widgets.keys() if rule_id not in processed]
            if not pending:
                break
            for rule_id in pending:
                processed.add(rule_id)
                self._apply_rule_collapse_state(rule_id)

    def _update_rule_toggle_button(self, rule_id: str) -> None:
        widget_data = self._action_widgets.get(rule_id)
        toggle_btn = widget_data.get("toggle_btn") if widget_data else None
        if toggle_btn:
            rule = widget_data.get("rule")
            child_count = self._count_rule_descendants(rule)
            icon = "▶" if rule_id in self._collapsed_items else "▼"
            text = f"{icon} {child_count}"
            if widget_data.get("toggle_text") == text:
                return
            widget_data["toggle_text"] = text
            toggle_btn.configure(text=text)

    def _sync_all_collapsed_state(self) -> None:
        if not self._collapsible_rule_ids:
            self._all_collapsed = False
        else:
            self._all_collapsed = self._collapsible_rule_ids.issubset(self._collapsed_items)
        self._set_collapse_button_text("모두 펼치기" if self._all_collapsed else "모두 접기")

    def _drop_rule_widget_mappings(self, rule: AutomationRule) -> None:
        """Remove stale widget mappings for a rule subtree before row-level redraw."""
        self._action_widgets.pop(rule.rule_id, None)
        for child in getattr(rule, "children", []) or []:
            self._drop_rule_widget_mappings(child)

    def _refresh_rule_row(self, rule_id: str) -> bool:
        """Rebuild one visible rule row/subtree without refreshing the full action list."""
        if isinstance(self._scrollable, VirtualScrollFrame):
            index = self._scrollable.find_item_index_by_object_id(rule_id, "AutomationRule")
            if index < 0:
                return False
            self._scrollable.refresh_item(index)
            return True
        widget_data = self._action_widgets.get(rule_id)
        if not widget_data:
            return False
        wrapper = widget_data.get("wrapper")
        rule = widget_data.get("rule")
        parent = wrapper.master if wrapper is not None else None
        if wrapper is None or rule is None or parent is None:
            return False
        try:
            siblings = list(parent.winfo_children())
            current_index = siblings.index(wrapper)
            before_widget = siblings[current_index + 1] if current_index + 1 < len(siblings) else None
            index_label = str(widget_data.get("index_label") or "")
            depth = int(widget_data.get("depth") or 0)
            self._drop_rule_widget_mappings(rule)
            wrapper.destroy()
            self._create_action_item(parent, index_label, rule, depth, before_widget=before_widget)
            return True
        except (tk.TclError, RuntimeError, ValueError, AttributeError):
            return False

    def _create_action_item(
        self,
        parent,
        index: str,
        rule: AutomationRule,
        depth: int = 0,
        before_widget=None,
        manage_geometry: bool = True,
        render_inline_children: bool = True,
    ):
        """동작 항목 생성"""
        # 깊이에 따른 들여쓰기 계산
        indent = depth * 25
        left_pad = 10 + indent

        # 자식이 있으면 다른 배경색
        bg_color = COLORS["bg_glass"] if depth == 0 else COLORS["child_bg"]

        item_wrapper = ctk.CTkFrame(parent, fg_color="transparent")
        if manage_geometry:
            if before_widget is not None:
                item_wrapper.pack(fill="x", before=before_widget)
            else:
                item_wrapper.pack(fill="x")

        item = ctk.CTkFrame(item_wrapper, fg_color=bg_color, corner_radius=IOS_METRICS["control_radius"])
        item.pack(fill="x", pady=3, padx=(left_pad, 10))

        self._action_widgets[rule.rule_id] = {
            "wrapper": item_wrapper,
            "widget": item,
            "rule": rule,
            "depth": depth,
            "index_label": index,
            "children_rendered": False,
            "children_rendering": False,
        }

        content = ctk.CTkFrame(item, fg_color="transparent")
        content.pack(fill="x", padx=12, pady=8)

        # 접기/펼치기 버튼 (자식이 있는 경우)
        if rule.children:
            self._collapsible_rule_ids.add(rule.rule_id)
            is_collapsed = rule.rule_id in self._collapsed_items
            toggle_text = "▶" if is_collapsed else "▼"
            child_count = self._count_rule_descendants(rule)
            full_toggle_text = f"{toggle_text} {child_count}"
            toggle_btn = ctk.CTkButton(
                content,
                text=full_toggle_text,
                command=lambda r=rule: self._toggle_item_collapse(r.rule_id),
                width=45,
                height=24,
                fg_color=COLORS["bg_elevated"],
                hover_color=COLORS["bg_card_hover"],
                text_color=COLORS["text_muted"],
                font=self._font(11),
                corner_radius=IOS_METRICS["control_radius_small"],
            )
            toggle_btn.pack(side="left", padx=(0, 8))
            self._action_widgets[rule.rule_id]["toggle_btn"] = toggle_btn
            self._action_widgets[rule.rule_id]["toggle_text"] = full_toggle_text

        # 썸네일
        thumb = ctk.CTkFrame(
            content,
            fg_color=COLORS["bg_elevated"],
            width=60,
            height=60,
            corner_radius=IOS_METRICS["control_radius_small"],
        )
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
            "random_key_sequence": COLORS["accent_orange"],
            "auto_list": COLORS["accent_blue"],
            "auto_list_value_input": COLORS["success"],
            "action_call": COLORS["accent_orange"],
            "scroll": COLORS["scroll_purple"],
            "drag": COLORS["warning"],
        }
        color = action_colors.get(rule.action_type, COLORS["text_muted"])

        ctk.CTkLabel(
            row1,
            text=f"{index}",
            font=self._font(13, "bold"),
            fg_color=color,
            text_color=COLORS["text_primary"],
            corner_radius=IOS_METRICS["control_radius_small"],
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
            "random_key_sequence": "랜덤키 입력",
            "auto_list": "자동 목록 처리",
            "auto_list_value_input": "현재 처리수량 입력",
            "action_call": "액션 호출",
            "scroll": "스크롤",
            "drag": "드래그",
        }
        ctk.CTkLabel(
            row1,
            text=action_names.get(rule.action_type, rule.action_type or "동작"),
            font=self._font(14, "bold"),
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
                text=truncate_ui_text("  |  ".join(details), 76),
                font=self._font(12),
                text_color=COLORS["text_secondary"],
                anchor="w",
                width=330,
            ).pack(side="left")

        # 대기 시간
        if rule.wait_after and rule.wait_after > 0.3:
            ctk.CTkLabel(
                row2,
                text=f"대기 {rule.wait_after:.1f}초",
                font=self._font(11),
                text_color=COLORS["warning_text"],
            ).pack(side="right")

        if render_inline_children and rule.children:
            children_container = ctk.CTkFrame(item_wrapper, fg_color="transparent")
            self._action_widgets[rule.rule_id]["children_container"] = children_container
            if rule.rule_id not in self._collapsed_items:
                children_container.pack(fill="x")
                self._ensure_children_rendered(rule.rule_id)

        return item_wrapper

    def _display_thumbnail(self, parent, rule: AutomationRule):
        """Display an action thumbnail without blocking the UI thread."""
        image_path = rule.target_image

        def show_fallback():
            icons = {
                "click": "M",
                "double_click": "M",
                "right_click": "M",
                "type": "T",
                "hotkey": "K",
                "key_press": "K",
                "random_key_sequence": "R",
                "auto_list": "L",
                "auto_list_value_input": "V",
                "action_call": "C",
                "scroll": "S",
                "drag": "D",
            }
            ctk.CTkLabel(
                parent,
                text=icons.get(rule.action_type, "A"),
                font=self._font(24, "bold"),
                text_color=COLORS["text_muted"],
            ).pack(expand=True)

        def pack_thumbnail(ctk_image, width: int, height: int, path: str):
            thumb_btn = ctk.CTkButton(
                parent,
                image=ctk_image,
                text="",
                width=width + 10,
                height=height + 10,
                fg_color="transparent",
                hover_color=COLORS["bg_card_hover"],
                corner_radius=IOS_METRICS["control_radius_small"],
                command=lambda p=path, r=rule: self._open_image_editor(p, r),
            )
            thumb_btn.pack(expand=True)
            self._thumbnail_refs.append(ctk_image)

        if not (image_path and self._is_valid_image_path(image_path)):
            show_fallback()
            return

        cached = get_cached_thumbnail(image_path, (60, 60))
        if cached is not None:
            pack_thumbnail(cached, 60, 60, image_path)
            return

        placeholder = ctk.CTkLabel(
            parent,
            text="IMG",
            font=self._font(12, "bold"),
            text_color=COLORS["text_muted"],
        )
        placeholder.pack(expand=True)

        def load_thumbnail(path=image_path, target_rule=rule, placeholder_widget=placeholder):
            try:
                img_arr = np.fromfile(path, np.uint8)
                img = cv2.imdecode(img_arr, cv2.IMREAD_COLOR)
                if img is None:
                    raise ValueError("Image load failed")
                img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                h, w = img_rgb.shape[:2]
                scale = min(60 / w, 60 / h)
                new_w, new_h = max(1, int(w * scale)), max(1, int(h * scale))
                resized = cv2.resize(img_rgb, (new_w, new_h))
                pil_image = Image.fromarray(resized)

                def apply_thumbnail():
                    try:
                        if getattr(target_rule, "target_image", None) != path:
                            return
                        if not placeholder_widget.winfo_exists():
                            return
                        ctk_image = ctk.CTkImage(
                            light_image=pil_image,
                            dark_image=pil_image,
                            size=(new_w, new_h),
                        )
                        set_cached_thumbnail(path, (60, 60), ctk_image)
                        placeholder_widget.destroy()
                        pack_thumbnail(ctk_image, new_w, new_h, path)
                    except (tk.TclError, RuntimeError):
                        pass

                self.after(0, apply_thumbnail)
            except Exception as e:
                logger.warning(f"Thumbnail load failed: {path} - {e}")
                try:
                    self.after(0, lambda: placeholder_widget.configure(text="ERR"))
                except (tk.TclError, RuntimeError):
                    pass

        submit_thumbnail_task(load_thumbnail)

    def _display_thumbnail_sync_legacy(self, parent, rule: AutomationRule):
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
                    corner_radius=IOS_METRICS["control_radius_small"],
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
        all_image_rules = []

        def collect(rules):
            for item in rules:
                if getattr(item, 'target_image', None):
                    all_image_rules.append(item)
                if getattr(item, 'children', None):
                    collect(item.children)

        collect(self._plan.initial_rules)
        collect(self._plan.monitoring_rules)

        rule_image_paths = set()
        for item in all_image_rules:
            try:
                path = getattr(item, "target_image", None)
                if path:
                    rule_image_paths.add(str(Path(path).resolve()).lower())
            except OSError:
                pass

        navigation_items = list(all_image_rules)
        templates_dir = DATA_DIR / "templates"
        if templates_dir.exists():
            try:
                video_paths = [
                    path for path in templates_dir.iterdir()
                    if path.is_file() and is_video_media_path(path)
                ]
                video_paths.sort(key=lambda p: (p.stat().st_mtime if p.exists() else 0, p.name), reverse=True)
                for path in video_paths:
                    try:
                        resolved = str(path.resolve()).lower()
                    except OSError:
                        resolved = str(path).lower()
                    if resolved in rule_image_paths:
                        continue
                    navigation_items.append({"path": str(path), "rule": TemplateMediaSettings(path)})
            except OSError as exc:
                logger.debug(f"템플릿 동영상 목록 로드 실패: {exc}")

        current_index = -1
        for i, item in enumerate(navigation_items):
            if getattr(item, 'rule_id', None) == getattr(rule, 'rule_id', None):
                current_index = i
                break

        needs_refresh = [False]
        changed_rule_ids = set()

        def mark_changed(target_rule=None, old_path=None, new_path=None):
            target = target_rule or rule
            self._plan.modified = True
            needs_refresh[0] = True
            changed_rule_ids.add(getattr(target, 'rule_id', rule.rule_id))
            if old_path:
                invalidate_thumbnail_cache(old_path)
            if new_path:
                invalidate_thumbnail_cache(new_path)

        def on_crop_complete(new_path: str, target_rule=None, old_path=None):
            invalidate_thumbnail_cache(new_path)
            mark_changed(target_rule, old_path=old_path, new_path=new_path)
            logger.info(f"이미지 크롭 완료: {new_path}")

        def on_delete(target_rule=None, old_path=None):
            mark_changed(target_rule, old_path=old_path)
            logger.info(f"이미지 삭제됨: {getattr(target_rule or rule, 'rule_id', rule.rule_id)}")

        def on_change(new_path: str, target_rule=None, old_path=None):
            mark_changed(target_rule, old_path=old_path, new_path=new_path)
            logger.info(f"이미지 변경 완료: {new_path}")

        def on_search_radius_change(target_rule=None):
            mark_changed(target_rule or rule)

        dialog = ImageCropDialog(
            self,
            image_path,
            on_crop=on_crop_complete,
            on_delete=on_delete,
            on_change=on_change,
            rule=rule,
            on_search_radius_change=on_search_radius_change,
            image_list=navigation_items,
            current_index=current_index,
        )
        self.wait_window(dialog)
        if needs_refresh[0]:
            for rule_id in changed_rule_ids:
                self._refresh_rule_row(rule_id)

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
        self._cancel_action_list_render_batch()
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
        self._selected_recording_id: Optional[int] = None

        self._automation_plan_result: Optional[bool] = None
        self._automation_plan_event = threading.Event()
        self._last_automation_plan: Optional[AutomationPlan] = None

        self._plan_modified_cache = {}  # plan_id ? modified ??
        self._plan_lock_cache = {}  # plan_id ? locked ??
        self._plans_load_generation: int = 0  # async/sync plan load generation guard
        self._recordings_load_generation: int = 0
        self._plan_items = []
        self._recording_items = []
        self._recording_index_by_id = {}
        self._ui_dispatcher = UiCallbackDispatcher(self, tick_ms=20, max_callbacks_per_tick=72)

        self._setup_ui()
        self.after(0, self._deferred_load)  # UI ?? ? ??? ??

    def after(self, ms, func=None, *args):
        """AnalyzerView? worker-thread after() ??? ????? dispatcher? ????."""
        if func is None:
            return super().after(ms)
        dispatcher = getattr(self, "_ui_dispatcher", None)
        if dispatcher is None or threading.current_thread() is threading.main_thread():
            return super().after(ms, func, *args)

        def _schedule_on_main():
            try:
                if not self.winfo_exists():
                    return
                super(AnalyzerView, self).after(ms, func, *args)
            except (tk.TclError, RuntimeError):
                pass

        dispatcher.post(_schedule_on_main)
        return None

    def _analyzer_ui_post(self, callback) -> None:
        try:
            dispatcher = getattr(self, "_ui_dispatcher", None)
            if dispatcher is not None:
                dispatcher.post(callback)
                return
            if threading.current_thread() is threading.main_thread():
                callback()
                return
            super(AnalyzerView, self).after(0, callback)
        except (tk.TclError, RuntimeError):
            pass

    def _deferred_load(self):
        """UI ?? ? ??? ?? (after(0) ??)"""
        self._load_recordings_async()  # ???? _build_plan_modified_cache() ??
        self._load_plans_async()

    def _build_plan_modified_cache(self):
        """플랜 파일의 modified 상태를 미리 캐시 (recording item에서 파일 I/O 방지)"""
        prev_cache = getattr(self, '_plan_modified_cache', {})
        prev_mtime = getattr(self, '_plan_mtime_cache', {})
        self._plan_modified_cache = {}
        self._plan_mtime_cache = {}
        if PLANS_DIR.exists():
            for plan_file in PLANS_DIR.glob("*.json"):
                try:
                    plan_id = plan_file.stem
                    mtime = os.path.getmtime(plan_file)
                    self._plan_mtime_cache[plan_id] = mtime
                    # mtime 변경 없으면 이전 캐시 값 재사용 (전체 JSON 파싱 방지)
                    if plan_id in prev_mtime and prev_mtime[plan_id] == mtime:
                        self._plan_modified_cache[plan_id] = prev_cache.get(plan_id, False)
                    else:
                        data = load_json_file(plan_file)
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
                        data = load_json_file(plan_file)
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
            self._analyzer_ui_post(lambda: self._apply_plans(plans, current_gen))

        threading.Thread(target=_load, daemon=True).start()

    def _apply_plans(self, plans, generation=None):
        """백그라운드에서 로드된 플랜을 UI에 적용"""
        # generation이 현재보다 오래된 경우 무시 (sync 로드가 이미 최신 데이터 적용)
        if generation is not None and generation < self._plans_load_generation:
            return

        if not plans:
            self._plan_items = []
            self._plans_scroll.pack_forget()
            self._plans_empty_label.pack(fill="both", expand=True, padx=10, pady=(0, 10))
            return

        # plan_id → locked 캐시 구축 (단일 쿼리로 전체 조회)
        self._plan_lock_cache = {}
        all_recordings = self._db.get_all_recordings()
        rec_by_plan = {r.automation_plan_id: r for r in all_recordings if r.automation_plan_id}
        for plan in plans:
            recording = rec_by_plan.get(plan.plan_id)
            self._plan_lock_cache[plan.plan_id] = recording.locked if recording else False

        self._plan_items = list(plans)
        self._plans_empty_label.pack_forget()
        self._plans_scroll.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self._plans_scroll.set_items(self._plan_items, preserve_scroll=True)

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

        # 좌상단: 분석된 재생 목록
        plans_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        plans_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 4), pady=(0, 4))
        self._setup_plans_card(plans_frame)

        # 우상단: 녹화 목록
        recordings_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        recordings_frame.grid(row=0, column=1, sticky="nsew", padx=(4, 0), pady=(0, 4))
        self._setup_recordings_card(recordings_frame)

        # 좌하단: 분석 실행
        analyze_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        analyze_frame.grid(row=1, column=0, sticky="nsew", padx=(0, 4), pady=(4, 0))
        self._setup_analyze_card(analyze_frame)

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
            text="+ 플랜추가",
            command=self._create_plan,
            style="primary",
            width=90,
            height=26,
        ).pack(side="left")

        self.create_button(
            header,
            text="새로고침",
            command=self._load_plans_async,
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
            corner_radius=IOS_METRICS["pill_radius"],
            command=self._cleanup_unused_images,
        ).pack(side="right", padx=(0, 8))

        self._plans_empty_label = ctk.CTkLabel(
            card,
            text="분석된 재생이 없습니다\n녹화를 분석하세요",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["text_muted"],
            justify="center",
        )

        self._plans_scroll = VirtualScrollFrame(
            card,
            item_height=56,
            buffer_count=5,
            fg_color=COLORS["bg_card"],
        )
        self._plans_scroll.set_render_callback(self._render_plan_item)
        self._plans_scroll.pack(fill="both", expand=True, padx=10, pady=(0, 10))

    def _load_plans(self):
        """분석된 재생 목록 로드"""
        self._load_plans_async()

    def _render_plan_item(self, parent, plan: AutomationPlan, index: int):
        return self._create_plan_item(plan, parent=parent)

    def _create_plan_item(self, plan: AutomationPlan, parent=None):
        """분석된 재생 항목 생성"""
        # 연관된 녹화의 잠금 상태 확인 (캐시 사용 — 루프 내 DB 쿼리 방지)
        is_locked = self._plan_lock_cache.get(plan.plan_id, False)

        item_wrapper = ctk.CTkFrame(
            parent or self._plans_scroll,
            fg_color="transparent",
            height=56,
        )
        item_wrapper.pack_propagate(False)
        if parent is None:
            item_wrapper.pack(fill="x", pady=0)

        item = ctk.CTkFrame(
            item_wrapper,
            fg_color=COLORS["bg_glass"],
            corner_radius=IOS_METRICS["control_radius"],
            height=46,
        )
        item.pack_propagate(False)
        item.pack(fill="x", pady=(2, 1), padx=0)
        ctk.CTkFrame(
            item_wrapper,
            height=2,
            fg_color=COLORS["accent"],
        ).pack(fill="x", padx=10, pady=(0, 2))

        content = ctk.CTkFrame(item, fg_color="transparent")
        content.pack(fill="x", padx=10, pady=8)

        # 잠금 아이콘 (잠겨있으면 표시)
        if is_locked:
            ctk.CTkLabel(
                content,
                text="🔒",
                font=ctk.CTkFont(size=14, weight="bold"),
                text_color=COLORS["danger_hover"],
            ).pack(side="left", padx=(0, 5))

        # 이름
        name_label = ctk.CTkLabel(
            content,
            text=truncate_ui_text(plan.name, 38),
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COLORS["text_primary"],
            anchor="w",
            width=190,
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
            hover_color=COLORS["danger_hover"],
            text_color=COLORS["text_on_accent"],
            font=ctk.CTkFont(size=11),
            corner_radius=IOS_METRICS["pill_radius"],
        ).pack(side="right")

        # 수정 버튼
        ctk.CTkButton(
            content,
            text="수정",
            command=lambda p=plan: self._edit_plan(p),
            width=40,
            height=20,
            fg_color=COLORS["accent_blue"],
            hover_color=COLORS["hover_blue"],
            text_color=COLORS["text_on_accent"],
            font=ctk.CTkFont(size=11),
            corner_radius=IOS_METRICS["pill_radius"],
        ).pack(side="right", padx=(0, 3))
        return item_wrapper

    def _edit_plan(self, plan: AutomationPlan):
        """재생 수정"""
        from .player_view import PlanDetailDialog
        dialog = PlanDetailDialog(self, plan)
        self.wait_window(dialog)
        self._load_recordings()  # 녹화 목록도 새로고침 (이름 동기화)
        self._load_plans()

    def _create_plan(self):
        """빈 플랜 생성 후 즉시 수정 다이얼로그 오픈"""
        from .player_view import PlanDetailDialog

        plan = AutomationPlan(
            name="새 플랜",
            description="분석된 재생 목록에서 직접 추가한 플랜",
            user_verified=True,
            modified=True,
        )
        dialog = PlanDetailDialog(self, plan)
        self.wait_window(dialog)
        self._load_recordings()
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
            command=self._load_recordings_async,
            style="ghost",
            width=70,
            height=26,
        ).pack(side="right")

        self._recordings_empty_label = ctk.CTkLabel(
            card,
            text="녹화 파일이 없습니다\n먼저 녹화를 진행하세요",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["text_muted"],
            justify="center",
        )

        self._recordings_scroll = VirtualScrollFrame(
            card,
            item_height=78,
            buffer_count=5,
            fg_color=COLORS["bg_card"],
        )
        self._recordings_scroll.set_render_callback(self._render_recording_item)
        self._recordings_scroll.pack(fill="both", expand=True, padx=10, pady=(0, 10))

    def _load_recordings(self):
        """녹화 목록 로드"""
        self._load_recordings_async()

    def _load_recordings_async(self):
        self._recordings_load_generation += 1
        current_gen = self._recordings_load_generation

        def _load():
            self._build_plan_modified_cache()
            recordings = self._db.get_all_recordings()
            self._analyzer_ui_post(lambda: self._apply_recordings(recordings, current_gen))

        threading.Thread(target=_load, daemon=True).start()

    def _apply_recordings(self, recordings, generation=None):
        if generation is not None and generation < self._recordings_load_generation:
            return

        self._recording_items = list(recordings)
        self._recording_index_by_id = {
            recording.id: idx for idx, recording in enumerate(self._recording_items) if recording.id is not None
        }

        if self._selected_recording_id not in self._recording_index_by_id:
            self._selected_recording_id = None
            self._selected_item_widget = None

        if not self._recording_items:
            self._recordings_scroll.pack_forget()
            self._recordings_empty_label.pack(fill="both", expand=True, padx=10, pady=(0, 10))
            return

        self._recordings_empty_label.pack_forget()
        self._recordings_scroll.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self._recordings_scroll.set_items(self._recording_items, preserve_scroll=True)

    def _render_recording_item(self, parent, recording: Recording, index: int):
        return self._create_recording_item(recording, parent=parent)

    def _create_recording_item(self, recording: Recording, parent=None):
        """녹화 항목 생성"""
        item = ctk.CTkFrame(
            parent or self._recordings_scroll,
            fg_color=COLORS["accent"] if recording.id == self._selected_recording_id else COLORS["bg_glass"],
            corner_radius=IOS_METRICS["control_radius"],
            cursor="hand2",
            height=72,
        )
        item.pack_propagate(False)
        if parent is None:
            item.pack(fill="x", pady=3)

        # 클릭으로 선택 기능
        def on_click(event, r=recording):
            self._select_recording_item(r)

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
                text_color=COLORS["danger_hover"],
                cursor="hand2",
            )
            lock_label.pack(side="left", padx=(0, 5))
            lock_label.bind("<Button-1>", on_click)

        name_label = ctk.CTkLabel(
            top_row,
            text=truncate_ui_text(recording.name, 38),
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COLORS["text_primary"],
            anchor="w",
            cursor="hand2",
            width=190,
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
            corner_radius=IOS_METRICS["pill_radius"],
        ).pack(side="right")

        # 잠금/해제 버튼
        lock_text = "🔓 해제" if recording.locked else "🔒 잠금"
        lock_color = COLORS["error"] if recording.locked else COLORS["bg_elevated"]
        ctk.CTkButton(
            btn_row,
            text=lock_text,
            command=lambda r=recording: self._toggle_recording_lock(r),
            width=70,
            height=28,
            fg_color=lock_color,
            hover_color=COLORS["danger_hover"] if recording.locked else COLORS["bg_card_hover"],
            text_color=COLORS["text_on_accent"] if recording.locked else COLORS["text_secondary"],
            font=ctk.CTkFont(size=13, weight="bold"),
            corner_radius=IOS_METRICS["pill_radius"],
        ).pack(side="right", padx=(0, 5))

        # 수정은 '분석된 재생 목록'에서만 가능 (녹화 목록에서는 수정 버튼 제거)
        return item

    def _select_recording_item(self, recording: Recording, widget=None):
        """녹화 항목 선택 (클릭으로)"""
        previous_id = self._selected_recording_id
        self._selected_recording_id = recording.id

        # 녹화 정보 설정
        self._selected_video = recording.video_path
        self._selected_input_log = recording.input_log_path
        self._plan_name_entry.delete(0, "end")
        self._plan_name_entry.insert(0, f"{recording.name}_자동화")
        self._selected_item_widget = widget

        if previous_id in self._recording_index_by_id:
            self._recordings_scroll.refresh_item(self._recording_index_by_id[previous_id])
        if recording.id in self._recording_index_by_id:
            self._recordings_scroll.refresh_item(self._recording_index_by_id[recording.id])

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
            data = load_json_file(plan_file)
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
            self._selected_recording_id = None

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
                self._progress_label.configure(text="녹화를 먼저 선택하세요", text_color=COLORS["warning_text"])
                return

            # 수정된 녹화는 재분석 불가
            try:
                recording = self._db.get_recording_by_video_path(self._selected_video)
                if recording and recording.automation_plan_id:
                    plan_file = PLANS_DIR / f"{recording.automation_plan_id}.json"
                    if plan_file.exists():
                        try:
                            plan_data = load_json_file(plan_file)
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
            self._progress_label.configure(text="취소됨", text_color=COLORS["warning_text"])
        except Exception as e:
            logger.error(f"취소 오류: {e}")

    def _on_progress(self, progress: AnalysisProgress):
        import time as _time
        now = _time.time()
        if not hasattr(self, '_last_progress_time') or now - self._last_progress_time >= 0.2 or progress.current >= progress.total:
            self._last_progress_time = now
            self._analyzer_ui_post(lambda: self._update_progress(progress))

    def _update_progress(self, progress: AnalysisProgress):
        self._progress_label.configure(text=truncate_ui_text(progress.message, 72))
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

            self._analyzer_ui_post(show_dialog_wrapper)

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
        self._analyzer_ui_post(lambda: self._show_analysis_result(plan))

    def _show_analysis_result(self, plan: Optional[AutomationPlan]):
        with self._analyze_lock:
            self._is_analyzing = False
        self._analyze_btn.configure(state="normal")
        self._cancel_btn.configure(state="disabled")

        if plan is not None:
            self._progress_label.configure(text="✅ 분석 완료", text_color=COLORS["success_text"])
            self._progress_bar.set(1)

            all_rules = plan.initial_rules + plan.monitoring_rules
            status = "✅ 승인됨" if plan.user_verified else "⏳ 미승인"
            status_color = COLORS["success"] if plan.user_verified else COLORS["warning"]

            self._result_status.configure(
                text=f"계획: {truncate_ui_text(plan.name, 52)}\n동작 수: {len(all_rules)}개\n상태: {status}",
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
                        data = load_json_file(plan_file)
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
                        self._analyzer_ui_post(_show_result)
                    except (tk.TclError, RuntimeError):
                        pass
                    logger.info(f"이미지 정리 완료: {deleted_count}개 삭제")

                threading.Thread(target=_do_delete, daemon=True).start()

            try:
                self._analyzer_ui_post(_confirm_and_delete)
            except (tk.TclError, RuntimeError):
                pass

        threading.Thread(target=_scan_and_cleanup, daemon=True).start()

    def refresh(self):
        """뷰 새로고침"""
        self._load_recordings()  # 내부에서 _build_plan_modified_cache() 호출
        self._load_plans_async()

    def cleanup(self):
        dispatcher = getattr(self, "_ui_dispatcher", None)
        if dispatcher is not None:
            dispatcher.close()
        if self._is_analyzing:
            self._video_analyzer.cancel()
