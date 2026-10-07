// UTC wall clock backed by SNTP.
//
// The ESP32 has no battery-backed RTC on a devkit, so after every boot the
// clock starts at 1970 until SNTP answers. Snapshots captured before that
// must say time_synced=false / sampled_at=null (contract §3) rather than carry
// a 1970 timestamp the backend would believe.
#pragma once

#include <stdint.h>

namespace wallclock {

// Start SNTP (UTC, no DST). Idempotent; call once Wi-Fi is up. SNTP then
// re-syncs periodically on its own.
void start(const char* server1, const char* server2);

// True once the system clock has been set from NTP.
bool synced();

// Current UTC time in epoch milliseconds (meaningful only when synced()).
int64_t nowMs();

}  // namespace wallclock
