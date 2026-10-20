"""Password hashing, JWT and role-based authorization."""
from datetime import timedelta
from typing import Any

import bcrypt
from jose import JWTError, jwt

from app.core.config import get_settings
from app.core.timeutil import utcnow


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode()[:72], bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode()[:72], hashed.encode())
    except ValueError:
        return False


def create_access_token(subject: Any, role: str, participant_id: Any | None = None) -> str:
    s = get_settings()
    payload: dict[str, Any] = {
        "sub": str(subject),
        "role": role,
        "pid": str(participant_id) if participant_id is not None else None,
        "exp": utcnow() + timedelta(minutes=s.access_token_minutes),
    }
    return jwt.encode(payload, s.require_secret(), algorithm=s.jwt_algorithm)


def decode_token(token: str) -> dict[str, Any]:
    s = get_settings()
    try:
        return jwt.decode(token, s.require_secret(), algorithms=[s.jwt_algorithm])
    except JWTError as exc:
        raise ValueError("invalid token") from exc
