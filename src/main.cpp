// X4 Classic (X4C) — minimal diagnostic firmware (FreeInk SDK)
//
// Scope (first hardware diagnostics ONLY):
//   - reports build version, reset reason, chip/flash/PSRAM parameters;
//   - safe read-only I2C diagnostics: bus scan + BM8563 detection at 0x51 +
//     reading the CLKOUT register (0x0D). No RTC register is ever written;
//   - GPIO15 (XTAL_32K_P) is left untouched; no sleep modes; no display init.
//
// Board data comes from the FreeInk SDK profile BoardConfig::XTEINK_X4_CLASSIC
// (I2C SDA 39 / SCL 38 @ 400 kHz, BM8563 at 0x51) — no hardcoded hardware
// implementations here.

#include <Arduino.h>
#include <Wire.h>

#include <esp_chip_info.h>
#include <esp_heap_caps.h>
#include <esp_system.h>

#include <BoardConfig.h>

namespace {

constexpr uint8_t BM8563_I2C_ADDR = 0x51;
constexpr uint8_t BM8563_REG_CLKOUT = 0x0D;

const char* resetReasonName(esp_reset_reason_t reason) {
    switch (reason) {
        case ESP_RST_UNKNOWN: return "UNKNOWN";
        case ESP_RST_POWERON: return "POWERON";
        case ESP_RST_SW: return "SW";
        case ESP_RST_PANIC: return "PANIC";
        case ESP_RST_INT_WDT: return "INT_WDT";
        case ESP_RST_TASK_WDT: return "TASK_WDT";
        case ESP_RST_WDT: return "WDT";
        case ESP_RST_DEEPSLEEP: return "DEEPSLEEP";
        case ESP_RST_BROWNOUT: return "BROWNOUT";
        case ESP_RST_SDIO: return "SDIO";
        case ESP_RST_USB: return "USB";
        case ESP_RST_JTAG: return "JTAG";
        case ESP_RST_EFUSE: return "EFUSE";
        case ESP_RST_PWR_GLITCH: return "PWR_GLITCH";
        case ESP_RST_CPU_LOCKUP: return "CPU_LOCKUP";
        default: return "OTHER";
    }
}

const char* flashModeName(FlashMode_t mode) {
    switch (mode) {
        case FM_QIO: return "QIO";
        case FM_QOUT: return "QOUT";
        case FM_DIO: return "DIO";
        case FM_DOUT: return "DOUT";
        default: return "UNKNOWN";
    }
}

const char* i2cDeviceName(uint8_t addr) {
    switch (addr) {
        case 0x51: return "BM8563 RTC (per profile)";
        case 0x63: return "CW2017 gauge (per profile)";
        case 0x6B: return "QMI8658 IMU (per profile)";
        default: return "unknown device";
    }
}

void printStartupBanner() {
    Serial.println();
    Serial.println("==========================================");
    Serial.println("  X4 Classic diag  (" FIRMWARE_VERSION ")");
    Serial.println("==========================================");
    Serial.printf("build:      %s\n", FIRMWARE_VERSION);
    Serial.printf("sdk commit: %s\n", FREEINK_SDK_SHA);
    Serial.printf("env:        x4c  (BoardConfig::XTEINK_X4_CLASSIC)\n");
}

void printChipInfo() {
    esp_chip_info_t chip = {};
    esp_chip_info(&chip);
    // IDF >= 5.x encodes chip_info.revision as full revision: major * 100 + minor.
    Serial.printf("chip:       ESP32-S3 rev %lu.%lu, cores %u\n",
                  (unsigned long)(chip.revision / 100), (unsigned long)(chip.revision % 100),
                  (unsigned)chip.cores);
    Serial.printf("idf:        %s   arduino: %s\n", ESP.getSdkVersion(), ESP_ARDUINO_VERSION_STR);
}

void printResetReason() {
    esp_reset_reason_t reason = esp_reset_reason();
    Serial.printf("reset:      %d (%s)\n", (int)reason, resetReasonName(reason));
}

void printFlashInfo() {
    Serial.printf("flash:      %u bytes (%u MB), %u Hz, mode %s (as configured in image)\n",
                  (unsigned)ESP.getFlashChipSize(), (unsigned)(ESP.getFlashChipSize() >> 20),
                  (unsigned)ESP.getFlashChipSpeed(), flashModeName(ESP.getFlashChipMode()));
}

void printPsramInfo() {
    size_t psramTotal = ESP.getPsramSize();
    size_t psramFree = ESP.getFreePsram();
    if (psramTotal == 0) {
        Serial.println("psram:      NOT DETECTED");
        return;
    }
    Serial.printf("psram:      %u bytes (%u MB), free %u bytes\n",
                  (unsigned)psramTotal, (unsigned)(psramTotal >> 20), (unsigned)psramFree);
    Serial.printf("psram heap: total %u bytes (MALLOC_CAP_SPIRAM)\n",
                  (unsigned)heap_caps_get_total_size(MALLOC_CAP_SPIRAM));
}

void printBoardProfile() {
    const BoardConfig::BoardProfile& p = BoardConfig::ACTIVE;
    Serial.printf("board:      %s (RTC 0x%02X @ I2C)\n", p.name, (unsigned)p.sensors.rtcAddr);
}

// Read-only I2C diagnostics. Writes nothing: this firmware never calls
// Wire.endTransmission with data to any device, never writes RTC registers.
void runI2cDiagnostics() {
    const BoardConfig::BoardProfile& p = BoardConfig::ACTIVE;
    const int sda = p.sensors.i2cSda;
    const int scl = p.sensors.i2cScl;
    const uint32_t hz = p.sensors.i2cHz;

    Serial.printf("i2c:        SDA=%d SCL=%d @%u Hz (from BoardConfig profile)\n",
                  sda, scl, (unsigned)hz);

    if (!Wire.begin(sda, scl, hz)) {
        Serial.println("i2c:        ERROR: bus init failed");
        return;
    }

    // 1) Bus scan: only START + ADDRESS probe (endTransmission with no data).
    Serial.print("i2c scan:  ");
    bool any = false;
    for (uint8_t addr = 0x08; addr <= 0x77; ++addr) {
        Wire.beginTransmission(addr);
        if (Wire.endTransmission() == 0) {
            Serial.printf(" 0x%02X (%s)", addr, i2cDeviceName(addr));
            any = true;
        }
    }
    Serial.println(any ? "" : " (no ACK from any address)");

    // 2) BM8563 detection at 0x51 + CLKOUT (0x0D) read.
    Wire.beginTransmission(BM8563_I2C_ADDR);
    uint8_t probe = Wire.endTransmission();
    if (probe != 0) {
        Serial.printf("bm8563:     NOT DETECTED at 0x%02X (endTransmission=%u)\n",
                      (unsigned)BM8563_I2C_ADDR, probe);
        return;
    }

    // Read CLKOUT register: write register pointer, then repeated-start read.
    Wire.beginTransmission(BM8563_I2C_ADDR);
    Wire.write(BM8563_REG_CLKOUT);
    uint8_t err = Wire.endTransmission(false);  // repeated start, no STOP write
    if (err != 0) {
        Serial.printf("bm8563:     ERROR: register pointer write failed (%u)\n", err);
        return;
    }
    uint8_t n = Wire.requestFrom(BM8563_I2C_ADDR, (uint8_t)1, (uint8_t)1);
    if (n != 1) {
        Serial.println("bm8563:     ERROR: CLKOUT read failed (no data)");
        return;
    }

    uint8_t clkout = Wire.read();
    const bool fe = clkout & 0x80;
    const uint8_t fd = clkout & 0x03;
    const char* fdName = fe ? (fd == 0 ? "32.768 kHz" : fd == 1 ? "1.024 kHz" : fd == 2 ? "32 Hz" : "static low")
                            : "output disabled (high-Z)";
    Serial.printf("bm8563:     detected at 0x%02X\n", (unsigned)BM8563_I2C_ADDR);
    Serial.printf("bm8563:     CLKOUT (0x0D) = 0x%02X  -> FE=%u FD=%u (%s)\n",
                  clkout, fe ? 1 : 0, fd, fdName);
    // PCF8563/BM8563 power-on default is 0x80 (FE=1, FD=00): 32.768 kHz output
    // enabled. An untouched factory RTC should read 0x80.
    Serial.println("bm8563:     expected factory default: 0x80 (32.768 kHz output enabled)");
}

}  // namespace

void setup() {
    // USB CDC (ARDUINO_USB_MODE=1 + ARDUINO_USB_CDC_ON_BOOT=1): wait briefly for
    // a host to attach, so the banner is not lost when monitored from reset.
    Serial.begin(115200);
    for (uint32_t start = millis(); !Serial && millis() - start < 8000;) {
        delay(10);
    }

    printStartupBanner();
    printChipInfo();
    printResetReason();
    printFlashInfo();
    printPsramInfo();
    printBoardProfile();
    runI2cDiagnostics();

    Serial.println();
    Serial.println("diag done (GPIO15/XTAL_32K_P untouched, RTC read-only, no sleep, no display init)");
}

void loop() {
    // Keep the device powered for a human to read the output; no sleep modes.
    static uint32_t last = 0;
    if (millis() - last >= 60000) {
        last = millis();
        Serial.printf("heartbeat: uptime %lu s\n", (unsigned long)(millis() / 1000));
    }
    delay(100);
}
