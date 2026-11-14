import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

from app.database.session import get_db

os.environ.setdefault("APP_ENV", "test")


@pytest.fixture
def client() -> TestClient:
    from app.main import create_app

    return TestClient(create_app(), raise_server_exceptions=False)


@pytest.fixture(scope="session")
def _db_engine():
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("needs TEST_DATABASE_URL (real PostgreSQL)")
    engine = create_engine(url)
    if not inspect(engine).has_table("products"):
        pytest.fail("Test database has no schema: run `alembic upgrade head` against it first.")
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(_db_engine):
    """A session whose work is rolled back after each test (service commits become savepoints)."""
    connection = _db_engine.connect()
    outer = connection.begin()
    session = Session(
        bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False
    )
    yield session
    session.close()
    outer.rollback()
    connection.close()


@pytest.fixture
def db_client(client, db_session):
    client.app.dependency_overrides[get_db] = lambda: db_session
    yield client
    client.app.dependency_overrides.clear()
