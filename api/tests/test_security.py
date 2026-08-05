"""Auth secret hardening: SITE_SECRET_KEY must be >=32 bytes, not a placeholder."""

import importlib
import sys
from datetime import datetime, timedelta, timezone

import jwt
import pytest


REASONABLE_SECRET = "x" * 32  # exactly 32 bytes


def _reload_security():
    """Reload app.security so module-level guard re-evaluates."""
    if "app.security" in sys.modules:
        del sys.modules["app.security"]
    return importlib.import_module("app.security")


@pytest.fixture
def strong_secret(monkeypatch):
    monkeypatch.setenv("SITE_SECRET_KEY", REASONABLE_SECRET)
    monkeypatch.delenv("ALLOW_INSECURE_DEV", raising=False)
    return _reload_security()


def test_round_trip_returns_same_user_id(strong_secret):
    user_id = "user-abc-123"
    token = strong_secret.create_token(user_id)
    assert isinstance(token, str) and token
    payload = strong_secret.decode_token(token)
    # ``issued_at`` entrou junto com a sessão de 7 dias: é o que diz quando
    # renovar o cookie de quem está usando o site.
    assert payload["user_id"] == user_id
    assert payload["epoch"] == 0
    assert payload["issued_at"] > 0


def test_decode_rejects_expired_token(strong_secret):
    expired_payload = {
        "sub": "user-x",
        "exp": datetime.now(timezone.utc) - timedelta(seconds=5),
    }
    token = jwt.encode(expired_payload, strong_secret.SECRET_KEY, algorithm=strong_secret.ALGORITHM)
    assert strong_secret.decode_token(token) is None


def test_decode_rejects_tampered_signature(strong_secret):
    token = strong_secret.create_token("user-y")
    head, payload, signature = token.split(".")
    flipped = ("AB" if signature[-2:] != "AB" else "CD")
    tampered = ".".join([head, payload, signature[:-2] + flipped])
    assert strong_secret.decode_token(tampered) is None


def test_import_without_strong_key_raises(monkeypatch):
    monkeypatch.setenv("SITE_SECRET_KEY", "")
    monkeypatch.delenv("ALLOW_INSECURE_DEV", raising=False)
    monkeypatch.setenv("ENV", "development")
    if "app.security" in sys.modules:
        del sys.modules["app.security"]
    with pytest.raises(RuntimeError, match="SITE_SECRET_KEY"):
        importlib.import_module("app.security")