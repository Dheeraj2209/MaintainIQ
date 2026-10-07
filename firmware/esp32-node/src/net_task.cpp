// Network task (core 0).
//
// Everything that can block on the outside world lives here and only here:
// Wi-Fi association, the MQTT CONNECT and 22 KB PUBLISHes, LittleFS writes
// for the edge buffer. The sampler on core 1 just hands over captured
// snapshots through a queue (see sampler.cpp), so a dead broker or a flaky
// AP can delay delivery but never distort capture timing.
//
// Ordering rule (contract §3): once anything is in the edge buffer, every
// newer snapshot also goes through the buffer, and the buffer is flushed
// oldest-first before live publishing resumes. Ingest therefore sees each
// (device_id, boot_id) in seq order, and "buffered": true marks exactly the
// snapshots that were held back.
//
// Delivery caveat: PubSubClient can only PUBLISH at QoS 0, although the
// contract asks for QoS 1. A publish counts as sent once the whole packet
// has been written to the TCP socket; a broker that drops the connection
// right after can still lose it. That loss is not silent — it shows up as a
// seq gap in the backend's transmission_success_rate KPI.
#include "net_task.h"

#include <Arduino.h>
#include <ArduinoJson.h>
#include <PubSubClient.h>
#include <WiFi.h>
#include <esp_timer.h>
#include <string.h>

#include "edge_buffer.h"
#include "node.h"
#include "payload.h"
#include "status_led.h"
#include "wallclock.h"

