// 노트북 웹캠(라즈베리파이 대용) -> 브라우저 자세 추정(MediaPipe) -> 분석 -> 영상 위 표시 -> 백엔드 업로드
import { Analyzer } from "./analysis.js?v=20261110.14";
import { drawOverlay, FLASH_MS } from "./overlay.js?v=20261110.14";
import { RepUploader } from "./uploader.js?v=20261110.14";

// 버전을 고정해 두었다. 올릴 때는 vision_bundle.mjs와 wasm 폴더가 같은 버전인지 확인할 것.
const MP_BASE = "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14";
const MODEL_URL =
  "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task";

// MediaPipe 33점 중 분석에 쓰는 관절의 인덱스 (분석기의 COCO 이름과 맞춘다)
const MP_INDEX = {
  left_shoulder: 11, right_shoulder: 12, left_elbow: 13, right_elbow: 14,
  left_wrist: 15, right_wrist: 16, left_hip: 23, right_hip: 24,
  left_knee: 25, right_knee: 26, left_ankle: 27, right_ankle: 28,
};

let landmarkerPromise = null;

async function createLandmarker() {
  const { FilesetResolver, PoseLandmarker } = await import(`${MP_BASE}/vision_bundle.mjs`);
  const fileset = await FilesetResolver.forVisionTasks(`${MP_BASE}/wasm`);
  const options = (delegate) => ({
    baseOptions: { modelAssetPath: MODEL_URL, delegate },
    runningMode: "VIDEO",
    numPoses: 1,
  });
  try {
    return await PoseLandmarker.createFromOptions(fileset, options("GPU"));
  } catch {
    return PoseLandmarker.createFromOptions(fileset, options("CPU")); // GPU를 못 쓰는 환경
  }
}

export function getLandmarker() {
  landmarkerPromise ??= createLandmarker().catch((e) => {
    landmarkerPromise = null; // 네트워크 실패 후 다시 시도할 수 있게
    throw new Error(`자세 인식 모델을 불러오지 못했습니다 (인터넷 연결 확인): ${e.message ?? e}`);
  });
  return landmarkerPromise;
}

export function toPose(landmarks, w, h) {
  const xy = {};
  const conf = {};
  for (const [name, i] of Object.entries(MP_INDEX)) {
    const lm = landmarks[i];
    xy[name] = [lm.x * w, lm.y * h];
    conf[name] = lm.visibility ?? 1;
  }
  return { xy, conf };
}

function cameraErrorMessage(e) {
  switch (e?.name) {
    case "NotAllowedError": return "카메라 권한이 거부되었습니다. 브라우저 주소창에서 카메라를 허용해 주세요.";
    case "NotFoundError": return "카메라를 찾을 수 없습니다.";
    case "NotReadableError": return "카메라를 다른 프로그램이 사용 중입니다.";
    default: return e?.message ?? String(e);
  }
}

export class CameraSession {
  // video/canvas: 겹쳐 놓은 요소. api: 업로드할 때만 사용. onEvent({type, ...}): 화면 갱신용
  constructor({ video, canvas, messages, onEvent = () => {} }) {
    Object.assign(this, { video, canvas, messages, onEvent });
    this.running = false;
  }

  // cfg: 운동 설정 JSON, api: 업로드하려면 Api 인스턴스(안 올리려면 null)
  async start(cfg, api) {
    if (this.running) return;
    this.cfg = cfg;
    this.analyzer = new Analyzer(cfg);
    this.flash = null;
    this.uploader = null;
    this.reps = [];
    try {
      this.onEvent({ type: "status", text: "연결 중" });
      this.stream = await navigator.mediaDevices.getUserMedia({
        video: { width: { ideal: 640 }, height: { ideal: 480 }, facingMode: "user" },
        audio: false,
      });
      this.video.srcObject = this.stream;
      await this.video.play();
      this.canvas.width = this.video.videoWidth;
      this.canvas.height = this.video.videoHeight;

      this.onEvent({ type: "status", text: "모델 로딩" });
      this.landmarker = await getLandmarker();

      if (api) {
        const session = await api.createSession(cfg.exercise);
        this.uploader = new RepUploader(api, session.id);
        this.sessionId = session.id;
      }
    } catch (e) {
      this._releaseCamera();
      throw new Error(e.name ? cameraErrorMessage(e) : e.message);
    }
    this.running = true;
    this.onEvent({ type: "status", text: "분석 중" });
    this._lastVideoTime = -1;
    this._raf = requestAnimationFrame(() => this._tick());
  }

  _tick() {
    if (!this.running) return;
    const { video, canvas } = this;
    if (video.readyState >= 2 && video.currentTime !== this._lastVideoTime) {
      this._lastVideoTime = video.currentTime;
      const w = canvas.width;
      const h = canvas.height;
      const now = performance.now();
      const result = this.landmarker.detectForVideo(video, now);
      const pose = result.landmarks?.[0] ? toPose(result.landmarks[0], w, h) : null;
      const frame = this.analyzer.process(pose, now / 1000);

      if (frame.rep) {
        this.flash = { rep: frame.rep, until: now + FLASH_MS };
        this.reps.push(frame.rep);
        this.uploader?.enqueue(frame.rep);
        this.uploader?.flush().then(() => this._emitUpload());
        this.onEvent({ type: "rep", rep: frame.rep });
      }
      drawOverlay(canvas.getContext("2d"), w, h, frame, pose, {
        exercise: this.cfg.exercise,
        primaryFeature: this.cfg.primary_feature,
        messages: this.messages,
        flash: this.flash,
        now,
      });
    }
    this._raf = requestAnimationFrame(() => this._tick());
  }

  _emitUpload() {
    if (this.uploader) {
      this.onEvent({ type: "upload", pending: this.uploader.pending.length, error: this.uploader.lastError });
    }
  }

  _releaseCamera() {
    this.stream?.getTracks().forEach((t) => t.stop());
    this.stream = null;
    this.video.srcObject = null;
  }

  // 분석을 멈추고 카메라를 끄고, 업로드를 마무리한다. 종료된 세션 정보(또는 null)를 돌려준다.
  async stop() {
    if (!this.running) return null;
    this.running = false;
    cancelAnimationFrame(this._raf);
    this._releaseCamera();
    this.canvas.getContext("2d").clearRect(0, 0, this.canvas.width, this.canvas.height);
    if (!this.uploader) return null;
    this.onEvent({ type: "status", text: "저장 중" });
    const finished = await this.uploader.finish();
    this._emitUpload();
    return finished;
  }
}
