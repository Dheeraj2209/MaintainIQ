// Edge buffer implementation. See edge_buffer.h for the why.
//
// On-flash layout: one file per snapshot, /eb/<8 hex digit index>.rec:
//   RecordHeader (magic, layout version, body length)
//   Snapshot     (raw struct bytes — the same firmware writes and reads it)
//   uint32 CRC-32 of the Snapshot bytes
// A record is written to /eb/pending.tmp and then renamed into place; the
// LittleFS rename is atomic, so a power cut mid-write leaves either no
// record or a complete one, never a half-written file with a valid name.
// The CRC still guards against flash corruption and a struct layout change
// between firmware versions (layout version bump => old records discarded,
// counted as dropped, instead of being published as garbage).
#include "edge_buffer.h"

#include <Arduino.h>
#include <LittleFS.h>
#include <Preferences.h>
#include <stdlib.h>
#include <string.h>

namespace {

constexpr uint32_t kMagic = 0x3151494D;  // "MIQ1"
constexpr uint16_t kLayoutVersion = 1;
constexpr const char* kDir = "/eb";
constexpr const char* kTmpPath = "/eb/pending.tmp";
constexpr const char* kPartitionLabel = "littlefs";

// Lifetime drop counter is persisted in NVS so drops across reboots stay
// visible on the serial console; rate-limited because a sustained outage can
// drop a snapshot every interval and NVS writes wear flash.
constexpr const char* kPrefsNs = "miq_eb";
constexpr const char* kPrefsDropped = "drop_life";
constexpr uint32_t kPersistEveryMs = 30000;

struct RecordHeader {
  uint32_t magic;
  uint16_t version;
  uint16_t reserved;
  uint32_t body_len;
};

constexpr size_t kRecordBytes = sizeof(RecordHeader) + sizeof(Snapshot) + sizeof(uint32_t);

// Plain bitwise CRC-32 (IEEE, reflected). ~1 ms for a 16 KB record at
// 240 MHz; only ever runs in the network task, so the cost is irrelevant and
// avoiding a ROM-API dependency keeps this portable across core versions.
uint32_t crc32(const uint8_t* data, size_t len) {
  uint32_t crc = 0xFFFFFFFFu;
  for (size_t i = 0; i < len; ++i) {
    crc ^= data[i];
    for (int b = 0; b < 8; ++b) crc = (crc >> 1) ^ (0xEDB88320u & (0u - (crc & 1u)));
  }
  return ~crc;
}

}  // namespace

bool EdgeBuffer::begin() {
  // formatOnFail=true: a blank or corrupted partition is formatted rather
  // than leaving the node without a persistent buffer.
  flash_ok_ = LittleFS.begin(true, "/littlefs", 4, kPartitionLabel);
  if (flash_ok_) {
    if (!LittleFS.exists(kDir)) LittleFS.mkdir(kDir);
    LittleFS.remove(kTmpPath);  // interrupted write from a previous boot

    // Recover [head, tail) from the surviving record names.
    bool any = false;
    uint32_t lo = 0, hi = 0, n = 0;
    File dir = LittleFS.open(kDir);
    for (File f = dir.openNextFile(); f; f = dir.openNextFile()) {
      const char* name = f.name();
      const char* base = strrchr(name, '/');
      base = base ? base + 1 : name;
      char* end = nullptr;
      uint32_t idx = strtoul(base, &end, 16);
      if (end == base || strcmp(end, ".rec") != 0) continue;
      if (!any || idx < lo) lo = idx;
      if (!any || idx > hi) hi = idx;
      any = true;
      ++n;
    }
    head_ = any ? lo : 0;
    tail_ = any ? hi + 1 : 0;
    count_ = n;
    log_i("edge buffer on LittleFS: %u record(s) recovered, %u KB free", count_,
          static_cast<unsigned>(flashFree() / 1024));
    // A smaller capacity in a new firmware build trims the backlog oldest-first.
    while (count_ > capacity()) dropOldest("capacity reduced");
  } else {
    log_e("LittleFS mount failed - edge buffer falls back to RAM (%u slots, not persistent)",
          static_cast<unsigned>(cfg::kRamBufferCapacity));
    ram_ = static_cast<Snapshot**>(calloc(cfg::kRamBufferCapacity, sizeof(Snapshot*)));
  }

  Preferences prefs;
  if (prefs.begin(kPrefsNs, true)) {
    log_i("edge buffer: %u snapshot(s) dropped over device lifetime",
          static_cast<unsigned>(prefs.getUInt(kPrefsDropped, 0)));
    prefs.end();
  }
  return flash_ok_;
}

uint32_t EdgeBuffer::capacity() const {
  return flash_ok_ ? cfg::kEdgeBufferCapacity : cfg::kRamBufferCapacity;
}

size_t EdgeBuffer::flashFree() const {
  size_t total = LittleFS.totalBytes();
  size_t used = LittleFS.usedBytes();
  return used >= total ? 0 : total - used;
}

void EdgeBuffer::pathFor(uint32_t index, char* out, size_t cap) const {
  snprintf(out, cap, "%s/%08lx.rec", kDir, static_cast<unsigned long>(index));
}

void EdgeBuffer::noteDropped(uint32_t n) {
  dropped_since_boot_ += n;
  unpersisted_drops_ += n;
  const uint32_t now = millis();
  if (now - last_persist_ms_ < kPersistEveryMs) return;
  Preferences prefs;
  if (prefs.begin(kPrefsNs, false)) {
    prefs.putUInt(kPrefsDropped, prefs.getUInt(kPrefsDropped, 0) + unpersisted_drops_);
    prefs.end();
    unpersisted_drops_ = 0;
    last_persist_ms_ = now;
  }
}

