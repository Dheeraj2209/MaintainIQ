// Sampling task (core 1).
//
// The one rule this file exists to enforce: sampling never waits on the
// network. Every queue operation here uses a zero timeout, and the task
// never touches Wi-Fi, MQTT or flash. If the network task falls behind
// (a slow 22 KB publish, a LittleFS write) the sampler recycles the OLDEST
// queued snapshot instead of blocking — the same drop-oldest policy the edge
// buffer applies — so capture timing stays regular no matter what the
// network does.
//
// seq is assigned only to snapshots that were actually captured, then
// handed on. A snapshot that is later dropped (here or in the edge buffer)
// has consumed its seq, which is exactly how the backend's
// transmission_success_rate KPI sees the loss: as a seq gap (contract §3).
#include "sampler.h"

#include <SPI.h>
#include <string.h>

#include "adxl345.h"
#include "node.h"
#include "wallclock.h"

namespace {

SPIClass g_vspi(VSPI);
Adxl345 g_adxl(g_vspi, PIN_ADXL_SCK, PIN_ADXL_MISO, PIN_ADXL_MOSI, PIN_ADXL_CS, PIN_ADXL_INT1,
               ADXL_SPI_HZ);

// A window must be contiguous: an overrun means samples are missing in the
// middle. Restart the window a few times before giving up on this interval.
constexpr int kMaxOverrunRestarts = 3;
// Generous ceiling on one window (nominal 1280 ms).
constexpr uint32_t kCaptureTimeoutMs = cfg::kCaptureDurationMs * 2 + 200;

uint32_t g_seq = 0;  // sampler-task-local; never shared

// Get a buffer to capture into without ever blocking.
Snapshot* acquireBuffer() {
  Snapshot* snap = nullptr;
  if (xQueueReceive(g_free_q, &snap, 0) == pdTRUE) return snap;
  // Pool exhausted: the network task still holds one buffer and the rest
  // are queued. Take back the oldest queued snapshot (drop-oldest) — its seq
  // becomes a visible gap downstream.
  if (xQueueReceive(g_ready_q, &snap, 0) == pdTRUE) {
    g_capture_dropped.fetch_add(1);
    log_w("network task behind: dropped queued snapshot seq=%u", snap->meta.seq);
    return snap;
  }
  return nullptr;  // cannot happen with kPoolSize >= 2, but never block
}

// Fill snap->horizontal / vertical with one contiguous window.
bool captureWindow(Snapshot* snap) {
  for (int attempt = 0; attempt <= kMaxOverrunRestarts; ++attempt) {
    g_adxl.resetFifo();
    size_t n = 0;
    bool overran = false;
    const uint32_t started = millis();
    while (n < cfg::kSnapshotSamples) {
      // Woken by the watermark ISR; the timeout doubles as a polling
      // fallback if INT1 is not wired or an edge was missed.
      ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(4));
      if (g_adxl.overrun()) {
        overran = true;
        break;
      }
      n += g_adxl.readFifo(snap->horizontal + n, snap->vertical + n, cfg::kSnapshotSamples - n);
      if (millis() - started > kCaptureTimeoutMs) {
        log_e("capture timeout after %u samples (sensor stalled?)", static_cast<unsigned>(n));
        return false;
      }
    }
    if (!overran) return true;
    log_w("ADXL345 FIFO overrun, restarting window (attempt %d)", attempt + 1);
  }
  return false;
}

void samplerTask(void*) {
  bool sensor_ok = false;
  TickType_t last_wake = xTaskGetTickCount();
  for (;;) {
    vTaskDelayUntil(&last_wake, pdMS_TO_TICKS(cfg::kSnapshotIntervalMs));

    if (!sensor_ok) {
      sensor_ok = g_adxl.begin(xTaskGetCurrentTaskHandle());
      if (!sensor_ok) {
        log_e("ADXL345 not found (DEVID mismatch) - check wiring; retrying");
        continue;  // nothing captured => no seq consumed
      }
      log_i("ADXL345 ready: 3200 Hz, full-res +/-16 g, FIFO stream wm=%u", Adxl345::kWatermark);
    }

    Snapshot* snap = acquireBuffer();
    if (snap == nullptr) continue;

    // Stamp capture time at the window start, before sampling.
    const bool synced = wallclock::synced();
    const int64_t started_ms = synced ? wallclock::nowMs() : 0;

    if (!captureWindow(snap)) {
      xQueueSend(g_free_q, &snap, 0);  // never captured => no seq consumed
      sensor_ok = false;               // re-probe the sensor next interval
      continue;
    }

    SnapshotMeta& m = snap->meta;
    memset(&m, 0, sizeof(m));
    strncpy(m.device_id, g_identity.device_id, sizeof(m.device_id) - 1);
    strncpy(m.machine_id, g_identity.machine_id, sizeof(m.machine_id) - 1);
    strncpy(m.boot_id, g_identity.boot_id, sizeof(m.boot_id) - 1);
    m.time_synced = synced ? 1 : 0;
    m.sampled_at_ms = started_ms;
    m.seq = g_seq++;
    m.sample_rate_hz = static_cast<float>(cfg::kSampleRateHz);
    m.speed_rpm = MACHINE_SPEED_RPM;
    m.load_kn = MACHINE_LOAD_KN;
    m.scale = static_cast<float>(cfg::kScaleGPerLsb);
    m.n_samples = cfg::kSnapshotSamples;

    // ready_q has room for the whole pool, so this cannot fail or block.
    xQueueSend(g_ready_q, &snap, 0);
  }
}

}  // namespace

void startSampler() {
  // Core 1, above the Arduino loop task, well below the Wi-Fi task (core 0).
  xTaskCreatePinnedToCore(samplerTask, "sampler", 4096, nullptr, 10, nullptr, 1);
}
