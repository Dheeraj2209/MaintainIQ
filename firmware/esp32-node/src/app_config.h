// Derived, compile-time-checked constants for the node.
//
// include/config.h holds what a person edits; this file holds what follows
// from it plus the wire contract (design/M6_LIVE_TELEMETRY.md). Keeping the
// arithmetic here — and static_assert'ing it — means a config change that
// would silently break the device in the field (an MQTT buffer too small for
// a snapshot, an interval shorter than the capture window) fails the build
// instead.
#pragma once

#include <stddef.h>
#include <stdint.h>

#if __has_include("config.h")
#include "config.h"
#else
#error "include/config.h missing: copy include/config.example.h to include/config.h and edit it"
#endif

#define FIRMWARE_VERSION "maintainiq-esp32/0.1.0"

namespace cfg {

// ---- Wire contract constants (section 3) ---------------------------------------------
constexpr int kProtocolVersion = 1;
constexpr const char* kEncoding = "i16le-b64";

// ADXL345 full-resolution mode at +/-16 g is a fixed 3.9 mg/LSB, so the
// device ships raw counts and lets ingest apply the scale (contract 11).
constexpr double kScaleGPerLsb = 0.0039;
constexpr double kSampleRateHz = 3200.0;  // ADXL345 maximum output data rate
constexpr size_t kSnapshotSamples = 4096;  // per axis (contract 11)

constexpr uint32_t kSnapshotIntervalMs = SNAPSHOT_INTERVAL_MS;
constexpr uint32_t kHeartbeatIntervalMs = HEARTBEAT_INTERVAL_MS;

// One window takes 4096 / 3200 Hz = 1280 ms of real time. The sampler
// captures one window per interval, so the interval must cover it (plus a
// little slack for FIFO draining) or windows would overlap.
constexpr uint32_t kCaptureDurationMs =
    static_cast<uint32_t>((kSnapshotSamples * 1000.0) / kSampleRateHz) + 1;
static_assert(kSnapshotIntervalMs >= kCaptureDurationMs + 100,
              "SNAPSHOT_INTERVAL_MS must exceed the 1280 ms capture window (+100 ms slack)");
static_assert(kHeartbeatIntervalMs >= 1000, "HEARTBEAT_INTERVAL_MS must be >= 1000");

// Contract section 3 bounds on samples per axis.
static_assert(kSnapshotSamples >= 32 && kSnapshotSamples <= 65536,
              "snapshot length must be within the contract's 32..65536 samples per axis");

// ---- Payload sizing -------------------------------------------------------------------
constexpr size_t base64Len(size_t raw_bytes) { return ((raw_bytes + 2) / 3) * 4; }

constexpr size_t kAxisBytes = kSnapshotSamples * sizeof(int16_t);  // 8192
constexpr size_t kAxisBase64Len = base64Len(kAxisBytes);           // 10924

// Upper bound on everything in the snapshot JSON except the two base64
// strings: keys, punctuation, 64-char device/machine ids, 32-char boot_id,
// the timestamp and numbers at their widest. tests/telemetry/
// test_firmware_payload_contract.py builds the worst case with the backend's
// own encoder and checks it stays under this, so the bound cannot rot.
constexpr size_t kSnapshotJsonOverheadMax = 768;
constexpr size_t kSnapshotJsonMax = 2 * kAxisBase64Len + kSnapshotJsonOverheadMax;  // 22616

// Broker message_size_limit (contract section 3).
static_assert(kSnapshotJsonMax <= 1024 * 1024, "snapshot payload exceeds the 1 MiB broker limit");

// Topic: "{prefix}/telemetry/{machine_id}", machine_id at most 64 chars.
constexpr size_t kTopicMax = sizeof(MQTT_TOPIC_PREFIX) - 1 + sizeof("/telemetry/") - 1 + 64;

// PubSubClient::publish() assembles the whole PUBLISH packet in its own
// buffer: up to 5 bytes fixed header (MQTT_MAX_HEADER_SIZE), a 2-byte topic
// length, the topic and the payload (QoS 0 => no packet id). Anything larger
// is refused with `false`, which would look like a network failure forever.
constexpr size_t kMqttHeaderMax = 5;
constexpr size_t kMqttBufferSize = kMqttHeaderMax + 2 + kTopicMax + kSnapshotJsonMax + 64;  // +64 slack
// setBufferSize() takes a uint16_t.
static_assert(kMqttBufferSize <= 65535, "PubSubClient buffer cannot exceed 65535 bytes");
static_assert(kMqttBufferSize >= kMqttHeaderMax + 2 + kTopicMax + kSnapshotJsonMax,
              "PubSubClient buffer too small for a full snapshot PUBLISH");

// Status JSON is small; 512 covers 64-char ids and every counter at 10 digits.
constexpr size_t kStatusJsonMax = 512;

// ---- Edge buffer -----------------------------------------------------------------------
constexpr uint32_t kEdgeBufferCapacity = EDGE_BUFFER_CAPACITY;
constexpr uint32_t kRamBufferCapacity = RAM_BUFFER_CAPACITY;
constexpr size_t kFsReserveBytes = FS_RESERVE_BYTES;
static_assert(kEdgeBufferCapacity >= 1, "EDGE_BUFFER_CAPACITY must be >= 1");
static_assert(kRamBufferCapacity >= 1, "RAM_BUFFER_CAPACITY must be >= 1");

// Snapshot buffers shared between sampler and network task: one being
// captured, one being published/buffered, one queued. More would only add
// latency; fewer would make the sampler drop on every slow publish.
constexpr size_t kPoolSize = 3;

// ---- Identity ---------------------------------------------------------------------------
constexpr size_t kIdMaxLen = 64;
constexpr size_t kBootIdLen = 16;  // hex chars, within contract's 1..32 [A-Za-z0-9]

}  // namespace cfg
