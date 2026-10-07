# M6 — Live telemetry over MQTT (contract)

Status: implementation contract. Every M6 component (backend ingest, KPIs,
simulator, ESP32 firmware, UI, infra) MUST follow this document. If code and
this document disagree, fix one of them — do not let them drift.

## 1. Goal and scope

Replace the M1 dataset input with a live telemetry feed, without changing the
M2–M5 pipeline:

```
ESP32 node / simulator ──MQTT──▶ Mosquitto ──▶ src/telemetry/mqtt_service.py
   (edge buffer on                               │ (paho network thread → bounded queue)
    network drop)                                ▼
                                       src/telemetry/ingest.py  (single worker thread)
                                         validate → decode → readings row
                                         → RealTimeRULPredictor.predict()   (public API only)
                                         → rul_store.persist_prediction + log_inference
                                         → src/prediction/pipeline.handle_prediction  ← SAME path as replay
                                         → telemetry_messages row (KPI source)
alerts (any source) ── realtime manager listener ──▶ maintainiq/v1/alerts/{machine_id} (downstream)
```

**Hard boundary — the ML model is being worked on in parallel. Do NOT modify:**
`src/prediction/rul_realtime.py`, `src/training/**`, `models/**`,
`src/ingestion/xjtu_sy.py`, `src/features/**`. Call the predictor only through
`src.api.routes.predictions._cached_predictor()` and its public
`predict(machine_id, horizontal, vertical, sample_rate_hz, speed_rpm, load_kn)`.

The `/demo` fault-injection endpoint stays as a manual tool; the simulator
becomes the primary "live" demo source.

## 2. Topics (prefix configurable, default `maintainiq/v1`)

| Direction | Topic | QoS | Retained | Payload |
|---|---|---|---|---|
| up | `{prefix}/telemetry/{machine_id}` | 1 | no | Snapshot (§3) |
| up | `{prefix}/status/{device_id}` | 1 | yes | Device status / heartbeat (§4); also the LWT |
| down | `{prefix}/alerts/{machine_id}` | 1 | yes | Alert event (§5) |

`machine_id` and `device_id` must match `^[A-Za-z0-9_.-]{1,64}$` (no `/`, `+`, `#`).
The ingest service subscribes to `{prefix}/telemetry/+` and `{prefix}/status/+`.
The `machine_id` in the topic must equal the one in the payload, else reject.

## 3. Snapshot payload (JSON, UTF-8)

```json
{
  "v": 1,
  "device_id": "esp32-a1b2c3",
  "machine_id": "sim-01",
  "boot_id": "5f3a9c1e",
  "seq": 1234,
  "sampled_at": "2026-10-06T12:00:00.000Z",
  "time_synced": true,
  "buffered": false,
  "sample_rate_hz": 25600.0,
  "speed_rpm": 2100.0,
  "load_kn": 12.0,
  "encoding": "f32le-b64",
  "scale": 1.0,
  "horizontal": "<base64>",
  "vertical": "<base64>"
}
```

| Field | Rule |
|---|---|
| `v` | must be `1` |
| `device_id`, `machine_id` | id regex above |
| `boot_id` | 1–32 chars `[A-Za-z0-9]`; random per device boot. `(device_id, boot_id, seq)` is the idempotency key |
| `seq` | int ≥ 0, strictly increasing per `(device_id, boot_id)`, +1 per captured snapshot (captured — not sent; gaps = lost snapshots) |
| `sampled_at` | ISO-8601 UTC capture time; may be `null` when `time_synced` is false (ingest then uses receive time) |
| `time_synced` | bool; false = device clock not NTP-synced |
| `buffered` | bool; true = this snapshot was held in the edge buffer during a network outage and is being flushed late |
| `sample_rate_hz` | > 0, ≤ 100000 |
| `speed_rpm` | > 0 ; `load_kn` ≥ 0 |
| `encoding` | `f32le-b64` (little-endian float32, base64), `i16le-b64` (little-endian int16 counts, base64), or `json` (plain number arrays) |
| `scale` | multiplier applied after decode (g per LSB for `i16le-b64`); default 1.0; must be > 0 |
| `horizontal`, `vertical` | equal length, 32 ≤ n ≤ 65536 samples per axis, all finite after decode |

