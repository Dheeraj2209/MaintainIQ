// Status LED driven by downstream alerts (contract §5, §11):
//   solid      = critical / faulty
//   slow blink = degrading
//   off        = healthy / alert resolved
#pragma once

namespace status_led {

enum class Mode { kOff, kSlowBlink, kSolid };

void begin(int pin);
void set(Mode mode);

// Map a §5 alert payload's fields to a mode. Pure, so the rule is easy to
// read in one place: health_state wins; severity is the fallback for an
// alert row that has none.
Mode modeForAlert(const char* type, const char* health_state, const char* severity);

}  // namespace status_led
