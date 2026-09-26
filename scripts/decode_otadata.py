#!/usr/bin/env python3
# X4 Classic — otadata (0xE000, 0xF000) decoder for the controlled OTA switch.
#
# Parses the two 4KB otadata sectors, validates each record against the
# esp_ota_select_entry_t layout, reports which OTA slot (app0/app1) the
# bootloader will select, and names the exact sector to erase to force app0.
#
# Layout (esp_ota_select_entry_t, 32 bytes; confirmed in ESP-IDF v4.4.7,
# v5.2.2 and v5.5 sources/objects — the "legacy 40B" layout attributed to
# older IDF in commit defcc00 does not exist and was removed):
#   ota_seq @0, seq_label[20] @4, ota_state @24, crc @28, 0xFF padding to 4KB.
#
# CRC (ESP-IDF bootloader_common_ota_select_crc(), proven by disassembly of
# the shipped IDF 5.5.0+sha.87912cd291 libbootloader_support.a:
#   esp_rom_crc32_le(0xFFFFFFFF, (uint8_t *)&s->ota_seq, 4)
# esp_rom_crc32_le applies ^~0 before and after the update, so in Python:
#   crc = zlib.crc32(seq_bytes, 0xFFFFFFFF) & 0xFFFFFFFF
# (NOT plain zlib.crc32(seq_bytes) — init differs).
#
# Record validity (bootloader_common_ota_select_valid(), same object):
#   invalid if ota_seq == 0xFFFFFFFF, or ota_state in {0x3 INVALID, 0x4 ABORTED};
#   valid iff not invalid and s->crc == bootloader_common_ota_select_crc(s).
#   ota_state is read for every record; on pre-5.3 IDF the field position held
#   the tail of the seq_label/hash area instead — a {3,4}-looking value there
#   makes this decoder conservative (STOP), never permissive.
#
# Slot mapping: slot = (seq - 1) % 2 -> 0 = app0 (ota_0), 1 = app1 (ota_1).
# Selection: the valid record with the HIGHEST seq wins.
#
# Never writes anything: read-only analysis of dump files.

import argparse
import sys
import zlib

OTA_SEQ_MAGIC_ERASED = 0xFFFFFFFF
RECORD_LEN = 32
SECTOR = 0x1000

IMG_STATES = {
    0x0: "INVALID(0x0)/NEW",
    0x1: "PENDING_VERIFY",
    0x2: "VALID",
    0x3: "INVALID",
    0x4: "ABORTED",
    0xFFFFFFFF: "UNDEFINED",
}

OTA_STATE_UNBOOTABLE = (0x3, 0x4)  # ESP_OTA_IMG_INVALID, ESP_OTA_IMG_ABORTED


def all_ff(data):
    return data == b"\xff" * len(data)


def crc32_seq(seq_bytes):
    """ESP-IDF bootloader_common_ota_select_crc(): CRC over the 4-byte
    ota_seq field, esp_rom_crc32_le(0xFFFFFFFF, &ota_seq, 4)."""
    return zlib.crc32(seq_bytes, 0xFFFFFFFF) & 0xFFFFFFFF


def decode_sector(data, abs_offset):
    info = {"offset": abs_offset, "seq": None, "valid": False}

    if all_ff(data[:SECTOR]):
        info.update(layout="erased", detail="sector fully erased (0xFF)")
        return info

    seq = int.from_bytes(data[0:4], "little")
    info["seq"] = seq
    if seq == OTA_SEQ_MAGIC_ERASED:
        info.update(layout="erased", detail="ota_seq = 0xFFFFFFFF (erased record)")
        return info

    crc_stored = int.from_bytes(data[28:32], "little")
    crc_calc = crc32_seq(data[0:4])
    state = int.from_bytes(data[24:28], "little")
    label = data[4:24]
    crc_ok = crc_stored == crc_calc
    state_ok = state not in OTA_STATE_UNBOOTABLE
    info.update(
        layout="esp_ota_select_entry_t (32B)",
        crc_ok=crc_ok,
        state="0x%08X (%s)" % (state, IMG_STATES.get(state, "UNKNOWN")),
        label=label.hex(),
    )

    if not all_ff(data[RECORD_LEN:]):
        info["detail"] = "no layout matched: data beyond 32-byte record is not 0xFF"
        return info
    if not crc_ok:
        info["detail"] = "CRC mismatch (esp_rom_crc32_le(0xFFFFFFFF, ota_seq, 4)): stored 0x%08X != calc 0x%08X" % (
            crc_stored,
            crc_calc,
        )
        return info
    if not state_ok:
        info["detail"] = "ota_state 0x%08X (%s) — bootloader will not select this record" % (
            state,
            IMG_STATES.get(state, "UNKNOWN"),
        )
        return info

    info["valid"] = True
    info["slot"] = (seq - 1) % 2
    info["detail"] = "CRC ok: stored 0x%08X == calc 0x%08X" % (crc_stored, crc_calc)
    return info


