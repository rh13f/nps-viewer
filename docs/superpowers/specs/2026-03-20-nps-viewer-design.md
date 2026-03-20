# NPS Viewer — Design Spec

**Date:** 2026-03-20
**Version:** 1.2
**Status:** Approved

---

## Overview

A RADIUS/NPS log viewer providing human-readable, searchable views of Windows Network Policy Server (NPS) accounting events. Modelled after ISA Log Viewer, but built on the user's existing Graylog/OpenSearch infrastructure with Grafana as the frontend and a small Python middleware API for session correlation and computed fields.

---

## Architecture

```
NPS01 (Windows)
  └─► Winlogbeat/Sidecar ──► Graylog ──► OpenSearch (already in place)
                                               │
                               ┌───────────────┘
                               ▼
                        nps-api (Python/FastAPI)
                          - Queries OpenSearch directly (port 9200)
                          - Correlates Start/Stop/Interim events by Acct-Session-Id
                          - Computes: session duration, data in/out totals
                          - Translates RADIUS reason codes to human-readable text
                          - Exposes JSON endpoints consumed by Grafana
                               │
                               ▼
                        Grafana OSS 10.x+
                          - marcusolsson-json-datasource plugin → nps-api
                          - OpenSearch datasource → live tail panel (direct)
                          - 5 purpose-built dashboards
```

- nps-api runs as a **systemd service** on the Graylog server, listening on `localhost:8000`
- Grafana is installed on the same server; dashboards are **provisioned via JSON files** on startup
- Both components are configured via `/etc/nps-api/config.yaml`
- An **install script** handles all setup for portability across Graylog servers

---

## Data Model

NPS events arrive in OpenSearch with fields prefixed `winlog_event_data_`. The following fields are consumed:

| Field | Meaning |
|---|---|
| `winlog_event_data_User-Name` | Authenticating username |
| `winlog_event_data_Acct-Status-Type` | 1=Start, 2=Stop, 3=Interim-Update |
| `winlog_event_data_Acct-Session-Id` | Session correlation key |
| `winlog_event_data_Calling-Station-Id` | Client MAC address |
| `winlog_event_data_NAS-IP-Address` | WAP/NAS IP address |
| `winlog_event_data_Client-Friendly-Name` | WAP friendly name |
| `winlog_event_data_Framed-IP-Address` | IP assigned to client |
| `winlog_event_data_Connect-Info` | Speed / 802.11 standard / RSSI / Channel |
| `winlog_event_data_Reason-Code` | 0=success, non-zero=failure |
| `winlog_event_data_Acct-Input-Octets` | Bytes uploaded by client |
| `winlog_event_data_Acct-Output-Octets` | Bytes downloaded by client |
| `winlog_event_data_Acct-Session-Time` | Session duration (seconds, on Stop/Interim) |
| `timestamp` | Event timestamp (configurable, see config.yaml) |

> All field names are configurable in `config.yaml` to accommodate variations across Graylog servers.

---

## nps-api

### Technology
- Python 3.11+, FastAPI, opensearch-py, uvicorn
- Runs as a systemd service: `nps-api.service`
- Config: `/etc/nps-api/config.yaml`
- Tests: pytest

### Configuration (`config.yaml`)
```yaml
opensearch:
  host: localhost
  port: 9200
  use_ssl: false
  verify_certs: false
  ca_certs: ""          # path to CA bundle if use_ssl=true
  username: ""          # optional basic auth
  password: ""          # optional basic auth
  index: "graylog_*"   # index pattern

session:
  active_threshold_minutes: 30   # sessions with last event older than this and no Stop are marked "unknown"

fields:
  timestamp: "timestamp"          # top-level timestamp field name
  prefix: "winlog_event_data_"    # field name prefix
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

### Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/sessions` | Correlated sessions with computed fields |
| GET | `/sessions/{session_id}/events` | All raw events for a single session |
| GET | `/failures` | Failed auth events with reason descriptions |
| GET | `/reason-codes` | Distinct reason codes present in the data (for Grafana variable) |
| GET | `/mac/{mac}` | All sessions for a given MAC address |
| GET | `/live` | Last N raw events (pass-through; for non-Grafana clients) |
| GET | `/aps` | Per-AP connection counts and stats |
| GET | `/health` | Health check |

