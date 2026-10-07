// LED blinking runs on a FreeRTOS software timer, not in the network task,
// so a multi-second blocking publish does not freeze the blink and make a
// "degrading" machine look "critical".
#include "status_led.h"

#include <Arduino.h>
#include <atomic>
#include <string.h>

namespace status_led {
namespace {

int g_pin = -1;
std::atomic<int> g_mode{static_cast<int>(Mode::kOff)};
bool g_level = false;
TimerHandle_t g_timer = nullptr;

void apply(bool level) {
  g_level = level;
  digitalWrite(g_pin, level ? HIGH : LOW);
}

void onTick(TimerHandle_t) {
  switch (static_cast<Mode>(g_mode.load())) {
    case Mode::kOff:
      apply(false);
      break;
    case Mode::kSolid:
      apply(true);
      break;
    case Mode::kSlowBlink:
      apply(!g_level);  // 1 s on / 1 s off
      break;
  }
}

bool eq(const char* a, const char* b) { return a != nullptr && strcmp(a, b) == 0; }

}  // namespace

void begin(int pin) {
  g_pin = pin;
  pinMode(pin, OUTPUT);
  apply(false);
  g_timer = xTimerCreate("led", pdMS_TO_TICKS(1000), pdTRUE, nullptr, onTick);
  xTimerStart(g_timer, 0);
}

void set(Mode mode) {
  g_mode.store(static_cast<int>(mode));
  // Solid/off take effect now rather than on the next tick.
  if (mode == Mode::kSolid) apply(true);
  if (mode == Mode::kOff) apply(false);
}

Mode modeForAlert(const char* type, const char* health_state, const char* severity) {
  if (eq(type, "alert_resolved")) return Mode::kOff;
  if (eq(health_state, "critical") || eq(health_state, "faulty")) return Mode::kSolid;
  if (eq(health_state, "degrading")) return Mode::kSlowBlink;
  if (eq(health_state, "healthy")) return Mode::kOff;
  // No health_state: fall back to the alert severity
  // (src/alerts/generation.py: degrading=low, faulty=medium, critical=high).
  if (eq(severity, "high") || eq(severity, "medium") || eq(severity, "critical")) return Mode::kSolid;
  if (eq(severity, "low")) return Mode::kSlowBlink;
  return Mode::kOff;
}

}  // namespace status_led
