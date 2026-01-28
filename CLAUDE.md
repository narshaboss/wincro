# WinCro (윈크로) - 프로젝트 문서

## 프로젝트 개요
영상 녹화 기반 업무 자동화 RPA 프로그램. 사용자가 화면을 녹화하면 입력 로그를 분석하여 마우스/키보드 동작을 추출하고, 이미지 매칭 기반으로 동작을 재현합니다.

**현재 버전:** 1.0.81

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
│   │   └── action_player.py       # 액션 실행 (레거시)
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
│   └── triggers/                  # 트리거 이미지
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

**AutomationPlan:**
```python
name: str                       # 플랜 이름
initial_rules: List[Rule]       # 실행할 규칙 목록
monitoring_rules: List[Rule]    # 모니터링 규칙
video_path: str                 # 원본 영상 경로
input_log_path: str             # 입력 로그 경로
```

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
