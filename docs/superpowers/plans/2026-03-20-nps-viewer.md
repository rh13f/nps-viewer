# NPS Viewer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Python/FastAPI middleware API (nps-api) + Grafana dashboards that provide a human-readable RADIUS/NPS log viewer over an existing Graylog/OpenSearch backend.

**Architecture:** nps-api runs as a systemd service on the Graylog server, querying OpenSearch directly via `search_after` pagination and correlating Start/Stop/Interim RADIUS events by session ID. Grafana uses the `marcusolsson-json-datasource` plugin to query nps-api for computed data, and the OpenSearch datasource directly for the live tail panel.

**Tech Stack:** Python 3.11+, FastAPI, opensearch-py, uvicorn, pytest, Grafana OSS 10.x, marcusolsson-json-datasource plugin

---

## File Map

```
nps-viewer/
  app/
    __init__.py              Empty package marker
    main.py                  FastAPI app factory, router registration
    config.py                Loads config.yaml, exposes Config dataclass
    opensearch_client.py     OpenSearch connection + search_after pagination
    session_correlator.py    Groups raw events → Session objects
    mac_utils.py             MAC address normalisation
    connect_info.py          Connect-Info string parser
    reason_codes.py          RADIUS reason code dict + lookup()
    grafana_response.py      Builds marcusolsson-json-datasource table response
    routers/
      __init__.py
      health.py              GET /health
      sessions.py            GET /sessions, GET /sessions/{id}/events
      failures.py            GET /failures
      reason_codes.py        GET /reason-codes
      mac.py                 GET /mac/{mac}
      live.py                GET /live
      aps.py                 GET /aps
  tests/
    unit/
      __init__.py
      test_mac_utils.py
      test_connect_info.py
      test_reason_codes.py
      test_session_correlator.py
    integration/
      __init__.py
      conftest.py            OpenSearch testcontainer fixture + seed data loader
      test_sessions_api.py
      test_failures_api.py
  grafana/
    provisioning/
      datasources/nps.yaml
      dashboards/nps.yaml
      dashboards/json/
        nps-user-sessions.json
        nps-session-detail.json
        nps-live-tail.json
        nps-auth-failures.json
        nps-mac-tracker.json
        nps-ap-summary.json
  requirements.txt
  requirements-dev.txt
  install.sh
  nps-api.service
  config.yaml.example
```

---

## Task 1: Project Scaffolding

**Files:**
- Create: `app/__init__.py`
- Create: `requirements.txt`
- Create: `requirements-dev.txt`
- Create: `config.yaml.example`
- Create: `app/config.py`

- [ ] **Step 1: Create directory structure**

> All commands assume CWD is `/home/dave/Claude/nps-viewer` for the duration of this plan.

```bash
cd /home/dave/Claude/nps-viewer
git init  # skip if repo already initialised
mkdir -p app/routers tests/unit tests/integration grafana/provisioning/datasources grafana/provisioning/dashboards/json
touch app/__init__.py app/routers/__init__.py tests/__init__.py tests/unit/__init__.py tests/integration/__init__.py
```

- [ ] **Step 2: Create `requirements.txt`**

```
fastapi>=0.110
uvicorn>=0.29
opensearch-py>=2.4
pyyaml>=6.0
```

- [ ] **Step 3: Create `requirements-dev.txt`**

```
pytest>=8.0
pytest-asyncio>=0.23
httpx>=0.27
testcontainers>=4.4,<5
```

- [ ] **Step 4: Create `config.yaml.example`**

```yaml
opensearch:
  host: localhost
  port: 9200
  use_ssl: false
  verify_certs: false
  ca_certs: ""
  username: ""
  password: ""
  index: "graylog_*"

session:
  active_threshold_minutes: 30

fields:
  timestamp: "timestamp"
  prefix: "winlog_event_data_"
  username: "User-Name"
  acct_status_type: "Acct-Status-Type"
  session_id: "Acct-Session-Id"
  calling_station_id: "Calling-Station-Id"
  nas_ip: "NAS-IP-Address"
  nas_name: "Client-Friendly-Name"
  framed_ip: "Framed-IP-Address"
  connect_info: "Connect-Info"
  reason_code: "Reason-Code"
  input_octets: "Acct-Input-Octets"
  output_octets: "Acct-Output-Octets"
  session_time: "Acct-Session-Time"

api:
  host: "127.0.0.1"
  port: 8000
```

- [ ] **Step 5: Create `app/config.py`**

```python
from __future__ import annotations
import os
from dataclasses import dataclass, field
from typing import Optional
import yaml


@dataclass
class OpenSearchConfig:
    host: str = "localhost"
    port: int = 9200
    use_ssl: bool = False
    verify_certs: bool = False
    ca_certs: str = ""
    username: str = ""
    password: str = ""
    index: str = "graylog_*"


@dataclass
class SessionConfig:
    active_threshold_minutes: int = 30


@dataclass
class FieldsConfig:
    timestamp: str = "timestamp"
    prefix: str = "winlog_event_data_"
    username: str = "User-Name"
    acct_status_type: str = "Acct-Status-Type"
    session_id: str = "Acct-Session-Id"
    calling_station_id: str = "Calling-Station-Id"
    nas_ip: str = "NAS-IP-Address"
    nas_name: str = "Client-Friendly-Name"
    framed_ip: str = "Framed-IP-Address"
    connect_info: str = "Connect-Info"
    reason_code: str = "Reason-Code"
    input_octets: str = "Acct-Input-Octets"
    output_octets: str = "Acct-Output-Octets"
    session_time: str = "Acct-Session-Time"

    def prefixed(self, key: str) -> str:
        """Return prefix + field name for the given config key."""
        return self.prefix + getattr(self, key)


@dataclass
class ApiConfig:
    host: str = "127.0.0.1"
    port: int = 8000


@dataclass
class Config:
    opensearch: OpenSearchConfig = field(default_factory=OpenSearchConfig)
    session: SessionConfig = field(default_factory=SessionConfig)
    fields: FieldsConfig = field(default_factory=FieldsConfig)
    api: ApiConfig = field(default_factory=ApiConfig)


def load_config(path: Optional[str] = None) -> Config:
    import logging
    logger = logging.getLogger(__name__)
    path = path or os.environ.get("NPS_CONFIG", "/etc/nps-api/config.yaml")
    try:
        with open(path) as f:
            raw = yaml.safe_load(f) or {}
    except FileNotFoundError:
        raw = {}

    def _build(cls, data):
        if not data:
            return cls()
        known = {k: v for k, v in data.items() if hasattr(cls, k)}
        unknown = [k for k in data if not hasattr(cls, k)]
        if unknown:
            logger.warning(
                "config.yaml: unknown keys in %s section: %s — check for typos",
                cls.__name__, unknown,
            )
        return cls(**known)

    return Config(
        opensearch=_build(OpenSearchConfig, raw.get("opensearch")),
        session=_build(SessionConfig, raw.get("session")),
        fields=_build(FieldsConfig, raw.get("fields")),
        api=_build(ApiConfig, raw.get("api")),
    )


# Module-level singleton — replaced in tests via dependency injection
_config: Optional[Config] = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = load_config()
    return _config


def set_config(cfg: Config) -> None:
    global _config
    _config = cfg
```

- [ ] **Step 6: Verify imports work**

```bash
cd /home/dave/Claude/nps-viewer
python -c "from app.config import load_config, Config; c = Config(); print(c.fields.prefixed('username'))"
```

Expected output: `winlog_event_data_User-Name`

- [ ] **Step 7: Commit**

```bash
git add app/ tests/ grafana/ requirements.txt requirements-dev.txt config.yaml.example
git commit -m "feat: project scaffolding and config module"
```

---

## Task 2: MAC Utils

**Files:**
- Create: `app/mac_utils.py`
- Create: `tests/unit/test_mac_utils.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_mac_utils.py
import pytest
from app.mac_utils import normalise_mac, InvalidMacError


def test_normalise_hyphen():
    assert normalise_mac("28-92-00-DA-05-CA") == "28-92-00-DA-05-CA"

def test_normalise_colon():
    assert normalise_mac("28:92:00:da:05:ca") == "28-92-00-DA-05-CA"

def test_normalise_dots():
    assert normalise_mac("2892.00da.05ca") == "28-92-00-DA-05-CA"

def test_normalise_no_delimiter():
    assert normalise_mac("289200DA05CA") == "28-92-00-DA-05-CA"

def test_normalise_lowercase():
    assert normalise_mac("a0:b3:39:0c:f2:e2") == "A0-B3-39-0C-F2-E2"

def test_invalid_mac_raises():
    with pytest.raises(InvalidMacError):
        normalise_mac("not-a-mac")

def test_invalid_too_short():
    with pytest.raises(InvalidMacError):
        normalise_mac("28:92:00")
```

- [ ] **Step 2: Run tests — expect FAIL**

```bash
pytest tests/unit/test_mac_utils.py -v
```

Expected: `ImportError` or `ModuleNotFoundError`

- [ ] **Step 3: Implement `app/mac_utils.py`**

