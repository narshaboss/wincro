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

## 메모

- 개발 완료: 2026-01-16
- 총 생성 파일: 28개
- 총 테스트 파일: 3개

- 프로젝트 시작 시간: 2026-01-16
- 마지막 업데이트: 2026-01-21

---
이 파일은 Claude가 자동으로 업데이트합니다.
세션이 중단되어도 이 파일을 읽고 작업을 이어갈 수 있습니다.
