import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.config import set_config


@pytest.fixture(autouse=True)
def inject_config(test_config):
    set_config(test_config)
    yield


@pytest.fixture
def client():
    return TestClient(app)


def test_health_ok(client, seeded_index):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_sessions_returns_list(client, seeded_index):
    resp = client.get("/sessions", params={"from": "2000-01-01T00:00:00Z"})
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    table = data[0]
    assert table["type"] == "table"
    assert len(table["columns"]) == 16
    assert isinstance(table["rows"], list)


def test_sessions_filter_by_user(client, seeded_index):
    resp = client.get("/sessions", params={"from": "2000-01-01T00:00:00Z", "user": "testuser"})
    assert resp.status_code == 200
    table = resp.json()[0]
    usernames = [row[1] for row in table["rows"]]
    assert all("testuser" in (u or "").lower() for u in usernames)


def test_session_events(client, seeded_index):
    resp = client.get("/sessions", params={"from": "2000-01-01T00:00:00Z"})
    sessions = resp.json()[0]["rows"]
    if not sessions:
        pytest.skip("No sessions in test data")
    session_id = sessions[0][0]

    resp2 = client.get(f"/sessions/{session_id}/events")
    assert resp2.status_code == 200
    table = resp2.json()[0]
    assert table["type"] == "table"
    assert len(table["rows"]) >= 1


def test_mac_invalid_returns_400(client, seeded_index):
    resp = client.get("/mac/not-a-mac")
    assert resp.status_code == 400
