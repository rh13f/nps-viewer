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
