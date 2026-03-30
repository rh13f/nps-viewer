"""Build Grafana JSON datasource table responses.

The marcusolsson-json-datasource plugin expects:
[
  {
    "columns": [{"text": "col_name", "type": "string|number|time"}, ...],
    "rows": [[val, val, ...], ...],
    "type": "table"
  }
]

Note: If the installed marcusolsson-json-datasource version does not accept this
SimpleJSON-style format, use simpod-json-datasource as a drop-in replacement.
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
