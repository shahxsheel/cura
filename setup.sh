#!/bin/bash
# Cura setup — Raspberry Pi / Linux only.
# Installs uv, syncs Python deps, brings up the SocketCAN interface, and
# does a smoke-test read from the arm if the CAN adapter is present.

set -e
export PATH="$HOME/.local/bin:$PATH"
cd "$(dirname "$0")"

echo "================================================"
echo "  Cura — Setup (Raspberry Pi / Linux)"
echo "================================================"
echo ""

# ── uv ───────────────────────────────────────────────────────────────────────
if ! command -v uv &>/dev/null; then
    echo "📦  Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    source "$HOME/.local/bin/env" 2>/dev/null || export PATH="$HOME/.local/bin:$PATH"
else
    echo "✅  uv $(uv --version) found"
fi

# ── Python deps ──────────────────────────────────────────────────────────────
echo "📦  Installing Python dependencies..."
uv sync
echo "✅  Dependencies installed"

# ── SocketCAN ────────────────────────────────────────────────────────────────
echo ""
if lsusb 2>/dev/null | grep -qi "1d50:606f"; then
    echo "✅  CAN adapter (candleLight) detected"
    echo "🔧  Bringing up can0 @ 1 Mbit/s..."
    sudo ip link set can0 type can bitrate 1000000 2>/dev/null || true
    sudo ip link set up can0
    echo "✅  can0 is up"

    echo ""
    echo "🦾  Testing arm connection..."
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
robot.connect()
time.sleep(1.5)
fps = robot.get_fps()
print(f"✅  Arm CAN FPS: {fps:.1f}" if fps > 0 else "⚠️   No CAN data — is arm powered on?")
PYEOF
else
    echo "⚠️   CAN adapter not detected — check USB cable"
fi

echo ""
echo "================================================"
echo "  Done! Next steps:"
echo "================================================"
echo "  Calibrate : ./calibrate.sh"
echo "  Teach     : ./teach.sh <waypoint_name>"
echo "  Run       : ./run.sh"
echo ""
echo "  Waypoints to teach (in order):"
echo "    pre_grasp, grasp, lift, pre_deliver, deliver, home"
echo ""
