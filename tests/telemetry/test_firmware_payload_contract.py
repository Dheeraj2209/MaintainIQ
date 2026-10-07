"""Static contract checks for the ESP32 firmware (firmware/esp32-node/).

The firmware is the only producer that re-implements the M6 wire format by
hand (C++ on a microcontroller cannot import src/telemetry/protocol.py), and
CI has no ESP32 toolchain or hardware. So instead of running it, these tests
read the firmware source and check it against the Python reference encoder:

- the snapshot / status / LWT encoders use exactly the contract's JSON keys,
  in the reference order, each written with a JSON type the decoder accepts;
- the alert handler only reads keys the backend actually publishes, and its
  LED mapping names real health states and severities;
- wire constants (protocol version, encoding, topic layout, sample count,
  scale, boot_id shape) match protocol.py and the contract;
- the firmware's compile-time payload-size bound really covers a worst-case
  snapshot, measured with protocol.build_snapshot_payload;
- a payload rendered the way the firmware renders it (number formatting,
  timestamp format, null sampled_at before NTP sync) is accepted by
  protocol.parse_snapshot.

This does not prove the firmware works on a device — only that it cannot
drift from the contract unnoticed.
"""
import base64
import json
import math
import re
import struct
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

from src.alerts.generation import SEVERITY_BY_STATE
from src.telemetry import protocol as p

FW = Path(__file__).resolve().parents[2] / "firmware" / "esp32-node"
SRC = FW / "src"


def _read(rel: str) -> str:
    return (FW / rel).read_text(encoding="utf-8")


def _function_body(source: str, signature_prefix: str) -> str:
    """Text of the brace-balanced body of the first function whose
    definition starts with `signature_prefix`."""
    start = source.index(signature_prefix)
    open_brace = source.index("{", start)
    depth = 0
    for i in range(open_brace, len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[open_brace:i + 1]
    raise AssertionError(f"unbalanced braces after {signature_prefix!r}")


def _kv_calls(body: str) -> list[tuple[str, str]]:
    """[(writer kind, key)] for every JsonOut kv*("key", ...) call, in order."""
    return re.findall(r"\.kv(\w+)\(\s*\"([A-Za-z0-9_]+)\"", body)


def _doc_keys(body: str) -> list[str]:
    return re.findall(r"doc\[\"([A-Za-z0-9_]+)\"\]", body)


def _constexpr(source: str, name: str) -> str:
    match = re.search(rf"constexpr\s+[\w:\s\*]+?\b{name}\s*=\s*([^;]+);", source)
    assert match, f"constexpr {name} not found"
    return match.group(1).strip()


def _define(source: str, name: str) -> str:
    match = re.search(rf"^#define\s+{name}\s+(.+?)\s*(//.*)?$", source, re.MULTILINE)
    assert match, f"#define {name} not found"
    return match.group(1).strip()


@pytest.fixture(scope="module")
def payload_cpp() -> str:
    return _read("src/payload.cpp")


@pytest.fixture(scope="module")
def app_config() -> str:
    return _read("src/app_config.h")


def _reference_snapshot(**overrides) -> dict:
    h = np.linspace(-1, 1, 64)
    kwargs = dict(
        device_id="esp32-a1b2c3", machine_id="esp32-01", boot_id="0000000a1b2c3d4e", seq=3,
        horizontal=h, vertical=h[::-1], sample_rate_hz=3200.0, speed_rpm=1500.0, load_kn=0.0,
        sampled_at=datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc), time_synced=True,
        buffered=False, encoding=p.ENCODING_I16, scale=0.0039,
    )
    kwargs.update(overrides)
    return json.loads(p.build_snapshot_payload(**kwargs))


# --- Snapshot (§3) ------------------------------------------------------------------

# JSON writer each key may use. sampled_at is the only field with two shapes
# (string when NTP-synced, null before).
_SNAPSHOT_WRITERS = {
    "v": {"Int"},
    "device_id": {"Str"},
    "machine_id": {"Str"},
    "boot_id": {"Str"},
    "seq": {"Int", "Uint"},
    "sampled_at": {"Str", "Null"},
    "time_synced": {"Bool"},
    "buffered": {"Bool"},
    "sample_rate_hz": {"Num"},
    "speed_rpm": {"Num"},
    "load_kn": {"Num"},
    "encoding": {"Str"},
    "scale": {"Num"},
    "horizontal": {"B64I16"},
    "vertical": {"B64I16"},
}


def test_snapshot_keys_match_reference_encoder_in_order(payload_cpp):
    calls = _kv_calls(_function_body(payload_cpp, "size_t encodeSnapshotJson("))
    firmware_keys = list(dict.fromkeys(key for _, key in calls))  # sampled_at appears twice
    assert firmware_keys == list(_reference_snapshot().keys())
    assert set(firmware_keys) == set(_SNAPSHOT_WRITERS)


