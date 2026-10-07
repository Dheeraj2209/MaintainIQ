// MaintainIQ ESP32 vibration node — entry point.
//
// Wiring of the pieces (design/M6_LIVE_TELEMETRY.md §11):
//
//   ADXL345 --SPI/FIFO--> sampler task (core 1) --ready_q--> network task (core 0)
//                               ^                                 |  Wi-Fi + MQTT
//                               +------------- free_q ------------+  edge buffer (LittleFS)
//
// setup() only establishes identity, allocates the snapshot pool and starts
// the two tasks; the Arduino loop() task then deletes itself, so nothing
// else competes for core 1 with the sampler.
#include <Arduino.h>
#include <Preferences.h>
#include <string.h>

#include "app_config.h"
#include "net_task.h"
#include "node.h"
#include "payload.h"
#include "sampler.h"
#include "status_led.h"

NodeIdentity g_identity;
QueueHandle_t g_free_q = nullptr;
QueueHandle_t g_ready_q = nullptr;
std::atomic<uint32_t> g_capture_dropped{0};

namespace {

// Fatal configuration error: blink fast forever and say why on serial.
// Publishing anything would only produce rows the backend rejects.
[[noreturn]] void halt(const char* why) {
  pinMode(PIN_STATUS_LED, OUTPUT);
  for (;;) {
    Serial.printf("FATAL: %s\n", why);
    for (int i = 0; i < 20; ++i) {
      digitalWrite(PIN_STATUS_LED, i & 1);
      delay(100);
    }
  }
}

// boot_id must differ on every boot (it scopes seq, and with seq forms the
// backend's idempotency key). A persisted boot counter guarantees that even
// if the RNG is weak this early (the ESP32 RNG is only truly random once the
// radio is on); the random half covers an NVS wipe resetting the counter.
void makeBootId(char out[cfg::kBootIdLen + 1]) {
  Preferences prefs;
  uint32_t boots = 0;
  if (prefs.begin("miq", false)) {
    boots = prefs.getUInt("boots", 0) + 1;
    prefs.putUInt("boots", boots);
    prefs.end();
  }
  const uint32_t noise = esp_random() ^ static_cast<uint32_t>(ESP.getEfuseMac()) ^ micros();
  snprintf(out, cfg::kBootIdLen + 1, "%08lx%08lx", static_cast<unsigned long>(boots),
           static_cast<unsigned long>(noise));
}

void makeIdentity() {
  memset(&g_identity, 0, sizeof(g_identity));
  if (DEVICE_ID[0] != '\0') {
    strncpy(g_identity.device_id, DEVICE_ID, cfg::kIdMaxLen);
  } else {
    // Factory MAC from eFuse (valid before Wi-Fi init; byte 0 is the
    // lowest byte of the returned integer).
    const uint64_t efuse = ESP.getEfuseMac();
    uint8_t mac[6];
    for (int i = 0; i < 6; ++i) mac[i] = static_cast<uint8_t>(efuse >> (8 * i));
    snprintf(g_identity.device_id, sizeof(g_identity.device_id), "esp32-%02x%02x%02x", mac[3],
             mac[4], mac[5]);
  }
  strncpy(g_identity.machine_id, MACHINE_ID, cfg::kIdMaxLen);
  makeBootId(g_identity.boot_id);

  // An id the backend's ^[A-Za-z0-9_.-]{1,64}$ check rejects would make
  // every message a rejected row; better to refuse to start.
  if (strlen(DEVICE_ID) > cfg::kIdMaxLen || !isValidId(g_identity.device_id)) {
    halt("DEVICE_ID must match ^[A-Za-z0-9_.-]{1,64}$");
  }
  if (strlen(MACHINE_ID) > cfg::kIdMaxLen || !isValidId(g_identity.machine_id)) {
    halt("MACHINE_ID must match ^[A-Za-z0-9_.-]{1,64}$");
  }
  if (!(MACHINE_SPEED_RPM > 0.0f) || !(MACHINE_LOAD_KN >= 0.0f)) {
    halt("MACHINE_SPEED_RPM must be > 0 and MACHINE_LOAD_KN >= 0");
  }
}

}  // namespace

void setup() {
  Serial.begin(115200);
  delay(200);
  status_led::begin(PIN_STATUS_LED);
  makeIdentity();
  Serial.printf("%s device=%s machine=%s boot_id=%s\n", FIRMWARE_VERSION, g_identity.device_id,
                g_identity.machine_id, g_identity.boot_id);

  g_free_q = xQueueCreate(cfg::kPoolSize, sizeof(Snapshot*));
  g_ready_q = xQueueCreate(cfg::kPoolSize, sizeof(Snapshot*));
  if (g_free_q == nullptr || g_ready_q == nullptr) halt("queue allocation failed");
  for (size_t i = 0; i < cfg::kPoolSize; ++i) {
    Snapshot* snap = static_cast<Snapshot*>(malloc(sizeof(Snapshot)));
    if (snap == nullptr) halt("snapshot pool allocation failed (heap)");
    xQueueSend(g_free_q, &snap, 0);
  }

  if (!startNetworkTask()) halt("network task start failed (heap)");
  startSampler();
}

void loop() {
  // All work happens in the sampler and network tasks.
  vTaskDelete(nullptr);
}
