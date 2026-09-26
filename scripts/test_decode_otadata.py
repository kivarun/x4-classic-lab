#!/usr/bin/env python3
# Tests for scripts/decode_otadata.py.
#
# Control CRC values below are INDEPENDENT of the code under test:
# they were produced by a from-scratch C implementation of the ESP32 ROM
# crc32_le semantics (reflected, poly 0xEDB88320, ^~0 at entry and exit)
# compiled with gcc, and cross-checked against zlib.crc32(buf, 0xFFFFFFFF).
# The test itself re-derives them with a third, bitwise (no-table)
# implementation and asserts three-way agreement before checking the decoder.
#
# Provenance of the formula under test:
#   ESP-IDF bootloader_common_ota_select_crc() =
#       esp_rom_crc32_le(0xFFFFFFFF, (uint8_t *)&s->ota_seq, 4)
#   (disassembly of libbootloader_support.a, IDF 5.5.0+sha.87912cd291:
#    args (0xFFFFFFFF, s, 4) passed to esp_rom_crc32_le).
#   Validity (bootloader_common_ota_select_valid/invalid, same object):
#       invalid iff ota_seq == 0xFFFFFFFF or ota_state in {0x3, 0x4};
#       valid iff not invalid and s->crc == bootloader_common_ota_select_crc(s).
#
# Run: python3 scripts/test_decode_otadata.py  (stdlib only)

import os
import struct
import subprocess
import sys
import tempfile
import unittest

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "decode_otadata.py")
sys.path.insert(0, os.path.dirname(SCRIPT))
import decode_otadata as dec  # noqa: E402

# Independent control values (see header).
REF_CRC_SEQ1 = 0x4743989A
REF_CRC_SEQ2 = 0x55F63774

# Bitwise, no-table implementation of ROM crc32_le (independent of dec).
def rom_crc32_le(init, buf):
    crc = init ^ 0xFFFFFFFF
    for b in buf:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xEDB88320 if crc & 1 else crc >> 1
    return (crc ^ 0xFFFFFFFF) & 0xFFFFFFFF


def sector(seq=None, state=None, crc=None, label=b"", fill=b"\xff"):
    """Build a 4KB otadata sector. seq/state/crc as ints, label <= 20 bytes."""
    data = bytearray(b"\xff" * dec.SECTOR)
    if seq is not None:
        data[0:4] = struct.pack("<I", seq)
        data[4:24] = (label + b"\xff" * 20)[:20]
        if state is not None:
            data[24:28] = struct.pack("<I", state)
        data[28:32] = struct.pack("<I", dec.crc32_seq(data[0:4]) if crc is None else crc)
    return bytes(data)


class TestIndependentReferences(unittest.TestCase):
    def test_bitwise_impl_matches_known_check_value(self):
        # CRC-32/ISO-HDLC of "123456789" with init param 0 -> 0xCBF43926
        self.assertEqual(rom_crc32_le(0, b"123456789"), 0xCBF43926)

    def test_bitwise_impl_matches_hardcoded_references(self):
        self.assertEqual(rom_crc32_le(0xFFFFFFFF, struct.pack("<I", 1)), REF_CRC_SEQ1)
        self.assertEqual(rom_crc32_le(0xFFFFFFFF, struct.pack("<I", 2)), REF_CRC_SEQ2)

    def test_decoder_crc_matches_independent_references(self):
        self.assertEqual(dec.crc32_seq(struct.pack("<I", 1)), REF_CRC_SEQ1)
        self.assertEqual(dec.crc32_seq(struct.pack("<I", 2)), REF_CRC_SEQ2)


class TestDecodeSector(unittest.TestCase):
    def test_valid_seq1_app0(self):
        rec = dec.decode_sector(sector(seq=1, state=0x2), 0xE000)
        self.assertTrue(rec["valid"])
        self.assertEqual(rec["slot"], 0)
        self.assertEqual(rec["state"], "0x00000002 (VALID)")

    def test_valid_seq2_app1(self):
        rec = dec.decode_sector(sector(seq=2, state=0x2), 0xF000)
        self.assertTrue(rec["valid"])
        self.assertEqual(rec["slot"], 1)

    def test_corrupted_crc_invalid(self):
        rec = dec.decode_sector(sector(seq=2, state=0x2, crc=REF_CRC_SEQ2 ^ 1), 0xF000)
        self.assertFalse(rec["valid"])
        self.assertIn("CRC mismatch", rec["detail"])

    def test_state_invalid_not_valid(self):
        rec = dec.decode_sector(sector(seq=2, state=0x3), 0xF000)
        self.assertFalse(rec["valid"])

    def test_state_aborted_not_valid(self):
        rec = dec.decode_sector(sector(seq=2, state=0x4), 0xF000)
        self.assertFalse(rec["valid"])

    def test_state_pending_verify_is_bootable(self):
        rec = dec.decode_sector(sector(seq=1, state=0x1), 0xE000)
        self.assertTrue(rec["valid"])

    def test_state_new_0x0_is_bootable(self):
        rec = dec.decode_sector(sector(seq=1, state=0x0), 0xE000)
        self.assertTrue(rec["valid"])

    def test_state_undefined_is_bootable(self):
        rec = dec.decode_sector(sector(seq=1, state=0xFFFFFFFF), 0xE000)
        self.assertTrue(rec["valid"])

    def test_erased_sector(self):
        rec = dec.decode_sector(sector(), 0xF000)
        self.assertFalse(rec["valid"])
        self.assertEqual(rec["layout"], "erased")

    def test_erased_seq_magic(self):
        rec = dec.decode_sector(sector(seq=0xFFFFFFFF, state=0x2, crc=0xFFFFFFFF), 0xF000)
        self.assertFalse(rec["valid"])
        self.assertEqual(rec["layout"], "erased")

    def test_data_beyond_32b_rejected(self):
        s = bytearray(sector(seq=1, state=0x2))
        s[40] = 0x42
        rec = dec.decode_sector(bytes(s), 0xE000)
        self.assertFalse(rec["valid"])
        self.assertEqual(rec["layout"], "esp_ota_select_entry_t (32B)")

    def test_slot_mapping(self):
        self.assertEqual(dec.decode_sector(sector(seq=1, state=0x2), 0xE000)["slot"], 0)
        self.assertEqual(dec.decode_sector(sector(seq=2, state=0x2), 0xE000)["slot"], 1)
        self.assertEqual(dec.decode_sector(sector(seq=3, state=0x2), 0xE000)["slot"], 0)


