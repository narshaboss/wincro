# WinCro 개발 진행 상태

## 현재 상태: 10단계 완료 (개발 완료!)

## 단계별 진행 현황

| 단계 | 내용 | 상태 | 완료 시간 |
|------|------|------|----------|
| 1 | 프로젝트 기본 구조 생성 | ✅ 완료 | 2026-01-16 |
| 2 | 유틸리티 모듈 개발 | ✅ 완료 | 2026-01-16 |
| 3 | 데이터베이스 모듈 개발 | ✅ 완료 | 2026-01-16 |
| 4 | 화면 녹화 모듈 개발 | ✅ 완료 | 2026-01-16 |
| 5 | 영상 분석 모듈 개발 | ✅ 완료 | 2026-01-16 |
| 6 | 동작 재현 모듈 개발 | ✅ 완료 | 2026-01-16 |
| 7 | GUI 개발 | ✅ 완료 | 2026-01-16 |
| 8 | 메인 앱 통합 | ✅ 완료 | 2026-01-16 |
| 9 | 테스트 및 검증 | ✅ 완료 | 2026-01-16 |
| 10 | 문서화 및 마무리 | ✅ 완료 | 2026-01-16 |

## 상태 범례
- ⏳ 대기: 아직 시작 안 함
- 🔄 진행중: 현재 작업 중
- ✅ 완료: 작업 완료
- ❌ 실패: 에러 발생 (아래 에러 로그 참조)
- ⚠️ 부분완료: 일부만 완료

## 현재 진행 중인 작업

없음 (모든 단계 완료)

## 다음 해야 할 작업

없음 (개발 완료)

## 완료된 파일 목록

### 1단계 - 프로젝트 기본 구조 생성
- requirements.txt (의존성 목록)
- src/__init__.py
- src/recorder/__init__.py
- src/analyzer/__init__.py
- src/player/__init__.py
- src/database/__init__.py
- src/ui/__init__.py
- src/utils/__init__.py
- src/i18n/__init__.py
- data/recordings/.gitkeep
- data/templates/.gitkeep
- data/sequences/.gitkeep
- logs/.gitkeep
- tests/__init__.py

### 2단계 - 유틸리티 모듈 개발
- src/utils/config.py (설정 관리)
- src/utils/security.py (암호화/보안)
- src/utils/logger.py (로깅 설정)
- src/i18n/ko.py (한글 번역)

### 3단계 - 데이터베이스 모듈 개발
- src/database/models.py (데이터 모델)
- src/database/db_manager.py (DB 관리자)

### 4단계 - 화면 녹화 모듈 개발
- src/recorder/screen_recorder.py (화면 캡처)
- src/recorder/input_logger.py (입력 이벤트 로깅)

### 5단계 - 영상 분석 모듈 개발
- src/analyzer/template_matcher.py (이미지 템플릿 매칭)
- src/analyzer/action_extractor.py (액션 추출)
- src/analyzer/video_analyzer.py (영상 분석)

### 6단계 - 동작 재현 모듈 개발
- src/player/action_player.py (액션 실행)
- src/player/ai_intervention.py (AI 변수 대응)

### 7단계 - GUI 개발
- src/ui/main_window.py (메인 윈도우)
- src/ui/recorder_view.py (녹화 화면)
- src/ui/analyzer_view.py (분석 화면)
- src/ui/player_view.py (실행 화면)
- src/ui/settings_view.py (설정 화면)

### 8단계 - 메인 앱 통합
- src/main.py (앱 진입점)
- src/app.py (메인 앱 클래스)

### 9단계 - 테스트 및 검증
- tests/test_recorder.py (녹화 모듈 테스트)
- tests/test_analyzer.py (분석 모듈 테스트)
- tests/test_player.py (동작 재현 모듈 테스트)

### 10단계 - 문서화 및 마무리
- README.md (프로젝트 설명)
- .env.example (환경변수 예시)

## 에러 로그

없음

## 버그 수정 이력

### 2026-03-17: ? ?? ??? + ??/??? ?? ??? + ?? ?? ?? (v1.0.142)

- **? ?? ??? ???:** `player_view`? `rule_executor` ?? ?? ??? ? ?? ?? ?? ??? ??? ???, ? OCR ???? ?? ?? ??/??? ???? ??? ??
- **start_pos ??? ?? ??:** fresh map ?? ??? `load()`? ???? `GameMap.load_and_merge()`? `start_pos` ??? ??? ??? ?? ?? ??? ??? ??? ?? ??
- **????/??? ?? ??:** ????? `game_mode` ??? ?????/???? ??? ?? ??, ??, ????, ??/???? ?? ??? ??
- **?? ?? ??:** AI ??? ?? 20???? ????, ?? ?? ?? ? ??? 1??? ?? ???? ??? ?? ??
- **???? ??/?? ??:** ??-?? ??? ?? ?? ??, ? ?? ??, ??/?? ??? ?? ? ??? ?? ??? ??
- **APP_VERSION:** `1.0.141` ? `1.0.142`
- **?? ??:** `src/ui/player_view.py`, `src/player/rule_executor.py`, `src/player/game_map.py`, `src/ui/main_window.py`, `src/player/map_canvas.py`, `data/plans/plan_20260205_000742.json`, `data/maps/*_boss_map.json`

---

### 2026-03-11: 맵 오염 차단 + 포탈 이탈 중단 + 저장 안정화 (v1.0.140)

- **좌표 오염 필터 추가:** 맵이 일정 크기 이상 쌓인 뒤 현재 맵 클러스터에서 과도하게 먼 좌표(예: 포탈 점프 좌표)가 `passable/blocked/soft_blocked`로 섞이지 않도록 sanity 필터 추가
- **맵 저장 원자화:** 저장 전 `passable/blocked/soft_blocked`를 sanitize 하고, 임시 파일 저장 후 교체하도록 변경해 저장 중간 상태/헤더 손상 완화
- **맵 로드/병합 정리:** 오염 좌표가 남아 있는 기존 맵도 load/load_and_merge 단계에서 정리 후 메모리에 반영
- **출발 포탈 보호 강화:** 전체맵핑 중 출발 포탈 보호 타일을 탐색 후보, 경로 계산, 되돌아가기 후보에서 일관되게 제외
- **맵핑 중 포탈 이탈 즉시 중단:** 포탈 점프가 감지되면 진입 방향만 벽으로 등록하고 현재 테스트를 즉시 중단해 다른 굴 좌표가 현재 맵에 저장되지 않도록 수정
- **출발지 교정 오인 처리 방지:** 보호 포탈을 밟아 생긴 점프를 `출발지 교정`으로 삼키지 않고 별도 포탈 이탈로 처리
- **벽 검증 집중화:** `probe_focus_target`을 도입해 의심 타일을 3회 기준으로 끝까지 검증하고, 중간에 다른 프런티어나 미탐색으로 새지 않도록 유지/복귀 로직 추가
- **APP_VERSION:** `1.0.139` → `1.0.140`
- **수정 파일:** `src/player/game_map.py`, `src/ui/player_view.py`, `src/utils/config.py`

### 2026-03-05: 맵핑 (0,0) 좌표 버그 + 특화모드 복사 수정 (v1.0.139)

- **route_starts/ends/walls falsy-zero 필터**: `(int(x)!=0 or int(y)!=0)` 조건이 유효한 (0,0) 좌표 제거 → `is not None`만 체크
- **도착지 벽 등록 → 맵핑 불가**: 맵핑테스트에서 도착지를 mark_blocked → 인접 unknown 프론티어 감지 불가 → passable + portal_protected로 변경 (3곳)
- **프론티어 자기위치 제외**: `_find_nearest_frontier` BFS가 현재 위치를 프론티어 후보에서 제외 → 유일한 프론티어일 때 즉시 완료 → 조건 제거
- **맵핑 기록 미저장 (boss segment)**: `_boss_segment_active`와 `skip_save`가 (0,0) 경유지를 보스로 판정 → 맵핑테스트에서 auto-save 차단 → `not _is_mapping_test` 조건 추가 (6곳)
- **entry tile blocked + start_pos 보호**: 전체맵핑 시 entry tile을 blocked+start_pos 설정 → A*/BFS 경유 불가 → passable + start_pos=None으로 변경 (2곳)
- **특화모드 복사 시 맵 미복사 + 잠금 미해제**: `_setup_copied_game_mode_maps` 헬퍼 추가 — 원본 맵 파일을 map_file로 참조 + map_locked 해제

### 2026-03-03: v1.0.137 정밀분석 + 맵핑테스트 버그 수정 (v1.0.138)

**v1.0.137 정밀분석 수정 (8파일 36건):**
- **[CRITICAL] 붙여넣기 AttributeError**: Action vs AutomationRule isinstance 분기 (player_view.py)
- **보스방 GIL 누락 6곳**: match_binary 직후 `time.sleep(0)` 추가
- **approaching→patrolling 상태 누수**: 6개 변수 초기화
- **self.after 무가드 14곳**: `_ui_update_ok` 가드 적용
- **모니터 스레드 TclError**: try/except 래핑
- **배치렌더 타이머 미취소**: _on_close에서 after_cancel
- **pyautogui.PAUSE 경쟁조건**: 전체 try 블록을 락 안으로 (rule_executor.py)
- **rule_executor GIL/sleep/path_pos_index/stop_event/벽등록/탈출스킬** 6건 수정
- **game_map falsy-zero 3곳**: save/load/merge에서 `is not None`
- **enhanced_matcher 좌표역산 오류**: bottom/right -offset
- **main_window GUILogHandler 누수**: _on_close에서 핸들러 제거
- **analyzer_view 진행콜백 큐폭주**: 200ms 쓰로틀링
- **action_player match_binary 미통일**: TM_CCOEFF_NORMED 직접 매칭
- **is_fully_explored soft_blocked 미포함**: soft_blocked도 탐색완료 판정에 포함
- **continue 전 sleep 추가 3곳** + **self.after 가드 7곳 추가** (player_view.py)

**맵핑테스트 버그 수정:**
- **포탈 감지 시 실행중지 → 탐색계속**: 벽 등록 후 continue (2곳)
- **포탈 continue 후 prev_x/prev_y 미갱신 → 무한 재감지**: prev 갱신 추가
- **_local_explore_phase: full_mapping_exploring = True 잔존 3곳**: 제거 (근처맵핑≠전체맵핑)
- **_mt_has_map 조건으로 is_boss_dungeon=False → 프론티어 탐색 건너뜀**: full_mapping_exploring 직접 사용
- **프론티어 반경 불일치 → 진동**: _find_nearest_frontier에 explore_center/explore_radius 필터 추가
- **max_iterations 5000 제한**: 맵핑테스트 50000으로 증가
- **보스구간 is_boss_dungeon 영구 True → 도착 불가**: _mt_explore_done 플래그 추가
- **보스구간 (0,0) 도착지 착각**: 전체맵핑 완료 후 보스구간 즉시 다음 경유지/완료 처리
- **Phase 1→2 전환 시 last_dir/stuck_count 미리셋**: 추가

**부분실행 game_mode RuleExecutor 라우팅 수정:**
- game_mode 규칙 포함 시 _run_remaining_rules 경유 (GameModeDialog 사용, route_ends 지원)

**UI 개선:**
- GameModeDialog 창 깜빡임 방지 (화면 밖 배치 후 이동)
- 붙여넣기 시 하위 규칙 자동 접기
- 특화모드 이름 수정 시 GameModeConfig.name 동기화