```python
import re


class InvalidMacError(ValueError):
    pass


_DELIMITERS = re.compile(r"[:\-.]")


def normalise_mac(mac: str) -> str:
    """Normalise a MAC address to uppercase hyphen-delimited format.

    Accepts: hyphens, colons, dots, or no delimiter.
    Raises InvalidMacError if the input cannot be parsed as a MAC address.
    """
    clean = _DELIMITERS.sub("", mac).upper()
    if not re.fullmatch(r"[0-9A-F]{12}", clean):
        raise InvalidMacError(
            f"Invalid MAC address: {mac!r}. "
            "Expected 12 hex digits with optional hyphens, colons, or dots."
        )
    return "-".join(clean[i:i+2] for i in range(0, 12, 2))
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
pytest tests/unit/test_mac_utils.py -v
```

Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add app/mac_utils.py tests/unit/test_mac_utils.py
git commit -m "feat: MAC address normalisation"
```

---

## Task 3: Connect-Info Parser

**Files:**
- Create: `app/connect_info.py`
- Create: `tests/unit/test_connect_info.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_connect_info.py
from app.connect_info import parse_connect_info, ConnectInfoFields


def test_parse_full():
    result = parse_connect_info("CONNECT 54.00 Mbps / 802.11ax / RSSI: 35 / Channel: 40")
    assert result == ConnectInfoFields(
        speed_mbps=54.0, standard="802.11ax", rssi=35, channel=40
    )

def test_parse_ac():
    result = parse_connect_info("CONNECT 54.00 Mbps / 802.11ac / RSSI: 26 / Channel: 153")
    assert result.standard == "802.11ac"
    assert result.channel == 153

def test_parse_zero_speed():
    # Interim/stop events sometimes report 0 speed
    result = parse_connect_info("CONNECT 0.00 Mbps / 802. / RSSI: 0 / Channel: 0")
    assert result.speed_mbps == 0.0
    assert result.rssi == 0

def test_parse_no_match_returns_nulls():
    result = parse_connect_info("unknown format")
    assert result.speed_mbps is None
    assert result.standard is None
    assert result.rssi is None
    assert result.channel is None

def test_parse_none_input():
    result = parse_connect_info(None)
    assert result.speed_mbps is None

def test_parse_empty():
    result = parse_connect_info("")
    assert result.speed_mbps is None
```

- [ ] **Step 2: Run tests — expect FAIL**

```bash
pytest tests/unit/test_connect_info.py -v
```

- [ ] **Step 3: Implement `app/connect_info.py`**

```python
from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Optional


_PATTERN = re.compile(
    r"CONNECT\s+([\d.]+)\s+Mbps\s*/\s*([\w.]+)\s*/\s*RSSI:\s*(\d+)\s*/\s*Channel:\s*(\d+)",
    re.IGNORECASE,
)


@dataclass
class ConnectInfoFields:
    speed_mbps: Optional[float] = None
    standard: Optional[str] = None
    rssi: Optional[int] = None
    channel: Optional[int] = None


def parse_connect_info(value: Optional[str]) -> ConnectInfoFields:
    """Parse a RADIUS Connect-Info string into structured fields.

    Returns ConnectInfoFields with all None values if parsing fails.
    Never raises.
    """
    if not value:
        return ConnectInfoFields()
    m = _PATTERN.match(value.strip())
    if not m:
        return ConnectInfoFields()
    return ConnectInfoFields(
        speed_mbps=float(m.group(1)),
        standard=m.group(2),
        rssi=int(m.group(3)),
        channel=int(m.group(4)),
    )
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
pytest tests/unit/test_connect_info.py -v
```

Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add app/connect_info.py tests/unit/test_connect_info.py
git commit -m "feat: Connect-Info string parser"
```

---

## Task 4: Reason Codes

**Files:**
- Create: `app/reason_codes.py`
- Create: `tests/unit/test_reason_codes.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_reason_codes.py
from app.reason_codes import lookup, REASON_CODES


def test_known_code_zero():
    assert lookup(0) == "Success"

def test_known_code_16():
    assert lookup(16) == "Authentication failed"

def test_unknown_code():
    assert lookup(9999) == "Unknown reason code 9999"

def test_string_code():
    # OpenSearch may index as string
    assert lookup("0") == "Success"
    assert lookup("16") == "Authentication failed"

def test_reason_codes_dict_nonempty():
    assert len(REASON_CODES) >= 20

def test_none_input():
    assert lookup(None) == "Unknown reason code None"
```

- [ ] **Step 2: Run tests — expect FAIL**

```bash
pytest tests/unit/test_reason_codes.py -v
```

- [ ] **Step 3: Implement `app/reason_codes.py`**

```python
"""RADIUS/NPS reason code descriptions.

Reference: https://docs.microsoft.com/en-us/windows-server/networking/technologies/nps/nps-crp-crp-processing
"""
from typing import Union

REASON_CODES: dict[int, str] = {
    0:  "Success",
    1:  "Internal error",
    2:  "Access denied",
    3:  "Malformed request",
    4:  "Global catalog unavailable",
    5:  "Domain unavailable",
    6:  "Server unavailable",
    7:  "No such domain",
    8:  "No such user",
    16: "Authentication failed",
    17: "Challenge response failed",
    18: "Unknown user",
    19: "Domain not available",
    20: "Account disabled",
    21: "Account expired",
    22: "Account locked out",
    23: "Invalid logon hours",
    24: "Account restriction",
    32: "Local policy does not permit the request",
    33: "Password expired",
    34: "Dial-in permission denied",
    35: "Connection request does not match policy",
    36: "NPS does not have permission to dial out",
    48: "Fully qualified user name does not match any policy",
    49: "NAS port type does not match policy",
    50: "Called station ID does not match policy",
    51: "Calling station ID does not match policy",
    52: "Client IP address does not match policy",
    53: "Time of day does not match policy",
    54: "Profile Framed-Protocol attribute value mismatch",
    55: "Profile Service-Type attribute value mismatch",
    64: "EAP-MSCHAP v2 mutual authentication failed",
    65: "EAP negotiation failed",
    66: "Connection request not parsed",
    67: "Inner tunnel method failed",
    68: "Client certificate not trusted",
    69: "Client certificate revoked",
    70: "Client certificate expired",
    71: "Client authentication failed by EAP method",
    72: "Certificate not found",
    73: "Client certificate not mapped to a user account",
    80: "IAS request timeout",
    96: "Proxy request sent to RADIUS server",
    97: "Proxy request not received by RADIUS server",
}


def lookup(code: Union[int, str, None]) -> str:
    """Return human-readable description for a RADIUS reason code."""
    if code is None:
        return "Unknown reason code None"
    try:
        int_code = int(code)
    except (ValueError, TypeError):
        return f"Unknown reason code {code}"
    return REASON_CODES.get(int_code, f"Unknown reason code {int_code}")
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
pytest tests/unit/test_reason_codes.py -v
```

Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add app/reason_codes.py tests/unit/test_reason_codes.py
git commit -m "feat: RADIUS reason code dictionary and lookup"
```

---

## Task 5: Grafana Response Helper

**Files:**
- Create: `app/grafana_response.py`

- [ ] **Step 1: Create `app/grafana_response.py`**

This module builds the table response format expected by `marcusolsson-json-datasource`.

> **Note for implementer:** If the installed `marcusolsson-json-datasource` version does not accept this SimpleJSON-style format, try `simpod-json-datasource` as a drop-in replacement — it uses the identical format and may be a better fit. Verify by checking the plugin docs after install.

```python
"""Build Grafana JSON datasource table responses.

The marcusolsson-json-datasource plugin expects:
[
  {
    "columns": [{"text": "col_name", "type": "string|number|time"}, ...],
    "rows": [[val, val, ...], ...],
    "type": "table"
  }
]
"""
from typing import Any


ColumnDef = dict[str, str]  # {"text": "name", "type": "string|number|time"}
Row = list[Any]


def table_response(columns: list[ColumnDef], rows: list[Row]) -> list[dict]:
    """Return a single-frame Grafana table response."""
    return [{"columns": columns, "rows": rows, "type": "table"}]


def sessions_columns() -> list[ColumnDef]:
    return [
        {"text": "session_id",       "type": "string"},
        {"text": "username",         "type": "string"},
        {"text": "mac",              "type": "string"},
        {"text": "ap_name",          "type": "string"},
        {"text": "ap_ip",            "type": "string"},
        {"text": "client_ip",        "type": "string"},
        {"text": "start_time",       "type": "time"},
        {"text": "end_time",         "type": "time"},
        {"text": "duration_seconds", "type": "number"},
        {"text": "data_in_bytes",    "type": "number"},
        {"text": "data_out_bytes",   "type": "number"},
        {"text": "speed_mbps",       "type": "number"},
        {"text": "standard",         "type": "string"},
        {"text": "rssi",             "type": "number"},
        {"text": "channel",          "type": "number"},
        {"text": "status",           "type": "string"},
    ]


def session_events_columns() -> list[ColumnDef]:
    return [
        {"text": "timestamp",            "type": "time"},
        {"text": "status_type",          "type": "number"},
        {"text": "status_label",         "type": "string"},
        {"text": "data_in_bytes",        "type": "number"},
        {"text": "data_out_bytes",       "type": "number"},
        {"text": "session_time_seconds", "type": "number"},
        {"text": "rssi",                 "type": "number"},
        {"text": "channel",              "type": "number"},
        {"text": "standard",             "type": "string"},
        {"text": "speed_mbps",           "type": "number"},
        {"text": "reason_code",          "type": "number"},
        {"text": "reason_description",   "type": "string"},
    ]


