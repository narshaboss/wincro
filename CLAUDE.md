# WinCro (윈크로) - 프로젝트 문서

## 프로젝트 개요
영상 녹화 기반 업무 자동화 RPA 프로그램. 사용자가 화면을 녹화하면 입력 로그를 분석하여 마우스/키보드 동작을 추출하고, 이미지 매칭 기반으로 동작을 재현합니다.

**현재 버전:** 1.0.156

---

## 기술 스택

| 분류 | 기술 |
|------|------|
| 언어 | Python 3.11+ |
| GUI | CustomTkinter (다크 테마) |
| 데이터베이스 | SQLite |
| 화면 캡처 | dxcam (DirectX), mss (GDI 폴백) |
| 입력 자동화 | pyautogui, pynput, Win32 API |
| 이미지 처리 | OpenCV, Pillow, numpy |
| 하드웨어 입력 | Arduino Leonardo (선택) |

---

## 프로젝트 구조

```
C:\Projects\wincro\
├── src/
│   ├── main.py                    # CLI 진입점
│   ├── app.py                     # WinCroApp 메인 클래스
│   │
│   ├── recorder/
│   │   ├── screen_recorder.py     # 화면 녹화 (dxcam/mss)
│   │   └── input_logger.py        # 입력 이벤트 로깅 (pynput)
│   │
│   ├── analyzer/
│   │   ├── video_analyzer.py      # 영상 분석, 스크린샷 추출
│   │   ├── action_extractor.py    # 입력 로그 → 액션 변환
│   │   ├── automation_models.py   # AutomationPlan, AutomationRule
│   │   ├── template_matcher.py    # 기본 템플릿 매칭
│   │   └── enhanced_matcher.py    # 향상된 매칭 (다중 방법)
│   │
│   ├── player/
│   │   ├── rule_executor.py       # 규칙 기반 실행 엔진
│   │   ├── action_player.py       # 액션 실행 (레거시)
│   │   ├── simple_pathfinder.py   # A* 경로탐색 알고리즘
│   │   ├── game_map.py            # 맵 데이터 (이동가능/장애물)
│   │   └── obstacle_avoidance.py  # 8방향 장애물 회피 (레거시)
│   │
│   ├── database/
│   │   ├── models.py              # Action, Sequence, ExecutionLog
│   │   └── db_manager.py          # SQLite 관리자
│   │
│   ├── ui/
│   │   ├── main_window.py         # 메인 윈도우
│   │   ├── recorder_view.py       # 녹화 탭
│   │   ├── analyzer_view.py       # 분석 탭
│   │   ├── player_view.py         # 실행 탭
│   │   ├── settings_view.py       # 설정 탭
│   │   ├── guide_view.py          # 가이드 탭
│   │   ├── help_dialog.py         # 도움말 다이얼로그
│   │   └── log_view.py            # 로그 패널
│   │
│   ├── utils/
│   │   ├── config.py              # 설정 관리 (AppConfig)
│   │   ├── logger.py              # 로깅 설정
│   │   ├── input_controller.py    # 입력 추상화 (pyautogui/Arduino)
│   │   ├── digit_templates.py     # 숫자 템플릿 매칭 (좌표 OCR)
│   │   ├── arduino_hid.py         # Arduino HID 통신
│   │   ├── arduino_uploader.py    # Arduino 펌웨어 업로드
│   │   ├── admin.py               # 관리자 권한
│   │   ├── updater.py             # GitHub 업데이트 확인
│   │   ├── security.py            # 암호화 (Fernet)
│   │   ├── window_position.py     # 윈도우 위치 저장
│   │   └── self_test.py           # 시스템 자가 진단
│   │
│   └── i18n/
│       └── ko.py                  # 한글 번역
│
├── arduino/
│   └── wincro_hid.ino             # Arduino Leonardo 펌웨어
│
├── data/
│   ├── config.json                # 사용자 설정
│   ├── wincro.db                  # SQLite 데이터베이스
│   ├── recordings/                # 녹화 영상 (.mp4) + 입력 로그 (.json)
│   ├── templates/                 # 클릭 스크린샷 이미지
│   ├── plans/                     # 자동화 플랜 (.json)
│   ├── sequences/                 # 시퀀스 파일
│   ├── triggers/                  # 트리거 이미지
│   ├── maps/                      # 특화모드 맵 데이터 ({name}_map.json)
│   └── digit_templates/           # 숫자 템플릿 (좌표 읽기용)
│
└── logs/                          # 로그 파일
```

---

## 핵심 워크플로우

```
[1. 녹화]                    [2. 분석]                    [3. 실행]
     │                            │                            │
ScreenRecorder              VideoAnalyzer               RuleExecutor
     │                            │                            │
     ├─ dxcam (DirectX)          ├─ 입력로그 파싱              ├─ 이미지 매칭
     ├─ mss (GDI 폴백)           ├─ 클릭 스크린샷 추출         ├─ pyautogui
     └─ .mp4 저장                └─ AutomationPlan 생성       └─ Arduino HID
     │                            │                            │
InputLogger                 ActionExtractor             InputController
     │                            │                            │
     ├─ pynput                   ├─ 클릭/드래그 감지           ├─ 마우스 이동
     ├─ 클릭 자동 캡처            ├─ 키보드 입력               ├─ 클릭
     └─ .json 저장               └─ 대기시간 계산              └─ 타이핑
```

---

## 주요 모듈 상세

### 1. Recorder - 화면 녹화

**파일:** `src/recorder/screen_recorder.py`

**캡처 엔진:**
- **dxcam (DirectX):** 우선 사용, 고성능
- **mss (GDI):** 폴백, 항상 작동

**주요 기능:**
```python
ScreenRecorder.start(region, output_name)   # 녹화 시작
ScreenRecorder.stop()                        # 녹화 중지, 경로 반환
ScreenRecorder.pause() / resume()            # 일시정지/재개
ScreenRecorder.get_current_frame()           # 현재 프레임 (스크린샷용)
```

**녹화 흐름:**
1. VideoWriter 초기화 (mp4v 코덱)
2. dxcam 초기화 시도 → 실패 시 mss 폴백
3. 캡처 루프 (설정된 FPS)
4. 프레임을 VideoWriter + 버퍼에 저장

---

### 2. Recorder - 입력 로깅

**파일:** `src/recorder/input_logger.py`

**이벤트 타입:**
- `mouse_move`, `mouse_click`, `mouse_scroll`
- `mouse_drag_start`, `mouse_drag_end`
- `key_press`, `key_release`

**클릭 vs 드래그 판정:**
```python
# 드래그 조건 (둘 다 만족해야 함)
distance >= drag_threshold_distance (기본 25px)
duration >= drag_threshold_time (기본 0.15초)
```

**특수 키:**
- **F7:** 녹화 중지
- **F8:** 전체화면 스크린샷 캡처

**RecordingSession:**
- ScreenRecorder + InputLogger 통합
- 클릭 시 자동 스크린샷 캡처

---

### 3. Analyzer - 영상 분석

**파일:** `src/analyzer/video_analyzer.py`

**분석 흐름:**
```python
1. 입력 로그에서 액션 추출 (ActionExtractor)
2. 비디오 시간과 입력 로그 시간 동기화
3. 클릭 위치 스크린샷 추출 (150x150 크롭)
4. AutomationPlan 생성
```

**시간 동기화:**
```python
# 비디오와 입력 로그의 시간 차이가 10% 이상이면 보정
time_scale = video_duration / input_log_duration
adjusted_timestamp = original_timestamp * time_scale
target_frame = int(adjusted_timestamp * fps) - 1  # 클릭 직전 프레임
```

**ActionExtractor** (`action_extractor.py`):
- 입력 로그 JSON → Action 리스트 변환
- 클릭, 더블클릭, 우클릭, 드래그, 스크롤, 키 입력 처리

---

### 4. Analyzer - 자동화 모델

**파일:** `src/analyzer/automation_models.py`

