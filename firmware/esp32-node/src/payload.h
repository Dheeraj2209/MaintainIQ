// JSON encoders for the M6 wire format (design/M6_LIVE_TELEMETRY.md §3, §4).
//
// src/telemetry/protocol.py is the reference implementation; this is the one
// hand-written re-implementation of it. Key names, types and order mirror
// protocol.build_snapshot_payload / build_status_payload exactly, and
// tests/telemetry/test_firmware_payload_contract.py statically checks that
// the keys used here are the contract's keys, so the two cannot drift
// without CI noticing.
#pragma once

#include <stddef.h>
#include <stdint.h>

#include "snapshot.h"

// Runtime figures that go into a status heartbeat. Counters are cumulative
// since boot (contract §4).
struct StatusInfo {
  const char* device_id;
  const char* machine_id;
  const char* boot_id;
  bool online;
  bool time_synced;
  int64_t now_ms;           // UTC epoch ms, only used when time_synced
  uint32_t uptime_s;
  uint32_t buffer_depth;
  uint32_t buffer_capacity;
  uint32_t buffer_dropped_total;
  uint32_t publish_attempts_total;
  uint32_t publish_failures_total;
  int32_t wifi_rssi_dbm;
};

// Encode `snap` as a §3 snapshot into out[0..cap). `buffered` is decided at
// send time (it is not part of the stored record). Returns the JSON length,
// or 0 if it did not fit — callers treat 0 as a poison record, not a retry.
size_t encodeSnapshotJson(const Snapshot& snap, bool buffered, char* out, size_t cap);

// §4 status / heartbeat. Returns length or 0 on overflow.
size_t encodeStatusJson(const StatusInfo& info, char* out, size_t cap);

// §4 LWT: exactly {"v":1,"device_id":"…","online":false}.
size_t encodeLwtJson(const char* device_id, char* out, size_t cap);

// Standard base64 (RFC 4648, '+/', '=' padding) of n int16 samples written
// as little-endian bytes. Writes no terminator; returns chars written
// (always cfg::base64Len(2 * n)).
size_t base64EncodeI16LE(const int16_t* samples, size_t n, char* out);

// "YYYY-MM-DDTHH:MM:SS.mmmZ" (24 chars + NUL), the contract timestamp form.
void formatUtcMs(int64_t epoch_ms, char out[25]);

// True iff s matches ^[A-Za-z0-9_.-]{1,64}$ (contract §2).
bool isValidId(const char* s);
