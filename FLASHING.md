# FLASHING — X4 Classic diag (a1caced): контролируемая запись в app0 + OTA-переключение

Статус: **прошивка НЕ выполнялась** — это исполняемая инструкция для оператора.
Агент физического устройства не имеет; все операции выполняет оператор.

## Объект прошивки

| Артефакт | Значение |
|---|---|
| `artifacts/firmware.bin` | SHA-256 `805c71d379c4115a6dbe79dafa718d0f6bd20cd15ee7fef0e09665ef8808b832`, 357 104 байт |
| `firmware.elf` | с символами для отладки/addr2line |
| `bootloader.bin`, `partitions.bin` | **НЕ прошивать** (см. «Исключения») |
| Коммит прошивки | `a1caced` (FreeInk SDK `5deb923c33d9`) |
| Образ | ESP32-S3, DIO / 80 МГц / 16 MB, MMU page 64 KB, min chip rev v0.0, secure version 0, IDF `v5.5.2-729-g87912cd291` |

## Заводские факты (сняты с дампа, зафиксированы ранее)

- Разметка 16 MB dual-OTA: `nvs@0x9000` (20 KB), `otadata@0xE000` (8 KB),
  `app0@0x10000` (8064 KB), `app1@0x7F0000` (8064 KB), `spiffs@0xFD0000`,
  `coredump@0xFE4000`. Фабричного app-раздела factory **нет**.
- `otadata`: **две записи, seq=1 и seq=2, обе с корректными CRC, обе VALID**.
  На момент дампа активна **seq=2 → app1**.
- Полный **заводской дамп Flash сохранён отдельно** — обязателен для
  отката. Без него процедуру не начинать.

## Механизм переключения (почему только erase, без записи otadata)

Bootloader выбирает из otadata запись с максимальным `seq` среди валидных
(CRC/seq ≠ 0xFFFFFFFF); слот = `(seq − 1) % 2`: seq=1 → `app0`, seq=2 → `app1`.

Стёрть сектор с seq=2 → остаётся единственная валидная запись seq=1 → bootloader
выбирает **app0**. Новый образ otadata **не записывается вообще**: не задействуются
ни алгоритм CRC, ни раскладка записи (IDF 5.5: 32 байта `ota_seq`/`seq_label[20]`/
`ota_state`/CRC-over-seq; легаси-IDF: 40 байт `ota_seq`/`ota_hash[32]`/CRC-over-36 —
заводской bootloader читает свои заводские байты без изменений). Откат — точная
перезапись стёртого сектора и `app0` из дампов. Проверку CRC делает
`scripts/decode_otadata.py` (zlib.crc32 ≡ `esp_rom_crc32_le`; обе раскладки
распознаются автоматически).

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

Рабочий каталог оператора: `mkdir x4c-flash && cd x4c-flash`; PORT = `/dev/ttyACM0`
(или `esptool.py chip-id` с автоопределением).

1. `esptool.py --chip esp32s3 --port $PORT flash-id`
   → размер флеша должен быть **16 MB**; `chip-id` → ESP32-S3.
   Иначе: СТОП (не та флеш/плата).
2. `esptool.py --chip esp32s3 --port $PORT get-security-info`
   → flash encryption и secure boot должны быть **disabled**. Иначе: СТОП
   (чтение/запись будут шифрованными; диагностический образ несовместим).
3. Дамп текущего otadata:
   `esptool.py --chip esp32s3 --port $PORT --after no-reset read-flash 0xE000 0x2000 otadata_before.bin`
4. Дамп текущего app0 (≈8.26 MB, несколько минут):
   `esptool.py --chip esp32s3 --port $PORT --after no-reset read-flash 0x10000 0x7E0000 app0_before.bin`
