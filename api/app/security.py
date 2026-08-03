import os
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


def create_token(user_id: str) -> str:
    expires = datetime.now(timezone.utc) + timedelta(days=30)
    return jwt.encode({"sub": user_id, "exp": expires}, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> str | None:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return str(payload["sub"])
    except (jwt.PyJWTError, KeyError, TypeError):
        return None
