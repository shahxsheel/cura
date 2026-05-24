#!/usr/bin/env python3
"""
pi_arm_server.py — Cura arm control server (runs on Raspberry Pi)

Uses pyAgxArm SDK (move_p for Cartesian control, units: meters + radians).

Two TCP connections to Mac (Mac is the server on both):
  - port 9998: Pi streams live arm position to Mac (used during calibration)
  - port 9999: Pi receives move commands from Mac (used during tracking)

Usage:
    python3 pi_arm_server.py
    python3 pi_arm_server.py --calibrate   # position stream only
"""
import json
import socket
import subprocess
import sys
import threading
import time

from pyAgxArm import create_agx_arm_config, AgxArmFactory, ArmModel, PiperFW

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MAC_HOST = "192.168.1.180"
ARM_POS_PORT = 9998   # Pi streams position TO Mac
CMD_PORT = 9999       # Pi receives move commands FROM Mac
CAN_PORT = "can_piper"
ARM_SPEED = 20        # percent, 0-100
POSITION_STREAM_HZ = 10

# Delivery orientation: keep whatever the arm's current orientation is.
# Set on first move_p call by reading get_flange_pose().
_delivery_orientation = None  # [roll, pitch, yaw] in radians


# ---------------------------------------------------------------------------
# Arm control
# ---------------------------------------------------------------------------

def move_arm(robot, cmd):
    """Parse incoming JSON command (units: 0.001 mm) and call move_p once."""
    global _delivery_orientation
    x_m = int(cmd["x"]) / 1e6   # 0.001mm -> m
    y_m = int(cmd["y"]) / 1e6
    z_m = int(cmd["z"]) / 1e6

    if not (-0.6 <= x_m <= 0.6 and -0.6 <= y_m <= 0.6 and 0 <= z_m <= 0.7):
        print(f"SAFETY REJECT: out of range x={x_m:.3f} y={y_m:.3f} z={z_m:.3f}")
        return

    # Lock in orientation from the arm's current pose on first command
    if _delivery_orientation is None:
        fp = robot.get_flange_pose()
        if fp is not None:
            _delivery_orientation = fp.msg[3:6]  # [roll, pitch, yaw]
            print(f"Locked delivery orientation: roll={_delivery_orientation[0]:.3f} pitch={_delivery_orientation[1]:.3f} yaw={_delivery_orientation[2]:.3f}")
        else:
            _delivery_orientation = [0.0, 1.484, 0.0]  # fallback: ~85 deg pitch

    roll, pitch, yaw = _delivery_orientation
    try:
        robot.move_p([x_m, y_m, z_m, roll, pitch, yaw])
        print(f"→ ARM MOVE: X={x_m*1000:.1f}mm Y={y_m*1000:.1f}mm Z={z_m*1000:.1f}mm")
    except Exception as e:
        print(f"move_p error: {type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# Position streaming thread
# ---------------------------------------------------------------------------

def stream_position(robot, mac_host):
    """Continuously connect to Mac's ARM_POS_PORT and stream live arm position."""
    while True:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            print(f"Connecting to Mac for position stream at {mac_host}:{ARM_POS_PORT}...")
            sock.connect((mac_host, ARM_POS_PORT))
            print("Position stream connected.")
            while True:
                try:
                    fp = robot.get_flange_pose()
                    if fp is not None:
                        x_m, y_m, z_m = fp.msg[0], fp.msg[1], fp.msg[2]
                        # Send in 0.001mm units to match mac_vision.py expectations
                        pos = {"x": int(x_m * 1e6), "y": int(y_m * 1e6), "z": int(z_m * 1e6)}
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

def receive_commands(robot, mac_host):
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
                        move_arm(robot, cmd)
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

    # Connect with pyAgxArm
    print("Connecting to arm via pyAgxArm...")
    cfg = create_agx_arm_config(
        robot=ArmModel.PIPER,
        firmeware_version=PiperFW.DEFAULT,
        interface="socketcan",
        channel=CAN_PORT,
    )
    robot = AgxArmFactory.create_arm(cfg)
    robot.connect()
    time.sleep(1.0)

    print("Enabling arm...")
    while not robot.enable():
        time.sleep(0.01)
    print("Arm enabled.")

    robot.set_speed_percent(ARM_SPEED)

    try:
        if args.calibrate:
            print("Calibration mode: streaming arm position to Mac...")
            stream_position(robot, MAC_HOST)
        else:
            pos_thread = threading.Thread(
                target=stream_position, args=(robot, MAC_HOST), daemon=True
            )
            pos_thread.start()
            receive_commands(robot, MAC_HOST)
    except KeyboardInterrupt:
        print("\nEmergency stop!")
        robot.electronic_emergency_stop()
        time.sleep(0.5)
        print("Done.")


if __name__ == "__main__":
    main()
