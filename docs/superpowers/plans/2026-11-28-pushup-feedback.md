# 푸시업 피드백 개선 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 푸시업에서 손을 넓게 짚으면 경고하고, 얕게 내려간 동작도 1회로 세어 "더 깊이 내려가세요" + 감점하며, 손 위치 피드백을 없앤다.

**Architecture:** 새 양쪽 몸 특징 `hand_out`을 파이썬 분석기(`features.py`)와 프론트(`analysis.js`)에 똑같이 추가한다. 푸시업 설정은 `calibrate.py`의 고정 조건·깊이 재정의로 표현하고, 그 결과와 같은 모양으로 두 `pushup.json`을 고친다(이 컴퓨터엔 AI Hub 데이터가 없어 스크립트를 돌릴 수 없음).

**Tech Stack:** Python 3 + numpy + pytest (analyzer), 바닐라 JS ES modules + `node --test` (frontend)

**Spec:** `docs/superpowers/specs/2026-11-28-pushup-feedback-design.md`

## Global Constraints

- 프론트 `frontend/configs/*.json`은 `analyzer/aipt_analyzer/configs/*.json`과 바이트 단위로 같아야 한다 (기존 테스트가 검사).
- 파이썬과 JS 특징 값·점수는 같은 숫자여야 한다 (같은 이름 테스트, 같은 기대값).
- `hand_out` limit 0.25, full_at 0.5, penalty 20, stat mean, direction above, tier medium, live true.
- count_threshold 145 (up_threshold 150 유지), depth target 129.721 유지, penalty_per_degree 2.0, min_score 60.
- 메시지: `"hand_width": "손을 어깨 너비로 짚으세요"`, `hand_position` 키 삭제.
- 캐시 버전 `?v=20261127.1` → `?v=20261128.1` (frontend/index.html, frontend/src 전체).

## Review Focus

- 한쪽 손만 넓게 짚음 → 경고가 떠야 한다 (양손 중 큰 값). Task 1 테스트로 고정.
- 카메라 좌우 반전 / 사람 좌우 바뀜 → "바깥"은 어깨 중점 기준이라 같은 값. Task 1에서 좌우를 뒤집은 자세로 고정.
- 손목이 어깨 안쪽 (좁게 짚음) → 음수, 경고 없음. Task 1 테스트.
- 140~145° 사이까지만 내려간 동작 → 1회로 세고 70점 + shallow_depth. Task 2 테스트.
- 145°보다 덜 내려간 흔들림(148°) → 1회로 세지 않는다. Task 2 테스트.

---

### Task 1: `hand_out` 특징 (분석기 + 프론트)

**Files:**
- Modify: `analyzer/aipt_analyzer/features.py` (`_hand_ratio` 옆에 `_hand_out`, `BODY_FEATURES`에 등록)
- Modify: `frontend/src/analysis.js` (`BODY_FEATURES`의 `hand_ratio` 다음에 `hand_out`)
- Test: `analyzer/tests/test_core.py`, `frontend/tests/analysis.test.js`

**Interfaces:**
- Produces: 특징 이름 `"hand_out"` (float, 키포인트 `left/right_shoulder`, `left/right_wrist`). 정의: 각 쪽 s에 대해 `dot(wrist_s - shoulder_s, unit(shoulder_s - mid(shoulders))) / shoulder_width`, 두 값 중 큰 값. 어깨 너비 < 1e-6이면 None/null.

- [ ] **Step 1: 실패하는 테스트 작성 (두 언어 같은 숫자)**

`test_body_features_on_frontal_pose` / JS 같은 테스트의 기대값에 `hand_out: 0.375` 추가 (frontal_pose: 어깨 120/80, 손목 135/65 → 15/40).

새 테스트 `test_hand_out_uses_wider_hand_and_is_mirror_safe` (JS: `"hand_out uses the wider hand and is mirror safe"`), frontal_pose 손목만 바꿔서:
- 오른손목 (80,195), 왼손목 (135,195) → 0.375 (한쪽만 넓음)
- 좌우 키포인트 이름을 서로 바꾼 frontal_pose → 0.375
- 손목 (110,195)/(90,195) → -0.25 (안쪽)

- [ ] **Step 2: 실패 확인**

Run: `cd analyzer && python -m pytest tests/test_core.py -k "frontal_pose or hand_out" -q` / `cd frontend && npm test`
Expected: KeyError / undefined `hand_out`로 FAIL

