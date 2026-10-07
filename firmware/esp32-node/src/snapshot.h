// One captured vibration window plus everything needed to publish it later.
//
// The same struct is what the sampler fills, what crosses the FreeRTOS queue
// (by pointer), and — byte for byte — what the edge buffer writes to
// LittleFS. Identity (device/machine/boot_id/seq) and the operating point are
// frozen INTO the record at capture time, so a snapshot buffered before a
// reboot or a reflash is still published under the boot_id and seq it was
// captured with. That is what keeps the backend's (device_id, boot_id, seq)
// idempotency key and its seq-gap loss accounting truthful across outages.
#pragma once

#include <stdint.h>

#include "app_config.h"

struct SnapshotMeta {
  char device_id[cfg::kIdMaxLen + 1];
  char machine_id[cfg::kIdMaxLen + 1];
  char boot_id[cfg::kBootIdLen + 1];
  uint8_t time_synced;      // 1 => sampled_at_ms is NTP-backed wall-clock time
  uint32_t seq;             // +1 per captured snapshot within a boot
  int64_t sampled_at_ms;    // UTC epoch ms of the window start (valid iff time_synced)
  float sample_rate_hz;
  float speed_rpm;
  float load_kn;
  float scale;              // g per LSB
  uint32_t n_samples;       // per axis; always cfg::kSnapshotSamples today
};

struct Snapshot {
  SnapshotMeta meta;
  int16_t horizontal[cfg::kSnapshotSamples];  // ADXL345 X, raw counts
  int16_t vertical[cfg::kSnapshotSamples];    // ADXL345 Z, raw counts
};

// ~16.6 KB; allocated on the heap (never the stack — task stacks are 4-12 KB).
static_assert(sizeof(Snapshot) < 17 * 1024, "unexpected Snapshot size");