**AutomationRule:**
```python
rule_id: str              # 규칙 ID
rule_type: str            # FIXED_SEQUENCE, TYPE_TEXT, HOTKEY 등
action_type: str          # click, double_click, type, hotkey, scroll, drag
action_x, action_y: int   # 클릭 좌표
action_text: str          # 타이핑 텍스트
action_keys: list         # 단축키 목록
target_image: str         # 매칭 이미지 경로
confidence: float         # 매칭 신뢰도 (기본 0.8)
search_radius: int        # 검색 범위 (0=전체, >0=반경)
wait_after: float         # 동작 후 대기시간
timeout: float            # 이미지 탐색 타임아웃
skip_on_not_found: bool   # 이미지 미발견 시 스킵
drag_to_x, drag_to_y: int # 드래그 종료 좌표
drag_duration: float      # 드래그 소요 시간
```

**모니터링 감시 항목 (monitoring_watches):**
```python
{
    "image": str                    # 감시 이미지 경로
    "goto_index": int               # 점프할 액션 인덱스
    "search_region": List[int]      # 검색 범위 [x1, y1, x2, y2]
    "monitor_actions": List[dict]   # 모니터링 액션 목록
    "condition_image": str          # 점프 조건 이미지 (없으면 항상 점프)
    "condition_search_region": List[int]  # 조건 이미지 검색 범위
    "condition_confidence": float   # 조건 이미지 인식률 (기본 0.65)
}
```

**AutomationPlan:**
```python
name: str                       # 플랜 이름
initial_rules: List[Rule]       # 실행할 규칙 목록
monitoring_rules: List[Rule]    # 모니터링 규칙
video_path: str                 # 원본 영상 경로
input_log_path: str             # 입력 로그 경로
game_mode: GameModeConfig       # 게임 특화 모드 설정
```

**GameModeConfig:**
```python
enabled: bool                   # 게임 모드 활성화
character_image: str            # 캐릭터 이미지 경로
target_image: str               # 목표 이미지 경로
character_confidence: float     # 캐릭터 인식률
target_confidence: float        # 목표 인식률
arrival_threshold: int          # 도달 판정 거리 (픽셀)
search_region: List[int]        # 검색 영역 [x1, y1, x2, y2]
smooth_move: bool               # 스무스 이동 (키 홀드 방식)
move_skill_key: str             # 이동 스킬 키
move_skill_distance: int        # 이동 스킬 사용 거리 (픽셀)
auto_skill_key: str             # 상시 스킬 키
auto_skill_cooldown_image: str  # 스킬 쿨타임 이미지
mapping_enabled: bool           # 이동 시 맵 데이터 기록 여부
move_skill_enabled: bool        # 이동 스킬 사용 여부
auto_skill_enabled: bool        # 상시 스킬 사용 여부
```

#### 특화모드 AI 알고리즘 (좌표 기반 이동)

**핵심 파일:**
| 파일 | 역할 |
|------|------|
| `src/player/simple_pathfinder.py` | A* 경로탐색 알고리즘 |
| `src/player/game_map.py` | 맵 데이터 (이동가능/장애물 좌표) |
| `src/player/obstacle_avoidance.py` | 8방향 장애물 회피 (레거시, 현재 미사용) |
| `src/utils/digit_templates.py` | 숫자 템플릿 매칭 (화면 좌표 읽기) |
| `data/maps/{name}_map.json` | 맵 저장 파일 |

**알고리즘 흐름:**
```
[화면 좌표 읽기] → [GameMap 조회] → [A* 경로탐색] → [방향키 입력] → [이동 결과 기록]
     │                  │                │                              │
DigitTemplateMatcher  game_map      SimplePathfinder              mark_passable()
 (템플릿 매칭)     (벽/이동가능)    (최단경로 계산)              mark_blocked()
```

**A* 경로탐색 (SimplePathfinder):**
- `find_path(start, goal, allow_unknown)` - A* 알고리즘으로 최단 경로 계산
- 맨해튼 거리 휴리스틱 사용
- 이동 비용: passable/unknown = 1, soft_blocked = 5, blocked = 통과 불가
- 1차: 알려진 이동가능 타일만으로 탐색 (`allow_unknown=False`)
- 2차: 미탐색 영역 포함 탐색 (`allow_unknown=True`)
- 3차: 경로 없으면 목표 방향으로 직진 (폴백)
- 장애물 발견 시 경로 자동 재계산

**GameMap 데이터 구조:**
```python
passable: Set[Tuple[int, int]]       # 이동 가능한 좌표들
blocked: Set[Tuple[int, int]]        # 장애물 좌표들 (영구벽)
soft_blocked: Dict[Tuple[int,int], int]  # 임시 장애물 {(x,y): fail_count}
```
- 이동 성공 → `mark_passable(x, y)` + `clear_soft_blocked(x, y)`
- 2회 연속 이동 실패 → `mark_soft_blocked(wall_x, wall_y)` 임시벽 등록
- 같은 좌표에서 5회 이상 실패 → `mark_blocked()` 영구벽 승격
- 10회 루프마다 `tick()` 호출 → soft_blocked fail_count 자동 감소
- 맵 데이터는 `data/maps/` 에 JSON으로 저장/로드
- 다음 실행 시 기존 맵 데이터를 `load_and_merge()` 로 누적 활용

**맵 저장 시점:**
1. "저장" 버튼 클릭 시
2. 다이얼로그 닫을 때
3. 실행 중지 시
4. 맵핑 중 10칸마다 자동 저장
5. 벽 발견 시 즉시 저장
6. 액션 실행 종료 시 (rule_executor)

**탈출 스킬 (escape_skill):**
- 연속 정체 횟수가 `escape_skill_stuck_threshold` 초과 시 발동
- 스킬 키 → 목표 방향키 연타 → Enter 키 순서 실행
- 쿨타임 적용 (`escape_skill_cooldown`)

**실행 경로 (테스트/액션 통일):**
- UI 테스트 실행: `player_view.py` → `_run_coordinate_loop()` → SimplePathfinder + A*
- 액션 실행: `rule_executor.py` → `execute_game_mode_coordinate()` → SimplePathfinder + A*
- 두 경로 모두 동일한 A* 알고리즘 + GameMap 활용

---

### 5. Player - 규칙 실행

**파일:** `src/player/rule_executor.py`

**실행 상태:**
```
IDLE → RUNNING_INITIAL → MONITORING → COMPLETED
                ↓
             PAUSED
                ↓
          STOPPED / FAILED
```

**이미지 매칭:**
```python
# search_radius가 설정된 경우 (클릭 액션은 기본 120px)
# 해당 영역에서만 이미지 검색 → 오탐 방지
search_area = (action_x - radius, action_y - radius,
               action_x + radius, action_y + radius)
```

**마우스 제어 우선순위:**
1. pynput MouseController (가장 안정적)
2. ctypes SetCursorPos + mouse_event
3. SendInput API (가장 저수준)

**멀티모니터 지원:**
- 가상 데스크톱 전체 좌표 사용
- MOUSEEVENTF_VIRTUALDESK 플래그

---

### 6. Arduino HID

**파일:** `src/utils/arduino_hid.py`

**하드웨어:** Arduino Leonardo (ATmega32U4)

**프로토콜:** Serial @ 115200 baud

| 명령 | 설명 |
|------|------|
| `MC,L/R/M` | 마우스 클릭 |
| `MP,L/R/M` | 마우스 버튼 누름 |
| `MR,L/R/M` | 마우스 버튼 뗌 |
| `MS,amount` | 스크롤 |
| `KP,keycode` | 키 누름 |
| `KR,keycode` | 키 뗌 |
| `KT,text` | 텍스트 타이핑 |
| `KD,delay_ms` | 타이핑 딜레이 설정 |
| `KA` | 모든 키 해제 |
| `PING` | 연결 테스트 (PONG 응답) |

**InputController** (`input_controller.py`):
- Arduino HID 활성화 시: 마우스 이동은 pyautogui, 클릭/키보드는 Arduino
- 비활성화 시: 모두 pyautogui

