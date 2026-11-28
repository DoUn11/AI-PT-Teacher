# AI PT쌤 로컬 분석기

Pi 카메라 영상을 PC에서 받아 **자세를 분석하고 영상 위에 바로 표시**한다. 끝난 반복(rep)은
백엔드로 올리고, 학습용 데이터는 파일로 남긴다.

```
Pi (H.264 TCP:8888 또는 MQTT JPEG) → LiveSource / MqttSource → PoseEstimator(YOLO) → Analyzer → Overlay(영상 위 표시)
                                                           │  ├→ SessionRecorder (학습 데이터)
                                                           │  └→ UploadWorker → 백엔드
                                                  features → counter → judge
```

## 실행

```bash
cd analyzer
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e ".[dev,yolo]"     # yolo는 torch를 받아 무겁다

# 백엔드(../backend)를 띄운 상태에서. 계정은 먼저 /api/v1/auth/register로 만든다.
export AIPT_EMAIL=me@example.com AIPT_PASSWORD=...             # 없으면 실행 시 물어본다
.venv/bin/python -m aipt_analyzer --exercise squat --source tcp://<Pi IP>:8888

# 자주 쓰는 옵션
--no-upload            백엔드 없이 화면 표시만
--record recordings    학습용 데이터 기록
--source 0             웹캠 / 영상 파일 경로도 가능
--source mqtt://<브로커>:1883/<토픽>   Pi가 MQTT로 보내는 JPEG 프레임 (pip install -e ".[mqtt]")
--publish mqtt://<브로커>:1883/<토픽>       관절선만 그린 분석 화면을 웹용으로 내보내기 (글자 없음)
--publish-info mqtt://<브로커>:1883/<토픽>  횟수·자세 조건 판정 같은 분석 정보를 JSON으로 내보내기
--thresholds my.json   보정·학습된 설정으로 교체
--no-show --seconds 60 창 없이 60초만
```

종료는 영상 창에서 `q`. 비밀번호는 보안상 명령줄 인자로 받지 않는다(환경변수 또는 프롬프트).

```bash
.venv/bin/python -m pytest        # 테스트
```

## 확장 지점

판정 로직은 네 겹으로 나뉘어 있고, 각 층을 코드 수정 없이 또는 최소한으로 바꿀 수 있다.

| 바꾸고 싶은 것 | 방법 |
|---|---|
| **임계값**(깊이 목표, 올라온 판정 각도, 자세 한계 등) | 운동 설정 JSON을 고치거나 `--thresholds`로 학습된 파일을 지정 |
| **새 운동** | `configs/<이름>.json` 추가 (기존 특징을 조합). 피드백 문구는 `messages.ko.json`에 추가 |
| **새 특징**(예: 어깨 너비) | `features.py`의 `FEATURES`에 항목 추가 |
| **판정 방식**(학습 모델 등) | `RepJudge`를 구현하고 `@register_judge("이름")`, 설정의 `"judge": {"type": "이름"}` |
| **자세 추정 모델**(RTMPose 등) | `PoseEstimator`를 구현하고 `@register_estimator("이름")`, `--estimator 이름` |

### 운동 설정 JSON

```jsonc
{
  "exercise": "squat", "version": 1,
  "calibrated": false,              // 실측/학습으로 보정된 값이면 true (false면 실행 시 경고)
  "primary_feature": "knee_angle",  // 이 값의 위→아래→위 움직임으로 rep을 센다
  "up_threshold": 160,              // 이 이상이면 시작(위) 자세
  "count_threshold": 130,           // 여기까지 내려가야 1회로 센다
  "smoothing": 0.6, "min_rep_seconds": 0.6, "min_keypoint_conf": 0.3,
  "depth": { "code": "shallow_depth", "target": 100, "tolerance": 10,
             "penalty_per_degree": 2.5, "min_score": 30 },
  "form_checks": [   // stat이 max면 최댓값 ≤ limit, min이면 최솟값 ≥ limit이어야 함
    { "code": "trunk_lean", "feature": "trunk_lean", "stat": "max", "limit": 55, "penalty": 15 }
  ],
  "judge": { "type": "threshold" }
}
```

잘못된 값(예: `count_threshold ≥ up_threshold`, 모르는 특징 이름)은 시작할 때 오류로 알려 준다.
학습 결과를 이 형식의 파일로 내보내면 그대로 쓸 수 있다. 내장 `squat`/`pushup`은 **미검증 초기값**이다.

사용 가능한 특징: `knee_angle`, `hip_angle`, `elbow_angle`, `body_line`, `trunk_lean`.
화면에 더 잘 보이는 쪽 몸(왼/오른쪽)으로 계산하므로 **측면 촬영**을 가정한다.

### 판정기를 학습 모델로 바꾸려면

```python
from aipt_analyzer import Judgement, register_judge

@register_judge("my_model")
class MyModelJudge:
    def __init__(self, cfg):                 # cfg.judge에 모델 경로 등 자유롭게 넣을 수 있다
        self.model = load(cfg.judge["path"])
    def judge(self, summary):                # RepSummary: 반복 하나의 특징 통계
        score, codes = self.model.predict(summary.stats)
        return Judgement(score=score, feedback=tuple(codes))
    def live_feedback(self, values):         # 진행 중 즉시 피드백 (필요 없으면 [])
        return []
```

rep을 세는 부분(`up/count_threshold`)은 상태 머신이 설정값을 쓰므로, 이 값들도 학습해서 JSON에 넣으면 된다.

## 학습 데이터 (`--record DIR`)

세션마다 `DIR/<운동>-<UTC시각>/` 폴더가 생긴다.

