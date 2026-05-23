#!/bin/bash
# Cura arm calibration — check arm state and optionally zero the gripper
# Run this once after physically attaching the gripper for the first time.
#
# What it does:
#   1. Reads and prints all joint positions + gripper state
#   2. Reports homing status from the arm firmware
#   3. Optionally zeroes the gripper (sets fully-closed as position 0)
#
# The "check joint 6 after attaching gripper" warning means:
#   The gripper finger motor needs a zero reference so the firmware knows
#   where "fully closed" is. Run option [z] below to set it.

set -e
cd "$(dirname "$0")"

echo "================================================"
echo "  Cura Arm Calibration / Diagnostics"
echo "================================================"
echo ""
echo "📋  Reading arm state..."
echo ""

sudo .venv/bin/python - <<'PYEOF'
import sys, time

from gs_usb.gs_usb import GsUsb
for _dev in GsUsb.scan():
    try: _dev.stop()
    except Exception: pass
time.sleep(0.3)

from piper_sdk import C_PiperInterface

p = C_PiperInterface("0", judge_flag=False, can_auto_init=False)
p.CreateCanBus("0", bustype="gs_usb", expected_bitrate=1000000, judge_flag=False)
p.ConnectPort()
time.sleep(1.5)

# ── Joint positions ──────────────────────────────────────────────────────────
j = p.GetArmJointMsgs()
s = j.joint_state
vals = [s.joint_1, s.joint_2, s.joint_3, s.joint_4, s.joint_5, s.joint_6]
print("Joint positions (0.001° units / degrees):")
for i, v in enumerate(vals, start=1):
    deg = v / 1000.0
    print(f"  J{i}: {int(v):>8}  ({deg:+.3f}°)")

# ── Gripper ──────────────────────────────────────────────────────────────────
try:
    g = p.GetArmGripperMsgs()
    gpos = g.gripper_state.pos
    geff = g.gripper_state.effort
    print(f"\nGripper position : {int(gpos):>8}  (0 = fully closed, 70000 = fully open)")
    print(f"Gripper effort   : {int(geff):>8}")
except Exception as e:
    print(f"\nGripper read failed: {e}")

# ── Arm status / homing ──────────────────────────────────────────────────────
try:
    s = p.GetArmStatus()
    hs = getattr(s, "homing_status", None)
    if hs is not None:
        zeroed = "✅ zeroed" if hs else "⚠️  NOT zeroed — gripper calibration needed"
        print(f"\nHoming status    : {zeroed}")
    else:
        print("\nHoming status    : (not available in this SDK version)")
except Exception as e:
    print(f"\nArm status read failed: {e}")

# ── CAN health ───────────────────────────────────────────────────────────────
fps = p.GetCanFps()
print(f"\nCAN FPS          : {fps:.1f}  ({'✅ healthy' if fps > 100 else '⚠️  low — check cable'})")

for _dev in GsUsb.scan():
    try: _dev.stop()
    except Exception: pass
time.sleep(0.2)

import os; os._exit(0)
PYEOF

echo ""
echo "================================================"
echo "  What do you want to do?"
echo "================================================"
echo ""
echo "  [z]  Zero the gripper  (run once after attaching gripper)"
echo "       → Closes gripper fully and sets that as position 0"
echo "       → Only needed if homing_status is NOT zeroed"
echo ""
echo "  [q]  Quit (no changes)"
echo ""
read -r -p "Choice [z/q]: " CHOICE

if [[ "$CHOICE" != "z" && "$CHOICE" != "Z" ]]; then
    echo "No changes made."
    exit 0
fi

echo ""
echo "⚠️   Gripper zeroing procedure"
echo "    The arm must be in a safe resting position (not near the patient)."
echo "    The gripper will close fully before setting zero."
echo ""
read -r -p "Arm is safe and clear — proceed? [y/N]: " CONFIRM
if [[ "$CONFIRM" != "y" && "$CONFIRM" != "Y" ]]; then
    echo "Aborted."
    exit 0
fi

echo ""
echo "🔧  Zeroing gripper..."

sudo .venv/bin/python - <<'PYEOF'
import sys, time

from gs_usb.gs_usb import GsUsb
for _dev in GsUsb.scan():
    try: _dev.stop()
    except Exception: pass
time.sleep(0.3)

from piper_sdk import C_PiperInterface

p = C_PiperInterface("0", judge_flag=False, can_auto_init=False)
p.CreateCanBus("0", bustype="gs_usb", expected_bitrate=1000000, judge_flag=False)
p.ConnectPort()
p.EnableArm(7)
time.sleep(1.5)

# Step 1: Close gripper fully (let it reach mechanical stop)
print("  Closing gripper to mechanical stop...")
p.GripperCtrl(0, 1000)
time.sleep(2.0)

# Step 2: Set current closed position as zero
# 0xAE is the piper_sdk "set zero" flag (from piper_set_gripper_zero.py demo)
try:
    p.GripperCtrl(0, 1000, 0x00, 0xAE)
    print("  ✅  Gripper zero set (4-arg API)")
except TypeError:
    # Older C_PiperInterface may not support the 3rd/4th args.
    # In that case the gripper zero must be set via C_PiperInterface_V2.
    print("  ⚠️   4-arg GripperCtrl not available in this SDK version.")
    print("       Try: sudo .venv/bin/python -c \"")
    print("         from piper_sdk import C_PiperInterface_V2")
    print("         p = C_PiperInterface_V2('0')")
    print("         p.CreateCanBus('0', bustype='gs_usb', expected_bitrate=1000000, judge_flag=False)")
    print("         p.ConnectPort()")
    print("         import time; time.sleep(1.5)")
    print("         p.GripperCtrl(0, 1000, 0x00, 0xAE)")
    print("       \"")

time.sleep(0.5)

# Verify
g = p.GetArmGripperMsgs()
print(f"  Gripper position after zeroing: {int(g.gripper_state.pos)}")
print("  (should read 0 or very close)")

for _dev in GsUsb.scan():
    try: _dev.stop()
    except Exception: pass
time.sleep(0.2)

import os; os._exit(0)
PYEOF

echo ""
echo "✅  Gripper calibration complete."
echo "    You can now run ./teach.sh to record waypoints."
