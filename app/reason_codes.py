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
