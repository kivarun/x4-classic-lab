# x4-classic-lab — X4 Classic (X4C) минимальная диагностическая прошивка

ESP32-S3 e-reader XTEINK X4 Classic: FreeInk SDK (submodule, pinned SHA) +
минимальная диагностическая прошивка для первой аппаратной проверки.

## Состав

- `platformio.ini` — зеркало проверенного `[base]` + `[env:x4c]` из
  `platformio.sample.ini` FreeInk SDK (pioarduino 55.03.37 = Arduino-ESP32
  3.3.7 / ESP-IDF 5.5.x, доска `esp32-s3-devkitc1-n16r8`, DIO / 16 MB,
  `-DFREEINK_DEVICE_X4CLASSIC=1`).
- `freeink-sdk/` — git submodule, зафиксирован по commit SHA
  (`git submodule status`). Аппаратные драйверы SDK не копируются: пины и
  адрес I2C берутся из `BoardConfig::XTEINK_X4_CLASSIC` (`BoardConfig::ACTIVE`).
- `src/main.cpp` — диагностическая прошивка (только USB Serial, 115200).
- `scripts/build_version.py` — подставляет `FIRMWARE_VERSION`
  (`git describe`) и `FREEINK_SDK_SHA` в бинарь.
- `partitions_x4c_factory.csv` — справочная копия заводской dual-OTA
  разметки 16 MB (только для документации).

## Что печатает прошивка при старте

- версия сборки (`FIRMWARE_VERSION`) и SHA SDK (`FREEINK_SDK_SHA`);
- причина перезагрузки (`esp_reset_reason`);
- чип (ESP32-S3 rev), параметры Flash (размер/частота/режим) и PSRAM
  (`ESP.getPsramSize()`, heap `MALLOC_CAP_SPIRAM`);
- параметры профиля платы из `BoardConfig::ACTIVE`;
- **read-only** диагностика I2C (SDA 39 / SCL 38 @ 400 kHz из профиля):
  скан шины, обнаружение BM8563 по `0x51`, чтение регистра `CLKOUT`
  (`0x0D`) и расшифровка (ожидаемое заводское значение `0x80` =
  выход 32,768 кГц включён).

Ограничения, заложенные в прошивку намеренно: регистры RTC не пишутся,
GPIO15 (`XTAL_32K_P`) не переконфигурируется, sleep-режимы не запускаются,
дисплей не инициализируется. SDK-шный `Rtc::begin()` не используется —
он сам пишет `0x00` в CLKOUT (`Rtc.cpp:101`).

## Сборка (в контейнере `opencode-docker/esp32`)

```bash
scripts/build_offline.sh   # офлайн-сборка без доступа к сети (см. ниже)
pio run                    # обычная сборка (сеть не требуется, все пакеты предустановлены)
pio run -e x4c             # явно
```

Артефакты: `.pio/build/x4c/firmware.bin`, `firmware.elf`,
`bootloader.bin`, `partitions.bin`.

**Офлайн-сборка.** Все нужные пакеты предустановлены
(`~/.platformio/packages`: pioarduino 55.03.37, Arduino core 3.3.7 +
`-libs` 5.5.0, toolchain 14.2.0, esptoolpy 5.1.2), но у платформы
pioarduino 55.03.37 есть баг проверки Python-зависимостей penv
(`penv_setup.py` ищет дистрибутив с именем `platformio`, а форк ставит тот
же код под именем `pioarduino-core`): без сети `pio run` падает, при наличии
сети — каждый раз безуспешно тянет zip по URL. `scripts/build_offline.sh`
создаёт оверлей-копию платформы в `/tmp/pio-platforms` с однострочным
фиксом matcher'а и запускает сборку с мёртвым прокси + `UV_OFFLINE=1` —
любая попытка неявной загрузки мгновенно видна как ошибка. Оригинальный
`~/.platformio` не изменяется. Без сети работает и `pio run`, и
`scripts/build_offline.sh`; отличия см. `REPORT.md`.

## Процедура прошивки (первая аппаратная диагностика)

Заводская разметка — dual-OTA (nvs@0x9000, otadata@0xE000, app0@0x10000,
app1@0x7F0000, spiffs@0xFD0000, coredump@0xFE4000). Заводской дамп Flash
сохранён отдельно; он обязателен для восстановления.

Подтверждённое состояние otadata: записи seq=1 и seq=2, обе VALID
(CRC совпадают с независимыми контрольными значениями `0x4743989A`/`0x55F63774`,
формула ESP-IDF `esp_rom_crc32_le(0xFFFFFFFF, &ota_seq, 4)`); активна seq=2 →
**app1**. Поэтому diag пишется в
**неактивный app0**, затем контролируемое OTA-переключение: erase 4 KB-
сектора otadata с seq=2 → остаётся seq=1 → bootloader грузит app0.
Откат — точная перезапись app0 и стёртого сектора otadata из дампов.
Bootloader и таблица разделов НЕ заменяются; совместимость закрывается
префлайт-гейтом (сверка заголовков заводского app1 и diag-образа).

**Полная исполняемая процедура: `FLASHING.md`** (фазы 0–4: дампы и
сверки → запись в app0 → переключение → загрузка → откат; СТОП-условия;
`scripts/decode_otadata.py` — декодер/валидатор otadata с тестами
`scripts/test_decode_otadata.py` на независимых контрольных CRC). На этапе
подготовки ничего на физическое устройство не записывается.

## Отчёт

`REPORT.md` — зафиксированные SHA/версии, результаты проверок, ограничения.