def failures_columns() -> list[ColumnDef]:
    return [
        {"text": "timestamp",          "type": "time"},
        {"text": "username",           "type": "string"},
        {"text": "mac",                "type": "string"},
        {"text": "ap_name",            "type": "string"},
        {"text": "ap_ip",              "type": "string"},
        {"text": "reason_code",        "type": "number"},
        {"text": "reason_description", "type": "string"},
    ]


def aps_columns() -> list[ColumnDef]:
    return [
        {"text": "ap_name",          "type": "string"},
        {"text": "ap_ip",            "type": "string"},
        {"text": "total_sessions",   "type": "number"},
        {"text": "unique_users",     "type": "number"},
        {"text": "failure_count",    "type": "number"},
        {"text": "failure_rate_pct", "type": "number"},
    ]


def reason_codes_columns() -> list[ColumnDef]:
    return [
        {"text": "code",        "type": "number"},
        {"text": "description", "type": "string"},
    ]
```

- [ ] **Step 2: Verify import**

```bash
python -c "from app.grafana_response import table_response, sessions_columns; print('ok')"
```

- [ ] **Step 3: Commit**

```bash
git add app/grafana_response.py
git commit -m "feat: Grafana JSON datasource response helper"
```

---

## Task 6: OpenSearch Client

**Files:**
- Create: `app/opensearch_client.py`

- [ ] **Step 1: Create `app/opensearch_client.py`**

```python
"""OpenSearch client with search_after pagination."""
from __future__ import annotations
import logging
from typing import Any, Generator, Optional

from opensearchpy import OpenSearch, ConnectionError as OSConnectionError

from app.config import Config

logger = logging.getLogger(__name__)


def build_client(cfg: Config) -> OpenSearch:
    """Create an OpenSearch client from config."""
    kwargs: dict[str, Any] = {
        "hosts": [{"host": cfg.opensearch.host, "port": cfg.opensearch.port}],
        "use_ssl": cfg.opensearch.use_ssl,
        "verify_certs": cfg.opensearch.verify_certs,
        "ssl_show_warn": False,
    }
    if cfg.opensearch.ca_certs:
        kwargs["ca_certs"] = cfg.opensearch.ca_certs
    if cfg.opensearch.username:
        kwargs["http_auth"] = (cfg.opensearch.username, cfg.opensearch.password)
    return OpenSearch(**kwargs)


def is_reachable(client: OpenSearch) -> bool:
    """Return True if OpenSearch responds to a ping."""
    try:
        return client.ping()
    except OSConnectionError:
        return False


def search_after_pages(
    client: OpenSearch,
    index: str,
    query: dict,
    sort: list,
    page_size: int = 1000,
    max_hits: int = 10_000,
) -> Generator[list[dict], None, None]:
    """Yield pages of hits using search_after pagination.

    Stops when all results are returned or max_hits is reached.
    """
    fetched = 0
    search_after: Optional[list] = None

    while fetched < max_hits:
        body: dict[str, Any] = {
            "query": query,
            "sort": sort,
            "size": min(page_size, max_hits - fetched),
        }
        if search_after is not None:
            body["search_after"] = search_after

        try:
            resp = client.search(index=index, body=body)
        except Exception as exc:
            logger.error("OpenSearch search failed: %s", exc)
            raise

        hits = resp["hits"]["hits"]
        if not hits:
            break

        yield hits
        fetched += len(hits)

        if len(hits) < page_size:
            break

        search_after = hits[-1]["sort"]


def fetch_all(
    client: OpenSearch,
    index: str,
    query: dict,
    sort: list,
    max_hits: int = 10_000,
) -> list[dict]:
    """Fetch all matching documents up to max_hits."""
    results = []
    for page in search_after_pages(client, index, query, sort, max_hits=max_hits):
        results.extend(page)
    return results


def count_hits(client: OpenSearch, index: str, query: dict) -> int:
    """Return the total hit count for a query (fast, no docs returned)."""
    try:
        resp = client.count(index=index, body={"query": query})
        return resp["count"]
    except Exception as exc:
        logger.error("OpenSearch count failed: %s", exc)
        raise
```

- [ ] **Step 2: Verify import**

```bash
python -c "from app.opensearch_client import build_client; print('ok')"
```

- [ ] **Step 3: Commit**

```bash
git add app/opensearch_client.py
git commit -m "feat: OpenSearch client with search_after pagination"
```

---

## Task 7: Session Correlator

**Files:**
- Create: `app/session_correlator.py`
- Create: `tests/unit/test_session_correlator.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_session_correlator.py
from datetime import datetime, timezone, timedelta
from app.session_correlator import correlate_sessions, Session
from app.config import FieldsConfig


FIELDS = FieldsConfig()
P = FIELDS.prefix  # "winlog_event_data_"
NOW = datetime.now(timezone.utc)


def _event(status_type, session_id="SID1", timestamp=None, **extra):
    ts = timestamp or NOW.isoformat()
    doc = {
        "_source": {
            FIELDS.timestamp: ts,
            P + FIELDS.acct_status_type: str(status_type),
            P + FIELDS.session_id: session_id,
            P + FIELDS.username: "user@test.com",
            P + FIELDS.calling_station_id: "AA-BB-CC-DD-EE-FF",
            P + FIELDS.nas_ip: "10.0.0.1",
            P + FIELDS.nas_name: "AP-01",
            P + FIELDS.framed_ip: "10.1.0.1",
            P + FIELDS.connect_info: "CONNECT 54.00 Mbps / 802.11ax / RSSI: 40 / Channel: 36",
            P + FIELDS.reason_code: "0",
            P + FIELDS.input_octets: "1000",
            P + FIELDS.output_octets: "2000",
            P + FIELDS.session_time: "0",
        }
    }
    doc["_source"].update({P + k: str(v) for k, v in extra.items()})
    return doc


def test_start_stop_produces_closed_session():
    start_ts = (NOW - timedelta(hours=1)).isoformat()
    stop_ts = NOW.isoformat()
    events = [
        _event(1, timestamp=start_ts),
        _event(2, timestamp=stop_ts, **{FIELDS.session_time: "3600",
                                        FIELDS.input_octets: "5000",
                                        FIELDS.output_octets: "10000"}),
    ]
    sessions = correlate_sessions(events, FIELDS, active_threshold_minutes=30)
    assert len(sessions) == 1
    s = sessions[0]
    assert s.status == "closed"
    assert s.duration_seconds == 3600
    assert s.data_in_bytes == 5000
    assert s.data_out_bytes == 10000


def test_start_only_recent_is_active():
    start_ts = (NOW - timedelta(minutes=10)).isoformat()
    events = [_event(1, timestamp=start_ts)]
    sessions = correlate_sessions(events, FIELDS, active_threshold_minutes=30)
    assert len(sessions) == 1
    assert sessions[0].status == "active"


def test_start_only_old_is_unknown():
    start_ts = (NOW - timedelta(hours=2)).isoformat()
    events = [_event(1, timestamp=start_ts)]
    sessions = correlate_sessions(events, FIELDS, active_threshold_minutes=30)
    assert sessions[0].status == "unknown"


def test_interim_data_used_when_no_stop():
    start_ts = (NOW - timedelta(minutes=5)).isoformat()
    interim_ts = (NOW - timedelta(minutes=1)).isoformat()
    events = [
        _event(1, timestamp=start_ts),
        _event(3, timestamp=interim_ts, **{FIELDS.input_octets: "9999",
                                           FIELDS.output_octets: "8888",
                                           FIELDS.session_time: "240"}),
    ]
    sessions = correlate_sessions(events, FIELDS, active_threshold_minutes=30)
    s = sessions[0]
    assert s.data_in_bytes == 9999
    assert s.data_out_bytes == 8888
    assert s.status == "active"


def test_multiple_sessions_separated():
    events = [
        _event(1, session_id="S1"),
        _event(2, session_id="S1"),
        _event(1, session_id="S2"),
    ]
    sessions = correlate_sessions(events, FIELDS, active_threshold_minutes=30)
    assert len(sessions) == 2


def test_mac_normalised():
    events = [_event(1, **{FIELDS.calling_station_id: "aa:bb:cc:dd:ee:ff"})]
    sessions = correlate_sessions(events, FIELDS, active_threshold_minutes=30)
    assert sessions[0].mac == "AA-BB-CC-DD-EE-FF"
```

- [ ] **Step 2: Run tests — expect FAIL**

```bash
pytest tests/unit/test_session_correlator.py -v
```

- [ ] **Step 3: Implement `app/session_correlator.py`**

```python
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional

from app.config import FieldsConfig
from app.connect_info import parse_connect_info
from app.mac_utils import normalise_mac, InvalidMacError
from app.reason_codes import lookup as rc_lookup


STATUS_LABELS = {1: "Start", 2: "Stop", 3: "Interim-Update"}


@dataclass
class RawEvent:
    timestamp: datetime
    status_type: int
    session_id: str
    username: Optional[str]
    mac: Optional[str]
    ap_ip: Optional[str]
    ap_name: Optional[str]
    client_ip: Optional[str]
    connect_info_raw: Optional[str]
    reason_code: Optional[int]
    input_octets: Optional[int]
    output_octets: Optional[int]
    session_time: Optional[int]


@dataclass
class Session:
    session_id: str
    username: Optional[str]
    mac: Optional[str]
    ap_name: Optional[str]
    ap_ip: Optional[str]
    client_ip: Optional[str]
    start_time: Optional[str]
    end_time: Optional[str]
    duration_seconds: Optional[int]
    data_in_bytes: Optional[int]
    data_out_bytes: Optional[int]
    connect_info: Optional[str]
    speed_mbps: Optional[float]
    standard: Optional[str]
    rssi: Optional[int]
    channel: Optional[int]
    status: str


