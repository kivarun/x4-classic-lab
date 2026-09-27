#!/usr/bin/env python3
"""X4 Classic (X4C) M1: reliable USB capture of the diag firmware boot log.

Host-side tool. pyserial only (already a dependency of esptool in the lab
venv; no new dependencies). See FLASHING.md Фаза 3 (M1) for the procedure.

Why this exists (M1 analysis):
  - `esptool run` leaves download mode; the response races the chip reset and
    the USB-Serial-JTAG re-enumeration, so esptool may exit with transport
    errors (`Invalid head of packet ...`). Such errors DO NOT prove that the
    firmware failed to boot - the captured log is the evidence, not esptool's
    exit code.
  - The reset (esptool) and the capture (monitor) never hold /dev/ttyACM*
    at the same time: phases are strictly sequential.

Phases:
  1. reset (skipped with --no-reset): run esptool `chip-id` with
     `--after hard-reset` - a harmless ROM command, then an RTS-pulse reset
     that boots the app normally (IO0 stays high, no download mode).
  2. capture: open the port with DTR=0/RTS=0 (applied before open), read with
     wall-clock + monotonic timestamps, auto-reconnect across USB
     re-enumeration until the total deadline.

Usage:
  python3 scripts/capture_usb.py --port /dev/ttyACM0 --seconds 120
  python3 scripts/capture_usb.py --no-reset --seconds 60   # attach only

No other monitor may hold the port while this script runs (single-instance
flock guard is included for this script only).
"""

import argparse
import fcntl
import subprocess
import sys
import time
from datetime import datetime, timezone

import serial  # pyserial (esptool dependency)

LOCK_FILE = "/tmp/x4c-usb-capture.lock"
RECONNECT_DELAY_S = 0.25
READ_TIMEOUT_S = 0.1


def stamp(t0_monotonic):
    """Wall clock + monotonic delta, e.g. `[12:03:04.123 T+8.456]`."""
    wall = datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3]
    return f"[{wall} T+{time.monotonic() - t0_monotonic:8.3f}] "


def write_line(out, line, t0, echo=True):
    """Timestamped, line-buffered write to the log file and stdout."""
    text = stamp(t0) + line
    out.write(text + "\n")
    out.flush()
    if echo:
        print(text, flush=True)


def reset_phase(args, out, t0):
    """Phase 1: reset via esptool as the EXCLUSIVE port owner, then release."""
    write_line(out, f"=== phase 1: esptool reset ({args.esptool} chip-id --after hard-reset) ===", t0)
    cmd = [
        args.esptool, "--chip", "esp32s3", "--port", args.port,
        "--after", "hard-reset", "chip-id",
    ]
    write_line(out, "cmd: " + " ".join(cmd), t0)
    try:
        proc = subprocess.run(cmd, text=True, timeout=90, capture_output=True)
    except subprocess.TimeoutExpired:
        write_line(out, "phase 1: esptool timed out (90 s) - continuing to capture", t0)
        return
    for line in (proc.stdout + proc.stderr).splitlines():
        write_line(out, "esptool| " + line, t0)
    # Informational only: transport errors (e.g. "Invalid head of packet")
    # race the chip reset and are NOT evidence of a firmware failure.
    write_line(out, f"phase 1: esptool exit code {proc.returncode} (informational, not a verdict)", t0)
    time.sleep(0.5)  # let esptool fully release the port before we open it


def capture_phase(args, out, t0):
    """Phase 2: exclusive capture with timestamps and re-enum-proof reconnect."""
    write_line(out, f"=== phase 2: capture for {args.seconds} s ===", t0)
    deadline = time.monotonic() + args.seconds
    reconnects = 0
    total_bytes = 0
    pending = bytearray()

    def handle_chunk(chunk):
        nonlocal total_bytes, pending
        total_bytes += len(chunk)
        pending.extend(chunk)
        while b"\n" in pending:
            line, _, rest = pending.partition(b"\n")
            pending.clear()
            pending.extend(rest)
            write_line(out, "dev| " + line.decode("latin-1").rstrip("\r"), t0)

    port = serial.Serial()
    port.port = args.port
    port.baudrate = 115200
    port.timeout = READ_TIMEOUT_S
    port.dtr = False  # safe state applied before open()
    port.rts = False
    opened_once = False

    while time.monotonic() < deadline:
        if not port.is_open:
            try:
                port.open()
                opened_once = True
                if reconnects or opened_once:
                    write_line(out, f"[reconnect #{reconnects}] port opened (DTR=0 RTS=0)", t0)
            except (serial.SerialException, OSError) as e:
                if opened_once:
                    write_line(out, f"[reconnect #{reconnects + 1}] port lost: {e}", t0)
                    reconnects += 1
                    opened_once = False
                port.port = args.port  # device may re-appear with the same node
                time.sleep(RECONNECT_DELAY_S)
                continue
        try:
            chunk = port.read(4096)
            if chunk:
                handle_chunk(chunk)
        except (serial.SerialException, OSError) as e:
            write_line(out, f"[reconnect #{reconnects + 1}] read failed: {e}", t0)
            reconnects += 1
            try:
                port.close()
            except Exception:
                pass
            time.sleep(RECONNECT_DELAY_S)

    if port.is_open:
        port.close()
    write_line(out, f"=== capture end: {total_bytes} bytes, {reconnects} re-enumerations ===", t0)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", default="/dev/ttyACM0", help="USB serial port of the device")
    parser.add_argument("--seconds", type=float, default=120, help="total capture window (s)")
    parser.add_argument("--out", default=None, help="log file (default /tmp/x4c-usb-capture-<ts>.log)")
    parser.add_argument("--esptool", default="esptool", help="esptool executable for the reset phase")
    parser.add_argument("--no-reset", action="store_true", help="skip phase 1; attach to an already-running device")
    args = parser.parse_args()

    lock = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        sys.exit("another capture_usb.py instance is running (port discipline)")

    default_out = f"/tmp/x4c-usb-capture-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.log"
    out_path = args.out or default_out
    t0 = time.monotonic()
    with open(out_path, "a", encoding="utf-8") as out:
        write_line(out, f"=== X4C M1 USB capture: port={args.port} out={out_path} ===", t0)
        if not args.no_reset:
            reset_phase(args, out, t0)
        capture_phase(args, out, t0)
    print(f"log: {out_path}")


if __name__ == "__main__":
    main()
