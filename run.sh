#!/bin/bash
# Cura run script — Raspberry Pi / Linux only.

set -e
export PATH="$HOME/.local/bin:$PATH"
cd "$(dirname "$0")"

echo "🚀  Starting Cura..."
echo "    SPACE = Start feeding / Done drinking"
echo "    ESC   = Emergency stop"
echo ""

sudo ip link set can0 type can bitrate 1000000 2>/dev/null || true
sudo ip link set up can0 2>/dev/null || true

uv run python -m cura.main "$@"
