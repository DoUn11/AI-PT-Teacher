# AI PT쌤 프론트엔드 (간단 버전)

빌드 도구 없는 정적 웹앱(순수 JS 모듈). 두 화면이 있다.

| 화면 | 역할 | 대응하는 실제 구성 |
|---|---|---|
| **촬영 (웹캠)** | 노트북 웹캠 → 브라우저에서 자세 추정 → 영상 위에 스켈레톤·각도·rep·피드백 표시 → 결과를 백엔드에 저장 | 라즈베리파이 + `analyzer/` 를 브라우저 하나로 대신한 것 |
| **기록 (결과 확인)** | 로그인한 사용자의 운동별 통계, 세션 목록, rep별 점수·피드백 | 백엔드 조회 API의 소비자(웹) |

## 실행

```bash
# 1) 백엔드 (다른 터미널)
cd backend && .venv/bin/uvicorn app.main:app

# 2) 프론트엔드
cd frontend && npm start          # = python3 -m http.server 5173 --bind localhost
```

브라우저에서 **`http://localhost:5173`** 을 연다 (`127.0.0.1`로 열면 백엔드 CORS에 막힌다).
처음 화면에서 회원가입 → 로그인. 서버 주소 기본값은 `http://127.0.0.1:8000`.

```bash
cd frontend && npm test           # Node 내장 테스트 러너, 추가 설치 없음 (Node 20+)
```

## 구성

```
index.html, styles.css
configs/            analyzer/aipt_analyzer/configs/ 의 복사본 (운동 설정 + 피드백 문구)
src/
  analysis.js       analyzer(Python)의 판정 로직 포팅: 특징 → 스무딩 → rep 상태 머신 → 판정
  camera.js         웹캠 + MediaPipe PoseLandmarker(CDN) + 분석 루프 + 업로드
  overlay.js        캔버스에 스켈레톤/HUD/피드백 그리기
  uploader.js       rep 업로드 대기열 (실패 시 재시도, 재전송해도 안전)
  api.js            백엔드 클라이언트 (토큰은 sessionStorage)
  dashboard.js      기록 화면
  main.js, dom.js   화면 연결, 안전한 DOM 헬퍼
tests/              analysis / uploader 테스트
```

## 알아둘 점

- **분석 로직이 두 벌이다.** `analysis.js`는 Python 분석기를 옮긴 것이다. 같은 합성 자세로 같은 결과가 나오는지 테스트하고(예: 상체 기울어짐 85점), `configs/*.json`이 분석기 원본과 **같은지도 테스트**가 확인한다. 임계값을 바꾸면 원본을 고친 뒤 복사해야 한다:
  ```bash
  cp analyzer/aipt_analyzer/configs/{squat,pushup,messages.ko}.json frontend/configs/
  ```
  규칙 로직(`analysis.js`)을 바꿀 때는 Python 쪽도 같이 바꿔야 한다. 로직이 안정되면 한쪽만 남기는 편이 좋다.
- **자세 추정 모델이 다르다.** 브라우저는 MediaPipe(33점)를, `analyzer/`는 YOLO(17점)를 쓴다. 판정에 쓰는 관절(어깨·팔꿈치·손목·엉덩이·무릎·발목)은 같지만 값이 약간 다를 수 있어, 학습한 임계값은 모델별로 보정해야 한다.
- **외부 CDN에 의존한다.** MediaPipe 라이브러리(jsdelivr, 버전 고정)와 모델(storage.googleapis.com)을 실행 시 내려받으므로 인터넷이 필요하고, 처음에는 몇 초 걸린다.
- **웹캠은 `localhost`나 HTTPS에서만** 켜진다. 같은 네트워크의 다른 기기에서 `http://<IP>:5173`으로 열면 브라우저가 카메라를 막는다.
- 측면 촬영·한 명을 가정한다. 판정 기준은 `calibrated: false` 초기값이다.
- 촬영 중에 탭을 닫으면 세션이 서버에서 "진행 중"으로 남는다 (종료 요청은 `종료` 버튼이나 탭 전환 시에만 간다).
- 로그인 토큰은 `sessionStorage`에 둔다 (탭을 닫으면 사라짐). 리프레시 토큰이 없어서 30분 뒤에는 다시 로그인해야 한다.

## 검증 범위

- 자동 테스트 19개: 판정 로직, 설정 일치, 업로드 규약·재시도.
- 실제 백엔드와 브라우저로 확인: 로그인, 새로고침 후 세션 유지, 통계·세션·상세 표시, 닉네임의 HTML 태그가 글자로만 표시됨(XSS 방어), MediaPipe 모델 로드, 실제 사람 사진에서 관절 검출과 특징 계산.
- **확인하지 못한 것: 실제 웹캠으로 운동하는 흐름.** 개발 환경의 브라우저가 카메라를 막아서, 웹캠 영상 → 실시간 추론 → rep 카운트 → 업로드의 전체 흐름과 실시간 성능(FPS)은 직접 돌려 봐야 한다.
