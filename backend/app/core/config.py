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

    @model_validator(mode="after")
    def _require_secret_outside_dev(self) -> "Settings":
        if not self.jwt_secret:
            if self.app_env != "dev":
                raise ValueError("JWT_SECRET must be set when APP_ENV is not 'dev'")
            self.jwt_secret = _DEV_SECRET
        return self


settings = Settings()