5. Контроль соответствия заводскому дампу (дамп ≠ записям на устройстве → СТОП,
   состояние ушло с момента дампа — откат из дампа был бы неточным):
   - otadata: `dd if=factory_dump.bin of=otadata_factory.bin bs=4k skip=14 count=2`
     (0xE000 = 56 KiB), затем `sha256sum otadata_before.bin otadata_factory.bin`;
   - app0: `dd if=factory_dump.bin of=app0_factory.bin bs=64k skip=1 count=126`
     (срез 0x10000, 8 064 KiB), затем `sha256sum app0_before.bin app0_factory.bin`.
   Хэши обязаны совпасть попарно. Несовпадение хотя бы одного: СТОП, обсудить
   с инженером.
6. Декодирование и валидация otadata:
   `python3 scripts/decode_otadata.py otadata_before.bin`
   Ожидается: обе записи **VALID**, seq=1 → app0, seq=2 → app1, ACTIVE = seq=2 → app1,
   и строка `TO BOOT APP0: erase sector @ 0x0?000` — запомнить её офсет как `$SEQ2`.
   Любое иное состояние (нет валидных, единственная валидная ≠ seq=1, seq-значения
   другие, CRC-ошибки): СТОП.

## Фаза 1 — совместимость с заводским bootloader (только чтение)

Bootloader и таблица разделов в этой процедуре **не заменяются**. Совместимость
проверяется сравнением заголовков образов (тот же формат, что bootloader уже
загружает):

7. Заголовок заводского приложения (app1) — 4 KB чтение:
   `esptool.py --chip esp32s3 --port $PORT --after no-reset read-flash 0x7F0000 0x1000 app1_header.bin`