**수정된 파일:**
- `src/ui/player_view.py` - 맵핑테스트 + 정밀분석 수정
- `src/player/rule_executor.py` - GIL/sleep/경쟁조건 수정
- `src/player/game_map.py` - falsy-zero 수정
- `src/player/simple_pathfinder.py` - A* GIL 해제
- `src/player/map_patroller.py` - nearest 순서 수정
- `src/player/action_player.py` - match_binary 통일
- `src/player/obstacle_avoidance.py` - pathfind_mode 누수 수정
- `src/player/minimap_pathfinder.py` - max_iterations 전달
- `src/player/map_explorer.py` - soft_blocked 탐색완료 포함
- `src/analyzer/enhanced_matcher.py` - 좌표역산 수정
- `src/ui/main_window.py` - GUILogHandler 누수 수정
- `src/ui/analyzer_view.py` - 진행콜백 쓰로틀링

---

### 2026-02-25: 다중 특화모드 + 맵/경로 시스템 대규모 개선 (v1.0.137)

**다중 특화모드 지원:**
- `automation_models.py`: `game_mode` 단일 → `game_modes: Dict[str, GameModeConfig]` 다중 지원
- 구버전 `game_mode` JSON 자동 마이그레이션 (하위 호환)
- `rule_executor.py`: rule_id 기반 config 조회로 변경

**GameModeConfig 신규 필드:**
- `auto_skill_cd_region`: 쿨타임 이미지 검색 영역
- `move_skill_cooldown`, `auto_skill_cooldown`: 스킬 쿨타임 (초)
- 경유지별 `character_image` 직렬화/역직렬화 추가

**맵 시스템 개선:**
- `game_map.py`: start_pos 보호 강화 (mark_passable에서 출발지 등록 차단)
- `game_map.py`: `allow_promote` 파라미터 (부분실행/전체테스트에서 영구벽 승격 방지)
- `game_map.py`: soft_blocked 비용 50→20 조정, 미탐색 타일 unknown_cost 파라미터 추가
- `game_map.py`: `get_walkable_neighbors`에 `allow_soft_blocked` 파라미터 추가
- `game_map.py`: `is_fully_explored`에서 soft_blocked 제외 (임시 장애물은 탐색 완료 판정에서 제외)
- `game_map.py`: `load_and_merge`에서 start_pos → blocked 보장
- `game_map.py`: `_find_main_cluster` 빈 리스트 방어

**경로탐색 개선:**
- `simple_pathfinder.py`: `allow_soft_blocked`, `unknown_cost` 파라미터 전파
- `simple_pathfinder.py`: 인접 목표 벽 도달 허용 (포탈 벽 등록된 경우 대응)
- `simple_pathfinder.py`: `set_goal` 변경 시에만 경로 초기화, `invalidate_path()` 메서드 추가
- `simple_pathfinder.py`: `get_next_direction`에 `stop_event`, `max_iterations` 전달

**맵 캔버스 개선:**
- `map_canvas.py`: 경로 출발/도착/벽 좌표 렌더링 (route_starts, route_ends, route_walls)
- `map_canvas.py`: 좌클릭 드래그 편집 모드, delete 모드, start/end 토글
- `map_canvas.py`: 우클릭 드래그 제거 (좌클릭 드래그로 통일)

**순찰 시스템 개선:**
- `map_patroller.py`: reset 시 현재 위치 기준 가장 가까운 포인트부터 재순찰
- `map_patroller.py`: 목표 도달 판정 ±1타일 허용
- `map_patroller.py`: 래핑 탐색 (끝까지 간 후 처음부터도 탐색)

**rule_executor 개선:**
- 맵 파일명 `{seg_idx:02d}_{seg_name}_map.json` 형식 통일 + 구버전 마이그레이션
- 공유 맵 파일 지원 (`map_file` 경유지 설정)
- ESC 핫키 ID 기반 제거 (다른 핫키 보호)
- `_smooth_key_input` 예외 시 키 해제 보장

**기타:**
- `constants.py`: 경유지 클립보드 (복사/붙여넣기)
- `analyzer_view.py`: 마스크 자동 생성 제거
- `digit_templates.py`: 겹침 판정 80%→50%로 완화, 스크린샷 캐시 추가
- `player_view.py`: 대규모 UI/로직 개선 (+4694/-839줄)

**수정된 파일 (10개):**
- `src/analyzer/automation_models.py`
- `src/player/game_map.py`
- `src/player/map_canvas.py`
- `src/player/map_patroller.py`
- `src/player/rule_executor.py`
- `src/player/simple_pathfinder.py`
- `src/ui/analyzer_view.py`
- `src/ui/constants.py`
- `src/ui/player_view.py`
- `src/utils/digit_templates.py`

---

### 2026-02-10: 모니터링 액션 로그 강화 (v1.0.136)

**모니터링 액션 실행 추적 로그 개선**

1. **모니터링 액션 실행 시작 로그**: debug→info 레벨 승격 + 워치 이미지명 표시
2. **개별 액션 실행 전 로그 추가**: 액션 타입 + 대상(이미지명/키) 표시
3. **성공/실패 로그 상세화**: 결과값(클릭 좌표, 키 이름 등) 포함

### 2026-02-10: 모니터링 교착 버그 수정 + 안전장치 (v1.0.135)

**전체재생 시 모니터링 최종타겟 이미지 인식 불가 버그 수정 (3건)**

1. **`monitoring_start` NameError 크래시**: 모니터링 타임아웃 제거 시 변수 정의도 삭제됨 → 10초 후 NameError 크래시. 변수 정의 복구
2. **`final_ever_absent=False` 교착 상태**: 전체재생에서 이전 액션 후 final_image가 화면에 이미 존재 → "사라질 때까지 무한 대기" → 영원히 종료 불가. 15초 grace period 후 강제 해제
3. **`pyautogui.FAILSAFE` 크래시**: 마우스가 화면 모서리 이동 시 fail-safe 에러. `FAILSAFE=False`로 변경 (rule_executor.py 2곳, action_player.py 1곳)
4. **모니터링 안전 타임아웃 제거**: 1시간 제한 타임아웃 제거 (stop_event로만 중지)

### 2026-02-10: 코드 중복 제거 및 최적화 (v1.0.134)

**전체 코드베이스 점검 후 중복 코드 제거 및 성능 최적화. 순 267줄 감소.**

**rule_executor.py 최적화:**
- `_radius_to_region` 헬퍼 추출 → search_radius 변환 코드 5곳 중복 제거
- `gc.collect()` 매 이미지 검색마다 호출 → 제거 (`del`만 유지)
- `pyautogui.size()` → `_get_screen_size_cached()` 캐시 활용
- `ImageGrab.grab()`으로 화면 크기만 얻는 낭비 제거
- `_find_all_images_on_screen` RGB→BGR→GRAY 이중 변환 → 단일 변환
- 미사용 코드 삭제: `EXECUTION_TIMEOUT`, `_monitor_loop`, `_check_trigger`
- 중복 import 제거: `import pyautogui`, `from PIL import ImageGrab`

**player_view.py 최적화:**
- `convert_to_monitor_action` 3중 복제 → `_convert_action_to_monitor_dict` 통합 (~93줄 감소)
- `collect_all_actions` 3중 복제 → constants.py import 활용

**기타 파일 정리:**
- action_player.py: gc.collect 제거, 미사용 변수/import 삭제
- input_controller.py: 빈 함수 `_release_mouse_capture` + 호출부 삭제, 미사용 ctypes 삭제
- constants.py: `click_type_map` 2중 정의 → 1회로 통합
- recorder_view.py: dead code 3개 메서드 삭제, 중복 핸들러 통합
- admin.py, updater.py, video_analyzer.py: 미사용 import 삭제

**수정된 파일:** rule_executor.py, player_view.py, action_player.py, constants.py, recorder_view.py, input_controller.py, admin.py, updater.py, video_analyzer.py, config.py

### 2026-02-10: 모니터링 즉시 종료 버그 수정 (v1.0.133)

**문제:** 모니터링 액션 시작 시 최종 타겟 이미지가 이미 화면에 있어서 즉시 종료됨. 이전 액션이 모니터링의 target_image를 next_target_image로 기다려서, 모니터링 진입 시 이미 존재.

**수정 (3건):**
- **rule_executor.py**: 다음 액션이 모니터링이면 next_target_image 체크 건너뛰기 (target_image는 종료 조건이므로)
- **rule_executor.py**: 모니터링 시작 시 최종 이미지 이미 존재하면, 사라졌다 재출현할 때만 종료 (final_ever_absent 안전장치)
- **rule_executor.py**: 감시 이미지 좌표 계산 falsy-zero 버그 수정 (`or` → `is None` 체크, 2곳)

**수정된 파일:**
- `src/player/rule_executor.py`
- `src/utils/config.py` - APP_VERSION 1.0.132 → 1.0.133

### 2026-02-10: 액션 전환/트리거/모니터링 흐름 버그 수정 (v1.0.132)

**문제:** 액션 간 전환, 트리거 대기, 부분실행 등에서 런타임 버그 7건 발견.

**수정 (7건):**
- **rule_executor.py**: `skip_on_not_found=True` + `wait_after=0`일 때 무한대기 → 즉시 스킵으로 수정
- **rule_executor.py**: `_wait_for_trigger` 시간 드리프트 (waited += interval → wall-clock 시간)
- **rule_executor.py**: `_wait_for_trigger` 로그 하드코딩 "무제한" → timeout 반영
- **rule_executor.py**: `_wait_for_trigger` 부동소수점 modulo → last_log_time 추적
- **rule_executor.py**: `_wait_for_image` 일시정지(pause) 체크 누락 추가
- **rule_executor.py**: `scroll_amount=0` 무의미 스크롤 성공 반환 → 에러 반환
- **rule_executor.py**: 모니터링 첫 루프에서 최종 이미지 즉시 감지 → skip_first_final_check 추가
- 데드코드 제거 (next_image 타임아웃 분기, execution_failed 미사용 변수)

**수정된 파일:**
- `src/player/rule_executor.py`
- `src/utils/config.py` - APP_VERSION 1.0.131 → 1.0.132

---

### 2026-02-09: v1.0.129~130 수정 검증 및 잔존 버그 수정 (v1.0.131)

**문제:** v1.0.129~130 대규모 수정 후 런타임 버그 8건 잔존.

**수정 (8건):**
- **rule_executor.py**: skip_timeout=0일 때 `remaining` 음수 로그 수정, 무제한 대기 시 "최대 0초" → "(무제한)" 표시
- **action_player.py**: `_wait_for_image`에서 `elapsed` 미정의 NameError 수정
- **map_canvas.py**: `auto_fit()`, `_calc_window_size()`, `_cleanup_outliers()` 3곳 스레드 안전 스냅샷 미적용 → `get_all_snapshots()`/`get_statistics()` 사용
- **player_view.py**: `_remove_waypoint()`에서 `final_btn` KeyError 수정 (가드 체크 추가)
- **screen_recorder.py**: `_loop_dxcam()` mss 폴백 시 `_pause_lock` 데드락 수정 (락 해제 후 호출)

**수정된 파일:**
- `src/player/rule_executor.py`, `src/player/action_player.py`, `src/player/map_canvas.py`
- `src/ui/player_view.py`, `src/recorder/screen_recorder.py`
- `src/utils/config.py` - APP_VERSION 1.0.130 → 1.0.131

---

### 2026-02-09: 이미지 타임아웃 제거 + 이미지 매칭 오탐 수정 (v1.0.130)

**수정 1 — 이미지 타임아웃 전부 제거:**
- **의도**: 이미지가 안 나타나도 무한 대기 (stop_event로만 중단)
- `rule_executor.py`: EXECUTION_TIMEOUT=0, 트리거/다음화면/target 이미지 검색 전부 무제한
- `action_player.py`: target 이미지 타임아웃 제거, `_wait_for_image` 무제한