void EdgeBuffer::dropOldest(const char* why) {
  if (count_ == 0) return;
  if (flash_ok_) {
    // Skip index holes (a record already deleted as corrupt).
    char path[32];
    while (head_ != tail_) {
      pathFor(head_++, path, sizeof(path));
      if (LittleFS.exists(path)) {
        LittleFS.remove(path);
        break;
      }
    }
  } else {
    free(ram_[ram_head_]);
    ram_[ram_head_] = nullptr;
    ram_head_ = (ram_head_ + 1) % cfg::kRamBufferCapacity;
  }
  --count_;
  noteDropped(1);
  log_w("edge buffer: dropped oldest snapshot (%s), depth=%u", why, count_);
}

bool EdgeBuffer::push(const Snapshot& snap) {
  if (!flash_ok_) {
    if (ram_ == nullptr) {
      noteDropped(1);
      return false;
    }
    if (count_ >= cfg::kRamBufferCapacity) dropOldest("RAM buffer full");
    Snapshot* copy = static_cast<Snapshot*>(malloc(sizeof(Snapshot)));
    if (copy == nullptr) {
      noteDropped(1);
      log_e("edge buffer: out of heap, snapshot seq=%u dropped", snap.meta.seq);
      return false;
    }
    memcpy(copy, &snap, sizeof(Snapshot));
    ram_[(ram_head_ + count_) % cfg::kRamBufferCapacity] = copy;
    ++count_;
    return true;
  }

  // Bounded by count AND by free space: drop oldest until both fit.
  while (count_ >= cfg::kEdgeBufferCapacity) dropOldest("buffer full");
  while (count_ > 0 && flashFree() < kRecordBytes + cfg::kFsReserveBytes) dropOldest("flash full");
  if (flashFree() < kRecordBytes + cfg::kFsReserveBytes) {
    noteDropped(1);
    log_e("edge buffer: no flash space even when empty, snapshot seq=%u dropped", snap.meta.seq);
    return false;
  }

  RecordHeader hdr{kMagic, kLayoutVersion, 0, static_cast<uint32_t>(sizeof(Snapshot))};
  const uint32_t crc = crc32(reinterpret_cast<const uint8_t*>(&snap), sizeof(Snapshot));
  File f = LittleFS.open(kTmpPath, FILE_WRITE);
  bool ok = static_cast<bool>(f);
  if (ok) {
    ok = f.write(reinterpret_cast<const uint8_t*>(&hdr), sizeof(hdr)) == sizeof(hdr) &&
         f.write(reinterpret_cast<const uint8_t*>(&snap), sizeof(Snapshot)) == sizeof(Snapshot) &&
         f.write(reinterpret_cast<const uint8_t*>(&crc), sizeof(crc)) == sizeof(crc);
    f.close();
  }
  char path[32];
  pathFor(tail_, path, sizeof(path));
  if (!ok || !LittleFS.rename(kTmpPath, path)) {
    LittleFS.remove(kTmpPath);
    noteDropped(1);
    log_e("edge buffer: flash write failed, snapshot seq=%u dropped", snap.meta.seq);
    return false;
  }
  ++tail_;
  ++count_;
  return true;
}

bool EdgeBuffer::readRecord(uint32_t index, Snapshot& out) {
  char path[32];
  pathFor(index, path, sizeof(path));
  File f = LittleFS.open(path, FILE_READ);
  if (!f) return false;
  RecordHeader hdr;
  uint32_t crc = 0;
  bool ok = f.size() == kRecordBytes &&
            f.read(reinterpret_cast<uint8_t*>(&hdr), sizeof(hdr)) == sizeof(hdr) &&
            hdr.magic == kMagic && hdr.version == kLayoutVersion &&
            hdr.body_len == sizeof(Snapshot) &&
            f.read(reinterpret_cast<uint8_t*>(&out), sizeof(Snapshot)) == sizeof(Snapshot) &&
            f.read(reinterpret_cast<uint8_t*>(&crc), sizeof(crc)) == sizeof(crc) &&
            crc == crc32(reinterpret_cast<const uint8_t*>(&out), sizeof(Snapshot));
  f.close();
  return ok;
}

bool EdgeBuffer::peekOldest(Snapshot& out) {
  if (!flash_ok_) {
    if (count_ == 0) return false;
    memcpy(&out, ram_[ram_head_], sizeof(Snapshot));
    return true;
  }
  while (count_ > 0) {
    char path[32];
    pathFor(head_, path, sizeof(path));
    if (!LittleFS.exists(path)) {  // hole in the index range
      if (++head_ == tail_) count_ = 0;  // bookkeeping drifted; resync
      continue;
    }
    if (readRecord(head_, out)) return true;
    // Unreadable: discard so one bad record cannot wedge the whole backlog.
    dropOldest("corrupt record");
  }
  return false;
}

void EdgeBuffer::popOldest() {
  if (count_ == 0) return;
  if (flash_ok_) {
    char path[32];
    pathFor(head_, path, sizeof(path));
    LittleFS.remove(path);
    ++head_;
  } else {
    free(ram_[ram_head_]);
    ram_[ram_head_] = nullptr;
    ram_head_ = (ram_head_ + 1) % cfg::kRamBufferCapacity;
  }
  --count_;
}
