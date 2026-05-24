#!/usr/bin/env python3
"""
pi_arm_server.py — Minimal arm command server (runs on Raspberry Pi)

Accepts JSON XYZ commands on a TCP socket and drives the arm to each target
using the proven test.py pattern (MotionCtrl_2 + EndPoseCtrl at 50 Hz, blocking
until motion_status reports arrival). No vision-side transformation here — the
client sends arm-frame coordinates directly.

Protocol (one JSON object per line):
    {"x": 200000, "y": 0, "z": 300000}
    {"x": 200000, "y": 300000, "z": 300000, "ry": 85000}
Units: x/y/z in 0.001 mm; optional rx/ry/rz in 0.001 deg (defaults 0/85000/0).
"""
import json
import os
import socket
import subprocess
import sys
import time

# piper_sdk is bundled under ./piper_sdk/
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "piper_sdk"))

from piper_sdk import C_PiperInterface_V2

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
MAC_HOST = "192.168.1.180"   # set to your Mac's IP
CMD_PORT = 9999              # Mac listens here, Pi connects
POSE_PORT = 9998             # Mac listens here for live arm-pose stream
CAN_PORT = "can0"
ARM_SPEED = 80               # 0-100
STREAM_RATE_HZ = 50          # inner-loop cadence
MOVE_TIMEOUT_S = 3.0         # max time to wait for a move to complete
WAYPOINTS_FILE = os.environ.get("CURA_WAYPOINTS", "bottle.json")
WAYPOINT_SPEED = 50          # 0-100, used during pickup replay
WAYPOINT_TIMEOUT_S = 15.0    # per-waypoint timeout
WAYPOINT_PAUSE_S = 0.3       # pause between waypoints

# Default delivery orientation (forward-tilted gripper).
DEFAULT_RX, DEFAULT_RY, DEFAULT_RZ = 0, 85_000, 0
DEFAULT_Z = 300_000   # 300mm — used when command omits "z"


# ---------------------------------------------------------------------------
# Arm primitives — identical to test.py
# ---------------------------------------------------------------------------

def move_xyz(piper: C_PiperInterface_V2, x: int, y: int, z: int,
             rx: int = DEFAULT_RX, ry: int = DEFAULT_RY, rz: int = DEFAULT_RZ,
             speed: int = ARM_SPEED, timeout: float = MOVE_TIMEOUT_S) -> bool:
    """Blocking Cartesian move. Returns True on arrival, False on timeout."""
    print(f"  ▶ Move to X={x/1000:.1f}mm Y={y/1000:.1f}mm Z={z/1000:.1f}mm "
          f"RY={ry/1000:.1f}°")
    start = time.time()
    started = False
    period = 1.0 / STREAM_RATE_HZ
    last_motion = None
    while time.time() - start < timeout:
        piper.MotionCtrl_2(0x01, 0x00, speed, 0x00)   # CAN ctrl, MOVE_P
        piper.EndPoseCtrl(x, y, z, rx, ry, rz)
        time.sleep(period)
        try:
            motion = piper.GetArmStatus().arm_status.motion_status
        except Exception:
            continue
        if motion != last_motion:
            print(f"    motion_status={motion}")
            last_motion = motion
        if not started and motion != 0:
            started = True
        elif started and motion == 0:
            print("  ✅ Reached")
            return True
    print("  ⚠️ Timeout")
    return False


def move_joints_zero(piper: C_PiperInterface_V2, timeout: float = 10.0) -> bool:
    """Drive all joints to 0 (MOVE_J streaming, blocks until reached)."""
    print("→ Going to zero...")
    start = time.time()
    started = False
    period = 1.0 / STREAM_RATE_HZ
    while time.time() - start < timeout:
        piper.MotionCtrl_2(0x01, 0x01, ARM_SPEED, 0x00)   # MOVE_J
        piper.JointCtrl(0, 0, 0, 0, 0, 0)
        time.sleep(period)
        try:
            motion = piper.GetArmStatus().arm_status.motion_status
        except Exception:
            continue
        if not started and motion != 0:
            started = True
        elif started and motion == 0:
            print("  ✅ Zero reached")
            return True
    print("  ⚠️ Zero timeout")
    return False


