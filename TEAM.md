# Cura — Team Task Distribution

## Hackathon: HackStorm 2.0 | May 22-24, 2026 | Mountain View, CA
## Time Budget: ~30 hours

---

## V1 Definition of Done

The system can:

1. Operator places water bottle in the fixed cradle on the table
2. Operator presses SPACE → arm picks up bottle and delivers to fixed mouth position
3. Patient drinks through straw; operator presses SPACE again → arm returns bottle to cradle
4. ESC = emergency stop at any time

**V1 does not require:** camera-based detection, face tracking, T5AI flashing, or the web dashboard. Those are V2/V3.

---

## V3 Vision (final product)

The full system can:

1. Scan a meal tray using the wrist camera (YOLO detects food items and utensils)
2. Select the correct utensil (fork, spoon) for the current food item
3. Scoop or pick up food, track the patient's mouth with MediaPipe, deliver food
4. Wipe the utensil, return it to the tray, and repeat for the next item
5. T5AI board shows real-time status and progress; web dashboard visible to nurse

---

## Member A — ML / Vision Lead

**Files owned:** `src/cura/vision/` (all files), `tests/test_vision.py`

**Do NOT touch:** `src/cura/arm/`, `src/cura/interface/`, `src/cura/main.py`

### V1 Tasks
- Set up camera capture in `src/cura/vision/camera.py` (test standalone; not wired into V1 main loop)
- Integrate YOLOv8n in `src/cura/vision/detector.py` — test that it can detect a water bottle from the wrist camera
- Scaffold coordinate transform in `detector.py`: pixel → robot-frame (placeholder math is fine for V1)
- Write `tests/test_vision.py` — mock camera, test detector output schema

### V2 Tasks
- Implement `src/cura/vision/mouth_tracker.py` with MediaPipe FaceMesh
- Wire mouth coordinates into orchestrator via a shared `threading.Event` + queue (coordinate with Member C)
- Update coordinate transform with real calibration data from Member B

### V3 Tasks
- Add multi-class food detection (fork, spoon, bowl, specific foods)
- Add plate scanning logic: sweep camera over tray, aggregate detections
- Feed item list to orchestrator for meal sequencing

### Dependencies
- Needs Member B's camera mount position / arm base coordinates for pixel→robot transform calibration
- Needs Member C to expose a hook in `main.py` for injecting vision-supplied coordinates (V2)

---

## Member B — Arm Control Lead

**Files owned:** `src/cura/arm/` (all files), `tests/test_arm.py`, `waypoints.json` (you create this via teaching mode)

**Do NOT touch:** `src/cura/vision/`, `src/cura/interface/`, `src/cura/main.py`

### V1 Tasks
- Bring up CAN connection: `sudo ip link set can0 up type can bitrate 1000000`
- Use teaching mode (`cura.arm.trajectories.teach_and_save`) to record all six waypoints: `home`, `pre_grasp`, `grasp`, `lift`, `pre_deliver`, `deliver`
- Tune `_POSITION_TOLERANCE` and `_REACH_TIMEOUT` in `controller.py` for reliable waypoint arrival detection
- Validate `safety.py` joint limits match the physical arm's safe workspace
- Run gripper calibration: confirm `gripper_open_position` and `gripper_close_position` in `settings.py` grip the bottle firmly without crushing it
- End-to-end trajectory test: `uv run python -c "from cura.arm.controller import ArmController; ..."`  run full PICKUP then RETURN sequences standalone before handing off to Member C

### CAN activation (run once per boot)
```bash
sudo ip link set can0 up type can bitrate 1000000
```

### Teaching mode snippet
```python
from cura.arm.controller import ArmController
from cura.arm.trajectories import teach_and_save

arm = ArmController(can_port="can0", speed=30)
arm.connect()
input("Jog arm to HOME, press Enter...")
teach_and_save(arm._piper, "home", "waypoints.json")
# repeat for each waypoint...
arm.disconnect()
```

### V2/V3 Tasks
- Add Cartesian-mode control for vision-guided positioning (requires piper_sdk Cartesian API)
- Expose force-feedback from gripper for safe food grasping
- Add collision detection hooks in `safety.py`

### Dependencies
- Provide `waypoints.json` to the repo before Member C does integration testing
- Provide camera-mount-to-base transform for Member A's coordinate calibration

---

## Member C — Vibe Coder 1 (Integration + Orchestrator)

**Files owned:** `src/cura/main.py`, `src/cura/config/settings.py`, `tests/test_integration.py`

**Do NOT touch:** `src/cura/vision/` internals, `src/cura/arm/` internals, `t5ai/`

### V1 Tasks
- The state machine in `src/cura/main.py` is already written — your job is integration testing and hardening
- Run `uv run python -m pytest tests/test_integration.py -v` — all tests should pass without hardware
- Once Member B provides `waypoints.json`, do a live dry-run: check arm moves through the full PICKUP → RETURN sequence
- Handle edge cases: arm disconnects mid-sequence, keyboard interrupt during motion, rapid double-SPACE
- Tune `settings.py` environment variable overrides as needed (`CURA_ARM_SPEED`, `CURA_WAYPOINTS_FILE`, etc.)
- Verify the FastAPI server starts cleanly and `/status` returns valid JSON

