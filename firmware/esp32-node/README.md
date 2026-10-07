# MaintainIQ ESP32 vibration node

Firmware for an ESP32 + ADXL345 sensor node that feeds MaintainIQ's live
telemetry path over MQTT. It is the hardware counterpart of the simulator
(`python -m src.telemetry.simulator`) and speaks exactly the same wire format,
defined in [`design/M6_LIVE_TELEMETRY.md`](../../design/M6_LIVE_TELEMETRY.md)
(topics in §2, snapshot §3, status/LWT §4, alerts §5, node requirements §11).
`src/telemetry/protocol.py` is the reference encoder/decoder; this firmware is
a hand-written re-implementation of it.

> **Status:** compile-checked with PlatformIO (`espressif32` 6.x, Arduino-ESP32
> 2.0.x). It has **not** been run on hardware yet; the wire format is checked
> in CI by `tests/telemetry/test_firmware_payload_contract.py`, which compares
> the firmware source against the Python reference encoder.

## What it does

```
ADXL345 ──SPI/FIFO──▶ sampler task (core 1) ──queue──▶ network task (core 0) ──MQTT──▶ Mosquitto ──▶ MaintainIQ
                       one 4096-sample window          Wi-Fi, NTP, MQTT,
                       every 2 s, never blocks         edge buffer (LittleFS),
                                                       status heartbeat, alerts → LED
```

- Samples X (horizontal) and Z (vertical) at 3200 Hz, full resolution ±16 g
  (3.9 mg/LSB), through the ADXL345 32-entry FIFO with a watermark interrupt.
- Every `SNAPSHOT_INTERVAL_MS` (default 2 s) captures one 4096-sample window
  and publishes it to `maintainiq/v1/telemetry/{machine_id}` as `i16le-b64`
  with `scale = 0.0039`.
- Publishes a retained status heartbeat to `maintainiq/v1/status/{device_id}`
  on connect, every `HEARTBEAT_INTERVAL_MS` (default 10 s) and right after the
  edge buffer has been flushed. The broker publishes the LWT
  `{"v":1,"device_id":"…","online":false}` if the node disappears.
- Subscribes to `maintainiq/v1/alerts/{machine_id}` and drives the LED on GPIO 2:
  **solid** = critical/faulty, **slow blink** (1 s on / 1 s off) = degrading,
  **off** = healthy or resolved. The alert is retained, so the LED shows the
  right state again after a reboot.

## Hardware and wiring

The ADXL345 is on SPI (VSPI), not I2C. The datasheet recommends SPI at 2 MHz
or more for the 3200/1600 Hz output data rates. This is why the node differs
from the I2C row in `design/diagrams/esp32_pin_interface_table.puml`
(contract §11).

| ADXL345 breakout pin | ESP32 (esp32dev) | Notes |
|---|---|---|
| VCC / VS / VDDIO | 3V3 | 3.3 V only |
| GND | GND | |
| CS | GPIO 5 | VSPI CS; held low = SPI mode |
| SCL / SCLK | GPIO 18 | VSPI SCK |
| SDO | GPIO 19 | VSPI MISO |
| SDA / SDI | GPIO 23 | VSPI MOSI |
| INT1 | GPIO 4 | FIFO watermark interrupt (polling backs it up) |
| INT2 | not connected | |
| — | GPIO 2 | Status LED (built in on most devkits) |

Every pin can be changed in `include/config.h`. Mount the sensor rigidly
(screwed or epoxied, not taped) as close to the bearing housing as you can.
Align the X axis with the horizontal radial direction and Z with the vertical
direction, to match the XJTU-SY channel convention.

## Build and flash

Requires [PlatformIO](https://platformio.org/) (`pip install platformio`, or
the VS Code extension).

```bash
cd firmware/esp32-node
cp include/config.example.h include/config.h   # gitignored: holds Wi-Fi + broker secrets
# edit include/config.h: WIFI_SSID/PASSWORD, MQTT_HOST, MACHINE_ID, MACHINE_SPEED_RPM/LOAD_KN
pio run                      # build (the first run downloads the toolchain, ~5 min)
pio run -t upload            # flash over USB
pio device monitor           # serial log at 115200
```

The first build may fail with `HTTPClientError` / `CERTIFICATE_VERIFY_FAILED`
behind a TLS-intercepting corporate proxy. If it does, install
`pip-system-certs` into the same Python environment as PlatformIO, so that it
trusts the OS certificate store.

