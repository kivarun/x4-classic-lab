# Отчёт — Задание 1: X4 Classic, минимальная диагностическая прошивка

Дата: 2026-09-26 · Репозиторий: `x4-classic-lab` (HEAD `b758d0e`) · Контейнер: `opencode-docker/esp32`

## Зафиксированные версии

| Компонент | Версия | Как зафиксировано |
|---|---|---|
| FreeInk SDK | `5deb923c33d97f918227b2ad983fda4d5a63c6ae` (main, 2026-09-26, merge PR #123) | git submodule `freeink-sdk` (gitlink в репо) |
| pioarduino platform-espressif32 | `55.03.37` (Arduino core 3.3.7 / ESP-IDF 5.5.x) | URL release-zip в `platformio.ini` `[base]` (immutable); пакет совпадает URL-спецификацией — не перекачивается |
| Arduino core (framework) | `3.3.7` — `esp32-core-3.3.7.tar.xz` + `-libs` (`5.5.0+sha.87912cd291`) | предустановлен в контейнере (`~/.platformio/packages`), оффлайн-сборка подтверждена |
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
- ✅ **Окружение проверено сборкой (оффлайн, сеть жёстко отключена
  мёртвым прокси + `UV_OFFLINE=1`; любая попытка загрузки упала бы
  мгновенно и видимо — загрузок не было)**:
  - минимальный smoke-проект (`/tmp/pio-min-test`, плата n16r8,
    без внешних либ) — SUCCESS, ~10 с;
  - сборка `env:x4c` — SUCCESS, ~12 с после прогрева фреймворка:
    RAM 7.4 % (24 132 / 327 680), Flash 5.4 % (356 710 / 6 553 600);
  - `esptool image-info`: bootloader и приложение — ESP32-S3,
    **DIO / 80 МГц / 16 MB** (board n16r8: flash_mode переопределён на dio,
    PSRAM-тип профиля qio_opi для R8-OPI);
  - в `firmware.bin` присутствуют строки баннера, версии, SHA SDK и
    имя профиля `xteink_x4_classic`.
- ✅ **Поправлена инжекция строковых макросов** в
  `scripts/build_version.py`: SCons передаёт `-D` через shell, который
  съедает одиночные кавычки — значение макроса раскрывалось как числовой
  литерал и ломало компиляцию; теперь используется экранированная форма
  `\"value\"`.
- ✅ **Обход бага pioarduino 55.03.37 (офлайн)**: `penv_setup.py` проверяет
  Python-зависимость по имени дистрибутива `platformio`, а форк ставит
  тот же код 6.1.19 как `pioarduino-core` → при каждой сборке попытка
  скачать zip (`~2 МБ`, GitHub) — оффлайн невозможна в принципе, онлайн —
  бесполезное перекачивание. `scripts/build_offline.sh` создаёт оверлей
  платформы в `/tmp/pio-platforms` (симлинки + однострочный фикс
  matcher'а в копии `penv_setup.py`) и собирает с мёртвым прокси.
  Оригинальный `~/.platformio` не изменён. Не-PIO-сабмодуль SDK
  (`lucide`, `-c81680e0`) в сборке не участвует (`library.json`
  BoardConfig без зависимостей) — для этой прошивки не нужен.

## Артефакты (этот коммит)

`.pio/build/x4c/`: `firmware.bin` (357 120), `firmware.elf`,
`firmware.map`, `bootloader.bin` (18 736), `partitions.bin` (3 072).
Копия выложена в `/exchange/outbox/x4c-classic-diag/artifacts/` вместе с
инструкцией прошивки (`FLASHING.md`).

## Открытые пункты / ограничения

1. ~~Сборка~~ — выполнена; `pio run` работает оффлайн через
   `scripts/build_offline.sh`, онлайн — без оверлея (каждый запуск
   повторно тянет penv-пакет pioarduino из-за бага matcher'а; сборка
   корректна, т.к. версия совпадает).
2. **Bootloader-совместимость**: заводской bootloader не проверен на
   приём образов IDF 5.5/Arduino 3.3.x. Если отклонит — полный набор
   (bootloader+partitions+app) с восстановлением из заводского дампа.
3. **otadata-состояние**: неизвестно, какой OTA-слот активен на заводе.
   Перед прошивкой сделать дамп `0xE000`+`0x2000`; если `otadata` указывает
   на `app1`, слот-переключение понадобится вручную.
4. **PSRAM-тип** (OPI/quad) не подтверждён на живом устройстве; профиль
   n16r8 собирается с `qio_opi` — PSRAM-инициализация будет видна в
   выводе прошивки (`psram: NOT DETECTED`, если не совпадёт).
5. **Per-экземпляр контроллер панели** (SSD1677/UC8179/UC8279 по NVS
   `hw_calib/screenType`) — к диагностике этой фазы не относится (дисплей
   не инициализируется).
6. SDK-отмеченные PENDING по X4C: роль GPIO4, полярность charge-STAT,
   ориентация панели — вне объёма фазы 1.
7. Репозиторий указывает remote `git@github.com:kivarun/x4-classic-lab.git` (SSH);
   публикация коммита — вне текущей задачи.
8. Контейнер имеет прямой доступ в интернет; все сборки этой сессии
   выполнены с сетевыми запретами (мёртвый прокси + `UV_OFFLINE=1`).
9. LSP/clangd «headers not found» — снимается генерацией
   compile_commands.json (`pio run -e x4c -t compiledb`), к сборке не
   относится.

## Статус: сборка воспроизводимо выполняется офлайн; артефакты готовы к прошивке
