#include "wallclock.h"

#include <Arduino.h>
#include <sys/time.h>
#include <time.h>

namespace wallclock {
namespace {

// Any time before 2024-01-01T00:00:00Z can only be the post-boot epoch-0
// clock counting up, never a real NTP answer.
constexpr time_t kMinValidEpoch = 1704067200;
bool g_started = false;

}  // namespace

void start(const char* server1, const char* server2) {
  if (g_started) return;
  g_started = true;
  // Offsets 0/0: the device only ever deals in UTC.
  configTime(0, 0, server1, server2);
}

bool synced() { return time(nullptr) >= kMinValidEpoch; }

int64_t nowMs() {
  struct timeval tv;
  gettimeofday(&tv, nullptr);
  return static_cast<int64_t>(tv.tv_sec) * 1000 + tv.tv_usec / 1000;
}

}  // namespace wallclock
