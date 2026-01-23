# WinCro (윈크로)

영상 녹화 기반 업무 자동화 RPA 프로그램

## 소개

WinCro는 사용자가 화면을 녹화하면 AI가 영상을 분석하여 마우스 동작, 클릭 위치, 키보드 입력 등 모든 행동을 추출하고, 이를 완벽하게 재현하는 지능형 RPA 솔루션입니다.

## 주요 기능

- **화면 녹화**: 화면과 입력을 동시에 녹화하여 영상(.mp4)과 로그(.json) 파일로 저장
- **영상 분석**: AI가 녹화된 영상을 분석하여 재현 가능한 액션 시퀀스를 자동 생성
- **동작 재현**: 추출된 액션 시퀀스를 실행하여 사용자 동작을 완벽하게 재현
- **AI 변수 대응**: 예상과 다른 상황 발생 시 ChatGPT가 화면을 분석하고 대응
- **시퀀스 관리**: 시퀀스 CRUD, 내보내기/가져오기 지원

## 설치

### 요구사항

- Python 3.11 이상
- Windows 10/11

### 설치 방법

```bash
# 저장소 클론
git clone https://github.com/your-repo/wincro.git
cd wincro

# 가상환경 생성 (권장)
python -m venv venv
venv\Scripts\activate

# 의존성 설치
pip install -r requirements.txt
```

### Tesseract OCR 설치 (선택사항)

텍스트 인식(OCR) 기능을 사용하려면 Tesseract를 설치해야 합니다.

1. [Tesseract 다운로드](https://github.com/UB-Mannheim/tesseract/wiki)에서 설치 프로그램 다운로드
2. 설치 시 한국어 언어 데이터 포함 선택
3. 시스템 환경변수 PATH에 Tesseract 경로 추가

## 사용법

### 애플리케이션 실행

```bash
python -m src.main
```

### 기본 워크플로우

1. **녹화 탭**에서 녹화 시작
2. 자동화하려는 작업 수행
3. 녹화 중지
4. **분석 탭**에서 녹화 선택 후 분석
5. **실행 탭**에서 시퀀스 선택 후 실행

### 긴급 중지

실행 중 문제 발생 시 **ESC 키를 2번** 연속으로 누르면 즉시 중지됩니다.

## 설정

### AI 설정 (OpenAI API)

1. [OpenAI](https://platform.openai.com/)에서 API 키 발급
2. 설정 탭에서 API 키 입력
3. AI 개입 기능 활성화

### 환경변수 (선택사항)

`.env.example`을 `.env`로 복사하고 필요한 값을 설정합니다.

```bash
cp .env.example .env
```

## 프로젝트 구조

```
wincro/
├── src/
│   ├── main.py           # 앱 진입점
│   ├── app.py            # 메인 앱 클래스
│   ├── recorder/         # 화면 녹화 모듈
│   ├── analyzer/         # 영상 분석 모듈
│   ├── player/           # 동작 재현 모듈
│   ├── database/         # 데이터베이스 모듈
│   ├── ui/               # GUI 모듈
│   ├── utils/            # 유틸리티
│   └── i18n/             # 다국어 지원
├── data/                 # 데이터 저장 디렉토리
├── logs/                 # 로그 파일
├── tests/                # 테스트 코드
└── requirements.txt      # 의존성 목록
```

## 테스트

```bash
# 전체 테스트 실행
pytest tests/ -v

# 커버리지 포함
pytest tests/ -v --cov=src --cov-report=html
```

## 보안 주의사항

- 모든 데이터는 로컬에만 저장됩니다 (클라우드 업로드 없음)
- API 키는 암호화되어 저장됩니다
- 녹화된 영상에는 민감한 정보가 포함될 수 있으므로 주의하세요

## 성능 요구사항

- 녹화 프레임: 최소 15fps, 권장 30fps
- 영상 분석: 10분 영상 기준 5분 이내
- 동작 재현 지연: 50ms 이내
- 메모리 사용: 500MB 이하

## 라이선스

MIT License

## 문의

이슈나 문의사항은 GitHub Issues를 통해 등록해주세요.