### Running the integration tests
```bash
uv run python -m pytest tests/test_integration.py -v
```

### Running the full system
```bash
# With real arm (after waypoints are taught)
uv run python -m cura.main

# Environment overrides
CURA_ARM_SPEED=30 CURA_WAYPOINTS_FILE=waypoints.json uv run python -m cura.main
```

### V2/V3 Tasks
- Add a vision-coordinate injection path: accept `(x, y, z)` from Member A's mouth tracker and pass to arm as dynamic target
- Add meal-session tracking using `MealProgress` model from `cura.interface.models`

### Dependencies
- Needs `waypoints.json` from Member B for live testing
- Needs `CuraServer` fully implemented from Member D for server startup

---

## Member D — Vibe Coder 2 (T5AI + Interface)

**Files owned:** `src/cura/interface/` (all files), `t5ai/` (TuyaOpen firmware)

**Do NOT touch:** `src/cura/vision/`, `src/cura/arm/`, `src/cura/main.py`

### V1 Tasks
- Implement `src/cura/interface/server.py` — the orchestrator imports `CuraServer` from here
  - Required API:
    ```python
    server = CuraServer()
    server.run(host: str, port: int)   # starts FastAPI in background thread
    server.update_state(state: SystemState)  # broadcast over WebSocket + update /status
    server.get_next_command() -> PatientCommand | None  # drain command queue
    server.stop()
    ```
  - `GET /status` → `SystemStatus` JSON
  - `POST /command` → accepts `PatientCommand`, enqueues it
  - `GET /ws` → WebSocket, broadcasts `SystemStatus` on every state change
- Test that `/status` returns valid JSON: `curl http://localhost:8000/status`
- Flash T5AI with LVGL UI from `t5ai/` that shows the `state_label` field and has a big "DONE DRINKING" button that POSTs to `/command`

### FastAPI quick test
```bash
uv run python -c "
from cura.interface.server import CuraServer
from cura.interface.models import SystemState
import time
s = CuraServer()
s.run('0.0.0.0', 8000)
s.update_state(SystemState.IDLE)
time.sleep(30)
"
# In another terminal:
curl http://localhost:8000/status
```

### V2/V3 Tasks
- Add WebSocket push for real-time dashboard updates
- Add T5AI button for patient-initiated commands (V2: "I want water", V3: "more food")
- Add LVGL animations for each system state

### Dependencies
- `SystemState`, `SystemStatus`, `PatientCommand` models are already in `src/cura/interface/models.py` — use them as-is
- Coordinate WebSocket message format with Member E (web dashboard consumer)

---

## Member E — Vibe Coder 3 (Frontend + Demo + 3D Print)

**Files owned:** Web dashboard (Lovable, separate repo), `prints/`, pitch deck

**Do NOT touch:** All `src/cura/` code — consume the API only.

### V1 Tasks
- Build Lovable dashboard that connects to `ws://[laptop-ip]:8000/ws`
  - Displays current state label (e.g. "Ready", "Moving to Bottle", "Enjoy!")
  - Shows a status timeline / log
  - Big red EMERGENCY STOP button → `POST /command` with `{"action": "emergency_stop", "source": "dashboard"}`
- 3D print the **bottle cradle** — must hold a standard 500 mL water bottle upright and be mountable on the hospital tray arm
- 3D print the **T5AI stand** — mounts the T5AI board at patient eye level
- Build the **pitch deck** (5 slides):
  1. Problem (nurse shortage + assisted feeding burden)
  2. Solution (Cura demo GIF)
  3. V1 → V2 → V3 roadmap
  4. Tech stack + architecture diagram
  5. Team + ask

### WebSocket message format (from Member D)
```json
{
  "state": "DRINKING",
  "state_label": "Enjoy!",
  "message": "",
  "is_moving": false,
  "estop_active": false,
  "timestamp": 1716500000.0
}
```

### POST /command format
```json
{
  "action": "emergency_stop",
  "source": "dashboard"
}
```

### V2/V3 Tasks
- Add live camera feed panel to dashboard (MJPEG stream)
- Add meal progress bar (bites given, drinks given)
- Add patient preference panel (food preferences, portion size)

### Dependencies
- Needs Member D's server IP and confirmed `/ws` WebSocket URL before wiring dashboard
- Needs Member B's cradle dimensions for 3D print tolerances

---

## Cross-Member Communication

| Topic | Owner | Consumer |
|---|---|---|
| `waypoints.json` | Member B | Member C (integration), Member A (calibration) |
| Camera-to-base transform | Member B | Member A |
| `CuraServer` API | Member D | Member C (imports it) |
| WebSocket message format | Member D | Member E |
| `/command` endpoint | Member D | Member E (dashboard), T5AI firmware |
| Mouth-tracking coordinates | Member A | Member C (V2) |

---

## Git Workflow

- Branch from `master`; name your branch `member-[A-E]/[feature]`
- Never push directly to `master`
- Open a PR and tag the relevant member for review before merging
- Each member's "Do NOT touch" directories are enforced by honour system — coordinate via PR if you genuinely need a cross-boundary change
