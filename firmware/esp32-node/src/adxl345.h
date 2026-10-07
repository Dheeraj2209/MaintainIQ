// Minimal ADXL345 driver: SPI, full resolution +/-16 g, 3200 Hz, FIFO stream
// mode with a watermark interrupt.
//
// Why a hand-written driver rather than the Adafruit library: that library
// is I2C-first and reads one sample per transaction with no FIFO support. At
// 3200 Hz the sensor produces a sample every 312 us; the only way to keep
// up without burning a core is to let the 32-entry FIFO absorb samples and
// drain it in bursts when the watermark interrupt fires (contract §11).
#pragma once

#include <Arduino.h>
#include <SPI.h>

class Adxl345 {
 public:
  // FIFO watermark: INT1 fires once this many samples are queued. 16 of 32
  // gives ~5 ms of slack at 3200 Hz before the FIFO would overrun.
  static constexpr uint8_t kWatermark = 16;
  static constexpr uint8_t kFifoDepth = 32;

  Adxl345(SPIClass& spi, int pin_sck, int pin_miso, int pin_mosi, int pin_cs, int pin_int1,
          uint32_t spi_hz);

  // Probe DEVID and configure the sensor. `notify_task` is woken (task
  // notification) from the INT1 ISR; pass nullptr to rely on polling only.
  // Returns false if the chip does not answer with DEVID 0xE5.
  bool begin(TaskHandle_t notify_task);

  // Discard everything in the FIFO and clear a latched overrun, so a new
  // window starts with fresh, contiguous samples.
  void resetFifo();

  // Number of samples currently in the FIFO (0..32).
  uint8_t fifoEntries();

  // True if the FIFO overflowed (samples were lost) since it was last drained.
  bool overrun();

  // Pop up to `max` samples into x/z (raw counts). Returns the number read.
  size_t readFifo(int16_t* x, int16_t* z, size_t max);

 private:
  uint8_t readReg(uint8_t reg);
  void writeReg(uint8_t reg, uint8_t value);
  static void IRAM_ATTR onInt1();

  SPIClass& spi_;
  SPISettings settings_;
  int pin_sck_, pin_miso_, pin_mosi_, pin_cs_, pin_int1_;
  static TaskHandle_t notify_task_;
};