Many values are checked at compile time in `src/app_config.h`. For example,
the interval must be longer than the 1280 ms capture window, and the MQTT
buffer must hold a full snapshot. A bad config fails the build instead of
failing in the field. At boot the node also refuses to start (fast LED blink
plus a serial message) if `DEVICE_ID` or `MACHINE_ID` would fail the backend's
id check.

### Memory and flash budget

| Item | Size |
|---|---|
| Snapshot struct (2 × 4096 int16 + metadata) | ~16.6 KB |
| Snapshot pool (3 buffers, heap) | ~50 KB |
| Snapshot JSON buffer (`kSnapshotJsonMax`) | 22,616 B |
| PubSubClient buffer (`kMqttBufferSize`) | ~22.8 KB |
| Flush scratch record | ~16.6 KB |
| Snapshot JSON on the wire | ~22.3 KB (2 × 10,924 B base64 + ~450 B fields) |
| LittleFS partition (`partitions.csv`) | 2 MB, about 110 records; capacity 50 by default |

PubSubClient builds the whole PUBLISH packet in its own buffer. The default
buffer is 256 bytes, so the firmware enlarges it with `setBufferSize`. The
required size is `5 (fixed header) + 2 + topic + payload`. It is computed and
`static_assert`ed in `app_config.h`, and must stay at or below 65,535 because
PubSubClient's size argument is `uint16_t`.

## How buffering works

The sampler and the network side are separate FreeRTOS tasks on separate
cores. The sampler never waits on the network. It hands each captured window
to the network task through a queue with a zero timeout. If the network task
is busy (for example during a slow publish), the sampler reuses the **oldest**
queued window and counts it as dropped, so its capture timing never slips.

The network task handles each new snapshot like this:

1. If MQTT is connected **and** the edge buffer is empty, it publishes the
   snapshot live with `"buffered": false`.
2. Otherwise, it appends the snapshot to the **edge buffer**. This also
   happens when the live publish fails.
3. While connected, it flushes the buffer **oldest-first**, one record per loop
   iteration, with `"buffered": true`. As long as anything is still buffered,
   new snapshots join the back of the buffer, so the backend receives each
   `(device_id, boot_id)` stream in `seq` order. When the buffer drains, the
   node publishes a status message.

Edge buffer details:

- **Storage:** one LittleFS file per snapshot (`/eb/<index>.rec`). Each file
  holds a header, the raw snapshot and a CRC-32. Records are written to a temp
  file and then renamed, so a power cut never leaves a half-written record
  that looks valid.
- **Bounds:** at most `EDGE_BUFFER_CAPACITY` records (default 50, about 100 s
  of data at 2 s per snapshot), and LittleFS must keep `FS_RESERVE_BYTES`
  free. When either limit is reached, the oldest record is dropped.
- **Counting drops:** every drop is counted in `buffer_dropped_total` in the
  status message (since boot, per contract §4). A lifetime total is also
  persisted with `Preferences` (NVS) and printed at boot.
- **Survives reboots:** records keep the `device_id`, `machine_id`, `boot_id`
  and `seq` they were captured with. After a reboot, the backlog is still
  flushed under its original identity. The new boot gets a new `boot_id`, so
  the `(device_id, boot_id, seq)` idempotency key never collides.
- **RAM fallback:** if LittleFS cannot be mounted or formatted, the buffer
  falls back to a RAM ring of `RAM_BUFFER_CAPACITY` (default 3) snapshots. That
  ring does not survive a reboot.

`seq` goes up by one for every snapshot that is actually captured, whether or
not it is ever delivered. Each snapshot also carries its `seq` inside its
buffered record. A dropped snapshot therefore shows up in the backend's
`transmission_success_rate` KPI as a gap in the sequence.

`boot_id` is 16 hex characters: an NVS boot counter followed by a random
word. It changes on every boot even if the early-boot random number generator
is weak.

### Time

The ESP32 devkit has no battery-backed clock. Until SNTP has synced (UTC,
`NTP_SERVER_1/2`), snapshots carry `"time_synced": false, "sampled_at": null`,
and the backend uses its own receive time for them. After sync, `sampled_at`
is the start of the capture window in the form `YYYY-MM-DDTHH:MM:SS.mmmZ`, and
the status message gains `reported_at`.

## Known limitations

- **Sample rate (by design, not fixed here):** the RUL model was trained on
  XJTU-SY data sampled at 25.6 kHz. The ADXL345 tops out at 3.2 kHz, so its
  feature vectors will usually be flagged out-of-distribution by the predictor,
  and its RUL estimates should not be trusted. The pipeline, alerts, device
  status and system KPIs all work regardless. A production node needs a
  wide-band sensor, such as an ADXL1002 or an IEPE accelerometer with an I2S
  ADC.
