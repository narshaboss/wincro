"""
좌표 기반 맵핑 시스템 - Canvas 기반 그래픽 맵

이미지처럼 시각적으로 맵을 표시합니다.
"""

import tkinter as tk
from tkinter import ttk
from typing import Optional, Tuple, List, Set, Dict
import math
import threading
import logging

try:
    from .game_map import GameMap, TileInfo, DIRECTIONS_4
except ImportError:
    from game_map import GameMap, TileInfo, DIRECTIONS_4

logger = logging.getLogger(__name__)

# 이 타일 수를 초과하면 개별 타일 대신 간략화된 뷰 표시
_MAX_VISIBLE_TILES = 5000


class MapCanvas(tk.Canvas):
    """
    그래픽 맵 캔버스

    타일을 색깔 블록으로 표시하고 장애물은 벽으로 표시
    """

    # 색상 테마
    COLORS = {
        "background": "#e8eef4",      # 배경 (연한 회색-파랑)
        "explored": "#a8d4f0",         # 탐색됨 (연한 파랑)
        "unknown": "#d8e4ec",          # 미탐색 (회색)
        "wall": "#e05050",             # 벽/장애물 (빨간색)
        "wall_border": "#c03030",      # 벽 테두리 (진한 빨강)
        "soft_blocked": "#e8a040",     # 임시벽 (주황)
        "soft_blocked_border": "#c88030",  # 임시벽 테두리
        "player": "#2d5a87",           # 플레이어 (진한 파랑)
        "target": "#c44040",           # 목표 (빨강)
        "target_tile": "#50c878",      # 도착 좌표 타일 (초록)
        "target_tile_border": "#3a9a5c",  # 도착 좌표 테두리
        "start_tile": "#9b59b6",       # 출발 좌표 타일 (보라)
        "start_tile_border": "#7d3c98",  # 출발 좌표 테두리
        "path": "#90c870",             # 경로 (연두)
        "grid": "#c0d0dc",             # 그리드 선
        "patrol": "#1a1a1a",             # 순찰 좌표 (검정)
        "patrol_border": "#444444",      # 순찰 테두리 (진한 회색)
    }

    def __init__(self, master, game_map: GameMap, width: int = 600, height: int = 500, **kwargs):
        super().__init__(master, width=width, height=height,
                        bg=self.COLORS["background"], highlightthickness=0, **kwargs)

        self.game_map = game_map
        self.canvas_width = width
        self.canvas_height = height

        # 뷰 설정
        self.tile_size = 20  # 픽셀
        self.offset_x = 0
        self.offset_y = 0
        self.zoom = 1.0

        # 상태
        self.player_pos: Optional[Tuple[int, int]] = None
        self.target_pos: Optional[Tuple[int, int]] = None
        self.start_pos: Optional[Tuple[int, int]] = None
        self.path: List[Tuple[int, int]] = []

        # 편집 모드: None / "blocked" / "patrol" / "passable" / "soft_blocked" / "start" / "end"
        self.edit_tile_type = None
        self._on_tile_changed = None  # 타일 변경 시 콜백

        # 드래그 이동
        self._drag_start = None
        self._drag_moved = False
        self.bind("<ButtonPress-1>", self._on_drag_start)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_click)
        self.bind("<ButtonPress-3>", self._on_right_press)    # 우클릭: 타일 배치/삭제
        self.bind("<B3-Motion>", self._on_right_drag)          # 우클릭 드래그: 연속 배치/삭제
        self._right_drag_last = None  # 드래그 중 마지막 타일 좌표
        self.bind("<MouseWheel>", self._on_zoom)  # Windows
        self.bind("<Button-4>", self._on_zoom)    # Linux scroll up
        self.bind("<Button-5>", self._on_zoom)    # Linux scroll down

        # 좌표 표시 라벨
        self._coord_label = None

    def set_positions(self, player: Optional[Tuple[int, int]] = None,
                      target: Optional[Tuple[int, int]] = None,
                      path: Optional[List[Tuple[int, int]]] = None,
                      start: Optional[Tuple[int, int]] = None):
        """위치 설정"""
        self.player_pos = player
        self.target_pos = target
        self.start_pos = start
        self.path = path or []

    def center_on(self, x: int, y: int):
        """특정 좌표를 중앙에"""
        self.offset_x = self.canvas_width // 2 - int(x * self.tile_size * self.zoom)
        self.offset_y = self.canvas_height // 2 - int(y * self.tile_size * self.zoom)

    def _sync_size(self):
        """캔버스 실제 크기를 내부 변수에 반영"""
        w = self.winfo_width()
        h = self.winfo_height()
        if w > 1 and h > 1:
            self.canvas_width = w
            self.canvas_height = h

    def auto_fit(self):
        """맵 전체가 보이도록 자동 조절 (이상치 무시)"""
        self._sync_size()
        all_coords = list(self.game_map.passable | self.game_map.blocked | set(self.game_map.soft_blocked.keys()))
        if not all_coords:
            return

        xs = sorted(p[0] for p in all_coords)
        ys = sorted(p[1] for p in all_coords)
        n = len(xs)

        if n == 1:
            self.zoom = 1.0
            self.center_on(xs[0], ys[0])
            return

        # 이상치 제거: gap 기반 메인 클러스터 찾기
        min_x, max_x = self._find_main_range(xs)
        min_y, max_y = self._find_main_range(ys)

        map_width = max(max_x - min_x + 3, 5)
        map_height = max(max_y - min_y + 3, 5)

        # 줌 계산
        zoom_x = self.canvas_width / (map_width * self.tile_size)
        zoom_y = self.canvas_height / (map_height * self.tile_size)
        self.zoom = min(zoom_x, zoom_y, 2.0)  # 최대 2배
        self.zoom = max(self.zoom, 0.1)  # 최소 0.1배

        # 중앙 정렬
        center_x = (min_x + max_x) / 2
        center_y = (min_y + max_y) / 2
        self.center_on(center_x, center_y)

    @staticmethod
    def _find_main_range(sorted_vals):
        """정렬된 좌표에서 이상치를 제거하고 메인 범위 반환"""
        n = len(sorted_vals)
        if n <= 1:
            return sorted_vals[0], sorted_vals[0]

        total_span = sorted_vals[-1] - sorted_vals[0]

        # 범위가 합리적이면 상하위 2% 트림만
        if total_span <= 500:
            trim = max(1, n // 50) if n > 4 else 0
            return sorted_vals[trim], sorted_vals[n - 1 - trim]

        # 범위가 넓으면 → 가장 큰 gap으로 클러스터 분리
        best_gap = 0
        best_idx = 0
        for i in range(n - 1):
            gap = sorted_vals[i + 1] - sorted_vals[i]
            if gap > best_gap:
                best_gap = gap
                best_idx = i

        # gap이 전체 범위의 20% 이상이면 이상치로 판단
        if best_gap > total_span * 0.2:
            left = sorted_vals[:best_idx + 1]
            right = sorted_vals[best_idx + 1:]
            # 더 큰 클러스터 선택
            cluster = left if len(left) >= len(right) else right
            cn = len(cluster)
            trim = max(1, cn // 50) if cn > 4 else 0
            return cluster[trim], cluster[cn - 1 - trim]

        # gap이 작으면 전체 사용 (트림만)
        trim = max(1, n // 50) if n > 4 else 0
        return sorted_vals[trim], sorted_vals[n - 1 - trim]

    def render(self):
        """맵 렌더링"""
        # 스레드 안전: Tkinter는 메인 스레드에서만 호출 가능
        if threading.current_thread() is not threading.main_thread():
            logger.warning("render()가 메인 스레드가 아닌 곳에서 호출됨 — 무시")
            return

        self._sync_size()
        self.delete("all")

        # 스레드 안전 스냅샷 사용
        passable_snap = self.game_map.get_passable_snapshot()
        blocked_snap = self.game_map.get_blocked_snapshot()
        soft_blocked_snap = self.game_map.get_soft_blocked_snapshot()

        if not passable_snap and not blocked_snap and not soft_blocked_snap:
            self._draw_empty_message()
            return

        # 타일 크기 (줌 적용)
        ts = int(self.tile_size * self.zoom)
        if ts < 3:
            ts = 3

        # 화면에 보이는 범위 계산
        min_tile_x = int(-self.offset_x / ts) - 2
        max_tile_x = int((self.canvas_width - self.offset_x) / ts) + 2
        min_tile_y = int(-self.offset_y / ts) - 2
        max_tile_y = int((self.canvas_height - self.offset_y) / ts) + 2

        # 보이는 타일 수 추정 (대략적)
        total_tiles = len(passable_snap) + len(blocked_snap) + len(soft_blocked_snap)

        # 타일이 너무 많으면 간략화된 뷰 (경계 사각형만 표시)
        if total_tiles > _MAX_VISIBLE_TILES:
            self._draw_simplified_bounds(passable_snap, blocked_snap, soft_blocked_snap, ts)
            # 경로, 순찰, 출발/도착, 플레이어, 정보는 계속 표시
        else:
            # 경로 셋
            path_set = set(self.path) if self.path else set()

            # 1. 이동 가능 타일 그리기 (스냅샷 — 스레드 안전)
            for (tx, ty) in passable_snap:
                if not (min_tile_x <= tx <= max_tile_x and min_tile_y <= ty <= max_tile_y):
                    continue

                x1 = self.offset_x + tx * ts
                y1 = self.offset_y + ty * ts
                x2 = x1 + ts
                y2 = y1 + ts

                if (tx, ty) in path_set:
                    color = self.COLORS["path"]
                else:
                    color = self.COLORS["explored"]
                self.create_rectangle(x1, y1, x2, y2, fill=color, outline="")

            # 2. 벽(장애물) 타일 그리기 - 빨간 박스
            for (tx, ty) in blocked_snap:
                if not (min_tile_x <= tx <= max_tile_x and min_tile_y <= ty <= max_tile_y):
                    continue

                x1 = self.offset_x + tx * ts
                y1 = self.offset_y + ty * ts
                x2 = x1 + ts
                y2 = y1 + ts

                self.create_rectangle(x1, y1, x2, y2, fill=self.COLORS["wall"],
                                    outline=self.COLORS["wall_border"], width=2)

            # 2.5. 임시벽 타일 그리기 - 주황 박스 (스냅샷)
            for (tx, ty) in soft_blocked_snap:
                if not (min_tile_x <= tx <= max_tile_x and min_tile_y <= ty <= max_tile_y):
                    continue

                x1 = self.offset_x + tx * ts
                y1 = self.offset_y + ty * ts
                x2 = x1 + ts
                y2 = y1 + ts

                self.create_rectangle(x1, y1, x2, y2, fill=self.COLORS["soft_blocked"],
                                    outline=self.COLORS["soft_blocked_border"], width=1)

        # 2.7. 순찰 좌표 그리기 - 검정 박스 + 번호
        for idx, (tx, ty) in enumerate(self.game_map.patrol_points):
            if not (min_tile_x <= tx <= max_tile_x and min_tile_y <= ty <= max_tile_y):
                continue

            x1 = self.offset_x + tx * ts
            y1 = self.offset_y + ty * ts
            x2 = x1 + ts
            y2 = y1 + ts

            self.create_rectangle(x1, y1, x2, y2, fill=self.COLORS["patrol"],
                                outline=self.COLORS["patrol_border"], width=2)
            # 번호 표시 (줌이 충분할 때만)
            if ts >= 14:
                cx = (x1 + x2) // 2
                cy = (y1 + y2) // 2
                self.create_text(cx, cy, text=str(idx + 1),
                               fill="white", font=("맑은 고딕", max(7, ts // 3), "bold"))

        # 3. 경로 그리기
        if self.path and len(self.path) > 1:
            points = []
            for px, py in self.path:
                cx = self.offset_x + px * ts + ts // 2
                cy = self.offset_y + py * ts + ts // 2
                points.extend([cx, cy])
            if len(points) >= 4:
                self.create_line(points, fill=self.COLORS["path"],
                               width=max(2, int(3 * self.zoom)), smooth=True)

        # 4. 출발 좌표 타일 (보라색) - 외부 설정 또는 맵 저장값
        start = self.start_pos or self.game_map.start_pos
        if start:
            sx, sy = start
            x1 = self.offset_x + sx * ts
            y1 = self.offset_y + sy * ts
            x2 = x1 + ts
            y2 = y1 + ts
            self.create_rectangle(x1, y1, x2, y2, fill=self.COLORS["start_tile"],
                                outline=self.COLORS["start_tile_border"], width=2)

        # 5. 도착 좌표 타일 (초록색) - 외부 설정 또는 맵 저장값
        target = self.target_pos or self.game_map.end_pos
        if target:
            tx, ty = target
            x1 = self.offset_x + tx * ts
            y1 = self.offset_y + ty * ts
            x2 = x1 + ts
            y2 = y1 + ts
            self.create_rectangle(x1, y1, x2, y2, fill=self.COLORS["target_tile"],
                                outline=self.COLORS["target_tile_border"], width=2)

        # 6. 플레이어 아이콘
        if self.player_pos:
            px, py = self.player_pos
            cx = self.offset_x + px * ts + ts // 2
            cy = self.offset_y + py * ts + ts // 2
            r = max(6, int(10 * self.zoom))
            self._draw_player_icon(cx, cy, r)

        # 7. 정보 표시
        self._draw_info()

    def _draw_player_icon(self, cx: int, cy: int, r: int):
        """플레이어 아이콘 (집 모양)"""
        # 원형 배경
        self.create_oval(cx - r, cy - r, cx + r, cy + r,
                        fill=self.COLORS["player"], outline="white", width=2)
        # 집 아이콘
        hr = r * 0.5
        self.create_polygon(
            cx, cy - hr,           # 지붕 꼭대기
            cx - hr, cy,           # 왼쪽
            cx + hr, cy,           # 오른쪽
            fill="white", outline=""
        )
        self.create_rectangle(
            cx - hr * 0.6, cy,
            cx + hr * 0.6, cy + hr * 0.7,
            fill="white", outline=""
        )

    def _draw_target_icon(self, cx: int, cy: int, r: int):
        """목표 아이콘"""
        # 원형 배경
        self.create_oval(cx - r, cy - r, cx + r, cy + r,
                        fill=self.COLORS["target"], outline="white", width=2)
        # 별 또는 깃발
        hr = r * 0.5
        self.create_polygon(
            cx, cy - hr,
            cx + hr * 0.3, cy - hr * 0.2,
            cx + hr, cy - hr * 0.2,
            cx + hr * 0.4, cy + hr * 0.2,
            cx + hr * 0.6, cy + hr,
            cx, cy + hr * 0.4,
            cx - hr * 0.6, cy + hr,
            cx - hr * 0.4, cy + hr * 0.2,
            cx - hr, cy - hr * 0.2,
            cx - hr * 0.3, cy - hr * 0.2,
            fill="white", outline=""
        )

    def _draw_info(self):
        """정보 표시"""
        stats = self.game_map.get_statistics()

        soft_count = stats.get('soft_blocked_tiles', 0)
        patrol_count = len(self.game_map.patrol_points)
        info_lines = [
            f"탐색: {stats['explored_tiles']}칸",
            f"벽: {stats['blocked_tiles']}개",
        ]
        if soft_count > 0:
            info_lines.append(f"임시벽: {soft_count}개")
        if patrol_count > 0:
            info_lines.append(f"순찰: {patrol_count}개")

        if self.player_pos:
            info_lines.insert(0, f"현재: x{self.player_pos[0]} y{self.player_pos[1]}")

        if self.target_pos:
            info_lines.append(f"목표: x{self.target_pos[0]} y{self.target_pos[1]}")
            if self.player_pos:
                dist = abs(self.target_pos[0] - self.player_pos[0]) + \
                       abs(self.target_pos[1] - self.player_pos[1])
                info_lines.append(f"거리: {dist}칸")

        # 배경 박스
        padding = 8
        line_height = 18
        box_width = 120
        box_height = len(info_lines) * line_height + padding * 2

        x1, y1 = 10, 10
        x2, y2 = x1 + box_width, y1 + box_height

        self.create_rectangle(x1, y1, x2, y2,
                            fill="white", outline="#ccc", stipple="gray50")

        for i, line in enumerate(info_lines):
            self.create_text(x1 + padding, y1 + padding + i * line_height,
                           text=line, anchor="nw", font=("맑은 고딕", 9),
                           fill="#333")

    def _draw_simplified_bounds(self, passable_snap, blocked_snap, soft_blocked_snap, ts):
        """타일이 너무 많을 때 간략화된 경계 사각형만 표시"""
        all_coords = passable_snap | blocked_snap | set(soft_blocked_snap.keys())
        if not all_coords:
            return

        xs = [p[0] for p in all_coords]
        ys = [p[1] for p in all_coords]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)

        # 탐색 영역 경계 사각형
        x1 = self.offset_x + min_x * ts
        y1 = self.offset_y + min_y * ts
        x2 = self.offset_x + (max_x + 1) * ts
        y2 = self.offset_y + (max_y + 1) * ts

        self.create_rectangle(x1, y1, x2, y2,
                            fill=self.COLORS["explored"], outline=self.COLORS["grid"], width=2)

        # 벽 영역이 있으면 별도 경계
        if blocked_snap:
            bxs = [p[0] for p in blocked_snap]
            bys = [p[1] for p in blocked_snap]
            bx1 = self.offset_x + min(bxs) * ts
            by1 = self.offset_y + min(bys) * ts
            bx2 = self.offset_x + (max(bxs) + 1) * ts
            by2 = self.offset_y + (max(bys) + 1) * ts
            self.create_rectangle(bx1, by1, bx2, by2,
                                outline=self.COLORS["wall"], width=2, dash=(4, 4))

        total = len(passable_snap) + len(blocked_snap) + len(soft_blocked_snap)
        cx = (x1 + x2) // 2
        cy = (y1 + y2) // 2
        self.create_text(cx, cy,
                        text=f"타일 {total}개 (간략 뷰)\n줌인하면 상세 표시",
                        font=("맑은 고딕", 11), fill="#555", justify="center")

    def _draw_empty_message(self):
        """빈 맵 메시지"""
        self.create_text(
            self.canvas_width // 2, self.canvas_height // 2,
            text="맵 데이터가 없습니다\n\n맵핑을 시작하면\n여기에 지도가 표시됩니다",
            font=("맑은 고딕", 14), fill="#888", justify="center"
        )

    def _on_drag_start(self, event):
        """드래그 시작"""
        self._drag_start = (event.x, event.y)
        self._drag_moved = False

    def _on_drag(self, event):
        """드래그 중"""
        if self._drag_start:
            dx = event.x - self._drag_start[0]
            dy = event.y - self._drag_start[1]
            if abs(dx) > 3 or abs(dy) > 3:
                self._drag_moved = True
            self.offset_x += dx
            self.offset_y += dy
            self._drag_start = (event.x, event.y)
            self.render()

    def _on_click(self, event):
        """클릭 - 좌표 표시 / 벽 편집"""
        if self._drag_moved:
            return  # 드래그 중이면 무시

        # 클릭한 위치의 타일 좌표 계산
        ts = int(self.tile_size * self.zoom)
        if ts < 1:
            ts = 1

        tile_x = math.floor((event.x - self.offset_x) / ts)
        tile_y = math.floor((event.y - self.offset_y) / ts)

        pos = (tile_x, tile_y)

        # 편집 모드
        if self.edit_tile_type:
            self._place_tile(pos, self.edit_tile_type)
            self.render()
            self._show_coord_popup(event.x, event.y, tile_x, tile_y, f"→ {self.edit_tile_type}")
            if self._on_tile_changed:
                self._on_tile_changed()
            return

        # 타일 정보 확인
        if pos in self.game_map.passable:
            tile_type = "이동가능"
        elif pos in self.game_map.blocked:
            tile_type = "벽"
        elif pos in self.game_map.soft_blocked:
            count = self.game_map.soft_blocked[pos]
            tile_type = f"임시벽({count})"
        else:
            tile_type = "미탐색"

        # 좌표 표시
        self._show_coord_popup(event.x, event.y, tile_x, tile_y, tile_type)

    def _place_tile(self, pos, tile_type: str):
        """타일 배치"""
        if tile_type == "patrol":
            # 순찰은 오버레이 — passable 유지, 토글만
            if pos in self.game_map.patrol_points:
                self.game_map.patrol_points.remove(pos)
            else:
                self.game_map.patrol_points.append(pos)
            return

        if tile_type == "start":
            self.game_map.start_pos = pos
            return

        if tile_type == "end":
            self.game_map.end_pos = pos
            return

        # 일반 타일: 기존 제거 후 새 타입
        self.game_map.passable.discard(pos)
        self.game_map.blocked.discard(pos)
        self.game_map.soft_blocked.pop(pos, None)

        if tile_type == "passable":
            self.game_map.passable.add(pos)
        elif tile_type == "blocked":
            self.game_map.blocked.add(pos)
            # 벽으로 바꾸면 순찰에서도 제거
            if pos in self.game_map.patrol_points:
                self.game_map.patrol_points.remove(pos)
        elif tile_type == "soft_blocked":
            self.game_map.soft_blocked[pos] = 1

    def _get_tile_pos(self, event):
        """마우스 이벤트에서 타일 좌표 계산"""
        ts = int(self.tile_size * self.zoom)
        if ts < 1:
            ts = 1
        tile_x = math.floor((event.x - self.offset_x) / ts)
        tile_y = math.floor((event.y - self.offset_y) / ts)
        return (tile_x, tile_y)

    def _delete_tile(self, pos):
        """타일 삭제 (모든 타입)"""
        removed = False
        if pos in self.game_map.passable:
            self.game_map.passable.discard(pos)
            removed = True
        if pos in self.game_map.blocked:
            self.game_map.blocked.discard(pos)
            removed = True
        if pos in self.game_map.soft_blocked:
            self.game_map.soft_blocked.pop(pos, None)
            removed = True
        if pos in self.game_map.patrol_points:
            self.game_map.patrol_points.remove(pos)
            removed = True
        if self.game_map.start_pos == pos:
            self.game_map.start_pos = None
            removed = True
        if self.game_map.end_pos == pos:
            self.game_map.end_pos = None
            removed = True
        return removed

    def _on_right_press(self, event):
        """우클릭 - 박스 활성화 시에만 배치"""
        if not self.edit_tile_type:
            return
        pos = self._get_tile_pos(event)
        self._right_drag_last = pos
        self._place_tile(pos, self.edit_tile_type)
        self.render()
        self._show_coord_popup(event.x, event.y, pos[0], pos[1], f"→ {self.edit_tile_type}")
        if self._on_tile_changed:
            self._on_tile_changed()

    def _on_right_drag(self, event):
        """우클릭 드래그 - 박스 활성화 시에만 연속 배치"""
        if not self.edit_tile_type:
            return
        pos = self._get_tile_pos(event)
        if pos == self._right_drag_last:
            return
        self._right_drag_last = pos
        self._place_tile(pos, self.edit_tile_type)
        self.render()
        if self._on_tile_changed:
            self._on_tile_changed()
        else:
            if self._delete_tile(pos):
                self.render()
                if self._on_tile_changed:
                    self._on_tile_changed()

    def _show_coord_popup(self, screen_x: int, screen_y: int,
                          tile_x: int, tile_y: int, tile_type: str):
        """좌표 팝업 표시"""
        # 기존 팝업 제거
        self.delete("coord_popup")

        # 팝업 텍스트
        text = f"x{tile_x} y{tile_y}\n({tile_type})"

        # 배경 박스
        padding = 5
        bbox_x = screen_x + 10
        bbox_y = screen_y - 30

        # 텍스트 먼저 그려서 크기 측정
        text_id = self.create_text(bbox_x + padding, bbox_y + padding,
                                  text=text, anchor="nw",
                                  font=("맑은 고딕", 10, "bold"),
                                  fill="#333", tags="coord_popup")
        bbox = self.bbox(text_id)

        if bbox:
            # 배경 그리기
            self.create_rectangle(bbox[0] - padding, bbox[1] - padding,
                                bbox[2] + padding, bbox[3] + padding,
                                fill="white", outline="#666",
                                tags="coord_popup")
            # 텍스트를 맨 위로
            self.tag_raise(text_id)

        # 3초 후 자동 제거
        self.after(3000, lambda: self.delete("coord_popup"))

    def _on_zoom(self, event):
        """줌"""
        # 마우스 휠 방향 (Windows: event.delta ±120, macOS: ±1, Linux: Button-4/5)
        if event.num == 5:
            factor = 0.9  # Linux scroll down → 축소
        elif event.num == 4:
            factor = 1.1  # Linux scroll up → 확대
        elif event.delta < 0:
            factor = 0.9  # 축소
        else:
            factor = 1.1  # 확대

        old_zoom = self.zoom
        self.zoom *= factor
        self.zoom = max(0.2, min(3.0, self.zoom))  # 제한

        # 마우스 위치 기준 줌
        if old_zoom != self.zoom:
            mx, my = event.x, event.y
            self.offset_x = mx - (mx - self.offset_x) * (self.zoom / old_zoom)
            self.offset_y = my - (my - self.offset_y) * (self.zoom / old_zoom)
            self.render()


class MapWindow(tk.Toplevel):
    """맵 전용 창"""

    def __init__(self, master, game_map: GameMap, title: str = "맵 보기",
                 restore_callback=None, save_callback=None):
        super().__init__(master)
        self.title(f"🗺️ {title}")
        self.configure(bg="#f0f4f8")

        self.game_map = game_map
        self._restore_callback = restore_callback
        self._save_callback = save_callback

        # 맵 크기에 맞게 창 크기 자동 조절
        win_w, win_h = self._calc_window_size(game_map)
        self.geometry(f"{win_w}x{win_h}")
        # 화면 중앙 배치
        self.update_idletasks()
        sx = (self.winfo_screenwidth() - win_w) // 2
        sy = (self.winfo_screenheight() - win_h) // 2
        self.geometry(f"+{sx}+{sy}")

        # 상단 툴바
        toolbar = tk.Frame(self, bg="#f0f4f8")
        toolbar.pack(fill="x", padx=10, pady=5)

        tk.Button(toolbar, text="전체보기", command=self._fit_view,
                 bg="#5e81ac", fg="white", relief="flat", padx=8).pack(side="left", padx=2)
        tk.Button(toolbar, text="새로고침", command=self._refresh,
                 bg="#5e81ac", fg="white", relief="flat", padx=8).pack(side="left", padx=2)

        # 이상치 정리 버튼
        tk.Button(toolbar, text="정리", command=self._cleanup_outliers,
                 bg="#5e81ac", fg="white", relief="flat", padx=8).pack(side="left", padx=2)

        # 되돌리기 버튼 (콜백이 있을 때만)
        if restore_callback:
            tk.Button(toolbar, text="되돌리기", command=self._on_restore,
                     bg="#d08770", fg="white", relief="flat", padx=8).pack(side="left", padx=2)

        # 범례 (모두 클릭하면 해당 타일 편집 모드)
        legend = tk.Frame(self, bg="#f0f4f8")
        legend.pack(fill="x", padx=10)

        self._legend_items = {}
        for tile_type, color, label in [
            ("passable",     "#a8d4f0", "이동"),
            ("blocked",      "#e05050", "벽"),
            ("soft_blocked", "#e8a040", "임시"),
            ("patrol",       "#1a1a1a", "순찰"),
            ("start",        "#9b59b6", "출발"),
            ("end",          "#50c878", "도착"),
        ]:
            frame = self._add_legend(legend, color, label,
                                     command=lambda t=tile_type: self._set_edit_type(t))
            self._legend_items[tile_type] = frame

        # 맵 캔버스 (창 크기에 맞춤)
        canvas_w = max(400, win_w - 20)
        canvas_h = max(300, win_h - 70)
        self.map_canvas = MapCanvas(self, game_map, width=canvas_w, height=canvas_h)
        self.map_canvas.pack(padx=10, pady=(0, 10), fill="both", expand=True)
        self.map_canvas._on_tile_changed = self._on_tile_changed

        # 초기 렌더링 (레이아웃 확정 후 auto_fit)
        self.update_idletasks()
        self.map_canvas.auto_fit()
        self.map_canvas.render()

        # 창을 최상단으로 강제로 올리기 (CustomTkinter 뒤에 숨는 문제 방지)
        self.attributes('-topmost', True)
        self.after(200, lambda: self.attributes('-topmost', False))

    def _calc_window_size(self, game_map: GameMap):
        """맵 크기에 맞게 창 크기 계산 (타일당 최소 8px 목표)"""
        all_coords = list(game_map.passable | game_map.blocked | set(game_map.soft_blocked.keys()))
        if not all_coords:
            return 700, 550

        xs = [p[0] for p in all_coords]
        ys = [p[1] for p in all_coords]
        map_w = max(xs) - min(xs) + 3  # 여백 포함
        map_h = max(ys) - min(ys) + 3

        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        max_w = int(screen_w * 0.85)
        max_h = int(screen_h * 0.85)

        # 타일당 최소 8px 확보 목표
        toolbar_h = 70  # 툴바 + 범례 높이
        margin = 30
        ideal_w = map_w * 8 + margin
        ideal_h = map_h * 8 + toolbar_h + margin

        win_w = max(700, min(ideal_w, max_w))
        win_h = max(550, min(ideal_h, max_h))
        return win_w, win_h

    def _add_legend(self, parent, color: str, text: str, command=None):
        """범례 아이템 추가. command가 있으면 클릭 시 편집 모드 토글."""
        frame = tk.Frame(parent, bg="#f0f4f8")
        frame.pack(side="left", padx=5)
        box = tk.Canvas(frame, width=12, height=12, bg=color,
                 highlightthickness=1, highlightbackground="#999")
        box.pack(side="left")
        label = tk.Label(frame, text=text, bg="#f0f4f8", font=("맑은 고딕", 9))
        label.pack(side="left", padx=2)
        if command:
            for w in (frame, box, label):
                w.configure(cursor="hand2")
                w.bind("<Button-1>", lambda e, cmd=command: cmd())
        return frame

    def _fit_view(self):
        """전체 보기"""
        self.map_canvas.auto_fit()
        self.map_canvas.render()

    def _center_player(self):
        """플레이어 중심"""
        if self.map_canvas.player_pos:
            self.map_canvas.center_on(*self.map_canvas.player_pos)
            self.map_canvas.render()

    def _refresh(self):
        """새로고침"""
        self.map_canvas.render()

    def _set_edit_type(self, tile_type: str):
        """편집 타일 타입 설정 (토글)"""
        # 같은 타입 다시 클릭하면 해제
        if self.map_canvas.edit_tile_type == tile_type:
            self.map_canvas.edit_tile_type = None
        else:
            self.map_canvas.edit_tile_type = tile_type

        # 범례 하이라이트 갱신
        active = self.map_canvas.edit_tile_type
        for t, frame in self._legend_items.items():
            bg = "#ffe0a0" if t == active else "#f0f4f8"
            frame.configure(bg=bg)
            for child in frame.winfo_children():
                if isinstance(child, tk.Label):
                    child.configure(bg=bg, font=("맑은 고딕", 9, "bold" if t == active else ""))

        self.map_canvas.configure(cursor="crosshair" if active else "")

    def _cleanup_outliers(self):
        """이상치 자동 정리"""
        from tkinter import messagebox
        before = len(self.game_map.passable) + len(self.game_map.blocked) + len(self.game_map.soft_blocked)
        removed = self.game_map.cleanup_outliers()
        if removed > 0:
            self.map_canvas.auto_fit()
            self.map_canvas.render()
            if self._save_callback:
                self._save_callback()
            messagebox.showinfo("정리 완료", f"{removed}개 이상치 타일 제거됨\n(전체 {before}개 → {before - removed}개)")
        else:
            messagebox.showinfo("정리", "이상치가 없습니다.")

    def _on_tile_changed(self):
        """타일 변경 시 저장"""
        if self._save_callback:
            self._save_callback()

    def _on_restore(self):
        """되돌리기 실행"""
        if self._restore_callback:
            self._restore_callback()
            self.map_canvas.auto_fit()
            self.map_canvas.render()

    def update_positions(self, player: Optional[Tuple[int, int]] = None,
                        target: Optional[Tuple[int, int]] = None,
                        path: Optional[List[Tuple[int, int]]] = None,
                        start: Optional[Tuple[int, int]] = None):
        """위치 업데이트"""
        self.map_canvas.set_positions(player, target, path, start)
        self.map_canvas.render()