def _parse_int(val) -> Optional[int]:
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def _parse_dt(val: Optional[str]) -> Optional[datetime]:
    if not val:
        return None
    try:
        dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, AttributeError):
        return None


def _extract_event(doc: dict, f: FieldsConfig) -> Optional[RawEvent]:
    src = doc.get("_source", {})
    p = f.prefix

    ts = _parse_dt(src.get(f.timestamp))
    if ts is None:
        return None

    status_type = _parse_int(src.get(p + f.acct_status_type))
    if status_type is None:
        return None

    session_id = src.get(p + f.session_id)
    if not session_id:
        return None

    raw_mac = src.get(p + f.calling_station_id)
    try:
        mac = normalise_mac(raw_mac) if raw_mac else None
    except InvalidMacError:
        mac = raw_mac  # keep as-is if normalisation fails

    return RawEvent(
        timestamp=ts,
        status_type=status_type,
        session_id=session_id,
        username=src.get(p + f.username),
        mac=mac,
        ap_ip=src.get(p + f.nas_ip),
        ap_name=src.get(p + f.nas_name),
        client_ip=src.get(p + f.framed_ip),
        connect_info_raw=src.get(p + f.connect_info),
        reason_code=_parse_int(src.get(p + f.reason_code)),
        input_octets=_parse_int(src.get(p + f.input_octets)),
        output_octets=_parse_int(src.get(p + f.output_octets)),
        session_time=_parse_int(src.get(p + f.session_time)),
    )


def correlate_sessions(
    docs: list[dict],
    fields: FieldsConfig,
    active_threshold_minutes: int = 30,
) -> list[Session]:
    """Group raw OpenSearch documents into correlated Session objects."""
    groups: dict[str, list[RawEvent]] = {}
    for doc in docs:
        event = _extract_event(doc, fields)
        if event is None:
            continue
        groups.setdefault(event.session_id, []).append(event)

    now = datetime.now(timezone.utc)
    threshold = timedelta(minutes=active_threshold_minutes)
    sessions = []

    for session_id, events in groups.items():
        events.sort(key=lambda e: e.timestamp)

        start_event = next((e for e in events if e.status_type == 1), None)
        stop_event = next((e for e in events if e.status_type == 2), None)
        interim_events = [e for e in events if e.status_type == 3]
        last_interim = interim_events[-1] if interim_events else None

        representative = start_event or events[0]
        last_event = events[-1]

        # Determine status
        if stop_event:
            status = "closed"
        elif (now - last_event.timestamp) <= threshold:
            status = "active"
        else:
            status = "unknown"

        # Data counters: prefer stop, fall back to last interim
        data_source = stop_event or last_interim
        data_in = data_source.input_octets if data_source else None
        data_out = data_source.output_octets if data_source else None
        duration = data_source.session_time if data_source else None

        # Connect-Info parsing from representative event
        ci = parse_connect_info(representative.connect_info_raw)

        sessions.append(Session(
            session_id=session_id,
            username=representative.username,
            mac=representative.mac,
            ap_name=representative.ap_name,
            ap_ip=representative.ap_ip,
            client_ip=representative.client_ip,
            start_time=start_event.timestamp.isoformat() if start_event else None,
            end_time=stop_event.timestamp.isoformat() if stop_event else None,
            duration_seconds=duration,
            data_in_bytes=data_in,
            data_out_bytes=data_out,
            connect_info=representative.connect_info_raw,
            speed_mbps=ci.speed_mbps,
            standard=ci.standard,
            rssi=ci.rssi,
            channel=ci.channel,
            status=status,
        ))

    return sessions
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
pytest tests/unit/test_session_correlator.py -v
```

Expected: 6 passed

- [ ] **Step 5: Run all unit tests**

```bash
pytest tests/unit/ -v
```

Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add app/session_correlator.py tests/unit/test_session_correlator.py
git commit -m "feat: RADIUS session correlator"
```

---

## Task 8: Health Router + FastAPI App

**Files:**
- Create: `app/routers/health.py`
- Create: `app/main.py`

- [ ] **Step 1: Create `app/routers/health.py`**

```python
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from app.config import get_config
from app.opensearch_client import build_client, is_reachable

router = APIRouter()


@router.get("/health")
def health():
    cfg = get_config()
    client = build_client(cfg)
    if is_reachable(client):
        return {"status": "ok", "opensearch": "reachable"}
    return JSONResponse(
        status_code=503,
        content={"status": "error", "opensearch": "unreachable",
                 "detail": "Could not connect to OpenSearch. Check config.yaml."},
    )
```

- [ ] **Step 2: Create `app/main.py`**

```python
from fastapi import FastAPI
from app.routers import health, sessions, failures, reason_codes, mac, live, aps

app = FastAPI(title="NPS Viewer API", version="1.0.0")

app.include_router(health.router)
app.include_router(sessions.router)
app.include_router(failures.router)
app.include_router(reason_codes.router)
app.include_router(mac.router)
app.include_router(live.router)
app.include_router(aps.router)
```

- [ ] **Step 3: Create stub routers for the remaining endpoints** (so the app starts)

Create each of these files with a minimal stub:

`app/routers/sessions.py`:
```python
from fastapi import APIRouter
router = APIRouter()

@router.get("/sessions")
def get_sessions():
    return []

@router.get("/sessions/{session_id}/events")
def get_session_events(session_id: str):
    return []
```

`app/routers/failures.py`:
```python
from fastapi import APIRouter
router = APIRouter()

@router.get("/failures")
def get_failures():
    return []
```

`app/routers/reason_codes.py`:
```python
from fastapi import APIRouter
router = APIRouter()

@router.get("/reason-codes")
def get_reason_codes():
    return []
```

`app/routers/mac.py`:
```python
from fastapi import APIRouter
router = APIRouter()

@router.get("/mac/{mac}")
def get_mac_sessions(mac: str):
    return []
```

`app/routers/live.py`:
```python
from fastapi import APIRouter
router = APIRouter()

@router.get("/live")
def get_live():
    return []
```

`app/routers/aps.py`:
```python
from fastapi import APIRouter
router = APIRouter()

@router.get("/aps")
def get_aps():
    return []
```

- [ ] **Step 4: Verify the app starts**

```bash
python -c "from app.main import app; print('app created:', app.title)"
```

Expected: `app created: NPS Viewer API`

- [ ] **Step 5: Commit**

```bash
git add app/main.py app/routers/
git commit -m "feat: FastAPI app skeleton with health endpoint and stub routers"
```

---

## Task 9: Sessions Router (full implementation)

**Files:**
- Modify: `app/routers/sessions.py`

- [ ] **Step 1: Implement `app/routers/sessions.py`**

```python
from __future__ import annotations
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Query, HTTPException, Response

from app.config import get_config
from app.opensearch_client import build_client, fetch_all
from app.session_correlator import correlate_sessions, Session, STATUS_LABELS
from app.grafana_response import table_response, sessions_columns, session_events_columns
from app.reason_codes import lookup as rc_lookup
from app.connect_info import parse_connect_info

router = APIRouter()


def _build_time_range_query(
    from_dt: datetime,
    to_dt: datetime,
    timestamp_field: str,
    extra_must: Optional[list] = None,
) -> dict:
    must: list = [{"range": {timestamp_field: {"gte": from_dt.isoformat(), "lte": to_dt.isoformat()}}}]
    if extra_must:
        must.extend(extra_must)
    return {"bool": {"must": must}}


def _default_times(from_str, to_str):
    now = datetime.now(timezone.utc)
    from_dt = datetime.fromisoformat(from_str.replace("Z", "+00:00")) if from_str else now - timedelta(hours=24)
    to_dt = datetime.fromisoformat(to_str.replace("Z", "+00:00")) if to_str else now
    return from_dt, to_dt


def _session_row(s: Session) -> list:
    return [
        s.session_id, s.username, s.mac, s.ap_name, s.ap_ip, s.client_ip,
        s.start_time, s.end_time, s.duration_seconds,
        s.data_in_bytes, s.data_out_bytes,
        s.speed_mbps, s.standard, s.rssi, s.channel, s.status,
    ]


@router.get("/sessions")
def get_sessions(
    response: Response,
    user: Optional[str] = Query(None),
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = Query(None),
    limit: int = Query(500, ge=1, le=2000),
    offset: int = Query(0, ge=0),
):
    cfg = get_config()
    client = build_client(cfg)
    f = cfg.fields
    from_dt, to_dt = _default_times(from_, to)

    must: list = [{"range": {f.timestamp: {"gte": from_dt.isoformat(), "lte": to_dt.isoformat()}}}]
    if user:
        must.append({"wildcard": {f.prefixed("username"): {"value": f"*{user}*", "case_insensitive": True}}})

    query = {"bool": {"must": must}}
    sort = [{f.timestamp: "asc"}, {"_id": "asc"}]

    raw_cap = min(10 * (offset + limit), 10_000)
    docs = fetch_all(client, cfg.opensearch.index, query, sort, max_hits=raw_cap)

    sessions = correlate_sessions(docs, f, cfg.session.active_threshold_minutes)
    total = len(sessions)
    page = sessions[offset: offset + limit]

    # Pagination metadata in headers (compatible with Grafana table format)
    response.headers["X-NPS-Total"] = str(total)
    response.headers["X-NPS-Offset"] = str(offset)
    response.headers["X-NPS-Limit"] = str(limit)

    rows = [_session_row(s) for s in page]
    return table_response(sessions_columns(), rows)


@router.get("/sessions/{session_id}/events")
def get_session_events(session_id: str):
    cfg = get_config()
    client = build_client(cfg)
    f = cfg.fields

    query = {"term": {f.prefixed("session_id"): session_id}}
    sort = [{f.timestamp: "asc"}, {"_id": "asc"}]
    docs = fetch_all(client, cfg.opensearch.index, query, sort, max_hits=500)

    rows = []
    for doc in docs:
        src = doc.get("_source", {})
        p = f.prefix
        ts = src.get(f.timestamp)
        status_type_raw = src.get(p + f.acct_status_type)
        status_type = int(status_type_raw) if status_type_raw else None
        status_label = STATUS_LABELS.get(status_type, f"Type-{status_type}") if status_type else None

        ci = parse_connect_info(src.get(p + f.connect_info))
        reason_code_raw = src.get(p + f.reason_code)
        reason_code = int(reason_code_raw) if reason_code_raw is not None else None

        rows.append([
            ts,
            status_type,
            status_label,
            int(src.get(p + f.input_octets) or 0),
            int(src.get(p + f.output_octets) or 0),
            int(src.get(p + f.session_time) or 0),
            ci.rssi,
            ci.channel,
            ci.standard,
            ci.speed_mbps,
            reason_code,
            rc_lookup(reason_code),
        ])

    return table_response(session_events_columns(), rows)
```

