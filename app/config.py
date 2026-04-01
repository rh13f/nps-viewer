"""Configuration dataclasses and loader for nps-api. Reads /etc/nps-api/config.yaml or NPS_CONFIG env var."""
from __future__ import annotations
import dataclasses
import logging
import os
from dataclasses import dataclass, field
from typing import Optional
import yaml

logger = logging.getLogger(__name__)


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
    port: int = 8765


@dataclass
class Config:
    opensearch: OpenSearchConfig = field(default_factory=OpenSearchConfig)
    session: SessionConfig = field(default_factory=SessionConfig)
    fields: FieldsConfig = field(default_factory=FieldsConfig)
    api: ApiConfig = field(default_factory=ApiConfig)


def load_config(path: Optional[str] = None) -> Config:
    path = path or os.environ.get("NPS_CONFIG", "/etc/nps-api/config.yaml")
    try:
        with open(path) as f:
            raw = yaml.safe_load(f) or {}
    except FileNotFoundError:
        logger.warning("Config file not found at %s, using defaults", path)
        raw = {}
    except PermissionError as e:
        logger.error("Cannot read config file at %s: %s", path, e)
        raw = {}

    def _build(cls, data):
        if not data:
            return cls()
        field_names = {f.name for f in dataclasses.fields(cls)}
        known = {k: v for k, v in data.items() if k in field_names}
        unknown = [k for k in data if k not in field_names]
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