> Dashboard 2 (Live Tail) queries OpenSearch directly via the Grafana OpenSearch datasource rather than using `/live`, for lower latency. The `/live` endpoint exists for other consumers (CLI tools, scripts, future integrations).

#### `/sessions` params
- `user` — username filter (partial match, case-insensitive)
- `from` — ISO8601 start time (default: -24h)
- `to` — ISO8601 end time (default: now)
- `limit` — max sessions returned (default: 500, max: 2000)
- `offset` — pagination offset (default: 0)

**Pagination semantics:** The raw event fetch cap is `10 × (offset + limit)`, max 10,000 raw events. The response envelope includes a `total` field (total correlated sessions found within the cap), allowing callers to determine whether further pages exist. This is best-effort: if the raw event cap is reached before all sessions are correlated, `total` reflects only what was retrieved.

**Response envelope:**
```json
{
  "total": 142,
  "offset": 0,
  "limit": 500,
  "sessions": [ ... ]
}
```

**Response per session:**
```json
{
  "session_id": "CE103B91FE473C8E",
  "username": "katherinegrech@livingstonesa.org",
  "mac": "28-92-00-DA-05-CA",
  "ap_name": "Meraki-WAP",
  "ap_ip": "10.66.24.134",
  "client_ip": "10.66.20.11",
  "start_time": "2026-02-21T19:01:56Z",
  "end_time": "2026-02-22T00:01:56Z",
  "duration_seconds": 18000,
  "data_in_bytes": 36211878,
  "data_out_bytes": 276789201,
  "connect_info": "CONNECT 54.00 Mbps / 802.11ax / RSSI: 35 / Channel: 40",
  "status": "closed"
}
```

**Session status values:**
- `closed` — Stop event received
- `active` — no Stop event; last Interim event is within `active_threshold_minutes`
- `unknown` — no Stop event; last event is older than `active_threshold_minutes`

#### `/failures` params
- `user` — username filter (partial match, case-insensitive, optional)
- `from`, `to` — time range
- `reason` — filter by specific reason code integer (optional)
- `limit` — default 500, max 2000
- `offset` — pagination offset

**Response per failure:**
```json
{
  "timestamp": "2026-02-22T00:20:07Z",
  "username": "jsmith@livingstonesa.org",
  "mac": "A0-B3-39-0C-F2-E2",
  "ap_name": "Meraki-WAP",
  "ap_ip": "10.66.24.110",
  "reason_code": 16,
  "reason_description": "Authentication failed"
}
```

#### `/mac/{mac}` params
- `from`, `to` — time range
- MAC address in path: any common delimiter (hyphens, colons, dots, or none) is accepted; normalised to uppercase hyphens internally (e.g., `28-92-00-DA-05-CA`)

**Response:** list of session objects (same schema as `/sessions`)

#### `/sessions/{session_id}/events`
Returns all raw events for a given session ID. Used by Dashboard 1 Detail — routing this through nps-api keeps field name resolution server-side (no hard-coded OpenSearch field names in dashboard JSON).

**Response per event:**
```json
{
  "timestamp": "2026-02-22T00:01:56Z",
  "status_type": 3,
  "status_label": "Interim-Update",
  "data_in_bytes": 36211878,
  "data_out_bytes": 276789201,
  "session_time_seconds": 128400,
  "rssi": 35,
  "channel": 40,
  "standard": "802.11ax",
  "speed_mbps": 54.0,
  "reason_code": 0,
  "reason_description": "Success"
}
```

#### `/reason-codes`
Returns distinct reason codes currently present in OpenSearch (for Grafana variable/dropdown on Dashboard 3). No parameters.

