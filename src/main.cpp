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

#include <driver/gpio.h>
#include <driver/pulse_cnt.h>
#include <soc/io_mux_reg.h>

namespace {

constexpr uint8_t BM8563_I2C_ADDR = 0x51;
constexpr uint8_t BM8563_REG_CLKOUT = 0x0D;

// M1 observability: HWCDC TX ring buffer enlarged before Serial.begin() so the
// whole startup output survives a host that connects later (default 256 B
// keeps only the newest bytes when full; ~1 KB of startup output would lose
// the banner).
constexpr size_t HWCDC_TX_BUFFER_SIZE = 4096;
// M1 observability: heartbeat period. Wire default timeout is 50 ms per
// transaction (Wire.cpp ctor); made explicit so a stuck I2C bus cannot stall
// diagnostics beyond it (scan worst case: 112 addresses x 50 ms < 6 s).
constexpr uint32_t HEARTBEAT_PERIOD_MS = 2000;
constexpr uint16_t I2C_TIMEOUT_MS = 50;

// --- M2: passive RTC CLKOUT probe on GPIO15 (the XTAL_32K_P pad) ------------
// GPIO15 is used strictly as a high-impedance digital input: no pull-up, no
// pull-down, no output driver, no GPIO interrupts. The internal 32 kHz
// oscillator is never touched: RTC clock source is the internal RC per the
// pinned sdkconfig (CONFIG_RTC_CLK_SRC_INT_RC), no rtc_clk_32k_* calls are
// made, and the BM8563 CLKOUT register stays read-only (M1).
constexpr int CLKOUT_PROBE_GPIO = 15;
constexpr uint32_t CLKOUT_WINDOW_MS = 100;  // short window; hardware counts edges meanwhile
constexpr int CLKOUT_WINDOWS = 6;           // consecutive windows measured at boot
constexpr int PCNT_LOW_LIMIT = -1;          // driver requires low_limit < 0
constexpr int PCNT_HIGH_LIMIT = 32767;      // far above any window: reaching it = noise marker
constexpr uint32_t CLKOUT_GLITCH_NS = 1000; // ignore pulses < 1 us (32 kHz half-period ~15.3 us)

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
    // Explicit per-transaction timeout (Wire default is already 50 ms in this
    // core): every endTransmission()/requestFrom() is bounded by it, so a
    // stuck bus returns errors instead of blocking the diagnostics.
    Wire.setTimeOut(I2C_TIMEOUT_MS);

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

// --- M2: passive RTC CLKOUT probe on GPIO15 (the XTAL_32K_P pad) ------------
// PCNT counts both edges of the pin in hardware; the app only samples the
// counter in short stop/clear/start/read windows, so raw counts never wrap in
// normal operation (<= ~6.6k counts per 100 ms window vs limit 32767). If a
// window reaches the limit (floating-input noise), the watch-point ISR marks
// the window instead of the count being trusted.
static pcnt_unit_handle_t sClkUnit = nullptr;
static pcnt_channel_handle_t sClkChan = nullptr;
static volatile uint32_t sClkWindowLimitHits = 0;

static bool IRAM_ATTR clkoutOnWatchReach(pcnt_unit_handle_t unit,
                                         const pcnt_watch_event_data_t *edata, void *user) {
    (void)unit;
    (void)edata;
    (void)user;
    sClkWindowLimitHits = sClkWindowLimitHits + 1;
    return false;
}

// Strictly high-impedance digital input on the XTAL_32K_P pad: input only,
// no pull-up, no pull-down, no output, no GPIO interrupt.
static void clkoutPinSetHighZ() {
    gpio_config_t io = {};
    io.pin_bit_mask = 1ULL << CLKOUT_PROBE_GPIO;
    io.mode = GPIO_MODE_INPUT;
    io.pull_up_en = GPIO_PULLUP_DISABLE;
    io.pull_down_en = GPIO_PULLDOWN_DISABLE;
    io.intr_type = GPIO_INTR_DISABLE;
    gpio_config(&io);
}

// Pull state lives in the GPIO15 IO MUX pad register (FUN_PU bit 8 / FUN_PD
// bit 7); read back so the log shows the real pad state, not assumptions.
static void clkoutPinReportPulls(const char* stage) {
    const uint32_t mux = REG_READ(IO_MUX_GPIO15_REG);
    Serial.printf("clkout:    GPIO15 pad %s: FUN_PU=%u FUN_PD=%u (1=on, off expected)\n",
                  stage, (unsigned)((mux & FUN_PU) ? 1 : 0), (unsigned)((mux & FUN_PD) ? 1 : 0));
}

static bool initClkoutProbe() {
    clkoutPinSetHighZ();
    clkoutPinReportPulls("before PCNT");

    pcnt_unit_config_t ucfg = {};
    ucfg.low_limit = PCNT_LOW_LIMIT;
    ucfg.high_limit = PCNT_HIGH_LIMIT;
    ucfg.flags.accum_count = 0;  // per-window restart; no accumulation needed
    esp_err_t err = pcnt_new_unit(&ucfg, &sClkUnit);
    if (err != ESP_OK) {
        Serial.printf("clkout:    ERROR: pcnt_new_unit failed (%d)\n", (int)err);
        return false;
    }

    pcnt_glitch_filter_config_t fcfg = {};
    fcfg.max_glitch_ns = CLKOUT_GLITCH_NS;
    err = pcnt_unit_set_glitch_filter(sClkUnit, &fcfg);
    if (err != ESP_OK) {
        Serial.printf("clkout:    ERROR: glitch filter failed (%d)\n", (int)err);
        return false;
    }

    pcnt_event_callbacks_t cbs = {};
    cbs.on_reach = clkoutOnWatchReach;
    err = pcnt_unit_register_event_callbacks(sClkUnit, &cbs, nullptr);
    if (err != ESP_OK) {
        Serial.printf("clkout:    ERROR: event callbacks failed (%d)\n", (int)err);
        return false;
    }

    err = pcnt_unit_add_watch_point(sClkUnit, PCNT_HIGH_LIMIT);
    if (err != ESP_OK) {
        Serial.printf("clkout:    ERROR: watch point failed (%d)\n", (int)err);
        return false;
    }

    pcnt_chan_config_t ccfg = {};
    ccfg.edge_gpio_num = CLKOUT_PROBE_GPIO;
    // M2 P1: level input is VIRTUAL (level_gpio_num < 0). Zero-init would have
    // left 0 here and the driver treats it as GPIO0 - a boot strapping pin -
    // enabling a pull-up on it (pcnt_new_channel calls gpio_pullup_en on the
    // level pin). With -1 the driver's GPIO branch for the level input is
    // skipped entirely and only the edge pin (GPIO15) is configured.
    ccfg.level_gpio_num = -1;
    ccfg.flags.virt_level_io_level = 0;  // constant low level into control_sig
    err = pcnt_new_channel(sClkUnit, &ccfg, &sClkChan);
    if (err != ESP_OK) {
        Serial.printf("clkout:    ERROR: pcnt_new_channel failed (%d)\n", (int)err);
        return false;
    }
    // The IDF pulse_cnt driver enables a pull-up on the edge input pin
    // (pcnt_new_channel in esp_driver_pcnt/src/pulse_cnt.c does
    // gpio_pullup_en). Strip it: the PCNT input runs over the GPIO matrix and
    // does not need pad pulls; the pin must stay floating.
    gpio_pullup_dis((gpio_num_t)CLKOUT_PROBE_GPIO);
    gpio_pulldown_dis((gpio_num_t)CLKOUT_PROBE_GPIO);
    clkoutPinReportPulls("after PCNT (stripped)");

    // Count BOTH edges (rising and falling) as increments.
    err = pcnt_channel_set_edge_action(sClkChan,
                                       PCNT_CHANNEL_EDGE_ACTION_INCREASE,
                                       PCNT_CHANNEL_EDGE_ACTION_INCREASE);
    if (err != ESP_OK) {
        Serial.printf("clkout:    ERROR: edge action failed (%d)\n", (int)err);
        return false;
    }
    // M2 P1: explicit KEEP for both control-level states - the counting mode
    // driven by the edge actions must never be altered by the (virtual,
    // constant) level input.
    err = pcnt_channel_set_level_action(sClkChan,
                                        PCNT_CHANNEL_LEVEL_ACTION_KEEP,
                                        PCNT_CHANNEL_LEVEL_ACTION_KEEP);
    if (err != ESP_OK) {
        Serial.printf("clkout:    ERROR: level action failed (%d)\n", (int)err);
        return false;
    }

    err = pcnt_unit_enable(sClkUnit);
    if (err != ESP_OK) {
        Serial.printf("clkout:    ERROR: unit enable failed (%d)\n", (int)err);
        return false;
    }
    return true;
}

// One measurement window: clear -> start -> fixed delay -> stop -> raw count.
// duration_us is measured around the counting interval; frequency is
// edges/s; the CLKOUT square wave has 2 edges per period => signal = edges/2.
static void measureClkoutWindow(int window) {
    sClkWindowLimitHits = 0;
    esp_err_t err = pcnt_unit_clear_count(sClkUnit);
    if (err != ESP_OK) {
        Serial.printf("clkout:    window %d: ERROR: clear failed (%d)\n", window, (int)err);
        return;
    }
    err = pcnt_unit_start(sClkUnit);
    if (err != ESP_OK) {
        Serial.printf("clkout:    window %d: ERROR: start failed (%d)\n", window, (int)err);
        return;
    }
    const uint32_t t0 = micros();
    delay(CLKOUT_WINDOW_MS);
    const uint32_t t1 = micros();
    err = pcnt_unit_stop(sClkUnit);
    if (err != ESP_OK) {
        Serial.printf("clkout:    window %d: ERROR: stop failed (%d)\n", window, (int)err);
        return;
    }
    int counts = 0;
    err = pcnt_unit_get_count(sClkUnit, &counts);
    if (err != ESP_OK) {
        Serial.printf("clkout:    window %d: ERROR: get count failed (%d)\n", window, (int)err);
        return;
    }

    const uint32_t windowUs = t1 - t0;
    const bool noisy = sClkWindowLimitHits != 0;
    float edgesHz = 0.0f, signalHz = 0.0f;
    if (windowUs > 0) {
        edgesHz = (float)counts * 1e6f / (float)windowUs;
        signalHz = edgesHz / 2.0f;
    }
    Serial.printf("clkout:    window %d: counts=%d dur=%lu us edges=%.1f Hz signal=%.1f Hz%s\n",
                  window, counts, (unsigned long)windowUs,
                  (double)edgesHz, (double)signalHz,
                  noisy ? " LIMIT-REACHED (noise, untrusted)" : "");
}

}  // namespace