Max MQTT payload: 1 MiB (broker `message_size_limit`). A 32768-sample XJTU-SY
snapshot as `f32le-b64` is ≈350 KB.

Device obligations: capture continues while offline; snapshots captured during
an outage go into a bounded FIFO edge buffer (drop-OLDEST when full, counting
drops); on reconnect the buffer is flushed oldest-first with `buffered: true`
BEFORE any newer live snapshot is sent, so ingest sees each
`(device_id, boot_id)` in `seq` order.

## 4. Device status payload (retained, also used as LWT)

```json
{
  "v": 1, "device_id": "esp32-a1b2c3", "machine_id": "sim-01", "boot_id": "5f3a9c1e",
  "online": true, "reported_at": "2026-10-06T12:00:00Z",
  "uptime_s": 3600, "firmware": "maintainiq-esp32/0.1.0",
  "snapshot_interval_s": 2.0, "heartbeat_interval_s": 10.0,
  "buffer_depth": 0, "buffer_capacity": 50, "buffer_dropped_total": 0,
  "publish_attempts_total": 1800, "publish_failures_total": 3,
  "wifi_rssi_dbm": -61
}
```

LWT (set at CONNECT): `{"v":1,"device_id":"…","online":false}` — every other
field is optional. All counters are cumulative since boot.
`publish_attempts_total` / `publish_failures_total` count **snapshot**
publishes only (not status heartbeats), on both the simulator and the
firmware, so `device_reported_failure_rate` means the same thing for both. A device publishes
status at connect, every `heartbeat_interval_s`, and right after a buffer flush.

Ingest treats a device as **online** iff its latest status has `online: true`
AND the last message of any kind from it is younger than
`3 × heartbeat_interval_s` (default heartbeat 10 s if unknown).

Since device health (§12) this rule is implemented once, in
`src/telemetry/device_health.device_state`, which refines it into four states:
`never_reported` (no status ever stored, `payload_json IS NULL`), `offline`
(LWT/clean shutdown, or nothing heard for ≥ 3 × hb), `stale` (heard
1.5 × hb – 3 × hb ago) and `online` (< 1.5 × hb). The boolean "online" above
is `state ∈ {online, stale}`, unchanged for the API's `online` field and for
`cloud_sync_health.devices_online`.

## 5. Downstream alert payload

Published (QoS 1, retained) to `{prefix}/alerts/{machine_id}` for every
realtime alert event regardless of origin (MQTT, replay, /demo):

```json
{"v": 1, "type": "alert_created|alert_escalated|alert_resolved", "machine_id": "sim-01",
 "severity": "critical", "health_state": "critical", "status": "open", "alert_id": 42,
 "at": "2026-10-06T12:00:00Z"}
```

Mechanism: `src/realtime/manager.py` gains `add_listener(fn)` / `remove_listener(fn)`;
`broadcast()` calls every listener with the event dict (listener exceptions are
logged and swallowed). The MQTT service registers a listener on start that
publishes alert-type events and unregisters on stop. ESP32 firmware subscribes
to its own machine's alert topic and drives the status LED.

A human close of an alert (`alert_closed`,
design/2026-10-07-prediction-feedback-design.md decision 16) is published as an
`alert_resolved` payload for that alert (`protocol.device_alert_event`), since no
reading-driven resolve will ever follow it and the retained topic would
otherwise keep the LED on. The payload vocabulary above is unchanged;
`alert_closed` and `alert_feedback_recorded` themselves never appear on MQTT.

