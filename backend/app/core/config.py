from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEV_SECRET = "dev-only-insecure-secret-change-me-0123456789"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "dev"
    database_url: str = "sqlite:///./aipt.db"
    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    # 웹 프론트엔드 출처 (JSON 배열 문자열로 환경변수 지정 가능)
    cors_origins: list[str] = ["http://localhost:3000", "http://localhost:5173"]
    # Pi 카메라 수신(MQTT). 비어 있으면 꺼진다. 예: mqtt://localhost:1883
    mqtt_url: str = ""
    mqtt_frame_topic: str = "ai_pt/cam/frame"
    # analyzer가 오버레이(관절선·횟수·피드백)를 그려 내보내는 분석 화면 토픽
    mqtt_analyzed_topic: str = "ai_pt/cam/analyzed"
    # analyzer가 횟수·각도·피드백 같은 분석 정보를 JSON으로 내보내는 토픽
    mqtt_analysis_topic: str = "ai_pt/cam/analysis"
    # 웹에서 고른 운동을 analyzer에 알리는 제어 토픽
    mqtt_control_topic: str = "ai_pt/cam/control"
    # 이 시간(초) 넘게 프레임이 없으면 카메라를 오프라인으로 본다
    camera_offline_after_seconds: float = 5.0

    @model_validator(mode="after")
    def _require_secret_outside_dev(self) -> "Settings":
        if not self.jwt_secret:
            if self.app_env != "dev":
                raise ValueError("JWT_SECRET must be set when APP_ENV is not 'dev'")
            self.jwt_secret = _DEV_SECRET
        return self


settings = Settings()