namespace {

WiFiClient g_net;
PubSubClient g_mqtt(g_net);
EdgeBuffer g_edge;

char* g_json = nullptr;          // snapshot JSON output (cfg::kSnapshotJsonMax + 1)
Snapshot* g_scratch = nullptr;   // edge-buffer record being flushed
char g_status_json[cfg::kStatusJsonMax];
char g_lwt_json[160];

char g_telemetry_topic[cfg::kTopicMax + 1];
char g_status_topic[cfg::kTopicMax + 1];
char g_alert_topic[cfg::kTopicMax + 1];

// Contract §4 counters, cumulative since boot. Attempts/failures count
// snapshot publishes (live and flushed), the figure the backend's
// device_reported_failure_rate is meant to reflect.
uint32_t g_publish_attempts = 0;
uint32_t g_publish_failures = 0;
uint32_t g_poison_dropped = 0;  // records that could not be encoded at all

uint32_t g_backoff_ms = MQTT_BACKOFF_MIN_MS;
uint32_t g_next_connect_ms = 0;
bool g_mqtt_was_connected = false;
uint32_t g_reconnects = 0;
uint32_t g_last_status_ms = 0;

bool g_wifi_down = true;
uint32_t g_wifi_down_since_ms = 0;

enum class SendResult { kSent, kFailed, kPoison };

bool hasCredentials() { return MQTT_USERNAME[0] != '\0' && MQTT_PASSWORD[0] != '\0'; }

uint32_t uptimeS() { return static_cast<uint32_t>(esp_timer_get_time() / 1000000LL); }

uint32_t droppedTotal() {
  return g_edge.droppedSinceBoot() + g_capture_dropped.load() + g_poison_dropped;
}

void publishStatus() {
  StatusInfo info{};
  info.device_id = g_identity.device_id;
  info.machine_id = g_identity.machine_id;
  info.boot_id = g_identity.boot_id;
  info.online = true;
  info.time_synced = wallclock::synced();
  info.now_ms = wallclock::nowMs();
  info.uptime_s = uptimeS();
  info.buffer_depth = g_edge.depth();
  info.buffer_capacity = g_edge.capacity();
  info.buffer_dropped_total = droppedTotal();
  info.publish_attempts_total = g_publish_attempts;
  info.publish_failures_total = g_publish_failures;
  info.wifi_rssi_dbm = WiFi.RSSI();

  g_last_status_ms = millis();
  const size_t len = encodeStatusJson(info, g_status_json, sizeof(g_status_json));
  if (len == 0) {
    log_e("status JSON overflow");
    return;
  }
  // Retained, so a dashboard or the backend learns the device state the
  // moment it subscribes; the LWT overwrites it with online:false.
  if (!g_mqtt.publish(g_status_topic, reinterpret_cast<const uint8_t*>(g_status_json), len, true)) {
    log_w("status publish failed");
  }
}

SendResult publishSnapshot(const Snapshot& snap, bool buffered) {
  // The topic comes from the RECORD's frozen machine_id, not the current
  // identity: an edge-buffer record that survived a reflash with a new
  // MACHINE_ID still names its original machine in the payload, and the
  // backend rejects a payload whose machine_id differs from the topic's
  // (contract §2). Publishing it on the new machine's topic would get every
  // backlog record rejected - and, being a QoS-0 "send", popped as delivered.
  char topic[cfg::kTopicMax + 1];
  const char* topic_out = g_telemetry_topic;
  if (strcmp(snap.meta.machine_id, g_identity.machine_id) != 0) {
    const int n = snprintf(topic, sizeof(topic), "%s/telemetry/%s", MQTT_TOPIC_PREFIX,
                           snap.meta.machine_id);
    if (!isValidId(snap.meta.machine_id) || n <= 0 || static_cast<size_t>(n) >= sizeof(topic)) {
      log_e("snapshot seq=%u has unusable machine_id - dropped", snap.meta.seq);
      ++g_poison_dropped;
      return SendResult::kPoison;
    }
    topic_out = topic;
  }
  const size_t len = encodeSnapshotJson(snap, buffered, g_json, cfg::kSnapshotJsonMax + 1);
  if (len == 0) {
    // Retrying cannot fix an encoding failure; drop it so it cannot block
    // the backlog forever.
    log_e("snapshot seq=%u could not be encoded - dropped", snap.meta.seq);
    ++g_poison_dropped;
    return SendResult::kPoison;
  }
  ++g_publish_attempts;
  if (g_mqtt.publish(topic_out, reinterpret_cast<const uint8_t*>(g_json), len, false)) {
    return SendResult::kSent;
  }
  ++g_publish_failures;
  log_w("publish failed (seq=%u, %u bytes, mqtt state=%d)", snap.meta.seq,
        static_cast<unsigned>(len), g_mqtt.state());
  return SendResult::kFailed;
}

// A freshly captured snapshot: publish live only if connected AND nothing
// older is waiting; otherwise it joins the back of the edge buffer.
void handleCaptured(const Snapshot& snap) {
  if (g_mqtt.connected() && g_edge.depth() == 0) {
    if (publishSnapshot(snap, false) != SendResult::kFailed) return;
  }
  g_edge.push(snap);
}

// Send one buffered record per call so the loop keeps servicing MQTT
// keep-alives and the capture queue between records.
void flushOne() {
  if (g_edge.depth() == 0) return;
  if (!g_edge.peekOldest(*g_scratch)) return;
  if (publishSnapshot(*g_scratch, true) == SendResult::kFailed) return;  // keep it, retry later
  g_edge.popOldest();
  if (g_edge.depth() == 0) {
    log_i("edge buffer flushed");
    publishStatus();  // contract §4: status right after a buffer flush
  }
}

void onMqttMessage(char* topic, byte* payload, unsigned int len) {
  if (strcmp(topic, g_alert_topic) != 0) return;
  if (len == 0) {  // retained alert cleared
    status_led::set(status_led::Mode::kOff);
    return;
  }
  JsonDocument doc;
  DeserializationError err = deserializeJson(doc, payload, len);
  if (err) {
    log_w("bad alert payload: %s", err.c_str());
    return;
  }
  if ((doc["v"] | 0) != cfg::kProtocolVersion) return;
  const char* machine = doc["machine_id"].as<const char*>();
  if (machine != nullptr && strcmp(machine, g_identity.machine_id) != 0) return;
  const char* type = doc["type"].as<const char*>();
  const char* health_state = doc["health_state"].as<const char*>();
  const char* severity = doc["severity"].as<const char*>();
  status_led::set(status_led::modeForAlert(type, health_state, severity));
  log_i("alert %s: health_state=%s severity=%s", type ? type : "?",
        health_state ? health_state : "null", severity ? severity : "null");
}

void serviceWifi(uint32_t now) {
  if (WiFi.status() == WL_CONNECTED) {
    if (g_wifi_down) {
      g_wifi_down = false;
      log_i("Wi-Fi up: %s rssi=%d", WiFi.localIP().toString().c_str(), WiFi.RSSI());
    }
    wallclock::start(NTP_SERVER_1, NTP_SERVER_2);
    return;
  }
  if (!g_wifi_down) {
    g_wifi_down = true;
    g_wifi_down_since_ms = now;
    log_w("Wi-Fi down - capturing into edge buffer");
    return;
  }
  // The driver auto-reconnects; this is the fallback for when it gets stuck.
  if (now - g_wifi_down_since_ms > WIFI_RECONNECT_MS) {
    log_w("Wi-Fi still down, forcing reconnect");
    WiFi.disconnect();
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    g_wifi_down_since_ms = now;
  }
}

uint32_t jittered(uint32_t ms) {
  // +/-20 % so a fleet that lost the broker together does not reconnect
  // in lockstep.
  const uint32_t span = ms / 5;
  if (span == 0) return ms;
  return ms - span + (esp_random() % (2 * span + 1));
}

void serviceMqtt(uint32_t now) {
  if (g_mqtt.connected()) {
    g_mqtt.loop();
    return;
  }
  if (g_mqtt_was_connected) {
    g_mqtt_was_connected = false;
    log_w("MQTT disconnected (state=%d)", g_mqtt.state());
    g_next_connect_ms = now + jittered(g_backoff_ms);
  }
  if (WiFi.status() != WL_CONNECTED) return;
  if (static_cast<int32_t>(now - g_next_connect_ms) < 0) return;

  // The LWT is registered at CONNECT, retained on the same topic as the
  // status heartbeat, so the broker itself flips the device to
  // online:false if this connection dies without a DISCONNECT (contract §4).
  const bool ok = g_mqtt.connect(g_identity.device_id, hasCredentials() ? MQTT_USERNAME : nullptr,
                                 hasCredentials() ? MQTT_PASSWORD : nullptr, g_status_topic, 1,
                                 true, g_lwt_json, true);
  if (!ok) {
    log_w("MQTT connect to %s:%d failed (state=%d), retry in ~%u ms", MQTT_HOST, MQTT_PORT,
          g_mqtt.state(), static_cast<unsigned>(g_backoff_ms));
    g_next_connect_ms = millis() + jittered(g_backoff_ms);
    g_backoff_ms = g_backoff_ms >= MQTT_BACKOFF_MAX_MS / 2 ? MQTT_BACKOFF_MAX_MS : g_backoff_ms * 2;
    return;
  }
  if (g_reconnects++ > 0) log_i("MQTT reconnected (#%u)", g_reconnects - 1);
  g_mqtt_was_connected = true;
  g_backoff_ms = MQTT_BACKOFF_MIN_MS;
  // Retained alert (if any) arrives right after SUBSCRIBE, restoring the LED
  // state after a reboot.
  g_mqtt.subscribe(g_alert_topic, 1);
  publishStatus();
  if (g_edge.depth() > 0) log_i("flushing %u buffered snapshot(s)", g_edge.depth());
}

void netTask(void*) {
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  g_wifi_down_since_ms = millis();

  for (;;) {
    const uint32_t now = millis();
    serviceWifi(now);
    serviceMqtt(now);

    // Drain the capture queue. Don't wait when there is backlog to flush.
    const bool flushing = g_mqtt.connected() && g_edge.depth() > 0;
    Snapshot* snap = nullptr;
    if (xQueueReceive(g_ready_q, &snap, flushing ? 0 : pdMS_TO_TICKS(20)) == pdTRUE) {
      handleCaptured(*snap);
      xQueueSend(g_free_q, &snap, 0);  // buffer back to the sampler
    }

    if (g_mqtt.connected()) {
      flushOne();
      if (millis() - g_last_status_ms >= cfg::kHeartbeatIntervalMs) publishStatus();
    }
  }
}

}  // namespace