- [ ] **Step 3: `_hand_out(p) -> float | None` (features.py), `hand_out: { keypoints: both("shoulder","wrist"), compute }` (analysis.js) 구현**

- [ ] **Step 4: 통과 확인** — 같은 명령, 전부 PASS (기존 테스트 포함)

- [ ] **Step 5: 커밋** `feat: add hand_out feature (wrist past same-side shoulder / shoulder width)`

---

### Task 2: 푸시업 설정, 메시지, calibrate.py, 문서

**Files:**
- Modify: `analyzer/calibration/calibrate.py` (pushup spec)
- Modify: `analyzer/aipt_analyzer/configs/pushup.json`, `frontend/configs/pushup.json` (같은 내용)
- Modify: `analyzer/aipt_analyzer/configs/messages.ko.json`, `frontend/configs/messages.ko.json`
- Modify: `frontend/README.md` (푸시업 피드백 표 60~72행), `analyzer/calibration/HANDOFF.md` (변경 한 줄)
- Modify: `frontend/index.html`, `frontend/src/*.js` 캐시 버전
- Test: `analyzer/tests/test_core.py`, `frontend/tests/analysis.test.js`

**Interfaces:**
- Consumes: Task 1의 `"hand_out"` 특징.

- [ ] **Step 1: 실패하는 테스트 작성 (두 언어 같은 숫자)**

`test_pushups_counted_and_shallow_ones_penalised_lightly` → 이름을 `test_pushups_counted_and_shallow_ones_penalised` (JS `"pushups are counted and shallow ones penalised"`)로 바꾸고:
- good (170→80): 1회, 100점, 피드백 없음 (그대로)
- sag: 그대로
- `cycle(170, 136, frames=60)` → 1회, 90점, `["shallow_depth"]` (6.28°×2=12.6 감점 → 87.4 → 90)
- `cycle(170, 143, frames=60)` → 1회, 70점, `["shallow_depth"]` (새: 140~145도 셈)
- `cycle(170, 148, frames=60)` → 0회 (새)

새 테스트 `test_pushup_wide_hands_flagged` (JS `"pushup wide hands are flagged"`): pushup 설정의 ThresholdJudge에 `primary_min=80`, `stats={"hand_out": {min/max/mean: 0.375}}` → 90점, `["hand_width"]`; 0.25면 100점, 피드백 없음.

설정 검사에 추가: pushup form_checks 코드 집합 == {"hand_width", "body_line"}, `"hand_position"`이 messages에 없음.

- [ ] **Step 2: 실패 확인** — 143 케이스 0회, hand_width 기대 불일치로 FAIL

- [ ] **Step 3: calibrate.py 고치기**

pushup spec: `count_threshold` 145, `checks`에서 두 줄 삭제 (빈 리스트), `fixed_checks` 맨 앞에 hand_width 고정 조건 (Global Constraints 값, `"_note": "어깨-손목 위치 고정 규칙 (데이터 라벨 없음). 웹캠으로 조정"`), 새 키 `"depth_override": {"penalty_per_degree": 2.0, "min_score": 60}`를 `build()`에서 depth dict에 덮어쓰기.

- [ ] **Step 4: pushup.json 두 벌을 calibrate.py가 낼 모양으로 고치기**

count_threshold 145, depth의 penalty_per_degree 2.0 / min_score 60, form_checks = [hand_width 고정 조건, body_line(그대로)]. `_note`에 "2026-11-28 hand_width·depth는 고정값, 데이터 없이 손으로 맞춤" 추가.

- [ ] **Step 5: messages.ko.json 두 벌** — hand_width 문구 교체, hand_position 삭제

- [ ] **Step 6: 테스트 통과 확인**

Run: `cd analyzer && python -m pytest -q` / `cd frontend && npm test`
Expected: 전부 PASS

- [ ] **Step 7: README 표 / HANDOFF / 캐시 버전 갱신**

README 푸시업 표: 손 간격 행 → "손을 어깨 너비로 짚으세요 | 손목이 어깨보다 바깥으로 어깨 너비 25% 초과 (평균) | 20 | 켬", 손 위치 행 삭제, 깊이 행 감점 40으로; 테스트 개수 갱신. 캐시 버전 일괄 치환 후 `grep -rn "20261127.1" frontend` 결과 없음.

- [ ] **Step 8: 커밋** `feat: pushup wide-hand check by shoulder/wrist position, count shallow reps, drop hand_position`