**Response:**
```json
[
  {"code": 0,  "description": "Success"},
  {"code": 16, "description": "Authentication failed"}
]
```

#### `/live` params
- `limit` — number of events (default: 100, max: 500)

**Response per event:** raw mapped fields (no correlation); same field set as the data model table above.

#### `/aps` params
- `from`, `to` — time range

**Response per AP:**
```json
{
  "ap_name": "Meraki-WAP",
  "ap_ip": "10.66.24.134",
  "total_sessions": 142,
  "unique_users": 38,
  "failure_count": 3,
  "failure_rate_pct": 2.1
}
```

#### `/health` response
```json
// 200 OK — healthy
{"status": "ok", "opensearch": "reachable"}

// 503 Service Unavailable — OpenSearch unreachable
{"status": "error", "opensearch": "unreachable", "detail": "<error message>"}
```

### OpenSearch Query Strategy

Session correlation fetches raw events in two passes:

1. **Count query** — determine total matching event count for the time range and user filter.
2. **Scroll / search_after fetch** — retrieve all matching events using OpenSearch `search_after` pagination (sort by `timestamp` asc, then `_id`). Each page fetches 1000 documents. Pages are fetched until all events are retrieved or the raw event cap (10× the `limit` parameter, max 10,000) is reached.
3. **In-memory grouping** — events are grouped by `Acct-Session-Id` in Python, then correlated into session objects.

This strategy avoids the deprecated scroll API and works well for typical NPS log volumes. For very large time ranges, callers should narrow the time window or use the `limit`/`offset` parameters.

### Session Correlation Logic

Events with the same `Acct-Session-Id` are grouped:
- **Start** event (Acct-Status-Type=1): provides session start time and initial connection info
- **Stop** event (Acct-Status-Type=2): sets end time; provides final data counters and session time; status = `closed`
- **Interim** events (Acct-Status-Type=3): used when no Stop event present; most recent Interim provides data counters
- If no Stop event: status = `active` if most recent event timestamp is within `active_threshold_minutes` of now; otherwise `unknown`
- Data totals: prefer Stop event values; fall back to most recent Interim event values

### Connect-Info Parsing

The `Connect-Info` field is a raw string with the format:
```
CONNECT <speed> Mbps / <standard> / RSSI: <rssi> / Channel: <channel>
```
Example: `CONNECT 54.00 Mbps / 802.11ax / RSSI: 35 / Channel: 40`

Parsing regex (Python):
```python
r"CONNECT\s+([\d.]+)\s+Mbps\s*/\s*([\w.]+)\s*/\s*RSSI:\s*(\d+)\s*/\s*Channel:\s*(\d+)"
```
Extracted fields: `speed_mbps` (float), `standard` (str), `rssi` (int), `channel` (int).

**Fallback:** If the string does not match, all four fields are set to `null` and the raw `connect_info` string is preserved. No exception is raised.

### MAC Address Normalisation

Canonical format: **uppercase, hyphen-delimited** (e.g., `28-92-00-DA-05-CA`).

All MAC addresses are normalised to this format on ingress (both from path parameters and from OpenSearch field values) before any comparison or storage.

### RADIUS Reason Code Map

Full Microsoft NPS reason code reference: https://docs.microsoft.com/en-us/windows-server/networking/technologies/nps/nps-crp-crp-processing

Key codes maintained as a Python dict in `reason_codes.py`. Includes all documented IAS/NPS reason codes. Unknown codes render as `"Unknown reason code <N>"`.

---

## Grafana Datasource & Dashboard Integration

### Grafana Version
Grafana OSS **10.x or later** is required. The install script targets the latest Grafana OSS 10.x available in the official apt/rpm repository.

### JSON Datasource Plugin
Plugin: **`marcusolsson-json-datasource`** (install via `grafana-cli plugins install marcusolsson-json-datasource`).

nps-api endpoints must return data in the format expected by this plugin:

