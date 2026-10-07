// MaintainIQ ESP32 node — per-device configuration template.
//
// Copy this file to include/config.h and edit it. config.h is gitignored
// because it holds the Wi-Fi password and broker credentials; this example is
// the documented, committed source of truth for which knobs exist.
//
// Everything here is compile-time on purpose: a node is flashed for one
// machine and one network, and compile-time values let app_config.h
// static_assert the invariants (interval vs capture time, MQTT buffer size,
// payload limit) instead of discovering them in the field.
#pragma once

// ---- Wi-Fi --------------------------------------------------------------------
#define WIFI_SSID "your-ssid"
#define WIFI_PASSWORD "your-password"

// ---- MQTT broker (the Mosquitto service from docker-compose.yml) ---------------
// Use the LAN IP / hostname of the machine running `docker compose up`.
#define MQTT_HOST "192.168.1.10"
#define MQTT_PORT 1883
// Leave both empty for the anonymous dev listener. Auth is only sent when
// both are non-empty (same rule as the backend, contract section 7).
#define MQTT_USERNAME ""
#define MQTT_PASSWORD ""
// Must match MQTT_TOPIC_PREFIX on the backend.
#define MQTT_TOPIC_PREFIX "maintainiq/v1"

// ---- Identity (contract section 2: ^[A-Za-z0-9_.-]{1,64}$) --------------------------
// Empty DEVICE_ID => derived from the Wi-Fi MAC as "esp32-xxxxxx".
#define DEVICE_ID ""
// The machine this node is bolted to. Unknown machines are auto-registered by
// the backend when MQTT_AUTO_REGISTER=1 (the default).
#define MACHINE_ID "esp32-01"

// Operating point of the monitored machine. Sent with every snapshot because
// the RUL predictor takes speed/load as inputs. speed must be > 0, load >= 0.
#define MACHINE_SPEED_RPM 1500.0f
#define MACHINE_LOAD_KN 0.0f

// ---- Timing ------------------------------------------------------------------------
// One 4096-sample window is captured every SNAPSHOT_INTERVAL_MS. At 3200 Hz a
// window takes 1280 ms, so this must be larger than that (static_assert'ed).
#define SNAPSHOT_INTERVAL_MS 2000
// Retained status heartbeat period. The backend calls a device offline after
// 3 x this without any message.
#define HEARTBEAT_INTERVAL_MS 10000

// MQTT reconnect backoff: doubles from MIN to MAX with +/-20 % jitter, reset
// after a successful connect.
#define MQTT_BACKOFF_MIN_MS 1000
#define MQTT_BACKOFF_MAX_MS 60000
// Wi-Fi is auto-reconnected by the driver; if it is still down after this
// long the node forces a full disconnect/begin cycle.
#define WIFI_RECONNECT_MS 30000

// ---- NTP (UTC) -----------------------------------------------------------------------
#define NTP_SERVER_1 "pool.ntp.org"
#define NTP_SERVER_2 "time.google.com"

// ---- Edge buffer ---------------------------------------------------------------------
// Max snapshots held on LittleFS during a network outage (drop-oldest beyond
// this). Each is ~16.6 KB; the 2 MB LittleFS partition fits ~110, so 50
// leaves headroom. Reported as buffer_capacity in status.
#define EDGE_BUFFER_CAPACITY 50
// Capacity used only if LittleFS cannot be mounted/formatted. Each slot is a
// 16.4 KB heap allocation, so keep this small.
#define RAM_BUFFER_CAPACITY 3
// Free space LittleFS must keep after a write (metadata, wear levelling).
#define FS_RESERVE_BYTES (64 * 1024)

// ---- Pins (VSPI + ADXL345 INT1 + status LED) ------------------------------------------
#define PIN_ADXL_SCK 18
#define PIN_ADXL_MISO 19
#define PIN_ADXL_MOSI 23
#define PIN_ADXL_CS 5
// ADXL345 INT1 (FIFO watermark interrupt). Polling backs it up, so a
// miswired INT1 slows nothing down catastrophically, but wire it.
#define PIN_ADXL_INT1 4
// On-board LED on most esp32dev boards.
#define PIN_STATUS_LED 2

// ADXL345 supports SPI up to 5 MHz; the datasheet recommends >= 2 MHz for
// the 3200 Hz output data rate.
#define ADXL_SPI_HZ 5000000
