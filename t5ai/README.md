# T5AI Firmware

TuyaOpen C firmware for the Tuya T5AI Board (ARM Cortex-M33, 3.5" TFT touchscreen, OVX4388 camera, dual mics, speaker).

## Hardware
- Board: Tuya T5AI (ARM Cortex-M33 @ 480MHz)
- Display: 3.5" 320×480 TFT touchscreen (LVGL v9)
- Camera: OVX4388 1600×1200 DVP
- Audio: Dual mics + speaker
- Connectivity: Wi-Fi 6, BLE 5.4

## Build
1. Install TuyaOpen SDK: https://github.com/tuya/TuyaOpen
2. Set up ARM toolchain
3. Build and flash:
   ```bash
   cd t5ai
   cmake -B build
   cmake --build build
   # Flash via USB
   ```

## Firmware Behavior (V1)
- Polls laptop FastAPI server at `http://<laptop-ip>:8000/status` every 500ms
- Displays current state in large text on the TFT screen
- Touch "Start" button → POST /command {"action": "start_feeding"}
- Touch "Emergency Stop" button → POST /command {"action": "emergency_stop"}
- States displayed: Ready / Detecting / Picking Up / Delivering / Enjoy! / Returning / Error

## Wi-Fi Setup
Both the laptop and T5AI must be on the same Wi-Fi network. Set the laptop IP in the firmware config before flashing.