- **QoS:** the contract specifies QoS 1, but PubSubClient can only publish at
  QoS 0. A snapshot counts as sent once its PUBLISH packet has been written to
  TCP. Subscriptions (alerts) and the LWT do use QoS 1. Snapshots lost on the
  device-to-broker link appear as `seq` gaps in the backend KPIs. Unless
  Mosquitto has `queue_qos0_messages true`, QoS 0 telemetry is also **not**
  queued for the app's persistent session while the app is down. If that
  cloud-side buffering matters, either set that option or switch the
  telemetry publish to ESP-IDF's `esp-mqtt` client, which supports QoS 1 and
  an outbox.
- **No TLS / no OTA yet:** the node connects with plain MQTT on 1883, using
  username and password when both are set. Use it only on the dev or bench
  network. TLS would need `WiFiClientSecure` plus a CA certificate in
  `config.h`.
- **Counters:** `publish_attempts_total` and `publish_failures_total` count
  snapshot publishes (live and flushed), not status heartbeats.

## Verifying against the simulator-backed stack

Use the simulator to bring the stack up first. Once you know the backend side
works, any difference you see comes from the node.

```bash
# from the repo root
just broker                                   # Mosquitto on :1883
MQTT_BROKER_HOST=localhost just serve         # app with MQTT ingest on
just simulate --devices 1 --count 20          # sanity check: simulated node appears
```

Then flash the node with `MQTT_HOST` set to the LAN IP of the machine running
the broker. The Docker broker binds to `127.0.0.1` by default, so first set
`MQTT_BIND=0.0.0.0` in the repo's `.env` and re-run `just broker` (isolated
bench network only — the dev listener is anonymous):

1. **Serial log:** look for `ADXL345 ready`, `Wi-Fi up`, and no
   `MQTT connect … failed` loops.
2. **Raw MQTT:**
   `docker compose exec mosquitto mosquitto_sub -v -t 'maintainiq/v1/status/#'`
   should show a retained status with `"online":true` every 10 s. Subscribing
   to `maintainiq/v1/telemetry/#` shows a ~22 KB snapshot every 2 s.
3. **Backend:** `GET /api/telemetry/devices` lists the node.
   `GET /api/telemetry/status` shows `accepted` going up and `rejected`
   staying at 0. A rejected row in `telemetry_messages.error` names the
   contract rule that failed.
4. **Buffering:** `just broker-stop`, wait about 30 s (the node logs
   `publish failed` / `MQTT disconnected`, and the broker has already fired
   the LWT), then `just broker`. The node reconnects with backoff, flushes the
   backlog with `"buffered":true` in `seq` order, then publishes a status
   with `buffer_depth: 0`. `GET /api/kpis/detail` shows `edge_buffer_health`
   with a non-zero `buffered_share`.
5. **LED:** publish a retained alert by hand:
   `docker compose exec mosquitto mosquitto_pub -r -q 1 -t maintainiq/v1/alerts/esp32-01 -m '{"v":1,"type":"alert_created","machine_id":"esp32-01","severity":"high","health_state":"critical","status":"open","alert_id":1,"at":"2026-10-06T12:00:00Z"}'`
   turns the LED solid. Publishing `"type":"alert_resolved"` turns it off.

## Source layout

| File | Role |
|---|---|
| `include/config.example.h` | Every tunable value (Wi-Fi, broker, ids, intervals, pins, buffer sizes) |
| `src/app_config.h` | Derived constants plus `static_assert`s (payload and MQTT buffer sizing) |
| `src/adxl345.*` | SPI register driver, FIFO stream mode, watermark interrupt |
| `src/sampler.*` | Capture task: window timing, `seq`, drop-oldest hand-off |
| `src/edge_buffer.*` | LittleFS FIFO with RAM fallback and persisted drop counter |
| `src/net_task.*` | Wi-Fi, NTP, MQTT (LWT, backoff, heartbeat), flush ordering, alert subscription |
| `src/payload.*` | Contract JSON encoders (snapshot, status, LWT) and base64 of int16 little-endian |
| `src/status_led.*` | Alert-to-LED mapping and blink timer |
| `src/wallclock.*` | SNTP-backed UTC clock and sync detection |
| `partitions.csv` | 1.875 MB app partition and 2 MB LittleFS partition |
