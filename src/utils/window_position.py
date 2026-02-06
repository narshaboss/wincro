"""
WinCro 창 위치 관리 모듈

각 창(다이얼로그)의 위치를 저장하고 복원합니다.
"""

import json
import tkinter as tk
from pathlib import Path
from typing import Optional, Tuple
import threading

from .config import DATA_DIR
from .logger import get_logger

logger = get_logger(__name__)

# 창 위치 저장 파일
POSITIONS_FILE = DATA_DIR / "window_positions.json"
_positions_lock = threading.RLock()
_positions_cache: Optional[dict] = None


def _load_positions() -> dict:
    """위치 데이터 로드 (스레드 안전)"""
    global _positions_cache

    # 락 내에서 캐시 체크 및 로드 (레이스 컨디션 방지)
    with _positions_lock:
        if _positions_cache is not None:
            return _positions_cache

        try:
            if POSITIONS_FILE.exists():
                with open(POSITIONS_FILE, 'r', encoding='utf-8') as f:
                    _positions_cache = json.load(f)
            else:
                _positions_cache = {}
        except (json.JSONDecodeError, IOError):
            _positions_cache = {}

        return _positions_cache


def _save_positions(positions: dict) -> bool:
    """위치 데이터 저장 (호출자가 _positions_lock을 이미 획득한 상태여야 함)"""
    global _positions_cache
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(POSITIONS_FILE, 'w', encoding='utf-8') as f:
            json.dump(positions, f, ensure_ascii=False, indent=2)
        _positions_cache = positions
        return True
    except IOError as e:
        logger.error(f"창 위치 저장 실패: {e}")
        return False


def get_window_position(window_id: str) -> Optional[Tuple[int, int]]:
    """
    저장된 창 위치 가져오기

    Args:
        window_id: 창 식별자 (예: "PlanDetailDialog", "ImageCropDialog")

    Returns:
        (x, y) 튜플 또는 None
    """
    # _load_positions()가 이미 락을 사용하므로 중복 락 제거
    positions = _load_positions()
    pos = positions.get(window_id)
    if pos and isinstance(pos, dict):
        x, y = pos.get("x"), pos.get("y")
        if x is not None and y is not None:
            return (int(x), int(y))
    return None


def save_window_position(window_id: str, x: int, y: int) -> bool:
    """
    창 위치 저장

    Args:
        window_id: 창 식별자
        x: X 좌표
        y: Y 좌표

    Returns:
        저장 성공 여부
    """
    with _positions_lock:
        positions = _load_positions()
        positions[window_id] = {"x": x, "y": y}
        return _save_positions(positions)


def apply_window_position(window, window_id: str) -> bool:
    """
    창에 저장된 위치 적용

    Args:
        window: Tkinter 창 객체 (Toplevel, CTkToplevel 등)
        window_id: 창 식별자

    Returns:
        위치 적용 성공 여부
    """
    pos = get_window_position(window_id)
    if pos:
        x, y = pos
        try:
            # 화면 범위 체크
            screen_width = window.winfo_screenwidth()
            screen_height = window.winfo_screenheight()

            # 창이 화면 밖으로 나가지 않도록 조정
            x = max(0, min(x, screen_width - 100))
            y = max(0, min(y, screen_height - 100))

            window.geometry(f"+{x}+{y}")
            return True
        except Exception as e:
            logger.warning(f"창 위치 적용 실패 [{window_id}]: {e}")
    return False


def bind_window_position_save(window, window_id: str):
    """
    창 이동 시 자동 저장 바인딩

    Args:
        window: Tkinter 창 객체
        window_id: 창 식별자
    """
    def on_configure(event):
        # 창이 표시된 후에만 저장 (초기 위치 설정 제외)
        if hasattr(window, '_position_initialized') and window._position_initialized:
            try:
                x = window.winfo_x()
                y = window.winfo_y()
                # 유효한 위치인 경우만 저장
                if x >= 0 and y >= 0:
                    save_window_position(window_id, x, y)
            except (tk.TclError, RuntimeError, OSError):
                pass

    def mark_initialized():
        window._position_initialized = True

    window._position_initialized = False
    window.bind("<Configure>", on_configure)
    # 약간의 지연 후 초기화 완료 표시 (초기 위치 설정이 끝난 후)
    window.after(500, mark_initialized)


def setup_window_position(window, window_id: str):
    """
    창 위치 관리 설정 (위치 복원 + 자동 저장)

    Args:
        window: Tkinter 창 객체
        window_id: 창 식별자

    사용 예:
        dialog = MyDialog()
        setup_window_position(dialog, "MyDialog")
    """
    # 저장된 위치 적용
    apply_window_position(window, window_id)

    # 이동 시 자동 저장
    bind_window_position_save(window, window_id)
