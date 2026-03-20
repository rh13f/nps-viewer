# NPS Viewer — Design Spec

**Date:** 2026-03-20
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
                        Grafana
                          - JSON datasource plugin → nps-api (computed data)
                          - OpenSearch datasource → raw/live tail panels
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
| `timestamp` | Event timestamp |

> Field names are configurable in `config.yaml` to accommodate variations across Graylog servers.

---

## nps-api

### Technology
- Python 3.11+, FastAPI, opensearch-py, uvicorn
- Runs as a systemd service: `nps-api.service`
- Config: `/etc/nps-api/config.yaml`

### Configuration (`config.yaml`)
```yaml
opensearch:
  host: localhost
  port: 9200
  index: "graylog_*"   # or specific index pattern
  username: ""         # optional
  password: ""         # optional

fields:
  prefix: "winlog_event_data_"  # field name prefix
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
| GET | `/failures` | Failed auth events with reason descriptions |
| GET | `/mac/{mac}` | All sessions for a given MAC address |
| GET | `/live` | Last N raw events (pass-through to OpenSearch) |
| GET | `/aps` | Per-AP connection counts and stats |
| GET | `/health` | Health check |

#### `/sessions` params
- `user` — username filter (partial match)
- `from` — ISO8601 start time (default: -24h)
- `to` — ISO8601 end time (default: now)
- `limit` — max results (default: 500)

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

#### `/failures` params
- `from`, `to` — time range
- `reason` — filter by specific reason code (optional)
- `limit` — default 500

#### `/mac/{mac}` params
- `from`, `to` — time range
- MAC address in path (any common delimiter format normalised)

#### `/live` params
- `limit` — number of events (default: 100, max: 500)

#### `/aps` params
- `from`, `to` — time range

### Session Correlation Logic

Events with the same `Acct-Session-Id` are grouped:
- **Start** event (Acct-Status-Type=1): sets session start time
- **Stop** event (Acct-Status-Type=2): sets end time; provides final data counters and session time
- **Interim** events (Acct-Status-Type=3): used to fill data if no Stop event present (session still active)
- Sessions with no Stop event and last Interim > configured threshold are marked `active`; otherwise `unknown`

Data totals prefer the Stop event values; fall back to most recent Interim.

### RADIUS Reason Code Map (partial)
```
0   = Success
16  = Authentication failed
23  = Bad message authenticator
65  = EAP negotiation failed
66  = Connection request not parsed
```
Full map is maintained as a Python dict in the codebase.

---

## Grafana Dashboards

### Datasources
1. **NPS-API** — Grafana JSON datasource plugin, URL: `http://localhost:8000`
2. **NPS-OpenSearch** — OpenSearch datasource, URL: `http://localhost:9200`, index: `graylog_*`

### Dashboard 1: User Session View
- Variable: `$username` (text input)
- Variable: time range
- **Table panel**: one row per session — Start Time, Duration, AP Name, Client IP, MAC, Data In, Data Out, 802.11 Standard, RSSI
- Row expansion shows all raw events for that session ID
- Datasource: NPS-API `/sessions`

### Dashboard 2: Live Tail
- Auto-refresh: 10s (user-adjustable: 5s / 10s / 30s)
- **Logs panel** or table: Timestamp, User, MAC, AP, Event Type, Reason Code
- Color coding: Start=green, Stop=grey, Interim=blue, Failure=red
- Datasource: NPS-OpenSearch (direct, low latency)

### Dashboard 3: Auth Failures
- Variable: time range, reason code filter
- **Time series panel**: failure count over time
- **Table panel**: Timestamp, User, MAC, AP, Reason Code, Reason Description
- Datasource: NPS-API `/failures`

### Dashboard 4: MAC Tracker
- Variable: `$mac` (text input)
- **Timeline/table**: sessions for that MAC — Start Time, Username, AP, Duration, Data In/Out
- Shows device use across different users and APs
- Datasource: NPS-API `/mac/{mac}`

### Dashboard 5: AP Summary *(nice-to-have)*
- **Bar chart**: connections per AP over time range
- **Table**: AP Name, IP, Unique Users, Total Sessions, Failure Rate
- Datasource: NPS-API `/aps`

---

## Installation & Packaging

### Install Script (`install.sh`)
Single bash script that:
1. Checks prerequisites (Python 3.11+, systemd)
2. Creates `/etc/nps-api/` and writes default `config.yaml`
3. Creates Python venv at `/opt/nps-api/`, installs dependencies
4. Writes and enables `nps-api.service` systemd unit
5. Installs Grafana (if not present) via official apt/rpm repo
6. Installs Grafana JSON datasource plugin
7. Copies dashboard JSON files to Grafana provisioning directory
8. Writes Grafana datasource provisioning YAML
9. Restarts services

### File Layout
```
/opt/nps-api/
  venv/
  app/
    main.py
    config.py
    opensearch_client.py
    session_correlator.py
    reason_codes.py
    routers/
      sessions.py
      failures.py
      mac.py
      live.py
      aps.py

/etc/nps-api/
  config.yaml

/etc/grafana/provisioning/
  datasources/nps.yaml
  dashboards/nps.yaml
  dashboards/json/
    nps-user-sessions.json
    nps-live-tail.json
    nps-auth-failures.json
    nps-mac-tracker.json
    nps-ap-summary.json

/lib/systemd/system/
  nps-api.service

install.sh
```

---

## Error Handling

- nps-api returns standard HTTP error codes; Grafana surfaces them as panel errors
- OpenSearch connection failures logged to systemd journal; `/health` endpoint returns 503
- Missing or null fields handled gracefully — sessions with incomplete data flagged rather than dropped
- Field prefix mismatches surfaced clearly in logs with a config hint

---

## Testing

- Unit tests for session correlator logic (Start/Stop/Interim grouping, data total selection)
- Unit tests for reason code translation
- Integration test: spin up OpenSearch test container, insert sample events, verify API responses
- Manual verification against the existing `IN260222.log` sample file

---

## Out of Scope

- Authentication/authorisation for the Grafana/API (assumed on internal network)
- Alerting rules (can be added later in Grafana)
- Historical log file import (logs already in OpenSearch via existing pipeline)