## 6. Storage (new tables, new migration version 2 in `src/storage/migrations.py`; also in `SCHEMA` in `src/storage/db.py`)

```sql
CREATE TABLE IF NOT EXISTS telemetry_messages (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id     TEXT NOT NULL,
    boot_id       TEXT,
    seq           INTEGER,
    machine_id    TEXT,
    sampled_at    TEXT,            -- device capture time (or received_at when not time-synced)
    received_at   TEXT NOT NULL,   -- ingest receive time, UTC ISO-8601
    buffered      INTEGER NOT NULL DEFAULT 0,
    time_synced   INTEGER NOT NULL DEFAULT 1,
    status        TEXT NOT NULL CHECK(status IN ('accepted','rejected','error')),
    error         TEXT,
    reading_id    INTEGER REFERENCES readings(id),
    latency_ms    REAL,            -- ingest processing time (decode→fan-out)
    payload_bytes INTEGER,
    UNIQUE(device_id, boot_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_telemetry_received ON telemetry_messages(received_at);
CREATE INDEX IF NOT EXISTS idx_telemetry_device ON telemetry_messages(device_id, boot_id, seq);

CREATE TABLE IF NOT EXISTS device_status (
    device_id               TEXT PRIMARY KEY,
    machine_id              TEXT,
    boot_id                 TEXT,
    online                  INTEGER NOT NULL DEFAULT 0,
    last_seen_at            TEXT NOT NULL,   -- last message of ANY kind from this device
    reported_at             TEXT,
    uptime_s                REAL,
    firmware                TEXT,
    snapshot_interval_s     REAL,
    heartbeat_interval_s    REAL,
    buffer_depth            INTEGER,
    buffer_capacity         INTEGER,
    buffer_dropped_total    INTEGER,
    publish_attempts_total  INTEGER,
    publish_failures_total  INTEGER,
    wifi_rssi_dbm           REAL,
    payload_json            TEXT
);
```

Duplicates (same idempotency key) are NOT inserted again; they are counted in
the service's in-memory stats (`duplicates`). Rejected messages with no usable
`device_id` use `device_id = '?'`.

Live readings: inserted into `readings` with `dataset = 'live_mqtt'`,
`cycle = COALESCE(MAX(cycle), -1) + 1` for that machine, `elapsed_minutes`
= minutes since the machine's first live reading (0 for the first),
`timestamp = sampled_at`, the six summary columns from the feature dict
(`h_rms`, `h_kurtosis`, `v_rms`, `v_kurtosis`, `cross_axis_rms_ratio`,
`cross_axis_correlation`), `features_json` = the full base feature dict
(computed by `src.ingestion.xjtu_sy.extract_snapshot_features` — read-only
use), `rul_minutes = NULL`.

Unknown machine: if `MQTT_AUTO_REGISTER` is truthy (default `1`), insert into
`machines` (`machine_id`, `bearing_id = machine_id`, `speed_rpm`, `load_kn`,
`dataset = 'live_mqtt'`, `is_documented_failure = 0`); otherwise reject with
`error = 'unknown machine'`.

Prediction fan-out uses `source = 'mqtt'` for alerts:
`pipeline.handle_prediction(conn, result, source="mqtt", features=base, timestamp=sampled_at)`.
A predictor exception is recorded as `rul_store.log_inference(... status='error')`
if that function supports it, else a `telemetry_messages.status='error'` row —
the reading row is still kept. Ingest never crashes the worker thread.

## 7. Configuration (env)

