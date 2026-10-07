"""Tests for the M6 MQTT wire protocol (src/telemetry/protocol.py).

Every rejection rule in design/M6_LIVE_TELEMETRY.md §3/§4 gets its own case:
a rule that is not tested is a rule a future refactor can silently drop, and
a silently-accepted malformed snapshot becomes a meaningless prediction.
"""
import base64
import json
from datetime import datetime, timezone

import numpy as np
import pytest

from src.telemetry import protocol as p
from src.telemetry.protocol import ProtocolError

TOPIC = "maintainiq/v1/telemetry/sim-01"
STATUS_TOPIC = "maintainiq/v1/status/simdev-01"
T0 = datetime(2026, 10, 6, 12, 0, 0, 123000, tzinfo=timezone.utc)


def _axes(n=256, seed=0):
    rng = np.random.default_rng(seed)
    return rng.normal(0, 0.5, n), rng.normal(0, 0.3, n)


def _payload(**overrides) -> bytes:
    h, v = _axes()
    kwargs = dict(
        device_id="simdev-01", machine_id="sim-01", boot_id="5f3a9c1e", seq=7,
        horizontal=h, vertical=v, sample_rate_hz=25600.0, speed_rpm=2100.0,
        load_kn=12.0, sampled_at=T0,
    )
    kwargs.update(overrides)
    return p.build_snapshot_payload(**kwargs)


def _mutate(**changes) -> bytes:
    """A valid payload with raw JSON fields replaced (or removed, via the
    _DROP sentinel) — to craft violations the encoder would refuse to build."""
    obj = json.loads(_payload())
    for key, value in changes.items():
        if value is _DROP:
            obj.pop(key, None)
        else:
            obj[key] = value
    return json.dumps(obj).encode("utf-8")


_DROP = object()


# --- Topics -------------------------------------------------------------------

def test_topic_builders_and_subscriptions():
    assert p.telemetry_topic("sim-01") == TOPIC
    assert p.status_topic("simdev-01") == STATUS_TOPIC
    assert p.alert_topic("sim-01") == "maintainiq/v1/alerts/sim-01"
    assert p.telemetry_topic("m", prefix="plant/a/") == "plant/a/telemetry/m"
    assert p.ingest_subscriptions() == [
        ("maintainiq/v1/telemetry/+", 1),
        ("maintainiq/v1/status/+", 1),
    ]


@pytest.mark.parametrize("bad", ["a/b", "a+b", "a#", "", "x" * 65, "sp ace"])
def test_topic_builders_reject_unsafe_ids(bad):
    with pytest.raises(ValueError):
        p.telemetry_topic(bad)


def test_parse_topic():
    assert p.parse_topic(TOPIC) == (p.KIND_TELEMETRY, "sim-01")
    assert p.parse_topic(STATUS_TOPIC) == (p.KIND_STATUS, "simdev-01")
    assert p.parse_topic("maintainiq/v1/alerts/m.1") == (p.KIND_ALERTS, "m.1")
    assert p.parse_topic("x/y/telemetry/m", prefix="x/y") == ("telemetry", "m")


@pytest.mark.parametrize("topic", [
    "other/v1/telemetry/sim-01",
    "maintainiq/v1/telemetry",
    "maintainiq/v1/telemetry/sim-01/extra",
    "maintainiq/v1/bogus/sim-01",
    "maintainiq/v1/telemetry/bad id",
    "maintainiq/v1/telemetry/",
])
def test_parse_topic_rejects(topic):
    with pytest.raises(ProtocolError):
        p.parse_topic(topic)


@pytest.mark.parametrize("prefix", ["", "/", "a/+/b", "a/#"])
def test_normalize_prefix_rejects(prefix):
    with pytest.raises(ValueError):
        p.normalize_prefix(prefix)


# --- Snapshot round-trips ---------------------------------------------------------