void setup() {
    // M1 markers: timestamps are taken before each init step; lines are printed
    // once HWCDC accepts writes. Writes before Serial.begin() are silently
    // dropped (tx_lock/tx_ring_buf are created inside begin()), so the pre-USB
    // marker carries its pre-begin timestamp instead.
    const uint32_t tUsbPre = millis();

    // USB CDC (ARDUINO_USB_MODE=1 + ARDUINO_USB_CDC_ON_BOOT=1): enlarge the TX
    // ring buffer BEFORE begin() (begin() keeps an existing buffer), then wait
    // briefly for a host to attach, so the banner is not lost when monitored
    // from reset.
    Serial.setTxBufferSize(HWCDC_TX_BUFFER_SIZE);
    Serial.begin(115200);
    Serial.printf("[mark] usb: pre-begin t=%lu ms\n", (unsigned long)tUsbPre);
    Serial.printf("[mark] usb: begin done t=%lu ms\n", (unsigned long)millis());
    for (uint32_t start = millis(); !Serial && millis() - start < 8000;) {
        delay(10);
    }
    Serial.printf("[mark] usb: host %s t=%lu ms\n",
                  Serial ? "connected" : "not detected", (unsigned long)millis());

    printStartupBanner();
    printChipInfo();
    printResetReason();
    printFlashInfo();
    printPsramInfo();
    printBoardProfile();

    Serial.printf("[mark] i2c: begin t=%lu ms\n", (unsigned long)millis());
    runI2cDiagnostics();
    Serial.printf("[mark] i2c: done t=%lu ms\n", (unsigned long)millis());

    // M2: passive RTC CLKOUT probe on GPIO15 (bounded setup burst; the 2 s
    // heartbeat loop below starts right after and is never blocked by it).
    // M2 P1 regression check: GPIO0 is the boot strapping pin; its IO MUX pad
    // register must be byte-identical before and after the whole PCNT setup
    // (a zero-initialized level_gpio_num would have made the driver touch it).
    Serial.printf("[mark] clkout: begin t=%lu ms\n", (unsigned long)millis());
    const uint32_t gpio0MuxBefore = REG_READ(IO_MUX_GPIO0_REG);
    bool clkoutOk = initClkoutProbe();
    if (clkoutOk) {
        for (int w = 1; w <= CLKOUT_WINDOWS; ++w) {
            measureClkoutWindow(w);
        }
        Serial.printf("clkout:    reference: 32768 Hz square => %d edge counts per %u ms window\n",
                      (int)(32768 * 2 * CLKOUT_WINDOW_MS / 1000), (unsigned)CLKOUT_WINDOW_MS);
    }
    const uint32_t gpio0MuxAfter = REG_READ(IO_MUX_GPIO0_REG);
    if (gpio0MuxBefore == gpio0MuxAfter) {
        Serial.printf("gpio0:      OK: IO_MUX pad unchanged by PCNT init (0x%08X), strapping pin untouched\n",
                      (unsigned)gpio0MuxAfter);
    } else {
        Serial.printf("gpio0:      REGRESSION: IO_MUX pad changed by PCNT init: 0x%08X -> 0x%08X\n",
                      (unsigned)gpio0MuxBefore, (unsigned)gpio0MuxAfter);
    }
    Serial.printf("[mark] clkout: done t=%lu ms\n", (unsigned long)millis());

    Serial.println();
    Serial.println("diag done (GPIO15 high-Z input probe only, RTC read-only, no sleep, no display init)");
    Serial.printf("[mark] loop: entering t=%lu ms\n", (unsigned long)millis());
}

void loop() {
    // Keep the device powered for a human to read the output; no sleep modes.
    // 2 s heartbeat: any capture window proves liveness quickly.
    static uint32_t last = 0;
    if (millis() - last >= HEARTBEAT_PERIOD_MS) {
        last = millis();
        Serial.printf("heartbeat: uptime %lu s\n", (unsigned long)(millis() / 1000));
    }
    delay(100);
}
