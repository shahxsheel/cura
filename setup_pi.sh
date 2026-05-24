#!/bin/bash
set -e

echo "================================================"
echo "  Cura — Raspberry Pi 5 Setup"
echo "================================================"
echo ""

# ── 1. System packages ───────────────────────────────────────────────────────
echo "📦  Installing system dependencies..."
sudo apt-get update -qq
sudo apt-get install -y -qq \
    libusb-1.0-0-dev \
    python3-dev \
    python3-pip \
    curl
echo "✅  System packages ready"

# ── 2. uv ────────────────────────────────────────────────────────────────────
if ! command -v uv &>/dev/null; then
    echo "📦  Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
else
    echo "✅  uv $(uv --version) found"
fi

# ── 3. Python dependencies ───────────────────────────────────────────────────
echo ""
echo "📦  Installing Python dependencies..."
uv sync
echo "✅  Dependencies installed"

# mediapipe has no official ARM64 wheel — try PiWheels community build
echo ""
echo "📦  Trying mediapipe for ARM64 (community build via PiWheels)..."
if uv pip install mediapipe --extra-index-url https://www.piwheels.org/simple 2>/dev/null; then
    echo "✅  mediapipe installed"
else
    echo "⚠️   mediapipe not available for ARM64 — mouth tracking will be disabled"
fi

# ── 4. Orbbec udev rules ─────────────────────────────────────────────────────
echo ""
echo "🔧  Installing Orbbec USB device rules..."
RULES_FILE="/etc/udev/rules.d/99-orbbec.rules"
if [ ! -f "$RULES_FILE" ]; then
    sudo bash -c "cat > $RULES_FILE" <<'RULES'
# Orbbec depth cameras (VID 0x2BC5)
SUBSYSTEM=="usb", ATTR{idVendor}=="2bc5", MODE="0666", GROUP="plugdev"
RULES
    sudo udevadm control --reload-rules
    sudo udevadm trigger
    echo "✅  udev rules installed — camera accessible without sudo"
else
    echo "✅  udev rules already present"
fi

# Add current user to plugdev group if not already a member
if ! groups "$USER" | grep -q plugdev; then
    sudo usermod -aG plugdev "$USER"
    echo "⚠️   Added $USER to plugdev group — log out and back in for it to take effect"
fi

# ── 5. Detect hardware ───────────────────────────────────────────────────────
echo ""
echo "🔍  Checking hardware..."

if lsusb | grep -qi "2bc5"; then
    echo "✅  Orbbec camera detected ($(lsusb | grep -i 2bc5))"
    CAM_OK=true
else
    echo "⚠️   Orbbec camera NOT detected — check USB connection"
    CAM_OK=false
fi

if lsusb | grep -qi "1d50:606f\|candleLight"; then
    echo "✅  CAN adapter detected"
    CAN_OK=true
else
    echo "⚠️   CAN adapter NOT detected — check USB connection to arm"
    CAN_OK=false
fi

# ── 6. Test camera ───────────────────────────────────────────────────────────
echo ""
if [ "$CAM_OK" = true ]; then
    echo "📷  Testing Orbbec camera..."
    uv run python scripts/test_camera.py
else
    echo "⏭️   Skipping camera test (not detected)"
fi

# ── 7. Next steps ────────────────────────────────────────────────────────────
echo ""
echo "================================================"
echo "  Setup complete! Next steps:"
echo "================================================"
echo ""
echo "  1. Test camera manually:"
echo "     uv run python scripts/test_camera.py"
echo ""
echo "  2. Run Cura:"
echo "     ./run.sh"
echo ""
