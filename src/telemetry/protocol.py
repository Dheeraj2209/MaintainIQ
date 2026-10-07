"""MQTT wire protocol for M6 live telemetry (design/M6_LIVE_TELEMETRY.md §2-§5).

One module owns both directions of the format — the decoders the ingest
service runs on every message and the encoders the simulator (and tests) use
to produce them — so "what the device sends" and "what ingest accepts" cannot
drift apart. ESP32 firmware is the only producer that re-implements the
encoding by hand; this file is its reference.

Everything here is pure: bytes in, dataclass out (or bytes out). No paho, no
SQLite. That keeps validation unit-testable without a broker and lets the
ingest service record a precise rejection reason in telemetry_messages.error
instead of a generic "bad message".

Validation is strict on purpose. A snapshot that is silently half-wrong (a
truncated base64 axis, a NaN, the wrong machine in the topic) would still
produce a feature vector and a prediction — just a meaningless one, which is
worse than a visible rejection. Unknown extra JSON fields are tolerated so a
newer device firmware can add fields without being rejected by older ingest.
"""
from __future__ import annotations

import base64
import binascii
import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, NamedTuple

import numpy as np

PROTOCOL_VERSION = 1
DEFAULT_TOPIC_PREFIX = "maintainiq/v1"

# Matches the broker's message_size_limit. Enforced here too so an
# over-limit payload delivered by a misconfigured broker is still rejected
# before we spend time base64-decoding it.
MAX_PAYLOAD_BYTES = 1024 * 1024

MIN_SAMPLES = 32
MAX_SAMPLES = 65536
MAX_SAMPLE_RATE_HZ = 100_000.0

ENCODING_F32 = "f32le-b64"
ENCODING_I16 = "i16le-b64"
ENCODING_JSON = "json"
ENCODINGS = (ENCODING_F32, ENCODING_I16, ENCODING_JSON)

# Bytes per sample for the binary encodings; a decoded base64 axis whose
# length is not a multiple of this was truncated or mis-encoded.
_SAMPLE_WIDTH = {ENCODING_F32: 4, ENCODING_I16: 2}
_NUMPY_DTYPE = {ENCODING_F32: "<f4", ENCODING_I16: "<i2"}

KIND_TELEMETRY = "telemetry"
KIND_STATUS = "status"
KIND_ALERTS = "alerts"
_KINDS = (KIND_TELEMETRY, KIND_STATUS, KIND_ALERTS)

# Topic-safe identifiers: no '/', '+', '#', so an id can never inject an
# extra topic level or a wildcard into a topic we build from it.
ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
BOOT_ID_PATTERN = re.compile(r"^[A-Za-z0-9]{1,32}$")

ALERT_EVENT_TYPES = ("alert_created", "alert_escalated", "alert_resolved")

TELEMETRY_QOS = 1
STATUS_QOS = 1
ALERT_QOS = 1


class ProtocolError(ValueError):
    """A topic or payload violates the M6 contract.

    The identifying fields are best-effort context salvaged from the message
    (each only set if it was itself valid), so the ingest service can still
    attribute a rejected row in telemetry_messages to a device and sequence
    number. device_id is None when nothing usable was found — ingest then
    records it as '?' per contract §6.
    """

    def __init__(
        self,
        message: str,
        *,
        device_id: str | None = None,
        machine_id: str | None = None,
        boot_id: str | None = None,
        seq: int | None = None,
    ) -> None:
        super().__init__(message)
        self.device_id = device_id
        self.machine_id = machine_id
        self.boot_id = boot_id
        self.seq = seq


# --- Topics -------------------------------------------------------------------


class ParsedTopic(NamedTuple):
    kind: str  # one of KIND_TELEMETRY / KIND_STATUS / KIND_ALERTS
    ident: str  # machine_id for telemetry/alerts, device_id for status


def is_valid_id(value: Any) -> bool:
    return isinstance(value, str) and ID_PATTERN.fullmatch(value) is not None


