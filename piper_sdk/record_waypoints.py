#!/usr/bin/env python3
"""Record waypoints by manually moving the Piper arm in teach mode.

Usage:
    python3 record_waypoints.py --can_port can0 --output waypoints.json
"""
import time
import json
import argparse
import sys
import threading
import select
from datetime import datetime
from piper_sdk import *

try:
    import termios
    import tty
    HAS_TERMIOS = True
except ImportError:
    import msvcrt
    HAS_TERMIOS = False


def read_current_state(piper):
    """Read current joint angles and gripper state from the arm."""
    joint_msg = piper.GetArmJointMsgs()
    gripper_msg = piper.GetArmGripperMsgs()
    joints = [
        joint_msg.joint_state.joint_1,
        joint_msg.joint_state.joint_2,
        joint_msg.joint_state.joint_3,
        joint_msg.joint_state.joint_4,
        joint_msg.joint_state.joint_5,
        joint_msg.joint_state.joint_6,
    ]
    gripper_angle = gripper_msg.gripper_state.grippers_angle
    gripper_effort = gripper_msg.gripper_state.grippers_effort
    return joints, gripper_angle, gripper_effort


def format_state(joints, gripper_angle):
    """Format current joint angles and gripper position as string."""
    return (f"Joints(deg): [{joints[0]*1e-3:.1f}, {joints[1]*1e-3:.1f}, {joints[2]*1e-3:.1f}, "
            f"{joints[3]*1e-3:.1f}, {joints[4]*1e-3:.1f}, {joints[5]*1e-3:.1f}]  "
            f"Gripper: {gripper_angle*1e-3:.1f} mm")


def display_thread_fn(piper, waypoints, stop_event, input_active):
    """Background thread that refreshes the position display every second."""
    while not stop_event.is_set():
        if not input_active.is_set():
            joints, gripper_angle, _ = read_current_state(piper)
            # Clear line and print status
            line = f"\r  Live | {format_state(joints, gripper_angle)} | saved: {len(waypoints)}"
            sys.stdout.write(f"\033[2K{line}")
            sys.stdout.flush()
        stop_event.wait(1.0)


def main():
    parser = argparse.ArgumentParser(description="Record Piper arm waypoints in teach mode")
    parser.add_argument("--can_port", type=str, default="can0", help="CAN port name")
    parser.add_argument("--output", type=str, default="waypoints.json", help="Output JSON file")
    parser.add_argument("--name", type=str, default="task", help="Task name for the recording")
    parser.add_argument("--gripper_effort", type=int, default=1000, help="Default gripper effort (0.001 N.m)")
    args = parser.parse_args()

    print(f"Connecting to arm on {args.can_port}...")
    piper = C_PiperInterface_V2(args.can_port)
    piper.ConnectPort()
    time.sleep(0.1)

    # Enter teach mode (drag teaching)
    print("Entering teach mode...")
    piper.MotionCtrl_1(0x00, 0x00, 0x01)
    time.sleep(0.5)

    waypoints = []
    stop_event = threading.Event()
    input_active = threading.Event()  # Suppress display while user is typing

    # Start background display thread
    display = threading.Thread(target=display_thread_fn,
                               args=(piper, waypoints, stop_event, input_active),
                               daemon=True)
    display.start()

    print("\n=== Waypoint Recorder ===")
    print("Position auto-refreshes every second.")
    print("Commands:")
    print("  [Enter] - Save current position as waypoint")
    print("  [d]     - Delete last waypoint")
    print("  [l]     - List all saved waypoints")
    print("  [s]     - Save to file and exit")
    print("  [q]     - Quit without saving")
    print("  [Ctrl+C]- Emergency exit (saves if possible)")
    print()

    if HAS_TERMIOS:
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
    else:
        fd = None
        old_settings = None

    try:
        if HAS_TERMIOS:
            tty.setcbreak(fd)

        while True:
            # Non-blocking key check
            if HAS_TERMIOS:
                if select.select([sys.stdin], [], [], 0.1)[0]:
                    key = sys.stdin.read(1)
                else:
                    continue
            else:
                if msvcrt.kbhit():
                    key = msvcrt.getwch()
                else:
                    time.sleep(0.1)
                    continue

            if key == '\n' or key == '\r':
                input_active.set()
                # Restore terminal for normal input
                if HAS_TERMIOS:
                    termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
                sys.stdout.write("\033[2K\r")
                joints, gripper_angle, _ = read_current_state(piper)
                label = input("  Label (or Enter to auto-name): ").strip()
                if not label:
                    label = f"waypoint_{len(waypoints)}"
                wp = {
                    "label": label,
                    "joints": joints,
                    "gripper_angle": gripper_angle,
                    "gripper_effort": args.gripper_effort,
                }
                waypoints.append(wp)
                print(f"  Saved #{len(waypoints)-1}: '{label}' | {format_state(joints, gripper_angle)}\n")
                # Re-enter cbreak mode
                if HAS_TERMIOS:
                    tty.setcbreak(fd)
                input_active.clear()

            elif key == 'd':
                sys.stdout.write("\033[2K\r")
                if waypoints:
                    removed = waypoints.pop()
                    print(f"  Deleted: '{removed['label']}'")
                else:
                    print("  No waypoints to delete.")

            elif key == 'l':
                input_active.set()
                sys.stdout.write("\033[2K\r")
                print(f"  --- {len(waypoints)} waypoints ---")
                for i, wp in enumerate(waypoints):
                    j = wp["joints"]
                    print(f"  [{i}] {wp['label']:<20} {format_state(j, wp['gripper_angle'])}")
                print()
                input_active.clear()

            elif key == 's':
                stop_event.set()
                sys.stdout.write("\033[2K\r")
                if not waypoints:
                    print("  No waypoints to save.")
                    stop_event.clear()
                    continue
                save_waypoints(args.output, args.name, waypoints)
                print(f"  Saved {len(waypoints)} waypoints to {args.output}")
                break

            elif key == 'q':
                stop_event.set()
                sys.stdout.write("\033[2K\r")
                print("  Quitting without saving.")
                break

    except KeyboardInterrupt:
        stop_event.set()
        print("\n\nInterrupted!")
        if waypoints:
            save_waypoints(args.output, args.name, waypoints)
            print(f"  Auto-saved {len(waypoints)} waypoints to {args.output}")

    finally:
        stop_event.set()
        if HAS_TERMIOS and old_settings:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        # Exit teach mode
        print("Exiting teach mode...")
        piper.MotionCtrl_1(0x00, 0x00, 0x02)
        time.sleep(0.3)


def save_waypoints(filepath, name, waypoints):
    data = {
        "name": name,
        "created": datetime.now().isoformat(),
        "waypoints": waypoints,
    }
    with open(filepath, "w") as f:
        json.dump(data, f, indent=2)


if __name__ == "__main__":
    main()
