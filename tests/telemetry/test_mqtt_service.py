"""MqttIngestService with a fake paho client — no broker needed."""
import json
import threading
import time
from types import SimpleNamespace

import pytest

from src.realtime.manager import ConnectionManager
from src.telemetry import protocol
from src.telemetry.config import TelemetrySettings
from src.telemetry.ingest import IngestOutcome
from src.telemetry.mqtt_service import MqttIngestService, build_service, disabled_status


class FakeClient:
    def __init__(self):
        self.calls = []
        self.published = []
        self.subscriptions = []
        self.on_connect = self.on_disconnect = self.on_message = None

    def username_pw_set(self, username, password):
        self.calls.append(("auth", username, password))

    def tls_set(self, ca_certs=None):
        self.calls.append(("tls", ca_certs))

    def reconnect_delay_set(self, min_delay, max_delay):
        self.calls.append(("backoff", min_delay, max_delay))

    def connect_async(self, host, port, keepalive=60):
        self.calls.append(("connect_async", host, port))

    def loop_start(self):
        self.calls.append(("loop_start",))

    def loop_stop(self):
        self.calls.append(("loop_stop",))

    def disconnect(self):
        self.calls.append(("disconnect",))

    def subscribe(self, topics):
        self.subscriptions.append(topics)

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append((topic, payload, qos, retain))

    # helpers simulating paho's network thread
    def deliver(self, topic, payload, retain=False):
        self.on_message(self, None, SimpleNamespace(topic=topic, payload=payload, retain=retain))


class RecordingIngestor:
    """Records calls; optionally blocks so the queue can fill up."""

    def __init__(self, status="accepted", gate: threading.Event | None = None):
        self.calls = []
        self.status = status
        self.gate = gate

    def handle_message(self, topic, payload, received_at=None, *, retained=False):
        if self.gate is not None:
            self.gate.wait(5)
        self.calls.append((topic, payload, retained))
        return IngestOutcome(status=self.status, kind="telemetry")


def _settings(**kw):
    base = dict(broker_host="broker.local", broker_port=1883, ingest_queue_max=8)
    base.update(kw)
    return TelemetrySettings(**base)


@pytest.fixture
def mgr():
    return ConnectionManager()


def _service(ingestor=None, mgr=None, **settings_kw):
    client = FakeClient()
    svc = MqttIngestService(_settings(**settings_kw), ingestor or RecordingIngestor(),
                            client_factory=lambda s: client, realtime_manager=mgr or ConnectionManager())
    return svc, client