**수정 2 — 이미지 매칭 TM_CCOEFF_NORMED로 복원:**
- **문제**: v1.0.111에서 `_find_image_on_screen`이 `match_binary`(TM_SQDIFF_NORMED + Otsu mask)로 변경됨 → 금색 버튼 템플릿이 비슷한 색의 UI 요소에 89% 오탐
- **수정**: `_find_image_on_screen`을 `TM_CCOEFF_NORMED` 직접 매칭으로 변경 (v1.0.108 방식 복원)
- 3개 메서드 전부 `TM_CCOEFF_NORMED`: `_find_image_on_screen`, `_wait_for_trigger`, `_find_all_images_on_screen`

**수정된 파일:**
- `src/player/rule_executor.py` - 이미지 타임아웃 제거 + TM_CCOEFF_NORMED 복원
- `src/player/action_player.py` - 이미지 타임아웃 제거
- `src/utils/config.py` - APP_VERSION 1.0.129 → 1.0.130

---

### 2026-02-09: 전체 프리징/행 방지 대규모 최적화 (v1.0.129)

**문제:** 15개 파일에서 UI 프리징, 스레드 교착, 타이트 루프, 무한 대기 등 31건의 성능/안정성 이슈 발견.

**수정 (31건):**
- **game_map.py**: RLock 추가로 스레드 안전성 확보 (mark_passable/blocked/soft_blocked, save/load 전부 보호)
- **player_view.py**: undefined move_delay 수정, continue 전 sleep 12곳 추가, _find_nearest_frontier BFS 최적화, UI 업데이트 rate-limit, 락 타임아웃 축소 (5→2초, 3→1초), bare except 4곳 로깅 추가, _on_close sleep 축소
- **action_player.py**: 일시정지 루프 탈출 불가 수정, _wait_for_image 타임아웃 체크, 스크린샷 로깅 경량화
- **rule_executor.py**: 모니터링 기본 타임아웃 4시간→1시간, 스크린샷 스레드 누수 Semaphore(3) 제한, 파일체크 스레드→직접호출
- **minimap_pathfinder.py**: A* max_iterations=5000 제한, _find_nearest_walkable BFS 최적화
- **obstacle_avoidance.py**: BFS max_iterations=10000, DirectionMemory 1000개 초과 강제 정리
- **map_explorer.py**: _backtrack() max_iterations=200 + 스택 클리어
- **map_canvas.py**: 5000타일 초과 간소화 렌더링, 스레드 안전 스냅샷, 비메인스레드 차단
- **enhanced_matcher.py**: 스크린 전처리 캐시 (shape+mean)
- **updater.py**: SSL 폴백 전체 타임아웃 15초 캡, 다운로드 300→60초
- **settings_view.py**: 버전체크 개별 20→8초 + 전체 20초 캡, 펌웨어체크 sleep 축소
- **analyzer_view.py**: 위젯 파괴 O(N²)→O(N), JSON mtime 캐시, DB 쿼리 N→1 배치화
- **recorder_view.py**: 폴링 30→10초, 적응형 인터벌
- **screen_recorder.py**: _pause_lock으로 레이스 컨디션 해결
- **db_manager.py**: SQLite timeout 30→10초
- **window_position.py**: configure 이벤트 500ms 디바운스
- **arduino_uploader.py**: 긴 작업 전 로그 + AVR 타임아웃 300→180초

---

### 2026-02-09: 조건 대기 중 최종 이미지로 모니터링 탈출하는 버그 수정 (v1.0.128)

**문제:** 모니터링에서 조건 이미지가 아직 있는 상태(점프 차단)인데, 최종 이미지가 화면에 나타나면 모니터링을 종료해버림. 점프가 실행되지 않아 화면 전환이 안 된 상태에서 자식 규칙이 실행 → 타겟 이미지 없음 → 300초 × 3회 = 15분 낭비.

**원인:** `rule_executor.py:2264`의 조건 `if not watch_found or condition_pending_watch is not None:`에서 `condition_pending_watch is not None` 때문에 조건 대기 중에도 최종 이미지 체크를 수행하여 모니터링 탈출.

**수정:** 조건 대기 중(`condition_pending_watch` 설정)에는 최종 이미지로 탈출하지 않도록 변경. 조건이 해소(조건 이미지 사라짐 → 점프 실행)되거나 감시 이미지가 사라질 때까지 모니터링 유지.

**수정된 파일:**
- `src/player/rule_executor.py` - 최종 이미지 체크 조건에서 `condition_pending_watch` 분기 제거
- `src/utils/config.py` - APP_VERSION 1.0.127 → 1.0.128

---

### 2026-02-09: 로그 개편 + ANSI 색상 통일 (v1.0.127)

**로그 개편 (rule_executor.py):**
- 저수준 마우스 로그(pynput, Win32, SendInput) 22건 → debug 강등
- 사용자 중심 메시지 재작성 27건 (이모지→텍스트, 경로→파일명, 분:초 포맷)
- ANSI 색상 적용: ✓ 성공(_GREEN), ⏳ 대기(_YELLOW), ✗ 에러(_RED), 헤더(_CYAN)
- 불필요 로그 5건 삭제 (빈 줄, 중복 스킵 메시지 등)
- 색상 오류 수정 7건 (_RED→_YELLOW 경고용, _YELLOW→_GREEN 성공용)

**ANSI 색상 통일 (main_window.py, log_view.py):**
- LogPanel, LogView, MiniPlayer 3곳에 ansi_red/ansi_cyan 태그 누락 수정
- _parse_ansi() 파서에 91(빨강), 96(청록) 코드 파싱 추가
- 4개 로그 표시 위치(에디터 LogPanel, LogView, 플레이 미니플레이어, 플레이 _append_log) 모두 동일 5색 지원

**수정된 파일:**
- `src/player/rule_executor.py` - 로그 ~50건 개편
- `src/ui/main_window.py` - LogPanel/MiniPlayer ANSI 색상 태그+파서 수정
- `src/ui/log_view.py` - LogView ANSI 색상 태그+파서 수정
- `src/utils/config.py` - APP_VERSION 1.0.126 → 1.0.127

---

### 2026-02-08: 모니터링 액션 중복 실행 + NameError 크래시 수정 (v1.0.126)

**문제 1:** 조건 미충족 시 감시 이미지가 화면에 계속 있으면 매 루프마다 모니터링 액션 반복 실행.
**문제 2:** v1.0.125에서 `monitor_actions` 변수가 skip 분기에서 미정의 → NameError 크래시.

**수정:** `condition_pending_watch`로 조건 대기 중인 watch 추적 + `monitor_actions` 변수를 skip 분기 밖에서 항상 정의하도록 구조 변경.

**수정된 파일:**
- `src/player/rule_executor.py` - monitor_actions 스코프 수정 + 조건 대기 시 액션 건너뛰기
- `src/utils/config.py` - APP_VERSION 1.0.125 → 1.0.126

---

### 2026-02-08: 모니터링 액션 중복 실행 버그 수정 (v1.0.125) - v1.0.126으로 대체됨

**문제:** 조건 이미지가 미충족(점프 차단) 상태에서 감시 이미지가 화면에 계속 있으면, 매 루프마다 모니터링 액션을 반복 실행. 모니터링 액션은 "조건과 무관하게 항상 실행"이므로 키 입력/클릭이 무한 반복됨.

**수정:** `condition_pending_watch` 추적 변수 도입. 조건 미충족으로 대기 중인 watch에 대해서는 다음 루프에서 모니터링 액션을 건너뛰고 조건만 재확인. 감시 이미지가 사라지거나 조건 충족 시 pending 상태 해제.

**수정된 파일:**
- `src/player/rule_executor.py` - `_execute_monitoring_mode` 모니터링 액션 중복 실행 방지
- `src/utils/config.py` - APP_VERSION 1.0.124 → 1.0.125

---

### 2026-02-08: 모니터링 모드 버그 9건 일괄 수정 (v1.0.124)

**4개 에이전트 병렬 심층 분석으로 발견된 버그 전량 수정.**

**Major 수정 5건:**
1. **감시 처리 후 불필요한 0.5초 대기**: watch 처리 완료 후 대기 구간으로 빠지는 문제 → `continue` 추가
2. **조건 미충족 3초 쿨다운 중 stop 불가**: `time.sleep(3.0)` → `self._stop_event.wait(timeout=3.0)` 변경
3. **monitor_action 개별 confidence 무시**: rule 전체 confidence만 사용 → `monitor_action.get('confidence', confidence)` 적용
4. **ExecutionState.MONITORING 미설정**: 모니터링 실행 중에도 RUNNING_INITIAL 상태 → 진입 시 MONITORING 설정, 종료 시 복원
5. **stop() 후 재진입 경쟁 상태**: 이전 스레드 alive 체크 없이 새 스레드 시작 → join(5초) 후 진행

**Minor 수정 4건:**
6. **대기 시간 로그 부정확**: `wait_count * 0.5` → `time.time() - monitoring_start` 실제 경과 시간 사용
7. **step_prefix 혼용**: `self._step_prefix`와 지역변수 `step_prefix` 혼용 → `step_prefix`로 통일
8. **goto 점프 후 sleep이 stop 무시**: `time.sleep(wait_time)` → `self._stop_event.wait(timeout=wait_time)` 변경
9. **resume() 상태 복원 오류**: 일시정지 전 상태 저장(`_state_before_pause`) 후 resume 시 정확히 복원

**수정된 파일:**
- `src/player/rule_executor.py` - 모니터링 모드 전반 (9건)
- `src/utils/config.py` - APP_VERSION 1.0.123 → 1.0.124

---

### 2026-02-08: 이미지 오인식 수정 - 2차 매처 폴백 제거 (v1.0.123)

**문제:** 감시 이미지가 화면에 없는데도 89%로 발견됨. 1차 마스크 매칭(match_binary)이 65%로 정확히 "없다"고 판정했지만, 2차 강화 매처(match_adaptive)가 6가지 방법 + 임계값 20% 하향으로 억지로 찾아내서 오인식 발생.

**수정:** 2차 매처 폴백(match_with_roi/match_adaptive) 완전 제거. 1차 마스크 매칭만 사용하도록 변경. Otsu 이진화로 전경/배경 분리 후 전경 픽셀만 비교하는 정확한 단일 방식으로 통일.

**수정된 파일:**
- `src/player/rule_executor.py` - `_find_image_on_screen`에서 2차 매처 폴백 코드 제거
- `src/utils/config.py` - APP_VERSION 1.0.122 → 1.0.123

---

### 2026-02-08: 감시 이미지 발견 로그 수정 - 조건 있을 때 "점프" 대신 "조건 확인" (v1.0.122)

**변경:** 조건 이미지가 설정된 감시 항목에서 감시 이미지 발견 시 "→ 액션 N로 점프"라고 로그 출력하던 것을 "→ 모니터링 액션 실행 후 조건 확인"으로 수정. 실제로는 조건 체크 후 점프 여부가 결정되는데 로그만 점프했다고 나와서 혼동 유발.

**수정된 파일:**
- `src/player/rule_executor.py` - 조건 이미지 유무에 따라 로그 메시지 분기
- `src/utils/config.py` - APP_VERSION 1.0.121 → 1.0.122

---

### 2026-02-08: 조건 이미지 로직 재수정 - 발견=점프차단이 정확 (v1.0.121)