8. `esptool.py image-info app1_header.bin` и `esptool.py image-info artifacts/firmware.bin`
   — сверить поля ESP32-S3 Image/Extended Header:
   - Image version — должен совпадать (у diag: `1`);
   - Flash mode/size/freq — у diag: DIO / 16 MB / 80 m; у заводского — совпадающий
     режим (если заводское приложение собрано под иной режим — СТОП);
   - Chip ID — 9 (ESP32-S3) у обоих;
   - Minimal/Maximal chip revision — у diag v0.0…v655.35 (совместимо с любым чипом);
   - **MMU page size** — у diag **64 KB**; если заводское приложение заявляет
     иную страницу — СТОП (bootloader может принудительно проверять страницу
     MMU, раскладка маппинга не гарантирована);
   - eFuse block revision в диапазоне diag (0.0–1.99).
   Несовпадение какого-либо поля: СТОП — дальнейшие проверки (анализ заводского
   bootloader-образа, его версии IDF, решения о замене bootloader'а) — вне этой
   процедуры, требуются отдельное согласование и заводской дамп.

   Примечание: `esptool image-info` на усечённом 4 KB-слайсе может предупреждать
   о нехватке данных сегментов — поля заголовков при этом выводятся корректно.

## Фаза 2 — запись diag в неактивный app0

9. Запись (esptool всегда сам делает MD5-верификацию записанного; параметры
   заголовка по умолчанию `keep` — образ пишется байт-в-байт):
   `esptool.py --chip esp32s3 --port $PORT --before no-reset --after no-reset write-flash 0x10000 artifacts/firmware.bin`
10. Явная проверка записанного:
    `esptool.py --chip esp32s3 --port $PORT --before no-reset --after no-reset verify-flash 0x10000 artifacts/firmware.bin`
11. Убедиться, что otadata не изменился:
    `esptool.py --chip esp32s3 --port $PORT --before no-reset --after no-reset read-flash 0xE000 0x2000 otadata_check1.bin && sha256sum otadata_check1.bin otadata_before.bin`
    Хэши обязаны совпасть. Иначе: СТОП.

## Фаза 3 — контролируемое переключение и загрузка

12. Стёрть сектор otadata с seq=2 (офсет `$SEQ2` из шага 6):
    `esptool.py --chip esp32s3 --port $PORT --before no-reset --after no-reset erase-region $SEQ2 0x1000`
13. Проверить итоговое состояние otadata:
    `esptool.py --chip esp32s3 --port $PORT --before no-reset --after no-reset read-flash 0xE000 0x2000 otadata_switched.bin`
    `python3 scripts/decode_otadata.py otadata_switched.bin`
    Ожидается: одна VALID запись seq=1 → app0, сектор `$SEQ2` = erased.
    Любое иное: откат (Фаза 4) без загрузки.
14. Загрузка diag: переткнуть USB (аппаратный reset) — или
    `esptool.py --chip esp32s3 --port $PORT --before no-reset run`.
    Сразу открыть монитор: `pio device monitor --port $PORT 115200`
    (из каталога репозитория; прошивка ждёт подключения CDC до 8 с — баннер не потеряется).

**Ожидаемый вывод** (успех): баннер `X4 Classic diag (a1caced)`, `sdk commit:
5deb923c33d9`, причина перезагрузки, чип/Flash (16 MB, DIO)/PSRAM, профиль
`xteink_x4_classic`, I²C-скан, `bm8563: detected at 0x51`,
`CLKOUT (0x0D) = 0x80 -> FE=1 FD=0 (32.768 kHz)` (заводское ожидание), строка
`diag done`. Неувиденный PSRAM (`psram: NOT DETECTED`) — зафиксировать, это
полезная диагностика (тип PSRAM на живом устройстве не подтверждён).

**Если баннера нет** (bootloader отверг образ, падение/цикл): сразу Фаза 4,
далее СТОП — совместимость bootloader'а считать несовместимой до отдельного
анализа; bootloader НЕ заменять.

## Фаза 4 — точный откат (выполняется всегда после диагностики)

15. Восстановить app0 целиком:
    `esptool.py --chip esp32s3 --port $PORT --before no-reset --after no-reset write-flash 0x10000 app0_before.bin`
16. Восстановить оба сектора otadata (байт-в-байт заводское состояние):
    `esptool.py --chip esp32s3 --port $PORT --before no-reset --after no-reset write-flash 0xE000 otadata_before.bin`
17. Финальная проверка отката:
    `esptool.py --chip esp32s3 --port $PORT --before no-reset --after no-reset read-flash 0xE000 0x2000 otadata_after.bin`
    `esptool.py --chip esp32s3 --port $PORT --before no-reset --after no-reset read-flash 0x10000 0x7E0000 app0_after.bin`
    `sha256sum otadata_after.bin otadata_before.bin app0_after.bin app0_before.bin`
    Хэши обязаны совпасть попарно. `python3 scripts/decode_otadata.py otadata_after.bin`
    → снова seq=1 и seq=2 VALID, ACTIVE seq=2 → app1.
18. Переткнуть USB → устройство возвращается в заводское состояние
    (загружается app1). Подтвердить визуально работой штатной прошивки.

## СТОП-условия (остановиться и доложить)

- Нет полного заводского дампа Flash до начала.
- `flash-id`/`chip-id` не 16 MB/ESP32-S3; encryption/secure boot включены.
- Дрейф: текущие otadata/app0 не совпадают с заводским дампом.
- Декодер видит состояние, отличное от «seq=1 + seq=2, обе VALID, активна app1».
- Несовпадение полей заголовков образов в Фазе 1 (особенно MMU page size).
- Отсутствие баннера diag при загрузке из app0 (после отката).
- Любое несовпадение хэшей при записи/откате.

## Запреты

- Не прошивать `bootloader.bin` и `partitions.bin` (их замена — отдельное
  решение с заводским дампом под рукой).
- Не выполнять `erase-flash`, `erase-region` вне областей из таблицы выше.
- Не трогать `app1`, `nvs`/калибровки, `spiffs`, `coredump`.
- Не запускать активный тест CLKOUT и запись RTC-регистров — диагностика
  read-only по самой прошивке.
- Не менять GPIO15, не использовать sleep, не инициализировать дисплей.
