#!/bin/bash
# Cura teaching mode — record arm waypoints
# Usage: ./teach.sh <waypoint_name>
# Example: ./teach.sh pre_grasp

set -e
cd "$(dirname "$0")"

WAYPOINT="${1:-}"
if [ -z "$WAYPOINT" ]; then
    echo "Usage: ./teach.sh <waypoint_name>"
    echo ""
    echo "Waypoints to record (in order):"
    echo "  pre_grasp   — above bottle, gripper open"
    echo "  grasp       — at bottle height, ready to close"
    echo "  lift        — bottle lifted clear of table"
    echo "  pre_deliver — approaching patient mouth area"
    echo "  deliver     — at mouth, bottle tilted for straw"
    echo "  home        — safe rest position (record last)"
    exit 1
fi

echo "📍  Recording waypoint: $WAYPOINT"
echo "    Move the arm to the desired position, then press ENTER"
read -r

sudo .venv/bin/python - "$WAYPOINT" <<'PYEOF'
import sys, os, time
import usb.core

# Reset candleLight USB device to clear stale state from any previous run
dev = usb.core.find(idVendor=0x1D50, idProduct=0x606F)
if dev:
    dev.reset()
    time.sleep(0.5)

from piper_sdk import C_PiperInterface
from cura.arm.trajectories import teach_and_save

waypoint = sys.argv[1]
p = C_PiperInterface("0", judge_flag=False, can_auto_init=False)
p.CreateCanBus("0", bustype="gs_usb", expected_bitrate=1000000, judge_flag=False)
p.ConnectPort()
time.sleep(1.5)
teach_and_save(p, waypoint, "waypoints.json")
print(f"✅  Waypoint '{waypoint}' saved to waypoints.json")
os._exit(0)  # skip GC teardown that causes segfault with gs_usb threads
PYEOF
