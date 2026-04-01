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


def test_failures_returns_table(client, seeded_index):
    resp = client.get("/failures", params={"from": "2000-01-01T00:00:00Z"})
    assert resp.status_code == 200
    data = resp.json()
    table = data[0]
    assert table["type"] == "table"
    for row in table["rows"]:
        reason_code = row[5]
        assert reason_code != 0


def test_reason_codes_returns_list(client, seeded_index):
    resp = client.get("/reason-codes", params={"from": "2000-01-01T00:00:00Z"})
    assert resp.status_code == 200
    table = resp.json()[0]
    assert table["type"] == "table"
    codes = [row[0] for row in table["rows"]]
    assert codes == sorted(codes)


def test_aps_returns_table(client, seeded_index):
    resp = client.get("/aps", params={"from": "2000-01-01T00:00:00Z"})
    assert resp.status_code == 200
    table = resp.json()[0]
    assert table["type"] == "table"
    assert len(table["columns"]) == 6