def is_valid_boot_id(value: Any) -> bool:
    return isinstance(value, str) and BOOT_ID_PATTERN.fullmatch(value) is not None


def normalize_prefix(prefix: str) -> str:
    """Strip surrounding slashes; reject empty prefixes and wildcards (a
    wildcard in a publish topic is a protocol error at the broker)."""
    cleaned = (prefix or "").strip().strip("/")
    if not cleaned:
        raise ValueError("MQTT topic prefix must not be empty")
    if "+" in cleaned or "#" in cleaned:
        raise ValueError(f"MQTT topic prefix must not contain wildcards: {prefix!r}")
    return cleaned


def _topic(prefix: str, kind: str, ident: str) -> str:
    if not is_valid_id(ident):
        raise ValueError(f"invalid id for {kind} topic: {ident!r} (must match {ID_PATTERN.pattern})")
    return f"{normalize_prefix(prefix)}/{kind}/{ident}"


def telemetry_topic(machine_id: str, prefix: str = DEFAULT_TOPIC_PREFIX) -> str:
    return _topic(prefix, KIND_TELEMETRY, machine_id)


def status_topic(device_id: str, prefix: str = DEFAULT_TOPIC_PREFIX) -> str:
    return _topic(prefix, KIND_STATUS, device_id)


def alert_topic(machine_id: str, prefix: str = DEFAULT_TOPIC_PREFIX) -> str:
    return _topic(prefix, KIND_ALERTS, machine_id)


def telemetry_subscription(prefix: str = DEFAULT_TOPIC_PREFIX) -> str:
    return f"{normalize_prefix(prefix)}/{KIND_TELEMETRY}/+"


def status_subscription(prefix: str = DEFAULT_TOPIC_PREFIX) -> str:
    return f"{normalize_prefix(prefix)}/{KIND_STATUS}/+"


def ingest_subscriptions(prefix: str = DEFAULT_TOPIC_PREFIX) -> list[tuple[str, int]]:
    """(filter, qos) pairs the ingest service subscribes to (contract §2)."""
    return [
        (telemetry_subscription(prefix), TELEMETRY_QOS),
        (status_subscription(prefix), STATUS_QOS),
    ]


def parse_topic(topic: str, prefix: str = DEFAULT_TOPIC_PREFIX) -> ParsedTopic:
    """Split `{prefix}/{kind}/{id}` into (kind, id), validating every part."""
    base = normalize_prefix(prefix)
    if not isinstance(topic, str) or not topic.startswith(base + "/"):
        raise ProtocolError(f"topic {topic!r} is not under prefix {base!r}")
    rest = topic[len(base) + 1:].split("/")
    if len(rest) != 2:
        raise ProtocolError(f"topic {topic!r} must be {base}/<kind>/<id>")
    kind, ident = rest
    if kind not in _KINDS:
        raise ProtocolError(f"unknown topic kind {kind!r} in {topic!r}")
    if not is_valid_id(ident):
        raise ProtocolError(f"invalid id {ident!r} in topic {topic!r}")
    return ParsedTopic(kind, ident)


# --- Shared field validation ----------------------------------------------------


