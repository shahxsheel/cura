#!/bin/bash
# Cura arm calibration — check arm state and optionally zero the gripper.
# Linux / Raspberry Pi only.
#
# What it does:
#   1. Reads and prints all joint positions + gripper state
#   2. Reports homing status from the arm firmware
#   3. Optionally zeroes the gripper (sets fully-closed as position 0)

set -e
export PATH="$HOME/.local/bin:$PATH"
cd "$(dirname "$0")"

echo "================================================"
echo "  Cura Arm Calibration / Diagnostics"
echo "================================================"
echo ""
echo "📋  Reading arm state..."
echo ""

sudo ip link set can0 type can bitrate 1000000 2>/dev/null || true
sudo ip link set up can0

uv run python - <<'PYEOF'
import math
import time
from pyAgxArm import create_agx_arm_config, AgxArmFactory, ArmModel, PiperFW

cfg = create_agx_arm_config(
    robot=ArmModel.PIPER,
    firmeware_version=PiperFW.DEFAULT,
    interface="socketcan",
    channel="can0",
)
robot = AgxArmFactory.create_arm(cfg)
gripper = None
try:
    gripper = robot.init_effector(robot.OPTIONS.EFFECTOR.AGX_GRIPPER)
except Exception as e:
    print(f"⚠️   Could not init gripper effector: {e}")
robot.connect()
time.sleep(1.5)

# Joint positions (pyAgxArm returns radians).
ja = robot.get_joint_angles()
if ja is None:
    print("⚠️   No joint feedback yet — is the arm powered on?")
else:
    rads = ja.msg
    print("Joint positions (0.001° units / degrees):")
    for i, r in enumerate(rads, start=1):
        deg = math.degrees(r)
        units = deg * 1000.0  # 0.001-deg units
        print(f"  J{i}: {int(units):>8}  ({deg:+.3f}°)")

# Gripper (pyAgxArm reports width in metres, force in newtons).
if gripper is not None:
    try:
        gs = gripper.get_gripper_status()
        if gs is None:
            print("\n⚠️   No gripper feedback received")
        else:
            width_m = float(gs.msg.value)
            print(f"\nGripper position : {int(width_m * 1e6):>8}  (0 = closed, 70000 = open, units = 0.001 mm)")
            print(f"Gripper force    : {gs.msg.force:>8.3f} N")
    except Exception as e:
        print(f"\nGripper read failed: {e}")

# CAN health (pyAgxArm exposes the receive frequency in Hz via get_fps()).
fps = robot.get_fps()
print(f"\nCAN FPS          : {fps:.1f}  ({'✅ healthy' if fps > 100 else '⚠️  low — check cable'})")
PYEOF

echo ""
echo "================================================"
echo "  What do you want to do?"
echo "================================================"
echo ""
echo "  [z]  Zero the gripper  (run once after attaching gripper)"
echo "  [q]  Quit (no changes)"
echo ""
read -r -p "Choice [z/q]: " CHOICE

if [[ "$CHOICE" != "z" && "$CHOICE" != "Z" ]]; then
    echo "No changes made."
    exit 0
fi

echo ""
echo "⚠️   Gripper zeroing: arm must be in a safe resting position."
read -r -p "Arm is safe and clear — proceed? [y/N]: " CONFIRM
if [[ "$CONFIRM" != "y" && "$CONFIRM" != "Y" ]]; then
    echo "Aborted."
    exit 0
fi

echo ""
echo "🔧  Zeroing gripper..."

uv run python - <<'PYEOF'
import time
from pyAgxArm import create_agx_arm_config, AgxArmFactory, ArmModel, PiperFW

cfg = create_agx_arm_config(
    robot=ArmModel.PIPER,
    firmeware_version=PiperFW.DEFAULT,
    interface="socketcan",
    channel="can0",
)
robot = AgxArmFactory.create_arm(cfg)
gripper = robot.init_effector(robot.OPTIONS.EFFECTOR.AGX_GRIPPER)
robot.connect()
time.sleep(0.5)

# Make sure motors are enabled before driving the gripper.
deadline = time.monotonic() + 5.0
while time.monotonic() < deadline and not robot.enable():
    time.sleep(0.05)

print("  Closing gripper to mechanical stop...")
# Drive to 0 m with the maximum supported force (3 N) so the gripper presses
# against its hard stop.
gripper.move_gripper_m(value=0.0, force=3.0)
time.sleep(2.0)

# Calibrate: set the current position as the gripper zero.
ok = gripper.calibrate_gripper()
print(f"  {'✅' if ok else '⚠️ '}  Gripper zero set" + ("" if ok else " (no ack received)"))
time.sleep(0.5)

gs = gripper.get_gripper_status()
if gs is None:
    print("  ⚠️   No gripper feedback after zeroing")
else:
    width_units = int(float(gs.msg.value) * 1e6)
    print(f"  Gripper position after zeroing: {width_units}  (should read 0)")
PYEOF

echo ""
echo "✅  Gripper calibration complete."
echo "    You can now run ./teach.sh to record waypoints."
