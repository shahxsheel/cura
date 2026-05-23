#!/bin/bash
# Cura run script — uses sudo on macOS for CAN USB access

set -e
cd "$(dirname "$0")"

echo "🚀  Starting Cura..."
echo "    SPACE = Start feeding / Done drinking"
echo "    ESC   = Emergency stop"
echo ""

if [[ "$(uname)" == "Darwin" ]]; then
    # macOS: needs sudo for gs_usb CAN access
    sudo .venv/bin/python -m cura.main "$@"
else
    # Linux: no sudo needed
    uv run python -m cura.main "$@"
fi
