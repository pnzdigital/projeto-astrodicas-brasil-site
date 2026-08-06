import os
import sys
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

TEST_DIR = Path(tempfile.mkdtemp(prefix="astrodicas-api-tests-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DIR / 'test.db'}"
os.environ["SITE_SECRET_KEY"] = "test-only-secret-that-is-at-least-32-bytes"
os.environ["COOKIE_SECURE"] = "0"
os.environ["SITE_ORIGIN"] = "http://testserver"
os.environ["GEOCODING_ENABLED"] = "0"
os.environ["ENV"] = "test"
os.environ["ALLOW_INSECURE_DEV"] = "1"
os.environ["RATE_LIMIT_ENABLED"] = "0"

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import User  # noqa: E402
from app.security import hash_password  # noqa: E402
from app import ratelimit  # noqa: E402


@pytest.fixture(autouse=True)
def clean_database():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    ratelimit.reset_all()
    yield


@pytest.fixture()
def db_session():
    """Sessão direta no banco, para testes que checam regra e não rota HTTP."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture()
def client():
    with TestClient(app) as test_client:
        yield test_client


def create_user(email: str, password: str, name: str = "Cliente Teste", locale: str = "pt-BR") -> User:
    """Cria uma conta direto no banco para testes.

    Substitui o antigo ``POST /api/auth/register``, removido: a única forma
    real de criar conta é a compra (webhook aprovado -> ``fulfill_order``).
    Testes que só precisam de um usuário autenticado não precisam simular o
    fluxo de pagamento inteiro; criar a linha direto no banco é equivalente e
    mais rápido.
    """
    db = SessionLocal()
    try:
        email = email.lower()
        existing = db.query(User).filter(User.email == email).one_or_none()
        if existing:
            return existing
        user = User(email=email, password_hash=hash_password(password), name=name, locale=locale)
        db.add(user)
        db.commit()
        db.refresh(user)
        return user
    finally:
        db.close()


def register(client, email: str, password: str, name: str = "Cliente Teste", locale: str = "pt-BR"):
    """Cria a conta no banco e loga com o `client`, imitando a resposta do
    antigo ``/api/auth/register`` (mesmo cookie de sessão no client)."""
    create_user(email, password, name=name, locale=locale)
    return client.post("/api/auth/login", json={"email": email, "password": password, "locale": locale})