---

### 7. 설정 구조

**파일:** `src/utils/config.py`

```python
AppConfig:
    recording:
        fps: 30
        quality: "high"
        drag_threshold_distance: 25
        drag_threshold_time: 0.15

    analyzer:
        template_match_threshold: 0.8
        dialog_timeout_seconds: 300

    player:
        speed_multiplier: 1.0
        default_wait_ms: 500
        mouse_move_duration: 0.2
        typing_interval: 0.05
        retry_count: 3
        emergency_stop_key: "escape"
        emergency_stop_count: 2

    ui:
        theme: "dark"
        language: "ko"
        window_mode: "medium"  # small, medium, large
        show_help_on_startup: False
        run_as_admin: False
        app_name: "Desktop"
        random_name_mode: False

    arduino:
        enabled: False
        com_port: ""
        baud_rate: 115200
        auto_connect: False

    update:
        github_repo: "narshaboss/wincro"
        auto_check: False
```

---

## UI 구조

**윈도우 모드:**
| 모드 | 크기 | 기능 |
|------|------|------|
| small | 480x320 | 미니 플레이어 |
| medium | 1200x800 | 전체 UI |
| large | 최대화 | 전체 UI |

**탭 구성:**
1. **녹화 (RecorderView):** 영역 선택, 녹화 제어
2. **분석 (AnalyzerView):** 녹화 파일 선택, 분석 실행
3. **실행 (PlayerView):** 플랜 선택, 실행 제어
4. **설정 (SettingsView):** 모든 설정 관리
5. **가이드 (GuideView):** 사용법 안내

---

## 코딩 규칙

- PEP 8 준수
- 한글 docstring
- 타입 힌트 사용
- logging 모듈 사용 (DEBUG, INFO, WARNING, ERROR)
- try-except 예외 처리

### 코드 수정 시 필수 체크리스트

**v1.0.105 교훈:** import 제거/변경 시 전체 검토 안 해서 버그 발생

| 단계 | 내용 |
|------|------|
| 1 | 변경할 키워드를 **Grep으로 전체 검색** (예: `import ssl` 제거 시 `ssl.` 전체 검색) |
| 2 | 수정 후 **해당 함수 호출부까지 확인** |
| 3 | **구문 검사** (`python -m py_compile`) |
| 4 | **논리 오류 검토** (None 체크, 타입 체크, 예외 처리) |
| 5 | 관련 파일이 여러 개면 **모든 파일 동시 검토** |

**자주 놓치는 버그:**
- import 제거 후 해당 모듈 참조 남아있음 → NameError
- 백그라운드 스레드에서 UI 객체 접근 시 None 체크 누락 → AttributeError
- 같은 기능인데 파일마다 다른 방식으로 구현 → 불일치

---

## 단축키

| 키 | 기능 |
|-----|------|
| F7 | 녹화 중지 |
| F8 | 전체화면 스크린샷 캡처 |
| ESC×2 | 실행 긴급 중지 |

---

## 배포 절차

### 1. 버전 업데이트
```python
# src/utils/config.py
APP_VERSION = "X.X.X"
```

### 2. Git 커밋
```bash
git add src/
git add data/plans/
git add data/templates/
git commit -m "vX.X.X: 변경 내용"
git tag vX.X.X
git push origin master
git push origin vX.X.X
```

### 3. 확인
- GitHub Actions 빌드 완료
- Release에 zip 파일 업로드

---

## 데이터 경로

**exe 실행 시:**
```
WinCro.exe
└── _internal/
    ├── data/
    │   ├── config.json
    │   ├── wincro.db
    │   ├── recordings/
    │   ├── templates/
    │   └── plans/
    └── logs/
```

**스크립트 실행 시:**
```
wincro/
├── data/
└── logs/
```

---

## Claude 작업 지침

### 대화 기록
- **중요한 대화 내용은 주기적으로 이 문서에 기록할 것**
- 새로운 기능 추가, 버그 수정, 설계 결정 등 중요 사항 기록
- 세션 종료 전 작업 내용 요약 추가

### 기록 위치
아래 "작업 히스토리" 섹션에 날짜별로 기록

---

## 작업 히스토리

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
- **맵 오염 차단:** 맵이 어느 정도 쌓인 뒤 현재 클러스터에서 너무 멀리 떨어진 좌표는 `passable/blocked/soft_blocked`로 등록하지 않도록 좌표 sanity 필터 추가
- **저장 안정화:** 맵 저장 전 상태를 정리하고, 임시 파일에 저장 후 교체하는 원자 저장으로 JSON 중간 손상과 읽기 경쟁 완화
- **포탈 이탈 차단:** 맵핑 중 포탈 점프가 감지되면 진입 방향만 벽으로 등록하고 현재 맵 테스트를 즉시 중지해 다른 맵 좌표가 현재 맵에 섞이지 않도록 수정
- **출발 포탈 보호 강화:** 전체맵핑 중 출발 포탈 보호 타일을 탐색/A*/되돌아가기 후보에서 일관되게 제외해 다음 굴/이전 굴로 잘못 넘어가는 치명 버그 완화
- **벽 검증 집중화:** 의심 타일을 `probe_focus_target`으로 고정해 1~2번 찔러보고 다른 곳으로 새지 않도록 벽 검증 유지/복귀 흐름 추가
- **수정 파일:** `src/player/game_map.py`, `src/ui/player_view.py`, `src/utils/config.py`

### 2026-02-08: 모니터링 조건 이미지 체크 시점 변경 (v1.0.115)
- **문제:** 조건 이미지 체크가 모니터링 액션 실행 후에 수행되어, 조건 미충족 시에도 불필요한 액션이 실행됨
- **수정:** 조건 이미지 체크를 모니터링 액션 실행 전으로 이동. 조건 미충족(조건 이미지 발견) 시 모니터링 액션+점프 모두 건너뜀
- **수정 파일:** `src/player/rule_executor.py`

### 2026-02-08: 경로탐색 버그 3건 수정 + 전체맵핑 안정화 (v1.0.111)
- **BFS O(N²) 최적화:** `obstacle_avoidance.py` BFS 경로탐색에서 큐에 전체 경로 저장 → `came_from` dict + 역추적 패턴으로 O(N) 최적화
- **list.index() 중복 좌표 버그:** `simple_pathfinder.py` 경로에 중복 좌표 있을 때 `list.index()`가 첫 번째만 반환 → 현재 인덱스 기준 순방향/역방향 탐색으로 수정
- **대각선 방향 점수 오류:** `obstacle_avoidance.py` dx==0일 때 잘못된 방향에 +1 점수 → 축 정렬 시 중립 점수(0) 부여
- **전체맵핑 안정화:** `full_mapping_exploring` 플래그 정상 동작 확인 (player_view.py)
- **수정 파일:** `obstacle_avoidance.py`, `simple_pathfinder.py`, `player_view.py`, `config.py`

### 2026-02-07: 경유지 이미지 시스템 리팩토링 + 안정성 개선 (v1.0.109)
- **경유지 이미지 시스템 변경:** 보스 이미지 pipe-delimited 문자열(`|`구분) → dict 구조로 변경
  - `wp[3]`이 `{"target_image", "target_images", "confidence", "search_radius", ...}` dict
  - `automation_models.py`: `_serialize_waypoints()` / `_deserialize_waypoints()` 추가 (이미지 경로 ↔ 상대경로 변환)
  - `player_view.py`: 보스 이미지 대화상자 제거 → `ImageCropDialog` 기반으로 전환
  - `_edit_waypoint_image()`, `_sync_rule_to_wp_image()`, `_update_card_image_status()` 신규 메서드
