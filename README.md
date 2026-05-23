# Cura

> "An AI-powered robotic feeding assistant that helps hospital patients drink and eat independently, freeing nurses for critical care."

---

## Problem Statement

Nurses spend **15–30 minutes per patient per meal** on assisted feeding. US hospitals face a shortage of **100,000+ nurses**, and that gap is widening every year. Patients with Parkinson's disease, post-stroke conditions, or severe motor impairments often cannot hold a cup or utensil independently — requiring constant, one-on-one staff attention for every sip or bite.

Cura gives that time back to nurses.

---

## Solution

Cura uses a **6-DOF robotic arm (AgileX Piper)** controlled by a laptop brain to autonomously pick up a water bottle and deliver it to a patient's mouth.

- **V1** — Voice/keyboard triggered with a fixed delivery position. Operator places a water bottle in the cradle, presses SPACE, and the arm does the rest.
- **V2** — Adds face and mouth tracking via MediaPipe so the delivery point adapts to the patient's position.
- **V3** — Full meal handling: plate scanning, utensil selection, scooping, and food delivery.

---

## Hardware Requirements

| Component | Purpose |
|---|---|
| AgileX Piper 6-DOF arm | Main manipulation hardware |
| USB-CAN adapter | CAN bus bridge between laptop and arm |
| Tuya T5AI board | Patient-facing UI display (LVGL) |
| Laptop (Linux or macOS) | Runs the Python orchestrator and vision models |
| 3D-printed bottle cradle | Fixed pickup position for V1 |

---

## Software Setup

```bash
# Install uv (fast Python package manager)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Clone and install
git clone https://github.com/shahxsheel/cura.git
cd cura
uv sync

# Activate the CAN interface (Linux only — run once per boot)
sudo ip link set can0 up type can bitrate 1000000

# Teach waypoints (first run — jog arm to each position and record)
uv run python -c "
from cura.arm.controller import ArmController
from cura.arm.trajectories import teach_and_save
arm = ArmController(can_port='can0', speed=30)
arm.connect()
input('Jog arm to HOME position, then press Enter...')
teach_and_save(arm._piper, 'home', 'waypoints.json')
input('Jog arm to PRE_GRASP position, then press Enter...')
teach_and_save(arm._piper, 'pre_grasp', 'waypoints.json')
input('Jog arm to GRASP position, then press Enter...')
teach_and_save(arm._piper, 'grasp', 'waypoints.json')
input('Jog arm to LIFT position, then press Enter...')
teach_and_save(arm._piper, 'lift', 'waypoints.json')
input('Jog arm to PRE_DELIVER position, then press Enter...')
teach_and_save(arm._piper, 'pre_deliver', 'waypoints.json')
input('Jog arm to DELIVER (mouth) position, then press Enter...')
teach_and_save(arm._piper, 'deliver', 'waypoints.json')
arm.disconnect()
print('All waypoints saved to waypoints.json')
"

# Run Cura
uv run python -m cura.main
```

---

## Keyboard Controls (V1)

| Key | Action |
|---|---|
| `SPACE` | Start feeding (when IDLE) / Done drinking (when DRINKING) |
| `ESC` | Emergency stop (any state) |

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                      Laptop Brain                       │
│                                                         │
│  ┌────────────────┐      ┌──────────────────────────┐  │
│  │ cura.main      │      │ cura.interface.server    │  │
│  │ (state machine)│◄────►│ (FastAPI, WebSocket)     │  │
│  └───────┬────────┘      └───────────┬──────────────┘  │
│          │                           │                  │
│  ┌───────▼────────┐      ┌───────────▼──────────────┐  │
│  │ cura.arm       │      │ Web Dashboard             │  │
│  │ (ArmController)│      │ (Lovable, ws://)          │  │
│  └───────┬────────┘      └──────────────────────────┘  │
└──────────┼──────────────────────────────────────────────┘
           │ CAN bus (1 Mbit/s)
           │
┌──────────▼──────────────────────────────────────────────┐
│                   AgileX Piper Arm                      │
│          6-DOF, max reach 600 mm, payload 1.5 kg        │
└─────────────────────────────────────────────────────────┘
           
┌────────────────────────────────────────────────────────┐
│                   Tuya T5AI Board                      │
│         LVGL patient UI — displays state labels        │
│         POST /command → laptop FastAPI server          │
└────────────────────────────────────────────────────────┘
```

---

## Version Roadmap

| Version | Capability | Status |
|---|---|---|
| **V1** | Water bottle delivery, fixed cradle, keyboard trigger | In progress |
| **V2** | MediaPipe mouth tracking, adaptive delivery point | Planned |
| **V3** | Full meal: plate scanning, utensil selection, scooping, food delivery | Future |

---

## Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3.11, strict type hints |
| Arm SDK | piper_sdk (AgileX official) |
| Object detection | YOLOv8n (Ultralytics) |
| Face / mouth tracking | MediaPipe |
| API server | FastAPI + Uvicorn |
| T5AI firmware | TuyaOpen + LVGL |
| Package management | uv |

---

## Running Tests

```bash
# All tests (no hardware required — arm and server are mocked)
uv run python -m pytest tests/ -v

# Integration tests only
uv run python -m pytest tests/test_integration.py -v
```

---

## Team

**HackStorm 2.0 — Mountain View, CA — May 22–24, 2026**

5-person team building Cura in ~30 hours.

See [TEAM.md](TEAM.md) for the full task distribution and per-member file ownership.

---

## License

MIT — see [LICENSE](LICENSE) for details.
