import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine

from claims_copilot.config import Settings
from claims_copilot.db import get_session
from claims_copilot.main import create_app


@pytest.fixture
def test_app(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path}/test.db", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(engine)
    upload_dir = tmp_path / "uploads"

    def override_session():
        with Session(engine) as session:
            yield session

    app = create_app(init_db_on_startup=False)
    app.dependency_overrides[get_session] = override_session
    return app, engine, upload_dir


@pytest.fixture
def client(test_app, monkeypatch):
    app, _, upload_dir = test_app
    settings = Settings(upload_dir=upload_dir)
    monkeypatch.setattr("claims_copilot.api.claims.get_settings", lambda: settings)
    with TestClient(app) as test_client:
        yield test_client
