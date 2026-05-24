# T5AI Firmware

TuyaOpen C firmware for the Tuya T5AI Board (ARM Cortex-M33, 3.5" TFT touchscreen, OVX4388 camera, dual mics, speaker).

## Hardware

| Component | Spec |
|---|---|
| SoC | ARM Cortex-M33 @ 480 MHz |
| Display | 3.5" 320×480 TFT touchscreen (LVGL v9) |
| Camera | OVX4388 1600×1200 DVP |
| Audio | Dual mics + speaker |
| Connectivity | Wi-Fi 6, BLE 5.4 |

---

## Source Files

| File | Purpose |
|---|---|
| `src/cura_config.h` | **Edit this before flashing** — Wi-Fi credentials and laptop IP |
| `src/main.c` | App entry: Wi-Fi connect, LVGL init, poll task launch |
| `src/cura_http.c/h` | HTTP GET /status, HTTP POST /command |
| `src/cura_ui.c/h` | LVGL v9 screen layout (header, state label, START / STOP buttons) |
| `CMakeLists.txt` | TuyaOpen project build config |

---

## Screen Layout

```
┌────────────────────────┐
│         CURA           │  ← blue header
├────────────────────────┤
│                        │
│        Ready           │  ← large state label (updates every 500 ms)
│                        │
├────────────────────────┤
│   ▶  START             │  ← green — POSTs {"action":"start_feeding"}
├────────────────────────┤
│   ■  EMERGENCY STOP    │  ← red   — POSTs {"action":"emergency_stop"}
├────────────────────────┤
│   Polling server...    │  ← grey status bar
└────────────────────────┘
```

State labels displayed (sourced from the laptop server's `state_label` field):

| Server state | Label shown on board |
|---|---|
| IDLE | Ready |
| APPROACHING | Moving to Bottle |
| GRASPING | Picking Up |
| LIFTING | Lifting |
| DELIVERING | Delivering |
| DRINKING | Enjoy! |
| RETRACTING | Returning Bottle |
| ERROR | Error — Please Help |

---

## Step 1 — Edit config before flashing

Open `src/cura_config.h` and set:

```c
#define CURA_WIFI_SSID      "YourNetworkName"
#define CURA_WIFI_PASSWORD  "YourNetworkPassword"
#define CURA_SERVER_HOST    "192.168.x.x"   // laptop IP — see step below
```

**Find your laptop's IP:**
```bash
# macOS
ipconfig getifaddr en0       # Wi-Fi interface

# Linux
hostname -I | awk '{print $1}'
```

Both laptop and T5AI board must be on the **same Wi-Fi network**.

---

## Step 2 — Build environment setup (one-time)

### Install ARM toolchain

```bash
# macOS
brew install arm-none-eabi-gcc

# Ubuntu/Debian
sudo apt install gcc-arm-none-eabi
```

### Clone TuyaOpen SDK

```bash
git clone https://github.com/tuya/TuyaOpen /opt/TuyaOpen
cd /opt/TuyaOpen
git submodule update --init --recursive
export TUYA_SDK_PATH=/opt/TuyaOpen
```

Add the export to `~/.zshrc` or `~/.bashrc` so it persists across sessions.

---

## Step 3 — Build

```bash
cd t5ai
cmake -B build -DTUYA_SDK_PATH=$TUYA_SDK_PATH
cmake --build build -j$(nproc)
```

The output binary is `build/cura_t5ai.bin`.

---

## Step 4 — Flash

Connect the T5AI board via USB, then:

```bash
# Install Tuya flash tool (Python)
pip install tuya-iot-tool

# Flash (hold BOOT button on board while running this)
tuya-flash --port /dev/tty.usbserial-* --firmware build/cura_t5ai.bin
```

**macOS serial port** is typically `/dev/tty.usbserial-XXXX`.  
**Linux** is typically `/dev/ttyUSB0`.

---

## Step 5 — Verify

1. Board reboots, connects to Wi-Fi (LED solid blue on T5AI when connected)
2. Screen shows `Connecting...` briefly then the current state label from the laptop
3. Start the laptop server first:
   ```bash
   # In the cura repo
   ./run.sh
   ```
4. Board should show **"Ready"** once it polls `/status` successfully

---

## Firmware Behaviour (V1)

- Polls `GET http://<laptop-ip>:8000/status` every **500 ms**
- Extracts `state_label` from JSON and renders it on screen
- **START** button → `POST /command {"action":"start_feeding","source":"t5ai"}`
- **EMERGENCY STOP** button → `POST /command {"action":"emergency_stop","source":"t5ai"}`
- If server is unreachable: displays **"No Server"** on screen

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Screen shows "No Server" | Check laptop IP in `cura_config.h`; confirm both on same network; confirm `./run.sh` is running |
| Board never connects to Wi-Fi | Double-check SSID/password in `cura_config.h`; SSID is case-sensitive |
| Flash fails | Hold BOOT button on board before running `tuya-flash`; try a different USB cable |
| START button has no effect | Check laptop terminal for `POST /command` log lines; if missing, network issue |

---

## V2 / V3 Extensions

- **V2:** Add a "Done Drinking" button that POSTs `{"action":"done_drinking"}` — replaces the operator pressing SPACE
- **V3:** Add meal progress display (read from `GET /meal`), patient preference panel, LVGL animations per state
