import os
import sys
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

TEST_DIR = Path(tempfile.mkdtemp(prefix="astrodicas-api-tests-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DIR / 'test.db'}"
os.environ["SITE_SECRET_KEY"] = "test-only-secret"
os.environ["COOKIE_SECURE"] = "0"
os.environ["SITE_ORIGIN"] = "http://testserver"

from app.db import Base, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(autouse=True)
def clean_database():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


@pytest.fixture()
def client():
    with TestClient(app) as test_client:
        yield test_client
