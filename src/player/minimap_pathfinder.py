"""
미니맵 기반 경로 탐색 모듈

미니맵 이미지를 분석하여 A* 알고리즘으로 최적 경로를 계산하고,
강물 등 장애물을 우회하는 경유지를 자동 생성합니다.
"""

import heapq
import math
import time
from typing import List, Tuple, Optional
from dataclasses import dataclass

import cv2
import numpy as np

from ..utils.logger import get_logger

logger = get_logger(__name__)


class AStarPathfinder:
    """
    A* 최단경로 알고리즘

    8방향 이동을 지원하며, Euclidean distance를 휴리스틱으로 사용합니다.
    """

    # 8방향 이동 (상하좌우 + 대각선)
    DIRECTIONS = [
        (0, -1),   # 상
        (0, 1),    # 하
        (-1, 0),   # 좌
        (1, 0),    # 우
        (-1, -1),  # 좌상
        (1, -1),   # 우상
        (-1, 1),   # 좌하
        (1, 1),    # 우하
    ]

    def __init__(self, obstacle_grid: np.ndarray):
        """
        Args:
            obstacle_grid: 2D numpy array (0=이동불가, 1=이동가능)
        """
        self.grid = obstacle_grid
        self.height, self.width = obstacle_grid.shape

    def _heuristic(self, a: Tuple[int, int], b: Tuple[int, int]) -> float:
        """Euclidean distance 휴리스틱"""
        return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2)

    def _is_valid(self, x: int, y: int) -> bool:
        """좌표가 유효하고 이동 가능한지 확인"""
        if 0 <= x < self.width and 0 <= y < self.height:
            return self.grid[y, x] == 1
        return False

    def find_path(self, start: Tuple[int, int], goal: Tuple[int, int],
                  max_iterations: int = 5000) -> List[Tuple[int, int]]:
        """
        A* 알고리즘으로 최단 경로 탐색

        Args:
            start: 시작 좌표 (x, y)
            goal: 목표 좌표 (x, y)
            max_iterations: 최대 반복 횟수 (무한 루프 방지)

        Returns:
            경로 좌표 리스트 [(x1,y1), (x2,y2), ...] 또는 빈 리스트
        """
        # 시작/목표 좌표가 유효하지 않으면 빈 경로 반환
        if not self._is_valid(start[0], start[1]):
            logger.warning(f"[A*] 시작점이 이동 불가: {start}")
            # 시작점이 장애물인 경우 주변 가장 가까운 이동 가능 지점 찾기
            start = self._find_nearest_walkable(start)
            if start is None:
                return []

        if not self._is_valid(goal[0], goal[1]):
            logger.warning(f"[A*] 목표점이 이동 불가: {goal}")
            # 목표점이 장애물인 경우 주변 가장 가까운 이동 가능 지점 찾기
            goal = self._find_nearest_walkable(goal)
            if goal is None:
                return []

        # 이미 도착한 경우
        if start == goal:
            return [start]

        # 우선순위 큐: (f_score, counter, (x, y))
        counter = 0
        open_set = []
        heapq.heappush(open_set, (0, counter, start))

        # 이미 방문한 노드
        came_from = {}
        g_score = {start: 0}
        f_score = {start: self._heuristic(start, goal)}

        iterations = 0
        while open_set:
            iterations += 1
            if iterations % 200 == 0:
                time.sleep(0)  # GIL 해제
            if iterations > max_iterations:
                logger.warning(f"[A*] 최대 반복 초과 ({max_iterations}): {start} → {goal}")
                return []

            _, _, current = heapq.heappop(open_set)

            if current == goal:
                # 경로 재구성
                path = [current]
                while current in came_from:
                    current = came_from[current]
                    path.append(current)
                path.reverse()
                return path

            for dx, dy in self.DIRECTIONS:
                neighbor = (current[0] + dx, current[1] + dy)

                if not self._is_valid(neighbor[0], neighbor[1]):
                    continue

                # 대각선 이동 비용은 √2
                move_cost = 1.414 if dx != 0 and dy != 0 else 1.0
                tentative_g = g_score[current] + move_cost

                if neighbor not in g_score or tentative_g < g_score[neighbor]:
                    came_from[neighbor] = current
                    g_score[neighbor] = tentative_g
                    f = tentative_g + self._heuristic(neighbor, goal)
                    f_score[neighbor] = f
                    counter += 1
                    heapq.heappush(open_set, (f, counter, neighbor))

        logger.warning(f"[A*] 경로를 찾을 수 없음: {start} → {goal}")
        return []

    def _find_nearest_walkable(self, pos: Tuple[int, int], max_search: int = 20) -> Optional[Tuple[int, int]]:
        """주변에서 가장 가까운 이동 가능 지점 찾기 (BFS)"""
        from collections import deque
        x, y = pos
        visited = {(x, y)}
        queue = deque([(x, y, 0)])

        while queue:
            cx, cy, dist = queue.popleft()
            if dist > max_search:
                break
            # 시작점이 아닌 유효한 타일을 찾으면 반환
            if (cx, cy) != (x, y) and self._is_valid(cx, cy):
                return (cx, cy)
            for dx, dy in self.DIRECTIONS:
                nx, ny = cx + dx, cy + dy
                if (nx, ny) not in visited and 0 <= nx < self.width and 0 <= ny < self.height:
                    visited.add((nx, ny))
                    queue.append((nx, ny, dist + 1))

        return None


