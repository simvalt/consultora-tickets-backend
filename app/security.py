import hashlib
import secrets
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.config import get_settings

_hasher = PasswordHasher()
# Hash de relleno para igualar tiempos cuando el correo no existe.
DUMMY_HASH = _hasher.hash("dummy-password-for-timing")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def create_access_token(user_id: int) -> tuple[str, int]:
    s = get_settings()
    now = datetime.now(UTC)
    exp = now + timedelta(minutes=s.access_token_minutes)
    token = jwt.encode({"sub": str(user_id), "iat": now, "exp": exp}, s.jwt_secret, algorithm="HS256")
    return token, s.access_token_minutes * 60


def decode_access_token(token: str) -> tuple[int, int] | None:
    """Devuelve (user_id, iat) si el token es válido; None si no lo es."""
    try:
        payload = jwt.decode(
            token, get_settings().jwt_secret, algorithms=["HS256"], options={"require": ["exp", "iat", "sub"]}
        )
        return int(payload["sub"]), int(payload["iat"])
    except (jwt.PyJWTError, ValueError, TypeError):
        return None


def new_refresh_token() -> tuple[str, str]:
    raw = secrets.token_urlsafe(48)
    return raw, hash_refresh_token(raw)


def hash_refresh_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()
