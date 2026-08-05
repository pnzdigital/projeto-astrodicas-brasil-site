import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from pwdlib import PasswordHash


SECRET_KEY = os.getenv("SITE_SECRET_KEY", "")
ALGORITHM = "HS256"

if len(SECRET_KEY.encode()) < 32:
    env = os.getenv("ENV", "development")
    allow_insecure = os.getenv("ALLOW_INSECURE_DEV", "0") == "1"
    if env == "production" or not allow_insecure:
        raise RuntimeError("SITE_SECRET_KEY ausente ou curta demais: defina ao menos 32 bytes.")
    SECRET_KEY = SECRET_KEY or "dev-only-insecure-secret-key-32bytes!!"
password_hash = PasswordHash.recommended()


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    return password_hash.verify(password, hashed)


# Janela curta o bastante para limitar o estrago de um cookie vazado, longa o
# bastante para quem entra uma vez por semana não precisar logar de novo. O
# ``REFRESH_AFTER_SECONDS`` renova a sessão de quem está usando o site, então na
# prática o usuário ativo nunca cai.
SESSION_TTL_DAYS = 7
SESSION_TTL_SECONDS = SESSION_TTL_DAYS * 24 * 60 * 60
REFRESH_AFTER_SECONDS = 24 * 60 * 60


def create_token(user_id: str, token_epoch: int = 0) -> str:
    issued_at = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": user_id,
            "iat": issued_at,
            "exp": issued_at + timedelta(seconds=SESSION_TTL_SECONDS),
            "ep": token_epoch,
        },
        SECRET_KEY,
        algorithm=ALGORITHM,
    )


def decode_token(token: str) -> dict | None:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return {
            "user_id": str(payload["sub"]),
            "epoch": int(payload.get("ep", 0)),
            "issued_at": int(payload.get("iat", 0)),
        }
    except (jwt.PyJWTError, KeyError, TypeError, ValueError):
        return None


def should_refresh(issued_at: int, now: int | None = None) -> bool:
    """Renova só quando o token já envelheceu, para não reescrever o cookie a
    cada request."""
    if not issued_at:
        return True
    now = now if now is not None else int(datetime.now(timezone.utc).timestamp())
    return now - issued_at >= REFRESH_AFTER_SECONDS


def hash_reset_token(raw_token: str) -> str:
    """SHA-256 hex do token bruto. Nunca guardamos o plaintext."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def new_reset_token() -> tuple[str, str]:
    """Gera um par (raw, hash). O ``raw`` vai no link do e-mail; o ``hash``
    é o que persiste no banco."""
    raw = secrets.token_urlsafe(32)
    return raw, hash_reset_token(raw)