def find_destination_in_minimap(minimap_image: np.ndarray,
                                 dest_template: np.ndarray,
                                 confidence: float = 0.7) -> Optional[Tuple[int, int]]:
    """
    미니맵에서 목적지 이미지 찾기 (템플릿 매칭)

    Args:
        minimap_image: 미니맵 이미지 (BGR)
        dest_template: 목적지 템플릿 이미지 (BGR)
        confidence: 매칭 신뢰도 (0~1)

    Returns:
        (x, y) 중심 좌표 또는 None
    """
    try:
        result = cv2.matchTemplate(minimap_image, dest_template, cv2.TM_CCOEFF_NORMED)
        min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)

        if max_val >= confidence:
            h, w = dest_template.shape[:2]
            cx = max_loc[0] + w // 2
            cy = max_loc[1] + h // 2
            logger.debug(f"[목적지] 템플릿 매칭: ({cx}, {cy}), 신뢰도={max_val:.3f}")
            return (cx, cy)

        logger.debug(f"[목적지] 템플릿 매칭 실패: 최대 신뢰도={max_val:.3f} < {confidence}")
        return None

    except Exception as e:
        logger.error(f"[목적지] 템플릿 매칭 오류: {e}")
        return None


class MinimapPathfinder:
    """
    미니맵 기반 경로 탐색

    미니맵 이미지를 분석하여 이동 가능/불가 영역을 판별하고,
    A* 알고리즘으로 경로를 계산합니다.
    """

    def __init__(self,
                 walkable_hsv_lower: List[int] = None,
                 walkable_hsv_upper: List[int] = None,
                 obstacle_hsv_lower: List[int] = None,
                 obstacle_hsv_upper: List[int] = None):
        """
        Args:
            walkable_hsv_lower: 이동 가능 영역 HSV 하한 [H, S, V]
            walkable_hsv_upper: 이동 가능 영역 HSV 상한 [H, S, V]
            obstacle_hsv_lower: 장애물 영역 HSV 하한 [H, S, V]
            obstacle_hsv_upper: 장애물 영역 HSV 상한 [H, S, V]
        """
        self.walkable_hsv_lower = np.array(walkable_hsv_lower or [20, 30, 30])
        self.walkable_hsv_upper = np.array(walkable_hsv_upper or [90, 255, 255])
        self.obstacle_hsv_lower = np.array(obstacle_hsv_lower or [100, 50, 50])
        self.obstacle_hsv_upper = np.array(obstacle_hsv_upper or [130, 255, 255])

        self._minimap_image: Optional[np.ndarray] = None
        self._obstacle_grid: Optional[np.ndarray] = None

    def load_minimap(self, image_path: str) -> bool:
        """
        미니맵 이미지 로드

        Args:
            image_path: 이미지 파일 경로

        Returns:
            로드 성공 여부
        """
        try:
            img_array = np.fromfile(image_path, np.uint8)
            self._minimap_image = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
            if self._minimap_image is None:
                logger.error(f"[미니맵] 이미지 로드 실패: {image_path}")
                return False
            logger.info(f"[미니맵] 이미지 로드: {image_path} "
                        f"({self._minimap_image.shape[1]}x{self._minimap_image.shape[0]})")
            return True
        except Exception as e:
            logger.error(f"[미니맵] 이미지 로드 오류: {e}")
            return False

    def get_minimap_image(self) -> Optional[np.ndarray]:
        """로드된 미니맵 이미지 반환"""
        return self._minimap_image

    def analyze_terrain(self) -> Optional[np.ndarray]:
        """
        미니맵 이미지의 지형 분석

        HSV 색상을 기반으로 이동 가능/불가 영역을 구분합니다.

        Returns:
            장애물 맵 (0=이동불가, 1=이동가능) 또는 None
        """
        if self._minimap_image is None:
            logger.error("[미니맵] 이미지가 로드되지 않음")
            return None

        # BGR → HSV 변환
        hsv = cv2.cvtColor(self._minimap_image, cv2.COLOR_BGR2HSV)

        # 이동 가능 영역 마스크 (초록/갈색 등)
        walkable_mask = cv2.inRange(hsv, self.walkable_hsv_lower, self.walkable_hsv_upper)

        # 장애물 영역 마스크 (파란색 = 물)
        obstacle_mask = cv2.inRange(hsv, self.obstacle_hsv_lower, self.obstacle_hsv_upper)

        # 최종 이동 가능 맵: 이동 가능 영역이고 장애물이 아닌 곳
        # walkable이면 1, obstacle이면 0
        grid = np.ones(hsv.shape[:2], dtype=np.uint8)

        # 장애물 영역을 0으로
        grid[obstacle_mask > 0] = 0

        # walkable 영역 밖도 0으로 (선택적)
        # grid[walkable_mask == 0] = 0

        self._obstacle_grid = grid
        walkable_count = np.sum(grid == 1)
        total = grid.shape[0] * grid.shape[1]
        logger.info(f"[미니맵] 지형 분석 완료: "
                    f"이동가능 {walkable_count}/{total} ({100*walkable_count/total:.1f}%)")

        return grid

    def find_path_pixels(self, start_pixel: Tuple[int, int],
                         goal_pixel: Tuple[int, int]) -> List[Tuple[int, int]]:
        """
        미니맵 픽셀 좌표로 A* 경로 탐색

        Args:
            start_pixel: 시작 픽셀 좌표 (x, y)
            goal_pixel: 목표 픽셀 좌표 (x, y)

        Returns:
            경로 픽셀 좌표 리스트
        """
        if self._obstacle_grid is None:
            logger.error("[미니맵] 지형 분석이 필요합니다")
            return []

        pathfinder = AStarPathfinder(self._obstacle_grid)
        path = pathfinder.find_path(start_pixel, goal_pixel)

        if path:
            logger.info(f"[미니맵] 경로 탐색 완료: {len(path)}개 포인트")
        else:
            logger.warning("[미니맵] 경로를 찾을 수 없습니다")

        return path

    def draw_path_on_minimap(self, path_pixels: List[Tuple[int, int]],
                              current_pixel: Tuple[int, int] = None,
                              goal_pixel: Tuple[int, int] = None) -> Optional[np.ndarray]:
        """
        미니맵 이미지 위에 경로 그리기 (미리보기용)

        Args:
            path_pixels: 경로 픽셀 좌표
            current_pixel: 현재 위치 (옵션)
            goal_pixel: 목표 위치 (옵션)

        Returns:
            경로가 그려진 이미지
        """
        if self._minimap_image is None:
            return None

        # 이미지 복사
        result = self._minimap_image.copy()

        # 경로 그리기 (빨간 선)
        if len(path_pixels) >= 2:
            for i in range(len(path_pixels) - 1):
                pt1 = path_pixels[i]
                pt2 = path_pixels[i + 1]
                cv2.line(result, pt1, pt2, (0, 0, 255), 2)  # BGR: 빨강

        # 현재 위치 표시 (노란 원)
        if current_pixel:
            cv2.circle(result, current_pixel, 5, (0, 255, 255), -1)  # BGR: 노랑

        # 목표 위치 표시 (초록 원)
        if goal_pixel:
            cv2.circle(result, goal_pixel, 5, (0, 255, 0), -1)  # BGR: 초록

        return result

    def draw_obstacle_map(self) -> Optional[np.ndarray]:
        """
        장애물 맵 시각화 (디버그용)

        Returns:
            시각화된 이미지 (이동가능=초록, 불가=빨강)
        """
        if self._obstacle_grid is None:
            return None

        # 3채널 이미지 생성
        h, w = self._obstacle_grid.shape
        result = np.zeros((h, w, 3), dtype=np.uint8)

        # 이동 가능 = 초록, 불가 = 빨강
        result[self._obstacle_grid == 1] = [0, 200, 0]   # 초록
        result[self._obstacle_grid == 0] = [0, 0, 200]   # 빨강

        return result