**Table/metrics response format:**
```json
[
  {
    "columns": [
      {"text": "session_id", "type": "string"},
      {"text": "username",   "type": "string"},
      {"text": "start_time", "type": "time"},
      {"text": "duration_seconds", "type": "number"}
    ],
    "rows": [
      ["CE103B91FE473C8E", "katherinegrech@livingstonesa.org", "2026-02-21T19:01:56Z", 18000]
    ],
    "type": "table"
  }
]
```

Each endpoint returns the appropriate column set for its dashboard. Column types: `string`, `number`, `time`.

### Datasources (provisioned via YAML)
1. **NPS-API** — `marcusolsson-json-datasource`, URL: `http://localhost:8000`
2. **NPS-OpenSearch** — OpenSearch datasource, URL: `http://localhost:9200`, index: `graylog_*`, timestamp field: `timestamp`

### JSON Datasource Column Contracts

Each nps-api endpoint returns the following columns for Grafana table panels:

**`/sessions` columns:**
| name | type |
|---|---|
| session_id | string |
| username | string |
| mac | string |
| ap_name | string |
| ap_ip | string |
| client_ip | string |
| start_time | time |
| end_time | time |
| duration_seconds | number |
| data_in_bytes | number |
| data_out_bytes | number |
| speed_mbps | number |
| standard | string |
| rssi | number |
| channel | number |
| status | string |

**`/sessions/{session_id}/events` columns:**
| name | type |
|---|---|
| timestamp | time |
| status_type | number |
| status_label | string |
| data_in_bytes | number |
| data_out_bytes | number |
| session_time_seconds | number |
| rssi | number |
| channel | number |
| standard | string |
| speed_mbps | number |
| reason_code | number |
| reason_description | string |

**`/failures` columns:**
| name | type |
|---|---|
| timestamp | time |
| username | string |
| mac | string |
| ap_name | string |
| ap_ip | string |
| reason_code | number |
| reason_description | string |

**`/mac/{mac}` columns:** same as `/sessions`

**`/aps` columns:**
| name | type |
|---|---|
| ap_name | string |
| ap_ip | string |
| total_sessions | number |
| unique_users | number |
| failure_count | number |
| failure_rate_pct | number |

**`/reason-codes` columns:**
| name | type |
|---|---|
| code | number |
| description | string |

### Dashboard 1: User Session View
- Variable: `$username` (text input)
- Variable: time range
- **Table panel**: one row per session — Start Time, Duration, AP Name, Client IP, MAC, Data In, Data Out, 802.11 Standard, RSSI
- Session drilldown: each row has a **Data Link** to Dashboard 1 Detail (a second dashboard filtered by `session_id`), opening in a new tab
- Datasource: NPS-API `/sessions`

> Row expansion is not natively supported in Grafana table panels. Drilldown to a detail dashboard via Data Links is the implementation approach.

### Dashboard 1 Detail: Session Raw Events
- Variable: `$session_id` (populated from drilldown link)
- Table of all raw events for that session: Timestamp, Status Label, Data In, Data Out, RSSI, Channel, Standard, Reason Description
- Datasource: NPS-API `/sessions/{session_id}/events` (field resolution stays server-side)

### Dashboard 2: Live Tail
- Auto-refresh: 10s (user-adjustable: 5s / 10s / 30s / off)
- **Logs panel** or table: Timestamp, User, MAC, AP, Event Type, Reason Code
- Color coding via value mappings: Start=green, Stop=grey, Interim=blue, Failure (Reason-Code ≠ 0)=red
- Datasource: NPS-OpenSearch (direct, low latency)

### Dashboard 3: Auth Failures
- Variable: time range, reason code filter (dropdown populated from `/failures` distinct reason codes)
- **Time series panel**: failure count over time
- **Table panel**: Timestamp, User, MAC, AP, Reason Code, Reason Description
- Datasource: NPS-API `/failures`