@pytest.mark.parametrize("encoding", p.ENCODINGS)
def test_round_trip_every_encoding(encoding):
    h, v = _axes(512, seed=3)
    raw = _payload(horizontal=h, vertical=v, encoding=encoding, buffered=True)
    snap = p.parse_snapshot(TOPIC, raw)

    assert snap.device_id == "simdev-01"
    assert snap.machine_id == "sim-01"
    assert snap.boot_id == "5f3a9c1e"
    assert snap.seq == 7
    assert snap.idempotency_key == ("simdev-01", "5f3a9c1e", 7)
    assert snap.sampled_at == T0
    assert snap.time_synced is True and snap.buffered is True
    assert (snap.sample_rate_hz, snap.speed_rpm, snap.load_kn) == (25600.0, 2100.0, 12.0)
    assert snap.encoding == encoding
    assert snap.n_samples == 512
    assert snap.payload_bytes == len(raw)
    assert snap.horizontal.dtype == np.float64 and snap.vertical.dtype == np.float64
    # Tolerance per encoding: json is exact, f32 is float32 precision, i16 is
    # quantized to half an LSB of the auto-chosen scale.
    atol = {"json": 0.0, "f32le-b64": 1e-6, "i16le-b64": snap.scale / 2 + 1e-12}[encoding]
    np.testing.assert_allclose(snap.horizontal, h, rtol=0, atol=atol)
    np.testing.assert_allclose(snap.vertical, v, rtol=0, atol=atol)


def test_explicit_scale_is_applied_on_decode():
    # ESP32 path: i16 counts with 0.0039 g/LSB.
    counts_h = np.arange(-64, 64, dtype=np.int16)
    counts_v = np.full(128, 256, dtype=np.int16)
    obj = json.loads(_payload())
    obj.update(
        encoding="i16le-b64", scale=0.0039,
        horizontal=base64.b64encode(counts_h.astype("<i2").tobytes()).decode(),
        vertical=base64.b64encode(counts_v.astype("<i2").tobytes()).decode(),
    )
    snap = p.parse_snapshot(TOPIC, json.dumps(obj).encode())
    np.testing.assert_allclose(snap.horizontal, counts_h * 0.0039)
    np.testing.assert_allclose(snap.vertical, np.full(128, 256 * 0.0039))


def test_scale_defaults_to_one_when_absent():
    h, v = _axes(64)
    raw = _mutate(scale=_DROP, encoding="json", horizontal=h.tolist(), vertical=v.tolist())
    snap = p.parse_snapshot(TOPIC, raw)
    assert snap.scale == 1.0
    np.testing.assert_array_equal(snap.horizontal, h)


def test_unsynced_snapshot_may_have_null_sampled_at():
    snap = p.parse_snapshot(TOPIC, _payload(sampled_at=None, time_synced=False))
    assert snap.sampled_at is None and snap.time_synced is False


def test_sampled_at_offsets_normalize_to_utc():
    snap = p.parse_snapshot(TOPIC, _mutate(sampled_at="2026-10-06T14:00:00+02:00"))
    assert snap.sampled_at == datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
    naive = p.parse_snapshot(TOPIC, _mutate(sampled_at="2026-10-06T12:00:00"))
    assert naive.sampled_at.tzinfo is not None


def test_str_payload_and_unknown_fields_are_accepted():
    obj = json.loads(_payload())
    obj["future_field"] = {"x": 1}
    assert p.parse_snapshot(TOPIC, json.dumps(obj)).seq == 7


def test_bounds_inclusive_min_and_max_samples():
    for n in (p.MIN_SAMPLES, p.MAX_SAMPLES):
        h = np.zeros(n)
        assert p.parse_snapshot(TOPIC, _payload(horizontal=h, vertical=h)).n_samples == n


# --- Snapshot rejections ------------------------------------------------------------

def _f32_b64(n):
    return base64.b64encode(np.zeros(n, dtype="<f4").tobytes()).decode()


