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

def test_invalid_hex_chars():
    with pytest.raises(InvalidMacError):
        normalise_mac("28:9G:00:DA:05:CA")
