from __future__ import annotations

import argparse
import getpass
import logging
import os
import sys

import cv2

from aipt_analyzer.analyzer import Analyzer
from aipt_analyzer.config import available_exercises, load_exercise_config, load_messages
from aipt_analyzer.estimators import create_estimator
from aipt_analyzer.overlay import Overlay
from aipt_analyzer.pipeline import run_pipeline
from aipt_analyzer.recorder import SessionRecorder
from aipt_analyzer.sources import open_source
from aipt_analyzer.uploader import BackendClient, UploadWorker


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="aipt-analyzer",
        description="Pi 영상을 받아 자세를 분석하고 영상 위에 바로 표시한다. q로 종료.",
    )
    p.add_argument("--exercise", required=True, help=f"내장: {', '.join(available_exercises())} 또는 설정 JSON 경로")
    p.add_argument("--thresholds", help="학습/보정된 설정 JSON (--exercise의 내장 설정을 대체)")
    p.add_argument("--source", default="tcp://192.168.1.101:8888",
                   help="tcp://<Pi IP>:8888 | 웹캠 번호(0) | 영상 파일 경로")
    p.add_argument("--estimator", default="yolo")
    p.add_argument("--model", default="yolo11n-pose.pt", help="자세 추정 모델 가중치")
    p.add_argument("--backend", default="http://127.0.0.1:8000", help="백엔드 주소")
    p.add_argument("--no-upload", action="store_true", help="백엔드에 결과를 올리지 않는다")
    p.add_argument("--record", metavar="DIR", help="학습용 데이터를 이 폴더 아래에 기록한다")
    p.add_argument("--no-show", action="store_true", help="영상 창을 띄우지 않는다")
    p.add_argument("--seconds", type=float, default=0, help="이 시간 후 종료 (0이면 q/EOF까지)")
    p.add_argument("--messages", help="피드백 문구 JSON (기본: 내장 한국어)")
    return p


def _credentials() -> tuple[str, str]:
    # 비밀번호를 명령줄 인자로 받으면 셸 기록과 프로세스 목록에 남으므로 환경변수/프롬프트만 쓴다.
    email = os.environ.get("AIPT_EMAIL") or input("이메일: ")
    password = os.environ.get("AIPT_PASSWORD") or getpass.getpass("비밀번호: ")
    return email, password


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    args = build_parser().parse_args(argv)

    try:
        cfg = load_exercise_config(args.exercise, args.thresholds)
    except (FileNotFoundError, ValueError) as e:
        print(f"설정 오류: {e}", file=sys.stderr)
        return 2
    if not cfg.calibrated:
        logging.getLogger("aipt").warning(
            "'%s' 설정은 보정되지 않은 초기값입니다. 판정 결과는 참고용입니다.", cfg.exercise)

    worker = None
    if not args.no_upload:
        email, password = _credentials()
        client = BackendClient(args.backend, email, password)
        try:
            client.login()
            client.start_session(cfg.exercise)
        except Exception as e:  # 네트워크/인증 오류를 한 가지로 안내
            print(f"백엔드 연결 실패 ({args.backend}): {e}\n업로드 없이 쓰려면 --no-upload", file=sys.stderr)
            return 3
        worker = UploadWorker(client).start()

    recorder = SessionRecorder(args.record, cfg) if args.record else None
    overlay = None if args.no_show else Overlay(
        cfg.exercise, load_messages(args.messages), cfg.primary_feature)
    source = None
    try:
        estimator = create_estimator(args.estimator, model=args.model)
        source = open_source(args.source)
        reps = run_pipeline(
            Analyzer(cfg), source, estimator,
            overlay=overlay, recorder=recorder, worker=worker, seconds=args.seconds,
        )
        print(f"완료: {reps}회")
        return 0
    except (RuntimeError, FileNotFoundError, ValueError) as e:
        print(f"오류: {e}", file=sys.stderr)
        return 1
    finally:
        if source:
            source.close()
        if worker:
            worker.close()
        if recorder:
            recorder.close()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    raise SystemExit(main())
