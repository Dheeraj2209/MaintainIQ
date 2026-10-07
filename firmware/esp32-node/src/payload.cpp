// Snapshot / status / LWT encoders. See payload.h for the contract link.
//
// Why the snapshot encoder is hand-rolled instead of ArduinoJson: the two
// base64 axes are ~11 KB each. ArduinoJson would copy them into its own pool
// before serializing, doubling peak RAM for the largest object on the device.
// Writing straight into the output buffer keeps one copy. The small status
// document does use ArduinoJson — there the convenience wins.
#include "payload.h"

#include <ArduinoJson.h>
#include <math.h>
#include <stdio.h>
#include <string.h>
#include <time.h>

namespace {

const char kB64[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

// Append-only JSON object writer over a fixed buffer. Every kv*() call
// writes `"key":value` (with the separating comma), so the set of keys a
// payload uses is exactly the set of string literals passed to kv*() — which
// is what the host-side contract test greps for. Overflow is sticky: once
// set, nothing more is written and finish() reports 0.
class JsonOut {
 public:
  JsonOut(char* buf, size_t cap) : buf_(buf), cap_(cap) { put("{", 1); }

  void kvInt(const char* key, long long value) {
    char tmp[24];
    int n = snprintf(tmp, sizeof(tmp), "%lld", value);
    key_(key);
    put(tmp, static_cast<size_t>(n));
  }

  void kvUint(const char* key, unsigned long value) {
    char tmp[24];
    int n = snprintf(tmp, sizeof(tmp), "%lu", value);
    key_(key);
    put(tmp, static_cast<size_t>(n));
  }

  // Values here are ids already validated against [A-Za-z0-9_.-], a fixed
  // encoding name or a timestamp, so no JSON escaping is ever needed; reject
  // anything that would need it rather than emit broken JSON.
  void kvStr(const char* key, const char* value) {
    for (const char* p = value; *p; ++p) {
      if (*p == '"' || *p == '\\' || static_cast<unsigned char>(*p) < 0x20) {
        overflow_ = true;
        return;
      }
    }
    key_(key);
    put("\"", 1);
    put(value, strlen(value));
    put("\"", 1);
  }

  void kvBool(const char* key, bool value) {
    key_(key);
    if (value) {
      put("true", 4);
    } else {
      put("false", 5);
    }
  }

  void kvNull(const char* key) {
    key_(key);
    put("null", 4);
  }

  // Finite number, always rendered with a decimal point ("1500.0" not
  // "1500") to match the contract examples and the Python encoder, which
  // emits floats for these fields. Non-finite values cannot be JSON.
  void kvNum(const char* key, double value) {
    if (!isfinite(value)) {
      overflow_ = true;
      return;
    }
    char tmp[32];
    int n = snprintf(tmp, sizeof(tmp), "%.7g", value);
    if (n <= 0 || static_cast<size_t>(n) >= sizeof(tmp) - 2) {
      overflow_ = true;
      return;
    }
    if (!strpbrk(tmp, ".eEn")) {  // integral: add ".0"
      tmp[n++] = '.';
      tmp[n++] = '0';
      tmp[n] = '\0';
    }
    key_(key);
    put(tmp, static_cast<size_t>(n));
  }

  // "key":"<base64 of little-endian int16 samples>"
  void kvB64I16(const char* key, const int16_t* samples, size_t n) {
    key_(key);
    size_t need = cfg::base64Len(n * 2) + 2;
    if (overflow_ || len_ + need >= cap_) {
      overflow_ = true;
      return;
    }
    buf_[len_++] = '"';
    len_ += base64EncodeI16LE(samples, n, buf_ + len_);
    buf_[len_++] = '"';
  }

  size_t finish() {
    put("}", 1);
    if (overflow_ || len_ >= cap_) return 0;
    buf_[len_] = '\0';
    return len_;
  }

 private:
  void key_(const char* key) {
    if (!first_) put(",", 1);
    first_ = false;
    put("\"", 1);
    put(key, strlen(key));
    put("\":", 2);
  }

  void put(const char* s, size_t n) {
    if (overflow_) return;
    if (len_ + n >= cap_) {  // keep room for the NUL
      overflow_ = true;
      return;
    }
    memcpy(buf_ + len_, s, n);
    len_ += n;
  }

  char* buf_;
  size_t cap_;
  size_t len_ = 0;
  bool first_ = true;
  bool overflow_ = false;
};

// Byte i of the little-endian int16 stream: low byte first, explicitly, so
// the wire format does not depend on the CPU's endianness (the ESP32 happens
// to be little-endian; the contract must not rely on that).
inline uint8_t leByte(const int16_t* samples, size_t i) {
  uint16_t v = static_cast<uint16_t>(samples[i >> 1]);
  return (i & 1) ? static_cast<uint8_t>(v >> 8) : static_cast<uint8_t>(v & 0xFF);
}

}  // namespace

size_t base64EncodeI16LE(const int16_t* samples, size_t n, char* out) {
  const size_t total = n * 2;
  size_t o = 0;
  size_t i = 0;
  for (; i + 3 <= total; i += 3) {
    uint32_t chunk = (static_cast<uint32_t>(leByte(samples, i)) << 16) |
                     (static_cast<uint32_t>(leByte(samples, i + 1)) << 8) |
                     static_cast<uint32_t>(leByte(samples, i + 2));
    out[o++] = kB64[(chunk >> 18) & 0x3F];
    out[o++] = kB64[(chunk >> 12) & 0x3F];
    out[o++] = kB64[(chunk >> 6) & 0x3F];
    out[o++] = kB64[chunk & 0x3F];
  }
  const size_t rest = total - i;
  if (rest == 1) {
    uint32_t chunk = static_cast<uint32_t>(leByte(samples, i)) << 16;
    out[o++] = kB64[(chunk >> 18) & 0x3F];
    out[o++] = kB64[(chunk >> 12) & 0x3F];
    out[o++] = '=';
    out[o++] = '=';
  } else if (rest == 2) {
    uint32_t chunk = (static_cast<uint32_t>(leByte(samples, i)) << 16) |
                     (static_cast<uint32_t>(leByte(samples, i + 1)) << 8);
    out[o++] = kB64[(chunk >> 18) & 0x3F];
    out[o++] = kB64[(chunk >> 12) & 0x3F];
    out[o++] = kB64[(chunk >> 6) & 0x3F];
    out[o++] = '=';
  }
  return o;
}

void formatUtcMs(int64_t epoch_ms, char out[25]) {
  time_t secs = static_cast<time_t>(epoch_ms / 1000);
  int ms = static_cast<int>(epoch_ms % 1000);
  struct tm tm_utc;
  gmtime_r(&secs, &tm_utc);
  size_t n = strftime(out, 25, "%Y-%m-%dT%H:%M:%S", &tm_utc);
  snprintf(out + n, 25 - n, ".%03dZ", ms);
}

bool isValidId(const char* s) {
  if (s == nullptr) return false;
  size_t n = strlen(s);
  if (n < 1 || n > cfg::kIdMaxLen) return false;
  for (size_t i = 0; i < n; ++i) {
    char c = s[i];
    bool ok = (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') ||
              c == '_' || c == '.' || c == '-';
    if (!ok) return false;
  }
  return true;
}

size_t encodeSnapshotJson(const Snapshot& snap, bool buffered, char* out, size_t cap) {
  const SnapshotMeta& m = snap.meta;
  if (m.n_samples != cfg::kSnapshotSamples) return 0;

  // Key order mirrors protocol.build_snapshot_payload / contract §3.
  JsonOut j(out, cap);
  j.kvInt("v", cfg::kProtocolVersion);
  j.kvStr("device_id", m.device_id);
  j.kvStr("machine_id", m.machine_id);
  j.kvStr("boot_id", m.boot_id);
  j.kvUint("seq", m.seq);
  // Until NTP has synced the device has no wall clock; the contract wants
  // null + time_synced=false so ingest substitutes its receive time instead
  // of trusting a 1970 timestamp.
  if (m.time_synced) {
    char ts[25];
    formatUtcMs(m.sampled_at_ms, ts);
    j.kvStr("sampled_at", ts);
  } else {
    j.kvNull("sampled_at");
  }
  j.kvBool("time_synced", m.time_synced != 0);
  j.kvBool("buffered", buffered);
  j.kvNum("sample_rate_hz", m.sample_rate_hz);
  j.kvNum("speed_rpm", m.speed_rpm);
  j.kvNum("load_kn", m.load_kn);
  j.kvStr("encoding", cfg::kEncoding);
  j.kvNum("scale", m.scale);
  j.kvB64I16("horizontal", snap.horizontal, m.n_samples);
  j.kvB64I16("vertical", snap.vertical, m.n_samples);
  return j.finish();
}

size_t encodeStatusJson(const StatusInfo& s, char* out, size_t cap) {
  JsonDocument doc;
  doc["v"] = cfg::kProtocolVersion;
  doc["device_id"] = s.device_id;
  doc["machine_id"] = s.machine_id;
  doc["boot_id"] = s.boot_id;
  doc["online"] = s.online;
  // reported_at is optional; omit it rather than send an unsynced clock.
  if (s.time_synced) {
    char ts[25];
    formatUtcMs(s.now_ms, ts);
    doc["reported_at"] = ts;  // char[] => ArduinoJson copies it
  }
  doc["uptime_s"] = s.uptime_s;
  doc["firmware"] = FIRMWARE_VERSION;
  doc["snapshot_interval_s"] = cfg::kSnapshotIntervalMs / 1000.0;
  doc["heartbeat_interval_s"] = cfg::kHeartbeatIntervalMs / 1000.0;
  doc["buffer_depth"] = s.buffer_depth;
  doc["buffer_capacity"] = s.buffer_capacity;
  doc["buffer_dropped_total"] = s.buffer_dropped_total;
  doc["publish_attempts_total"] = s.publish_attempts_total;
  doc["publish_failures_total"] = s.publish_failures_total;
  doc["wifi_rssi_dbm"] = s.wifi_rssi_dbm;

  if (doc.overflowed() || measureJson(doc) + 1 > cap) return 0;
  return serializeJson(doc, out, cap);
}

size_t encodeLwtJson(const char* device_id, char* out, size_t cap) {
  JsonOut j(out, cap);
  j.kvInt("v", cfg::kProtocolVersion);
  j.kvStr("device_id", device_id);
  j.kvBool("online", false);
  return j.finish();
}
