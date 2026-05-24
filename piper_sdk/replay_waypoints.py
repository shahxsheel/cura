#!/usr/bin/env python3
"""Replay recorded waypoints on the Piper arm.

Usage:
    python3 replay_waypoints.py --can_port can0 --file waypoints.json --speed 50
"""
import time
import json
import argparse
import subprocess
import sys
from piper_sdk import *


def raise_can_txqueue(can_port: str) -> None:
    """Bump SocketCAN TX queue depth so high-rate sends don't hit ENOBUFS.

    Default qlen on candleLight is 10, which overflows in milliseconds when
    sending 5 frames/tick at 200 Hz. 1000 is comfortable headroom.
    """
    try:
        subprocess.run(
            ["sudo", "ip", "link", "set", can_port, "txqueuelen", "1000"],
            check=False,
        )
    except Exception as e:
        print(f"  (txqueuelen bump skipped: {e})")


def wait_until_reached(piper, timeout=10.0):
    """Wait until the arm reports it has reached the target position."""
    start = time.time()
    while time.time() - start < timeout:
        status = piper.GetArmStatus()
        if status.arm_status.motion_status == 0:
            return True
        time.sleep(0.05)
    print("  Warning: timeout waiting for arm to reach target")
    return False


def main():
    parser = argparse.ArgumentParser(description="Replay Piper arm waypoints")
    parser.add_argument("--can_port", type=str, default="can0", help="CAN port name")
    parser.add_argument("--file", type=str, required=True, help="Waypoints JSON file")
    parser.add_argument("--speed", type=int, default=50, help="Speed rate 0-100 (default: 50)")
    parser.add_argument("--loop", type=int, default=1, help="Number of replay loops (default: 1)")
    parser.add_argument("--pause", type=float, default=0.5, help="Pause between waypoints in seconds (default: 0.5)")
    args = parser.parse_args()

    # Load waypoints
    with open(args.file, "r") as f:
        data = json.load(f)
    waypoints = data["waypoints"]
    print(f"Loaded {len(waypoints)} waypoints from '{data.get('name', 'unknown')}' ({args.file})")

    if not waypoints:
        print("No waypoints to replay.")
        return

    # Connect and enable arm
    print(f"Connecting to arm on {args.can_port}...")
    raise_can_txqueue(args.can_port)
    piper = C_PiperInterface_V2(args.can_port)
    piper.ConnectPort()
    time.sleep(0.1)

    print("Enabling arm...")
    while not piper.EnablePiper():
        time.sleep(0.01)
    print("Arm enabled.")

    # Set to CAN control + MoveJ mode
    speed = max(0, min(100, args.speed))
    print(f"Setting MoveJ mode, speed={speed}%")

    try:
        for loop_i in range(args.loop):
            if args.loop > 1:
                print(f"\n--- Loop {loop_i + 1}/{args.loop} ---")

            for i, wp in enumerate(waypoints):
                j = wp["joints"]
                gripper_angle = wp.get("gripper_angle", 0)
                gripper_effort = wp.get("gripper_effort", 1000)
                label = wp.get("label", f"waypoint_{i}")

                print(f"[{i}/{len(waypoints)-1}] Moving to '{label}' "
                      f"joints(deg): [{j[0]*1e-3:.1f}, {j[1]*1e-3:.1f}, {j[2]*1e-3:.1f}, "
                      f"{j[3]*1e-3:.1f}, {j[4]*1e-3:.1f}, {j[5]*1e-3:.1f}]  "
                      f"gripper: {gripper_angle*1e-3:.1f} mm")

                # Send motion command continuously until reached. Keep the
                # cadence at ~50 Hz: 200 Hz with 5 frames/tick saturates the
                # SocketCAN TX queue and trips SEND_MESSAGE_FAILED (ENOBUFS).
                # Send the gripper command once per waypoint, not every tick.
                piper.GripperCtrl(abs(gripper_angle), gripper_effort, 0x01, 0)

                reached = False
                start = time.time()
                timeout = 15.0
                started_moving = False  # arm must report "moving" before "arrived"
                while not reached and (time.time() - start < timeout):
                    piper.MotionCtrl_2(0x01, 0x01, speed, 0x00)
                    piper.JointCtrl(j[0], j[1], j[2], j[3], j[4], j[5])
                    time.sleep(0.02)

                    motion = piper.GetArmStatus().arm_status.motion_status
                    if not started_moving and motion != 0:
                        started_moving = True
                    elif started_moving and motion == 0:
                        reached = True

                if reached:
                    print(f"  Reached '{label}'")
                else:
                    print(f"  Warning: timeout reaching '{label}'")

                if args.pause > 0 and i < len(waypoints) - 1:
                    time.sleep(args.pause)

        print("\nReplay complete.")

    except KeyboardInterrupt:
        print("\n\nInterrupted! Stopping arm...")
    finally:
        # Send stop to be safe
        piper.MotionCtrl_1(0x01, 0x00, 0x00)
        time.sleep(0.5)
        piper.MotionCtrl_1(0x02, 0x00, 0x00)
        print("Done.")


if __name__ == "__main__":
    main()