**변경:** v1.0.120에서 잘못 되돌린 조건 이미지 로직 재수정. 올바른 동작: "조건 이미지 발견 = 아직 조건 상태 = 점프 차단", "조건 이미지 없음 = 조건 해소 = 점프 실행".

**수정된 파일:**
- `src/player/rule_executor.py` - 조건 이미지 발견 시 condition_met=False(점프대기), 미발견 시 True(점프실행)
- `src/utils/config.py` - APP_VERSION 1.0.120 → 1.0.121

---

### 2026-02-08: 인식률 키보드 조절 + 재감지 confidence 버그 수정 (v1.0.119)

**변경 1 - 키보드 조절:** 감시 이미지/조건 이미지 인식률 슬라이더에 키보드 좌우 화살표 지원 (1%씩 미세 조절).

**변경 2 - 재감지 버그:** 최종 이미지 감지 후 감시 이미지 재감지 시 watch별 개별 confidence를 무시하고 rule 기본값만 사용하던 버그 수정.

**수정된 파일:**
- `src/ui/monitoring_editor.py` - 슬라이더 Left/Right 키바인딩 추가 (감시+조건 모두)
- `src/player/rule_executor.py` - recheck 시 watch.get('confidence', confidence) 사용
- `src/utils/config.py` - APP_VERSION 1.0.118 → 1.0.119

---

### 2026-02-08: 감시 이미지별 인식률 UI 복원 (v1.0.118)

**변경:** v1.0.53에서 제거된 감시 이미지별 인식률(confidence) 슬라이더를 모니터링 편집기에 복원. 각 감시 항목마다 30~100% 범위로 개별 인식률 설정 가능.

**수정된 파일:**
- `src/ui/monitoring_editor.py` - 인식률 슬라이더 UI 추가, 데이터 로드/저장에 confidence 필드 복원
- `src/player/rule_executor.py` - watch별 개별 confidence 사용 (없으면 rule confidence 폴백)
- `src/utils/config.py` - APP_VERSION 1.0.117 → 1.0.118

---

### 2026-02-08: 조건 이미지 오탐 방지 + 로그 개선 (v1.0.117)

**변경 1 - 조건 이미지 오탐:** 조건 이미지 기본 인식률을 65% → 80%로 상향. 로직 반전(발견=점프차단) 후 낮은 임계값이 오탐을 유발하여 점프가 계속 차단되던 문제 해결.

**변경 2 - 로그 개선:** 조건 이미지 매칭 시 실제 매칭률과 설정 임계값을 함께 표시하여 디버깅 용이. 모니터링 액션 실행 시작/없음 로그를 info 레벨로 승격.

**수정된 파일:**
- `src/player/rule_executor.py` - 기본 condition_confidence 0.65→0.80, 로그에 실제 신뢰도 표시, 모니터링 액션 로그 info 승격
- `src/ui/monitoring_editor.py` - 기본 condition_confidence 0.65→0.80 (5곳)
- `src/utils/config.py` - APP_VERSION 1.0.116 → 1.0.117

---

### 2026-02-08: 부분실행 rule_id 중복 수정 + 모니터링 조건 체크 순서 수정 (v1.0.116)

**변경 1 - 부분실행 버그:** 하위액션 ▶ 클릭 시 상위액션부터 실행되는 버그 수정. 원인: 순차적 rule_id(rule_0000) 생성 시 중복 발생 → 첫 번째 매칭만 사용하여 잘못된 규칙 선택됨.
- `_test_run_rule` 검색을 객체 동일성(`r is rule`)으로 변경 + 중복 ID 폴백 로직 추가
- 순차 ID 생성(`rule_{len}:04d`) → UUID 기반 ID(`rule_{uuid}`)로 전부 교체

**변경 2 - 모니터링 조건:** 조건 이미지 체크를 모니터링 액션 실행 후로 재배치. 모니터링 액션은 조건과 무관하게 항상 실행, 조건은 점프만 제어. 재감지 쿨다운 3초 적용.

**수정된 파일:**
- `src/ui/player_view.py` - 부분실행 검색 로직 개선 + UUID ID 생성
- `src/analyzer/video_analyzer.py` - 녹화 시 UUID ID 생성
- `src/player/rule_executor.py` - 조건 체크를 액션 후 점프 전으로 이동, 3초 쿨다운
- `src/utils/config.py` - APP_VERSION 1.0.115 → 1.0.116

---

### 2026-02-08: 모니터링 조건 이미지 체크 시점 변경 (v1.0.115)

**변경:** 조건 이미지 체크를 모니터링 액션 실행 후 → 실행 전으로 이동. 조건 미충족(조건 이미지 발견) 시 모니터링 액션+점프 모두 건너뛰고 모니터링 대기로 복귀.

**수정된 파일:**
- `src/player/rule_executor.py` - 조건 이미지 체크를 모니터링 액션 실행 전으로 이동
- `src/utils/config.py` - APP_VERSION 1.0.114 → 1.0.115

---

### 2026-02-08: 검색범위 직사각형 모델 도입 (v1.0.114)

**변경:** 검색범위를 center+radius(정사각형) 대신 [x1,y1,x2,y2] 직사각형으로 저장하도록 개선. 얇은 영역 선택 시 정사각형으로 뻥튀기되던 문제 해결.

**수정된 파일:**
- `src/analyzer/automation_models.py` - `search_region` 필드 추가 (Optional[List[int]])
- `src/ui/analyzer_view.py` - 선택 영역을 search_region에 직사각형 그대로 저장, 버튼/표시 텍스트 개선
- `src/player/rule_executor.py` - search_region 우선 사용 (3곳), _find_all_images_on_screen에 search_region 파라미터 추가
- `src/utils/config.py` - APP_VERSION 1.0.113 → 1.0.114

---

### 2026-02-08: 검색범위(ScreenRegionSelector) 좌표 버그 수정 (v1.0.113)

**수정된 버그:**
1. **잘못된 DPI 스케일링 제거**: `GetDeviceCaps(hdc, 118/117)`은 멀티모니터 전체 데스크톱 크기를 반환하여 좌표가 2배+ 뻥튀기됨. tkinter와 ImageGrab 모두 논리 좌표를 사용하므로 DPI 스케일링 자체가 불필요
2. **빨간선 시각적 확장 수정**: 5중 동심 사각형(offset 0~4, width=2) → 단일 사각형(width=3)으로 변경. 기존에는 ~10px 확장되어 실제 범위보다 크게 표시됨
3. **중복 이벤트 바인딩 제거**: canvas와 window에 동일 마우스 이벤트를 이중 바인딩하고 있었음

**수정된 파일:**
- `src/ui/analyzer_view.py` - ScreenRegionSelector DPI 스케일링 제거, 빨간선 단일화, 중복 바인딩 정리
- `src/utils/config.py` - APP_VERSION 1.0.112 → 1.0.113

---

### 2026-02-08: 모니터링 타임아웃 확대 (v1.0.112)

**변경:** 모니터링 모드 안전 타임아웃 2시간(7200초) → 4시간(14400초)으로 확대

**수정된 파일:**
- `src/player/rule_executor.py` - `monitoring_timeout` 기본값 7200 → 14400
- `src/utils/config.py` - APP_VERSION 1.0.111 → 1.0.112

---

### 2026-02-08: 경로탐색 버그 3건 수정 + 전체맵핑 안정화 (v1.0.111)

**수정된 버그:**

| 파일 | 버그 | 수정 내용 |
|------|------|----------|
| `src/player/obstacle_avoidance.py` | BFS 경로탐색에서 큐에 전체 경로 저장 (O(N²) 메모리) | `came_from` dict + 역추적 패턴으로 O(N) 최적화 |
| `src/player/simple_pathfinder.py` | `list.index()` 중복 좌표 시 첫 번째만 반환 → 경로 추적 오류 | 현재 인덱스 기준 순방향/역방향 탐색으로 정확한 위치 찾기 |
| `src/player/obstacle_avoidance.py` | 대각선 방향 점수 계산에서 `dx==0`일 때 잘못된 방향에 +1 점수 | 축 정렬 시 중립 점수(0) 부여 |

**전체맵핑 안정화:**
- `full_mapping_exploring` 플래그로 전체맵핑 프런티어 탐색 정상 동작 확인
- `is_boss_dungeon` 계산에 `full_mapping_exploring` 반영 (player_view.py)
- 목표 도달 시 `continue`로 프런티어 탐색 진입 (target_idx 증가 건너뜀)

**기타:**
- 맵 데이터 업데이트 (흑해골굴 1~9굴)
- 빌드/배포 설정 업데이트

**수정된 파일:**
- `src/player/obstacle_avoidance.py` - BFS came_from + 방향 점수 수정
- `src/player/simple_pathfinder.py` - 경로 인덱스 추적 수정
- `src/ui/player_view.py` - 전체맵핑 안정화
- `src/utils/config.py` - APP_VERSION 1.0.110 → 1.0.111

---

### 2026-02-07: 특화모드 저장 버그 수정 + 이미지 삭제 기능 (v1.0.110)

**버그:** 저장 버튼 클릭 시 messagebox가 GameModeDialog 뒤에 숨어 프로그램이 멈춘 것처럼 보임

**원인:** `_save_config()` → `_save_callback()` → `PlanDetailDialog._save_plan()` → `messagebox.showinfo()` 순서로 호출되는데, messagebox가 PlanDetailDialog에 parenting되어 GameModeDialog 뒤에 표시됨. 이미지 추가/삭제/편집 시에도 동일한 messagebox 반복 표시.

**수정:**

| 파일 | 변경 내용 |
|------|----------|
| `src/ui/player_view.py` | `_save_config()` → JSON 직접 저장 (messagebox 없이 silent save) |
| `src/ui/player_view.py` | `_save_config_with_msg()` 추가 — 저장 버튼 전용, messagebox를 GameModeDialog에 parenting |
| `src/ui/player_view.py` | `_delete_waypoint_image()` 추가 — 경유지 카드에서 이미지 삭제 |
| `src/ui/player_view.py` | 경유지 카드에 이미지 삭제 버튼("X") 추가, `_update_card_image_status`에서 표시/숨김 |
| `src/utils/config.py` | APP_VERSION 1.0.109 → 1.0.110 |

---

### 2026-02-07: 경유지 이미지 시스템 리팩토링 + 안정성 개선 (v1.0.109)

**주요 변경:**

| 파일 | 변경 내용 |
|------|----------|
| `src/analyzer/automation_models.py` | waypoints 직렬화/역직렬화 메서드 추가 (이미지 경로 ↔ 상대경로 변환) |
| `src/ui/player_view.py` | 보스 이미지 대화상자 제거 → ImageCropDialog 기반으로 전환, 보스 스킬 UI 제거, 비동기 로드 경쟁 방지 |
| `src/ui/analyzer_view.py` | 비동기 플랜 로드 경쟁 방지, 분석 취소 상태 정리, 이미지 정리 시 target_images 고려 |
| `src/ui/main_window.py` | `_switch_view()` 안정화 — 새 뷰 준비 후에만 이전 뷰 숨기기 |
| `src/ui/settings_view.py` | `import json` 모듈 레벨로 이동 |
| `src/app.py` | `_cleanup()`에 settings_view, guide_view 리소스 정리 추가 |
| `src/player/map_canvas.py` | 순찰 타일 오버레이 동작, 벽 배치 시 순찰 제거 |
| `src/utils/config.py` | APP_VERSION 1.0.108 → 1.0.109 |

---

### 2026-02-07: UI 로딩 성능 최적화 (v1.0.108)

**문제:** 에디터 모드 진입 시 5개 뷰 동기 생성(2~5초 블로킹), 탭 전환 시 refresh() 자동 호출(DB+JSON 재로드+위젯 재생성)

