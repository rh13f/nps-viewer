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
