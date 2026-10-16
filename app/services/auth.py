import threading
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import Connection, text

from app.config import get_settings
from app.deps import CurrentUser, load_permissions
from app.errors import AppError
from app.security import DUMMY_HASH, create_access_token, hash_refresh_token, new_refresh_token, verify_password


class LoginRateLimiter:
    """Límite de intentos fallidos por (IP, correo) en memoria. Con varias réplicas: usar Redis o el WAF de Cloudflare."""

    def __init__(self) -> None:
        self._fails: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, key: str) -> list[float]:
        s = get_settings()
        cutoff = time.monotonic() - s.login_window_seconds
        recent = [t for t in self._fails.get(key, []) if t > cutoff]
        self._fails[key] = recent
        return recent

    def check(self, key: str) -> None:
        with self._lock:
            if len(self._recent(key)) >= get_settings().login_max_attempts:
                raise AppError(429, "too_many_attempts", "Demasiados intentos fallidos. Inténtalo más tarde.")

    def fail(self, key: str) -> None:
        with self._lock:
            self._recent(key).append(time.monotonic())

    def clear(self, key: str) -> None:
        with self._lock:
            self._fails.pop(key, None)

    def reset(self) -> None:
        with self._lock:
            self._fails.clear()


limiter = LoginRateLimiter()


def _me(conn: Connection, row) -> dict:
    return {
        "id": row["id"], "email": row["email"], "full_name": row["full_name"], "role_id": row["role_id"],
        "role_name": row["role_name"], "permissions": load_permissions(conn, row["role_id"]),
    }


def me_payload(conn: Connection, user: CurrentUser) -> dict:
    return {
        "id": user.id, "email": user.email, "full_name": user.full_name, "role_id": user.role_id,
        "role_name": user.role_name, "permissions": user.permissions,
    }


def _issue_refresh(conn: Connection, user_id: int) -> tuple[str, datetime]:
    raw, digest = new_refresh_token()
    expires = datetime.now(UTC) + timedelta(days=get_settings().refresh_token_days)
    conn.execute(
        text("INSERT INTO refresh_tokens (user_id, token_hash, expires_at) VALUES (:u, :h, :e)"),
        {"u": user_id, "h": digest, "e": expires},
    )
    return raw, expires


def login(conn: Connection, email: str, password: str, ip: str) -> tuple[dict, str, datetime]:
    key = f"{ip}|{email}"
    limiter.check(key)
    row = conn.execute(
        text(
            "SELECT u.id, u.email, u.full_name, u.password_hash, u.is_active, u.role_id, r.name AS role_name, "
            "r.is_active AS role_active FROM users u JOIN roles r ON r.id = u.role_id WHERE lower(u.email) = :e"
        ),
        {"e": email},
    ).mappings().first()
    ok = verify_password(password, row["password_hash"] if row else DUMMY_HASH)
    if not row or not ok:
        limiter.fail(key)
        raise AppError(401, "invalid_credentials", "Correo o contraseña incorrectos.")
    if not row["is_active"] or not row["role_active"]:
        raise AppError(403, "user_inactive", "Tu usuario está inactivo. Contacta a un administrador.")
    limiter.clear(key)
    conn.execute(text("UPDATE users SET last_login_at = now() WHERE id = :id"), {"id": row["id"]})
    conn.execute(text("DELETE FROM refresh_tokens WHERE user_id = :u AND expires_at < now()"), {"u": row["id"]})
    refresh, expires = _issue_refresh(conn, row["id"])
    token, ttl = create_access_token(row["id"])
    return {"access_token": token, "expires_in": ttl, "user": _me(conn, row)}, refresh, expires


def refresh(conn: Connection, raw: str | None) -> tuple[dict, str, datetime]:
    invalid = AppError(401, "invalid_refresh", "Sesión expirada. Inicia sesión de nuevo.")
    if not raw:
        raise invalid
    row = conn.execute(
        text(
            "SELECT t.id AS token_id, u.id, u.email, u.full_name, u.role_id, r.name AS role_name "
            "FROM refresh_tokens t JOIN users u ON u.id = t.user_id JOIN roles r ON r.id = u.role_id "
            "WHERE t.token_hash = :h AND t.revoked_at IS NULL AND t.expires_at > now() "
            "AND u.is_active AND r.is_active FOR UPDATE OF t"
        ),
        {"h": hash_refresh_token(raw)},
    ).mappings().first()
    if not row:
        raise invalid
    # Rotación: el token usado se revoca y se emite uno nuevo.
    conn.execute(text("UPDATE refresh_tokens SET revoked_at = now() WHERE id = :id"), {"id": row["token_id"]})
    new_raw, expires = _issue_refresh(conn, row["id"])
    token, ttl = create_access_token(row["id"])
    return {"access_token": token, "expires_in": ttl, "user": _me(conn, row)}, new_raw, expires


def logout(conn: Connection, raw: str | None) -> None:
    if raw:
        conn.execute(
            text("UPDATE refresh_tokens SET revoked_at = now() WHERE token_hash = :h AND revoked_at IS NULL"),
            {"h": hash_refresh_token(raw)},
        )