@pytest.mark.parametrize("changes, message", [
    ({"v": 2}, "version"),
    ({"v": True}, "version"),
    ({"v": _DROP}, "version"),
    ({"device_id": "bad/id"}, "device_id"),
    ({"device_id": _DROP}, "device_id"),
    ({"machine_id": "sim-02"}, "does not match topic"),
    ({"machine_id": "a+b"}, "machine_id"),
    ({"boot_id": "has-dash"}, "boot_id"),
    ({"boot_id": "x" * 33}, "boot_id"),
    ({"boot_id": ""}, "boot_id"),
    ({"seq": -1}, "seq"),
    ({"seq": 1.5}, "seq"),
    ({"seq": True}, "seq"),
    ({"seq": "3"}, "seq"),
    ({"time_synced": 1}, "time_synced"),
    ({"time_synced": _DROP}, "time_synced"),
    ({"buffered": "false"}, "buffered"),
    ({"sampled_at": None}, "sampled_at"),  # null while time_synced is true
    ({"sampled_at": "yesterday"}, "sampled_at"),
    ({"sampled_at": "2026-10-06"}, "sampled_at"),
    ({"sampled_at": 1700000000}, "sampled_at"),
    ({"sample_rate_hz": 0}, "sample_rate_hz"),
    ({"sample_rate_hz": 100001}, "sample_rate_hz"),
    ({"sample_rate_hz": "25600"}, "sample_rate_hz"),
    ({"sample_rate_hz": _DROP}, "sample_rate_hz"),
    ({"speed_rpm": 0}, "speed_rpm"),
    ({"speed_rpm": -5}, "speed_rpm"),
    ({"load_kn": -0.1}, "load_kn"),
    ({"load_kn": None}, "load_kn"),
    ({"encoding": "f64le-b64"}, "encoding"),
    ({"encoding": _DROP}, "encoding"),
    ({"scale": 0}, "scale"),
    ({"scale": -1.0}, "scale"),
    ({"scale": "1"}, "scale"),
    ({"horizontal": "!!!not-base64!!!"}, "base64"),
    ({"horizontal": _f32_b64(256)[:-1]}, "base64"),  # broken padding
    ({"horizontal": base64.b64encode(b"\x00" * 1026).decode()}, "multiple of the 4-byte"),
    ({"horizontal": _f32_b64(31), "vertical": _f32_b64(31)}, "between 32 and 65536"),
    ({"horizontal": _f32_b64(65537), "vertical": _f32_b64(65537)}, "between 32 and 65536"),
    ({"horizontal": _f32_b64(128)}, "equal length"),
    ({"horizontal": [0.0] * 256}, "base64 string"),
    ({"vertical": _DROP}, "vertical"),
])
def test_snapshot_rejections(changes, message):
    with pytest.raises(ProtocolError, match=message):
        p.parse_snapshot(TOPIC, _mutate(**changes))


def test_rejects_non_finite_f32_samples():
    h = np.zeros(64, dtype="<f4")
    h[10] = np.nan
    raw = _mutate(horizontal=base64.b64encode(h.tobytes()).decode(), vertical=_f32_b64(64))
    with pytest.raises(ProtocolError, match="non-finite"):
        p.parse_snapshot(TOPIC, raw)
    h[10] = np.inf
    raw = _mutate(horizontal=base64.b64encode(h.tobytes()).decode(), vertical=_f32_b64(64))
    with pytest.raises(ProtocolError, match="non-finite"):
        p.parse_snapshot(TOPIC, raw)


def test_rejects_scale_overflowing_to_infinity():
    raw = _mutate(scale=1e308, horizontal=base64.b64encode(np.full(64, 1e10, "<f4").tobytes()).decode(),
                  vertical=_f32_b64(64))
    with pytest.raises(ProtocolError, match="non-finite"):
        p.parse_snapshot(TOPIC, raw)


