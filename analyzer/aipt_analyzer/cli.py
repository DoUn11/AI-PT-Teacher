from __future__ import annotations

import argparse
import getpass
import logging
import os
import sys

import cv2

from aipt_analyzer.config import available_exercises, load_exercise_config, load_messages
from aipt_analyzer.estimators import create_estimator
from aipt_analyzer.overlay import Overlay
from aipt_analyzer.control import ExerciseControl
from aipt_analyzer.pipeline import run_pipeline
from aipt_analyzer.publisher import AnalysisPublisher, FramePublisher
from aipt_analyzer.sources import open_source
from aipt_analyzer.station import Station
from aipt_analyzer.uploader import BackendClient


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="aipt-analyzer",
        description="Pi 영상을 받아 자세를 분석하고 영상 위에 바로 표시한다. q로 종료.",
    )
    p.add_argument("--exercise", required=True, help=f"내장: {', '.join(available_exercises())} 또는 설정 JSON 경로")
    p.add_argument("--thresholds", help="학습/보정된 설정 JSON (--exercise의 내장 설정을 대체)")
    p.add_argument("--source", default="tcp://192.168.1.101:8888",
                   help="tcp://<Pi IP>:8888 | mqtt://<브로커>[:1883][/토픽] | 웹캠 번호(0) | 영상 파일 경로")
    p.add_argument("--estimator", default="yolo")
    p.add_argument("--model", default="yolo11n-pose.pt", help="자세 추정 모델 가중치")
    p.add_argument("--backend", default="http://127.0.0.1:8000", help="백엔드 주소")
    p.add_argument("--no-upload", action="store_true", help="백엔드에 결과를 올리지 않는다")
    p.add_argument("--record", metavar="DIR", help="학습용 데이터를 이 폴더 아래에 기록한다")
    p.add_argument("--no-show", action="store_true", help="영상 창을 띄우지 않는다")
    p.add_argument("--publish", metavar="MQTT_URL",
                   help="오버레이가 그려진 분석 화면을 이 MQTT 토픽으로 발행 (예: mqtt://localhost:1883/ai_pt/cam/analyzed)")
    p.add_argument("--publish-info", metavar="MQTT_URL",
                   help="횟수·각도·피드백 같은 분석 정보를 이 MQTT 토픽으로 JSON 발행 "
                        "(예: mqtt://localhost:1883/ai_pt/cam/analysis)")
    p.add_argument("--control", metavar="MQTT_URL",
                   help="웹에서 운동을 고르고 세션을 시작/종료할 수 있게 이 MQTT 제어 토픽을 구독 "
                        "(예: mqtt://localhost:1883/ai_pt/cam/control). 켜면 준비 상태로 시작해 웹의 시작을 기다린다")
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

    client = None
    if not args.no_upload:
        email, password = _credentials()
        client = BackendClient(args.backend, email, password)
        try:
            client.login()
        except Exception as e:  # 네트워크/인증 오류를 한 가지로 안내
            print(f"백엔드 연결 실패 ({args.backend}): {e}\n업로드 없이 쓰려면 --no-upload", file=sys.stderr)
            return 3

    messages = load_messages(args.messages)
    # 창을 안 띄워도 분석 화면을 내보내려면 오버레이는 그려야 한다
    make_overlay = not args.no_show or bool(args.publish)

    def build_overlay(c):
        return Overlay(c.exercise, messages, c.primary_feature, cfg=c) if make_overlay else None

    source = None
    publisher = None
    info_publisher = None
    control = None
    station = None

    try:
        if args.publish:
            publisher = FramePublisher(args.publish)
        if args.publish_info:
            info_publisher = AnalysisPublisher(args.publish_info, cfg)
        if args.control:
            control = ExerciseControl(args.control)
        estimator = create_estimator(args.estimator, model=args.model)
        # 웹이 시작/종료를 누르는 쪽(--control)이면 준비 상태로 기다리고, 아니면 켜자마자 세션을 연다
        station = Station(cfg, client=client, build_overlay=build_overlay, record_dir=args.record,
                          info=info_publisher, autostart=control is None)
        if client is not None and control is None and station.session_id is None:
            print(f"백엔드에 세션을 열지 못했습니다 ({args.backend}). 업로드 없이 쓰려면 --no-upload", file=sys.stderr)
            return 3
        parts = station.parts
        source = open_source(args.source)
        run_pipeline(
            parts.analyzer, source, estimator,
            overlay=parts.overlay, recorder=parts.recorder, worker=parts.worker, seconds=args.seconds,
            show=not args.no_show, on_annotated=publisher.publish if publisher else None,
            on_result=info_publisher.update if info_publisher else None,
            switch=(lambda: station.step(control.take())) if control else None,
        )
        print(f"완료: {station.parts.analyzer.counter.reps}회")
        return 0
    except (RuntimeError, FileNotFoundError, ValueError) as e:
        print(f"오류: {e}", file=sys.stderr)
        return 1
    finally:
        if source:
            source.close()
        if control:
            control.close()
        if publisher:
            publisher.close()
        if info_publisher:
            info_publisher.close()
        if station:  # 진행 중인 세션이 있으면 남은 반복을 보내고 닫는다
            station.shutdown()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    raise SystemExit(main())