| 파일 | 내용 |
|---|---|
| `meta.json` | 운동, 시작 시각, **사용한 설정 전체**(버전 포함) |
| `frames.jsonl` | 프레임마다 `{t, visible, phase, keypoints[17][x,y,conf], features}` |
| `reps.jsonl` | rep마다 요약 통계(`stats`: 특징별 min/max/mean, `primary_min`, `duration`) + 현재 설정의 `score`/`feedback` |

- `keypoints`가 모델 원본 출력이라, 특징 정의를 바꿔도 `frames.jsonl`에서 다시 계산할 수 있다.
- **사람이 매긴 정답 라벨은 아직 없다.** `reps.jsonl`의 `index`를 키로 별도 라벨 파일을 만들어 학습에 쓰면 된다. 현재 `score`/`feedback`은 미검증 규칙의 출력이므로 정답으로 쓰지 말 것.
- 용량: 30fps에서 대략 시간당 100MB 안팎(추정). 영상 자체는 저장하지 않는다.

## 구조

```
aipt_analyzer/
  pose.py        Pose(COCO 17점), 각도 계산
  features.py    Pose → 특징 (FEATURES 레지스트리)
  config.py      운동 설정 로더/검증 (configs/*.json)
  counter.py     rep 상태 머신
  judges.py      RepJudge 인터페이스 + 임계값 판정기 + 레지스트리
  analyzer.py    프레임 파이프라인 (스무딩 포함)
  estimators.py  PoseEstimator 레지스트리 + YOLO 어댑터
  sources.py     tcp 스트림/웹캠(항상 최신 프레임만) / 파일
  overlay.py     영상 위 표시 (한글은 PIL 폰트, 없으면 영문)
  recorder.py    학습 데이터 기록
  uploader.py    백엔드 클라이언트 + 백그라운드 업로드
  pipeline.py    메인 루프, cli.py  진입점
```

## 검증 범위와 한계

- 합성 키포인트로 로직을 테스트했다(41개). 실제 백엔드 서버와 연결해 업로드·저장까지 확인했다.
- **아직 실제로 돌려 보지 않은 것**: 실제 Pi 스트림(`tcp://`) 수신, YOLO 추정(`ultralytics` 미설치 상태로 테스트), 실제 사람 영상. 이 부분은 어댑터 로직만 가짜 결과 객체로 테스트했다.
- Pi 스트림에 적용한 FFmpeg 저지연 옵션(`OPENCV_FFMPEG_CAPTURE_OPTIONS`)은 실제 스트림에서 효과를 확인해야 한다.
- 한 명(화면에서 가장 큰 사람)만 분석한다. 푸시업 같은 수평 자세에서 키포인트가 흔들릴 수 있어 실측 보정이 필요하다.
- 한 세션 안에서 카메라 쪽 몸이 바뀌면(몸을 돌리면) 특징이 불연속할 수 있다.
- 화면에서 사람이 오래(1초 이상) 사라지면 스무딩 이력은 버리지만, 진행 중이던 rep 상태는 유지한다.

## 웹에 분석 화면 보여 주기

로컬 창과 별개로, 웹 "Pi 카메라" 탭이 분석 결과를 보여 줄 수 있다. 영상에는 **글자를 쓰지 않고**(관절 부위만 선으로 잇는다),
글자 정보는 JSON으로 따로 보내 웹이 영상 아래에 그린다.

- **관절선 색:** 올바른 부위는 초록, 기준에 다가갈수록 노랑을 거쳐 빨강으로 서서히 변한다. 실시간(`live`) 조건은 지금 값으로,
  반복이 끝난 뒤에만 판정하는 조건(`live: false`)과 깊이 부족은 반복 직후 해당 부위를 잠깐 붉게 한다. `enabled: false` 조건은 무시한다.
  어느 조건이 어느 부위(팔/상체/다리)인지는 `severity.py`에 있고, 오버레이와 웹 패널이 같은 기준(`check_severity`)을 쓴다.
- **분석 정보 JSON(`publisher.py`):** 조건별 판정(`checks`), 주 특징 값과 기준선, 횟수, 마지막 반복, 점수 이력. 비율 특징도 있어서 값은 소수 3자리까지 보낸다.
- 새 특징이나 조건을 추가하면 `severity.py`의 `FEATURE_PARTS`(부위)와 프런트 `analysis-view.js`의 `FEATURE_META`(이름/단위)에 한 줄씩 넣는다.
  모르는 특징은 이름 그대로, 비율처럼 표시된다.

## 웹에서 운동 시작/종료 (세션)

`--control`을 켜면(`run_mqtt.sh`는 항상 켠다) 분석기는 **준비 상태**로 시작한다. 자세 표시와 관절선은 돌지만 횟수는 세지 않고 백엔드에도 올리지 않는다.
웹의 **운동 시작**을 누르면(`{"action":"start","exercise":"..."}`) 백엔드에 세션을 열고 0회부터 세며(`station.py`), **운동 종료**(`{"action":"stop"}`)를 누르면 남은 반복을 올리고
세션을 닫은 뒤 방금 끝난 세션의 id를 분석 정보(`session.last`)로 알린다. 웹은 그 id의 리포트로 이동한다. `--control` 없이 쓰면 예전처럼 켜자마자 세션이 열리고 종료할 때 닫힌다.

- 반복마다 `parts`(팔·몸통·다리 약점, 0~1)를 함께 올린다. 판정기가 지적한 항목만 보고 계산한다(`severity.rep_parts`).
- 분석 정보 JSON은 `v: 3`부터 `session: {state: idle|active, id, started_at, last}`를 싣는다.
- 전체 흐름과 화면은 [../docs/USECASES.md](../docs/USECASES.md).
