# FLASHING — X4 Classic diag (M1): контролируемая запись в app0 + OTA-переключение + захват загрузки

Статус: **M1 — вторая диагностическая сборка; на устройстве ещё не выполнялась**.
Первая сборка (`a1caced`, SHA-256 `805c71d3…b832`) была прошита и откачана:
загрузка дала 75 с тишины на USB без баннера/heartbeat (анализ — в отчёте M1:
кольцевой буфер HWCDC 256 Б с вытеснением старейших + гонка ответа FLASH_END с
сбросом чипа в `esptool run`). M1 исправляет наблюдаемость загрузки.
Агент физического устройства не имеет; все операции выполняет оператор.

## Объект прошивки

| Артефакт | Значение |
|---|---|
| `firmware.bin` | SHA-256 и размер — **в отчёте M1** (`/exchange/outbox/m1-boot-observability/REPORT.md`) и в `firmware.bin.sha256` рядом с артефактом; сверка обязательна (шаги 1 и 11) |
| `firmware.elf` | с символами для отладки/addr2line |
| `bootloader.bin`, `partitions.bin` | **НЕ прошивать** (см. «Исключения») |
| Артефакты M1 | `/exchange/outbox/m1-boot-observability/artifacts/` |
| Коммит прошивки | M1 в `x4-classic-lab` (FreeInk SDK `5deb923c33d9`) — точный SHA см. в отчёте M1 |
| Образ | ESP32-S3, DIO / 80 МГц / 16 MB, MMU page 64 KB, min chip rev v0.0, secure version 0, IDF `v5.5.2-729-g87912cd291` |

Артефакты M1 лежат в `/exchange/outbox/m1-boot-observability/artifacts/`. Перед
стартом оператор копирует `firmware.bin` в свой рабочий каталог, сверяет SHA-256
с отчётом M1/sidecar-файлом (шаг 1 Фазы 0) и записывает значение в `$FW_SHA`
для шагов 11/последующих сверок.

## Пути (определить один раз, все команды — только через них)

```sh
WORK=/путь/к/операторскому-каталогу    # mkdir -p $WORK; cd $WORK — все дампы и артефакты живут здесь
REPO=/путь/к/клону/x4-classic-lab      # клон репозитория с процедурой
SCRIPT=$REPO/scripts/decode_otadata.py # декодер otadata
FW=$WORK/firmware.bin                  # diag-образ (копия из artifacts/), НЕ $REPO-относительный путь
FACTORY_DUMP=$WORK/factory_dump.bin    # полный заводской дамп Flash (16 MB, 16 777 216 байт)
PORT=/dev/ttyACM0                      # USB-порт устройства
```

Пути в командах ниже — только через эти переменные. Относительные пути
(`artifacts/firmware.bin`, `scripts/decode_otadata.py`) в командах не
использовать: рабочий каталог оператора и каталог репозитория — разные
каталоги.

## Заводские факты (сняты с дампа, переподтверждены исправленным декодером)

- Разметка 16 MB dual-OTA: `nvs@0x9000` (20 KB), `otadata@0xE000` (8 KB),
  `app0@0x10000` (8064 KB), `app1@0x7F0000` (8064 KB), `spiffs@0xFD0000`,
  `coredump@0xFE4000`. Фабричного app-раздела factory **нет**.
- `otadata`: две записи, обе проходят проверку подтверждённым алгоритмом
  ESP-IDF (`esp_ota_select_entry_t`, 32 байта; CRC32 =
  `esp_rom_crc32_le(0xFFFFFFFF, &ota_seq, 4)`):
  - сектор 0xE000: seq=1 → app0, `ota_state=VALID(0x2)`, CRC stored `0x4743989A` ✓;
  - сектор 0xF000: seq=2 → app1, `ota_state=VALID(0x2)`, CRC stored `0x55F63774` ✓;
  - активна **seq=2 → app1**; цель erase для переключения — **0xF000**.
  Контрольные CRC получены независимо (отдельная C-реализация ROM-семантики
  CRC32, не используемый в декодере код) и совпали с заводскими байтами.