**수정된 파일:**

| 파일 | 변경 내용 |
|------|----------|
| `src/app.py` | 뷰 지연 생성 — 팩토리 등록 방식, 탭 첫 클릭 시에만 생성 |
| `src/ui/main_window.py` | `_switch_view()` 팩토리 지연 생성 + `refresh()` 자동 호출 제거 |
| `src/ui/analyzer_view.py` | `__init__` 데이터 로드 → `after(0)` + 백그라운드 스레드, JSON I/O + DB 쿼리 → 캐시 |
| `src/ui/player_view.py` | `__init__` 데이터 로드 → `after(0)` + 백그라운드 스레드, DB 쿼리 → 캐시, 썸네일 비동기화 |
| `src/ui/recorder_view.py` | DB 로드 `after(0)` 지연 |
| `src/ui/settings_view.py` | 설정 로드 `after(0)` 지연 |
| `src/ui/monitoring_editor.py` | 글로벌 썸네일 캐시 적용 |
| `src/utils/config.py` | APP_VERSION 1.0.107 → 1.0.108 |

**핵심 패턴:**
- 뷰 팩토리 (`register_view_factory`) — 탭 첫 접근 시에만 생성
- `after(0)` 콜백 — UI 렌더 후 데이터 로드
- 백그라운드 스레드 + `self.after(0, callback)` — JSON 파싱 메인 스레드 분리
- 데이터 캐시 (`_plan_modified_cache`, `_plan_lock_cache`) — 루프 내 I/O 제거
- 썸네일 비동기 로딩 — 플레이스홀더 → 백그라운드 로드 → UI 갱신

---

### 2026-02-03: 템플릿 전용 좌표 인식 시스템으로 전환

**목표:** OCR(EasyOCR, Tesseract) 폴백을 모두 제거하고 템플릿 매칭만 사용

**삭제된 파일:**
- `src/utils/ocr_utils.py` (353줄) - OCR 유틸리티 전체 삭제

**수정된 파일:**

| 파일 | 변경 내용 |
|------|----------|
| `src/utils/digit_templates.py` | 슬라이딩 윈도우 방식으로 전환, `read_both_coordinates()` 추가 |
| `src/ui/player_view.py` | OCR import/UI 제거, 템플릿 전용으로 변경 |
| `src/player/rule_executor.py` | OCR 폴백 제거, 템플릿만 사용 |

**digit_templates.py 주요 개선:**

#### 1. 슬라이딩 윈도우 방식 (컨투어 기반 → 템플릿 슬라이딩)
```python
# 기존: 컨투어로 숫자 영역 분리 후 개별 매칭 (불안정)
contours = cv2.findContours(binary, ...)
for cnt in contours:
    digit = match_digit(crop)

# 변경: 템플릿을 이미지 위에서 슬라이드하며 매칭 (안정적)
result = cv2.matchTemplate(binary, scaled_template, cv2.TM_CCOEFF_NORMED)
locations = np.where(result >= threshold)
```

#### 2. 다중 스케일 매칭
```python
for scale in [0.9, 1.0, 1.1]:  # 3가지 크기로 시도
    scaled_template = cv2.resize(template, (new_w, new_h))
    result = cv2.matchTemplate(binary, scaled_template, ...)
```

#### 3. 최대 자릿수 제한 (중복 인식 방지)
```python
avg_tmpl_w, _ = self._get_template_size()
max_digits = max(1, int(img_w / (avg_tmpl_w * 0.7)))  # 영역 너비 기준
```

#### 4. 범위 기반 NMS (Non-Maximum Suppression)
```python
# 기존: 중심점 거리만 비교 (17 → 172 오인식 발생)
if abs(center_x - used_x) < width * 0.6:

# 변경: 실제 x 범위(start~end)의 겹침 비율 계산
overlap_start = max(new_start, start_x)
overlap_end = min(new_end, end_x)
overlap_width = max(0, overlap_end - overlap_start)
if overlap_width > width * 0.5:  # 50% 이상 겹치면 제외
```

#### 5. 인접 숫자 간격 검증
```python
# 최종 결과에서 숫자 간 간격이 적절한지 추가 검증
gap = curr_x - (prev_x + prev_width)
if gap >= -prev_width * 0.3:  # 간격이 너비의 -30% 이상이면 유효
    validated_matches.append(final_matches[i])
```

**UI 변경:**
- "좌표 기반 (OCR)" → "좌표 기반 (템플릿)"
- "📍 좌표 읽기 영역 (OCR)" → "📍 좌표 읽기 영역"
- "📡 실시간 좌표 (OCR 테스트)" → "📡 실시간 좌표"
- "OCR 원본" → "인식 상태"
- Tesseract 설치 확인 버튼/상태 라벨 제거

**효과:**
- Tesseract/EasyOCR 설치 불필요
- 인식 속도 향상 (OCR 대비 빠름)
- 게임 폰트에 최적화된 정확한 인식
- 17 → 172 같은 중복 인식 문제 해결

---

### 2026-01-31: SSL DLL 로드 오류 수정 (v1.0.104)

**증상:** "자동 업데이트 확인 오류: DLL load failed while importing _ssl: %1은(는) 올바른 Win32 응용 프로그램이 아닙니다"

**원인:** `updater.py`, `update_service.py` 모듈 최상단에 `import ssl`이 있어서, SSL DLL 로드 실패 시 모듈 전체 import가 실패함

**해결:**

| 파일 | 변경 내용 |
|------|----------|
| `updater.py` | `import ssl` 제거, `_get_ssl_context()` 함수로 지연 import |
| `update_service.py` | `import ssl` 제거, `_get_ssl_context()` 함수로 지연 import |

```python
def _get_ssl_context(verify: bool = True):
    """SSL 컨텍스트 생성 (SSL 모듈 로드 실패 시 None 반환)"""
    try:
        import ssl
        ctx = ssl.create_default_context()
        if not verify:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        return ctx
    except Exception:
        return None
```

**효과:**
- SSL DLL 로드 실패해도 프로그램 정상 실행
- "SSL 없음" 방법으로 업데이트 확인 시도 가능
- 일부 환경에서 HTTPS 연결이 안 될 수 있지만 프로그램은 크래시하지 않음

---

### 2026-01-31: 업데이트 관련 버그 수정 (v1.0.103)

**증상:**
- 버전 확인 버튼 클릭 시 "버전확인중"만 표시되고 무한 대기
- 업데이트 버튼 클릭 시 "릴리즈확인중"만 표시되고 무한 대기
- 업데이트 완료 후 프로그램이 자동 재시작되지 않음
- 수동으로 재시작해도 창이 나타나지 않음

**원인 분석:**

| 문제 | 원인 |
|------|------|
| 버전 확인 무한 대기 | `_check_version_thread` 내부 예외 발생 시 UI 복구 코드 미실행 |
| 릴리즈 확인 무한 대기 | `_fetch_latest_release_direct` 예외 발생 시 안전하게 처리 안 됨 |
| 재시작 실패 | 배치 파일의 `start` 명령어가 공백 포함 경로에서 실패 |

**해결:**

#### 1. 버전 확인 스레드 예외 처리 (settings_view.py)

```python
def _check_version_thread(self, repo: str) -> None:
    try:
        # 기존 로직 전체
        ...
    except Exception as e:
        # 전체 스레드 예외 발생 시 UI 복구
        logger.error(f"버전 확인 스레드 예외: {e}", exc_info=True)
        self.after(0, lambda: self._update_check_failed("예기치 않은 오류"))
```

#### 2. 릴리즈 정보 가져오기 예외 처리 (settings_view.py)

```python
def _fetch_latest_release_direct(self, repo: str) -> dict:
    try:
        # 기존 로직 전체
        ...
        return None
    except Exception as e:
        logger.error(f"릴리즈 정보 가져오기 전체 오류: {e}")
        return None
```

#### 3. 배치 파일 재시작 명령어 수정 (app.py)

| 변경 전 | 변경 후 |
|---------|---------|
| `start "" "{app_dir}\\{exe_name}"` | `cd /d "{app_dir}"` 후 `start "" "{exe_name}"` |

공백이 포함된 경로(예: `C:\Program Files\WinCro\WinCro.exe`)에서 `start` 명령어가 실패하는 문제 해결.

**수정된 파일:**
- `src/ui/settings_view.py` - `_check_version_thread`, `_fetch_latest_release_direct`
- `src/app.py` - 배치 파일 재시작 명령어 4곳 수정

---

### 2026-01-31: 모니터링 모드 점프 조건 기능 추가 (v1.0.102)

**기능:** 모니터링 모드에서 점프 실행 전 조건 이미지를 확인하여 조건부 점프 지원

**동작 흐름:**
```
감시 이미지 발견 → 모니터링 액션 실행 → 조건 이미지 확인
                                          ├─ 조건 충족 → 점프 액션 실행 → 모니터링 복귀
                                          └─ 조건 미충족 → 점프 건너뜀 → 모니터링 복귀
```

**UI 변경:**
| 파일 | 변경 내용 |
|------|----------|
| `monitoring_editor.py` | 감시 항목에 "조건" 버튼 추가 (점프 드롭다운 왼쪽) |
| `monitoring_editor.py` | `_edit_condition()` 메서드 추가 - 조건 설정 다이얼로그 |

**조건 설정 다이얼로그:**
- 이미지 선택/미리보기
- 검색 범위 지정 (ScreenRegionSelector)
- 인식률 슬라이더 (30~100%, 기본 65%)

**데이터 필드 (monitoring_watches 항목):**
```python
{
    "condition_image": str,           # 점프 조건 이미지 경로
    "condition_search_region": list,  # 검색 범위 [x1, y1, x2, y2]
    "condition_confidence": float,    # 인식률 (기본 0.65)
}
```

**실행 로직 (rule_executor.py):**
```python
# 조건 이미지 존재 시 확인
condition_image = watch.get('condition_image')
condition_met = True  # 조건 없으면 항상 충족
if condition_image and Path(condition_image).exists():
    result = self._find_image_on_screen(condition_image, condition_confidence,
                                        search_region=condition_search_region)
    condition_met = result is not None

# 조건 충족 시에만 점프
if goto_index >= 0 and condition_met:
    # 점프 액션 실행
```

**경로 변환 (automation_models.py):**
- `to_dict()`: condition_image → 파일명만 저장
- `from_dict()`: 파일명 → 절대 경로 복원

**수정된 파일:**
- `src/ui/monitoring_editor.py` - UI 및 _edit_condition 메서드
- `src/player/rule_executor.py` - 조건 체크 로직
- `src/analyzer/automation_models.py` - 경로 변환

---

### 2026-01-30: 게임 모드 스무스 이동 및 스킬 기능 추가 (v1.0.86 ~ v1.0.101)

**기능:** 게임 특화 모드에 스무스 이동, 이동 스킬, 상시 스킬 기능 추가

**스무스 이동 (smooth_move):**
| 모드 | 방식 | 특징 |
|------|------|------|
| OFF (기본) | 키 반복 | 호환성 좋음, 약간 로봇같음 |
| ON | 울트라 탭 | 15ms 누름 + 5ms 간격, 자연스러움 |

**이동 스킬 (move_skill):**
| 설정 | 설명 |
|------|------|
| `move_skill_key` | 이동 스킬 키 (예: "4") |
| `move_skill_distance` | 스킬 사용 거리 (기본 150픽셀 이상) |

**상시 스킬 (auto_skill):**
| 설정 | 설명 |
|------|------|
| `auto_skill_key` | 상시 사용 스킬 키 |
| `auto_skill_cooldown_image` | 쿨타임 이미지 (없으면 스킬 사용) |

