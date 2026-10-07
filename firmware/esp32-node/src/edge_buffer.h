// Bounded FIFO of captured snapshots held while the network is down
// (contract §3 "device obligations", §11 "edge buffer").
//
// Primary store is LittleFS, so an outage that ends in a brown-out or a
// watchdog reset does not lose what was captured. If LittleFS cannot be
// mounted (or formatted) the buffer degrades to a small RAM ring instead of
// refusing to buffer at all — the node keeps working, it just survives a
// shorter outage and loses the backlog on reboot.
//
// Not thread-safe by design: only the network task touches it.
#pragma once

#include <stddef.h>
#include <stdint.h>

#include "snapshot.h"

class EdgeBuffer {
 public:
  // Mount LittleFS (formatting on first use) and recover any records left by
  // a previous boot; falls back to RAM on failure. Returns true on flash.
  bool begin();

  // Append a snapshot. When full (by count, or by LittleFS free space) the
  // OLDEST records are dropped to make room, each counted. Returns false
  // only if even an empty buffer cannot take it (the new one is dropped and
  // counted).
  bool push(const Snapshot& snap);

  // Copy the oldest record into `out` without removing it. Corrupt or
  // unreadable records (torn write, layout change) are deleted and counted
  // as dropped on the way. Returns false when empty.
  bool peekOldest(Snapshot& out);

  // Remove the oldest record (after it was published successfully).
  void popOldest();

  uint32_t depth() const { return count_; }
  uint32_t capacity() const;
  bool onFlash() const { return flash_ok_; }

  // Records dropped since boot (contract §4 counters are since-boot).
  uint32_t droppedSinceBoot() const { return dropped_since_boot_; }

 private:
  void dropOldest(const char* why);
  void noteDropped(uint32_t n);
  size_t flashFree() const;
  void pathFor(uint32_t index, char* out, size_t cap) const;
  bool readRecord(uint32_t index, Snapshot& out);

  bool flash_ok_ = false;

  // Flash FIFO: files /eb/<index>.rec for index in [head_, tail_). Indices
  // are recovered from the directory listing at boot, so no per-record NVS
  // write is needed to keep them.
  uint32_t head_ = 0;
  uint32_t tail_ = 0;
  uint32_t count_ = 0;

  // RAM fallback ring (allocated only if flash is unavailable).
  Snapshot** ram_ = nullptr;
  uint32_t ram_head_ = 0;

  uint32_t dropped_since_boot_ = 0;
  uint32_t last_persist_ms_ = 0;
  uint32_t unpersisted_drops_ = 0;
};