- [ ] **Step 2: Verify import**

```bash
python -c "from app.routers.sessions import router; print('ok')"
```

- [ ] **Step 3: Commit**

```bash
git add app/routers/sessions.py
git commit -m "feat: sessions and session events endpoints"
```

---

## Task 10: Failures and Reason-Codes Routers

**Files:**
- Modify: `app/routers/failures.py`
- Modify: `app/routers/reason_codes.py`

- [ ] **Step 1: Implement `app/routers/failures.py`**

```python
from __future__ import annotations
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Query

from app.config import get_config
from app.opensearch_client import build_client, fetch_all
from app.grafana_response import table_response, failures_columns
from app.mac_utils import normalise_mac, InvalidMacError
from app.reason_codes import lookup as rc_lookup

router = APIRouter()


def _default_times(from_str, to_str):
    now = datetime.now(timezone.utc)
    from_dt = datetime.fromisoformat(from_str.replace("Z", "+00:00")) if from_str else now - timedelta(hours=24)
    to_dt = datetime.fromisoformat(to_str.replace("Z", "+00:00")) if to_str else now
    return from_dt, to_dt


@router.get("/failures")
def get_failures(
    user: Optional[str] = Query(None),
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = Query(None),
    reason: Optional[int] = Query(None),
    limit: int = Query(500, ge=1, le=2000),
    offset: int = Query(0, ge=0),
):
    cfg = get_config()
    client = build_client(cfg)
    f = cfg.fields
    from_dt, to_dt = _default_times(from_, to)

    must: list = [
        {"range": {f.timestamp: {"gte": from_dt.isoformat(), "lte": to_dt.isoformat()}}},
        # Exclude reason_code=0 (success) by requiring it exists and is not "0"
        {"exists": {"field": f.prefixed("reason_code")}},
    ]
    must_not: list = [
        {"term": {f.prefixed("reason_code"): "0"}},
        {"term": {f.prefixed("reason_code"): 0}},
    ]

    if user:
        must.append({"wildcard": {f.prefixed("username"): {"value": f"*{user}*", "case_insensitive": True}}})
    if reason is not None:
        must.append({"term": {f.prefixed("reason_code"): str(reason)}})

    query = {"bool": {"must": must, "must_not": must_not}}
    sort = [{f.timestamp: "desc"}, {"_id": "asc"}]
    docs = fetch_all(client, cfg.opensearch.index, query, sort, max_hits=min(10 * (offset + limit), 10_000))

    p = f.prefix
    rows = []
    for doc in docs[offset: offset + limit]:
        src = doc.get("_source", {})
        raw_mac = src.get(p + f.calling_station_id)
        try:
            mac = normalise_mac(raw_mac) if raw_mac else None
        except InvalidMacError:
            mac = raw_mac

        reason_code_raw = src.get(p + f.reason_code)
        reason_code = int(reason_code_raw) if reason_code_raw is not None else None

        rows.append([
            src.get(f.timestamp),
            src.get(p + f.username),
            mac,
            src.get(p + f.nas_name),
            src.get(p + f.nas_ip),
            reason_code,
            rc_lookup(reason_code),
        ])

    return table_response(failures_columns(), rows)
```

- [ ] **Step 2: Implement `app/routers/reason_codes.py`**

```python
from fastapi import APIRouter, Query
from datetime import datetime, timezone, timedelta
from typing import Optional

from app.config import get_config
from app.opensearch_client import build_client
from app.grafana_response import table_response, reason_codes_columns
from app.reason_codes import lookup as rc_lookup, REASON_CODES

router = APIRouter()


@router.get("/reason-codes")
def get_reason_codes(
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = Query(None),
):
    """Return distinct reason codes present in OpenSearch + their descriptions."""
    cfg = get_config()
    client = build_client(cfg)
    f = cfg.fields
    now = datetime.now(timezone.utc)
    from_dt = datetime.fromisoformat(from_.replace("Z", "+00:00")) if from_ else now - timedelta(days=7)
    to_dt = datetime.fromisoformat(to.replace("Z", "+00:00")) if to else now

    body = {
        "query": {"range": {f.timestamp: {"gte": from_dt.isoformat(), "lte": to_dt.isoformat()}}},
        "size": 0,
        "aggs": {
            "distinct_codes": {
                "terms": {"field": f.prefixed("reason_code"), "size": 100}
            }
        }
    }
    resp = client.search(index=cfg.opensearch.index, body=body)
    buckets = resp.get("aggregations", {}).get("distinct_codes", {}).get("buckets", [])

    rows = []
    for bucket in buckets:
        code = bucket["key"]
        try:
            int_code = int(code)
        except (ValueError, TypeError):
            continue
        rows.append([int_code, rc_lookup(int_code)])

    rows.sort(key=lambda r: r[0])
    return table_response(reason_codes_columns(), rows)
```

- [ ] **Step 3: Commit**

```bash
git add app/routers/failures.py app/routers/reason_codes.py
git commit -m "feat: failures and reason-codes endpoints"
```

---

## Task 11: MAC, Live, and APs Routers

**Files:**
- Modify: `app/routers/mac.py`
- Modify: `app/routers/live.py`
- Modify: `app/routers/aps.py`

- [ ] **Step 1: Implement `app/routers/mac.py`**

```python
from __future__ import annotations
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Query, HTTPException

from app.config import get_config
from app.opensearch_client import build_client, fetch_all
from app.session_correlator import correlate_sessions
from app.mac_utils import normalise_mac, InvalidMacError
from app.grafana_response import table_response, sessions_columns
from app.routers.sessions import _session_row

router = APIRouter()


@router.get("/mac/{mac}")
def get_mac_sessions(
    mac: str,
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = Query(None),
):
    try:
        canonical_mac = normalise_mac(mac)
    except InvalidMacError as e:
        raise HTTPException(status_code=400, detail=str(e))

    cfg = get_config()
    client = build_client(cfg)
    f = cfg.fields
    now = datetime.now(timezone.utc)
    from_dt = datetime.fromisoformat(from_.replace("Z", "+00:00")) if from_ else now - timedelta(days=30)
    to_dt = datetime.fromisoformat(to.replace("Z", "+00:00")) if to else now

    query = {
        "bool": {
            "must": [
                {"range": {f.timestamp: {"gte": from_dt.isoformat(), "lte": to_dt.isoformat()}}},
                {"term": {f.prefixed("calling_station_id"): canonical_mac}},
            ]
        }
    }
    sort = [{f.timestamp: "asc"}, {"_id": "asc"}]
    docs = fetch_all(client, cfg.opensearch.index, query, sort, max_hits=10_000)
    sessions = correlate_sessions(docs, f, cfg.session.active_threshold_minutes)
    rows = [_session_row(s) for s in sessions]
    return table_response(sessions_columns(), rows)
```

- [ ] **Step 2: Implement `app/routers/live.py`**

```python
from fastapi import APIRouter, Query
from app.config import get_config
from app.opensearch_client import build_client, fetch_all

router = APIRouter()


@router.get("/live")
def get_live(limit: int = Query(100, ge=1, le=500)):
    cfg = get_config()
    client = build_client(cfg)
    f = cfg.fields

    query = {"match_all": {}}
    sort = [{f.timestamp: "desc"}, {"_id": "asc"}]
    docs = fetch_all(client, cfg.opensearch.index, query, sort, max_hits=limit)

    results = []
    for doc in docs:
        src = doc.get("_source", {})
        p = f.prefix
        results.append({
            "timestamp": src.get(f.timestamp),
            "username": src.get(p + f.username),
            "mac": src.get(p + f.calling_station_id),
            "ap_name": src.get(p + f.nas_name),
            "ap_ip": src.get(p + f.nas_ip),
            "client_ip": src.get(p + f.framed_ip),
            "status_type": src.get(p + f.acct_status_type),
            "reason_code": src.get(p + f.reason_code),
            "connect_info": src.get(p + f.connect_info),
        })
    return results
```

