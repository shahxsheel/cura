#!/bin/bash
# Cura teaching mode — record arm waypoints.
# Usage: ./teach.sh <waypoint_name>
# Example: ./teach.sh pre_grasp

set -e
export PATH="$HOME/.local/bin:$PATH"
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

sudo ip link set can0 type can bitrate 1000000 2>/dev/null || true
sudo ip link set up can0

uv run python - "$WAYPOINT" <<'PYEOF'
import sys, time
from pyAgxArm import create_agx_arm_config, AgxArmFactory, ArmModel, PiperFW
from cura.arm.trajectories import teach_and_save

waypoint = sys.argv[1]
cfg = create_agx_arm_config(
    robot=ArmModel.PIPER,
    firmeware_version=PiperFW.DEFAULT,
    interface="socketcan",
    channel="can0",
)
robot = AgxArmFactory.create_arm(cfg)
robot.connect()
time.sleep(1.5)
teach_and_save(robot, waypoint, "waypoints.json")
print(f"✅  Waypoint '{waypoint}' saved to waypoints.json")
PYEOF
