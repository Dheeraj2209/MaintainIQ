"""Long-lived MQTT client that feeds TelemetryIngestor (M6 contract §2, §5, §7, §8).

Threading model, and why:

* paho runs its own network thread (``loop_start``). Its callbacks must return
  quickly — while ``on_message`` runs, paho is not reading the socket, not
  sending PINGREQs, and not acking. Feature extraction plus a model prediction
  per snapshot is far too slow to do there, so ``on_message`` only appends to
  a bounded ``queue.Queue`` and returns.
* ONE worker thread drains the snapshot queue and calls the ingestor. One,
  not a pool: ingest must see each ``(device_id, boot_id)`` in ``seq`` order
  (the device flushes its edge buffer oldest-first for exactly this reason),
  the idempotency check is check-then-insert, and SQLite serialises writers
  anyway.
* Status / heartbeat / LWT messages get their OWN queue and worker. A model
  prediction takes about a second per snapshot, so whenever snapshots back up
  (a fleet flushing edge buffers, or simply more devices than the model keeps
  up with) a status sharing that queue would be applied tens of seconds late —
  and the §4 online rule (last message younger than 3 × heartbeat) would then
  show every device offline, and an LWT would not show at all until the
  backlog cleared. Status upserts are cheap, so their lane stays current.
* QoS-1 messages are acked MANUALLY (``manual_ack=True``), and only after the
  worker has run them through the ingestor. paho's default would PUBACK as
  soon as ``on_message`` returned — i.e. when a message was merely queued —
  and that breaks the broker-side buffer below: after an app outage Mosquitto
  would stream its whole stored backlog at socket speed (every message acked
  instantly, so its in-flight window never fills), everything past the queue
  bound would be dropped, and the broker would already have deleted it.
  Acking after ingest makes Mosquitto's in-flight window
  (``max_inflight_messages``, 20 by default) the flow control: at most that
  many QoS-1 messages are with us un-acked, the rest of the backlog stays on
  the broker until we catch up. A message still queued when the app stops is
  not acked either, so the broker redelivers it to the next session; ingest is
  idempotent on ``(device_id, boot_id, seq)``, so a redelivery of something we
  did process is just a ``duplicate``.
* The queue is still bounded (MQTT_INGEST_QUEUE_MAX). What can overflow it is
  traffic the broker does not window: QoS-0 publishes (the ESP32 firmware,
  §11) delivered in a burst. Those are dropped and counted in
  ``queue_overflows`` instead of growing memory without limit, and the KPIs
  show the loss as sequence gaps. A QoS-1 message that meets a full queue is
  counted the same way but deliberately left un-acked, so the broker keeps it
  and redelivers it when the session next resumes.

Durability across app restarts comes from the broker, not from us: a fixed
client id with ``clean_session=False`` makes Mosquitto keep the session and
queue QoS-1 telemetry while the app is down (the "cloud-side buffer" in §7).
The flip side of a fixed id: two app processes configured with the same
MQTT_CLIENT_ID take the session from each other on every reconnect. The
service cannot prevent that (the id must stay stable for the session to
survive restarts), but it logs a warning when connections keep dying within
seconds of being established, which is what a takeover loop looks like.
Subscriptions are re-issued on every CONNACK anyway, because a broker that
lost its persistence store would otherwise leave us connected but deaf.

Nothing here blocks the API event loop: ``connect_async`` + ``loop_start`` do
the connect (and every reconnect, with ``reconnect_delay_set`` backoff) on
paho's thread, so an unreachable broker delays nothing at startup.

The service also republishes realtime alert events to
``{prefix}/alerts/{machine_id}`` (retained, QoS 1) through a realtime manager
listener, for every alert regardless of origin — MQTT, replay or /demo — so a
device LED reflects the same alert state the dashboard shows.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable

from src.telemetry import protocol
from src.telemetry.config import TelemetrySettings
from src.telemetry.ingest import ACCEPTED, DUPLICATE, ERROR, REJECTED, TelemetryIngestor

logger = logging.getLogger(__name__)

# Reconnect backoff bounds (seconds) for paho's automatic reconnect.
RECONNECT_MIN_DELAY_S = 1
RECONNECT_MAX_DELAY_S = 60
KEEPALIVE_S = 30

# How long the worker blocks on an empty queue before re-checking for stop.
_WORKER_POLL_S = 0.2

# A connection that dies this soon after CONNACK, this many times in a row,
# looks like a client-id takeover loop (another process with the same
# MQTT_CLIENT_ID) rather than ordinary network trouble.
_SHORT_SESSION_S = 5.0
_SHORT_SESSIONS_BEFORE_WARNING = 3

_STAT_KEYS = ("received", "accepted", "rejected", "duplicates", "errors", "queue_overflows", "reconnects")
_OUTCOME_STAT = {ACCEPTED: "accepted", REJECTED: "rejected", DUPLICATE: "duplicates", ERROR: "errors"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def default_client_factory(settings: TelemetrySettings):
    """A real paho client. Credentials/TLS/backoff are applied by the service
    (not here) so a fake factory in tests sees the same configuration calls."""
    import paho.mqtt.client as mqtt

    return mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=settings.client_id,
        # Persistent session: the broker queues QoS-1 messages for us while
        # the app is down. Requires the fixed client id above.
        clean_session=False,
        protocol=mqtt.MQTTv311,
        # Ack QoS-1 messages only once ingest has handled them, so the
        # broker's in-flight window throttles a reconnect backlog instead of
        # us acking (and then dropping) it — see the module docstring.
        manual_ack=True,
    )


def zero_stats() -> dict:
    stats = {key: 0 for key in _STAT_KEYS}
    stats["queue_depth"] = 0
    return stats


def disabled_status(settings: TelemetrySettings | None = None) -> dict:
    """The §8 /telemetry/status body when MQTT_BROKER_HOST is unset."""
    return {
        "enabled": False,
        "connected": False,
        "broker": None,
        "topic_prefix": settings.topic_prefix if settings else protocol.DEFAULT_TOPIC_PREFIX,
        "started_at": None,
        "last_message_at": None,
        "stats": zero_stats(),
    }


def _reason_failed(reason_code: Any) -> bool:
    # VERSION2 callbacks pass a ReasonCode object (also for MQTT 3.1.1);
    # tolerate a bare int from fakes.
    if hasattr(reason_code, "is_failure"):
        return bool(reason_code.is_failure)
    try:
        return int(reason_code) != 0
    except (TypeError, ValueError):
        return False


class MqttIngestService:
    def __init__(
        self,
        settings: TelemetrySettings,
        ingestor: TelemetryIngestor,
        *,
        client_factory: Callable[[TelemetrySettings], Any] = default_client_factory,
        realtime_manager=None,
    ) -> None:
        self._settings = settings
        self._ingestor = ingestor
        self._client_factory = client_factory
        if realtime_manager is None:
            from src.realtime.manager import manager as realtime_manager
        self._manager = realtime_manager

        # Snapshot lane and status lane (see module docstring); each bounded
        # by MQTT_INGEST_QUEUE_MAX.
        self._queue: queue.Queue = queue.Queue(maxsize=settings.ingest_queue_max)
        self._status_queue: queue.Queue = queue.Queue(maxsize=settings.ingest_queue_max)
        self._lock = threading.Lock()
        self._stats = {key: 0 for key in _STAT_KEYS}
        self._client = None
        self._worker: threading.Thread | None = None
        self._status_worker: threading.Thread | None = None
        self._stop = threading.Event()
        self._started = False
        self._connected = False
        self._connect_count = 0
        self._connected_mono: float | None = None
        self._short_sessions = 0
        self._started_at: datetime | None = None
        self._last_message_at: datetime | None = None

    # --- Lifecycle ------------------------------------------------------------------

    @property
    def settings(self) -> TelemetrySettings:
        return self._settings

    @property
    def running(self) -> bool:
        return self._started

    def start(self) -> None:
        """Begin connecting and consuming. Returns immediately; idempotent."""
        with self._lock:
            if self._started:
                return
            self._started = True
            self._stop.clear()
            self._started_at = _now()

        s = self._settings
        try:
            client = self._client_factory(s)
            client.on_connect = self._on_connect
            client.on_disconnect = self._on_disconnect
            client.on_message = self._on_message
            if s.use_auth:
                client.username_pw_set(s.username, s.password)
            if s.tls:
                # A bad CA path raises here; that is a configuration error the
                # caller (app lifespan) logs, and start() may be retried.
                client.tls_set(ca_certs=s.ca_cert)
            client.reconnect_delay_set(min_delay=RECONNECT_MIN_DELAY_S, max_delay=RECONNECT_MAX_DELAY_S)
        except Exception:
            with self._lock:
                self._started = False
            raise
        self._client = client

        self._worker = threading.Thread(target=self._run_worker, args=(self._queue,),
                                        name="mqtt-ingest", daemon=True)
        self._worker.start()
        self._status_worker = threading.Thread(target=self._run_worker, args=(self._status_queue,),
                                               name="mqtt-ingest-status", daemon=True)
        self._status_worker.start()
        self._manager.add_listener(self._on_realtime_event)

        try:
            # Non-blocking: the TCP connect happens on paho's thread, which
            # keeps retrying with backoff if the broker is not up yet.
            client.connect_async(s.broker_host, s.broker_port, keepalive=KEEPALIVE_S)
            client.loop_start()
        except Exception:
            # Ingest stays down but the app keeps serving; /telemetry/status
            # reports connected=false.
            logger.exception("Could not start MQTT client for %s", s.broker_label)
        logger.info("MQTT ingest started for %s (prefix %s)", s.broker_label, s.topic_prefix)

    def stop(self, timeout: float = 5.0) -> None:
        """Disconnect and stop the worker; idempotent. Messages still queued
        are dropped here but were never acked, so the broker redelivers the
        QoS-1 ones to the next session (see module docstring)."""
        with self._lock:
            if not self._started:
                return
            self._started = False
        self._manager.remove_listener(self._on_realtime_event)
        client = self._client
        if client is not None:
            try:
                client.disconnect()
            except Exception:
                logger.debug("MQTT disconnect failed", exc_info=True)
            try:
                client.loop_stop()
            except Exception:
                logger.debug("MQTT loop_stop failed", exc_info=True)
        self._stop.set()
        for worker in (self._worker, self._status_worker):
            if worker is not None:
                worker.join(timeout=timeout)
        with self._lock:
            self._connected = False
            self._worker = None
            self._status_worker = None
            self._client = None
        logger.info("MQTT ingest stopped")

    # --- paho callbacks (network thread: must stay fast) -----------------------------

    def _on_connect(self, client, userdata, flags, reason_code, properties=None) -> None:
        if _reason_failed(reason_code):
            logger.warning("MQTT connect refused by %s: %s", self._settings.broker_label, reason_code)
            with self._lock:
                self._connected = False
            return
        with self._lock:
            self._connected = True
            self._connect_count += 1
            self._connected_mono = time.monotonic()
            if self._connect_count > 1:
                self._stats["reconnects"] += 1
        try:
            client.subscribe(protocol.ingest_subscriptions(self._settings.topic_prefix))
        except Exception:
            logger.exception("MQTT subscribe failed")
        logger.info("MQTT connected to %s", self._settings.broker_label)

    def _on_disconnect(self, client, userdata, flags=None, reason_code=None, properties=None) -> None:
        with self._lock:
            was_connected = self._connected
            self._connected = False
            session_s = (time.monotonic() - self._connected_mono
                         if was_connected and self._connected_mono is not None else None)
            if session_s is not None:
                self._short_sessions = self._short_sessions + 1 if session_s < _SHORT_SESSION_S else 0
            short_sessions = self._short_sessions
        if self._started:
            logger.warning("MQTT disconnected from %s (%s); paho will reconnect",
                           self._settings.broker_label, reason_code)
            if short_sessions >= _SHORT_SESSIONS_BEFORE_WARNING:
                # MQTT 3.1.1 gives no reason code for a session takeover, so
                # this is a heuristic; it only ever logs.
                logger.warning(
                    "MQTT connection to %s keeps dropping within %.0f s of connecting "
                    "(%d times in a row). Is another app instance using MQTT_CLIENT_ID=%r? "
                    "The id must be unique per running instance.",
                    self._settings.broker_label, _SHORT_SESSION_S, short_sessions,
                    self._settings.client_id,
                )

    def _on_message(self, client, userdata, message) -> None:
        received_at = _now()
        qos = int(getattr(message, "qos", 0) or 0)
        mid = getattr(message, "mid", None)
        with self._lock:
            self._stats["received"] += 1
            self._last_message_at = received_at
            # The ack is only meaningful on the connection that delivered the
            # message; after a reconnect the broker redelivers anything un-acked.
            generation = self._connect_count
        item = (message.topic, bytes(message.payload), received_at,
                bool(getattr(message, "retain", False)), (mid, qos, generation))
        lane = self._status_queue if self._is_status_topic(message.topic) else self._queue
        try:
            lane.put_nowait(item)
        except queue.Full:
            with self._lock:
                self._stats["queue_overflows"] += 1
            # Not acked: a QoS-1 message stays with the broker and comes back
            # on the next session resume. QoS 0 has no ack and is simply lost.
            logger.warning("MQTT ingest queue full (%d); %s message on %s",
                           self._settings.ingest_queue_max,
                           "left un-acked" if qos > 0 else "dropped", message.topic)

    # --- Worker --------------------------------------------------------------------

    def _is_status_topic(self, topic: str) -> bool:
        # Anything unparseable goes to the snapshot lane, where the ingestor
        # records it as rejected like any other malformed message.
        try:
            return protocol.parse_topic(topic, self._settings.topic_prefix).kind == protocol.KIND_STATUS
        except protocol.ProtocolError:
            return False

    def _run_worker(self, lane: queue.Queue) -> None:
        while not self._stop.is_set():
            try:
                topic, payload, received_at, retained, ack = lane.get(timeout=_WORKER_POLL_S)
            except queue.Empty:
                continue
            self.process(topic, payload, received_at, retained=retained)
            self._ack(*ack)

    def _ack(self, mid, qos: int, generation: int) -> None:
        """PUBACK a QoS-1 message now that ingest is done with it (manual_ack;
        see module docstring). Skipped when the connection that delivered it
        is gone: the broker redelivers the message on the new session and the
        redelivery is acked instead."""
        if qos <= 0 or mid is None:
            return
        with self._lock:
            client = self._client
            current = self._connected and self._connect_count == generation
        if client is None or not current:
            return
        try:
            client.ack(mid, qos)
        except Exception:
            logger.debug("MQTT ack of mid %s failed", mid, exc_info=True)

    def process(self, topic: str, payload: bytes, received_at: datetime | None = None,
                *, retained: bool = False) -> None:
        """Run one message through the ingestor and update stats. Public so
        tests can drive it synchronously; the ingestor never raises, but the
        guard keeps the worker alive even if it someday does."""
        try:
            outcome = self._ingestor.handle_message(topic, payload, received_at, retained=retained)
            key = _OUTCOME_STAT.get(outcome.status, "errors")
        except Exception:
            logger.exception("Unexpected ingest failure on %s", topic)
            key = "errors"
        with self._lock:
            self._stats[key] += 1

    # --- Downstream alerts ------------------------------------------------------------

    def _on_realtime_event(self, event: dict) -> None:
        """Realtime manager listener (runs on the API loop): publish alert
        events to the device-facing topic. paho's publish only enqueues, so
        this never blocks the loop; while disconnected paho queues QoS-1
        messages and sends them after reconnect.

        A human close (alert_closed) goes out as the §5 alert_resolved it
        implies, so the device LED clears; see protocol.device_alert_event."""
        device_event = protocol.device_alert_event(event)
        if device_event is None:
            return  # e.g. alert_acknowledged is UI-only (§5)
        client = self._client
        if client is None:
            return
        try:
            topic = protocol.alert_topic(device_event["machine_id"], self._settings.topic_prefix)
            client.publish(topic, protocol.build_alert_payload(device_event),
                           qos=protocol.ALERT_QOS, retain=True)
        except Exception:
            logger.exception("Failed to publish alert for %s", event.get("machine_id"))

    # --- Introspection ---------------------------------------------------------------

    def connected_for_s(self) -> float | None:
        """Seconds the current broker connection has been up; None while not
        started or disconnected. Restarts at every (re)connect, which is what
        the device-silence watchdog's grace window keys off: right after a
        connect the retained-status replay and QoS-1 backlog would make live
        nodes look stale (device-health design, decision 6)."""
        with self._lock:
            if not self._started or not self._connected or self._connected_mono is None:
                return None
            return time.monotonic() - self._connected_mono

    def status(self) -> dict:
        """The §8 /telemetry/status body."""
        with self._lock:
            stats = dict(self._stats)
            connected = self._connected
            started_at = self._started_at
            last_message_at = self._last_message_at
        stats["queue_depth"] = self._queue.qsize() + self._status_queue.qsize()
        return {
            "enabled": True,
            "connected": connected,
            "broker": self._settings.broker_label,
            "topic_prefix": self._settings.topic_prefix,
            "started_at": protocol.format_utc(started_at) if started_at else None,
            "last_message_at": protocol.format_utc(last_message_at) if last_message_at else None,
            "stats": stats,
        }


def build_service(settings: TelemetrySettings, **kwargs) -> MqttIngestService:
    """Production wiring: the cached predictor and one SQLite connection per
    message (the worker thread owns it for that message only)."""
    from src.storage.db import get_connection

    def _predictor():
        # Lazy, like src/api/routes/ingestion.py: building the predictor cache
        # loads the model; a missing model must surface per message (recorded
        # as an ingest error), not at app startup.
        from src.api.routes.predictions import _cached_predictor

        return _cached_predictor()

    ingestor = TelemetryIngestor(
        predictor_provider=_predictor,
        connection_factory=get_connection,
        topic_prefix=settings.topic_prefix,
        auto_register=settings.auto_register,
    )
    return MqttIngestService(settings, ingestor, **kwargs)