- [ ] **Step 3: Implement `app/routers/aps.py`**

```python
from __future__ import annotations
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Query

from app.config import get_config
from app.opensearch_client import build_client
from app.grafana_response import table_response, aps_columns

router = APIRouter()


@router.get("/aps")
def get_aps(
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = Query(None),
):
    cfg = get_config()
    client = build_client(cfg)
    f = cfg.fields
    now = datetime.now(timezone.utc)
    from_dt = datetime.fromisoformat(from_.replace("Z", "+00:00")) if from_ else now - timedelta(hours=24)
    to_dt = datetime.fromisoformat(to.replace("Z", "+00:00")) if to else now

    # Use aggregations to get per-AP stats
    body = {
        "query": {
            "range": {f.timestamp: {"gte": from_dt.isoformat(), "lte": to_dt.isoformat()}}
        },
        "size": 0,
        "aggs": {
            "per_ap": {
                "terms": {"field": f.prefixed("nas_name"), "size": 500},
                "aggs": {
                    "ap_ip": {"terms": {"field": f.prefixed("nas_ip"), "size": 1}},
                    "unique_sessions": {"cardinality": {"field": f.prefixed("session_id")}},
                    "unique_users": {"cardinality": {"field": f.prefixed("username")}},
                    "failures": {
                        "filter": {
                            "bool": {
                                "must": [{"exists": {"field": f.prefixed("reason_code")}}],
                                "must_not": [
                                    {"term": {f.prefixed("reason_code"): "0"}},
                                    {"term": {f.prefixed("reason_code"): 0}},
                                ],
                            }
                        }
                    }
                }
            }
        }
    }
    resp = client.search(index=cfg.opensearch.index, body=body)
    buckets = resp.get("aggregations", {}).get("per_ap", {}).get("buckets", [])

    rows = []
    for bucket in buckets:
        ap_name = bucket["key"]
        ap_ip_buckets = bucket.get("ap_ip", {}).get("buckets", [])
        ap_ip = ap_ip_buckets[0]["key"] if ap_ip_buckets else None
        total_sessions = bucket.get("unique_sessions", {}).get("value", 0)
        unique_users = bucket.get("unique_users", {}).get("value", 0)
        failure_count = bucket.get("failures", {}).get("doc_count", 0)
        total_events = bucket.get("doc_count", 0)
        # failure_rate_pct = failures as % of total events for this AP
        failure_rate = round(failure_count / total_events * 100, 1) if total_events > 0 else 0.0
        rows.append([ap_name, ap_ip, total_sessions, unique_users, failure_count, failure_rate])

    rows.sort(key=lambda r: r[2], reverse=True)
    return table_response(aps_columns(), rows)
```

- [ ] **Step 4: Commit**

```bash
git add app/routers/mac.py app/routers/live.py app/routers/aps.py
git commit -m "feat: MAC tracker, live, and AP summary endpoints"
```

---

## Task 12: Integration Tests

**Files:**
- Create: `tests/integration/conftest.py`
- Create: `tests/integration/test_sessions_api.py`
- Create: `tests/integration/test_failures_api.py`

> Integration tests require Docker running (`docker info` should succeed). They spin up an OpenSearch 2.x container and seed it with sample events extracted from `IN260222.log`.
> **Note on log format:** `IN260222.log` uses one `<Event>...</Event>` XML element per line (confirmed by inspection). The parser below handles this correctly. If the file is absent or yields no parseable lines, synthetic events are used as a fallback.

- [ ] **Step 1: Create `tests/integration/conftest.py`**

```python
"""Integration test fixtures — real OpenSearch container."""
from __future__ import annotations
import json
import re
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
        # Format: "02/22/2026 00:01:56.701" -> ISO8601
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
    time.sleep(3)  # brief wait for port to be ready
    yield container
    container.stop()


@pytest.fixture(scope="session")
def os_client(opensearch_container):
    port = opensearch_container.get_exposed_port(9200)
    client = OpenSearch(hosts=[{"host": "localhost", "port": int(port)}])
    # Wait for green/yellow status
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
        pass  # handled by fallback below

    if not docs:
        # Fallback: use synthetic events if log absent or yielded nothing
        docs = _synthetic_events()

    if not os_client.indices.exists(index=INDEX):
        os_client.indices.create(index=INDEX)

    for i, doc in enumerate(docs[:500]):  # limit for test speed
        os_client.index(index=INDEX, id=str(i), body=doc)

    os_client.indices.refresh(index=INDEX)
    return INDEX


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
```

- [ ] **Step 2: Create `tests/integration/test_sessions_api.py`**

```python
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
    # First item is the table response
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
    # First get a session ID
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
```

- [ ] **Step 3: Create `tests/integration/test_failures_api.py`**

```python
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
    # All rows should have non-zero reason codes
    for row in table["rows"]:
        reason_code = row[5]
        assert reason_code != 0


def test_reason_codes_returns_list(client, seeded_index):
    resp = client.get("/reason-codes", params={"from": "2000-01-01T00:00:00Z"})
    assert resp.status_code == 200
    table = resp.json()[0]
    assert table["type"] == "table"
    codes = [row[0] for row in table["rows"]]
    # Code 0 should not appear in reason-codes (it's success)
    # Codes should be sorted
    assert codes == sorted(codes)


def test_aps_returns_table(client, seeded_index):
    resp = client.get("/aps", params={"from": "2000-01-01T00:00:00Z"})
    assert resp.status_code == 200
    table = resp.json()[0]
    assert table["type"] == "table"
    assert len(table["columns"]) == 6
```

- [ ] **Step 4: Run unit tests first (fast)**

```bash
pytest tests/unit/ -v
```

Expected: all pass

- [ ] **Step 5: Run integration tests (requires Docker)**

```bash
pytest tests/integration/ -v --tb=short
```

Expected: all pass. If the OpenSearch container takes time to start, wait and retry.

- [ ] **Step 6: Commit**

```bash
git add tests/integration/
git commit -m "test: integration tests with OpenSearch testcontainer"
```

---

## Task 13: Grafana Provisioning Files

**Files:**
- Create: `grafana/provisioning/datasources/nps.yaml`
- Create: `grafana/provisioning/dashboards/nps.yaml`

- [ ] **Step 1: Create datasource provisioning `grafana/provisioning/datasources/nps.yaml`**

> **Important:** `uid` values must be set explicitly so dashboard JSON files can reference them by a stable name. The dashboard JSON uses `${DS_NPS-API}` and `${DS_NPS-OPENSEARCH}` as datasource UID placeholders — these resolve correctly in Grafana 10.x provisioning when the UIDs below match.

```yaml
apiVersion: 1

datasources:
  - name: NPS-API
    uid: nps-api
    type: marcusolsson-json-datasource
    url: http://localhost:8000
    access: proxy
    isDefault: false
    editable: false

  - name: NPS-OpenSearch
    uid: nps-opensearch
    type: grafana-opensearch-datasource
    url: http://localhost:9200
    access: proxy
    isDefault: false
    editable: false
    jsonData:
      index: "graylog_*"
      timeField: "timestamp"
      version: "2.0.0"
      flavor: "opensearch"
      logMessageField: "winlog_event_data_User-Name"
      logLevelField: "winlog_event_data_Acct-Status-Type"
```

- [ ] **Step 2: Create dashboard provisioning `grafana/provisioning/dashboards/nps.yaml`**

```yaml
apiVersion: 1

providers:
  - name: NPS Dashboards
    folder: NPS Viewer
    type: file
    disableDeletion: false
    updateIntervalSeconds: 30
    options:
      path: /etc/grafana/provisioning/dashboards/json
```

- [ ] **Step 3: Commit**

```bash
git add grafana/provisioning/
git commit -m "feat: Grafana provisioning YAML files"
```

---

## Task 14: Grafana Dashboard JSONs

**Files:**
- Create: `grafana/provisioning/dashboards/json/nps-user-sessions.json`
- Create: `grafana/provisioning/dashboards/json/nps-session-detail.json`
- Create: `grafana/provisioning/dashboards/json/nps-live-tail.json`
- Create: `grafana/provisioning/dashboards/json/nps-auth-failures.json`
- Create: `grafana/provisioning/dashboards/json/nps-mac-tracker.json`
- Create: `grafana/provisioning/dashboards/json/nps-ap-summary.json`

> Dashboard JSON files are Grafana model JSON. The structure below is complete and valid for Grafana 10.x. Panels use `marcusolsson-json-datasource` for nps-api endpoints, and `grafana-opensearch-datasource` for the live tail.

- [ ] **Step 1: Create `grafana/provisioning/dashboards/json/nps-user-sessions.json`**