# ---------------------------------------------------------------------------
# Waypoint replay (fixed pickup workflow)
# ---------------------------------------------------------------------------

def replay_waypoints(piper: C_PiperInterface_V2, path: str,
                     speed: int = WAYPOINT_SPEED,
                     timeout: float = WAYPOINT_TIMEOUT_S,
                     pause: float = WAYPOINT_PAUSE_S) -> bool:
    """Replay a recorded joint-space trajectory. Ends at the last waypoint
    which should be the 'ready-to-deliver' pose."""
    import json as _json
    try:
        with open(path) as f:
            data = _json.load(f)
    except (OSError, _json.JSONDecodeError) as e:
        print(f"⚠️  Cannot load waypoints '{path}': {e}")
        return False

    waypoints = data.get("waypoints", [])
    if not waypoints:
        print(f"⚠️  No waypoints in '{path}'.")
        return False

    print(f"▶ Replaying {len(waypoints)} waypoints from '{data.get('name','?')}' "
          f"({path}) at speed={speed}%")
    period = 1.0 / STREAM_RATE_HZ

    for i, wp in enumerate(waypoints):
        j = wp["joints"]
        label = wp.get("label", f"waypoint_{i}")
        gripper_angle = wp.get("gripper_angle", 0)
        gripper_effort = wp.get("gripper_effort", 1000)
        print(f"  [{i+1}/{len(waypoints)}] -> '{label}'")

        # Gripper command once per waypoint (not every tick).
        piper.GripperCtrl(abs(gripper_angle), gripper_effort, 0x01, 0)

        start = time.time()
        started = False
        reached = False
        while time.time() - start < timeout:
            piper.MotionCtrl_2(0x01, 0x01, speed, 0x00)   # MOVE_J
            piper.JointCtrl(j[0], j[1], j[2], j[3], j[4], j[5])
            time.sleep(period)
            try:
                motion = piper.GetArmStatus().arm_status.motion_status
            except Exception:
                continue
            if not started and motion != 0:
                started = True
            elif started and motion == 0:
                reached = True
                break
        if reached:
            print(f"     ✅ reached '{label}'")
        else:
            print(f"     ⚠️ timeout on '{label}' (continuing)")
        if pause > 0 and i < len(waypoints) - 1:
            time.sleep(pause)

    print("▶ Pickup workflow done. Handing off to Mac vision.")
    return True


# ---------------------------------------------------------------------------
# CAN + connect
# ---------------------------------------------------------------------------

def bring_up_can() -> None:
    print(f"Bringing up {CAN_PORT}...")
    subprocess.run(["sudo", "ip", "link", "set", CAN_PORT, "type", "can",
                    "bitrate", "1000000"], check=False)
    subprocess.run(["sudo", "ip", "link", "set", "up", CAN_PORT], check=False)
    subprocess.run(["sudo", "ip", "link", "set", CAN_PORT, "txqueuelen", "1000"], check=False)


def connect_arm() -> C_PiperInterface_V2:
    print(f"Connecting to arm on {CAN_PORT}...")
    piper = C_PiperInterface_V2(CAN_PORT)
    piper.ConnectPort()
    time.sleep(0.3)
    print("Enabling arm (polling EnablePiper)...")
    while not piper.EnablePiper():
        time.sleep(0.01)
    print("Arm enabled.")
    return piper


# ---------------------------------------------------------------------------
# Command receive loop
# ---------------------------------------------------------------------------

