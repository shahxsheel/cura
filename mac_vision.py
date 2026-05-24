#!/usr/bin/env python3
"""
mac_vision.py — Cura vision client (runs on Mac)

Usage:
    python3 mac_vision.py --calibrate       # 4-corner homography calibration
    python3 mac_vision.py --calibrate-z     # Z depth calibration from face size
    python3 mac_vision.py --track-hand      # hand tracking prototype (landmark 9)
    python3 mac_vision.py --track-mouth     # mouth tracking for real demo
"""
import argparse
import json
import os
import socket
import sys

import cv2
import mediapipe as mp
import numpy as np

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
PI_HOST = "192.168.1.180"   # Mac IP — Mac is the TCP server
PI_PORT = 9999
BOX_X1, BOX_Y1 = 160, 60
BOX_X2, BOX_Y2 = 480, 420
FALLBACK_Z = 350000          # 350mm in 0.001mm units
KNOWN_FACE_WIDTH_MM = 150    # average adult face width at cheekbones
SEND_THRESHOLD = 2000        # minimum change in 0.001mm before re-sending
ARM_RY_DELIVERY = 85000      # 85 degree tilt for delivery (0.001 degree units)

CALIBRATION_FILE = "calibration.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def draw_box(frame):
    """Draw calibration box, midline, and zone labels onto frame."""
    cv2.rectangle(frame, (BOX_X1, BOX_Y1), (BOX_X2, BOX_Y2), (0, 255, 0), 2)
    mid_y = (BOX_Y1 + BOX_Y2) // 2
    cv2.line(frame, (BOX_X1, mid_y), (BOX_X2, mid_y), (0, 255, 255), 1)
    cv2.putText(frame, "FACE", (BOX_X1 + 4, mid_y - 8),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 1)
    cv2.putText(frame, "FOOD", (BOX_X1 + 4, BOX_Y2 - 8),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 1)


def pixel_to_arm(px, py, H):
    pt = np.array([[[px, py]]], dtype=np.float32)
    result = cv2.perspectiveTransform(pt, H)
    return int(result[0][0][0]), int(result[0][0][1])


def load_calibration():
    if not os.path.exists(CALIBRATION_FILE):
        print(f"ERROR: {CALIBRATION_FILE} not found. Run --calibrate first.")
        sys.exit(1)
    with open(CALIBRATION_FILE) as f:
        return json.load(f)


def save_calibration(data):
    with open(CALIBRATION_FILE, "w") as f:
        json.dump(data, f, indent=2)
    print(f"Calibration saved to {CALIBRATION_FILE}")


def open_webcam():
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("ERROR: Could not open webcam.")
        sys.exit(1)
    return cap