def detect_yellow_dot(image: np.ndarray,
                      hsv_lower: List[int] = None,
                      hsv_upper: List[int] = None) -> Optional[Tuple[int, int]]:
    """
    미니맵에서 노란점(현재 위치) 자동 감지

    Args:
        image: BGR 이미지 (미니맵 캡처)
        hsv_lower: 노란색 HSV 하한 [H, S, V]
        hsv_upper: 노란색 HSV 상한 [H, S, V]

    Returns:
        (x, y) 픽셀 좌표 또는 None
    """
    if hsv_lower is None:
        hsv_lower = [20, 150, 150]
    if hsv_upper is None:
        hsv_upper = [35, 255, 255]

    try:
        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, np.array(hsv_lower), np.array(hsv_upper))

        # 가장 큰 노란 영역의 중심점 찾기
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if contours:
            largest = max(contours, key=cv2.contourArea)
            M = cv2.moments(largest)
            if M["m00"] > 0:
                cx = int(M["m10"] / M["m00"])
                cy = int(M["m01"] / M["m00"])
                logger.debug(f"[노란점] 감지됨: ({cx}, {cy})")
                return (cx, cy)

        logger.debug("[노란점] 감지 실패")
        return None

    except Exception as e:
        logger.error(f"[노란점] 감지 오류: {e}")
        return None


