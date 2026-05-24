#!/usr/bin/env python3
"""
pi_arm_server.py — Cura arm control server (runs on Raspberry Pi)

Connects to the Mac vision client over TCP and moves the Piper arm
to coordinates received as newline-delimited JSON.

Usage:
    python3 pi_arm_server.py
"""
import json
import socket
import subprocess
import time

from piper_sdk import *

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MAC_HOST = "192.168.1.180"
MAC_PORT = 9999
CAN_PORT = "can0"
ARM_SPEED = 5   # percent, 0-100


# ---------------------------------------------------------------------------
# Arm control
# ---------------------------------------------------------------------------

def move_arm(piper, cmd):
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

    piper.MotionCtrl_2(0x01, 0x00, ARM_SPEED, 0x00)  # MOVE P mode
    piper.EndPoseCtrl(x, y, z, rx, ry, rz)
    print(f"→ ARM MOVE: X={x//1000}mm Y={y//1000}mm Z={z//1000}mm")


# ---------------------------------------------------------------------------
# TCP receive loop
# ---------------------------------------------------------------------------

def receive_loop(sock, piper):
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
                print(f"JSON parse error: {e} — line: {line!r}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    # Activate CAN bus
    print(f"Activating CAN bus on {CAN_PORT}...")
    subprocess.run(
        ["sudo", "bash", "piper_sdk/piper_sdk/can_activate.sh", CAN_PORT, "1000000"],
        check=False,
    )
    time.sleep(1.0)

    # Connect arm
    print("Connecting to arm...")
    piper = C_PiperInterface_V2(CAN_PORT)
    piper.ConnectPort()
    time.sleep(0.1)

    print("Enabling arm...")
    while not piper.EnablePiper():
        time.sleep(0.01)
    print("Arm enabled.")

    try:
        while True:
            # Connect to Mac (Mac is the TCP server)
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            while True:
                try:
                    print(f"Connecting to Mac at {MAC_HOST}:{MAC_PORT}...")
                    sock.connect((MAC_HOST, MAC_PORT))
                    print("Connected to Mac.")
                    break
                except (ConnectionRefusedError, OSError):
                    time.sleep(2.0)
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

            try:
                receive_loop(sock, piper)
            except (ConnectionResetError, OSError) as e:
                print(f"Connection error: {e}")
            finally:
                sock.close()

            print("Mac disconnected. Waiting for reconnect...")

    except KeyboardInterrupt:
        print("\nEmergency stop!")
        piper.MotionCtrl_1(0x01, 0x00, 0x00)
        time.sleep(0.5)
        piper.MotionCtrl_1(0x02, 0x00, 0x00)
        print("Done.")


if __name__ == "__main__":
    main()