**인식률 분리:**
- `character_confidence`: 캐릭터 이미지 인식률
- `target_confidence`: 목표 이미지 인식률

**수정된 파일:**
- `src/analyzer/automation_models.py` - GameModeConfig 필드 추가
- `src/ui/player_view.py` - 게임 모드 설정 UI
- `src/player/rule_executor.py` - 스킬/이동 로직

---

### 2026-01-21: 녹화 시작/중지 버튼 UI 먹통 현상 수정

**증상:** 녹화 시작 또는 중지 버튼 클릭 시 프로그램이 먹통(UI 응답 없음)

**원인 및 해결:**

#### 1. 녹화 시작 시 블로킹 (screen_recorder.py)

| 원인 | 해결 |
|------|------|
| `cv2.VideoWriter` 초기화가 `start()` 메서드에서 실행되어 메인 스레드 블로킹 | `_record_loop()` 내부로 이동 (lazy init) |
| 녹화 시작 타임아웃 2초 부족 | 5초로 증가 |

```python
# 변경 전: start()에서 VideoWriter 초기화
self._writer = cv2.VideoWriter(...)  # 블로킹!

# 변경 후: _record_loop()에서 lazy 초기화
def _record_loop(self):
    self._writer = cv2.VideoWriter(...)  # 별도 스레드에서 실행
```

#### 2. 녹화 시작 시 pynput 리스너 충돌 (input_logger.py - start)

| 원인 | 해결 |
|------|------|
| pynput 마우스/키보드 리스너 초기화가 동시에 실행되어 Windows 훅 충돌 | 각 리스너 초기화 후 대기 + 0.1초 딜레이 추가 |
| 리스너 초기화 실패 시 에러 처리 미흡 | 개별 try/except 및 cleanup 추가 |

```python
# 변경 후: 리스너 초기화 대기 및 딜레이
self._mouse_listener.start()
for _ in range(20):  # 최대 1초 대기
    if self._mouse_listener.running:
        break
    time.sleep(0.05)

time.sleep(0.1)  # 훅 충돌 방지 딜레이

self._keyboard_listener.start()
# ... 동일하게 대기
```

#### 3. 녹화 중지 시 블로킹 (input_logger.py - stop)

| 원인 | 해결 |
|------|------|
| pynput `Listener.stop()`이 내부적으로 `join()`을 호출하여 블로킹 | 별도 스레드에서 stop() 실행 + 1초 타임아웃 |

```python
# 변경 전: 직접 stop() 호출 (블로킹)
self._mouse_listener.stop()
self._keyboard_listener.stop()

# 변경 후: 별도 스레드 + 타임아웃
def stop_listeners():
    self._mouse_listener.stop()
    self._keyboard_listener.stop()

stop_thread = threading.Thread(target=stop_listeners, daemon=True)
stop_thread.start()
stop_thread.join(timeout=1.0)  # 최대 1초만 대기
```

**수정된 파일:**
- `src/recorder/screen_recorder.py` - start(), _record_loop()
- `src/recorder/input_logger.py` - start(), stop()

---

### 2026-01-21: 창 드래그 시 마우스 이동 캡처 안 되는 문제 수정

**증상:** 녹화 중 창 제목 표시줄을 드래그하면 마우스 이동이 캡처되지 않음, 분석에서도 드래그로 인식 안 됨

**원인:**
Windows에서 창 제목 표시줄 드래그 시 "모달 드래그 루프"에 진입하여 pynput 훅에 마우스 이동 이벤트가 전달되지 않음. Win32 API도 이벤트 기반으로는 위치를 받지 못함.

**해결:**
`_monitor_mouse_state()` 함수에 드래그 중 커서 위치 폴링 기능 추가

```python
# 변경 전: 놓침 이벤트만 감지
def _monitor_mouse_state(self):
    if self._mouse_pressed and self._drag_start_pos:
        if not self._is_mouse_button_pressed():
            # 놓침 이벤트 처리만 함

# 변경 후: 드래그 중 위치도 폴링
def _monitor_mouse_state(self):
    if self._mouse_pressed and self._drag_start_pos:
        current_pos = self._get_cursor_position()  # Win32 API로 위치 폴링

        if not self._is_mouse_button_pressed():
            # 놓침 이벤트 처리
        else:
            # 드래그 중 - 위치 업데이트 및 이벤트 기록
            self._last_mouse_pos = current_pos
            self._add_event(...)  # mouse_move 이벤트 추가
```

**핵심 변경:**
- 마우스 버튼이 눌린 동안 50ms 간격으로 Win32 API (`GetCursorPos`)로 커서 위치 폴링
- 폴링된 위치를 `_last_mouse_pos`에 업데이트
- `mouse_move` 이벤트로 기록하여 드래그 종료 시 정확한 거리 계산 가능

**수정된 파일:**
- `src/recorder/input_logger.py` - _monitor_mouse_state()

---

### 2026-01-21: 드래그 기능 종합 개선

**개선 내용:**

#### 1. 우클릭/중간버튼 드래그 지원

| 변경 전 | 변경 후 |
|---------|---------|
| 좌클릭만 드래그로 감지 | 좌/우/중간 버튼 모두 드래그 감지 |

```python
# _is_mouse_button_pressed() 함수 개선
def _is_mouse_button_pressed(self, button: str = "left") -> bool:
    vk_map = {
        "left": 0x01,    # VK_LBUTTON
        "right": 0x02,   # VK_RBUTTON
        "middle": 0x04,  # VK_MBUTTON
    }
    vk = vk_map.get(button.lower(), 0x01)
    state = ctypes.windll.user32.GetAsyncKeyState(vk)
    return (state & 0x8000) != 0
```

#### 2. 드래그 임계값 설정 가능

| 변경 전 | 변경 후 |
|---------|---------|
| 하드코딩된 임계값 (25px, 0.15초) | config.py에서 설정 가능 |

```python
# config.py - RecordingConfig
@dataclass
class RecordingConfig:
    drag_threshold_distance: int = 25  # 드래그 최소 이동 거리 (픽셀)
    drag_threshold_time: float = 0.15  # 드래그 최소 누름 시간 (초)
```

#### 3. 예외 처리 강화 (try-finally)

| 변경 전 | 변경 후 |
|---------|---------|
| 예외 발생 시 상태 초기화 누락 가능 | try-finally로 상태 초기화 보장 |

```python
def _handle_mouse_release(self, x, y, button_name):
    try:
        # 드래그/클릭 처리 로직
        ...
    except Exception as e:
        logger.error(f"마우스 놓음 처리 중 오류: {e}")
    finally:
        # 상태 초기화 (예외 발생 시에도 반드시 실행)
        self._drag_start_pos = None
        self._drag_start_time = None
        self._drag_start_modifiers = []
        self._drag_button = None
        self._drag_path = []
```

#### 4. 좌표 보정 로직 개선

| 변경 전 | 변경 후 |
|---------|---------|
| Win32 위치 vs pynput 중 거리가 큰 것 선택 | 폴링 위치 > Win32 > pynput 우선순위 |

```python
# 좌표 선택 우선순위 (개선)
# 1. _last_mouse_pos: 드래그 중 50ms 간격 폴링된 최신 위치 (가장 신뢰도 높음)
# 2. win32_pos: 마우스 놓는 순간 Win32 API 위치
# 3. pynput 위치: 기본값 (Windows 창 드래그 시 부정확할 수 있음)
if self._last_mouse_pos and last_pos_dist > min_movement_threshold:
    end_x, end_y = self._last_mouse_pos
elif win32_dist > pynput_dist and win32_dist > min_movement_threshold:
    end_x, end_y = win32_pos
```

#### 5. 드래그 경로 정보 저장

| 변경 전 | 변경 후 |
|---------|---------|
| 시작/끝 좌표만 저장 | 전체 경로 포인트 저장 (x, y, timestamp) |

```python
# InputEvent에 drag_path 필드 추가
@dataclass
class InputEvent:
    drag_path: Optional[List[Dict[str, Any]]] = None  # [{"x": int, "y": int, "t": float}, ...]

# 드래그 중 경로 기록
self._drag_path.append({"x": current_pos[0], "y": current_pos[1], "t": current_time})

# MOUSE_DRAG_END 이벤트에 경로 포함
drag_end = InputEvent(
    event_type=InputEventType.MOUSE_DRAG_END.value,
    drag_path=self._drag_path.copy(),
    ...
)
```

**수정된 파일:**
- `src/recorder/input_logger.py` - 전체 드래그 로직 개선
- `src/utils/config.py` - 드래그 임계값 설정 추가

---

### 2026-01-21: 녹화 시작 시 프로그램 크래시/멈춤 현상 수정

**증상:** 녹화 시작 버튼 클릭 후 1~4분 내에 프로그램이 크래시되거나 멈춤

**원인 분석:**
1. **pynput 훅 충돌**: 동시에 너무 많은 Windows 입력 훅 등록
   - `GlobalHotKeys` (F7 단축키)
   - `mouse.Listener` (마우스 입력 기록)
   - `keyboard.Listener` (키보드 입력 기록)
   - `GetAsyncKeyState` 폴링 스레드 (F7 중복 감지)

2. **중복 F7 감지 메커니즘**: GlobalHotKeys와 GetAsyncKeyState 폴링이 동시에 F7 감지

**해결책:**

#### 1. GetAsyncKeyState 폴링 스레드 제거 (중복 감지 제거)

| 변경 전 | 변경 후 |
|---------|---------|
| GlobalHotKeys + GetAsyncKeyState 폴링 동시 사용 | GlobalHotKeys만 사용 (녹화 중에는 InputLogger에서 처리) |

제거된 함수:
- `_start_f7_monitor()`
- `_monitor_f7_key()`
- `_f7_monitor_running`, `_f7_monitor_thread` 변수

#### 2. 녹화 중 GlobalHotKeys 일시 중지

| 시점 | 동작 |
|------|------|
| 녹화 시작 시 | `GlobalHotKeys.stop()` → InputLogger의 keyboard.Listener에서 F7 처리 |
| 녹화 종료 시 | `GlobalHotKeys` 재시작 |

```python
def _pause_global_hotkeys(self):
    """GlobalHotKeys 일시 중지 (녹화 중 keyboard.Listener와 충돌 방지)"""
    if hasattr(self, '_global_hotkeys') and self._global_hotkeys is not None:
        if not self._global_hotkeys_paused:
            self._global_hotkeys.stop()
            self._global_hotkeys_paused = True

def _resume_global_hotkeys(self):
    """GlobalHotKeys 재시작 (녹화 종료 후)"""
    # GlobalHotKeys 새로 생성 및 시작
```

#### 3. pynput 리스너 초기화 딜레이 증가

| 변경 전 | 변경 후 |
|---------|---------|
| 마우스→키보드 리스너 간 200ms 딜레이 | 500ms 딜레이 |

```python
# input_logger.py
time.sleep(0.5)  # 500ms로 증가 (pynput 훅 충돌 방지)
```

#### 4. InputLogger에서 F7 키 처리

| 변경 전 | 변경 후 |
|---------|---------|
| F7 키 무시 (`return`) | F7 감지 시 콜백 호출 |

```python
def _on_key_press(self, key) -> None:
    # F7: 녹화 중지 단축키 - 콜백 호출
    if key == keyboard.Key.f7:
        logger.info("===== F7 감지 (InputLogger keyboard.Listener) =====")
        if self._on_f7_pressed:
            self._on_f7_pressed()
        return
```

