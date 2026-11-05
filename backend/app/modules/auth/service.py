from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security import hash_password, verify_password
from app.modules.auth.models import User


class EmailAlreadyRegistered(Exception):
    pass


def _normalize(email: str) -> str:
    return email.strip().lower()


def create_user(db: Session, email: str, password: str, nickname: str) -> User:
    user = User(
        email=_normalize(email),
        password_hash=hash_password(password),
        nickname=nickname.strip(),
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise EmailAlreadyRegistered() from None
    return user


def authenticate(db: Session, email: str, password: str) -> User | None:
    user = db.scalar(select(User).where(User.email == _normalize(email)))
    # 사용자가 없어도 verify_password를 호출해 응답 시간 차이로 가입 여부가 드러나지 않게 한다.
    if verify_password(password, user.password_hash if user else None) and user:
        return user
    return None