### Dashboard 4: MAC Tracker
- Variable: `$mac` (text input)
- **Table**: sessions for that MAC — Start Time, Username, AP, Duration, Data In/Out, Status
- Shows device use across different users and APs
- Datasource: NPS-API `/mac/{mac}`

### Dashboard 5: AP Summary *(nice-to-have)*
- **Bar chart**: connections per AP over time range
- **Table**: AP Name, IP, Unique Users, Total Sessions, Failure Count, Failure Rate %
- Datasource: NPS-API `/aps`

---

## Installation & Packaging

### Install Script (`install.sh`)
Single bash script that:
1. Detects OS package manager (`apt` for Debian/Ubuntu, `dnf`/`yum` for RHEL/Rocky/CentOS)
2. Checks Python 3.11+ is available (exits with clear message if not)
3. Creates `/etc/nps-api/` and writes default `config.yaml`
4. Creates Python venv at `/opt/nps-api/venv/`, installs dependencies from `requirements.txt`
5. Writes and enables `nps-api.service` systemd unit
6. Installs Grafana OSS 10.x via official apt/rpm repo (skipped if Grafana already installed)
7. Installs `marcusolsson-json-datasource` and OpenSearch datasource plugins
8. Copies dashboard JSON files and provisioning YAML to `/etc/grafana/provisioning/`
9. Restarts `nps-api` and `grafana-server` services
10. Prints post-install summary: API URL, Grafana URL, config file location

### Python Dependencies (minimum versions)

```
fastapi>=0.110
uvicorn>=0.29
opensearch-py>=2.4
pyyaml>=6.0
```

### systemd Unit (`nps-api.service`)

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

The install script creates a dedicated `nps-api` system user (no login shell, no home directory) and sets ownership of `/opt/nps-api` and `/etc/nps-api` accordingly.

### File Layout
```
/opt/nps-api/
  venv/
  app/
    main.py
    config.py
    opensearch_client.py
    session_correlator.py
    mac_utils.py
    reason_codes.py
    routers/
      sessions.py
      failures.py
      mac.py
      live.py
      aps.py
      health.py

/etc/nps-api/
  config.yaml

/etc/grafana/provisioning/
  datasources/nps.yaml
  dashboards/nps.yaml
  dashboards/json/
    nps-user-sessions.json
    nps-session-detail.json
    nps-live-tail.json
    nps-auth-failures.json
    nps-mac-tracker.json
    nps-ap-summary.json

/lib/systemd/system/
  nps-api.service

install.sh
requirements.txt
```

---

## Error Handling

- nps-api returns standard HTTP error codes; Grafana surfaces them as panel errors
- OpenSearch connection failures: logged to systemd journal; `/health` returns 503
- Missing or null fields handled gracefully — sessions with incomplete data are included with null fields rather than dropped
- Field name mismatches produce a clear log warning with a hint to check `config.yaml`
- Invalid MAC address format in `/mac/{mac}` returns HTTP 400 with a descriptive message

> **Note on OpenSearch field types:** Winlogbeat may index `Acct-Status-Type`, `Reason-Code`, and similar numeric fields as strings rather than integers, depending on the NPS/IAS input configuration. The API uses `term` queries (not range queries) for these fields, which work correctly regardless of whether the field is mapped as string or integer. If numeric comparisons are needed in future, the OpenSearch index template can be adjusted.

---

## Testing

- **Unit tests** (`pytest`): session correlator logic (Start/Stop/Interim grouping, data total selection, status determination), reason code translation, MAC normalisation
- **Integration tests** (`pytest`): spin up OpenSearch test container (opensearchproject/opensearch:2), insert sample events from `IN260222.log`, verify API responses match expected output
- **Manual verification**: run against existing `IN260222.log` imported into a test OpenSearch instance

---

## Out of Scope

- Authentication/authorisation for the Grafana/API (assumed on internal network)
- Alerting rules (can be added later in Grafana)
- Historical log file import (logs already in OpenSearch via existing pipeline)
- CI/CD pipeline (tests run manually with `pytest`)