```json
{
  "title": "NPS - User Sessions",
  "uid": "nps-user-sessions",
  "tags": ["nps", "radius"],
  "timezone": "browser",
  "refresh": "",
  "time": {"from": "now-24h", "to": "now"},
  "templating": {
    "list": [
      {
        "name": "username",
        "label": "Username",
        "type": "textbox",
        "current": {"value": ""},
        "hide": 0
      }
    ]
  },
  "panels": [
    {
      "id": 1,
      "title": "Sessions",
      "type": "table",
      "gridPos": {"h": 20, "w": 24, "x": 0, "y": 0},
      "datasource": {"type": "marcusolsson-json-datasource", "uid": "nps-api"},
      "targets": [
        {
          "refId": "A",
          "method": "GET",
          "path": "/sessions",
          "params": {
            "user": "${username}",
            "from": "${__from:date:iso}",
            "to": "${__to:date:iso}",
            "limit": "500"
          }
        }
      ],
      "options": {
        "sortBy": [{"displayName": "start_time", "desc": true}]
      },
      "fieldConfig": {
        "overrides": [
          {
            "matcher": {"id": "byName", "options": "data_in_bytes"},
            "properties": [{"id": "unit", "value": "bytes"}, {"id": "displayName", "value": "Data In"}]
          },
          {
            "matcher": {"id": "byName", "options": "data_out_bytes"},
            "properties": [{"id": "unit", "value": "bytes"}, {"id": "displayName", "value": "Data Out"}]
          },
          {
            "matcher": {"id": "byName", "options": "duration_seconds"},
            "properties": [{"id": "unit", "value": "s"}, {"id": "displayName", "value": "Duration"}]
          },
          {
            "matcher": {"id": "byName", "options": "session_id"},
            "properties": [
              {
                "id": "links",
                "value": [
                  {
                    "title": "View raw events",
                    "url": "/d/nps-session-detail?var-session_id=${__value.raw}&${__url_time_range}",
                    "targetBlank": true
                  }
                ]
              }
            ]
          },
          {
            "matcher": {"id": "byName", "options": "status"},
            "properties": [
              {
                "id": "mappings",
                "value": [
                  {"type": "value", "options": {"closed":   {"text": "Closed",  "color": "blue"}}},
                  {"type": "value", "options": {"active":   {"text": "Active",  "color": "green"}}},
                  {"type": "value", "options": {"unknown":  {"text": "Unknown", "color": "orange"}}}
                ]
              }
            ]
          }
        ]
      }
    }
  ],
  "schemaVersion": 38
}
```

- [ ] **Step 2: Create `grafana/provisioning/dashboards/json/nps-session-detail.json`**

```json
{
  "title": "NPS - Session Detail",
  "uid": "nps-session-detail",
  "tags": ["nps", "radius"],
  "timezone": "browser",
  "time": {"from": "now-24h", "to": "now"},
  "templating": {
    "list": [
      {
        "name": "session_id",
        "label": "Session ID",
        "type": "textbox",
        "current": {"value": ""},
        "hide": 0
      }
    ]
  },
  "panels": [
    {
      "id": 1,
      "title": "Raw Events for Session ${session_id}",
      "type": "table",
      "gridPos": {"h": 16, "w": 24, "x": 0, "y": 0},
      "datasource": {"type": "marcusolsson-json-datasource", "uid": "nps-api"},
      "targets": [
        {
          "refId": "A",
          "method": "GET",
          "path": "/sessions/${session_id}/events"
        }
      ],
      "fieldConfig": {
        "overrides": [
          {
            "matcher": {"id": "byName", "options": "data_in_bytes"},
            "properties": [{"id": "unit", "value": "bytes"}, {"id": "displayName", "value": "Data In"}]
          },
          {
            "matcher": {"id": "byName", "options": "data_out_bytes"},
            "properties": [{"id": "unit", "value": "bytes"}, {"id": "displayName", "value": "Data Out"}]
          },
          {
            "matcher": {"id": "byName", "options": "session_time_seconds"},
            "properties": [{"id": "unit", "value": "s"}, {"id": "displayName", "value": "Session Time"}]
          },
          {
            "matcher": {"id": "byName", "options": "status_label"},
            "properties": [
              {
                "id": "mappings",
                "value": [
                  {"type": "value", "options": {"Start":          {"color": "green"}}},
                  {"type": "value", "options": {"Stop":           {"color": "text"}}},
                  {"type": "value", "options": {"Interim-Update": {"color": "blue"}}}
                ]
              }
            ]
          }
        ]
      }
    }
  ],
  "schemaVersion": 38
}
```

- [ ] **Step 3: Create `grafana/provisioning/dashboards/json/nps-live-tail.json`**

```json
{
  "title": "NPS - Live Tail",
  "uid": "nps-live-tail",
  "tags": ["nps", "radius", "live"],
  "timezone": "browser",
  "refresh": "10s",
  "time": {"from": "now-5m", "to": "now"},
  "panels": [
    {
      "id": 1,
      "title": "Live RADIUS Events",
      "type": "logs",
      "gridPos": {"h": 22, "w": 24, "x": 0, "y": 0},
      "datasource": {"type": "grafana-opensearch-datasource", "uid": "nps-opensearch"},
      "options": {
        "showTime": true,
        "showLabels": true,
        "showCommonLabels": false,
        "wrapLogMessage": false,
        "prettifyLogMessage": false,
        "enableLogDetails": true,
        "sortOrder": "Descending",
        "dedupStrategy": "none"
      },
      "targets": [
        {
          "refId": "A",
          "query": "*",
          "queryType": "lucene",
          "metrics": [{"type": "logs", "id": "1"}],
          "timeField": "timestamp"
        }
      ]
    }
  ],
  "schemaVersion": 38
}
```

- [ ] **Step 4: Create `grafana/provisioning/dashboards/json/nps-auth-failures.json`**

```json
{
  "title": "NPS - Auth Failures",
  "uid": "nps-auth-failures",
  "tags": ["nps", "radius", "failures"],
  "timezone": "browser",
  "refresh": "",
  "time": {"from": "now-24h", "to": "now"},
  "templating": {
    "list": [
      {
        "name": "reason_code",
        "label": "Reason Code",
        "type": "query",
        "datasource": {"type": "marcusolsson-json-datasource", "uid": "nps-api"},
        "query": "/reason-codes",
        "current": {"value": ""},
        "includeAll": true,
        "allValue": ""
      }
    ]
  },
  "panels": [
    {
      "id": 1,
      "title": "Failures Over Time",
      "type": "timeseries",
      "gridPos": {"h": 8, "w": 24, "x": 0, "y": 0},
      "datasource": {"type": "grafana-opensearch-datasource", "uid": "nps-opensearch"},
      "targets": [
        {
          "refId": "A",
          "query": "winlog_event_data_Reason-Code:(NOT 0)",
          "queryType": "lucene",
          "timeField": "timestamp",
          "metrics": [{"type": "count", "id": "1"}],
          "bucketAggs": [{"type": "date_histogram", "id": "2", "field": "timestamp", "settings": {"interval": "auto"}}]
        }
      ]
    },
    {
      "id": 2,
      "title": "Failure Events",
      "type": "table",
      "gridPos": {"h": 14, "w": 24, "x": 0, "y": 8},
      "datasource": {"type": "marcusolsson-json-datasource", "uid": "nps-api"},
      "targets": [
        {
          "refId": "A",
          "method": "GET",
          "path": "/failures",
          "params": {
            "from": "${__from:date:iso}",
            "to": "${__to:date:iso}",
            "reason": "${reason_code}",
            "limit": "500"
          }
        }
      ],
      "fieldConfig": {
        "overrides": [
          {
            "matcher": {"id": "byName", "options": "reason_description"},
            "properties": [{"id": "displayName", "value": "Reason"}]
          }
        ]
      }
    }
  ],
  "schemaVersion": 38
}
```

- [ ] **Step 5: Create `grafana/provisioning/dashboards/json/nps-mac-tracker.json`**

```json
{
  "title": "NPS - MAC Tracker",
  "uid": "nps-mac-tracker",
  "tags": ["nps", "radius", "mac"],
  "timezone": "browser",
  "refresh": "",
  "time": {"from": "now-30d", "to": "now"},
  "templating": {
    "list": [
      {
        "name": "mac",
        "label": "MAC Address",
        "type": "textbox",
        "current": {"value": ""},
        "hide": 0
      }
    ]
  },
  "panels": [
    {
      "id": 1,
      "title": "Sessions for MAC: ${mac}",
      "type": "table",
      "gridPos": {"h": 20, "w": 24, "x": 0, "y": 0},
      "datasource": {"type": "marcusolsson-json-datasource", "uid": "nps-api"},
      "targets": [
        {
          "refId": "A",
          "method": "GET",
          "path": "/mac/${mac}",
          "params": {
            "from": "${__from:date:iso}",
            "to": "${__to:date:iso}"
          }
        }
      ],
      "fieldConfig": {
        "overrides": [
          {
            "matcher": {"id": "byName", "options": "data_in_bytes"},
            "properties": [{"id": "unit", "value": "bytes"}, {"id": "displayName", "value": "Data In"}]
          },
          {
            "matcher": {"id": "byName", "options": "data_out_bytes"},
            "properties": [{"id": "unit", "value": "bytes"}, {"id": "displayName", "value": "Data Out"}]
          },
          {
            "matcher": {"id": "byName", "options": "duration_seconds"},
            "properties": [{"id": "unit", "value": "s"}, {"id": "displayName", "value": "Duration"}]
          }
        ]
      }
    }
  ],
  "schemaVersion": 38
}
```

- [ ] **Step 6: Create `grafana/provisioning/dashboards/json/nps-ap-summary.json`**

