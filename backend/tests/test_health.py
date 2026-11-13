import os

import pytest

from app.database.session import get_db


def test_liveness(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
    assert r.headers["X-Request-ID"]


def test_readiness_reports_unavailable_when_db_fails(client):
    class BrokenSession:
        def execute(self, *_a, **_k):
            raise RuntimeError("db down")

        def close(self):
            pass

    client.app.dependency_overrides[get_db] = lambda: BrokenSession()
    r = client.get("/health/ready")
    assert r.status_code == 503
    assert r.json()["database"] == "down"


@pytest.mark.db
@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="needs TEST_DATABASE_URL")
def test_readiness_with_real_postgres(client):
    r = client.get("/health/ready")
    assert r.status_code == 200
    assert r.json()["database"] == "up"


def test_unknown_route_uses_framework_404(client):
    assert client.get("/nope").status_code == 404