- Полный **заводской дамп Flash сохранён отдельно** — обязателен для
  отката. Без него процедуру не начинать.
- Контрольные SHA-256 срезов заводского дампа (оператор сверяет свои
  срезы — расхождение означает, что дамп другой или повреждён):

  | Срез | Команда `dd` | SHA-256 |
  |---|---|---|
  | otadata (0xE000, 8 KB) | `bs=4k skip=14 count=2` | `b7e293bb607d3bddb99b7f38a7a45afd5823c0c61e3216e67858bbc759535282` |
  | app0 (0x10000, 8064 KB) | `bs=64k skip=1 count=126` | `45a21d41c8933df1eb763a30f33673fb974b879abe989a1e272510fef9a8ec16` |
  | app1 (0x7F0000, 8064 KB) | `bs=64k skip=127 count=126` | `57f29c2c40c98baeb3bd668ea7975cff5ae8afa989ff05cd6c15c045724795fd` |

## Механизм переключения (почему только erase, без записи otadata)

Bootloader выбирает из otadata запись с максимальным `seq` среди валидных
(валидность: `ota_seq ≠ 0xFFFFFFFF`, `ota_state ∉ {INVALID(0x3), ABORTED(0x4)}`,
CRC совпадает); слот = `(seq − 1) % 2`: seq=1 → `app0`, seq=2 → `app1`.

Стёрть сектор с seq=2 → остаётся единственная валидная запись seq=1 → bootloader
выбирает **app0**. Новый образ otadata **не записывается вообще**: не задействуются
ни алгоритм CRC, ни раскладка записи, заводской bootloader читает свои заводские
байты без изменений. Откат — точная перезапись стёртого сектора и `app0` из дампов.
Проверку CRC делает `scripts/decode_otadata.py` (формат подтверждён по исходникам
ESP-IDF v4.4.7/v5.2.2 и дизассемблированию `libbootloader_support.a` IDF 5.5;
легаси-лэйаут «40 байт» из прежней редакции не существует — удалён).

## Затрагиваемые области (единственные)

| Область | Действие | Откат |
|---|---|---|
| `app0` (0x10000, 8 064 KB) | запись diag | полная перезапись из `app0_before.bin` |
| otadata, один 4 KB сектор (0xE000 или 0xF000 — определяет декодер) | erase | точная перезапись сектора из `otadata_before.bin` |

`app1`, `nvs` (калибровки), `spiffs`, `coredump`, bootloader (0x0–0x8000),
таблица разделов (0x8000–0x9000) — **не изменяются никогда**.

## Вход в download mode

USB-порт — ESP32-S3 native (USB-Serial-JTAG, D−/D+ = GPIO19/20).

- A (пробовать первым): `esptool.py --chip esp32s3 chip-id` — если отвечает,
  download mode доступен штатным reset-танцем esptool.
- B (если A не подключается — заводская прошивка может держать USB в OTG/MSC,
  отключая USB-Serial-JTAG): зажать **левую боковую клавишу** (GPIO0, strap
  «up/prev») и переткнуть USB → ROM download mode → отпустить после старта
  `esptool`.

## Фаза 0 — префлайт и дампы (только чтение)

Все чтения флеша — с `--no-stub` (ROM-путь без загрузчика-стаба: резервные
дампы и все хэш-сверки получаются единственным детерминированным способом;
ROM-чтение 8 MB занимает несколько минут — это норма).

1. Подготовка артефактов и сверка диагностического бинарника (M1):
   `cp /exchange/outbox/m1-boot-observability/artifacts/firmware.bin $FW` (или
   получить артефакты из места, согласованном с отчётом), затем
   `sha256sum $FW` — обязано совпасть с SHA-256 из отчёта M1 и из
   `firmware.bin.sha256` рядом с артефактом. Несовпадение: СТОП
   (чужой/повреждённый образ).