| Var | Default | Meaning |
|---|---|---|
| `MQTT_BROKER_HOST` | empty | empty ⇒ MQTT ingest disabled (app runs as before) |
| `MQTT_BROKER_PORT` | `1883` | 8883 when TLS |
| `MQTT_USERNAME` / `MQTT_PASSWORD` | empty | auth used only when both set |
| `MQTT_TLS` | `0` | `1` ⇒ TLS; `MQTT_CA_CERT` optional CA bundle path |
| `MQTT_CLIENT_ID` | `maintainiq-ingest` | fixed id + `clean_session=False` so the broker queues QoS-1 telemetry while the app is down (cloud-side buffer) |
| `MQTT_TOPIC_PREFIX` | `maintainiq/v1` | |
| `MQTT_AUTO_REGISTER` | `1` | auto-create unknown machines |
| `MQTT_INGEST_QUEUE_MAX` | `256` | bounded hand-off queue; overflow ⇒ message dropped + counted (`queue_overflows`). QoS-1 messages are acked manually, only after ingest handled them, so the broker's in-flight window throttles a reconnect backlog; a QoS-1 message that overflows is left un-acked and redelivered on the next session |

## 8. API (all under `/api`, any authenticated role)

`GET /api/telemetry/status`
```json
{"enabled": true, "connected": true, "broker": "mosquitto:1883", "topic_prefix": "maintainiq/v1",
 "started_at": "…", "last_message_at": "…",
 "stats": {"received": 0, "accepted": 0, "rejected": 0, "duplicates": 0, "errors": 0,
           "queue_overflows": 0, "queue_depth": 0, "reconnects": 0}}
```
When disabled: `{"enabled": false, "connected": false, "broker": null, …, "stats": {…zeros}}`.

`GET /api/telemetry/devices` → list of device rows:
```json
[{"device_id": "…", "machine_id": "…", "online": true, "last_seen_at": "…", "firmware": "…",
  "uptime_s": 0, "buffer_depth": 0, "buffer_capacity": 50, "buffer_dropped_total": 0,
  "publish_attempts_total": 0, "publish_failures_total": 0, "wifi_rssi_dbm": -61,
  "snapshot_interval_s": 2.0, "heartbeat_interval_s": 10.0,
  "state": "online", "silent_for_s": 3.2, "expected_heartbeat_s": 10.0, "open_incident_id": null}]
```
`?machine_id=` filters the list. The last four fields come from device health
(§12), as do `GET /api/telemetry/devices/{device_id}` (one row plus
`recent_incidents`), `GET /api/telemetry/incidents?status=&device_id=&limit=`,
`POST /api/telemetry/incidents/{id}/acknowledge`, and the `device_watchdog`
block on `/status`:
`{"enabled": true, "interval_s": 5.0, "grace_s": 30.0, "armed": true, "last_tick_at": "…"}`.

`GET /api/kpis/detail` → `system` section now computed (§9).

## 9. System KPIs (`src/kpi/calculations.py: system_kpis(conn, *, now=None, window_minutes=60.0)`)

If `telemetry_messages` and `device_status` are both empty, every KPI keeps
`{"status": "not_applicable", "reason": "no live telemetry received yet — start the MQTT simulator (just simulate) or connect an ESP32 node"}`.
Otherwise each KPI is `{"status": "available", "window_minutes": W, …}`.
The window filters on `received_at >= now - W` for message-based figures.

"Accepted" below means **delivered**: `status = 'accepted'`, or
`status = 'error'` with a non-NULL `reading_id` (the snapshot reached the
database and only the prediction failed — e.g. no model file during a
retrain). These KPIs describe the transport path, so a model outage must not
read as lost or uncollected snapshots.

- **sensor_collection_rate** — per device: `collected / expected`, where
  `collected` = distinct accepted `(boot_id, seq)` whose `sampled_at` is in the
  window, `expected` = `effective_window_s / snapshot_interval_s`,
  `effective_window_s` = seconds from max(window start, device's first message
  in window) to `now`; rate clamped to [0, 1]; null when interval unknown.
  Payload: `{"rate": fleet mean, "devices": [{"device_id","collected","expected","rate"}]}`.