**수정된 파일:**
- `src/ui/recorder_view.py` - F7 폴링 제거, GlobalHotKeys 일시 중지/재시작 로직 추가
- `src/recorder/input_logger.py` - 딜레이 500ms 증가, F7 키 처리 추가

---

### 2026-01-21: dxcam(DirectX) 캡처 엔진 지원 추가

**기능:** 화면 캡처 성능 향상을 위한 dxcam(DirectX) 지원

**구현 내용:**

#### 1. dxcam 우선 사용, mss 자동 폴백

| 시도 순서 | 엔진 | 설명 |
|-----------|------|------|
| 1차 | dxcam (DirectX) | GPU 가속 화면 캡처, 더 빠름 |
| 2차 | mss (GDI) | 폴백, 호환성 높음 |

#### 2. UI 블로킹 방지

모든 초기화 작업을 별도 스레드에서 수행:
- VideoWriter 초기화
- dxcam 임포트 (타임아웃 3초)
- dxcam 카메라 생성 (타임아웃 3초)
- 테스트 캡처 (타임아웃 2초)

```python
def _init_dxcam(self) -> bool:
    """dxcam 초기화 (타임아웃 적용)"""
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(import_dxcam)
        future.result(timeout=3.0)  # 타임아웃 적용
```

#### 3. 런타임 폴백

dxcam 캡처 중 연속 10회 실패 시 자동으로 mss로 전환:

```python
if consecutive_failures >= max_failures:
    logger.warning("dxcam 연속 10회 실패 - mss로 전환")
    self._capture_loop_mss(frame_interval)
```

#### 4. UI에 캡처 엔진 표시

녹화 중 상태 라벨에 현재 사용 중인 엔진 표시:
- `🔴 녹화 중 (DirectX)` - dxcam 사용 중
- `🔴 녹화 중 (GDI)` - mss 사용 중

**수정된 파일:**
- `src/recorder/screen_recorder.py` - dxcam 지원 추가, 타임아웃 및 폴백 로직
- `src/ui/recorder_view.py` - 캡처 엔진 표시 추가

---

### 2026-01-21: dxcam COM 스레드 친화성 문제 수정

**증상:** dxcam(DirectX) 캡처 엔진 사용 시 녹화 시작 후 몇 초 뒤에 프로그램 멈춤

**원인 분석:**

DirectX COM 객체는 **스레드 친화성(Thread Affinity)**이 있어서, COM 객체를 생성한 스레드에서만 사용해야 합니다.

기존 코드에서 `_init_dxcam()` 메서드가 `ThreadPoolExecutor`를 사용하여 dxcam 초기화를 수행했습니다:
- `ThreadPoolExecutor` 워커 스레드에서 `dxcam.create()` 실행 → COM 객체 생성
- 녹화 스레드에서 `camera.grab()` 실행 → 다른 스레드에서 COM 객체 사용

이로 인해 DirectX COM 객체가 잘못된 스레드에서 접근되어 데드락 또는 크래시가 발생했습니다.

로그 분석:
```
12:05:49 | dxcam 초기화 성공 (ThreadPoolExecutor 워커 스레드)
12:05:49 | 녹화 시작 신호 전송 (녹화 스레드)
12:05:50 | _on_recording_started 호출 (메인 스레드)
12:05:50 | (이후 UI 업데이트 로그 없음 - 멈춤)
12:05:54 | 마우스 이벤트 감지 (입력 로거 스레드는 동작)
12:07:04 | 앱 재시작 (사용자가 강제 종료 후)
```

**해결책:**

#### 1. ThreadPoolExecutor 제거, 현재 스레드에서 직접 초기화

| 변경 전 | 변경 후 |
|---------|---------|
| `ThreadPoolExecutor`로 dxcam 초기화 (타임아웃용) | 녹화 스레드에서 직접 dxcam 초기화 |

```python
# 변경 전: ThreadPoolExecutor 사용 (스레드 불일치 발생)
def _init_dxcam(self) -> bool:
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(lambda: dxcam.create())
        camera = future.result(timeout=3.0)  # 워커 스레드에서 생성!
    self._dxcam_camera = camera  # 녹화 스레드에서 사용 → 문제!

# 변경 후: 현재 스레드에서 직접 실행
def _init_dxcam(self) -> bool:
    """
    중요: dxcam은 DirectX COM 객체를 사용하므로 스레드 친화성이 있습니다.
    반드시 캡처 루프를 실행할 동일한 스레드에서 초기화해야 합니다.
    """
    import dxcam
    camera = dxcam.create(output_color="BGR")  # 녹화 스레드에서 직접 생성
    test_frame = camera.grab()  # 동일 스레드에서 테스트
    self._dxcam_camera = camera
```

#### 2. dxcam 리소스 명시적 해제

```python
# _cleanup()에서 dxcam 리소스 명시적 해제
if self._dxcam_camera is not None:
    import dxcam
    dxcam.delete(self._dxcam_camera)
    self._dxcam_camera = None
```

**핵심 원칙:**
- DirectX/COM 객체는 생성한 스레드에서만 사용해야 함
- 멀티스레드 환경에서 COM 객체 공유 시 주의 필요
- `ThreadPoolExecutor` 사용 시 COM 객체가 워커 스레드에서 생성됨에 주의

**수정된 파일:**
- `src/recorder/screen_recorder.py` - `_init_dxcam()` 함수 수정, `_cleanup()` dxcam 해제 추가

### 2026-01-26: 플레이 모드 경량화 (v1.0.63)

**목적:** 저사양 PC에서 플레이 모드 메모리/CPU 점유율 감소

**구현 내용:**
- 플레이 모드에서 불필요한 뷰 모듈 지연 로딩
- 에디터 모드에서만 로드되는 모듈:
  - RecorderView (녹화) - dxcam, cv2, screen_recorder 등
  - AnalyzerView (분석) - video_analyzer, template_matcher 등
  - PlayerView (에디터 실행)
  - SettingsView (설정)
  - GuideView (가이드)

**변경 방식:**
```python
# 변경 전: 최상단에서 모두 import
from .ui import MainWindow, RecorderView, AnalyzerView, PlayerView, SettingsView, GuideView

# 변경 후: MainWindow만 import, 나머지는 필요할 때 import
from .ui.main_window import MainWindow

def _create_views(self):  # 에디터 모드에서만 호출
    from .ui.recorder_view import RecorderView
    from .ui.analyzer_view import AnalyzerView
    # ...
```

**영향 없는 기능:**
- 플레이 모드 실행 기능 (RuleExecutor)
- 아두이노 마우스/키보드 (InputController, ArduinoHID)
- 이미지 매칭 (EnhancedMatcher)

**수정된 파일:**
- `src/app.py` - 지연 로딩 적용

---

### 2026-01-26: 플레이 모드 재생횟수 저장/로드 수정 (v1.0.62)

**문제:** 재생횟수를 4회로 저장하고 재시작하면 1회로 표시됨

**원인:** UI 초기화 시 기본값 "1"로 설정, 저장된 값 불러오기 안 함

**해결:**
- 시작 시 선택된 플랜의 저장된 재생횟수 자동 로드
- 플랜 변경 시 해당 플랜의 재생횟수 자동 로드
- 드롭다운에 command 연결 추가

**수정된 파일:**
- `src/ui/main_window.py` - 초기 재생횟수 로드, 플랜 변경 시 로드

---

### 2026-01-26: 프로그램 시작 시 자동 실행 기능 추가 (v1.0.60 ~ v1.0.61)

**기능:** 플레이 모드에서 프로그램 시작 시 지정한 플랜 자동 실행

**구현 내용:**

#### 1. 설정 추가 (config.py - PlayerConfig)
| 설정 | 설명 |
|------|------|
| `auto_run_enabled` | 자동 실행 활성화 여부 |
| `auto_run_plan` | 자동 실행할 플랜 파일 경로 |

#### 2. 환경설정 UI (settings_view.py)
- 재생 설정에 "프로그램 시작 시 자동 실행" 섹션 추가
- 활성화 토글
- 플랜 선택 드롭다운 (분석된 플랜 목록 표시)

#### 3. 자동 실행 로직 (app.py, main_window.py)
- **플레이 모드일 때만** 동작
- 5초 후 실행 (업데이트 확인, 아두이노 연결 완료 대기)
- 미니 플레이어에 설정된 **반복 횟수** 적용

**사용법:**
1. 환경설정 → 재생 → "프로그램 시작 시 자동 실행" 활성화
2. 드롭다운에서 실행할 플랜 선택
3. 미니 플레이어에서 반복 횟수 설정 (예: 4회)
4. 창 모드를 "플레이 모드"로 변경
5. 프로그램 재시작 → 5초 후 자동 실행 (설정한 횟수만큼 반복)

**수정된 파일:**
- `src/utils/config.py` - PlayerConfig에 auto_run_enabled, auto_run_plan 추가
- `src/ui/settings_view.py` - 자동 실행 설정 UI 추가
- `src/ui/main_window.py` - auto_run_plan() 메서드 추가
- `src/app.py` - _auto_run_plan() 메서드 추가, 5초 후 실행 예약

---

### 2026-01-25: 인식률 설정 통일 및 "다음 화면 대기" 버그 수정 (v1.0.53 ~ v1.0.59)

**증상:**
- 모니터링 액션을 **부분 실행**하면 인식률 97%로 정상 감지
- 이전 액션에서 **순차 실행**으로 넘어오면 "인식률 부족: 0.49 < 0.65" 에러 발생
- 사용자가 80% 인식률을 설정했는데 로그에는 0.65로 표시됨

**원인 분석:**

#### 1. 용어 혼란 (v1.0.53)
| 문제 | 해결 |
|------|------|
| 로그에 "신뢰도", "임계값" 혼용 | 모든 로그를 "인식률"로 통일 |

#### 2. 중복된 인식률 설정 (v1.0.53)
| 문제 | 해결 |
|------|------|
| `rule.confidence`, `watch.confidence`, `monitor_action.confidence` 3개 존재 | `rule.confidence`만 남기고 모두 제거 |
| 어떤 설정이 실제로 적용되는지 혼란 | UI에서 감시 이미지 인식률 슬라이더 제거 |

#### 3. 인식률 변경이 JSON에 저장 안 됨 (v1.0.55)
| 문제 | 해결 |
|------|------|
| UI에서 인식률 변경 시 `_modified = True`만 설정 | `_save_plan()` 즉시 호출 추가 |
| JSON 파일에는 이전 값 유지됨 | 변경 즉시 파일에 저장 |

```python
# player_view.py 수정
def save_confidence_only():
    rule.confidence = conf_var.get() / 100.0
    logger.info(f"트리거 이미지 인식률 저장: {int(conf_var.get())}%")
    self._modified = True
    self._save_plan()  # 추가: JSON 파일에 즉시 저장
```

#### 4. "다음 화면 대기" 하드코딩된 임계값 (v1.0.56~v1.0.59)

**"다음 화면 대기"란?**
- 현재 액션 완료 후, **다음 액션의 타겟 이미지**가 화면에 나타날 때까지 대기하는 기능
- 화면 전환이 완료됐는지 확인하는 용도

| 문제 | 해결 |
|------|------|
| 하드코딩된 `0.65` 임계값 사용 | v1.0.56: `next_rule.confidence` 사용으로 변경 |
| 검색 범위(`search_region`) 미적용 | v1.0.57: 다음 액션의 검색 범위 계산 및 전달 |
| 사용자 인식률(80%)이 타겟 이미지에도 적용됨 | v1.0.58~59: **45% 고정**으로 변경 |