2. `esptool.py --chip esp32s3 --port $PORT flash-id`
   → размер флеша должен быть **16 MB**; `chip-id` → ESP32-S3.
   Иначе: СТОП (не та флеш/плата).
3. `esptool.py --chip esp32s3 --port $PORT get-security-info`
   → flash encryption и secure boot должны быть **disabled**. Иначе: СТОП
   (чтение/запись будут шифрованными; диагностический образ несовместим).
4. Дамп текущего otadata (резерв):
   `esptool.py --chip esp32s3 --port $PORT --no-stub --after no-reset read-flash 0xE000 0x2000 otadata_before.bin`
5. Дамп текущего app0 (резерв; ≈8.26 MB, несколько минут):
   `esptool.py --chip esp32s3 --port $PORT --no-stub --after no-reset read-flash 0x10000 0x7E0000 app0_before.bin`
6. Контроль соответствия заводскому дампу (дамп ≠ записям на устройстве → СТОП,
   состояние ушло с момента дампа — откат из дампа был бы неточным):
   - срезы из заводского дампа:
     `dd if=$FACTORY_DUMP of=otadata_factory.bin bs=4k skip=14 count=2`,
     `dd if=$FACTORY_DUMP of=app0_factory.bin bs=64k skip=1 count=126`,
     затем `sha256sum otadata_factory.bin app0_factory.bin` — сверить с
     контрольными хэшами из таблицы выше (целостность самого дампа);
   - устройство: `sha256sum otadata_before.bin app0_before.bin` — обязаны
     совпасть с хэшами срезов попарно. Несовпадение хотя бы одного: СТОП,
     обсудить с инженером.
7. Декодирование и валидация otadata:
   `python3 $SCRIPT otadata_before.bin`
   Ожидается: обе записи **VALID**, seq=1 → app0 (CRC `0x4743989A`), seq=2 → app1
   (CRC `0x55F63774`), ACTIVE = seq=2 → app1, и строка
   `TO BOOT APP0: erase sector @ 0x0?000` — запомнить её офсет как `$SEQ2`
   (по заводским фактам это `0xF000`).
   Любое иное состояние (нет валидных, единственная валидная ≠ seq=1, seq-значения
   другие, CRC-ошибки, `ota_state` ≠ VALID): СТОП.

## Фаза 1 — совместимость с заводским bootloader (только чтение)

Bootloader и таблица разделов в этой процедуре **не заменяются**. Совместимость
проверяется сравнением заголовков образов (тот же формат, что bootloader уже
загружает). Для анализа используется **полный образ заводского app1**,
извлечённый из `$FACTORY_DUMP` (не усечённый 4 KB-слайс: `image-info` на
усечённом файле предупреждает о нехватке сегментов и не может провалидировать
контрольную сумму образа).

8. Извлечь полный app1 из заводского дампа и сверить его целостность:
   `dd if=$FACTORY_DUMP of=app1_factory.bin bs=64k skip=127 count=126`
   (0x7F0000, 8 064 KB), затем
   `sha256sum app1_factory.bin` — обязано быть
   `57f29c2c40c98baeb3bd668ea7975cff5ae8afa989ff05cd6c15c045724795fd`.
9. Проверка соответствия текущему устройству (полное чтение app1, несколько
   минут):
   `esptool.py --chip esp32s3 --port $PORT --no-stub --after no-reset read-flash 0x7F0000 0x7E0000 app1_device.bin`
   затем `sha256sum app1_device.bin app1_factory.bin` — хэши обязаны совпасть
   (на устройстве именно тот образ, что в дампе; иначе дрейф — СТОП).
