from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from app.core.config import settings

# bcrypt는 72바이트까지만 해싱하므로 스키마에서 같은 한도로 입력을 막는다.
MAX_PASSWORD_BYTES = 72

# 존재하지 않는 이메일로 로그인할 때도 해시 비교 시간을 비슷하게 맞추기 위한 더미 해시
_DUMMY_HASH = bcrypt.hashpw(b"dummy-password", bcrypt.gensalt())


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str | None) -> bool:
    target = password_hash.encode() if password_hash else _DUMMY_HASH
    ok = bcrypt.checkpw(password.encode()[:MAX_PASSWORD_BYTES], target)
    return ok and password_hash is not None


def create_access_token(user_id: int) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> int | None:
    """유효한 토큰이면 user_id, 아니면 None."""
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["sub", "exp"]},
        )
        return int(payload["sub"])
    except (jwt.PyJWTError, ValueError):
        return None