**핵심 문제:**
- 사용자가 설정한 80%는 **감시 이미지** 감지용
- "다음 화면 대기"는 **타겟 이미지** 확인용 (다른 목적)
- 타겟 이미지가 46%로만 매칭되는데 80% 임계값 적용 → 실패
- 부분 실행 시에는 "다음 화면 대기" 없이 바로 감시 모드 진입 → 정상 작동

```python
# rule_executor.py - v1.0.59 최종 수정
# "다음 화면 대기"는 화면 전환 확인용이므로 낮은 임계값(0.45) 사용
next_confidence = 0.45  # 다음 화면 대기는 고정 45%

# 다음 액션의 검색 범위 계산
next_search_region = None
if next_rule:
    next_search_radius = getattr(next_rule, 'search_radius', 0) or 0
    next_action_x = getattr(next_rule, 'action_x', None)
    next_action_y = getattr(next_rule, 'action_y', None)
    if next_search_radius > 0 and next_action_x is not None and next_action_y is not None:
        import pyautogui
        screen_w, screen_h = pyautogui.size()
        x1 = max(0, next_action_x - next_search_radius)
        y1 = max(0, next_action_y - next_search_radius)
        x2 = min(screen_w, next_action_x + next_search_radius)
        y2 = min(screen_h, next_action_y + next_search_radius)
        next_search_region = [x1, y1, x2, y2]

location = self._find_image_on_screen(next_target_image, next_confidence, search_region=next_search_region)
```

**수정된 파일:**
- `src/player/rule_executor.py` - "다음 화면 대기" 로직, 로그 메시지
- `src/ui/player_view.py` - 인식률 저장 로직, 감시 이미지 슬라이더 제거
- `src/ui/analyzer_view.py` - UI 라벨 변경
- `src/i18n/ko.py` - "신뢰도" → "인식률" 번역 변경
- `src/utils/config.py` - 버전 업데이트 (1.0.53 → 1.0.59)

**교훈:**
- 같은 "이미지 인식률"이라도 용도가 다르면 별도 설정이 필요
- 하드코딩된 값은 나중에 문제 찾기 어려움
- 부분 실행과 순차 실행의 코드 경로가 다를 수 있음

---

### 2026-01-29: 저사양 PC 메모리 해제 개선 (v1.0.89)

**증상:** 플레이 모드에서 저사양 PC에서 WinCro가 아닌 다른 프로그램들이 간헐적으로 종료됨

**원인 분석:**
이미지 매칭 루프에서 화면 캡처 후 메모리가 즉시 해제되지 않아 메모리 누적 발생

**해결:**

#### 1. 명시적 메모리 해제 추가

| 파일 | 함수 | 변경 내용 |
|------|------|----------|
| `action_player.py` | `_find_all_images_on_screen` | `del` + `gc.collect()` 추가 |
| `action_player.py` | 이미지 대기 루프 | `del` 추가 (5초 로그용 스크린샷) |
| `action_player.py` | `_wait_for_image` | `del screen` 추가 |
| `rule_executor.py` | `_find_image_on_screen` | `del` + `gc.collect()` 추가 |
| `rule_executor.py` | `_wait_for_trigger` | 루프 내 `del` 추가 |
| `rule_executor.py` | `_find_all_images_on_screen` | `del` + `gc.collect()` 추가 |

```python
# 예시: _find_all_images_on_screen
screenshot = None
screenshot_np = None
screenshot_gray = None
result = None
try:
    screenshot = ImageGrab.grab()
    screenshot_np = np.array(screenshot)
    # ... 이미지 매칭 ...
finally:
    # 메모리 해제 (저사양 PC 지원)
    del screenshot, screenshot_np, screenshot_gray, result
    gc.collect()
```

**수정된 파일:**
- `src/player/action_player.py` - gc import 추가, 메모리 해제 코드 추가
- `src/player/rule_executor.py` - gc import 추가, 메모리 해제 코드 추가

---

### 2026-01-29: 플레이 모드 반복 횟수 버그 수정 (v1.0.89)

**증상:** 반복 횟수를 설정하고 저장 후 실행해도 1회만 실행되고 멈춤

**원인:** `rule_executor.py`의 `_execution_loop`에서 `plan.total_repeat_count`를 사용하지 않음

**해결:**

#### 1. `_execution_loop`에 전체 반복 루프 추가

```python
def _execution_loop(self) -> None:
    # 전체 반복 횟수 (기본값 1)
    total_repeat_count = getattr(plan, 'total_repeat_count', 1) or 1
    current_repeat = 0

    # 전체 반복 루프
    while current_repeat < total_repeat_count:
        current_repeat += 1
        if total_repeat_count > 1:
            logger.info(f"▶ 반복 {current_repeat}/{total_repeat_count} 시작")

        # 매 반복마다 결과 초기화
        if current_repeat > 1:
            self._results.clear()
            self._progress.initial_completed = 0

        # 모든 규칙 실행
        for i, (rule, step_num) in enumerate(all_rules_with_step):
            # ... 기존 실행 로직 ...
```

**수정된 파일:**
- `src/player/rule_executor.py` - `_execution_loop()` 전체 반복 루프 추가

---

### 2026-01-29: 에디터/플레이 모드 반복 횟수 동기화 (v1.0.89)

**증상:** 플레이 모드에서 반복 횟수 변경 시 에디터 모드 자동실행 설정에 반영 안 됨

**원인:**
1. 에디터 모드 설정에서 플랜 파일의 최신 `total_repeat_count`를 읽지 않음
2. 에디터 모드에서 반복 횟수 변경 시 플랜 파일에 저장 안 됨
3. 미니 플레이어와 rule_executor에서 이중 반복 발생 가능

**해결:**

#### 1. 에디터 모드 설정 로드 시 플랜 파일에서 최신 값 읽기

```python
# settings_view.py - _load_settings_to_ui
for i, seq_path in enumerate(self._seq_plan_paths):
    # 플랜 파일에서 최신 반복횟수 읽기
    repeat = 1
    try:
        if Path(seq_path).exists():
            with open(seq_path, "r", encoding="utf-8") as f:
                data = _json.load(f)
                repeat = data.get("total_repeat_count", 1) or 1
    except Exception:
        pass
    self._seq_plan_repeats.append(repeat)
```

#### 2. 에디터 모드에서 반복 횟수 변경 시 플랜 파일에 저장

```python
# settings_view.py - _seq_apply_repeat
# 플랜 파일에도 반복횟수 저장 (플레이 모드와 동기화)
try:
    if Path(plan_path).exists():
        with open(plan_path, "r", encoding="utf-8") as f:
            data = _json.load(f)
        data["total_repeat_count"] = new_count
        with open(plan_path, "w", encoding="utf-8") as f:
            _json.dump(data, f, ensure_ascii=False, indent=2)
except Exception as e:
    logger.error(f"플랜 반복횟수 저장 실패: {e}")
```

#### 3. 미니 플레이어 이중 반복 방지

```python
# main_window.py
# 플랜 객체에 반복횟수 설정 (rule_executor에서 사용)
plan.total_repeat_count = repeat_count
# 반복은 rule_executor에서 처리하므로 여기서는 1회만
self._mini_total_repeat = 1
```

**동기화 흐름:**
```
플레이 모드 반복횟수 저장 → 플랜 JSON 파일
                              ↓
에디터 모드 설정 열기 → 플랜 JSON에서 읽기 → 자동실행 목록에 표시
                              ↓
에디터 모드 반복횟수 변경 → 플랜 JSON 파일 → 플레이 모드에서 반영
```

**수정된 파일:**
- `src/ui/settings_view.py` - 설정 로드/저장 시 플랜 파일 동기화
- `src/ui/main_window.py` - 미니 플레이어 이중 반복 방지

---

## 메모

- 개발 완료: 2026-01-16
- 총 생성 파일: 28개
- 총 테스트 파일: 3개

- 프로젝트 시작 시간: 2026-01-16
- ??? ????: 2026-03-17 (? ?? ??? + ??/??? ?? ??? + ?? ?? ??)

## 업데이트 규칙

- **전체 업데이트 시 포함할 파일:**
  - 소스 코드 (`src/` 폴더)
  - PROGRESS.md
  - data/plans/*.json (사용자 플랜 파일)
  - data/config.json (설정 파일)
  - data/templates/*.png (템플릿 이미지 파일)
  - data/triggers/*.png (트리거 이미지 파일)

- **전체 업데이트 절차:**
  1. PROGRESS.md 업데이트 규칙 확인
  2. 버전 업데이트 (src/utils/config.py - APP_VERSION)
  3. PROGRESS.md 마지막 업데이트 날짜 갱신
  4. 수정된 파일 구문 검사 (python -m py_compile)
  5. 위 규칙의 모든 파일 git add
  6. 커밋 (v버전: 설명)
  7. 태그 생성 (v버전)
  8. master + 태그 푸시

---
이 파일은 Claude가 자동으로 업데이트합니다.
세션이 중단되어도 이 파일을 읽고 작업을 이어갈 수 있습니다.


### 2026-03-17: ? ?? ??? + ?? ??? ?? ?? + ?? ?? ?? ?? (v1.0.143)

- **? ?? ???:** ?? ?? ?? ??/UI ?? ??, ?? ?? ?? ?? ??, ?? ? ?? ? ??? ?????? ??? ?? ??? ?? ??
- **?? ?? ??:** `???? ? ????`, `mark_portal_entry`, `???? ??`, `???? ??/??/??` ?? ??? ?? ??? ?? ?? ?? ??
- **?? ??? ??:** `plan_20260205_000742.json` ?? ?? ? ?? `local_map`?? ?? ?? ??/route_ends? passable ??? ??
- **?? ?? ??:** `arrival_keys`? ?? `route_ends` ???? 1? ?? ??? ??? ?? ????? ??? ??? ???? ?? ??
- **APP_VERSION:** `1.0.142` -> `1.0.143`
- **?? ??:** `src/ui/player_view.py`, `src/player/rule_executor.py`, `src/utils/config.py`, `data/plans/plan_20260205_000742.json`, `data/update_cache.json`

---

### 2026-03-18: 보스방 응답없음 완화 + 상시스킬 on/off 확인 (v1.0.144)

- **증상:** 보스 경유지에서 순찰/추적/보스처치 직후 WinCro 창 자체가 `응답없음`으로 멈추거나, 정상 완료 직전에 재생이 중간 종료됨
- **원인 정리:**
  1. 보스 후보 감지/추적/순찰/타이밍 로그와 상태라벨 갱신이 메인 UI에 과도하게 누적됨
  2. 보스 완료 후 `_on_arrival()`과 `_stop_execution()`이 경쟁해 정상 완료가 끊길 수 있었음
  3. 일부 보스 분기에서 worker thread -> Tk 직접 호출 경로가 남아 있었음
- **수정:**
  - `src/ui/player_view.py`
    - `_ui_post()`, `_drain_ui_call_queue()`, `_flush_log_buffer()`로 UI 호출 메인스레드화
    - `_queue_normal_completion()` 추가로 정상 완료 예약 시 `finally`의 stop 처리와 경쟁하지 않도록 수정
    - `_schedule_boss_ui_log()`, `_schedule_boss_status()` 추가
    - 보스방의 후보감지/추적/순찰/방향없음/타이밍/완료/전환 로그를 보스 전용 dedupe 경로로 통일
    - 보스 전용 UI 큐 백프레셔 추가
- **검증:**
  - `player_view.py` `py_compile` 통과
  - `auto_skill_enabled`가 `False`일 때 `player_view.py`, `rule_executor.py` 모두 상시스킬 입력 분기에 진입하지 않음을 확인
- **APP_VERSION:** `1.0.143` -> `1.0.144`

---

