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