- **보스 스킬 설정 제거:** `boss_skill_enabled`, `boss_skill_key`, `boss_skill_cooldown` UI/로직 전부 제거
- **비동기 로드 경쟁 방지:** `analyzer_view.py`, `player_view.py`에 `_load_generation` 카운터 추가 — sync 로드가 async 로드보다 먼저 완료되면 오래된 async 결과 무시
- **뷰 전환 안정화:** `main_window.py` `_switch_view()` — 새 뷰 준비된 후에만 이전 뷰 숨기기 (깜빡임 방지)
- **리소스 정리 확장:** `app.py` `_cleanup()`에 `settings_view`, `guide_view` 추가
- **map_canvas 타일 배치 개선:** 순찰 타일은 오버레이로 동작 (passable 유지), 벽 배치 시 순찰에서도 제거
- **analyzer_view 분석 취소:** `_on_cancel()`에서 `_is_analyzing = False` 상태 정리 추가
- **이미지 정리 강화:** 미사용 이미지 삭제 시 `target_images` 목록도 고려
- **부분실행 에러 콜백:** `PlanDetailDialog` 에러 시 `_on_execution_complete()` 호출 추가
- **settings_view import 정리:** `import json` 지역 import → 모듈 레벨 import
- **수정 파일:** `app.py`, `main_window.py`, `player_view.py`, `analyzer_view.py`, `settings_view.py`, `automation_models.py`, `map_canvas.py`, `config.py`

### 2026-02-07: UI 로딩 성능 최적화 (페이지 렉 해소)
- **문제:** 에디터 모드 진입 시 5개 뷰가 모두 동기 생성 → 2~5초 블로킹, 탭 전환 시 `refresh()` 자동 호출 → DB 재쿼리 + JSON 재파싱 + 위젯 전부 삭제/재생성 → 체감 렉
- **최적화 내역 (7개 파일):**
  1. `app.py`: 뷰 지연 생성 — 팩토리 등록 방식으로 변경, 탭 처음 클릭 시에만 뷰 생성
  2. `main_window.py`: `_switch_view()`에서 팩토리 지연 생성 지원, `refresh()` 자동 호출 제거
  3. `analyzer_view.py`: `__init__`에서 `_load_recordings()` + `_load_plans()` 제거 → `after(0)` 콜백 + 백그라운드 스레드 로딩, `_create_recording_item`의 루프 내 JSON I/O → 캐시로 대체, `_create_plan_item`의 루프 내 DB 쿼리 → 캐시로 대체
  4. `player_view.py`: `__init__`에서 `_load_sequences()` + `_load_automation_plans()` 제거 → `after(0)` + 백그라운드 스레드, `_create_plan_item`의 루프 내 DB 쿼리 → 캐시로 대체, SequenceDetailDialog 썸네일 동기 로딩 → 비동기 로딩 (PlanDetailDialog 패턴 적용)
  5. `recorder_view.py`: `_refresh_recordings_list()` → `after(0)` 지연
  6. `settings_view.py`: `_load_settings()` → `after(0)` 지연
  7. `monitoring_editor.py`: `_load_thumbnail()`에 글로벌 썸네일 캐시 적용
- **핵심 패턴:**
  - 뷰 팩토리 (`register_view_factory`) — 탭 첫 접근 시에만 뷰 생성 (첫 탭만 즉시 생성)
  - `after(0)` 콜백 — UI 프레임 렌더 후 데이터 로드 (블로킹 방지)
  - 백그라운드 스레드 + `self.after(0, callback)` — JSON 파싱을 메인 스레드에서 분리
  - 데이터 캐시 (`_plan_modified_cache`, `_plan_lock_cache`) — 루프 내 파일 I/O/DB 쿼리 방지
  - 썸네일 비동기 로딩 — 플레이스홀더 → 백그라운드 로드 → UI 갱신

### 2026-02-05: 특화모드(좌표모드) 버그 4건 수정
- **BUG 1 (CRASH):** `rule_executor.py:3145` - 탈출스킬 방향키에 `move_keys.get()` 기본값 없음 → `pyautogui.press(None)` crash
  - **수정:** `config.move_keys.get(dir_name, dir_name)` 기본값 추가
- **BUG 2 (무한 루프):** `rule_executor.py:3041` + `player_view.py:3393` - 좌표 읽기 연속 실패 시 제한 없음 → 수천 회 무의미 반복
  - **수정:** `coord_fail_count` 카운터 추가, 연속 50회 실패 시 자동 중단
- **BUG 3 (데이터 누락):** `rule_executor.py:3057` + `player_view.py:3410` - 첫 반복에서 시작 위치가 passable로 등록 안 됨
  - **수정:** `prev_x is None`일 때 `mark_passable(current_x, current_y)` 추가
- **BUG 4 (CRASH):** `rule_executor.py:2921` - `escape_skill_key`가 빈 문자열이면 `pyautogui.press("")` crash
  - **수정:** `or 'z'` 폴백 추가

### 2026-02-05: 동작분석 액션 많을 때 렉 개선
- **문제:** 동작분석 후 액션 수가 많아지면 UI가 심하게 렉걸림
- **원인 3가지:**
  1. `_get_flat_rules_with_depth()`가 O(n²) — 매 규칙마다 전체 결과 리스트를 순회해서 형제 번호 계산
  2. 썸네일을 메인 스레드에서 동기 로딩 — 캐시 미스 시 `np.fromfile()` → `cv2.imdecode()` → `cv2.resize()` 블로킹
  3. `_refresh_action_list()`에서 매번 전체 규칙에 대해 디버그 루프 실행
- **수정:**
  1. `player_view.py:_get_flat_rules_with_depth()` — O(n²) → O(n) 카운터 방식으로 변경. `child_counters` dict로 부모별 자식 번호 즉시 계산
  2. `player_view.py:_display_thumbnail()` — 캐시 히트 시 즉시 표시, 캐시 미스 시 `📷` 플레이스홀더 표시 후 백그라운드 스레드에서 이미지 로딩 → `self.after(0, _apply)`로 UI 갱신
  3. `player_view.py:_refresh_action_list()` — 불필요한 디버그 순회 루프 제거

### 2026-02-05: 프로그램 멈춤(Freeze/Hang) 방지 수정
- **문제:** 간헐적으로 프로그램이 응답 없음 상태로 전환
- **수정 내역 (총 13건):**
  1. `rule_executor.py:2051` - 모니터링 while True 루프에 안전 타임아웃 2시간 추가
  2. `action_player.py:723` - 일시정지 루프가 타임아웃을 우회하는 문제 수정 (pause 시간도 timeout에 포함)
  3. `recorder_view.py:542` - 비동기 폴링에 최대 300회(30초) 재시도 제한 추가
  4. `video_analyzer.py:461` - `future.result()` 호출에 `timeout=60` 추가
  5. `db_manager.py:34` - `threading.Lock()` → `threading.RLock()` 변경 (싱글톤 재진입 교착 방지)
  6. `digit_templates.py` - 3곳의 `ImageGrab.grab()` → `_safe_grab()` (5초 타임아웃 보호)
  7. `app.py:263` - `_auto_connect_arduino()` 시리얼 I/O를 백그라운드 스레드로 이동
  8. `main_window.py:522` - 백그라운드 플랜 로드 실패 시 예외 처리 추가 (UI 복원 보장)
  9. `monitoring_editor.py:546,1119` - 백그라운드 스레드에서 `.configure()` → `self.after(0, ...)` 래핑
  10. `player_view.py:4534,4723,4862` - 3곳의 `ImageGrab.grab()` → `_safe_grab()` (5초 타임아웃 보호)
  11. `player_view.py:2621,6550` - 2곳의 `pyautogui.screenshot()` → `_safe_grab()` (5초 타임아웃 보호)
- **핵심 패턴:** `_safe_grab()` 함수 - `ImageGrab.grab()`을 별도 스레드에서 실행, 5초 타임아웃으로 DWM 행 방지