def receive_loop(piper: C_PiperInterface_V2, mac_host: str) -> None:
    """Connect to Mac:CMD_PORT and execute the *latest* JSON command only.

    While a move is blocking, the Mac keeps sending. We drain the socket
    each cycle and act on the newest command, discarding any stale ones
    that piled up in the recv buffer — so the arm always chases the live
    target, never replays the queue.
    """
    import select as _select
    while True:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            print(f"Connecting to Mac at {mac_host}:{CMD_PORT}...")
            sock.connect((mac_host, CMD_PORT))
            sock.setblocking(False)
            print("Command connection established.")
            buf = ""
            while True:
                # Block briefly for at least one byte; then drain everything
                # else that's already in the kernel buffer.
                ready, _, _ = _select.select([sock], [], [], 1.0)
                if ready:
                    while True:
                        try:
                            chunk = sock.recv(4096)
                        except BlockingIOError:
                            break
                        if not chunk:
                            raise ConnectionResetError("peer closed")
                        buf += chunk.decode()

                # Keep only the LAST complete line; discard older ones.
                if "\n" not in buf:
                    continue
                lines = buf.split("\n")
                buf = lines[-1]                    # incomplete tail
                complete = [ln.strip() for ln in lines[:-1] if ln.strip()]
                if not complete:
                    continue
                stale = len(complete) - 1
                latest = complete[-1]
                if stale > 0:
                    print(f"  (dropped {stale} stale command{'s' if stale>1 else ''})")

                try:
                    cmd = json.loads(latest)
                    move_xyz(
                        piper,
                        int(cmd["x"]), int(cmd["y"]),
                        int(cmd.get("z", DEFAULT_Z)),
                        int(cmd.get("rx", DEFAULT_RX)),
                        int(cmd.get("ry", DEFAULT_RY)),
                        int(cmd.get("rz", DEFAULT_RZ)),
                    )
                except (json.JSONDecodeError, KeyError, ValueError) as e:
                    print(f"  bad command: {e}")
        except (ConnectionRefusedError, ConnectionResetError, OSError) as e:
            print(f"Connection error: {e}. Retrying in 2s...")
        finally:
            try: sock.close()
            except Exception: pass
        print("Mac disconnected. Waiting for reconnect...")
        time.sleep(2.0)


# ---------------------------------------------------------------------------
# Calibration mode — drag-teach + stream live end-pose to Mac:9998
# ---------------------------------------------------------------------------

def calibrate_stream(piper: C_PiperInterface_V2, mac_host: str) -> None:
    """Put arm in drag-teach mode and stream end-pose JSON to Mac."""
    print("→ Entering drag-teach mode (you can now move the arm by hand)")
    piper.MotionCtrl_1(0x00, 0x00, 0x01)
    time.sleep(0.5)

    try:
        while True:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                print(f"Connecting to Mac at {mac_host}:{POSE_PORT} for pose stream...")
                sock.connect((mac_host, POSE_PORT))
                print("Pose stream connected. Move arm by hand; Ctrl-C to finish.")
                while True:
                    try:
                        ep = piper.GetArmEndPoseMsgs().end_pose
                        x = int(ep.X_axis); y = int(ep.Y_axis); z = int(ep.Z_axis)
                    except Exception:
                        time.sleep(0.05)
                        continue
                    msg = json.dumps({"x": x, "y": y, "z": z}) + "\n"
                    try:
                        sock.sendall(msg.encode())
                    except (BrokenPipeError, OSError):
                        break
                    time.sleep(0.1)   # ~10 Hz
            except (ConnectionRefusedError, OSError) as e:
                print(f"Pose stream conn err: {e}. Retrying in 2s...")
                time.sleep(2.0)
            finally:
                try: sock.close()
                except Exception: pass
            print("Pose stream dropped. Reconnecting...")
    finally:
        print("→ Exiting drag-teach mode")
        piper.MotionCtrl_1(0x00, 0x00, 0x02)
        time.sleep(0.3)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse as _ap
    parser = _ap.ArgumentParser()
    parser.add_argument("--calibrate", action="store_true",
                        help="Drag-teach mode: stream live arm pose to Mac:9998")
    args = parser.parse_args()

    bring_up_can()
    time.sleep(0.5)

    piper = connect_arm()

    if args.calibrate:
        try:
            calibrate_stream(piper, MAC_HOST)
        except KeyboardInterrupt:
            print("\nCalibration stream stopped.")
        return

    move_joints_zero(piper)

    if os.path.exists(WAYPOINTS_FILE):
        replay_waypoints(piper, WAYPOINTS_FILE)
    else:
        print(f"No {WAYPOINTS_FILE} found — skipping pickup phase.")

    try:
        receive_loop(piper, MAC_HOST)
    except KeyboardInterrupt:
        print("\nEmergency stop!")
    finally:
        try:
            piper.MotionCtrl_1(0x01, 0x00, 0x00)   # stop
            time.sleep(0.3)
            piper.MotionCtrl_1(0x02, 0x00, 0x00)   # reset
        except Exception:
            pass
        print("Done.")


if __name__ == "__main__":
    main()
