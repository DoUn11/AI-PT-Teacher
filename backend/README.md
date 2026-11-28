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
| `MQTT_URL` | (비어 있음=꺼짐) | Pi 카메라 수신 브로커. 예: `mqtt://localhost:1883` |
| `MQTT_FRAME_TOPIC` | `ai_pt/cam/frame` | 프레임 토픽 |
| `CAMERA_OFFLINE_AFTER_SECONDS` | `5` | 이 시간 넘게 프레임이 없으면 오프라인 |

## Pi 카메라 수신 (MQTT)

`MQTT_URL`을 설정하면 서버가 브로커를 구독해 Pi가 보내는 JPEG 프레임을 받는다. 프레임은 저장하지 않고
**메모리에 최신 1장**만 둔다. 최근 5초 안에 프레임이 오면 `online`이고, 상태가 바뀔 때(카메라 연결/끊김,
브로커 연결/끊김)만 `camera_events` 테이블에 기록한다.

```bash
uv pip install --python .venv/bin/python -e ".[mqtt]"
MQTT_URL=mqtt://localhost:1883 .venv/bin/uvicorn app.main:app     # 토픽 기본값 ai_pt/cam/frame
```

영상은 두 갈래다. `MQTT_FRAME_TOPIC`(Pi 원본)과 `MQTT_ANALYZED_TOPIC`(analyzer가 **글자 없이 관절선만** 그려 다시 내보낸 화면).
횟수·각도·피드백 같은 **글자 정보는 영상에 쓰지 않고** `MQTT_ANALYSIS_TOPIC`의 JSON으로 받아 `/camera/analysis`로 내보내며, 웹이 영상 아래 패널로 직접 그린다.
analyzer 쪽 발행은 `./run_mqtt.sh`가 켠다. 이 JSON의 `checks`는 새 분석기(보정된 설정)의 규칙을 그대로 따른다:
`live=true` 조건은 지금 값, `live=false` 조건은 마지막 반복 결과로 판정하고, `enabled=false` 조건은 보내지 않는다.

로그인한 **모든 사용자가** 같은 카메라 영상을 볼 수 있다(기기가 하나인 현재 구조). 사용자별 접근 제어가 필요하면
카메라 소유자 개념을 추가해야 한다. 스트림 URL의 `?token=`은 접속 로그에 남을 수 있으므로 외부에 공개할 때는 HTTPS와 짧은 토큰 수명이 필요하다.

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
| GET | `/workouts/sessions/{id}/report` | **세션 리포트:** 부위별(팔·몸통·다리) 약점·등급·지적 횟수, 다음에 신경 쓸 부위(`focus`), 자주 지적된 자세, 같은 운동의 이전 세션 대비 점수 변화 |
| GET | `/workouts/insights` | **홈/기록용 요약:** 누적, 연속 운동일, 주간 활동, 부위별 약점과 추세, 다음 운동 추천, 최근 30세션. 날짜 경계는 `tz_offset`(UTC에서 동쪽으로 분, 한국 540) 기준 |
| POST | `/camera/session/start` | 웹에서 운동 세션 시작 `{exercise}`. analyzer에 제어 명령을 보낸다(202). analyzer가 꺼져 있거나 이미 운동 중이면 409, MQTT가 꺼져 있으면 503 |
| POST | `/camera/session/stop` | 운동 세션 종료(202). 끝난 세션 id는 `/camera/analysis`의 `info.session.last`로 알려 준다 |
| GET | `/camera/status` | Pi 카메라 수신 상태 `{enabled, broker_connected, online, fps, last_frame_age_seconds, frames_total}` |
| GET | `/camera/analysis` | analyzer가 보낸 최신 분석 정보 `{online, age_seconds, info}`. `info`: 횟수·단계·주 특징 값/기준선·실시간 피드백·**자세 조건별 판정(`checks`)**·마지막 반복·점수 이력 |
| GET | `/camera/snapshot?source=raw\|analyzed` | 최신 프레임 한 장 (JPEG). 아직 없으면 404 |
| GET | `/camera/stream?source=raw\|analyzed` | 실시간 영상 (MJPEG). `raw`=Pi 원본, `analyzed`=analyzer가 관절선을 그린 화면. `<img>`가 헤더를 못 붙이므로 `?token=<access_token>`도 허용 (이 경로만) |
| GET | `/camera/events` | 카메라/브로커 연결 변화 기록 (최신순, `limit`≤200) |
| GET | `/health` | 상태 확인 (인증 불필요, `/api/v1` 아래가 아님) |

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
- `parts`는 생략 가능한 `{arms, torso, legs}`(각 0~1, 반복에서 그 부위가 얼마나 약했나). 로컬 분석기가 보낸다. 생략하면(웹캠 분석, 예전 기록) 피드백 코드가 가리키는 부위를 0.7로 칠해서 응답한다 (`body_parts.py`). 응답의 `parts`는 항상 채워져 있다.
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
    camera/          Pi 카메라 MQTT 수신, 상태/스냅샷/스트림, 연결 기록
tests/               pytest (인메모리 SQLite)
```

모듈은 서로의 DB 모델을 직접 import하지 않고(공통인 `User` 제외) 라우터 단위로 나뉘어 있어, 나중에 서비스로 분리하기 쉽다.

## 알려진 한계 / 다음 단계

- 테이블은 시작 시 `create_all`로 만든다. 스키마가 굳으면 Alembic으로 바꿔야 한다.
- 액세스 토큰만 있고 리프레시 토큰은 없다. 30분이 넘는 운동에서는 로컬 분석기가 재로그인해야 한다.
- 로그인 시도 횟수 제한(rate limit)이 없다. 외부에 공개하기 전에 게이트웨이나 미들웨어로 추가해야 한다.
- 명세서의 Kafka, API Gateway, 컬럼 단위 암호화(AES-256), 알림 서비스는 아직 구현하지 않았다.
- PostgreSQL에서는 아직 테스트하지 않았다(테스트는 SQLite).