### 2026-02-05: Soft Blocked 시스템 도입 (몬스터 임시벽 구분)
- **문제:** 던전 몬스터를 만나면 2회 이동 실패 → 영구벽(`blocked`)으로 등록 → 맵 데이터 오염
- **해결:** `soft_blocked`(임시벽) 시스템 도입. 이동 실패 시 즉시 영구벽이 아닌 임시벽으로 등록
- **핵심 동작:**
  - 이동 실패 2회 → `mark_soft_blocked()` (임시벽, fail_count 누적)
  - fail_count >= 5 → `mark_blocked()` 영구벽 승격
  - 이동 성공 시 → `clear_soft_blocked()` 즉시 해제
  - 10회 루프마다 `tick()` → fail_count 자동 감소, 0이면 만료
  - A* 비용: soft_blocked = 5 (우회 선호), blocked = 통과 불가
- **수정 파일:**
  - `src/player/game_map.py`: `soft_blocked: Dict` 추가, 6개 메서드 추가 (`mark_soft_blocked`, `clear_soft_blocked`, `is_soft_blocked`, `get_soft_blocked_cost`, `tick`), save/load/merge/stats 등 수정
  - `src/player/simple_pathfinder.py`: A* 비용 `g_cost + 1` → `g_cost + get_soft_blocked_cost()` 변경
  - `src/player/rule_executor.py`: `mark_blocked` → `mark_soft_blocked`, `clear_soft_blocked` 추가, `tick()` 호출
  - `src/ui/player_view.py`: rule_executor.py와 동일 변경, 통계 표시에 임시벽 카운트 추가
  - `src/player/map_canvas.py`: 주황색(`#e8a040`) 임시벽 렌더링, 범례/클릭팝업 업데이트
  - `src/player/map_visualizer.py`: `▒` 기호 추가, 범례 업데이트
- **하위 호환:** 기존 맵 JSON에 `soft_blocked` 키 없어도 정상 로드
- **복원:** `docs/pathfinding_algorithm.md`에 v1.0.107 전체 코드 백업 보관

### 2026-02-03: 게임 충돌 원인 분석 및 ReleaseCapture 제거
- **문제:** 플레이 모드에서 게임 2개 + WinCro 실행 중 다른 프로그램들이 간헐적으로 충돌 (WinCro는 안 튕김)
- **원인 분석:** `ReleaseCapture()` 과다 호출 - 클릭 1회당 6~10회 호출
  - 게임이 마우스 캡처 중일 때 (드래그, 카메라 회전, 스킬 시전) ReleaseCapture() 호출되면 충돌
  - 간헐적인 이유: 게임이 캡처 안 쓰는 순간이면 괜찮고, 쓰는 순간이면 충돌
- **수정 파일:**
  - `src/utils/input_controller.py`: `_release_mouse_capture()` 함수 내용을 `pass`로 변경
  - `src/player/rule_executor.py`: 6개 위치의 ReleaseCapture()/ClipCursor() 호출 주석 처리
    - 라인 189-190: `_win32_move_click()` 함수 시작부
    - 라인 248-249: SendInput 시도 전
    - 라인 315-316: `_win32_force_click_at()` 함수
    - 라인 637-638: 사용자 개입 후 재개 시
    - 라인 1298-1299: 마우스 이동 루프
    - 라인 1795-1796: 트리거 후 준비
- **검증:** 두 파일 모두 구문 검사 통과 (py_compile)
- **추가 조사 필요 시:** 화면 캡처 간격 조정 (0.2초→0.5초), dxcam→mss 전환

### 2026-01-31: 업데이트 시 환경설정 초기화 버그 수정 (v1.0.106 → v1.0.107)
- **문제:** 업데이트할 때마다 환경설정(config.json)이 초기화됨
- **원인:** `settings_view.py` 배치 파일에 config.json 복원 코드 누락
- **추가 버그:** 1685행 `taskkill /f /im dwm.exe` - 잘못된 프로세스 (dwm.exe는 Windows Desktop Window Manager)
- **수정:** config.json 복원 코드 추가, taskkill 대상을 `{exe_name}`으로 수정

### 2026-01-31: 플레이 모드 반복횟수 저장 버그 수정 (v1.0.105 → v1.0.106)
- **문제:** 플레이 모드에서 반복횟수 저장 → 에디터 모드 자동실행에서 미반영
- **원인:** 플랜 로드 시 원래 파일 경로를 저장 안 함 → 저장 시 `{plan_id}.json` 새 파일 생성 → 원래 파일 미업데이트
- **수정:** `main_window.py`에서 플랜 로드 시 `plan._source_file` 속성에 원래 경로 저장, 저장 시 해당 경로 사용

### 2026-01-31: 업데이트 관련 추가 버그 수정 (v1.0.104 → v1.0.105)
- **BUG-1 수정:** `update_service.py`에서 ssl 제거 후 `ssl.SSLError`, `ssl.create_default_context()` 참조 남아있던 문제 → 지연 import로 수정
- **BUG-7 수정:** `app.py`에서 백그라운드 스레드가 `_main_window.after()` 호출 시 None 체크 누락 → AttributeError 방지
- **BUG-23 수정:** `settings_view.py` creationflags를 `app.py`와 일치시킴 (`DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`)

### 2026-01-31: SSL DLL 로드 오류 수정 (v1.0.103 → v1.0.104)
- **문제:** "DLL load failed while importing _ssl: %1은(는) 올바른 Win32 응용 프로그램이 아닙니다" 오류
- **원인:** `updater.py`, `update_service.py`에서 모듈 최상단에 `import ssl` → SSL DLL 로드 실패 시 모듈 전체 import 실패
- **수정:**
  - `updater.py`: `import ssl` 제거, `_get_ssl_context()` 함수로 지연 import
  - `update_service.py`: `import ssl` 제거, `_get_ssl_context()` 함수로 지연 import
  - SSL 로드 실패해도 "SSL 없음" 방법으로 연결 시도 가능

### 2026-01-31: 업데이트 관련 버그 수정 (v1.0.102 → v1.0.103)
- **버전 확인 무한 대기 버그 수정:**
  - `settings_view.py`: `_check_version_thread` 전체를 try-except로 감싸 예외 발생 시 UI 복구
  - 예외 발생 시 `_update_check_failed()` 호출하여 버튼 활성화
- **릴리즈 확인 무한 대기 버그 수정:**
  - `settings_view.py`: `_fetch_latest_release_direct` 전체를 try-except로 감싸 안전하게 None 반환
- **업데이트 후 프로그램 재시작 안 되는 버그 수정:**
  - `app.py`: 배치 파일의 `start` 명령어 수정
  - 기존: `start "" "{app_dir}\\{exe_name}"` (공백 포함 경로 실패)
  - 변경: `cd /d "{app_dir}"` 후 `start "" "{exe_name}"` (상대 경로로 실행)
  - 모든 재시작 지점 4곳 수정 (롤백 3곳 + 정상 완료 1곳)

### 2026-01-31: 모니터링 모드 점프 조건 기능 추가 (v1.0.101 → v1.0.102)
- **기능:** 모니터링 모드에서 점프 실행 전 조건 이미지 확인
- **동작 흐름:** 감시 이미지 발견 → 모니터링 액션 실행 → **조건 이미지 확인** → 조건 충족 시 점프, 미충족 시 모니터링 복귀
- **UI 변경:**
  - `monitoring_editor.py`: 감시 항목에 "조건" 버튼 추가 (점프 드롭다운 왼쪽)
  - 조건 설정 다이얼로그: 이미지 선택, 검색 범위 지정, 인식률 슬라이더 (30~100%)
- **데이터 필드:**
  - `condition_image`: 점프 조건 이미지 경로
  - `condition_search_region`: 조건 이미지 검색 범위 [x1, y1, x2, y2]
  - `condition_confidence`: 조건 이미지 인식률 (기본 0.65)
- **실행 로직:** `rule_executor.py`에서 조건 이미지 존재 시 `_find_image_on_screen()` 호출, 발견되면 점프, 없으면 모니터링 루프 계속
- **경로 변환:** `automation_models.py`의 `to_dict`/`from_dict`에 condition_image 경로 변환 추가

