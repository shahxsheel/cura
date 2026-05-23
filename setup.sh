#!/bin/bash
set -e

echo "================================================"
echo "  Cura — Robotic Feeding Assistant Setup"
echo "================================================"
echo ""

# ── 1. Check for Homebrew ────────────────────────────────────────────────────
if ! command -v brew &>/dev/null; then
    echo "❌  Homebrew not found. Install it first:"
    echo "    /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
    exit 1
fi
echo "✅  Homebrew found"

# ── 2. Install libusb (required for CAN adapter on macOS) ───────────────────
if ! brew list libusb &>/dev/null; then
    echo "📦  Installing libusb..."
    brew install libusb
else
    echo "✅  libusb already installed"
fi

# ── 3. Check for uv ─────────────────────────────────────────────────────────
if ! command -v uv &>/dev/null; then
    echo "📦  Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.cargo/bin:$PATH"
else
    echo "✅  uv $(uv --version) found"
fi

# ── 4. Install Python dependencies ──────────────────────────────────────────
echo ""
echo "📦  Installing Python dependencies (this may take a minute)..."
uv sync
echo "✅  Dependencies installed"

# ── 5. Detect hardware ──────────────────────────────────────────────────────
echo ""
echo "🔍  Checking hardware..."

# Check for CAN adapter
if system_profiler SPUSBDataType 2>/dev/null | grep -q "candleLight"; then
    echo "✅  CAN adapter (candleLight) detected"
    CAN_OK=true
else
    echo "⚠️   CAN adapter NOT detected — check USB connection to arm"
    CAN_OK=false
fi

# Check for Orbbec camera
if system_profiler SPUSBDataType 2>/dev/null | grep -q -i "orbbec\|ORBBEC"; then
    echo "✅  Orbbec depth camera detected"
    CAM_OK=true
else
    echo "⚠️   Orbbec camera NOT detected — check USB connection"
    CAM_OK=false
fi

# ── 6. Test CAN connection ───────────────────────────────────────────────────
echo ""
if [ "$CAN_OK" = true ]; then
    echo "🦾  Testing arm connection (requires sudo for USB access)..."
    sudo .venv/bin/python - <<'PYEOF'
import sys, time
try:
    from piper_sdk import C_PiperInterface
    p = C_PiperInterface("0", judge_flag=False, can_auto_init=False)
    p.CreateCanBus("0", bustype="gs_usb", expected_bitrate=1000000, judge_flag=False)
    p.ConnectPort()
    time.sleep(1.5)
    j = p.GetArmJointMsgs()
    fps = p.GetCanFps()
    if fps > 0:
        print(f"✅  Arm connected! CAN FPS: {fps:.1f}")
        print(f"   Joints: {j}")
    else:
        print("⚠️   Arm connected but no CAN data — is the arm powered on?")
except Exception as e:
    print(f"❌  Arm connection failed: {e}")
    sys.exit(1)
PYEOF
else
    echo "⏭️   Skipping arm test (adapter not detected)"
fi

# ── 7. Print next steps ──────────────────────────────────────────────────────
echo ""
echo "================================================"
echo "  Setup complete! Next steps:"
echo "================================================"
echo ""
echo "  1. Teach waypoints (first time only):"
echo "     sudo uv run python -c \""
echo "       from piper_sdk import C_PiperInterface"
echo "       from cura.arm.trajectories import teach_and_save"
echo "       p = C_PiperInterface('0', judge_flag=False, can_auto_init=False)"
echo "       p.CreateCanBus('0', bustype='gs_usb', expected_bitrate=1000000, judge_flag=False)"
echo "       p.ConnectPort()"
echo "       # Move arm to position, then:"
echo "       teach_and_save(p, 'pre_grasp', 'waypoints.json')"
echo "     \""
echo ""
echo "  2. Run Cura:"
echo "     ./run.sh"
echo ""
echo "  Keyboard controls:"
echo "     SPACE = Start feeding / Done drinking"
echo "     ESC   = Emergency stop"
echo ""
