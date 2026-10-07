// State shared between the sampler task (core 1) and the network task
// (core 0).
//
// Ownership is deliberately narrow: identity is written once in setup()
// before either task starts and is read-only afterwards; snapshot buffers
// change hands only through the two FreeRTOS queues (so exactly one task
// touches a buffer at a time); the only shared mutable counter is atomic.
#pragma once

#include <Arduino.h>
#include <atomic>

#include "app_config.h"
#include "snapshot.h"

struct NodeIdentity {
  char device_id[cfg::kIdMaxLen + 1];
  char machine_id[cfg::kIdMaxLen + 1];
  char boot_id[cfg::kBootIdLen + 1];
};

// Written in setup(), read-only afterwards.
extern NodeIdentity g_identity;

// Buffer pool hand-off. free_q: empty Snapshot* for the sampler to fill.
// ready_q: captured Snapshot* for the network task, oldest first. Both have
// length cfg::kPoolSize, so a send into either can never block.
extern QueueHandle_t g_free_q;
extern QueueHandle_t g_ready_q;

// Snapshots captured but discarded before they reached the edge buffer
// (the sampler had to recycle the oldest queued one because the network
// task fell behind). Folded into buffer_dropped_total.
extern std::atomic<uint32_t> g_capture_dropped;