### 2026-01-30: 게임 모드 스무스 이동 및 스킬 기능 추가 (v1.0.86 → v1.0.101)
- **스무스 이동 (smooth_move):**
  - 키 반복 방식 → 키 홀드 방식으로 변경 옵션
  - 울트라 탭 방식: 15ms 누름 + 5ms 간격으로 자연스러운 이동
- **이동 스킬 (move_skill):**
  - `move_skill_key`: 이동 스킬 키 (예: "4")
  - `move_skill_distance`: 이동 스킬 사용 거리 (기본 150픽셀 이상이면 스킬 사용)
- **상시 스킬 (auto_skill):**
  - `auto_skill_key`: 상시 사용 스킬 키
  - `auto_skill_cooldown_image`: 쿨타임 이미지 (이 이미지가 없으면 스킬 사용)
- **캐릭터/목표 인식률 분리:**
  - `character_confidence`: 캐릭터 이미지 인식률
  - `target_confidence`: 목표 이미지 인식률
- **수정된 파일:**
  - `automation_models.py`: GameModeConfig에 새 필드 추가
  - `player_view.py`: 게임 모드 설정 UI 확장
  - `rule_executor.py`: 스킬 실행 및 스무스 이동 로직

### 2026-01-28: 최종타겟이미지 감시이미지 재확인 로직 추가 (v1.0.84 → v1.0.85)
- **문제:** 최종타겟이미지가 감시이미지보다 약간 먼저 나타나면 감시이미지를 놓치고 모니터링 종료
- **해결:** `rule_executor.py` 최종타겟이미지 발견 시 즉시 종료하지 않고 0.5초 대기 후 감시이미지 재확인. 감시이미지가 뒤늦게 나타났으면 최종타겟 무시하고 모니터링 루프 계속, 감시이미지 없으면 그때 모니터링 종료

### 2026-01-28: 모니터링 우선순위 변경 + 액션 복사/붙여넣기 (v1.0.83 → v1.0.84)
- **모니터링 이미지 우선순위 변경:** `rule_executor.py` 모니터링 루프에서 감시이미지를 최종타겟이미지보다 먼저 확인하도록 변경. 최종타겟이미지는 감시이미지가 하나도 없을 때만 처리 (모니터링 종료)
- **모니터링 액션 복사/붙여넣기:** `monitoring_editor.py`에 CP(복사) 버튼과 붙여넣기 버튼 추가. 모니터링 액션을 같은 에디터 내에서 복사하여 다른 감시 항목에 붙여넣기 가능

### 2026-01-28: 플레이 모드 재생횟수 로드 버그 수정 (v1.0.82 → v1.0.83)
- **증상:** 플레이 모드에서 재생횟수 저장 후 재시작하면 항상 1회로 초기화
- **원인:** `main_window.py`의 `_create_mini_player_ui()`에서 재생횟수 로드 시 플랜이 백그라운드 로드 중이라 `self._mini_plans`가 빈 리스트 → 로드 건너뜀
- **수정:** `_apply_plans()`에서 플랜 목록 업데이트 직후 선택된 플랜의 저장된 재생횟수를 UI에 반영

### 2026-01-28: 모니터링 모드 반복설정 확장 (v1.0.81 → v1.0.82)
- **변경:** `monitoring_editor.py`의 반복횟수 다이얼로그를 계획수정 액션(`player_view.py`)과 동일하게 확장
- **추가된 기능:**
  - 반복 대기시간(초) 입력
  - 랜덤시간 활성화 체크박스
  - ±범위 입력
- **기존:** `CTkInputDialog`로 반복 횟수만 입력 → **변경:** `CTkToplevel` 전체 반복 설정 다이얼로그

### 2026-01-27: 2차 심층 분석 버그 수정 (v1.0.79 → v1.0.80)
- **분석 범위:** 로직 오류, UI 안전성, 이미지 처리 심층 분석
- **CRITICAL 수정:**
  - `db_manager.py:73`: `_get_connection()` 내 WAL 초기화에서 `with self._lock:` 제거 → **앱 시작 데드락 해결** (`__init__`이 이미 `_lock`을 보유한 상태에서 재획득 시도 → `threading.Lock`은 비재진입이므로 무한 대기)
  - `video_analyzer.py:25`: `SCREENSHOT_SCREENSHOT_CROP_SIZE` 오타 → `SCREENSHOT_CROP_SIZE`로 수정 (NameError 크래시)
  - `rule_executor.py:119-158`: `_get_cached_template()` 실패 시 `(None, 0, 0)` 튜플 반환 → `None` 반환으로 수정 (호출부 `if cached is None:` 체크가 튜플에 매칭되지 않는 버그)
- **HIGH 수정:**
  - `rule_executor.py:1226-1229`: 도달 불가능한 dead else 블록 제거 (`len(locations)==0`은 이미 상위에서 처리됨, 도달 시 IndexError)
  - `monitoring_editor.py:279`: `_refresh_monitor_actions()`에 `watch_idx` 범위 검증 추가 (KeyError 방지)

### 2026-01-27: 전체 코드베이스 심층 버그 수정 (v1.0.78 → v1.0.79)
- **분석 범위:** recorder, analyzer, player, utils, ui, database, app 모듈 전체
- **CRITICAL 수정:**
  - `app.py`: `get_db().close()` 호출 제거 (DatabaseManager에 close() 메서드 없음 → AttributeError)
  - `rule_executor.py`: `watch.get('image')` None일 때 `Path(None).name` → TypeError 크래시 방지
- **HIGH 수정:**
  - `screen_recorder.py`: pause/resume/elapsed_time에 `_pause_lock` 추가 (경쟁 조건 해결)
  - `screen_recorder.py`: stop()에서 `_last_frame` 해제 시 `_frame_lock` 보호 추가
  - `screen_recorder.py`: `full_screen()` 모니터 인덱스 범위 검증 추가 (IndexError 방지)
  - `input_logger.py`: `_events` 리스트에 `_events_lock` 추가 (마우스/키보드 스레드 동시 접근)
  - `input_logger.py`: `_pressed_keys` 셋에 `_pressed_keys_lock` 추가 (스레드 안전성)
  - `config.py`: `reset()` 메서드에 `_lock` 추가 (스레드 안전)
  - `config.py`: `update()` 메서드의 `load()` 호출을 락 내부로 이동 (경쟁 조건 해결)
  - `enhanced_matcher.py`: `match_edge_regions()` bottom/right 좌표 계산 오류 수정 (- → +)
  - `action_player.py`: 빈 locations 리스트 접근 시 IndexError 방지 (방어 코드 추가)
  - `app.py`: type hint NameError 방지 (문자열 참조로 변경)
  - `app.py`: `_start_auto_update`에서 스레드풀 정리 추가 후 os._exit
- **MEDIUM 수정:**
  - `arduino_hid.py`: `mouse_double_click()` 첫 번째 클릭 결과 확인하도록 수정
  - `rule_executor.py`: ROI 크기 0 검증 추가 (빈 배열 슬라이싱 방지)
  - `app.py`: 스레드풀 shutdown에 `cancel_futures=True` 추가
  - `db_manager.py`: WAL 초기화를 `_lock`으로 보호 (경쟁 조건 해결)
  - `enhanced_matcher.py`: 파일 미존재 시 캐시 삭제를 `_cache_lock`으로 보호
  - `input_logger.py`: RecordingSession start_time None 폴백 추가

