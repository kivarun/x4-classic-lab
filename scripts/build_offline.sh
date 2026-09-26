#!/usr/bin/env bash
# Offline (no-download) build of the x4c diagnostic firmware.
#
# pioarduino 55.03.37 carries a penv_setup.py dependency check that resolves
# the pinned `platformio` requirement by distribution NAME. The fork ships the
# same PlatformIO 6.1.19 code under the dist name `pioarduino-core`, so an
# unpatched build attempts a network install on every run and cannot complete
# offline. This script creates an overlay copy of the installed platform in a
# writable directory (one matcher fix inside), points PLATFORMIO_PLATFORMS_DIR
# at it, and hard-disables network access for the build process itself: any
# accidental download attempt fails immediately and loudly instead of
# silently succeeding.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SHADOW="${X4C_SHADOW_PLATFORMS_DIR:-/tmp/pio-platforms}"
CORE_DIR="${PLATFORMIO_CORE_DIR:-$HOME/.platformio}"
SRC_PLATFORM="$CORE_DIR/platforms/espressif32"

if [ ! -d "$SRC_PLATFORM" ]; then
    echo "error: platform not found: $SRC_PLATFORM" >&2
    exit 1
fi

mkdir -p "$SHADOW"

python3 - "$SRC_PLATFORM" "$SHADOW/espressif32" <<'PYEOF'
import pathlib
import sys

src_root, dst_root = map(pathlib.Path, sys.argv[1:3])
dst_root.mkdir(parents=True, exist_ok=True)

for entry in src_root.iterdir():
    if entry.name == 'builder':
        continue
    target = dst_root / entry.name
    if not (target.exists() or target.is_symlink()):
        target.symlink_to(entry)

builder_dst = dst_root / 'builder'
builder_dst.mkdir(exist_ok=True)
for entry in (src_root / 'builder').iterdir():
    if entry.name in ('penv_setup.py', '__pycache__'):
        continue
    target = builder_dst / entry.name
    if not (target.exists() or target.is_symlink()):
        target.symlink_to(entry)

patched = builder_dst / 'penv_setup.py'
if not patched.exists():
    original = (src_root / 'builder' / 'penv_setup.py').read_text()
    old = (
        '        if name not in installed_packages:\n'
        '            yield package\n'
        '        elif name == "platformio":\n'
    )
    new = (
        '        if name == "platformio" and name not in installed_packages:\n'
        '            name = "pioarduino-core"\n'
        '        if package.lower() not in installed_packages and name not in installed_packages:\n'
        '            yield package\n'
        '        elif package.lower() == "platformio":\n'
    )
    assert original.count(old) == 1, 'upstream anchor not found; check pioarduino version'
    patched.write_text(original.replace(old, new))
    print('penv_setup.py patched into overlay (platformio -> pioarduino-core dist name)')
print('overlay ready:', dst_root)
PYEOF

export PLATFORMIO_PLATFORMS_DIR="$SHADOW"
export PLATFORMIO_SETTING_ENABLE_TELEMETRY=No
export UV_OFFLINE=1
DEADPROXY=http://127.0.0.1:9
export HTTP_PROXY=$DEADPROXY HTTPS_PROXY=$DEADPROXY ALL_PROXY=$DEADPROXY
export http_proxy=$DEADPROXY https_proxy=$DEADPROXY all_proxy=$DEADPROXY

exec pio run -e x4c "$@"
