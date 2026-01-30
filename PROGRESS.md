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
- 마지막 업데이트: 2026-01-31 (v1.0.105)

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
