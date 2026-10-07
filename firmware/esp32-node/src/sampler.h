// Sampling task: one 4096-sample ADXL345 window every SNAPSHOT_INTERVAL_MS.
#pragma once

#include <Arduino.h>

// Create the sampler task pinned to core 1 (the network stack lives on
// core 0). Must be called after g_identity and the queues are set up.
void startSampler();
