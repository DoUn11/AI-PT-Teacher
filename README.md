# AI PT쌤 (AI-PT-Teacher)

CLOUD AIOT TEAM 4 — 카메라로 운동 자세를 분석하고, 반복(rep)별 점수와 피드백을 기록하는 AI 퍼스널 트레이너.

```
Pi 카메라 ─H.264─> 로컬 PC (YOLO pose 분석 + 영상 오버레이) ─REST─> 백엔드(FastAPI + DB) <─ 웹 (기록 조회)
웹캠 ──────────> 웹 프런트엔드 (MediaPipe 분석) ─REST─────────────┘
```

지원 종목: 스쿼트, 푸시업 (런지·플랭크·크런치는 추가 예정)

## 구성

| 폴더 | 역할 | 문서 |
|---|---|---|
| [backend/](backend/) | FastAPI 서버. 회원가입·로그인(JWT), 운동 세션·rep 저장, 통계 | [README](backend/README.md) |
| [frontend/](frontend/) | 웹 화면. 웹캠 자세 분석, 기록 대시보드 (빌드 도구 없음) | [README](frontend/README.md) |
| [analyzer/](analyzer/) | 로컬 분석기. Pi 영상을 YOLO로 분석하고 결과를 백엔드로 업로드 | [README](analyzer/README.md) |
| [raspberry/](raspberry/) | Pi 카메라 영상 송출 설정과 성능 측정 | [README](raspberry/README.md) |

## 요구 사항

- Python 3.11 이상 (권장 3.12), [uv](https://docs.astral.sh/uv/)
- 프런트엔드 서버용 Python 3 (Node.js 20 이상은 `npm start`/테스트를 쓸 때만)
- 웹캠 분석은 Chrome 등 최신 브라우저 + 인터넷 연결 (MediaPipe를 CDN에서 불러옴)

## 서버 켜는 절차 (웹 + 백엔드)

터미널 2개가 필요하다. **백엔드를 먼저** 켠다.

### 1. 백엔드 (포트 8000)

처음 한 번만 환경을 만든다.

```bash
cd backend
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e ".[dev]"
```

서버 실행:

```bash
cd backend
.venv/bin/uvicorn app.main:app --reload
```

- API 문서: http://127.0.0.1:8000/docs
- 상태 확인: http://127.0.0.1:8000/health → `{"status":"ok"}`
- DB는 별도 설치 없이 `backend/aipt.db`(SQLite)에 자동 생성된다. 테이블도 시작 시 자동 생성된다.
- 설정을 바꾸려면 `backend/.env.example`을 `backend/.env`로 복사해 수정한다. 아무 설정 없이도 개발용 기본값으로 동작한다.

### 2. 프런트엔드 (포트 5173)

다른 터미널에서:

```bash
cd frontend
python3 -m http.server 5173 --bind localhost     # 또는 npm start
```

브라우저에서 http://localhost:5173 을 연다. 회원가입 → 로그인 → 자세 분석 / 운동 기록 순서로 사용한다.

- 주소는 반드시 `localhost:5173`으로 연다. 백엔드의 CORS 허용 출처가 이 주소다. `file://`로 직접 여는 방식은 안 된다.
- 카메라는 localhost 또는 HTTPS에서만 동작하고, 브라우저의 카메라 권한 허용이 필요하다.

### 3. 종료

각 터미널에서 `Ctrl+C`.

## (선택) 로컬 분석기로 Pi 영상 분석하기

백엔드가 켜져 있고 계정이 있어야 한다. (계정은 웹에서 가입하거나 `/docs`의 `/api/v1/auth/register`로 만든다.)

```bash
cd analyzer
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e ".[dev,yolo]"      # torch를 받아 무겁다

export AIPT_EMAIL=me@example.com AIPT_PASSWORD=...             # 없으면 실행 시 물어본다
.venv/bin/python -m aipt_analyzer --exercise squat --source tcp://<Pi IP>:8888
```

백엔드 없이 화면만 보려면 `--no-upload`, 웹캠을 쓰려면 `--source 0`. 종료는 영상 창에서 `q`. 자세한 옵션은 [analyzer/README.md](analyzer/README.md).

분석기를 `./run_mqtt.sh`로 켜면 웹의 **운동** 탭에서 운동을 고르고 **운동 시작 / 종료**를 누른다. 끝내면 약점 부위를 몸 그림으로 보여 주는 리포트가 열린다.
사용 흐름과 파이프라인은 [docs/USECASES.md](docs/USECASES.md).

## (선택) Firebase 동기화

기본은 로컬 DB(SQLite)만 쓴다. 가입·세션 종료 시 Firestore에도 복사하려면:

1. Firebase 서비스 계정 키를 `backend/app/core/serviceAccountkey.json`에 둔다. (Git에 올라가지 않는다. 다른 위치면 `FIREBASE_CREDENTIALS`로 지정)
2. `uv pip install --python backend/.venv/bin/python -e "backend[firebase]"`
3. 백엔드를 재시작한다.

키나 패키지가 없으면 시작 로그에 경고만 남기고 건너뛴다.

## 테스트

```bash
cd backend  && .venv/bin/python -m pytest
cd analyzer && .venv/bin/python -m pytest
cd frontend && npm test
```

## 문제 해결

| 증상 | 확인 |
|---|---|
| 웹에서 "서버에 연결할 수 없습니다" | 백엔드가 8000번 포트에서 켜져 있는지 `/health`로 확인 |
| 브라우저 콘솔에 CORS 오류 | 주소를 `127.0.0.1:5173`이 아닌 `localhost:5173`으로 열었는지 확인 |
| `Address already in use` | 같은 포트를 쓰는 서버가 이미 실행 중. 종료하거나 `--port`로 변경 (프런트 주소를 바꾸면 CORS 설정도 맞춰야 함) |
| 30분 뒤 갑자기 로그인 화면 | 액세스 토큰 유효기간이 30분. 다시 로그인 |
| DB를 초기화하고 싶다 | 백엔드를 끈 뒤 `backend/aipt.db` 삭제 |
| `Firebase 동기화 비활성화` 경고 | 정상. 키가 없어 로컬 DB만 쓰는 중 |

## 알려진 한계

- 운영 배포용 설정(HTTPS, `JWT_SECRET`, PostgreSQL, 로그인 시도 제한)은 아직 없다. 자세한 내용은 [backend/README.md](backend/README.md)의 "알려진 한계".
- 분석 임계값은 초기값이며 실제 동작 데이터로 보정이 필요하다.