def make_tcp_server():
    """Bind TCP server socket and return (server_sock)."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", PI_PORT))
    srv.listen(1)
    print(f"Waiting for Pi to connect on port {PI_PORT}...")
    return srv


def accept_connection(srv):
    conn, addr = srv.accept()
    print(f"Pi connected from {addr}. Starting tracking.")
    return conn


# ---------------------------------------------------------------------------
# Mode A — Homography calibration
# ---------------------------------------------------------------------------

def calibrate():
    cap = open_webcam()
    corners = [
        ("TL", (BOX_X1, BOX_Y1)),
        ("TR", (BOX_X2, BOX_Y1)),
        ("BL", (BOX_X1, BOX_Y2)),
        ("BR", (BOX_X2, BOX_Y2)),
    ]
    arm_points = []
    step = 0

    instructions = [
        "[1/4] Move arm claw tip to TOP-LEFT corner of the green box. Press SPACE to record.",
        "[2/4] Move arm claw tip to TOP-RIGHT corner. Press SPACE to record.",
        "[3/4] Move arm claw tip to BOTTOM-LEFT corner. Press SPACE to record.",
        "[4/4] Move arm claw tip to BOTTOM-RIGHT corner. Press SPACE to record.",
    ]

    print("\n=== 4-Corner Homography Calibration ===")
    print(instructions[0])

    try:
        while step < 4:
            ret, frame = cap.read()
            if not ret:
                continue

            draw_box(frame)

            # Draw all corner dots
            for label, (cx, cy) in corners:
                cv2.circle(frame, (cx, cy), 6, (0, 0, 255), -1)
                cv2.putText(frame, label, (cx + 8, cy - 8),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1)

            # Highlight current target corner
            _, (tx, ty) = corners[step]
            cv2.circle(frame, (tx, ty), 10, (255, 255, 0), 2)

            # Overlay instruction
            cv2.putText(frame, instructions[step], (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)

            cv2.imshow("Cura Calibration", frame)
            key = cv2.waitKey(1) & 0xFF

            if key == ord(' '):
                raw = input(f"\nEnter arm X,Y from GetArmEndPoseMsgs() [X Y]: ").strip()
                try:
                    parts = raw.split()
                    ax, ay = int(parts[0]), int(parts[1])
                    arm_points.append([ax, ay])
                    print(f"  Recorded {corners[step][0]}: pixel={corners[step][1]}, arm=({ax}, {ay})")
                    step += 1
                    if step < 4:
                        print(instructions[step])
                except (ValueError, IndexError):
                    print("  Invalid input, try again.")
            elif key == ord('q'):
                print("Calibration cancelled.")
                return
    finally:
        cap.release()
        cv2.destroyAllWindows()

    image_points = np.array(
        [[BOX_X1, BOX_Y1], [BOX_X2, BOX_Y1], [BOX_X1, BOX_Y2], [BOX_X2, BOX_Y2]],
        dtype=np.float32,
    )
    arm_pts = np.array(arm_points, dtype=np.float32)
    H, _ = cv2.findHomography(image_points, arm_pts)

    data = {
        "H": H.tolist(),
        "box": [BOX_X1, BOX_Y1, BOX_X2, BOX_Y2],
        "focal_length_px": None,
        "reference_face_width_px": None,
        "reference_z_mm": None,
    }
    save_calibration(data)


# ---------------------------------------------------------------------------
# Mode B — Z calibration
# ---------------------------------------------------------------------------

def calibrate_z():
    cal = load_calibration()
    cap = open_webcam()

    mp_face_mesh = mp.solutions.face_mesh
    face_mesh = mp_face_mesh.FaceMesh(
        max_num_faces=1,
        min_detection_confidence=0.5,
        min_tracking_confidence=0.5,
    )
    mp_draw = mp.solutions.drawing_utils

    print("\n=== Z Depth Calibration ===")
    print("Have the patient sit at the EXACT delivery position.")
    print("Press SPACE when ready.")

    captured = False
    face_width_px = None

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                continue

            h, w = frame.shape[:2]
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            results = face_mesh.process(rgb)

            if results.multi_face_landmarks:
                for fl in results.multi_face_landmarks:
                    mp_draw.draw_landmarks(
                        frame, fl,
                        mp_face_mesh.FACEMESH_TESSELATION,
                        landmark_drawing_spec=mp_draw.DrawingSpec(
                            color=(0, 255, 0), thickness=1, circle_radius=1),
                        connection_drawing_spec=mp_draw.DrawingSpec(
                            color=(0, 128, 0), thickness=1),
                    )
                    lm234 = fl.landmark[234]
                    lm454 = fl.landmark[454]
                    face_width_px = abs(lm234.x - lm454.x) * w
                    cv2.putText(frame, f"Face width: {face_width_px:.0f}px", (10, 30),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 1)

            cv2.putText(frame, "Press SPACE to capture Z reference", (10, 60),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
            cv2.imshow("Cura Z Calibration", frame)
            key = cv2.waitKey(1) & 0xFF

            if key == ord(' '):
                if face_width_px is None or face_width_px < 10:
                    print("No face detected — make sure patient is visible.")
                    continue
                try:
                    z_mm = float(input("\nEnter measured Z distance from arm base to mouth in mm: "))
                except ValueError:
                    print("Invalid input.")
                    continue
                focal_length_px = face_width_px * z_mm / KNOWN_FACE_WIDTH_MM
                cal["focal_length_px"] = focal_length_px
                cal["reference_face_width_px"] = face_width_px
                cal["reference_z_mm"] = z_mm
                save_calibration(cal)
                print(f"Z calibration saved. Reference face width: {face_width_px:.0f}px at Z={z_mm}mm")
                break
            elif key == ord('q'):
                print("Z calibration cancelled.")
                break
    finally:
        face_mesh.close()
        cap.release()
        cv2.destroyAllWindows()


# ---------------------------------------------------------------------------
# Mode C — Hand tracking
# ---------------------------------------------------------------------------

def track_hand():
    cal = load_calibration()
    H = np.array(cal["H"], dtype=np.float64)

    cap = open_webcam()
    srv = make_tcp_server()

    mp_hands = mp.solutions.hands
    hands = mp_hands.Hands(
        max_num_hands=1,
        min_detection_confidence=0.6,
        min_tracking_confidence=0.5,
    )
    mp_draw = mp.solutions.drawing_utils

    conn = None
    last_x, last_y = None, None

    try:
        conn = accept_connection(srv)

        while True:
            ret, frame = cap.read()
            if not ret:
                continue

            h, w = frame.shape[:2]
            draw_box(frame)

            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            results = hands.process(rgb)

            if results.multi_hand_landmarks:
                hl = results.multi_hand_landmarks[0]
                mp_draw.draw_landmarks(frame, hl, mp_hands.HAND_CONNECTIONS)

                lm = hl.landmark[9]
                px = int(lm.x * w)
                py = int(lm.y * h)

                cv2.circle(frame, (px, py), 10, (255, 255, 0), -1)  # cyan

                if BOX_X1 <= px <= BOX_X2 and BOX_Y1 <= py <= BOX_Y2:
                    arm_x, arm_y = pixel_to_arm(px, py, H)
                    arm_z = FALLBACK_Z

                    changed = (
                        last_x is None
                        or abs(arm_x - last_x) > SEND_THRESHOLD
                        or abs(arm_y - last_y) > SEND_THRESHOLD
                    )

                    if changed:
                        cmd = {
                            "x": arm_x,
                            "y": arm_y,
                            "z": arm_z,
                            "rx": 0,
                            "ry": ARM_RY_DELIVERY,
                            "rz": 0,
                        }
                        try:
                            conn.sendall((json.dumps(cmd) + "\n").encode())
                            last_x, last_y = arm_x, arm_y
                        except (BrokenPipeError, OSError) as e:
                            print(f"TCP error: {e}. Waiting for reconnect...")
                            conn.close()
                            conn = accept_connection(srv)
                            last_x, last_y = None, None

                    cv2.putText(
                        frame,
                        f"ARM: X={arm_x//1000}mm Y={arm_y//1000}mm Z={arm_z//1000}mm",
                        (10, h - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 0), 1,
                    )

            cv2.imshow("Cura Vision", frame)
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break
    finally:
        hands.close()
        cap.release()
        cv2.destroyAllWindows()
        if conn:
            conn.close()
        srv.close()


# ---------------------------------------------------------------------------
# Mode D — Mouth tracking
# ---------------------------------------------------------------------------

def track_mouth():
    cal = load_calibration()
    H = np.array(cal["H"], dtype=np.float64)

    cap = open_webcam()
    srv = make_tcp_server()

    mp_face_mesh = mp.solutions.face_mesh
    face_mesh = mp_face_mesh.FaceMesh(
        max_num_faces=1,
        min_detection_confidence=0.5,
        min_tracking_confidence=0.5,
    )

    conn = None
    last_x, last_y = None, None

    try:
        conn = accept_connection(srv)

        while True:
            ret, frame = cap.read()
            if not ret:
                continue

            h, w = frame.shape[:2]
            draw_box(frame)

            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            results = face_mesh.process(rgb)

            if results.multi_face_landmarks:
                fl = results.multi_face_landmarks[0]

                lm13 = fl.landmark[13]
                lm14 = fl.landmark[14]
                px = int((lm13.x + lm14.x) / 2 * w)
                py = int((lm13.y + lm14.y) / 2 * h)

                cv2.circle(frame, (px, py), 10, (255, 0, 255), -1)  # magenta

                # Z from face width
                lm234 = fl.landmark[234]
                lm454 = fl.landmark[454]
                face_width_px = abs(lm234.x - lm454.x) * w
                if cal.get("focal_length_px") and face_width_px > 10:
                    z_mm = (KNOWN_FACE_WIDTH_MM * cal["focal_length_px"]) / face_width_px
                    arm_z = int(z_mm * 1000)
                else:
                    arm_z = FALLBACK_Z

                if BOX_X1 <= px <= BOX_X2 and BOX_Y1 <= py <= BOX_Y2:
                    arm_x, arm_y = pixel_to_arm(px, py, H)

                    changed = (
                        last_x is None
                        or abs(arm_x - last_x) > SEND_THRESHOLD
                        or abs(arm_y - last_y) > SEND_THRESHOLD
                    )

                    if changed:
                        cmd = {
                            "x": arm_x,
                            "y": arm_y,
                            "z": arm_z,
                            "rx": 0,
                            "ry": ARM_RY_DELIVERY,
                            "rz": 0,
                        }
                        try:
                            conn.sendall((json.dumps(cmd) + "\n").encode())
                            last_x, last_y = arm_x, arm_y
                        except (BrokenPipeError, OSError) as e:
                            print(f"TCP error: {e}. Waiting for reconnect...")
                            conn.close()
                            conn = accept_connection(srv)
                            last_x, last_y = None, None

                    cv2.putText(
                        frame,
                        f"ARM: X={arm_x//1000}mm Y={arm_y//1000}mm Z={arm_z//1000}mm",
                        (10, h - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 0, 255), 1,
                    )

            cv2.imshow("Cura Vision", frame)
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break
    finally:
        face_mesh.close()
        cap.release()
        cv2.destroyAllWindows()
        if conn:
            conn.close()
        srv.close()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Cura vision client")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--calibrate", action="store_true",
                       help="4-corner homography calibration")
    group.add_argument("--calibrate-z", action="store_true",
                       help="Z depth calibration from face size")
    group.add_argument("--track-hand", action="store_true",
                       help="Hand tracking prototype (landmark 9)")
    group.add_argument("--track-mouth", action="store_true",
                       help="Mouth tracking for real demo")
    args = parser.parse_args()

    if args.calibrate:
        calibrate()
    elif args.calibrate_z:
        calibrate_z()
    elif args.track_hand:
        track_hand()
    elif args.track_mouth:
        track_mouth()


if __name__ == "__main__":
    main()