def test_i16_odd_byte_length_rejected():
    raw = _mutate(encoding="i16le-b64", horizontal=base64.b64encode(b"\x00" * 129).decode(),
                  vertical=base64.b64encode(b"\x00" * 128).decode())
    with pytest.raises(ProtocolError, match="multiple of the 2-byte"):
        p.parse_snapshot(TOPIC, raw)


@pytest.mark.parametrize("axis", [[1, 2, "3"] + [0] * 40, [True] * 64, [0] * 10, "abc"])
def test_json_encoding_rejects_bad_arrays(axis):
    raw = _mutate(encoding="json", horizontal=axis, vertical=[0.0] * len(axis) if isinstance(axis, list) else axis)
    with pytest.raises(ProtocolError):
        p.parse_snapshot(TOPIC, raw)


def test_json_encoding_rejects_nan_literal():
    # Python's json module accepts the non-standard NaN token; it must still
    # be rejected as non-finite.
    text = _mutate(encoding="json", horizontal=[0.0] * 64, vertical=[0.0] * 64).decode()
    text = text.replace('"horizontal": [0.0,', '"horizontal": [NaN,', 1)
    with pytest.raises(ProtocolError, match="non-finite"):
        p.parse_snapshot(TOPIC, text.encode())


@pytest.mark.parametrize("raw, message", [
    (b"\xff\xfe\x00garbage", "UTF-8"),
    (b"{not json", "JSON"),
    (b"[1, 2, 3]", "JSON object"),
    (b"null", "JSON object"),
])
def test_rejects_undecodable_payloads(raw, message):
    with pytest.raises(ProtocolError, match=message) as info:
        p.parse_snapshot(TOPIC, raw)
    # No usable device id → ingest will record '?'; machine comes from topic.
    assert info.value.device_id is None
    assert info.value.machine_id == "sim-01"


def test_rejects_oversized_payload():
    with pytest.raises(ProtocolError, match="limit"):
        p.parse_snapshot(TOPIC, b" " * (p.MAX_PAYLOAD_BYTES + 1))


def test_rejects_non_telemetry_topic():
    with pytest.raises(ProtocolError, match="not telemetry"):
        p.parse_snapshot(STATUS_TOPIC, _payload())
    with pytest.raises(ProtocolError):
        p.parse_snapshot("elsewhere/telemetry/sim-01", _payload())


def test_rejection_salvages_identifying_context():
    with pytest.raises(ProtocolError) as info:
        p.parse_snapshot(TOPIC, _mutate(speed_rpm=-1))
    err = info.value
    assert (err.device_id, err.boot_id, err.seq, err.machine_id) == ("simdev-01", "5f3a9c1e", 7, "sim-01")
    assert isinstance(err, ValueError)

    with pytest.raises(ProtocolError) as info:
        p.parse_snapshot(TOPIC, _mutate(device_id="bad/id", seq=-3))
    assert info.value.device_id is None and info.value.seq is None


# --- Encoder guards ------------------------------------------------------------------

def test_build_snapshot_rejects_bad_inputs():
    h, v = _axes(64)
    base = dict(device_id="d", machine_id="m", boot_id="b1", seq=0, horizontal=h, vertical=v,
                sample_rate_hz=1.0, speed_rpm=1.0, load_kn=0.0)
    with pytest.raises(ValueError):
        p.build_snapshot_payload(**{**base, "device_id": "a/b"})
    with pytest.raises(ValueError):
        p.build_snapshot_payload(**{**base, "boot_id": "no-dash"})
    with pytest.raises(ValueError):
        p.build_snapshot_payload(**{**base, "encoding": "raw"})
    with pytest.raises(ValueError):
        p.build_snapshot_payload(**{**base, "scale": 0.0})
    with pytest.raises(ValueError):
        p.build_snapshot_payload(**{**base, "horizontal": np.zeros((2, 32))})