class MinimapNavigator:
    """
    미니맵 기반 실시간 네비게이션

    게임 화면에서 미니맵을 캡처하고, 노란점(현재 위치)을 자동 감지하여
    목적지까지 A* 경로를 따라 이동합니다.
    """

    # 8방향 이름 매핑
    DIRECTION_NAMES = {
        (0, -1): "up",
        (0, 1): "down",
        (-1, 0): "left",
        (1, 0): "right",
        (-1, -1): "up_left",
        (1, -1): "up_right",
        (-1, 1): "down_left",
        (1, 1): "down_right",
    }

    def __init__(self,
                 minimap_image_path: str,
                 minimap_region: List[int],
                 obstacle_hsv_lower: List[int] = None,
                 obstacle_hsv_upper: List[int] = None,
                 yellow_hsv_lower: List[int] = None,
                 yellow_hsv_upper: List[int] = None):
        """
        Args:
            minimap_image_path: 미니맵 이미지 파일 경로 (장애물 분석용)
            minimap_region: 게임 화면에서 미니맵 영역 [x1, y1, x2, y2]
            obstacle_hsv_lower: 장애물 HSV 하한
            obstacle_hsv_upper: 장애물 HSV 상한
            yellow_hsv_lower: 노란점 HSV 하한
            yellow_hsv_upper: 노란점 HSV 상한
        """
        self.minimap_region = minimap_region
        self.yellow_hsv_lower = yellow_hsv_lower or [20, 150, 150]
        self.yellow_hsv_upper = yellow_hsv_upper or [35, 255, 255]

        # 경로 탐색기 초기화
        self._pathfinder = MinimapPathfinder(
            obstacle_hsv_lower=obstacle_hsv_lower,
            obstacle_hsv_upper=obstacle_hsv_upper,
        )

        # 미니맵 이미지 로드 및 지형 분석
        self._minimap_loaded = False
        if minimap_image_path:
            if self._pathfinder.load_minimap(minimap_image_path):
                self._pathfinder.analyze_terrain()
                self._minimap_loaded = True

        # 현재 경로 캐시
        self._current_path: List[Tuple[int, int]] = []
        self._path_index = 0
        self._last_position: Optional[Tuple[int, int]] = None

    def capture_minimap(self) -> Optional[np.ndarray]:
        """
        게임 화면에서 미니맵 영역 캡처

        Returns:
            미니맵 이미지 (BGR) 또는 None
        """
        try:
            import pyautogui
            x1, y1, x2, y2 = self.minimap_region
            width = x2 - x1
            height = y2 - y1

            screenshot = pyautogui.screenshot(region=(x1, y1, width, height))
            # PIL Image → numpy array (RGB → BGR)
            img = np.array(screenshot)
            img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
            return img

        except Exception as e:
            logger.error(f"[미니맵] 캡처 오류: {e}")
            return None

    def get_current_position(self) -> Optional[Tuple[int, int]]:
        """
        현재 노란점 위치 반환

        Returns:
            (x, y) 미니맵 픽셀 좌표 또는 None
        """
        screenshot = self.capture_minimap()
        if screenshot is None:
            return None

        return detect_yellow_dot(
            screenshot,
            hsv_lower=self.yellow_hsv_lower,
            hsv_upper=self.yellow_hsv_upper
        )

    def calculate_path(self, current_pos: Tuple[int, int],
                       destination: Tuple[int, int]) -> List[Tuple[int, int]]:
        """
        현재 위치에서 목적지까지 A* 경로 계산

        Args:
            current_pos: 현재 위치 (미니맵 픽셀)
            destination: 목적지 (미니맵 픽셀)

        Returns:
            경로 좌표 리스트
        """
        if not self._minimap_loaded:
            logger.warning("[네비] 미니맵이 로드되지 않음 - 직선 경로 사용")
            return [current_pos, destination]

        path = self._pathfinder.find_path_pixels(current_pos, destination)
        if not path:
            logger.warning("[네비] 경로를 찾을 수 없음 - 직선 경로 사용")
            return [current_pos, destination]

        return path

    def get_next_direction(self, current_pos: Tuple[int, int],
                           destination: Tuple[int, int]) -> Optional[str]:
        """
        다음 이동 방향 계산

        Args:
            current_pos: 현재 위치 (미니맵 픽셀)
            destination: 최종 목적지 (미니맵 픽셀)

        Returns:
            방향 문자열: "up", "down", "left", "right",
                        "up_left", "up_right", "down_left", "down_right"
                        또는 None (도착 또는 오류)
        """
        # 경로가 없거나 위치가 크게 변했으면 재계산
        if (not self._current_path or
            self._last_position is None or
            self._distance(current_pos, self._last_position) > 20):
            self._current_path = self.calculate_path(current_pos, destination)
            self._path_index = 0
            logger.info(f"[네비] 경로 재계산: {len(self._current_path)}개 포인트")

        self._last_position = current_pos

        # 다음 경로 포인트 찾기 (현재 위치와 가까운 포인트는 스킵)
        while self._path_index < len(self._current_path):
            next_point = self._current_path[self._path_index]
            if self._distance(current_pos, next_point) > 3:
                break
            self._path_index += 1

        if self._path_index >= len(self._current_path):
            # 경로 끝 도달 - 목적지로 직접 향함
            next_point = destination
        else:
            next_point = self._current_path[self._path_index]

        # 방향 계산
        dx = next_point[0] - current_pos[0]
        dy = next_point[1] - current_pos[1]

        # 방향 정규화
        if abs(dx) > abs(dy) * 2:
            dy = 0
        elif abs(dy) > abs(dx) * 2:
            dx = 0

        dir_x = 1 if dx > 0 else (-1 if dx < 0 else 0)
        dir_y = 1 if dy > 0 else (-1 if dy < 0 else 0)

        direction = self.DIRECTION_NAMES.get((dir_x, dir_y))
        if direction:
            logger.debug(f"[네비] 방향: {direction} (현재: {current_pos}, 다음: {next_point})")

        return direction

    def is_arrived(self, current_pos: Tuple[int, int],
                   destination: Tuple[int, int],
                   threshold: int = 10) -> bool:
        """
        목적지 도착 여부 확인

        Args:
            current_pos: 현재 위치 (미니맵 픽셀)
            destination: 목적지 (미니맵 픽셀)
            threshold: 도착 판정 거리 (픽셀)

        Returns:
            도착 여부
        """
        distance = self._distance(current_pos, destination)
        arrived = distance < threshold
        if arrived:
            logger.info(f"[네비] 목적지 도착! 거리: {distance:.1f}px")
        return arrived

    def _distance(self, p1: Tuple[int, int], p2: Tuple[int, int]) -> float:
        """두 점 사이의 거리 계산"""
        return math.sqrt((p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2)

    def get_path_preview(self, current_pos: Tuple[int, int],
                         destination: Tuple[int, int]) -> Optional[np.ndarray]:
        """
        경로 미리보기 이미지 생성

        Args:
            current_pos: 현재 위치
            destination: 목적지

        Returns:
            경로가 그려진 미니맵 이미지
        """
        if not self._minimap_loaded:
            return None

        path = self.calculate_path(current_pos, destination)
        return self._pathfinder.draw_path_on_minimap(path, current_pos, destination)

    def find_destination(self, dest_image_path: str, confidence: float = 0.7) -> Optional[Tuple[int, int]]:
        """
        목적지 이미지를 미니맵에서 찾아서 좌표 반환

        Args:
            dest_image_path: 목적지 템플릿 이미지 경로
            confidence: 매칭 신뢰도 (0~1)

        Returns:
            (x, y) 미니맵 픽셀 좌표 또는 None
        """
        if not self._minimap_loaded:
            logger.error("[네비] 미니맵이 로드되지 않음")
            return None

        minimap_img = self._pathfinder.get_minimap_image()
        if minimap_img is None:
            return None

        try:
            img_array = np.fromfile(dest_image_path, np.uint8)
            dest_template = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
            if dest_template is None:
                logger.error(f"[네비] 목적지 이미지 로드 실패: {dest_image_path}")
                return None

            return find_destination_in_minimap(minimap_img, dest_template, confidence)

        except Exception as e:
            logger.error(f"[네비] 목적지 찾기 오류: {e}")
            return None

    def capture_and_detect(self, dest_template: np.ndarray = None,
                            confidence: float = 0.7) -> Tuple[Optional[Tuple[int, int]], Optional[Tuple[int, int]]]:
        """
        게임에서 미니맵 캡처 → 노란점, 목적지 위치 반환

        Args:
            dest_template: 목적지 템플릿 이미지 (없으면 노란점만 감지)
            confidence: 템플릿 매칭 신뢰도

        Returns:
            (yellow_pos, dest_pos) 튜플
            - yellow_pos: 노란점 위치 또는 None
            - dest_pos: 목적지 위치 또는 None
        """
        screenshot = self.capture_minimap()
        if screenshot is None:
            return (None, None)

        # 노란점 감지
        yellow_pos = detect_yellow_dot(
            screenshot,
            hsv_lower=self.yellow_hsv_lower,
            hsv_upper=self.yellow_hsv_upper
        )

        # 목적지 감지 (템플릿이 있는 경우)
        dest_pos = None
        if dest_template is not None:
            dest_pos = find_destination_in_minimap(screenshot, dest_template, confidence)

        return (yellow_pos, dest_pos)

    def set_cached_path(self, path: List[Tuple[int, int]]):
        """경로 캐시 설정 (미리 계산된 경로 사용)"""
        self._current_path = path
        self._path_index = 0
        self._last_position = None
        logger.info(f"[네비] 캐시된 경로 설정: {len(path)}개 포인트")

    def get_pathfinder(self) -> MinimapPathfinder:
        """내부 경로 탐색기 반환"""
        return self._pathfinder