```json
{
  "title": "NPS - AP Summary",
  "uid": "nps-ap-summary",
  "tags": ["nps", "radius", "ap"],
  "timezone": "browser",
  "refresh": "",
  "time": {"from": "now-24h", "to": "now"},
  "panels": [
    {
      "id": 1,
      "title": "Connections per AP",
      "type": "barchart",
      "gridPos": {"h": 8, "w": 24, "x": 0, "y": 0},
      "datasource": {"type": "marcusolsson-json-datasource", "uid": "nps-api"},
      "targets": [
        {
          "refId": "A",
          "method": "GET",
          "path": "/aps",
          "params": {"from": "${__from:date:iso}", "to": "${__to:date:iso}"}
        }
      ],
      "options": {"xField": "ap_name"},
      "fieldConfig": {
        "overrides": [
          {"matcher": {"id": "byName", "options": "total_sessions"}, "properties": [{"id": "displayName", "value": "Total Sessions"}]},
          {"matcher": {"id": "byName", "options": "failure_rate_pct"}, "properties": [{"id": "unit", "value": "percent"}, {"id": "displayName", "value": "Failure Rate"}]}
        ]
      }
    },
    {
      "id": 2,
      "title": "AP Details",
      "type": "table",
      "gridPos": {"h": 12, "w": 24, "x": 0, "y": 8},
      "datasource": {"type": "marcusolsson-json-datasource", "uid": "nps-api"},
      "targets": [
        {
          "refId": "A",
          "method": "GET",
          "path": "/aps",
          "params": {"from": "${__from:date:iso}", "to": "${__to:date:iso}"}
        }
      ],
      "fieldConfig": {
        "overrides": [
          {"matcher": {"id": "byName", "options": "failure_rate_pct"}, "properties": [{"id": "unit", "value": "percent"}]}
        ]
      }
    }
  ],
  "schemaVersion": 38
}
```

- [ ] **Step 7: Commit**

```bash
git add grafana/provisioning/dashboards/json/
git commit -m "feat: Grafana dashboard JSON files"
```

---

## Task 15: systemd Unit File

**Files:**
- Create: `nps-api.service`

- [ ] **Step 1: Create `nps-api.service`**

```ini
[Unit]
Description=NPS API middleware service
After=network.target

[Service]
Type=simple
User=nps-api
WorkingDirectory=/opt/nps-api
ExecStart=/opt/nps-api/venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=on-failure
RestartSec=5
Environment=NPS_CONFIG=/etc/nps-api/config.yaml

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: Commit**

```bash
git add nps-api.service
git commit -m "feat: systemd unit file"
```

---

## Task 16: Install Script

**Files:**
- Create: `install.sh`

- [ ] **Step 1: Create `install.sh`**

```bash
#!/usr/bin/env bash
# NPS Viewer install script
# Supports: Debian/Ubuntu (apt) and RHEL/Rocky/CentOS (dnf/yum)
set -euo pipefail

INSTALL_DIR="/opt/nps-api"
CONFIG_DIR="/etc/nps-api"
SERVICE_USER="nps-api"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== NPS Viewer Install Script ==="

# ── Detect package manager ────────────────────────────────────────────────────
if command -v apt-get &>/dev/null; then
    PKG_MGR="apt"
elif command -v dnf &>/dev/null; then
    PKG_MGR="dnf"
elif command -v yum &>/dev/null; then
    PKG_MGR="yum"
else
    echo "ERROR: Unsupported OS — no apt, dnf, or yum found." >&2
    exit 1
fi
echo "Detected package manager: $PKG_MGR"

# ── Check Python 3.11+ ────────────────────────────────────────────────────────
PYTHON=""
for cmd in python3.12 python3.11 python3; do
    if command -v "$cmd" &>/dev/null; then
        VER=$("$cmd" -c 'import sys; print(sys.version_info[:2])')
        if "$cmd" -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)' 2>/dev/null; then
            PYTHON="$cmd"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    echo "ERROR: Python 3.11+ is required but not found." >&2
    echo "Install it with: $PKG_MGR install python3.11" >&2
    exit 1
fi
echo "Using Python: $PYTHON ($($PYTHON --version))"

# ── Create service user ───────────────────────────────────────────────────────
if ! id "$SERVICE_USER" &>/dev/null; then
    useradd --system --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
    echo "Created system user: $SERVICE_USER"
fi

# ── Install nps-api ───────────────────────────────────────────────────────────
echo "Installing nps-api to $INSTALL_DIR ..."
mkdir -p "$INSTALL_DIR"
cp -r "$SCRIPT_DIR/app" "$INSTALL_DIR/"
cp "$SCRIPT_DIR/requirements.txt" "$INSTALL_DIR/"

$PYTHON -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install --quiet --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install --quiet -r "$INSTALL_DIR/requirements.txt"

chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"

# ── Write config ──────────────────────────────────────────────────────────────
mkdir -p "$CONFIG_DIR"
if [ ! -f "$CONFIG_DIR/config.yaml" ]; then
    cp "$SCRIPT_DIR/config.yaml.example" "$CONFIG_DIR/config.yaml"
    echo "Config written to $CONFIG_DIR/config.yaml — edit before use."
else
    echo "Config already exists at $CONFIG_DIR/config.yaml — not overwritten."
fi
chown -R "$SERVICE_USER:$SERVICE_USER" "$CONFIG_DIR"

# ── Install systemd unit ──────────────────────────────────────────────────────
cp "$SCRIPT_DIR/nps-api.service" /lib/systemd/system/nps-api.service
systemctl daemon-reload
systemctl enable nps-api
systemctl restart nps-api
echo "nps-api service started."

# ── Install Grafana ───────────────────────────────────────────────────────────
if command -v grafana-server &>/dev/null; then
    echo "Grafana already installed — skipping."
else
    echo "Installing Grafana OSS 10.x ..."
    if [ "$PKG_MGR" = "apt" ]; then
        apt-get install -y apt-transport-https software-properties-common wget gnupg
        wget -q -O - https://apt.grafana.com/gpg.key | gpg --dearmor | tee /etc/apt/keyrings/grafana.gpg > /dev/null
        echo "deb [signed-by=/etc/apt/keyrings/grafana.gpg] https://apt.grafana.com stable main" \
            > /etc/apt/sources.list.d/grafana.list
        apt-get update -q
        apt-get install -y grafana
    else
        cat > /etc/yum.repos.d/grafana.repo <<'EOF'
[grafana]
name=grafana
baseurl=https://rpm.grafana.com
repo_gpgcheck=1
enabled=1
gpgcheck=1
gpgkey=https://rpm.grafana.com/gpg.key
sslverify=1
sslcacert=/etc/pki/tls/certs/ca-bundle.crt
EOF
        $PKG_MGR install -y grafana
    fi
fi

# ── Install Grafana plugins ───────────────────────────────────────────────────
echo "Installing Grafana plugins ..."
grafana-cli plugins install marcusolsson-json-datasource || true
grafana-cli plugins install grafana-opensearch-datasource || true

# ── Provision dashboards ──────────────────────────────────────────────────────
echo "Copying Grafana provisioning files ..."
mkdir -p /etc/grafana/provisioning/datasources
mkdir -p /etc/grafana/provisioning/dashboards/json

cp "$SCRIPT_DIR/grafana/provisioning/datasources/nps.yaml" \
    /etc/grafana/provisioning/datasources/nps.yaml
cp "$SCRIPT_DIR/grafana/provisioning/dashboards/nps.yaml" \
    /etc/grafana/provisioning/dashboards/nps.yaml
cp "$SCRIPT_DIR/grafana/provisioning/dashboards/json/"*.json \
    /etc/grafana/provisioning/dashboards/json/

systemctl enable grafana-server
systemctl restart grafana-server
echo "Grafana started."

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "=== Install complete ==="
echo "  nps-api:  http://localhost:8000/health"
echo "  Grafana:  http://$(hostname -I | awk '{print $1}'):3000  (admin/admin — change on first login)"
echo "  Config:   $CONFIG_DIR/config.yaml"
echo ""
echo "Next step: edit $CONFIG_DIR/config.yaml to point at your OpenSearch instance,"
echo "then restart with: systemctl restart nps-api"
```

- [ ] **Step 2: Make executable**

```bash
chmod +x install.sh
```

- [ ] **Step 3: Verify script syntax**

```bash
bash -n install.sh && echo "Syntax OK"
```

Expected: `Syntax OK`

- [ ] **Step 4: Run all tests one final time**

```bash
pytest tests/unit/ -v
```

Expected: all pass

- [ ] **Step 5: Final commit**

```bash
git add install.sh
git commit -m "feat: install script for Debian/Ubuntu and RHEL/Rocky"
```

---

## Task 17: Final Verification

- [ ] **Step 1: Confirm all files exist**

```bash
find app tests grafana -type f | sort
ls requirements.txt requirements-dev.txt install.sh nps-api.service config.yaml.example
```

- [ ] **Step 2: Run full test suite**

```bash
pytest tests/unit/ -v
```

Expected: all unit tests pass

- [ ] **Step 3: Start the API locally and hit health endpoint**

```bash
NPS_CONFIG=/dev/null uvicorn app.main:app --host 127.0.0.1 --port 8000 &
sleep 2
curl -s http://localhost:8000/health | python3 -m json.tool
kill %1
```

Expected: `{"status": "error", "opensearch": "unreachable", ...}` (no OpenSearch locally — that's fine, proves the app starts and responds)

- [ ] **Step 4: Final commit**

```bash
git add -A
git status  # verify no untracked surprises
git commit -m "chore: final review and cleanup" --allow-empty
```