def test_snapshot_keys_use_contract_json_types(payload_cpp):
    calls = _kv_calls(_function_body(payload_cpp, "size_t encodeSnapshotJson("))
    for kind, key in calls:
        assert kind in _SNAPSHOT_WRITERS[key], f"{key} written as {kind}"
    sampled = {kind for kind, key in calls if key == "sampled_at"}
    assert sampled == {"Str", "Null"}, "sampled_at must be a string when synced and null otherwise"


def test_snapshot_encoder_writes_int16_little_endian(payload_cpp):
    # Low byte first, regardless of host endianness.
    body = _function_body(payload_cpp, "inline uint8_t leByte(")
    assert "(i & 1) ?" in body and "v >> 8" in body and "v & 0xFF" in body
    assert 'kB64[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"' in payload_cpp


# --- Status / LWT (§4) -----------------------------------------------------------------


def test_status_keys_are_exactly_the_contract_fields(payload_cpp):
    keys = _doc_keys(_function_body(payload_cpp, "size_t encodeStatusJson("))
    assert len(keys) == len(set(keys)), "duplicate status key"
    contract = {"v", "device_id", "online", *p._STATUS_OPTIONAL_FIELDS}
    assert set(keys) == contract


def test_status_values_round_trip_through_parse_status():
    # Same fields and value types the firmware sends (ArduinoJson renders
    # 2000 / 1000.0 as 2, uptime and counters as integers).
    payload = json.dumps({
        "v": 1, "device_id": "esp32-a1b2c3", "machine_id": "esp32-01", "boot_id": "0000000a1b2c3d4e",
        "online": True, "reported_at": "2026-10-06T12:00:00.000Z", "uptime_s": 3600,
        "firmware": "maintainiq-esp32/0.1.0", "snapshot_interval_s": 2, "heartbeat_interval_s": 10,
        "buffer_depth": 0, "buffer_capacity": 50, "buffer_dropped_total": 0,
        "publish_attempts_total": 1800, "publish_failures_total": 3, "wifi_rssi_dbm": -61,
    })
    status = p.parse_status("maintainiq/v1/status/esp32-a1b2c3", payload)
    assert status.online and status.buffer_capacity == 50 and status.snapshot_interval_s == 2.0


def test_lwt_matches_reference(payload_cpp):
    calls = _kv_calls(_function_body(payload_cpp, "size_t encodeLwtJson("))
    assert [key for _, key in calls] == list(json.loads(p.build_lwt_payload("esp32-a1b2c3")).keys())
    assert ("Bool", "online") in calls
    assert "kvBool(\"online\", false)" in _function_body(payload_cpp, "size_t encodeLwtJson(")


# --- Downstream alerts (§5) --------------------------------------------------------------


def test_alert_handler_reads_only_published_keys():
    net = _read("src/net_task.cpp")
    keys = set(_doc_keys(_function_body(net, "void onMqttMessage(")))
    published = json.loads(p.build_alert_payload({
        "type": "alert_created", "machine_id": "esp32-01",
        "alert": {"id": 1, "severity": "high", "health_state": "critical", "status": "open"},
    })).keys()
    assert keys and keys <= set(published)
    assert {"type", "health_state", "severity", "machine_id"} <= keys


def test_led_mapping_uses_real_states_and_severities():
    led = _function_body(_read("src/status_led.cpp"), "Mode modeForAlert(")
    health = set(re.findall(r"eq\(health_state, \"(\w+)\"\)", led))
    severity = set(re.findall(r"eq\(severity, \"(\w+)\"\)", led))
    types = set(re.findall(r"eq\(type, \"(\w+)\"\)", led))
    assert health == set(SEVERITY_BY_STATE) | {"healthy"}
    assert set(SEVERITY_BY_STATE.values()) <= severity
    assert types <= set(p.ALERT_EVENT_TYPES)


# --- Constants and topics ------------------------------------------------------------------


def test_wire_constants_match_contract(app_config):
    assert int(_constexpr(app_config, "kProtocolVersion")) == p.PROTOCOL_VERSION
    assert _constexpr(app_config, "kEncoding") == f'"{p.ENCODING_I16}"'
    assert int(_constexpr(app_config, "kSnapshotSamples")) == 4096  # contract §11
    assert float(_constexpr(app_config, "kScaleGPerLsb")) == 0.0039  # contract §11
    assert float(_constexpr(app_config, "kSampleRateHz")) == 3200.0
    assert p.MIN_SAMPLES <= 4096 <= p.MAX_SAMPLES
    boot_len = int(_constexpr(app_config, "kBootIdLen"))
    assert 1 <= boot_len <= 32
    # main.cpp renders boot_id as two 8-digit hex words.
    assert '"%08lx%08lx"' in _read("src/main.cpp") and boot_len == 16
    assert p.is_valid_boot_id("%08x%08x" % (0xFFFFFFFF, 0))