def test_i16_all_zero_axes_still_valid():
    z = np.zeros(64)
    snap = p.parse_snapshot(TOPIC, _payload(horizontal=z, vertical=z, encoding="i16le-b64"))
    assert snap.scale == 1.0
    assert not snap.horizontal.any()


def test_format_utc():
    assert p.format_utc(T0) == "2026-10-06T12:00:00.123Z"
    assert p.format_utc(datetime(2026, 1, 1)) == "2026-01-01T00:00:00.000Z"


# --- Device status (§4) ----------------------------------------------------------------

def _full_status(**overrides) -> bytes:
    fields = dict(
        machine_id="sim-01", boot_id="5f3a9c1e", reported_at=T0, uptime_s=3600,
        firmware="maintainiq-esp32/0.1.0", snapshot_interval_s=2.0, heartbeat_interval_s=10.0,
        buffer_depth=3, buffer_capacity=50, buffer_dropped_total=1,
        publish_attempts_total=1800, publish_failures_total=3, wifi_rssi_dbm=-61,
    )
    fields.update(overrides)
    return p.build_status_payload(device_id="simdev-01", online=True, **fields)


def test_status_round_trip():
    raw = _full_status()
    st = p.parse_status(STATUS_TOPIC, raw)
    assert st.device_id == "simdev-01" and st.online is True
    assert st.machine_id == "sim-01" and st.boot_id == "5f3a9c1e"
    assert st.reported_at == T0
    assert st.uptime_s == 3600.0 and st.firmware == "maintainiq-esp32/0.1.0"
    assert (st.snapshot_interval_s, st.heartbeat_interval_s) == (2.0, 10.0)
    assert (st.buffer_depth, st.buffer_capacity, st.buffer_dropped_total) == (3, 50, 1)
    assert (st.publish_attempts_total, st.publish_failures_total) == (1800, 3)
    assert st.wifi_rssi_dbm == -61.0
    assert st.raw["firmware"] == "maintainiq-esp32/0.1.0"
    assert st.payload_bytes == len(raw)


def test_lwt_minimal_status_is_valid():
    raw = p.build_lwt_payload("simdev-01")
    assert json.loads(raw) == {"v": 1, "device_id": "simdev-01", "online": False}
    st = p.parse_status(STATUS_TOPIC, raw)
    assert st.online is False
    assert st.machine_id is None and st.buffer_depth is None and st.heartbeat_interval_s is None


def test_status_null_optional_fields_are_treated_as_absent():
    raw = json.dumps({"v": 1, "device_id": "simdev-01", "online": True, "firmware": None,
                      "wifi_rssi_dbm": None}).encode()
    st = p.parse_status(STATUS_TOPIC, raw)
    assert st.firmware is None and st.wifi_rssi_dbm is None


def _status_mutate(**changes) -> bytes:
    obj = json.loads(_full_status())
    for key, value in changes.items():
        if value is _DROP:
            obj.pop(key, None)
        else:
            obj[key] = value
    return json.dumps(obj).encode()


@pytest.mark.parametrize("changes, message", [
    ({"v": 0}, "version"),
    ({"device_id": "simdev-02"}, "does not match topic"),
    ({"device_id": "bad id"}, "device_id"),
    ({"online": _DROP}, "online"),
    ({"online": "true"}, "online"),
    ({"machine_id": "a/b"}, "machine_id"),
    ({"boot_id": "a-b"}, "boot_id"),
    ({"reported_at": "noon"}, "reported_at"),
    ({"uptime_s": -1}, "uptime_s"),
    ({"firmware": ""}, "firmware"),
    ({"firmware": 3}, "firmware"),
    ({"snapshot_interval_s": 0}, "snapshot_interval_s"),
    ({"heartbeat_interval_s": -10}, "heartbeat_interval_s"),
    ({"buffer_depth": -1}, "buffer_depth"),
    ({"buffer_capacity": 2.5}, "buffer_capacity"),
    ({"publish_failures_total": True}, "publish_failures_total"),
    ({"wifi_rssi_dbm": "weak"}, "wifi_rssi_dbm"),
])
def test_status_rejections(changes, message):
    with pytest.raises(ProtocolError, match=message) as info:
        p.parse_status(STATUS_TOPIC, _status_mutate(**changes))
    # Attribution comes from the topic even when the payload id is bad.
    assert info.value.device_id == "simdev-01"


