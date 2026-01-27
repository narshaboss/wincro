"""
WinCro 가상 스크롤 프레임

보이는 항목만 렌더링하여 대량의 리스트를 효율적으로 표시합니다.
"""

import customtkinter as ctk
import tkinter as tk

from .theme import COLORS


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
            except (tk.TclError, RuntimeError, AttributeError):
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
            except (tk.TclError, RuntimeError, AttributeError):
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
        except (tk.TclError, RuntimeError, AttributeError):
            pass

    def scroll_to_item(self, index: int):
        """특정 항목으로 스크롤"""
        if not self._items:
            return
        total_height = len(self._items) * self._item_height
        y_pos = index * self._item_height
        self._canvas.yview_moveto(y_pos / total_height)
        self._schedule_render()
