#!/usr/bin/env python3
# X4 Classic — otadata (0xE000, 0x2000) decoder for the controlled OTA switch.
#
# Parses the two 4KB otadata sectors, validates each record against both
# known layouts, reports which OTA slot (app0/app1) the bootloader will
# select, and names the exact sector to erase to force app0.
#
# Layouts (esp_ota_select_entry_t):
#   new (IDF >= 5.3):  ota_seq @0, seq_label[20] @4, ota_state @24, crc @28 (32 bytes)
#                      CRC32 of the ota_seq field only (esp_rom_crc32_le, zlib.crc32-compatible)
#   legacy (older IDF): ota_seq @0, ota_hash[32] @4, crc @36 (40 bytes)
#                      CRC32 of the first 36 bytes (seq + hash)
# Slot mapping: slot = (seq - 1) % 2 -> 0 = app0 (ota_0), 1 = app1 (ota_1).
# Selection: the valid record with the HIGHEST seq wins.
#
# Never writes anything: read-only analysis of dump files.

import argparse
import sys
import zlib

OTA_SEQ_MAGIC_ERASED = 0xFFFFFFFF
NEW_LEN = 32
LEGACY_LEN = 40
SECTOR = 0x1000

IMG_STATES = {
    0x0: "INVALID(0x0)",
    0x1: "PENDING_VERIFY",
    0x2: "VALID",
    0x3: "INVALID",
    0x4: "ABORTED",
    0xFFFFFFFF: "UNDEFINED",
}


def all_ff(data):
    return data == b"\xff" * len(data)


def crc32(data):
    return zlib.crc32(data) & 0xFFFFFFFF


def decode_sector(data, abs_offset):
    if all_ff(data[:SECTOR]):
        return {
            "offset": abs_offset,
            "layout": "erased",
            "valid": False,
            "seq": None,
            "detail": "sector fully erased (0xFF)",
        }

    seq = int.from_bytes(data[0:4], "little")
    if seq == OTA_SEQ_MAGIC_ERASED:
        return {
            "offset": abs_offset,
            "layout": "erased",
            "valid": False,
            "seq": seq,
            "detail": "ota_seq = 0xFFFFFFFF (erased record)",
        }

    info = {"offset": abs_offset, "seq": seq, "slot": (seq - 1) % 2}

    if len(data) >= NEW_LEN and all_ff(data[NEW_LEN:]):
        crc_stored = int.from_bytes(data[28:32], "little")
        crc_calc = crc32(data[0:4])
        if crc_stored == crc_calc:
            label = data[4:24]
            state = int.from_bytes(data[24:28], "little")
            info.update(
                layout="new (IDF>=5.3, 32B)",
                valid=True,
                state="0x%08X (%s)" % (state, IMG_STATES.get(state, "UNKNOWN")),
                label=label,
                detail="CRC over ota_seq only: stored 0x%08X == calc 0x%08X" % (crc_stored, crc_calc),
            )
            return info

    if len(data) >= LEGACY_LEN and all_ff(data[LEGACY_LEN:]):
        crc_stored = int.from_bytes(data[36:40], "little")
        crc_calc = crc32(data[0:36])
        if crc_stored == crc_calc:
            info.update(
                layout="legacy (40B)",
                valid=True,
                state="n/a (legacy layout has no ota_state)",
                label=None,
                detail="CRC over seq+hash (36 bytes): stored 0x%08X == calc 0x%08X" % (crc_stored, crc_calc),
            )
            return info

    return {
        "offset": abs_offset,
        "layout": "unknown",
        "valid": False,
        "seq": seq,
        "detail": "no layout matched: seq=0x%08X crc@28=0x%08X crc@36=0x%08X" % (
            seq,
            int.from_bytes(data[28:32], "little"),
            int.from_bytes(data[36:40], "little"),
        ),
    }


def load_sectors(path):
    with open(path, "rb") as f:
        blob = f.read()
    if len(blob) == 0x1000000:
        base = 0xE000
        blob = blob[base : base + 0x2000]
        source = "%s (16MB dump, otadata sliced at 0xE000)" % path
    else:
        base = args.base_offset
        source = path
    if len(blob) < 0x2000 and len(blob) != 0x1000:
        sys.exit("error: %s: expected 0x1000 (sector), 0x2000 (otadata region) or 16MB dump, got 0x%X" % (path, len(blob)))

    offsets = [base, base + 0x1000] if len(blob) == 0x2000 else [base]
    return source, [(off, blob[(off - base) : (off - base) + 0x1000]) for off in offsets]


def main():
    global args
    ap = argparse.ArgumentParser(description="Decode and validate X4 Classic otadata dump")
    ap.add_argument("dumps", nargs="+", help="otadata dump: 8KB region (0xE000+0x2000), 4KB sector, or full 16MB flash dump")
    ap.add_argument("--base-offset", default=0xE000, help="flash address of the first byte of each dump (default 0xE000)")
    args = ap.parse_args()
    base = int(args.base_offset, 0) if isinstance(args.base_offset, str) else args.base_offset
    args.base_offset = base

    valid_records = []
    for path in args.dumps:
        source, sectors = load_sectors(path)
        print("== %s" % source)
        for off, data in sectors:
            rec = decode_sector(data, off)
            tag = "VALID" if rec["valid"] else "invalid"
            print("  sector @ 0x%05X: %-6s layout=%-17s" % (off, tag, rec["layout"]), end="")
            if rec["valid"]:
                print(" seq=%d -> slot %d (%s)  state=%s" % (rec["seq"], rec["slot"], "app0" if rec["slot"] == 0 else "app1", rec["state"]))
                valid_records.append(rec)
            else:
                print(" %s" % rec["detail"])
        print()

    if not valid_records:
        print("VERDICT: no valid otadata record -> bootloader falls back to factory partition (absent on X4C!) — DO NOT PROCEED")
        return 1

    active = max(valid_records, key=lambda r: r["seq"])
    slot = active["slot"]
    other = [r for r in valid_records if r["offset"] != active["offset"]]
    print("ACTIVE (bootloader selects): sector @ 0x%05X, seq=%d -> %s" % (
        active["offset"],
        active["seq"],
        "app0 (ota_0 @ 0x10000)" if slot == 0 else "app1 (ota_1 @ 0x7F0000)",
    ))

    if slot == 1:
        for r in other:
            if r["seq"] < active["seq"] and r["seq"] % 2 == 1:
                print("TO BOOT APP0: erase sector @ 0x%05X (holds seq=%d); remaining seq=%d -> app0" % (active["offset"], active["seq"], r["seq"]))
                print("expected after erase: single VALID record seq=%d -> app0" % r["seq"])
                break
        else:
            print("TO BOOT APP0: no lower odd-seq record remains — procedure must STOP (ask engineer)")
    else:
        print("app0 already selected — no OTA switch needed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