def _wait_for(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_start_configures_client_non_blocking_and_is_idempotent(mgr):
    svc, client = _service(mgr=mgr, username="u", password="p", tls=True, ca_cert="ca.pem")
    svc.start()
    svc.start()
    try:
        assert ("auth", "u", "p") in client.calls
        assert ("tls", "ca.pem") in client.calls
        assert ("backoff", 1, 60) in client.calls
        assert client.calls.count(("connect_async", "broker.local", 1883)) == 1
        assert client.calls.count(("loop_start",)) == 1
        assert svc.status()["connected"] is False  # nothing connected yet
    finally:
        svc.stop()
        svc.stop()
    assert client.calls.count(("disconnect",)) == 1 and ("loop_stop",) in client.calls


def test_no_auth_without_both_credentials():
    svc, client = _service(username="u", password="")
    svc.start()
    svc.stop()
    assert not [c for c in client.calls if c[0] in ("auth", "tls")]


def test_default_client_factory_uses_fixed_id_and_persistent_session():
    from src.telemetry.mqtt_service import default_client_factory

    client = default_client_factory(_settings(client_id="maintainiq-ingest"))
    assert client._client_id == b"maintainiq-ingest"
    assert client._clean_session is False


def test_subscribes_qos1_on_every_connect_and_counts_reconnects():
    svc, client = _service(topic_prefix="plant/v1")
    svc.start()
    try:
        client.on_connect(client, None, {}, 0, None)
        assert svc.status()["connected"] is True
        client.on_disconnect(client, None, {}, 1, None)
        assert svc.status()["connected"] is False
        client.on_connect(client, None, {}, 0, None)
        expected = [("plant/v1/telemetry/+", 1), ("plant/v1/status/+", 1)]
        assert client.subscriptions == [expected, expected]
        assert svc.status()["stats"]["reconnects"] == 1
        # a refused CONNACK does not subscribe
        client.on_connect(client, None, {}, 5, None)
        assert len(client.subscriptions) == 2 and svc.status()["connected"] is False
    finally:
        svc.stop()


def test_messages_are_processed_in_order_by_one_worker():
    ingestor = RecordingIngestor()
    svc, client = _service(ingestor)
    svc.start()
    try:
        for i in range(5):
            client.deliver(f"maintainiq/v1/telemetry/m{i}", f"p{i}".encode(), retain=(i == 0))
        assert _wait_for(lambda: len(ingestor.calls) == 5)
        assert [c[1] for c in ingestor.calls] == [b"p0", b"p1", b"p2", b"p3", b"p4"]
        assert ingestor.calls[0][2] is True
        st = svc.status()
        assert st["stats"]["received"] == 5 and st["stats"]["accepted"] == 5
        assert st["last_message_at"] is not None and st["enabled"] is True
        assert st["broker"] == "broker.local:1883"
    finally:
        svc.stop()


def test_queue_overflow_drops_and_counts():
    gate = threading.Event()
    ingestor = RecordingIngestor(gate=gate)
    svc, client = _service(ingestor, ingest_queue_max=2)
    svc.start()
    try:
        client.deliver("maintainiq/v1/telemetry/m", b"first")
        # wait until the worker has taken the first message and is blocked on it
        assert _wait_for(lambda: svc.status()["stats"]["queue_depth"] == 0)
        for i in range(5):
            client.deliver("maintainiq/v1/telemetry/m", f"b{i}".encode())
        st = svc.status()["stats"]
        assert st["received"] == 6 and st["queue_overflows"] == 3 and st["queue_depth"] == 2
        gate.set()
        assert _wait_for(lambda: len(ingestor.calls) == 3)
        assert [c[1] for c in ingestor.calls] == [b"first", b"b0", b"b1"]
    finally:
        gate.set()
        svc.stop()


def test_status_is_not_stuck_behind_slow_snapshots():
    """A backed-up snapshot lane (slow predictions) must not delay heartbeats
    or LWTs, or the §4 online rule would show live devices as offline."""
    snapshot_gate = threading.Event()

    class SplitIngestor(RecordingIngestor):
        def handle_message(self, topic, payload, received_at=None, *, retained=False):
            if "/telemetry/" in topic:
                snapshot_gate.wait(5)
            self.calls.append((topic, payload, retained))
            return IngestOutcome(status="accepted", kind="x")

    ingestor = SplitIngestor()
    svc, client = _service(ingestor)
    svc.start()
    try:
        client.deliver("maintainiq/v1/telemetry/m", b"slow")
        client.deliver("maintainiq/v1/telemetry/m", b"queued")
        client.deliver("maintainiq/v1/status/d1", b"hb1")
        client.deliver("maintainiq/v1/status/d1", b"lwt")
        assert _wait_for(lambda: [c[1] for c in ingestor.calls] == [b"hb1", b"lwt"])
        assert svc.status()["stats"]["queue_depth"] == 1  # "queued" still waiting
        snapshot_gate.set()
        assert _wait_for(lambda: len(ingestor.calls) == 4)
        assert [c[1] for c in ingestor.calls][2:] == [b"slow", b"queued"]
    finally:
        snapshot_gate.set()
        svc.stop()


@pytest.mark.parametrize("status,key", [("rejected", "rejected"), ("duplicate", "duplicates"), ("error", "errors")])
def test_outcomes_map_to_stats(status, key):
    svc, _client = _service(RecordingIngestor(status=status))
    svc.process("maintainiq/v1/telemetry/m", b"x")
    assert svc.status()["stats"][key] == 1


def test_ingestor_exception_is_counted_not_raised():
    class Exploding:
        def handle_message(self, *a, **k):
            raise RuntimeError("bug")

    svc, _client = _service(Exploding())
    svc.process("maintainiq/v1/telemetry/m", b"x")
    assert svc.status()["stats"]["errors"] == 1


def test_listener_publishes_alert_events_retained_and_unregisters(mgr):
    svc, client = _service(mgr=mgr)
    svc.start()
    event = {"type": "alert_created", "machine_id": "sim-01", "at": "2026-10-06T12:00:00Z",
             "alert": {"id": 42, "severity": "critical", "health_state": "critical", "status": "open"}}
    mgr.broadcast_threadsafe(event)  # no loop bound: listeners are still called directly
    mgr.broadcast_threadsafe({"type": "alert_acknowledged", "machine_id": "sim-01", "alert": {}})
    assert len(client.published) == 1
    topic, payload, qos, retain = client.published[0]
    assert topic == "maintainiq/v1/alerts/sim-01" and qos == 1 and retain is True
    body = json.loads(payload)
    assert body == {"v": 1, "type": "alert_created", "machine_id": "sim-01", "severity": "critical",
                    "health_state": "critical", "status": "open", "alert_id": 42,
                    "at": "2026-10-06T12:00:00Z"}
    svc.stop()
    mgr.broadcast_threadsafe(event)
    assert len(client.published) == 1


def test_disabled_status_shape():
    st = disabled_status()
    assert st["enabled"] is False and st["connected"] is False and st["broker"] is None
    assert set(st["stats"]) == {"received", "accepted", "rejected", "duplicates", "errors",
                                "queue_overflows", "queue_depth", "reconnects"}
    assert all(v == 0 for v in st["stats"].values())


def test_status_stats_keys_match_contract():
    svc, _ = _service()
    assert set(svc.status()["stats"]) == set(disabled_status()["stats"])


def test_build_service_end_to_end_with_fake_client(db_path, monkeypatch):
    """Production wiring (real ingestor + DB) driven through the fake client."""
    import numpy as np

    from src.storage import db as db_module
    from tests.telemetry.test_ingest import FakePredictor

    monkeypatch.setattr(db_module, "get_connection", lambda: db_module.sqlite3.connect(db_path))
    client = FakeClient()
    svc = build_service(_settings(), client_factory=lambda s: client, realtime_manager=ConnectionManager())
    # swap the lazily-imported real predictor for the fake
    svc._ingestor._predictor_provider = FakePredictor
    svc._ingestor._on_prediction = lambda *a, **k: None
    rng = np.random.default_rng(0)
    payload = protocol.build_snapshot_payload(
        device_id="simdev-01", machine_id="sim-01", boot_id="b1", seq=0,
        horizontal=rng.normal(size=128), vertical=rng.normal(size=128),
        sample_rate_hz=25600.0, speed_rpm=2100.0, load_kn=12.0, sampled_at="2026-10-06T12:00:00Z",
    )
    svc.start()
    try:
        client.deliver(protocol.telemetry_topic("sim-01"), payload)
        assert _wait_for(lambda: svc.status()["stats"]["accepted"] == 1)
    finally:
        svc.stop()


# --- Manual ack / backpressure (M6 review regression) -----------------------------------


class AckingFakeClient(FakeClient):
    def __init__(self):
        super().__init__()
        self.acks = []

    def ack(self, mid, qos):
        self.acks.append((mid, qos))

    def deliver_qos(self, topic, payload, mid, qos=1):
        self.on_message(self, None, SimpleNamespace(topic=topic, payload=payload, retain=False,
                                                    mid=mid, qos=qos))


def _acking_service(ingestor, **settings_kw):
    client = AckingFakeClient()
    svc = MqttIngestService(_settings(**settings_kw), ingestor,
                            client_factory=lambda s: client, realtime_manager=ConnectionManager())
    return svc, client


def test_default_client_factory_acks_manually():
    from src.telemetry.mqtt_service import default_client_factory

    assert default_client_factory(_settings())._manual_ack is True


def test_qos1_is_acked_only_after_ingest_handled_it():
    gate = threading.Event()
    ing = RecordingIngestor(gate=gate)
    svc, client = _acking_service(ing)
    svc.start()
    try:
        client.on_connect(client, None, {}, 0, None)
        client.deliver_qos("maintainiq/v1/telemetry/m1", b"{}", mid=11)
        client.deliver_qos("maintainiq/v1/telemetry/m1", b"{}", mid=12, qos=0)
        time.sleep(0.1)
        assert client.acks == []  # queued is not delivered
        gate.set()
        assert _wait_for(lambda: len(ing.calls) == 2)
        assert _wait_for(lambda: client.acks == [(11, 1)])  # QoS 0 has nothing to ack
    finally:
        gate.set()
        svc.stop()


def test_full_queue_leaves_qos1_unacked_for_broker_redelivery():
    gate = threading.Event()
    ing = RecordingIngestor(gate=gate)
    svc, client = _acking_service(ing, ingest_queue_max=1)
    svc.start()
    try:
        client.on_connect(client, None, {}, 0, None)
        topic = "maintainiq/v1/telemetry/m1"
        client.deliver_qos(topic, b"{}", mid=1)  # taken by the (blocked) worker
        assert _wait_for(lambda: svc.status()["stats"]["queue_depth"] == 0)
        client.deliver_qos(topic, b"{}", mid=2)  # fills the queue
        client.deliver_qos(topic, b"{}", mid=3)  # overflow
        assert svc.status()["stats"]["queue_overflows"] == 1
        gate.set()
        assert _wait_for(lambda: len(ing.calls) == 2)
        assert _wait_for(lambda: sorted(client.acks) == [(1, 1), (2, 1)])
        time.sleep(0.05)
        assert (3, 1) not in client.acks
    finally:
        gate.set()
        svc.stop()


def test_no_ack_on_a_newer_connection():
    """A mid belongs to the connection that delivered it; after a reconnect
    the broker redelivers un-acked messages, so the stale one is not acked."""
    gate = threading.Event()
    ing = RecordingIngestor(gate=gate)
    svc, client = _acking_service(ing)
    svc.start()
    try:
        client.on_connect(client, None, {}, 0, None)
        client.deliver_qos("maintainiq/v1/telemetry/m1", b"{}", mid=5)
        client.on_disconnect(client, None, {}, 1, None)
        client.on_connect(client, None, {}, 0, None)
        gate.set()
        assert _wait_for(lambda: len(ing.calls) == 1)
        time.sleep(0.05)
        assert client.acks == []
    finally:
        gate.set()
        svc.stop()


def test_repeated_short_sessions_warn_about_client_id_clash(caplog):
    svc, client = _service()
    svc.start()
    try:
        with caplog.at_level("WARNING", logger="src.telemetry.mqtt_service"):
            for _ in range(3):
                client.on_connect(client, None, {}, 0, None)
                client.on_disconnect(client, None, {}, 7, None)
        assert any("MQTT_CLIENT_ID" in r.getMessage() for r in caplog.records)
    finally:
        svc.stop()


def test_connected_for_s_tracks_the_current_session(monkeypatch):
    from src.telemetry import mqtt_service

    clock = {"t": 1000.0}
    monkeypatch.setattr(mqtt_service.time, "monotonic", lambda: clock["t"])
    svc, client = _service()
    assert svc.connected_for_s() is None  # not started
    svc.start()
    try:
        assert svc.connected_for_s() is None  # started, not connected yet
        client.on_connect(client, None, {}, 0, None)
        assert svc.connected_for_s() == 0.0
        clock["t"] += 42.5
        assert svc.connected_for_s() == 42.5
        client.on_disconnect(client, None, {}, 1, None)
        assert svc.connected_for_s() is None
        # A reconnect restarts the clock (grace window covers the replay).
        clock["t"] += 5
        client.on_connect(client, None, {}, 0, None)
        clock["t"] += 3
        assert svc.connected_for_s() == 3.0
    finally:
        svc.stop()
    assert svc.connected_for_s() is None


def test_listener_translates_alert_closed_and_skips_feedback_events(mgr):
    """A human close never produces an alert_resolved from apply_reading, so
    the device LED would stay on; the listener publishes a §5 alert_resolved
    in its place (feedback design, decision 16)."""
    svc, client = _service(mgr=mgr)
    svc.start()
    alert = {"id": 42, "severity": "high", "health_state": "critical", "status": "resolved"}
    mgr.broadcast_threadsafe({"type": "alert_feedback_recorded", "machine_id": "sim-01",
                              "alert": alert, "feedback": {}, "at": "2026-10-07T12:00:00Z"})
    assert client.published == []
    mgr.broadcast_threadsafe({"type": "alert_closed", "machine_id": "sim-01",
                              "alert": alert, "feedback": {}, "at": "2026-10-07T12:00:00Z"})
    assert len(client.published) == 1
    topic, payload, qos, retain = client.published[0]
    assert topic == "maintainiq/v1/alerts/sim-01" and retain is True
    body = json.loads(payload)
    assert body["type"] == "alert_resolved" and body["status"] == "resolved" and body["alert_id"] == 42
    svc.stop()