def test_status_rejects_wrong_topic_and_garbage():
    with pytest.raises(ProtocolError, match="not status"):
        p.parse_status(TOPIC, _full_status())
    with pytest.raises(ProtocolError, match="JSON"):
        p.parse_status(STATUS_TOPIC, b"{")


def test_build_status_rejects_unknown_fields():
    with pytest.raises(TypeError):
        p.build_status_payload(device_id="d", online=True, battery=3)


# --- Downstream alerts (§5) --------------------------------------------------------------

def test_build_alert_payload_from_pipeline_event():
    event = {
        "type": "alert_created", "machine_id": "sim-01", "at": "2026-10-06T12:00:00Z",
        "alert": {"id": 42, "severity": "critical", "health_state": "critical", "status": "open",
                  "probable_cause": "outer race"},
        "prediction": {"predicted_rul_minutes": 10},
    }
    assert json.loads(p.build_alert_payload(event)) == {
        "v": 1, "type": "alert_created", "machine_id": "sim-01", "severity": "critical",
        "health_state": "critical", "status": "open", "alert_id": 42,
        "at": "2026-10-06T12:00:00Z",
    }


def test_build_alert_payload_tolerates_missing_alert_and_at():
    body = json.loads(p.build_alert_payload({"type": "alert_resolved", "machine_id": "m"}))
    assert body["alert_id"] is None and body["severity"] is None
    assert body["at"].endswith("Z")


@pytest.mark.parametrize("event", [
    {"type": "alert_acknowledged", "machine_id": "m"},
    {"type": "alert_created", "machine_id": "a/b"},
    {"type": "alert_created"},
    "alert_created",
])
def test_build_alert_payload_rejects_non_alert_events(event):
    assert not p.is_alert_event(event)
    with pytest.raises(ValueError):
        p.build_alert_payload(event)


# --- device_alert_event (feedback design, decision 16) ------------------------------------

def _closed_event():
    return {"type": "alert_closed", "machine_id": "sim-01", "at": "2026-10-07T12:00:00Z",
            "alert": {"id": 42, "severity": "high", "health_state": "critical", "status": "resolved"},
            "feedback": {"id": 1, "outcome": "false_alarm"}}


@pytest.mark.parametrize("kind", p.ALERT_EVENT_TYPES)
def test_device_alert_event_passes_alert_events_through(kind):
    event = {"type": kind, "machine_id": "sim-01", "alert": {"id": 1}}
    assert p.device_alert_event(event) is event


def test_device_alert_event_translates_alert_closed_to_resolved():
    original = _closed_event()
    translated = p.device_alert_event(original)
    assert translated["type"] == "alert_resolved"
    assert translated["alert"]["id"] == 42 and translated["machine_id"] == "sim-01"
    assert original["type"] == "alert_closed"  # not mutated
    body = json.loads(p.build_alert_payload(translated))
    assert body["type"] == "alert_resolved" and body["status"] == "resolved" and body["alert_id"] == 42


@pytest.mark.parametrize("kind", ["alert_feedback_recorded", "alert_acknowledged", "work_order_updated"])
def test_device_alert_event_drops_ui_only_events(kind):
    assert p.device_alert_event({"type": kind, "machine_id": "sim-01", "alert": {"id": 1}}) is None


def test_device_alert_event_rejects_unsafe_ids_and_non_dicts():
    assert p.device_alert_event({"type": "alert_closed", "machine_id": "a/b", "alert": {}}) is None
    assert p.device_alert_event("alert_closed") is None