### 2026-01-27: 전체 코드베이스 버그 수정 (v1.0.70)
- **분석 범위:** recorder, analyzer, player, utils, ui, database 모듈 전체
- **수정된 버그:**
  - `input_logger.py`: bare except 수정 (`except:` → `except (AttributeError, TypeError):`)
  - `input_logger.py`: `_cleanup()` 메서드에서 리스너 중지 후 참조 해제하도록 수정
  - `rule_executor.py`: 이미지 대기 시 무한 루프 버그 수정 (`float('inf')` → 최대 300초 타임아웃)
  - `rule_executor.py`: 타임아웃 도달 시 실패 처리 추가
  - `models.py`: JSON 및 datetime 파싱 시 예외 처리 추가 (JSONDecodeError, ValueError 방지)
  - `db_manager.py`: `__init__` 초기화 경쟁 조건 수정 (락 보호 추가)
  - `db_manager.py`: Foreign key 및 WAL 모드 활성화 (동시성 개선)
  - `db_manager.py`: 마이그레이션 코드 화이트리스트 검증 추가
  - `player_view.py`: bare except 6개 수정 (구체적 예외 타입으로 변경)
- **코드 품질 개선:** 예외 처리 강화, 리소스 누수 방지, 스레드 안전성 향상

### 2026-01-26: 플레이 모드 경량화 (v1.0.63)
- 플레이 모드에서 불필요한 뷰 모듈 지연 로딩
- RecorderView, AnalyzerView, PlayerView, SettingsView, GuideView
- 저사양 PC 메모리/CPU 절약
- 실행 기능, 아두이노 정상 동작

### 2026-01-26: 재생횟수 저장/로드 수정 (v1.0.62)
- 시작 시 저장된 재생횟수 자동 로드
- 플랜 변경 시 재생횟수 로드

### 2026-01-26: 프로그램 시작 시 자동 실행 기능 (v1.0.60 ~ v1.0.61)
- **기능:** 플레이 모드에서 시작 시 지정한 플랜 자동 실행
- **설정:** 환경설정 → 재생 → "프로그램 시작 시 자동 실행"
- 플랜 선택 드롭다운 (분석된 플랜 목록)
- 미니 플레이어에 설정된 반복 횟수 적용
- 5초 후 실행 (업데이트/아두이노 연결 완료 대기)

### 2026-01-25: 인식률 버그 수정 (v1.0.53 ~ v1.0.59)
- **문제:** 부분 실행하면 97% 인식, 순차 실행하면 인식률 부족 에러
- **원인:** "다음 화면 대기" 기능이 하드코딩된 0.65 임계값 사용 + 사용자 인식률(80%)과 충돌
- **해결:**
  - 로그 용어 통일: "신뢰도/임계값" → "인식률"
  - 중복 설정 제거: `watch.confidence`, `monitor_action.confidence` 삭제
  - 인식률 변경 시 JSON 즉시 저장
  - "다음 화면 대기" 임계값 45% 고정 + 검색 범위 적용
- **상세 내용:** PROGRESS.md 참조

### 2025-01-25
- CLAUDE.md 문서 전면 재작성
- 실제 코드에 없는 기능 제거 (AI Intervention, OCR 등)
- 실제 코드 기반으로 정확한 문서 작성
- **모니터링 액션 테스트 기능 추가:**
  - `rule_executor.py`: `test_single_monitor_action()`, `test_monitor_actions_sequence()` 메서드 추가
  - `player_view.py`: 모니터링 액션 카드에 ▶ 버튼 추가
    - 해당 액션부터 현재 감시 끝까지 자동 순차 실행
    - 토글 버튼: 재생(오렌지) ↔ 정지(초록)
- **감시 레벨 재생 버튼 추가:**
  - `rule_executor.py`: `test_single_rule()` 메서드 추가 (규칙 단일 실행)
  - `player_view.py`: 감시 목록 헤더에 ▶ 버튼 추가
    - 해당 감시의 모니터링 액션 전체 실행
    - 점프 액션(goto_index) 실행
    - 토글 버튼: 재생(오렌지) ↔ 정지(초록)
- **이미지 클릭 액션 이미지 변경 기능:**
  - 모니터링 액션 카드에 "IMG" 버튼 추가 (이미지 클릭 타입만)
  - 이미지 있음: 보라색 "IMG", 없음: 빨간색 "IMG?"
  - 클릭 시 파일 다이얼로그로 이미지 선택/변경 가능
- **이미지 매칭 캐시 버그 수정:**
  - `enhanced_matcher.py`: 파일 수정 시간 체크 추가 (같은 경로 파일 변경 시 캐시 갱신)
  - `invalidate_template()` 메서드 추가 (특정 템플릿 캐시 제거)
  - 이미지 변경 시 이전/새 이미지의 캐시와 위치 기록 무효화
  - `rule_executor.py`: 이미지 클릭 디버그 로그 추가 (좌표, 신뢰도, 매칭 방법)
- **검색 범위 선택 시 기존 범위 표시:**
  - `ScreenRegionSelector`에 `existing_region` 파라미터 추가
  - 새 범위를 드래그할 때 기존 범위가 빨간색 점선으로 화면에 표시
  - 안내 텍스트: "빨간색 점선 = 기존 범위 | 파란색 실선 = 새 범위"
  - 모니터링 액션/감시 이미지 모두 동일하게 적용
- **자동 업데이트 롤백 기능 추가:**
  - `app.py`: 업데이트 배치 파일 로직 전면 수정
  - **삭제 대신 백업**: 기존 `_internal` → `_internal_old`, exe → `exe.old` 이름 변경
  - **자동 롤백**: 파일 복사 실패 시 백업에서 자동 복원 후 이전 버전 재시작
  - **무결성 검사**: 새 exe와 _internal 폴더 존재 여부 확인, 없으면 롤백
  - **복구 스크립트**: `recovery.bat` 파일을 앱 폴더에 생성 (수동 복구용)
  - 업데이트 실패해도 앱이 깨진 상태로 남지 않음

---

## 참고사항

- 세션 재시작 시 이 문서와 PROGRESS.md 확인
- 모든 데이터는 로컬 저장 (클라우드 업로드 없음)
- API 키는 Fernet으로 암호화 저장
- 중요한 대화 내용은 이 문서에 기록
- **업데이트 규칙**: "전부 업데이트해라" 명령 시 소스코드, data/plans, data/templates 등 데이터 파일 전부 포함하고, APP_VERSION 올리고, 커밋 + 태그 + 푸시까지 한 번에 처리할 것

### 2026-03-18: 보스방 응답없음 완화 및 상시스킬 on/off 확인 (v1.0.144)
- `player_view.py`
  - 보스 경유지에서 반복되던 UI 로그/상태라벨 갱신을 전용 큐/중복제거 경로로 통일
  - 보스 전용 UI 큐 백프레셔 추가
  - 보스 처치/추적종료 후 정상 완료와 중지 처리 경쟁 상태 보정
- `player_view.py`
  - 보스 관련 worker thread -> Tk 직접 호출 경로를 메인스레드 큐 기반으로 정리
- `config.py`
  - `APP_VERSION` `1.0.143` -> `1.0.144`
- 상시스킬 확인
  - `auto_skill_enabled=False`면 실행부에서 상시스킬 입력이 발생하지 않음을 코드상 확인

### 2026-03-18: Generic transition UI queue hardening + boss chase reroute (v1.0.145)
- `player_view.py`
  - Generic jump-confirm / portal-transition UI logs no longer call `after(0, ...)` directly from the worker path.
  - Transition logs now go through `_schedule_ui_log()` / `_ui_post()` to reduce UI queue pressure outside boss rooms.
  - Boss chase now uses `blocked_dirs`, `portal_protected`, `avoid_set`, and `respect_blocked_edges=True` like patrol mode, reducing repeated retries on the same blocked edge.
- `config.py`
  - `APP_VERSION` `1.0.144` -> `1.0.145`

### 2026-03-18: Auto-skill image-mode verification sync (v1.0.146)
- Verified current game-mode runs are using plan-level auto-skill settings, not `data/config.json`.
- Verified auto-skill is currently configured as `key=5`, `cooldown=image` in the active special-mode plan.
- Verified the cooldown template file exists and image mode remains active when the template is present.
- `config.py`
  - `APP_VERSION` `1.0.145` -> `1.0.146`

