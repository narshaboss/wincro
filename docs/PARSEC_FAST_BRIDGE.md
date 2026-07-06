# Parsec Fast Bridge

`tools/parsec_fast_bridge.py`는 WinCro 본체와 분리된 관제용 보조 도구다. Codex가 Parsec 창을 빠르게 보고, 이미지 템플릿을 찾고, 필요할 때만 `SendInput`으로 클릭/키입력을 수행하기 위한 1차 브릿지다.

## 기본 원칙

- WinCro 실행 로직에는 연결하지 않는다.
- 기본값은 실제 입력을 보내지 않는 dry-run이다.
- 실제 클릭/키입력은 반드시 `--execute`를 명시해야 한다.
- 클릭 전에는 이미지 매칭 점수가 임계값 이상이어야 한다.
- Parsec 창 제목이 달라지면 `--title` 정규식을 조정한다.

## 명령

Parsec 창 찾기:

```powershell
python tools\parsec_fast_bridge.py windows --title Parsec
```

Parsec 창 캡처:

```powershell
python tools\parsec_fast_bridge.py screenshot --title Parsec --output tmp_parsec.png
```

템플릿 찾기:

```powershell
python tools\parsec_fast_bridge.py find --title Parsec --template C:\path\to\button.png --threshold 0.95 --region 880,170,300,130
```

템플릿 중심 클릭 dry-run:

```powershell
python tools\parsec_fast_bridge.py click-template --title Parsec --template C:\path\to\button.png --threshold 0.95
```

템플릿 중심 실제 클릭:

```powershell
python tools\parsec_fast_bridge.py click-template --title Parsec --template C:\path\to\button.png --threshold 0.95 --execute
```

윈도우 기준 좌표 실제 클릭:

```powershell
python tools\parsec_fast_bridge.py click --title Parsec --x 1032 --y 244 --execute
```

키 입력 dry-run:

```powershell
python tools\parsec_fast_bridge.py key --keys Shift+Up --hold-ms 40
```

키 입력 실제 실행:

```powershell
python tools\parsec_fast_bridge.py key --keys Enter --execute
```

Peer ID 직접 접속 dry-run:

```powershell
python tools\parsec_fast_bridge.py connect-peer --peer-id example-peer
```

Peer ID 직접 접속 실행:

```powershell
python tools\parsec_fast_bridge.py connect-peer --peer-id example-peer --execute
```

## 현재 범위

- 1차 완료: 창 찾기, 빠른 캡처, OpenCV 템플릿 매칭, dry-run/실행 분리, SendInput 클릭/키입력.
- 1.5차 완료: Parsec 공식 `peer_id=...` 직접 접속 명령 dry-run.
- 2차 필요: PC번호 ↔ peer_id 매핑 저장, `connect_pc(2)` 같은 번호 기반 직접 접속, 연결 후 WinCro 상태 판정.

## 구조적 리스크

캡처 없이 좌표만 클릭하는 방식은 빠르지만 위험하다. Parsec 스크롤 위치나 창 크기가 바뀌면 다른 PC를 누를 수 있다. 운영용 자동복구에서는 `find` 또는 `click-template`처럼 이미지 확인을 거친 뒤 입력하는 방식을 기본으로 써야 한다.

단, 각 PC의 `peer_id`를 확보하면 UI 클릭보다 `connect-peer` 방식이 우선이다. 이 방식은 Parsec 공식 명령줄 연결 경로를 사용하므로 캡처/이미지판단 없이 바로 접속을 시도할 수 있다.
