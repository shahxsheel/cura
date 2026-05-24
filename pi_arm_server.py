#!/usr/bin/env python3
"""
pi_arm_server.py — Cura arm control server (runs on Raspberry Pi)

Two TCP connections to Mac (Mac is the server on both):
  - port 9998: Pi streams live arm position to Mac (used during calibration)
  - port 9999: Pi receives move commands from Mac (used during tracking)

Usage:
    python3 pi_arm_server.py
"""
import json
import socket
import subprocess
import sys
import threading
import time

sys.path.insert(0, "piper_sdk")
from piper_sdk import *

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MAC_HOST = "192.168.1.180"
ARM_POS_PORT = 9998   # Pi streams position TO Mac
CMD_PORT = 9999       # Pi receives move commands FROM Mac
CAN_PORT = "can_piper"
ARM_SPEED = 20        # percent, 0-100
POSITION_STREAM_HZ = 10


# ---------------------------------------------------------------------------
# Arm control
# ---------------------------------------------------------------------------

# Firmware requires continuous command streaming at ~100Hz to move.
# _target holds the current target; the streamer thread sends it continuously.
_target = None
_target_lock = threading.Lock()

def _arm_streamer(piper):
    """Background thread: streams current target to arm at 100Hz (firmware requirement)."""
    while True:
        with _target_lock:
            tgt = _target
        if tgt is not None:
            x, y, z, rx, ry, rz = tgt
            piper.MotionCtrl_2(0x01, 0x00, ARM_SPEED, 0x00)
            piper.EndPoseCtrl(x, y, z, rx, ry, rz)
        time.sleep(0.01)  # 100 Hz

def move_arm(piper, cmd):
    global _target
    x = int(cmd["x"])
    y = int(cmd["y"])
    z = int(cmd["z"])
    rx = int(cmd.get("rx", 0))
    ry = int(cmd.get("ry", 85000))
    rz = int(cmd.get("rz", 0))

    if not (-600000 <= x <= 600000 and
            -600000 <= y <= 600000 and
            0 <= z <= 700000):
        print(f"SAFETY REJECT: out of range x={x} y={y} z={z}")
        return

    with _target_lock:
        _target = (x, y, z, rx, ry, rz)
    print(f"→ ARM MOVE: X={x//1000}mm Y={y//1000}mm Z={z//1000}mm")


# ---------------------------------------------------------------------------
# Position streaming thread
# ---------------------------------------------------------------------------

def stream_position(piper, mac_host):
    """Continuously connect to Mac's ARM_POS_PORT and stream live arm position."""
    while True:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            print(f"Connecting to Mac for position stream at {mac_host}:{ARM_POS_PORT}...")
            sock.connect((mac_host, ARM_POS_PORT))
            print("Position stream connected.")
            while True:
                try:
                    pose = piper.GetArmEndPoseMsgs().end_pose
                    pos = {"x": pose.X_axis, "y": pose.Y_axis, "z": pose.Z_axis}
                    sock.sendall((json.dumps(pos) + "\n").encode())
                except OSError:
                    break
                time.sleep(1.0 / POSITION_STREAM_HZ)
        except (ConnectionRefusedError, OSError) as e:
            print(f"Position stream error: {e}. Retrying in 2s...")
        finally:
            try:
                sock.close()
            except Exception:
                pass
        time.sleep(2.0)


# ---------------------------------------------------------------------------
# Command receive loop
# ---------------------------------------------------------------------------

def receive_commands(piper, mac_host):
    """Continuously connect to Mac's CMD_PORT and execute move commands."""
    while True:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            print(f"Connecting to Mac for commands at {mac_host}:{CMD_PORT}...")
            sock.connect((mac_host, CMD_PORT))
            print("Command connection established.")
            buf = ""
            while True:
                data = sock.recv(4096).decode()
                if not data:
                    break
                buf += data
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        cmd = json.loads(line)
                        move_arm(piper, cmd)
                    except json.JSONDecodeError as e:
                        print(f"JSON parse error: {e}")
        except (ConnectionRefusedError, OSError) as e:
            print(f"Command connection error: {e}. Retrying in 2s...")
        finally:
            sock.close()

        print("Mac disconnected. Waiting for reconnect...")
        time.sleep(2.0)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--calibrate", action="store_true",
                        help="Stream arm position only (used during --calibrate on Mac)")
    args = parser.parse_args()

    # Activate CAN bus
    print(f"Activating CAN bus on {CAN_PORT}...")
    subprocess.run(
        ["sudo", "bash", "piper_sdk/piper_sdk/can_activate.sh", CAN_PORT, "1000000"],
        check=False,
    )
    time.sleep(1.0)

    # Connect and enable arm
    print("Connecting to arm...")
    piper = C_PiperInterface_V2(CAN_PORT)
    piper.ConnectPort()
    time.sleep(0.1)

    print("Enabling arm...")
    while not piper.EnablePiper():
        time.sleep(0.01)
    print("Arm enabled.")

    try:
        if args.calibrate:
            # Calibration mode: only stream position to Mac, no command receiving
            print("Calibration mode: streaming arm position to Mac...")
            stream_position(piper, MAC_HOST)  # runs forever until Ctrl+C
        else:
            # Tracking mode: stream position in background + receive commands in foreground
            pos_thread = threading.Thread(
                target=stream_position, args=(piper, MAC_HOST), daemon=True
            )
            pos_thread.start()
            streamer_thread = threading.Thread(target=_arm_streamer, args=(piper,), daemon=True)
            streamer_thread.start()
            receive_commands(piper, MAC_HOST)
    except KeyboardInterrupt:
        print("\nEmergency stop!")
        piper.MotionCtrl_1(0x01, 0x00, 0x00)
        time.sleep(0.5)
        piper.MotionCtrl_1(0x02, 0x00, 0x00)
        print("Done.")


if __name__ == "__main__":
    main()