def _is_number(value: Any) -> bool:
    # bool is an int subclass; `true` is never a valid measurement.
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _parse_utc(value: Any, name: str) -> datetime:
    """ISO-8601 → aware UTC datetime. A naive timestamp is taken as UTC (the
    contract mandates UTC; devices that omit the offset still mean UTC)."""
    if not isinstance(value, str) or "T" not in value:
        raise ValueError(f"{name} must be an ISO-8601 date-time string, got {value!r}")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{name} is not valid ISO-8601: {value!r}") from None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def format_utc(moment: datetime) -> str:
    """Contract timestamp format: millisecond UTC with a 'Z' suffix."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    moment = moment.astimezone(timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


def _decode_json_object(payload: bytes | str, what: str) -> tuple[dict, int]:
    """Bytes → JSON object plus its size in bytes. Raises plain ValueError;
    callers wrap it with the salvaged ids."""
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    if not isinstance(payload, (bytes, bytearray, memoryview)):
        raise ValueError(f"{what} payload must be bytes, got {type(payload).__name__}")
    raw = bytes(payload)
    if len(raw) > MAX_PAYLOAD_BYTES:
        raise ValueError(f"{what} payload is {len(raw)} bytes, over the {MAX_PAYLOAD_BYTES}-byte limit")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{what} payload is not valid UTF-8: {exc}") from None
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{what} payload is not valid JSON: {exc}") from None
    if not isinstance(obj, dict):
        raise ValueError(f"{what} payload must be a JSON object, got {type(obj).__name__}")
    return obj, len(raw)


def _check_version(obj: dict) -> None:
    version = obj.get("v")
    if not _is_int(version) or version != PROTOCOL_VERSION:
        raise ValueError(f"unsupported protocol version v={version!r} (expected {PROTOCOL_VERSION})")


def _salvage(obj: Any) -> dict:
    """Pull whichever identifying fields are individually valid, for
    ProtocolError context. Never raises."""
    if not isinstance(obj, dict):
        return {}
    out: dict = {}
    if is_valid_id(obj.get("device_id")):
        out["device_id"] = obj["device_id"]
    if is_valid_id(obj.get("machine_id")):
        out["machine_id"] = obj["machine_id"]
    if is_valid_boot_id(obj.get("boot_id")):
        out["boot_id"] = obj["boot_id"]
    seq = obj.get("seq")
    if _is_int(seq) and seq >= 0:
        out["seq"] = seq
    return out


# --- Snapshot (§3) ----------------------------------------------------------------


@dataclass(frozen=True)
class Snapshot:
    """A validated, decoded telemetry snapshot. Axes are float64 in physical
    units (scale already applied)."""

    device_id: str
    machine_id: str
    boot_id: str
    seq: int
    sampled_at: datetime | None  # aware UTC; None when the device clock is unsynced
    time_synced: bool
    buffered: bool
    sample_rate_hz: float
    speed_rpm: float
    load_kn: float
    encoding: str
    scale: float
    horizontal: np.ndarray = field(repr=False)
    vertical: np.ndarray = field(repr=False)
    payload_bytes: int = 0

    @property
    def n_samples(self) -> int:
        return int(self.horizontal.shape[0])

    @property
    def idempotency_key(self) -> tuple[str, str, int]:
        return (self.device_id, self.boot_id, self.seq)


def _require_bool(obj: dict, name: str) -> bool:
    value = obj.get(name)
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean, got {value!r}")
    return value


def _require_number(obj: dict, name: str) -> float:
    value = obj.get(name)
    if not _is_number(value) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number, got {value!r}")
    return float(value)


def _decode_axis(value: Any, name: str, encoding: str, scale: float) -> np.ndarray:
    if encoding == ENCODING_JSON:
        if not isinstance(value, list):
            raise ValueError(f"{name} must be a JSON array for encoding {encoding!r}")
        if not all(_is_number(x) for x in value):
            raise ValueError(f"{name} must contain only numbers")
        try:
            samples = np.asarray(value, dtype=np.float64)
        except OverflowError:  # an integer too large for float64
            raise ValueError(f"{name} contains an out-of-range number") from None
    else:
        if not isinstance(value, str):
            raise ValueError(f"{name} must be a base64 string for encoding {encoding!r}")
        try:
            # validate=True: reject non-alphabet characters instead of
            # silently skipping them (which would shift every sample after).
            raw = base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError(f"{name} is not valid base64: {exc}") from None
        width = _SAMPLE_WIDTH[encoding]
        if len(raw) % width:
            raise ValueError(
                f"{name} decodes to {len(raw)} bytes, not a multiple of the "
                f"{width}-byte sample width for {encoding!r}"
            )
        samples = np.frombuffer(raw, dtype=_NUMPY_DTYPE[encoding]).astype(np.float64)

    n = samples.shape[0]
    if not MIN_SAMPLES <= n <= MAX_SAMPLES:
        raise ValueError(f"{name} has {n} samples; must be between {MIN_SAMPLES} and {MAX_SAMPLES}")
    # An absurd scale can overflow to inf; that is reported by the finiteness
    # check below, so the numpy overflow warning is just noise.
    with np.errstate(over="ignore", invalid="ignore"):
        samples = samples * scale
    if not np.all(np.isfinite(samples)):
        raise ValueError(f"{name} contains non-finite values (NaN/Inf) after decode")
    return samples


def _validate_snapshot(obj: dict, topic_machine_id: str, payload_bytes: int) -> Snapshot:
    _check_version(obj)

    device_id = obj.get("device_id")
    if not is_valid_id(device_id):
        raise ValueError(f"device_id {device_id!r} must match {ID_PATTERN.pattern}")
    machine_id = obj.get("machine_id")
    if not is_valid_id(machine_id):
        raise ValueError(f"machine_id {machine_id!r} must match {ID_PATTERN.pattern}")
    if machine_id != topic_machine_id:
        raise ValueError(
            f"payload machine_id {machine_id!r} does not match topic machine_id {topic_machine_id!r}"
        )
    boot_id = obj.get("boot_id")
    if not is_valid_boot_id(boot_id):
        raise ValueError(f"boot_id {boot_id!r} must match {BOOT_ID_PATTERN.pattern}")
    seq = obj.get("seq")
    if not _is_int(seq) or seq < 0:
        raise ValueError(f"seq must be an integer >= 0, got {seq!r}")

    time_synced = _require_bool(obj, "time_synced")
    buffered = _require_bool(obj, "buffered")

    # Capture time is only optional for a device that has no trustworthy
    # clock yet; a synced device omitting it would hide real sync lag.
    raw_sampled_at = obj.get("sampled_at")
    if raw_sampled_at is None:
        if time_synced:
            raise ValueError("sampled_at may only be null when time_synced is false")
        sampled_at = None
    else:
        sampled_at = _parse_utc(raw_sampled_at, "sampled_at")

    sample_rate_hz = _require_number(obj, "sample_rate_hz")
    if not 0 < sample_rate_hz <= MAX_SAMPLE_RATE_HZ:
        raise ValueError(f"sample_rate_hz must be in (0, {MAX_SAMPLE_RATE_HZ:g}], got {sample_rate_hz!r}")
    speed_rpm = _require_number(obj, "speed_rpm")
    if speed_rpm <= 0:
        raise ValueError(f"speed_rpm must be > 0, got {speed_rpm!r}")
    load_kn = _require_number(obj, "load_kn")
    if load_kn < 0:
        raise ValueError(f"load_kn must be >= 0, got {load_kn!r}")

    encoding = obj.get("encoding")
    if encoding not in ENCODINGS:
        raise ValueError(f"encoding {encoding!r} is not one of {', '.join(ENCODINGS)}")
    scale = 1.0 if obj.get("scale") is None else _require_number(obj, "scale")
    if scale <= 0:
        raise ValueError(f"scale must be > 0, got {scale!r}")

    horizontal = _decode_axis(obj.get("horizontal"), "horizontal", encoding, scale)
    vertical = _decode_axis(obj.get("vertical"), "vertical", encoding, scale)
    if horizontal.shape != vertical.shape:
        raise ValueError(
            f"horizontal and vertical must have equal length, got {horizontal.shape[0]} and {vertical.shape[0]}"
        )

    return Snapshot(
        device_id=device_id,
        machine_id=machine_id,
        boot_id=boot_id,
        seq=seq,
        sampled_at=sampled_at,
        time_synced=time_synced,
        buffered=buffered,
        sample_rate_hz=sample_rate_hz,
        speed_rpm=speed_rpm,
        load_kn=load_kn,
        encoding=encoding,
        scale=scale,
        horizontal=horizontal,
        vertical=vertical,
        payload_bytes=payload_bytes,
    )


def parse_snapshot(
    topic: str, payload: bytes | str, *, prefix: str = DEFAULT_TOPIC_PREFIX
) -> Snapshot:
    """Validate and decode one telemetry message. Raises ProtocolError (with
    salvaged device/boot/seq context) on any contract violation."""
    parsed = parse_topic(topic, prefix)
    if parsed.kind != KIND_TELEMETRY:
        raise ProtocolError(f"topic {topic!r} is a {parsed.kind} topic, not telemetry")
    context: dict = {"machine_id": parsed.ident}
    try:
        obj, size = _decode_json_object(payload, "snapshot")
        context.update(_salvage(obj))
        return _validate_snapshot(obj, parsed.ident, size)
    except ProtocolError:
        raise
    except ValueError as exc:
        raise ProtocolError(str(exc), **context) from None


# --- Device status (§4) -------------------------------------------------------------


@dataclass(frozen=True)
class DeviceStatus:
    """A validated status/heartbeat. Only device_id and online are
    guaranteed: an LWT carries nothing else."""

    device_id: str
    online: bool
    machine_id: str | None = None
    boot_id: str | None = None
    reported_at: datetime | None = None
    uptime_s: float | None = None
    firmware: str | None = None
    snapshot_interval_s: float | None = None
    heartbeat_interval_s: float | None = None
    buffer_depth: int | None = None
    buffer_capacity: int | None = None
    buffer_dropped_total: int | None = None
    publish_attempts_total: int | None = None
    publish_failures_total: int | None = None
    wifi_rssi_dbm: float | None = None
    raw: dict = field(default_factory=dict, repr=False)  # full payload, for device_status.payload_json
    payload_bytes: int = 0


# Optional status fields → validator returning the normalized value.
_COUNTER_FIELDS = (
    "buffer_depth",
    "buffer_capacity",
    "buffer_dropped_total",
    "publish_attempts_total",
    "publish_failures_total",
)
_STATUS_OPTIONAL_FIELDS = (
    "machine_id",
    "boot_id",
    "reported_at",
    "uptime_s",
    "firmware",
    "snapshot_interval_s",
    "heartbeat_interval_s",
    *_COUNTER_FIELDS,
    "wifi_rssi_dbm",
)


def _validate_status_field(name: str, value: Any) -> Any:
    if name == "machine_id":
        if not is_valid_id(value):
            raise ValueError(f"machine_id {value!r} must match {ID_PATTERN.pattern}")
        return value
    if name == "boot_id":
        if not is_valid_boot_id(value):
            raise ValueError(f"boot_id {value!r} must match {BOOT_ID_PATTERN.pattern}")
        return value
    if name == "reported_at":
        return _parse_utc(value, "reported_at")
    if name == "firmware":
        if not isinstance(value, str) or not 0 < len(value) <= 128:
            raise ValueError(f"firmware must be a non-empty string of at most 128 chars, got {value!r}")
        return value
    if name in _COUNTER_FIELDS:
        if not _is_int(value) or value < 0:
            raise ValueError(f"{name} must be an integer >= 0, got {value!r}")
        return value
    if not _is_number(value) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number, got {value!r}")
    value = float(value)
    if name == "uptime_s" and value < 0:
        raise ValueError(f"uptime_s must be >= 0, got {value!r}")
    if name in ("snapshot_interval_s", "heartbeat_interval_s") and value <= 0:
        raise ValueError(f"{name} must be > 0, got {value!r}")
    return value


def parse_status(
    topic: str, payload: bytes | str, *, prefix: str = DEFAULT_TOPIC_PREFIX
) -> DeviceStatus:
    """Validate one status/heartbeat/LWT message. Raises ProtocolError."""
    parsed = parse_topic(topic, prefix)
    if parsed.kind != KIND_STATUS:
        raise ProtocolError(f"topic {topic!r} is a {parsed.kind} topic, not status")
    context: dict = {"device_id": parsed.ident}
    try:
        obj, size = _decode_json_object(payload, "status")
        salvaged = _salvage(obj)
        salvaged.pop("device_id", None)  # the topic's id is authoritative for attribution
        context.update(salvaged)
        _check_version(obj)
        device_id = obj.get("device_id")
        if not is_valid_id(device_id):
            raise ValueError(f"device_id {device_id!r} must match {ID_PATTERN.pattern}")
        if device_id != parsed.ident:
            raise ValueError(
                f"payload device_id {device_id!r} does not match topic device_id {parsed.ident!r}"
            )
        online = _require_bool(obj, "online")
        values = {
            name: _validate_status_field(name, obj[name])
            for name in _STATUS_OPTIONAL_FIELDS
            if obj.get(name) is not None
        }
        return DeviceStatus(device_id=device_id, online=online, raw=obj, payload_bytes=size, **values)
    except ProtocolError:
        raise
    except ValueError as exc:
        raise ProtocolError(str(exc), **context) from None


# --- Encoders (simulator / tests) ---------------------------------------------------


def _to_axis(values: Any, name: str) -> np.ndarray:
    arr = np.asarray(values, dtype=np.float64)
    if arr.ndim != 1:
        raise ValueError(f"{name} must be one-dimensional, got shape {arr.shape}")
    return arr


def _encode_axis(samples: np.ndarray, encoding: str, scale: float) -> Any:
    scaled = samples / scale
    if encoding == ENCODING_JSON:
        return scaled.tolist()
    if encoding == ENCODING_I16:
        # Quantize like an ADC would: round to the nearest count, saturate.
        counts = np.clip(np.rint(scaled), -32768, 32767).astype("<i2")
        return base64.b64encode(counts.tobytes()).decode("ascii")
    return base64.b64encode(scaled.astype("<f4").tobytes()).decode("ascii")


def _auto_i16_scale(horizontal: np.ndarray, vertical: np.ndarray) -> float:
    """Pick g-per-LSB so the larger axis peak maps to full int16 range."""
    peak = float(max(np.max(np.abs(horizontal), initial=0.0), np.max(np.abs(vertical), initial=0.0)))
    return peak / 32767.0 if peak > 0 and math.isfinite(peak) else 1.0


def _encode_json(obj: dict) -> bytes:
    return json.dumps(obj, separators=(",", ":"), allow_nan=False).encode("utf-8")


def build_snapshot_payload(
    *,
    device_id: str,
    machine_id: str,
    boot_id: str,
    seq: int,
    horizontal: Any,
    vertical: Any,
    sample_rate_hz: float,
    speed_rpm: float,
    load_kn: float,
    sampled_at: datetime | str | None = None,
    time_synced: bool = True,
    buffered: bool = False,
    encoding: str = ENCODING_F32,
    scale: float | None = None,
) -> bytes:
    """Encode a §3 snapshot. `horizontal`/`vertical` are physical values;
    they are divided by `scale` before encoding so parse_snapshot (which
    multiplies) returns them. For i16le-b64 a None scale is chosen to use
    the full int16 range; for the other encodings it defaults to 1.0."""
    if not is_valid_id(device_id) or not is_valid_id(machine_id):
        raise ValueError(f"invalid device_id/machine_id: {device_id!r}/{machine_id!r}")
    if not is_valid_boot_id(boot_id):
        raise ValueError(f"invalid boot_id: {boot_id!r}")
    if encoding not in ENCODINGS:
        raise ValueError(f"encoding {encoding!r} is not one of {', '.join(ENCODINGS)}")
    h = _to_axis(horizontal, "horizontal")
    v = _to_axis(vertical, "vertical")
    if scale is None:
        scale = _auto_i16_scale(h, v) if encoding == ENCODING_I16 else 1.0
    if not scale > 0:
        raise ValueError(f"scale must be > 0, got {scale!r}")

    if isinstance(sampled_at, datetime):
        sampled_at = format_utc(sampled_at)

    return _encode_json({
        "v": PROTOCOL_VERSION,
        "device_id": device_id,
        "machine_id": machine_id,
        "boot_id": boot_id,
        "seq": int(seq),
        "sampled_at": sampled_at,
        "time_synced": bool(time_synced),
        "buffered": bool(buffered),
        "sample_rate_hz": float(sample_rate_hz),
        "speed_rpm": float(speed_rpm),
        "load_kn": float(load_kn),
        "encoding": encoding,
        "scale": float(scale),
        "horizontal": _encode_axis(h, encoding, scale),
        "vertical": _encode_axis(v, encoding, scale),
    })


def build_status_payload(*, device_id: str, online: bool, **fields: Any) -> bytes:
    """Encode a §4 status. Optional fields set to None are omitted, so
    build_status_payload(device_id=..., online=False) is exactly the LWT."""
    if not is_valid_id(device_id):
        raise ValueError(f"invalid device_id: {device_id!r}")
    unknown = set(fields) - set(_STATUS_OPTIONAL_FIELDS)
    if unknown:
        raise TypeError(f"unknown status fields: {sorted(unknown)}")
    body: dict = {"v": PROTOCOL_VERSION, "device_id": device_id, "online": bool(online)}
    for name in _STATUS_OPTIONAL_FIELDS:
        value = fields.get(name)
        if value is None:
            continue
        body[name] = format_utc(value) if isinstance(value, datetime) else value
    return _encode_json(body)


def build_lwt_payload(device_id: str) -> bytes:
    return build_status_payload(device_id=device_id, online=False)


# --- Downstream alerts (§5) -------------------------------------------------------


def is_alert_event(event: Any) -> bool:
    """True for realtime events that belong on the alert topic (not e.g.
    alert_acknowledged, which is a UI-only event)."""
    return (
        isinstance(event, dict)
        and event.get("type") in ALERT_EVENT_TYPES
        and is_valid_id(event.get("machine_id"))
    )


def device_alert_event(event: Any) -> dict | None:
    """The §5 alert event to publish to the device for a realtime event, or
    None when the device should not hear about it.

    ALERT_EVENT_TYPES events pass through unchanged. A human close
    (`alert_closed`, design/2026-10-07-prediction-feedback-design.md decision
    16) becomes a copy typed `alert_resolved`: apply_reading will never emit
    a resolve for that alert, so without it the retained alert topic would
    keep the device LED on. The device vocabulary itself is unchanged.
    Everything else (acknowledgements, feedback, work orders) is UI-only."""
    if is_alert_event(event):
        return event
    if (isinstance(event, dict) and event.get("type") == "alert_closed"
            and is_valid_id(event.get("machine_id"))):
        return {**event, "type": "alert_resolved"}
    return None


def build_alert_payload(event: dict) -> bytes:
    """Encode a realtime manager event (the dict pipeline.handle_prediction
    and the /demo route broadcast) as a §5 downstream alert. The nested
    `alert` row supplies severity/state/id; a missing one yields nulls rather
    than an error so a partial event still reaches the device LED."""
    if not is_alert_event(event):
        raise ValueError(
            f"not an alert event (type must be one of {ALERT_EVENT_TYPES} with a valid machine_id): "
            f"{event.get('type') if isinstance(event, dict) else event!r}"
        )
    alert = event.get("alert") or {}
    at = event.get("at")
    if isinstance(at, datetime):
        at = format_utc(at)
    return _encode_json({
        "v": PROTOCOL_VERSION,
        "type": event["type"],
        "machine_id": event["machine_id"],
        "severity": alert.get("severity"),
        "health_state": alert.get("health_state"),
        "status": alert.get("status"),
        "alert_id": alert.get("id"),
        "at": at or format_utc(datetime.now(timezone.utc)),
    })
