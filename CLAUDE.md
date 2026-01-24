# WinCro (윈크로) - 프로젝트 컨텍스트

## 프로젝트 개요
영상 녹화 기반 업무 자동화 RPA 프로그램. 사용자가 화면을 녹화하면 AI가 영상을 분석하여 마우스 동작, 클릭 위치, 키보드 입력 등 모든 행동을 추출하고, 이를 완벽하게 재현하는 지능형 RPA 솔루션.

## 기술 스택
- **언어**: Python 3.11+
- **GUI**: CustomTkinter (다크 테마)
- **데이터베이스**: SQLite
- **AI API**: OpenAI (ChatGPT) - 변수 상황 대응용
- **핵심 라이브러리**:
  - mss: 화면 캡처/녹화
  - opencv-python: 영상 분석, 이미지 매칭
  - pyautogui: 마우스/키보드 자동화
  - pynput: 입력 이벤트 감지 및 녹화
  - pytesseract: OCR 텍스트 인식
  - openai: ChatGPT API
  - Pillow: 이미지 처리
  - numpy: 데이터 처리
  - cryptography: API 키 암호화

## 프로젝트 구조
```
C:\Projects\wincro\
├── src/
│   ├── __init__.py
│   ├── main.py                 # 앱 진입점
│   ├── app.py                  # 메인 앱 클래스
│   ├── recorder/               # 화면 녹화 모듈
│   │   ├── __init__.py
│   │   ├── screen_recorder.py  # 화면 캡처
│   │   └── input_logger.py     # 입력 이벤트 로깅
│   ├── analyzer/               # 영상 분석 모듈
│   │   ├── __init__.py
│   │   ├── video_analyzer.py   # 영상 분석
│   │   ├── action_extractor.py # 액션 추출
│   │   └── template_matcher.py # 이미지 템플릿 매칭
│   ├── player/                 # 동작 재현 모듈
│   │   ├── __init__.py
│   │   ├── action_player.py    # 액션 실행
│   │   └── ai_intervention.py  # AI 변수 대응
│   ├── database/               # 데이터베이스 모듈
│   │   ├── __init__.py
│   │   ├── models.py           # 데이터 모델
│   │   └── db_manager.py       # DB 연결/쿼리
│   ├── ui/                     # GUI 모듈
│   │   ├── __init__.py
│   │   ├── main_window.py      # 메인 윈도우
│   │   ├── recorder_view.py    # 녹화 화면
│   │   ├── analyzer_view.py    # 분석 화면
│   │   ├── player_view.py      # 실행 화면
│   │   └── settings_view.py    # 설정 화면
│   ├── utils/                  # 유틸리티
│   │   ├── __init__.py
│   │   ├── config.py           # 설정 관리
│   │   ├── security.py         # 암호화/보안
│   │   └── logger.py           # 로깅 설정
│   └── i18n/                   # 다국어 지원
│       ├── __init__.py
│       └── ko.py               # 한글 번역
├── data/
│   ├── recordings/             # 녹화 영상 저장
│   ├── templates/              # 이미지 템플릿 저장
│   ├── sequences/              # 시퀀스 JSON 저장
│   └── wincro.db               # SQLite 데이터베이스
├── logs/                       # 로그 파일
├── tests/                      # 테스트 코드
│   ├── __init__.py
│   ├── test_recorder.py
│   ├── test_analyzer.py
│   └── test_player.py
├── requirements.txt            # 의존성 목록
├── setup.py                    # 패키지 설정
├── README.md                   # 프로젝트 설명
├── PROGRESS.md                 # 작업 진행 상태
└── .env.example                # 환경변수 예시
```

## 데이터베이스 스키마
```sql
-- 시퀀스 테이블
CREATE TABLE sequences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT,
    actions JSON NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_run DATETIME,
    run_count INTEGER DEFAULT 0,
    success_rate REAL DEFAULT 0.0
);

-- 액션 템플릿 테이블
CREATE TABLE action_templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sequence_id INTEGER,
    step_order INTEGER,
    action_type TEXT NOT NULL,
    target_image_path TEXT,
    parameters JSON,
    FOREIGN KEY (sequence_id) REFERENCES sequences(id)
);

-- 실행 로그 테이블
CREATE TABLE execution_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sequence_id INTEGER,
    started_at DATETIME,
    ended_at DATETIME,
    status TEXT,
    error_message TEXT,
    ai_interventions INTEGER DEFAULT 0,
    FOREIGN KEY (sequence_id) REFERENCES sequences(id)
);
```

## 코딩 규칙
- PEP 8 준수
- 모든 함수/클래스에 한글 docstring 작성
- 타입 힌트 사용
- 로깅: logging 모듈 사용 (DEBUG, INFO, WARNING, ERROR)
- 에러 처리: try-except로 예외 처리, 사용자 친화적 메시지

## 핵심 기능 요구사항
1. **화면 녹화**: 화면 + 입력 동시 녹화, 영상(.mp4) + 로그(.json) 출력
2. **영상 분석**: AI가 영상 분석 → 재현 가능한 액션 시퀀스(JSON) 생성
3. **동작 재현**: 액션 시퀀스 실행하여 사용자 동작 재현
4. **AI 변수 대응**: 예상과 다른 상황 시 ChatGPT가 화면 분석 후 대응
5. **시퀀스 관리**: CRUD, 내보내기/가져오기

## 필수 포함 사항
- 긴급 중지: ESC 2번
- 시퀀스 내보내기/가져오기 (JSON)
- 실행 속도 조절 (0.5x ~ 2x)
- 각 단계별 대기시간 설정
- 반복 실행 (N회 또는 무한)

## 필수 제외 사항
- 클라우드 업로드 금지 (모든 데이터 로컬)
- 원격 제어 기능 금지
- 사용자 추적/분석 금지

## 성능 요구사항
- 녹화 프레임: 최소 15fps, 권장 30fps
- 영상 분석: 10분 영상 기준 5분 이내
- 동작 재현 지연: 50ms 이내
- 메모리 사용: 500MB 이하

## 배포 절차 (중요!)
배포/업데이트 요청 시 반드시 아래 체크리스트 확인:

### 1. 코드 변경
- [ ] src/ 폴더 내 변경된 파일 모두 커밋
- [ ] src/utils/config.py 버전 번호 업데이트

### 2. 데이터 파일 (배포에 포함되어야 함)
- [ ] data/plans/*.json - 플랜 파일
- [ ] data/templates/*.png - 템플릿 이미지
- [ ] data/sequences/*.json - 시퀀스 파일

### 3. Git 작업
```bash
# 변경된 코드 + 데이터 파일 모두 add
git add src/
git add data/plans/
git add data/templates/
git add data/sequences/

# 커밋
git commit -m "vX.X.X: 변경 내용"

# 태그 생성 및 푸시
git tag vX.X.X
git push origin master
git push origin vX.X.X
```

### 4. 확인 사항
- GitHub Actions 빌드 완료 확인
- Release에 zip 파일 업로드 확인

## 참고
- 세션 재시작 시 PROGRESS.md를 읽고 작업 이어서 진행
- 에러 발생 시 PROGRESS.md에 기록 후 가능한 범위까지 진행
