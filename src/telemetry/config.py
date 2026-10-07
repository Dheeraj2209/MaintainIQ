"""MQTT ingest configuration from the environment (design/M6_LIVE_TELEMETRY.md §7).

Live ingest is opt-in: with MQTT_BROKER_HOST unset the app behaves exactly as
before M6 (dataset backfill + replay + /demo), so tests, CI, and a bare
`uvicorn` run never try to reach a broker that isn't there.

Parsed once into a frozen dataclass rather than read ad hoc like
src/notifications/email.py does per send: the MQTT client is long-lived and
its connection parameters are fixed at connect time, so re-reading env on
every message would only create the illusion that a change takes effect.
Truthy parsing deliberately matches email.py ("1"/"true"/"yes"/"on") so every
boolean env var in the project behaves the same.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Mapping

from src.telemetry.protocol import DEFAULT_TOPIC_PREFIX, normalize_prefix

_TRUTHY = {"1", "true", "yes", "on"}

DEFAULT_PORT = 1883
DEFAULT_TLS_PORT = 8883
DEFAULT_CLIENT_ID = "maintainiq-ingest"
DEFAULT_QUEUE_MAX = 256


def _flag(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in _TRUTHY


def _int(env: Mapping[str, str], name: str, default: int, *, minimum: int, maximum: int | None = None) -> int:
    raw = (env.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from None
    if value < minimum or (maximum is not None and value > maximum):
        bound = f">= {minimum}" if maximum is None else f"between {minimum} and {maximum}"
        raise ValueError(f"{name} must be {bound}, got {value}")
    return value


@dataclass(frozen=True)
class TelemetrySettings:
    broker_host: str = ""
    broker_port: int = DEFAULT_PORT
    username: str = ""
    password: str = field(default="", repr=False)  # keep secrets out of logs
    tls: bool = False
    ca_cert: str | None = None
    client_id: str = DEFAULT_CLIENT_ID
    topic_prefix: str = DEFAULT_TOPIC_PREFIX
    auto_register: bool = True
    ingest_queue_max: int = DEFAULT_QUEUE_MAX

    @property
    def enabled(self) -> bool:
        return bool(self.broker_host)

    @property
    def use_auth(self) -> bool:
        # Contract §7: auth only when BOTH are set — a lone username is far
        # more likely a half-filled .env than an intentional password-less login.
        return bool(self.username and self.password)

    @property
    def broker_label(self) -> str | None:
        """"host:port" for /api/telemetry/status; None when ingest is disabled."""
        return f"{self.broker_host}:{self.broker_port}" if self.enabled else None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "TelemetrySettings":
        """Build settings from `env` (defaults to os.environ). Raises
        ValueError naming the offending variable on a malformed value."""
        env = os.environ if env is None else env
        tls = _flag(env, "MQTT_TLS", False)
        return cls(
            broker_host=(env.get("MQTT_BROKER_HOST") or "").strip(),
            # Port default follows TLS so `MQTT_TLS=1` alone points at 8883.
            broker_port=_int(
                env, "MQTT_BROKER_PORT", DEFAULT_TLS_PORT if tls else DEFAULT_PORT,
                minimum=1, maximum=65535,
            ),
            username=env.get("MQTT_USERNAME") or "",
            password=env.get("MQTT_PASSWORD") or "",
            tls=tls,
            ca_cert=(env.get("MQTT_CA_CERT") or "").strip() or None,
            client_id=(env.get("MQTT_CLIENT_ID") or "").strip() or DEFAULT_CLIENT_ID,
            topic_prefix=normalize_prefix(env.get("MQTT_TOPIC_PREFIX") or DEFAULT_TOPIC_PREFIX),
            auto_register=_flag(env, "MQTT_AUTO_REGISTER", True),
            ingest_queue_max=_int(env, "MQTT_INGEST_QUEUE_MAX", DEFAULT_QUEUE_MAX, minimum=1),
        )