### 2026-03-18: Developer/deploy runtime parity audit + auto-skill matcher unification (v1.0.147)
- Audited developer-mode vs frozen/deployed execution paths and confirmed there is no `sys.frozen` split in the core execution path used by special mode or playback.
- Frozen-only branches remain in relaunch, updater, startup registration, and resource-root resolution.
- Unified auto-skill runtime image matching with the same `TemplateMatcher.match_binary()` path used by the image test in `src/ui/player_view.py` and `src/player/rule_executor.py`.
- Image-mode auto-skill exceptions no longer fall back to pressing the skill key.
- `config.py`
  - `APP_VERSION` `1.0.146` -> `1.0.147`

### 2026-03-18: Auto-skill runtime rollback after parity check (v1.0.148)
- Re-checked developer-mode vs deployed/frozen execution and confirmed there is no additional execution-path split in the core game-mode / playback path.
- The recent `match_binary()` unification for auto-skill runtime was a regression because both developer and deployed runs stopped using auto-skill reliably.
- Rolled auto-skill runtime detection back to the prior `cv2.matchTemplate(..., TM_CCOEFF_NORMED)` path in `src/ui/player_view.py` and `src/player/rule_executor.py`.
- `config.py`
  - `APP_VERSION` `1.0.147` -> `1.0.148`

### 2026-03-18: UI batching and lazy view activation refactor (v1.0.149)
- Added shared UI batching helpers in `src/ui/ui_batcher.py`:
  - `UiCallbackDispatcher`
  - `BufferedRecordPump`
- Refactored `src/ui/main_window.py` so:
  - embedded log updates are batched
  - nav buttons respond immediately
  - loading placeholders can render before expensive view creation
  - heavy view creation is deferred with `after_idle(...)`
- Refactored `src/ui/log_view.py` to use the same buffered log update path.
- Refactored `src/ui/player_view.py` so `GameModeDialog` uses the shared dispatcher and buffered log pump for UI updates.
- Fixed two regressions found during validation:
  - missing `UiCallbackDispatcher` import in `main_window.py`
  - local `time` import shadowing in `player_view.py` close/save flow
- Verification:
  - `py_compile` passed for `src/ui/ui_batcher.py`, `src/ui/main_window.py`, `src/ui/log_view.py`, and `src/ui/player_view.py`
  - real app startup test passed
  - real view-switch test passed (`recorder -> analyzer -> player`)
  - real `GameModeDialog` open/close test passed
- Re-checked developer vs deployed UI execution differences and found no extra hidden split beyond expected packaging-specific branches.

### 2026-03-18: Player-mode UI dispatcher expansion + AnalyzerView thread-safe UI dispatch (v1.0.150)
- Extended the UI-only refactor without touching backend logic, pathfinding, mapping, or plan/map data.
- `src/ui/player_view.py`
  - Expanded `UiCallbackDispatcher` usage from `GameModeDialog` into `PlayerView`.
  - Coalesced plan progress, playback progress, and action-text updates.
  - Routed worker-thread `after(...)` calls for both `PlayerView` and `GameModeDialog` back through the main-thread dispatcher.
  - Added coalesced live-status scheduling for hot playback updates.
- `src/ui/analyzer_view.py`
  - Added a thread-safe `after(...)` override and `_analyzer_ui_post(...)` helper.
  - Moved async plan-load completion, analysis progress, plan-review dialog launch, analysis-complete handling, and cleanup dialog callbacks onto the dispatcher.
  - Added dispatcher shutdown in `cleanup()`.
- Verification:
  - `py_compile` passed for `src/ui/ui_batcher.py`, `src/ui/main_window.py`, `src/ui/log_view.py`, `src/ui/player_view.py`, and `src/ui/analyzer_view.py`
  - real app startup test passed
  - real view-switch test passed (`recorder -> analyzer -> player`)
  - real `GameModeDialog` open/close test passed
  - real destroy-time test passed after switching into `AnalyzerView`, with no late worker-thread Tk exception
- `config.py`
  - `APP_VERSION` `1.0.149` -> `1.0.150`

### 2026-03-19: MainWindow UI dispatch guard + auto-skill template-load fallback + boss step watchdog (v1.0.152)
- Tightened UI-only safety around the play/miniplayer path without touching backend logic, pathfinding, mapping, or plan/map data.
- `src/ui/main_window.py`
  - Added a root-level `UiCallbackDispatcher`.
  - Routed worker-thread `after(...)` calls back through the main-thread dispatcher so play/miniplayer callbacks no longer schedule Tk work directly from background threads.
- `src/ui/player_view.py`
  - Added a safe OpenCV template-loading fallback for auto-skill cooldown images:
    - try `cv2.imread(...)`
    - if it fails, retry via `Path.read_bytes() + np.frombuffer() + cv2.imdecode(...)`
  - Added a boss patrol/chase step watchdog for the "last movement log then no next coordinate" freeze pattern.
  - Watchdog now logs OCR delay / post-input coordinate timeout and performs a local movement reset only for the live step state.
- `src/player/rule_executor.py`
  - Applied the same auto-skill template-loading fallback so playback/runtime matches the special-mode path.
- Verification:
  - `py_compile` passed for `src/ui/main_window.py`, `src/ui/player_view.py`, `src/player/rule_executor.py`, and `src/utils/config.py`
  - Existing `SyntaxWarning` lines in `player_view.py` remained unchanged; no new syntax errors were introduced.
- `config.py`
  - `APP_VERSION` `1.0.151` -> `1.0.152`

### 2026-03-19: Auto-skill runtime diagnostic logging (v1.0.151)
- Added throttled runtime diagnostic logs for auto-skill without changing the decision logic.
- `src/ui/player_view.py`
  - Added `[??????]` logs for special-mode execution.
  - Logs capture config summary, template load failure, missing `last_screenshot`, region-too-small cases, runtime match score / use decision, and actual key-press reasons.
- `src/player/rule_executor.py`
  - Added matching `[coordinate-mode][auto-skill-diag]` logs for playback/coordinate-mode execution.
- Verification:
  - `py_compile` passed for `src/ui/player_view.py` and `src/player/rule_executor.py`
- `config.py`
  - `APP_VERSION` `1.0.150` -> `1.0.151`
### 2026-03-19: GameModeDialog stop-reason tracing (v1.0.153)
- Added stop-reason tracing for unexpected play-mode termination without touching backend logic, pathfinding, mapping, or plan/map data.
- `src/ui/player_view.py`
  - Added `_mark_stop_reason(...)` and `_request_stop_execution(...)`.
  - Reset stop-trace state on each run start.
  - Logged final stop reason inside `_stop_execution()` and appended `🧭 중단사유: ...` to the special-mode log.
  - Tagged key stop paths such as template-incomplete, initial wait interruption, coordinate-fail limit, abnormal coordinate jump, mapping portal exit, top-level loop exception, and unclassified thread exit.
- Verification:
  - `py_compile` passed for `src/ui/player_view.py`
  - Existing `SyntaxWarning` lines remained unchanged.
- `config.py`
  - `APP_VERSION` `1.0.152` -> `1.0.153`

### 2026-03-19: Auto-skill diagnostic tuple fix (v1.0.154)
- Fixed an immediate special-mode/playback stop regression introduced by the new auto-skill diagnostics.
- Root cause:
  - `src/ui/player_view.py` and `src/player/rule_executor.py` built the config diagnostic signature with `bool(_auto_skill_cd_tmpl)`.
  - After the template-load fallback started returning a real OpenCV/numpy image array, `bool(ndarray)` raised `ValueError: The truth value of an array with more than one element is ambiguous`.
- Fix:
  - Replaced `bool(_auto_skill_cd_tmpl)` with `(_auto_skill_cd_tmpl is not None)` in both runtime paths.
- Verification:
  - `py_compile` passed for `src/ui/player_view.py`, `src/player/rule_executor.py`, and `src/utils/config.py`
  - Targeted smoke test confirmed the new diagnostic tuple no longer calls object truthiness.
- `config.py`
  - `APP_VERSION` `1.0.153` -> `1.0.154`