class TestCliScenarios(unittest.TestCase):
    def run_cli(self, files):
        p = subprocess.run(
            [sys.executable, SCRIPT] + files,
            capture_output=True,
            text=True,
            timeout=60,
        )
        return p.returncode, p.stdout + p.stderr

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def dump(self, name, s0, s1=None):
        path = os.path.join(self.tmp.name, name)
        with open(path, "wb") as f:
            f.write(s0)
            if s1 is not None:
                f.write(s1)
        return path

    def test_factory_pair_switch(self):
        p = self.dump("pair.bin", sector(seq=1, state=0x2), sector(seq=2, state=0x2))
        code, out = self.run_cli([p])
        self.assertEqual(code, 0)
        self.assertIn("seq=2 -> slot 1 (app1)", out)
        self.assertIn("ACTIVE (bootloader selects): sector @ 0x0F000, seq=2 -> app1", out)
        self.assertIn("TO BOOT APP0: erase sector @ 0x0F000 (holds seq=2); remaining seq=1 -> app0", out)
        self.assertIn("expected after erase: single VALID record seq=1 -> app0", out)

    def test_corrupted_crc_no_erase_proposal(self):
        p = self.dump(
            "corrupt.bin",
            sector(seq=1, state=0x2),
            sector(seq=2, state=0x2, crc=REF_CRC_SEQ2 ^ 0xFFFFFFFF),
        )
        code, out = self.run_cli([p])
        self.assertEqual(code, 0)
        self.assertIn("seq=1 -> slot 0 (app0)", out)
        self.assertIn("CRC mismatch", out)
        self.assertIn("app0 already selected", out)
        self.assertNotIn("TO BOOT APP0: erase", out)

    def test_state_invalid_seq2_no_erase_proposal(self):
        for state in (0x3, 0x4):
            p = self.dump("state%X.bin" % state, sector(seq=1, state=0x2), sector(seq=2, state=state))
            code, out = self.run_cli([p])
            self.assertEqual(code, 0)
            self.assertIn("ota_state 0x%08X (%s)" % (state, "INVALID" if state == 0x3 else "ABORTED"), out)
            self.assertIn("app0 already selected", out)
            self.assertNotIn("TO BOOT APP0: erase", out)

    def test_state_invalid_both_records_stops(self):
        p = self.dump("bad.bin", sector(seq=1, state=0x3), sector(seq=2, state=0x4))
        code, out = self.run_cli([p])
        self.assertEqual(code, 1)
        self.assertIn("DO NOT PROCEED", out)

    def test_post_erase_scenario(self):
        p = self.dump("posterase.bin", sector(seq=1, state=0x2), sector())
        code, out = self.run_cli([p])
        self.assertEqual(code, 0)
        self.assertIn("sector @ 0x0F000: invalid layout=erased", out)
        self.assertIn("ACTIVE (bootloader selects): sector @ 0x0E000, seq=1 -> app0", out)
        self.assertIn("app0 already selected", out)
        self.assertNotIn("TO BOOT APP0: erase", out)

    def test_post_erase_seq2_only_stops(self):
        # seq=1 corrupted -> after erasing seq=2 the device would have no
        # valid record; decoder must not propose the erase.
        p = self.dump(
            "danger.bin",
            sector(seq=1, state=0x2, crc=REF_CRC_SEQ1 ^ 1),
            sector(seq=2, state=0x2),
        )
        code, out = self.run_cli([p])
        self.assertEqual(code, 0)
        self.assertIn("TO BOOT APP0: no lower odd-seq record remains", out)

    def test_full_16mb_dump_slicing(self):
        blob = bytearray(b"\xff" * 0x1000000)
        blob[0xE000:0xE000 + 0x1000] = sector(seq=1, state=0x2)
        blob[0xF000:0xF000 + 0x1000] = sector(seq=2, state=0x2)
        p = os.path.join(self.tmp.name, "full.bin")
        with open(p, "wb") as f:
            f.write(blob)
        code, out = self.run_cli([p])
        self.assertEqual(code, 0)
        self.assertIn("otadata sliced at 0xE000", out)
        self.assertIn("TO BOOT APP0: erase sector @ 0x0F000", out)

    def test_custom_base_offset(self):
        p = self.dump("at0.bin", sector(seq=1, state=0x2), sector(seq=2, state=0x2))
        _, out = self.run_cli([p, "--base-offset", "0x100000"])
        self.assertIn("ACTIVE (bootloader selects): sector @ 0x101000", out)

    def test_verify_after_erase_from_8kb_region(self):
        # The exact expected post-erase state of the 8KB otadata region.
        p = self.dump("after.bin", sector(seq=1, state=0x2), sector())
        code, out = self.run_cli([p])
        self.assertEqual(code, 0)
        self.assertIn("seq=1 -> slot 0 (app0)", out)
        self.assertIn("app0 already selected", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
