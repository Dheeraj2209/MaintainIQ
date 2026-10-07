// Network task: Wi-Fi, NTP, MQTT, edge-buffer flushing, status heartbeat
// and the downstream alert subscription.
#pragma once

// Create the network task pinned to core 0 (alongside the Wi-Fi/LwIP tasks).
// Returns false if the large buffers it needs could not be allocated.
bool startNetworkTask();