def load_sectors(path, base_offset):
    with open(path, "rb") as f:
        blob = f.read()
    if len(blob) == 0x1000000:
        base = 0xE000
        blob = blob[base : base + 0x2000]
        source = "%s (16MB dump, otadata sliced at 0xE000)" % path
    else:
        base = base_offset
        source = path
    if len(blob) < 0x2000 and len(blob) != 0x1000:
        sys.exit("error: %s: expected 0x1000 (sector), 0x2000 (otadata region) or 16MB dump, got 0x%X" % (path, len(blob)))

    offsets = [base, base + 0x1000] if len(blob) == 0x2000 else [base]
    return source, [(off, blob[(off - base) : (off - base) + 0x1000]) for off in offsets]


def main():
    ap = argparse.ArgumentParser(description="Decode and validate X4 Classic otadata dump")
    ap.add_argument("dumps", nargs="+", help="otadata dump: 8KB region (0xE000+0x2000), 4KB sector, or full 16MB flash dump")
    ap.add_argument("--base-offset", default=0xE000, help="flash address of the first byte of each dump (default 0xE000)")
    args = ap.parse_args()
    base = int(args.base_offset, 0) if isinstance(args.base_offset, str) else args.base_offset

    valid_records = []
    for path in args.dumps:
        source, sectors = load_sectors(path, base)
        print("== %s" % source)
        for off, data in sectors:
            rec = decode_sector(data, off)
            tag = "VALID" if rec["valid"] else "invalid"
            print("  sector @ 0x%05X: %-6s layout=%-26s" % (off, tag, rec["layout"]), end="")
            if rec["valid"]:
                print(
                    " seq=%d -> slot %d (%s)  state=%s"
                    % (rec["seq"], rec["slot"], "app0" if rec["slot"] == 0 else "app1", rec["state"])
                )
                if rec["label"] != "ff" * 20:
                    print("           label: %s" % rec["label"])
                print("           %s" % rec["detail"])
                valid_records.append(rec)
            else:
                print(" %s" % rec["detail"])
                if rec["layout"] != "erased" and rec["seq"] not in (None, OTA_SEQ_MAGIC_ERASED):
                    print("           WARN: sector holds a real record that FAILED validation (CRC=%s)" % rec.get("crc_ok"))
        print()

    if not valid_records:
        print("VERDICT: no valid otadata record -> bootloader falls back to factory partition (absent on X4C!) — DO NOT PROCEED")
        return 1

    active = max(valid_records, key=lambda r: r["seq"])
    slot = active["slot"]
    other = [r for r in valid_records if r["offset"] != active["offset"]]
    print(
        "ACTIVE (bootloader selects): sector @ 0x%05X, seq=%d -> %s"
        % (
            active["offset"],
            active["seq"],
            "app0 (ota_0 @ 0x10000)" if slot == 0 else "app1 (ota_1 @ 0x7F0000)",
        )
    )

    if slot == 1:
        for r in other:
            if r["seq"] < active["seq"] and r["seq"] % 2 == 1:
                print(
                    "TO BOOT APP0: erase sector @ 0x%05X (holds seq=%d); remaining seq=%d -> app0"
                    % (active["offset"], active["seq"], r["seq"])
                )
                print("expected after erase: single VALID record seq=%d -> app0" % r["seq"])
                break
        else:
            print("TO BOOT APP0: no lower odd-seq record remains — procedure must STOP (ask engineer)")
    else:
        print("app0 already selected — no OTA switch needed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
