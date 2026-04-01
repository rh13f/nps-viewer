"""Integration test fixtures — real OpenSearch container."""
from __future__ import annotations
import time
from datetime import datetime, timezone
from xml.etree import ElementTree

import pytest
from testcontainers.core.container import DockerContainer
from testcontainers.core.waiting_utils import wait_for_logs
from opensearchpy import OpenSearch

from app.config import Config, OpenSearchConfig, FieldsConfig, SessionConfig, ApiConfig
from app.opensearch_client import build_client

INDEX = "test-nps"
FIELD_PREFIX = "winlog_event_data_"


def _parse_log_line(line: str) -> dict | None:
    """Parse one XML event line from IN260222.log into an OpenSearch doc."""
    line = line.strip()
    if not line:
        return None
    try:
        root = ElementTree.fromstring(line)
    except ElementTree.ParseError:
        return None

    doc = {}
    ts_elem = root.find("Timestamp")
    if ts_elem is not None and ts_elem.text:
        try:
            dt = datetime.strptime(ts_elem.text, "%m/%d/%Y %H:%M:%S.%f")
            doc["timestamp"] = dt.replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            doc["timestamp"] = ts_elem.text

    for child in root:
        tag = child.tag
        if tag == "Timestamp":
            continue
        val = child.text
        if val is not None:
            doc[FIELD_PREFIX + tag] = val

    return doc if doc.get("timestamp") else None


@pytest.fixture(scope="session")
def opensearch_container():
    container = (
        DockerContainer("opensearchproject/opensearch:2")
        .with_env("discovery.type", "single-node")
        .with_env("DISABLE_SECURITY_PLUGIN", "true")
        .with_exposed_ports(9200)
    )
    container.start()
    wait_for_logs(container, "started", timeout=60)
    time.sleep(3)
    yield container
    container.stop()


@pytest.fixture(scope="session")
def os_client(opensearch_container):
    port = opensearch_container.get_exposed_port(9200)
    client = OpenSearch(hosts=[{"host": "localhost", "port": int(port)}])
    for _ in range(30):
        try:
            client.cluster.health(wait_for_status="yellow", timeout="5s")
            break
        except Exception:
            time.sleep(1)
    yield client


@pytest.fixture(scope="session")
def seeded_index(os_client):
    """Seed OpenSearch with events from IN260222.log."""
    import os
    log_path = os.path.join(os.path.dirname(__file__), "../../IN260222.log")
    docs = []
    try:
        with open(log_path) as f:
            for line in f:
                doc = _parse_log_line(line)
                if doc:
                    docs.append(doc)
    except FileNotFoundError:
        pass

    if not docs:
        docs = _synthetic_events()

    if not os_client.indices.exists(index=INDEX):
        os_client.indices.create(index=INDEX, body=_index_mapping())

    for i, doc in enumerate(docs[:500]):
        os_client.index(index=INDEX, id=str(i), body=doc)

    os_client.indices.refresh(index=INDEX)
    return INDEX


def _index_mapping() -> dict:
    """Explicit keyword mappings for all winlog_event_data_ fields.

    In production, Graylog indexes NPS/Winlogbeat fields as keyword (for IDs
    and codes) or text+keyword (for names). Using keyword here lets term queries
    and aggregations work without .keyword suffix, matching the production layout.
    """
    p = FIELD_PREFIX
    kw = {"type": "keyword"}
    return {
        "mappings": {
            "properties": {
                "timestamp": {"type": "date"},
                p + "User-Name":           kw,
                p + "Acct-Status-Type":    kw,
                p + "Acct-Session-Id":     kw,
                p + "Calling-Station-Id":  kw,
                p + "NAS-IP-Address":      kw,
                p + "Client-Friendly-Name": kw,
                p + "Framed-IP-Address":   kw,
                p + "Connect-Info":        kw,
                p + "Reason-Code":         kw,
                p + "Acct-Input-Octets":   kw,
                p + "Acct-Output-Octets":  kw,
                p + "Acct-Session-Time":   kw,
            }
        }
    }


def _synthetic_events():
    """Minimal synthetic events for tests when log file is absent."""
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    p = FIELD_PREFIX
    return [
        {
            "timestamp": (now - timedelta(hours=2)).isoformat(),
            p + "User-Name": "testuser@example.com",
            p + "Acct-Status-Type": "1",
            p + "Acct-Session-Id": "TESTSESS001",
            p + "Calling-Station-Id": "AA-BB-CC-DD-EE-01",
            p + "NAS-IP-Address": "10.0.0.1",
            p + "Client-Friendly-Name": "TestAP",
            p + "Framed-IP-Address": "10.1.0.1",
            p + "Connect-Info": "CONNECT 54.00 Mbps / 802.11ax / RSSI: 40 / Channel: 36",
            p + "Reason-Code": "0",
            p + "Acct-Input-Octets": "0",
            p + "Acct-Output-Octets": "0",
            p + "Acct-Session-Time": "0",
        },
        {
            "timestamp": (now - timedelta(hours=1)).isoformat(),
            p + "User-Name": "testuser@example.com",
            p + "Acct-Status-Type": "2",
            p + "Acct-Session-Id": "TESTSESS001",
            p + "Calling-Station-Id": "AA-BB-CC-DD-EE-01",
            p + "NAS-IP-Address": "10.0.0.1",
            p + "Client-Friendly-Name": "TestAP",
            p + "Framed-IP-Address": "10.1.0.1",
            p + "Connect-Info": "CONNECT 54.00 Mbps / 802.11ax / RSSI: 40 / Channel: 36",
            p + "Reason-Code": "0",
            p + "Acct-Input-Octets": "5000",
            p + "Acct-Output-Octets": "10000",
            p + "Acct-Session-Time": "3600",
        },
        {
            "timestamp": (now - timedelta(minutes=30)).isoformat(),
            p + "User-Name": "failuser@example.com",
            p + "Acct-Status-Type": "1",
            p + "Acct-Session-Id": "TESTSESS002",
            p + "Calling-Station-Id": "AA-BB-CC-DD-EE-02",
            p + "NAS-IP-Address": "10.0.0.2",
            p + "Client-Friendly-Name": "TestAP2",
            p + "Framed-IP-Address": "",
            p + "Connect-Info": "",
            p + "Reason-Code": "16",
            p + "Acct-Input-Octets": "0",
            p + "Acct-Output-Octets": "0",
            p + "Acct-Session-Time": "0",
        },
    ]


@pytest.fixture(scope="session")
def test_config(opensearch_container):
    port = opensearch_container.get_exposed_port(9200)
    return Config(
        opensearch=OpenSearchConfig(host="localhost", port=int(port), index=INDEX),
        fields=FieldsConfig(),
        session=SessionConfig(active_threshold_minutes=30),
        api=ApiConfig(),
    )