10. `esptool.py image-info $WORK/app1_factory.bin` и
    `esptool.py image-info $FW` — сверить поля ESP32-S3 Image/Extended Header:

    | Поле | Диагностический образ (по факту сборки) | Требование к заводскому |
    |---|---|---|
    | Image version | 1 | совпадать (факт: 1) |
    | Flash mode/size/freq | DIO / 16 MB / 80 m | совпадать |
    | Chip ID | 9 (ESP32-S3) | 9 |
    | Min/Max chip revision | v0.0 … v655.35 | внутри диапазона diag (факт: v0.0 … v655.35) |
    | eFuse block revision | 0.0–1.99 | внутри диапазона diag |
    | **MMU page size** | **64 KB** | совпадать (иное — СТОП) |
    | Secure version | 0 | совпадать |

    Несовпадение какого-либо поля: СТОП — дальнейшие проверки (анализ заводского
    bootloader-образа, его версии IDF, решения о замене bootloader'а) — вне этой
    процедуры, требуются отдельное согласование и заводской дамп.

    Справочно (заводской app1 из дампа): проект `arduino-lib-builder`,
    версия `idf-master-106-gc9c8a06-dirty`, IDF v5.5.4 — формат otadata с
    `ota_state` подтверждается.

## Фаза 2 — запись diag в неактивный app0

11. Перед записью — контрольная сверка бинарника (обязательна непосредственно
    перед `write-flash`):
    `sha256sum $FW` — обязано совпадать с SHA-256 из отчёта M1 и с результатом
    шага 1. Несовпадение: СТОП (образ повреждён/подменён между Фазой 0 и записью).
12. Запись (esptool всегда сам делает MD5-верификацию записанного; параметры
    заголовка по умолчанию `keep` — образ пишется байт-в-байт):
    `esptool.py --chip esp32s3 --port $PORT --before no-reset --after no-reset write-flash 0x10000 $FW`
13. Явная проверка записанного:
    `esptool.py --chip esp32s3 --port $PORT --before no-reset --after no-reset verify-flash 0x10000 $FW`
14. Убедиться, что otadata не изменился:
    `esptool.py --chip esp32s3 --port $PORT --no-stub --before no-reset --after no-reset read-flash 0xE000 0x2000 otadata_check1.bin && sha256sum otadata_check1.bin otadata_before.bin`
    Хэши обязаны совпасть. Иначе: СТОП.

## Фаза 3 — контролируемое переключение и загрузка

15. Стёрть сектор otadata с seq=2 (офсет `$SEQ2` из шага 7):
    `esptool.py --chip esp32s3 --port $PORT --before no-reset --after no-reset erase-region $SEQ2 0x1000`
16. Проверить итоговое состояние otadata:
    `esptool.py --chip esp32s3 --port $PORT --no-stub --before no-reset --after no-reset read-flash 0xE000 0x2000 otadata_switched.bin`
    `python3 $SCRIPT otadata_switched.bin`
    Ожидается: одна VALID запись seq=1 → app0, сектор `$SEQ2` = erased.
    Любое иное: откат (Фаза 4) без загрузки.
17. Загрузка diag с захватом (M1) — сброс через esptool и захват портом
    **строго последовательны, одновременное владение `$PORT` запрещено**:
    `python3 $REPO/scripts/capture_usb.py --port $PORT --seconds 120`
    Скрипт сам выполняет две фазы:
    - Фаза 1: `esptool --chip esp32s3 --port $PORT --after hard-reset chip-id`
      (безобидная ROM-команда без flash-операций, затем RTS-сброс в нормальный
      бут при IO0=high — приложение стартует); esptool освобождает порт и
      завершается ДО открытия монитора.
    - Фаза 2: захват с таймштампами, автопереподключением при переэнумерации
      USB, DTR/RTS=0 до открытия порта, до общего дедлайна; лог-файл печатается
      в конце.
    Прошивка ждёт подключения CDC до 8 с (TX-буфер 4096 Б сохраняет весь
    стартовый вывод даже при позднем подключении), heartbeat каждые 2 с.

**Ожидаемый вывод** (успех; в логе захвата): маркеры `[mark] usb: pre-begin /
begin done / host connected`, баннер `X4 Classic diag (<M1-версия>)`,
`sdk commit: 5deb923c33d9`, причина перезагрузки, чип/Flash (16 MB, DIO)/PSRAM,
профиль `xteink_x4_classic`, `[mark] i2c: begin/done`, I²C-скан,
`bm8563: detected at 0x51`, `CLKOUT (0x0D) = 0x80 -> FE=1 FD=0 (32.768 kHz)`
(заводское ожидание), `[mark] loop: entering`, `diag done`, далее
`heartbeat: uptime N s` каждые 2 с. Неувиденный PSRAM (`psram: NOT DETECTED`)
— зафиксировать, это полезная диагностика (тип PSRAM на живом устройстве не
подтверждён).

**Интерпретация отсутствия баннера (M1):** ошибки протокола esptool
(`Invalid head of packet` и т.п.) — **не доказательство падения прошивки**
(гонка ответа FLASH_END с сбросом чипа); вердикт — только лог захвата:
- маркеры/баннер есть → приложение живо, диагностика удалась;
- полная тишина ≥2 мин при живом переэнумерирующемся порте и отвечающем
  `chip-id` → чип в download mode, приложение не стартовало: Фаза 4, СТОП;
- периодические переэнумерации/чатор без маркеров приложения → boot-loop до
  инициализации USB: Фаза 4, СТОП (причина — reset reason в чаторе).

## Фаза 4 — точный откат (выполняется всегда после диагностики)

18. Восстановить app0 целиком:
    `esptool.py --chip esp32s3 --port $PORT --before no-reset --after no-reset write-flash 0x10000 app0_before.bin`
19. Восстановить оба сектора otadata (байт-в-байт заводское состояние):
    `esptool.py --chip esp32s3 --port $PORT --before no-reset --after no-reset write-flash 0xE000 otadata_before.bin`
20. Финальная проверка отката:
    `esptool.py --chip esp32s3 --port $PORT --no-stub --before no-reset --after no-reset read-flash 0xE000 0x2000 otadata_after.bin`
    `esptool.py --chip esp32s3 --port $PORT --no-stub --before no-reset --after no-reset read-flash 0x10000 0x7E0000 app0_after.bin`
    `sha256sum otadata_after.bin otadata_before.bin app0_after.bin app0_before.bin`
    Хэши обязаны совпасть попарно. `python3 $SCRIPT otadata_after.bin`
    → снова seq=1 и seq=2 VALID, ACTIVE seq=2 → app1.
21. Переткнуть USB → устройство возвращается в заводское состояние
    (загружается app1). Подтвердить визуально работой штатной прошивки.

## СТОП-условия (остановиться и доложить)

- Нет полного заводского дампа Flash до начала.
- SHA-256 `$FW` не равен ожидаемому (шаги 1 и 11).
- `flash-id`/`chip-id` не 16 MB/ESP32-S3; encryption/secure boot включены.
- Срезы заводского дампа не совпали с контрольными SHA-256 (дамп другой/повреждён).
- Дрейф: текущие otadata/app0 не совпадают с заводским дампом.
- Декодер видит состояние, отличное от «seq=1 + seq=2, обе VALID с CRC
  `0x4743989A`/`0x55F63774`, активна app1».
- Несовпадение полей заголовков образов в Фазе 1 (особенно MMU page size).
- Отсутствие маркеров/баннера в логе захвата при загрузке diag (шаг 17,
  интерпретация — там же).
- Любое несовпадение хэшей при записи/откате.

## Запреты

- Не прошивать `bootloader.bin` и `partitions.bin` (их замена — отдельное
  решение с заводским дампом под рукой).
- Не выполнять `erase-flash`, `erase-region` вне областей из таблицы выше.
- Не трогать `app1`, `nvs`/калибровки, `spiffs`, `coredump`.
- Не запускать активный тест CLKOUT и запись RTC-регистров — диагностика
  read-only по самой прошивке.
- Не менять GPIO15, не использовать sleep, не инициализировать дисплей.
