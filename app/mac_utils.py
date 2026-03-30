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
