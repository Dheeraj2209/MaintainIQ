// ADXL345 register-level driver. Register map and timing notes from the
// Analog Devices ADXL345 datasheet (Rev. G).
#include "adxl345.h"

namespace {

// Registers
constexpr uint8_t kRegDevId = 0x00;
constexpr uint8_t kRegBwRate = 0x2C;
constexpr uint8_t kRegPowerCtl = 0x2D;
constexpr uint8_t kRegIntEnable = 0x2E;
constexpr uint8_t kRegIntMap = 0x2F;
constexpr uint8_t kRegIntSource = 0x30;
constexpr uint8_t kRegDataFormat = 0x31;
constexpr uint8_t kRegDataX0 = 0x32;
constexpr uint8_t kRegFifoCtl = 0x38;
constexpr uint8_t kRegFifoStatus = 0x39;

constexpr uint8_t kDevId = 0xE5;

// SPI command bits: bit7 = read, bit6 = multi-byte (auto-increment).
constexpr uint8_t kSpiRead = 0x80;
constexpr uint8_t kSpiMulti = 0x40;

// BW_RATE: rate code 0b1111 = 3200 Hz output data rate, normal power.
constexpr uint8_t kRate3200Hz = 0x0F;
// DATA_FORMAT: FULL_RES (bit3) + range +/-16 g (0b11); SPI bit (6) = 0 => 4-wire.
// Full resolution keeps 3.9 mg/LSB at every range, so scale is constant.
constexpr uint8_t kFormatFullRes16g = 0x08 | 0x03;
// POWER_CTL: Measure bit.
constexpr uint8_t kPowerMeasure = 0x08;
// INT_ENABLE / INT_SOURCE bits.
constexpr uint8_t kIntWatermark = 0x02;
constexpr uint8_t kIntOverrun = 0x01;
// FIFO_CTL: FIFO_MODE in bits 7:6 (00 bypass, 10 stream); samples in 4:0.
// Trigger bit (5) = 0 => watermark/trigger events on INT1.
constexpr uint8_t kFifoBypass = 0x00;
constexpr uint8_t kFifoStream = 0x80;

}  // namespace

TaskHandle_t Adxl345::notify_task_ = nullptr;

Adxl345::Adxl345(SPIClass& spi, int pin_sck, int pin_miso, int pin_mosi, int pin_cs,
                 int pin_int1, uint32_t spi_hz)
    // ADXL345 SPI is mode 3 (CPOL=1, CPHA=1), MSB first.
    : spi_(spi),
      settings_(spi_hz, MSBFIRST, SPI_MODE3),
      pin_sck_(pin_sck),
      pin_miso_(pin_miso),
      pin_mosi_(pin_mosi),
      pin_cs_(pin_cs),
      pin_int1_(pin_int1) {}

void IRAM_ATTR Adxl345::onInt1() {
  if (notify_task_ == nullptr) return;
  BaseType_t woken = pdFALSE;
  vTaskNotifyGiveFromISR(notify_task_, &woken);
  if (woken) portYIELD_FROM_ISR();
}

bool Adxl345::begin(TaskHandle_t notify_task) {
  pinMode(pin_cs_, OUTPUT);
  digitalWrite(pin_cs_, HIGH);
  spi_.begin(pin_sck_, pin_miso_, pin_mosi_, pin_cs_);

  if (readReg(kRegDevId) != kDevId) return false;

  writeReg(kRegPowerCtl, 0x00);  // standby while configuring
  writeReg(kRegBwRate, kRate3200Hz);
  writeReg(kRegDataFormat, kFormatFullRes16g);
  writeReg(kRegIntMap, 0x00);  // every source => INT1
  writeReg(kRegIntEnable, kIntWatermark);
  writeReg(kRegFifoCtl, kFifoStream | kWatermark);
  writeReg(kRegPowerCtl, kPowerMeasure);

  notify_task_ = notify_task;
  if (pin_int1_ >= 0 && notify_task != nullptr) {
    pinMode(pin_int1_, INPUT);
    // INT1 is active-high (DATA_FORMAT INT_INVERT = 0) and stays high while
    // the FIFO is at/above the watermark, so a rising edge per drain cycle.
    attachInterrupt(digitalPinToInterrupt(pin_int1_), onInt1, RISING);
  }
  return true;
}

void Adxl345::resetFifo() {
  // Going through bypass mode empties the FIFO; re-entering stream mode
  // restarts collection from the next sample.
  writeReg(kRegFifoCtl, kFifoBypass);
  writeReg(kRegFifoCtl, kFifoStream | kWatermark);
  (void)readReg(kRegIntSource);
}

uint8_t Adxl345::fifoEntries() { return readReg(kRegFifoStatus) & 0x3F; }

bool Adxl345::overrun() { return (readReg(kRegIntSource) & kIntOverrun) != 0; }

size_t Adxl345::readFifo(int16_t* x, int16_t* z, size_t max) {
  size_t entries = fifoEntries();
  if (entries > max) entries = max;
  uint8_t raw[6];
  for (size_t i = 0; i < entries; ++i) {
    // One FIFO entry must be read as a single 6-byte burst from DATAX0;
    // the FIFO pops when the burst ends (CS rises).
    spi_.beginTransaction(settings_);
    digitalWrite(pin_cs_, LOW);
    spi_.transfer(kRegDataX0 | kSpiRead | kSpiMulti);
    for (uint8_t b = 0; b < 6; ++b) raw[b] = spi_.transfer(0x00);
    digitalWrite(pin_cs_, HIGH);
    spi_.endTransaction();
    // Data registers are little-endian two's complement: X0 X1 Y0 Y1 Z0 Z1.
    x[i] = static_cast<int16_t>(static_cast<uint16_t>(raw[0]) | (static_cast<uint16_t>(raw[1]) << 8));
    z[i] = static_cast<int16_t>(static_cast<uint16_t>(raw[4]) | (static_cast<uint16_t>(raw[5]) << 8));
    // Datasheet: >= 5 us between the end of one FIFO read and the start of
    // the next at high output data rates, so the FIFO can shift the entry.
    delayMicroseconds(5);
  }
  return entries;
}

uint8_t Adxl345::readReg(uint8_t reg) {
  spi_.beginTransaction(settings_);
  digitalWrite(pin_cs_, LOW);
  spi_.transfer(reg | kSpiRead);
  uint8_t value = spi_.transfer(0x00);
  digitalWrite(pin_cs_, HIGH);
  spi_.endTransaction();
  return value;
}

void Adxl345::writeReg(uint8_t reg, uint8_t value) {
  spi_.beginTransaction(settings_);
  digitalWrite(pin_cs_, LOW);
  spi_.transfer(reg & 0x3F);
  spi_.transfer(value);
  digitalWrite(pin_cs_, HIGH);
  spi_.endTransaction();
}