def test_topic_layout_matches_protocol():
    net = _read("src/net_task.cpp")
    example = _read("include/config.example.h")
    assert _define(example, "MQTT_TOPIC_PREFIX") == f'"{p.DEFAULT_TOPIC_PREFIX}"'
    for kind, build in (("telemetry", p.telemetry_topic), ("status", p.status_topic), ("alerts", p.alert_topic)):
        assert f'"%s/{kind}/%s"' in net
        assert build("X", "P") == f"P/{kind}/X"


def test_example_config_has_contract_defaults():
    example = _read("include/config.example.h")
    assert int(_define(example, "EDGE_BUFFER_CAPACITY")) == 50
    assert int(_define(example, "SNAPSHOT_INTERVAL_MS")) == 2000
    assert int(_define(example, "HEARTBEAT_INTERVAL_MS")) == 10000
    assert p.is_valid_id(_define(example, "MACHINE_ID").strip('"'))
    for pin, value in (("PIN_ADXL_SCK", 18), ("PIN_ADXL_MISO", 19), ("PIN_ADXL_MOSI", 23),
                       ("PIN_ADXL_CS", 5), ("PIN_STATUS_LED", 2)):
        assert int(_define(example, pin)) == value


# --- Payload size bound ----------------------------------------------------------------------


def test_overhead_bound_covers_worst_case_snapshot(app_config):
    bound = int(_constexpr(app_config, "kSnapshotJsonOverheadMax"))
    n = int(_constexpr(app_config, "kSnapshotSamples"))
    axis = np.full(n, -32768 * 0.0039)
    worst = p.build_snapshot_payload(
        device_id="d" * 64, machine_id="m" * 64, boot_id="b" * 32, seq=2**32 - 1,
        horizontal=axis, vertical=axis, sample_rate_hz=3200.0, speed_rpm=123456.7,
        load_kn=98765.43, sampled_at="2026-10-06T12:00:00.000Z", time_synced=False,
        buffered=False, encoding=p.ENCODING_I16, scale=0.0039,
    )
    b64_axis = 4 * math.ceil(2 * n / 3)
    assert len(json.loads(worst)["horizontal"]) == b64_axis
    overhead = len(worst) - 2 * b64_axis
    assert overhead <= bound, f"worst-case JSON overhead {overhead} B exceeds firmware bound {bound} B"
    assert 2 * b64_axis + bound <= p.MAX_PAYLOAD_BYTES


# --- Firmware-style rendering is accepted by the decoder --------------------------------------


def _fw_num(value: float) -> str:
    """payload.cpp kvNum: float32 field, printf %.7g, '.0' appended if integral."""
    text = "%.7g" % struct.unpack("<f", struct.pack("<f", value))[0]
    return text if any(c in text for c in ".eEn") else text + ".0"


def _fw_snapshot(counts_h, counts_v, *, synced: bool, buffered: bool) -> bytes:
    """Render a snapshot the way encodeSnapshotJson does (hand-built JSON text,
    firmware key order, firmware number/timestamp formatting)."""
    b64 = lambda counts: base64.b64encode(np.asarray(counts, dtype="<i2").tobytes()).decode()  # noqa: E731
    sampled = '"2026-10-06T12:00:00.250Z"' if synced else "null"
    return (
        '{"v":1,"device_id":"esp32-a1b2c3","machine_id":"esp32-01","boot_id":"0000000a1b2c3d4e",'
        f'"seq":4294967295,"sampled_at":{sampled},"time_synced":{str(synced).lower()},'
        f'"buffered":{str(buffered).lower()},"sample_rate_hz":{_fw_num(3200.0)},'
        f'"speed_rpm":{_fw_num(1500.0)},"load_kn":{_fw_num(0.0)},"encoding":"i16le-b64",'
        f'"scale":{_fw_num(0.0039)},"horizontal":"{b64(counts_h)}","vertical":"{b64(counts_v)}"}}'
    ).encode()


@pytest.mark.parametrize("synced,buffered", [(True, False), (False, True), (False, False)])
def test_firmware_rendering_is_accepted(synced, buffered):
    rng = np.random.default_rng(0)
    counts_h = rng.integers(-32768, 32768, 4096)
    counts_v = rng.integers(-4096, 4096, 4096)
    snap = p.parse_snapshot("maintainiq/v1/telemetry/esp32-01", _fw_snapshot(counts_h, counts_v, synced=synced, buffered=buffered))
    assert snap.n_samples == 4096 and snap.seq == 2**32 - 1
    assert snap.time_synced is synced and snap.buffered is buffered
    assert (snap.sampled_at is None) is (not synced)
    assert snap.scale == 0.0039 and snap.sample_rate_hz == 3200.0 and snap.load_kn == 0.0
    np.testing.assert_allclose(snap.horizontal, counts_h * 0.0039)
    np.testing.assert_allclose(snap.vertical, counts_v * 0.0039)


def test_firmware_number_rendering():
    assert _fw_num(3200.0) == "3200.0"
    assert _fw_num(0.0039) == "0.0039"
    assert _fw_num(0.0) == "0.0"
    assert _fw_num(1500.0) == "1500.0"