- **transmission_success_rate** — from sequence gaps: per `(device_id, boot_id)`
  in window, `received = count distinct seq (accepted)`, `sent = max_seq - min_seq + 1`;
  fleet `rate = Σreceived / Σsent`. Also report device-side
  `publish_failures_total / publish_attempts_total` from latest status as
  `device_reported_failure_rate`. Payload: `{"rate", "received", "expected", "lost", "device_reported_failure_rate", "devices": [...]}`.
- **edge_buffer_health** — from latest `device_status` per device:
  `utilization = buffer_depth / buffer_capacity`; per-device `state` =
  `critical` if utilization ≥ 0.9, `degraded` if ≥ 0.5 or `buffer_dropped_total > 0`,
  else `ok`; a device whose row has no buffer figures (created by its first
  snapshot, never a status with buffer fields) is `unknown` and does not take
  part in the worst-of; fleet `state` = worst (`unknown` when telemetry exists
  but no device has published its buffer figures yet). Also `buffered_share` = fraction of window
  accepted messages with `buffered = 1`. Payload: `{"state", "buffered_share", "dropped_total", "devices": [{"device_id","depth","capacity","utilization","dropped_total","state"}]}`.
- **cloud_sync_health** — `lag_p50_s` / `lag_p95_s` of `received_at - sampled_at`
  over accepted, non-buffered, time-synced messages in window (nearest-rank, as
  `src/observability/telemetry.py`); `devices_online` / `devices_total`
  (online rule §4); `last_message_age_s` (now − max received_at);
  `rejected_rate` = rejected / all in window; `state` = `ok` | `degraded`
  (any device offline, or p95 lag > 30 s, or rejected_rate > 0.05) | `down`
  (no device online). Payload: `{"state", "lag_p50_s", "lag_p95_s", "devices_online", "devices_total", "last_message_age_s", "rejected_rate"}`.

The `/api/kpis/detail` route passes the request DB connection. Response
contract keys stay identical (`sensor_collection_rate`,
`transmission_success_rate`, `edge_buffer_health`, `cloud_sync_health`).

## 10. Simulator (`python -m src.telemetry.simulator`, `just simulate`)

Simulates N ESP32 nodes as real MQTT clients (one paho client per device,
own LWT, own boot_id), publishing §3/§4 payloads, with a simulated edge
buffer and network outages:

- `--broker localhost --port 1883 [--username --password --tls]`, `--topic-prefix`
- `--devices 3`, `--machine-prefix sim` (machine `sim-01`…, device `simdev-01`…)
- `--source synthetic|xjtu` ; `--data-dir` (default `local_data/xjtu_full/XJTU-SY_Bearing_Datasets`) — xjtu replays real run-to-failure CSV snapshots in order, one bearing per device (25.6 kHz, f32le-b64), using the bearing's real speed/load; synthetic generates shaft harmonics + noise + an outer-race fault impulse train whose amplitude grows over `--life-snapshots` (default 300) then holds at failure.
- `--interval 2.0` (snapshot period, s), `--heartbeat 10.0`, `--samples 32768` (synthetic), `--sample-rate 25600`
- `--outage-every 0` (s, 0 = never), `--outage-duration 20` (s), `--outage-jitter`; during an outage the client really disconnects (`loop_stop` + socket drop without DISCONNECT so the broker fires the LWT), capture continues into the bounded edge buffer (`--buffer-capacity 50`, drop-oldest), and on reconnect the buffer is flushed oldest-first with `buffered: true` before live data resumes.
- `--loss-rate 0.0` — probability a snapshot is captured but lost (seq consumed, never sent), to exercise transmission KPIs.
- `--count` (snapshots per device, 0 = forever), `--seed`, `--encoding f32le-b64|i16le-b64|json`.
- Pure logic (signal generation, edge buffer, payload building) lives in importable functions/classes with unit tests; network I/O is a thin layer.

## 11. ESP32 node (`firmware/esp32-node/`, PlatformIO, Arduino framework)

