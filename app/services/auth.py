import logging
import threading
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import Connection, text

from app.config import get_settings
from app.deps import CurrentUser, load_permissions
from app.errors import AppError
from app.security import (
    DUMMY_HASH,
    create_access_token,
    hash_password,
    hash_refresh_token,
    new_refresh_token,
    verify_password,
)

log = logging.getLogger("tickets.auth")
TOO_MANY = AppError(429, "too_many_attempts", "Demasiados intentos fallidos. Inténtalo más tarde.")


class LoginRateLimiter:
    """Intentos fallidos de login en memoria con tres ventanas:

    - IP + correo (fuerza bruta sobre una cuenta),
    - sólo IP (muchas cuentas desde un origen: credential stuffing),
    - sólo correo (una cuenta desde muchas IPs: ataque distribuido).

    Con varias réplicas debe sustituirse por Redis o por reglas del WAF de Cloudflare.
    """

    MAX_KEYS = 50_000

    def __init__(self) -> None:
        self._fails: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _keys(ip: str, email: str) -> list[tuple[str, int]]:
        s = get_settings()
        return [
            (f"ie|{ip}|{email}", s.login_max_attempts),
            (f"i|{ip}", s.login_max_attempts_ip),
            (f"e|{email}", s.login_max_attempts_email),
        ]

    def _recent(self, key: str, now: float) -> list[float]:
        cutoff = now - get_settings().login_window_seconds
        recent = [t for t in self._fails.get(key, ()) if t > cutoff]
        if recent:
            self._fails[key] = recent
        else:
            self._fails.pop(key, None)
        return recent

    def _purge(self, now: float) -> None:
        """Evita que el diccionario crezca sin límite con claves caducadas."""
        if len(self._fails) > self.MAX_KEYS:
            for key in list(self._fails):
                self._recent(key, now)

    def check(self, ip: str, email: str) -> None:
        now = time.monotonic()
        with self._lock:
            for key, limit in self._keys(ip, email):
                if len(self._recent(key, now)) >= limit:
                    log.warning("Login bloqueado por exceso de intentos (%s)", key.split("|")[0])
                    raise TOO_MANY

    def fail(self, ip: str, email: str) -> None:
        now = time.monotonic()
        with self._lock:
            for key, _ in self._keys(ip, email):
                self._fails.setdefault(key, []).append(now)
            self._purge(now)

    def success(self, ip: str, email: str) -> None:
        with self._lock:
            self._fails.pop(f"ie|{ip}|{email}", None)

    def reset(self) -> None:
        with self._lock:
            self._fails.clear()


limiter = LoginRateLimiter()


def _mask(email: str) -> str:
    name, _, domain = email.partition("@")
    return f"{name[:2]}***@{domain}"


def _me(conn: Connection, row) -> dict:
    return {
        "id": row["id"], "email": row["email"], "full_name": row["full_name"], "role_id": row["role_id"],
        "role_name": row["role_name"], "permissions": load_permissions(conn, row["role_id"], row["id"]),
    }


def _issue_refresh(conn: Connection, user_id: int) -> tuple[str, datetime]:
    raw, digest = new_refresh_token()
    expires = datetime.now(UTC) + timedelta(days=get_settings().refresh_token_days)
    conn.execute(
        text("INSERT INTO refresh_tokens (user_id, token_hash, expires_at) VALUES (:u, :h, :e)"),
        {"u": user_id, "h": digest, "e": expires},
    )
    return raw, expires


def _session(conn: Connection, row) -> tuple[dict, str, datetime]:
    refresh_raw, expires = _issue_refresh(conn, row["id"])
    token, ttl = create_access_token(row["id"])
    return {"access_token": token, "expires_in": ttl, "user": _me(conn, row)}, refresh_raw, expires


def login(conn: Connection, email: str, password: str, ip: str) -> tuple[dict, str, datetime]:
    limiter.check(ip, email)
    row = conn.execute(
        text(
            "SELECT u.id, u.email, u.full_name, u.password_hash, u.is_active, u.role_id, r.name AS role_name, "
            "r.is_active AS role_active FROM users u JOIN roles r ON r.id = u.role_id WHERE lower(u.email) = :e"
        ),
        {"e": email},
    ).mappings().first()
    # Se verifica siempre un hash (real o de relleno) para no revelar por tiempo si el correo existe.
    ok = verify_password(password, row["password_hash"] if row else DUMMY_HASH)
    if not row or not ok:
        limiter.fail(ip, email)
        log.info("Login fallido para %s desde %s", _mask(email), ip)
        raise AppError(401, "invalid_credentials", "Correo o contraseña incorrectos.")
    if not row["is_active"] or not row["role_active"]:
        log.info("Login rechazado (usuario inactivo) para %s", _mask(email))
        raise AppError(403, "user_inactive", "Tu usuario está inactivo. Contacta a un administrador.")
    limiter.success(ip, email)
    conn.execute(text("UPDATE users SET last_login_at = now() WHERE id = :id"), {"id": row["id"]})
    conn.execute(text("DELETE FROM refresh_tokens WHERE user_id = :u AND expires_at < now()"), {"u": row["id"]})
    return _session(conn, row)


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
    return _session(conn, row)


def logout(conn: Connection, raw: str | None) -> None:
    if raw:
        conn.execute(
            text("UPDATE refresh_tokens SET revoked_at = now() WHERE token_hash = :h AND revoked_at IS NULL"),
            {"h": hash_refresh_token(raw)},
        )


def set_password(conn: Connection, user_id: int, new_password: str) -> None:
    """Cambia la contraseña e invalida TODAS las sesiones del usuario (refresh revocados y access tokens
    anteriores rechazados por `password_changed_at`)."""
    conn.execute(
        text("UPDATE users SET password_hash = :h, password_changed_at = now(), updated_at = now() WHERE id = :id"),
        {"h": hash_password(new_password), "id": user_id},
    )
    conn.execute(
        text("UPDATE refresh_tokens SET revoked_at = now() WHERE user_id = :u AND revoked_at IS NULL"), {"u": user_id}
    )


def change_own_password(
    conn: Connection, user: CurrentUser, current: str, new: str
) -> tuple[dict, str, datetime]:
    """Cambio de la propia contraseña: exige la actual y devuelve una sesión nueva (las demás se cierran)."""
    stored = conn.execute(text("SELECT password_hash FROM users WHERE id = :id"), {"id": user.id}).scalar_one()
    if not verify_password(current, stored):
        raise AppError(400, "invalid_current_password", "La contraseña actual no es correcta.")
    if current == new:
        raise AppError(400, "same_password", "La nueva contraseña debe ser distinta de la actual.")
    # password_changed_at = now() (inicio de la transacción), así el token nuevo (emitido después) sigue siendo válido.
    set_password(conn, user.id, new)
    row = conn.execute(
        text(
            "SELECT u.id, u.email, u.full_name, u.role_id, r.name AS role_name "
            "FROM users u JOIN roles r ON r.id = u.role_id WHERE u.id = :id"
        ),
        {"id": user.id},
    ).mappings().one()
    log.info("Contraseña cambiada por el propio usuario %s", user.id)
    return _session(conn, row)
