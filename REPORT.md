# Отчёт — Задание 1: X4 Classic, минимальная диагностическая прошивка

Дата: 2026-09-26 · Репозиторий: `x4-classic-lab` · Контейнер: `opencode-docker/esp32`

## Зафиксированные версии

| Компонент | Версия | Как зафиксировано |
|---|---|---|
| FreeInk SDK | `5deb923c33d97f918227b2ad983fda4d5a63c6ae` (main, 2026-09-26, merge PR #123) | git submodule `freeink-sdk` (gitlink в репо) |
| pioarduino platform-espressif32 | `55.03.37` (Arduino core 3.3.7 / ESP-IDF 5.5.x) | URL release-zip в `platformio.ini` `[base]` (immutable) |
| Arduino core (framework) | `3.3.7` — `esp32-core-3.3.7.tar.xz` + `-libs` | объявлен платформой 55.03.37; **не установлен** (см. ограничения) |
| toolchain | `tool-esp_install @ 5.3.4` | предустановлен в контейнере |
| PlatformIO Core | `6.1.19` | предустановлен (`/opt/esp32-venv`) |
| esptool | `5.3.1` | предустановлен (`/opt/esp32-venv`) |

## Реализация

- `platformio.ini` — зеркало проверенного `[base]` + `[env:x4c]` из
  `platformio.sample.ini` SDK: доска `esp32-s3-devkitc1-n16r8`,
  `flash_mode=dio`, `flash_size=16MB`, `BOARD_HAS_PSRAM`,
  `USE_BLOCK_DEVICE_INTERFACE=1`, `FREEINK_DEVICE_X4CLASSIC=1`
  (авто-включает `FREEINK_CAP_RTC`, `FREEINK_BATTERY_I2C_GAUGE`,
  `FREEINK_SD_SDMMC`; touch/frontlight остаются выключенными).
- `freeink-sdk/` — submodule на pinned SHA. Драйверы не копируются:
  пины/шина/адрес берутся из `BoardConfig::ACTIVE`
  (`namespace BoardConfig`, SDA 39 / SCL 38 @ 400 kHz, RTC 0x51).
- `src/main.cpp` — вывод через USB CDC (`ARDUINO_USB_MODE=1`,
  `ARDUINO_USB_CDC_ON_BOOT=1`, 115200): версия сборки (git describe),
  SHA SDK, причина перезагрузки (`esp_reset_reason`), чип rev
  (`esp_chip_info`, кодировка full-revision → «0.2»), Flash
  (размер/частота/режим из заголовка образа), PSRAM
  (`ESP.getPsramSize()` + `MALLOC_CAP_SPIRAM`), скан шины I2C, детекция
  BM8563 по `0x51`, чтение `CLKOUT` (`0x0D`) с расшифровкой
  FE/FD; ожидаемое заводское значение `0x80` (выход 32,768 кГц включён —
  согласуется с гипотезой GPIO15/`XTAL_32K_P`).
- `scripts/build_version.py` — `FIRMWARE_VERSION` (git describe) и
  `FREEINK_SDK_SHA` (submodule status) компилируются в бинарь.
- `partitions_x4c_factory.csv` — справочная заводская dual-OTA разметка
  (не подключена к сборке; для документации/восстановления).

Соблюдены запреты: RTC-регистры только читаются (SDK `Rtc::begin()` не
используется — он сам пишет `0x00` в CLKOUT, `Rtc.cpp:101`); GPIO15 не
конфигурируется; sleep-режимы не запускаются; дисплей не инициализируется
(дисплейные/input/SD библиотеки SDK в `lib_deps` не включены — только
`BoardConfig`); BLE отсутствует. На устройство ничего не записано.

## Проверки

- ✅ Структура SDK подтверждена из клона: `BoardConfig` (включая полный
  профиль `XTEINK_X4_CLASSIC`, строки 1700–1800 `BoardConfig.h`),
  API `Rtc` (найдена причина отказаться от него для этой задачи).
- ✅ `platformio.sample.ini` и `docs/xteink-x4c-support.md` сверены с
  исходными данными (SDA 39/SCL 38, 0x51, 16 MB/DIO, dual-OTA).
- ✅ Submodule привязан, `git submodule status` без признака расхождения.
- ⚠️ **Сборка не выполнена** — пользователь запретил скачивание недостающих
  framework-пакетов (правило «не качать»). `.bin`/`.elf` пока не получены.
- ⚠️ LSP/clangd показывает ожидаемые ошибки «headers not found» — без
  Arduino core; снимется после первой сборки.

## Открытые пункты / ограничения

1. **Сборка**: первый `pio run` должен скачать `framework-arduinoespressif32`
   (3.3.7) + `framework-arduinoespressif32-libs` (~300 МБ, GitHub). Требуется
   разрешение или офлайн-архив. Это единственный блокирующий пункт для
   получения `.bin`/`.elf`.
2. **Bootloader-совместимость**: заводской bootloader не проверен на
   приём образов IDF 5.5/Arduino 3.3.x. Если отклонит — полный набор
   (bootloader+partitions+app) с восстановлением из заводского дампа.
3. **otadata-состояние**: неизвестно, какой OTA-слот активен на заводе.
   Перед прошивкой сделать дамп `0xE000`+`0x2000`; если `otadata` указывает
   на `app1`, слот-переключение понадобится вручную.
4. **PSRAM-тип** (OPI/quad) не подтверждён esptool; профиль x4c собирается
   с настройками по умолчанию n16r8 (`qio_opi`) — PSRAM-инициализация
   будет видна в выводе прошивки (`psram: NOT DETECTED`, если не совпадёт).
5. **Per-экземпляр контроллер панели** (SSD1677/UC8179/UC8279 по NVS
   `hw_calib/screenType`) — к диагностике этой фазы не относится (дисплей
   не инициализируется).
6. SDK-отмеченные PENDING по X4C: роль GPIO4, полярность charge-STAT,
   ориентация панели — вне объёма фазы 1.
7. Репозиторий указывает remote `git@github.com:kivarun/x4-classic-lab.git` (SSH);
   публикация коммита — вне текущей задачи.

## Статус: подготовка завершена, сборка ожидает разрешения на скачивание