- ADXL345 over **SPI** (VSPI: SCK 18, MISO 19, MOSI 23, CS 5) at 3200 Hz ODR,
  FIFO stream mode with watermark — the datasheet recommends SPI ≥ 2 MHz for
  3200/1600 Hz ODR, which is why this deviates from the I2C row of
  `design/diagrams/esp32_pin_interface_table.puml`. Horizontal = X, vertical = Z.
- Snapshot: 4096 samples/axis, `i16le-b64`, `scale = 0.0039` g/LSB (full-res ±16 g).
- FreeRTOS: sampling task (core 1) → queue → network task (core 0). Sampling never blocks on the network.
- Edge buffer: LittleFS FIFO of serialized snapshots, bounded by count and free space, drop-oldest with persisted counters; RAM-only fallback if LittleFS mount fails.
- Telemetry is published at **QoS 0**: PubSubClient cannot publish QoS 1
  (LWT and the alert subscription are QoS 1). Device→broker losses still
  surface as `seq` gaps in the transmission KPI, and the broker sets
  `queue_qos0_messages true` so snapshots published while the app is down are
  still queued for its persistent session.
- Wi-Fi + MQTT (PubSubClient with enlarged buffer) with exponential reconnect backoff, LWT, retained status heartbeat, NTP time (`time_synced=false` + `sampled_at=null` until synced), downstream alert subscription → GPIO 2 LED (solid = critical/faulty, slow blink = degrading, off = healthy/resolved).
- Config via `include/config.h` (copied from `config.example.h`, gitignored).
- **Known limitation (documented, not fixed here):** the RUL model was trained on 25.6 kHz XJTU-SY data; an ADXL345 tops out at 3.2 kHz, so its feature vectors will typically be flagged out-of-distribution by the predictor. Production nodes need a wide-band sensor (e.g. ADXL1002 / IEPE + I2S ADC). The pipeline and KPIs work regardless.

## 12. Device health / silence

Full design: `design/2026-10-06-device-health-design.md`. Summary:

- **States** (§4): one rule in `src/telemetry/device_health.py`, used by ingest,
  `/api/telemetry/devices`, the system KPIs and the watchdog.
- **Incidents**: table `device_incidents` (migration 3), separate from
  `alerts`. A node in state `offline` without an open incident gets one
  (`kind = 'lwt'` if the stored flag is 0, else `'silent'`); `stale` and
  `never_reported` never open one. The incident resolves when the node is
  heard again (online or stale); a later silence opens a new row. A partial
  unique index allows one open incident per device.
- **Watchdog**: `src/telemetry/watchdog.py`, a job on the generic background
  scheduler (`src/background/scheduler.py`), which runs only when
  `MAINTAINIQ_SWEEP_INTERVAL_S > 0`. It is the only writer of
  `status`/`resolved_at`. It acts only while MQTT ingest is enabled, connected,
  and has been connected for `DEVICE_SILENT_GRACE_S` (default 30 s) — while we
  are deaf, or replaying retained status after a (re)connect, every node would
  look silent. Its first armed tick migrates the DB lazily
  (`ingest.ensure_telemetry_schema`).
- **Paging**: when an incident opens, admins and supervisors are emailed and
  admins, supervisors and operators get a Web Push
  (`dispatch.notify_device_incident`; push via `src/notifications/push.py`,
  only when the VAPID keys are configured). Every delivery is a
  `notifications` row with `device_incident_id` and `channel` (`'email'` or
  `'push'`). Never on recovery, and not again if the same node opened another
  incident within 15 minutes. See
  `design/2026-10-07-mobile-operator-pwa-design.md` for the push channel.
- **Realtime**: `device_offline` / `device_online` events
  `{type, device_id, machine_id|null, incident, at}`, broadcast by the
  scheduler. They are not in `ALERT_EVENT_TYPES`, so they are never
  republished to devices over MQTT.
- **Acknowledge**: any role, idempotent, allowed on resolved incidents; no
  realtime event (the Devices page polls).