bool startNetworkTask() {
  snprintf(g_telemetry_topic, sizeof(g_telemetry_topic), "%s/telemetry/%s", MQTT_TOPIC_PREFIX,
           g_identity.machine_id);
  snprintf(g_status_topic, sizeof(g_status_topic), "%s/status/%s", MQTT_TOPIC_PREFIX,
           g_identity.device_id);
  snprintf(g_alert_topic, sizeof(g_alert_topic), "%s/alerts/%s", MQTT_TOPIC_PREFIX,
           g_identity.machine_id);
  if (encodeLwtJson(g_identity.device_id, g_lwt_json, sizeof(g_lwt_json)) == 0) return false;

  g_edge.begin();

  g_json = static_cast<char*>(malloc(cfg::kSnapshotJsonMax + 1));
  g_scratch = static_cast<Snapshot*>(malloc(sizeof(Snapshot)));
  if (g_json == nullptr || g_scratch == nullptr) return false;

  g_mqtt.setServer(MQTT_HOST, MQTT_PORT);
  // Default PubSubClient buffer is 256 bytes; a snapshot PUBLISH needs
  // cfg::kMqttBufferSize (computed and static_assert'ed in app_config.h).
  if (!g_mqtt.setBufferSize(static_cast<uint16_t>(cfg::kMqttBufferSize))) return false;
  g_mqtt.setKeepAlive(30);
  g_mqtt.setSocketTimeout(15);
  g_mqtt.setCallback(onMqttMessage);

  log_i("MQTT buffer %u B, snapshot JSON max %u B, free heap %u B",
        static_cast<unsigned>(cfg::kMqttBufferSize), static_cast<unsigned>(cfg::kSnapshotJsonMax),
        static_cast<unsigned>(ESP.getFreeHeap()));

  // 12 KB stack: ArduinoJson documents and the TLS-free Wi-Fi client are
  // modest, but log formatting and LittleFS calls add up.
  return xTaskCreatePinnedToCore(netTask, "net", 12288, nullptr, 3, nullptr, 0) == pdPASS;
}
