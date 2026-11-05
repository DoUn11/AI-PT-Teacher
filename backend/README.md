# AI PT쌤 백엔드

FastAPI 기반 **모듈형 모놀리스**. 역할은 두 가지다.

1. **로컬 분석기**(Pi 영상을 받아 PC에서 자세를 분석하고 영상 위에 바로 표시하는 프로그램)가 올리는 운동 결과를 저장한다.
2. **웹**이 로그인한 사용자의 세션·반복(rep)·통계를 조회할 수 있게 REST API를 제공한다.

자세 분석(각도·rep 계산)은 이 서버에서 하지 않는다. 실시간 표시가 필요해 로컬에서 처리한다.

```
Pi 카메라 ─H.264─> 로컬 PC (YOLO pose + 각도/rep 계산 + 영상 오버레이)
                        │  결과 업로드 (REST, JWT)
                        ▼
                    이 백엔드 (DB 저장) ◀── 웹 (조회)
```

## 실행

```bash
cd backend
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e ".[dev]"

.venv/bin/uvicorn app.main:app --reload      # http://127.0.0.1:8000/docs
.venv/bin/python -m pytest                   # 테스트
```

설정은 환경변수 또는 `.env`(`.env.example` 참고).

| 변수 | 기본값 | 설명 |
|---|---|---|
| `APP_ENV` | `dev` | `dev`가 아니면 `JWT_SECRET` 필수 |
| `DATABASE_URL` | `sqlite:///./aipt.db` | 운영은 PostgreSQL (`pip install -e ".[postgres]"`) |
| `JWT_SECRET` | (dev 임시 키) | 운영에서는 반드시 긴 무작위 값 |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `30` | |
| `CORS_ORIGINS` | `["http://localhost:3000","http://localhost:5173"]` | 웹 출처 (JSON 배열) |

## API (`/api/v1`)

인증이 필요한 요청은 `Authorization: Bearer <access_token>` 헤더를 쓴다.

| 메서드 | 경로 | 설명 |
|---|---|---|
| POST | `/auth/register` | 회원가입 `{email, password(8자~72바이트), nickname}` |
| POST | `/auth/login` | `{email, password}` → `{access_token, token_type}` |
| GET | `/users/me` | 내 정보 |
| POST | `/workouts/sessions` | 세션 시작 `{exercise: "squat" \| "pushup"}` |
| POST | `/workouts/sessions/{id}/reps` | **로컬 분석기용.** 완료된 rep 업로드 (아래) |
| POST | `/workouts/sessions/{id}/finish` | 세션 종료, `total_reps`·`avg_score` 계산 |
| GET | `/workouts/sessions` | 내 세션 목록 (`exercise`, `limit`≤100, `offset`) 최신순 |
| GET | `/workouts/sessions/{id}` | 세션 상세 + rep 목록 |
| GET | `/workouts/stats` | 종료된 세션 기준 운동별 누적 통계 (`since` 선택) |
| GET | `/health` | 상태 확인 (인증 불필요) |

### rep 업로드 규약

```json
POST /api/v1/workouts/sessions/1/reps
{"reps": [
  {"index": 1, "score": 90, "min_angle": 92.5, "feedback": [],
   "completed_at": "2026-11-05T10:00:00Z"},
  {"index": 2, "score": 65, "min_angle": 118.0, "feedback": ["shallow_depth"]}
]}
→ 201 {"accepted": 2, "duplicates": 0}
```

- `index`는 세션 안에서 1부터. **같은 index를 다시 보내면 무시**하므로 네트워크 재시도가 안전하다.
- `completed_at`은 생략 가능. 보낼 때는 **시간대 포함 필수**(`Z` 또는 `+09:00`). 응답의 모든 시각은 UTC.
- `feedback`은 문자열 코드 목록(최대 10개). 서버는 값을 검증하지 않는다. 현재 로컬 분석기(`../analyzer`)가 쓰는 코드는 `shallow_depth`, `trunk_lean`, `body_line`이며, 웹은 코드를 문구로 매핑해야 한다(분석기의 `configs/messages.ko.json` 참고).
- 종료된 세션에는 rep을 추가할 수 없다(409). `finish`는 여러 번 호출해도 같은 결과를 돌려준다.
- 다른 사용자의 세션은 존재 여부를 숨기려고 404로 응답한다.

## 구조

```
app/
  main.py            앱 조립, CORS, 라우터 등록
  db.py              엔진/세션, UtcDateTime(항상 UTC aware)
  models.py          모든 ORM 모델 등록
  core/              config, security(bcrypt, JWT)
  modules/
    auth/            회원가입·로그인·현재 사용자
    workouts/        세션·rep·통계
tests/               pytest (인메모리 SQLite)
```

모듈은 서로의 DB 모델을 직접 import하지 않고(공통인 `User` 제외) 라우터 단위로 나뉘어 있어, 나중에 서비스로 분리하기 쉽다.

## 알려진 한계 / 다음 단계

- 테이블은 시작 시 `create_all`로 만든다. 스키마가 굳으면 Alembic으로 바꿔야 한다.
- 액세스 토큰만 있고 리프레시 토큰은 없다. 30분이 넘는 운동에서는 로컬 분석기가 재로그인해야 한다.
- 로그인 시도 횟수 제한(rate limit)이 없다. 외부에 공개하기 전에 게이트웨이나 미들웨어로 추가해야 한다.
- 명세서의 Kafka, API Gateway, 컬럼 단위 암호화(AES-256), 알림 서비스는 아직 구현하지 않았다.
- PostgreSQL에서는 아직 테스트하지 않았다(테스트는 SQLite).
