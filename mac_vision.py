#!/usr/bin/env python3
"""
mac_vision.py — Cura vision client (runs on Mac)

Usage:
    python3 mac_vision.py --adjust-box      # live box position adjustment
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
import threading

import cv2
import mediapipe as mp
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision as mp_vision
from mediapipe.tasks.python.vision import RunningMode
import numpy as np

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MAC_IP = "0.0.0.0"
ARM_POS_PORT = 9998   # Pi streams arm position TO Mac on this port
CMD_PORT = 9999       # Mac sends arm move commands TO Pi on this port
BOX_X1, BOX_Y1 = 510, 350
BOX_X2, BOX_Y2 = 1370, 1050
FALLBACK_Z = 350000          # 350mm in 0.001mm units
KNOWN_FACE_WIDTH_MM = 150    # average adult face width at cheekbones
SEND_THRESHOLD = 2000        # minimum change in 0.001mm before re-sending
ARM_RY_DELIVERY = 85000      # 85 degree tilt for delivery (0.001 degree units)

CALIBRATION_FILE = "calibration.json"

_MODEL_URLS = {
    "hand_landmarker": "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task",
    "face_landmarker": "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/latest/face_landmarker.task",
}

def _download_model(name: str) -> str:
    import urllib.request
    path = f"{name}.task"
    if not os.path.exists(path):
        print(f"Downloading {name} model...")
        urllib.request.urlretrieve(_MODEL_URLS[name], path)
        print(f"  Saved to {path}")
    return path


# ---------------------------------------------------------------------------
# Shared arm position (updated by background thread)
# ---------------------------------------------------------------------------
_arm_pos = {"x": None, "y": None, "z": None}
_arm_pos_lock = threading.Lock()


def start_arm_pos_listener():
    """Accept one connection from Pi and continuously read arm position into _arm_pos."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((MAC_IP, ARM_POS_PORT))
    srv.listen(1)
    print(f"Waiting for Pi arm-position stream on port {ARM_POS_PORT}...")
    conn, addr = srv.accept()
    print(f"Pi connected for position stream from {addr}")

    def _reader():
        buf = ""
        while True:
            try:
                data = conn.recv(4096).decode()
                if not data:
                    break
                buf += data
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    try:
                        pos = json.loads(line.strip())
                        with _arm_pos_lock:
                            _arm_pos["x"] = pos["x"]
                            _arm_pos["y"] = pos["y"]
                            _arm_pos["z"] = pos["z"]
                    except (json.JSONDecodeError, KeyError):
                        pass
            except OSError:
                break

    t = threading.Thread(target=_reader, daemon=True)
    t.start()
    return srv, conn


def get_arm_pos():
    with _arm_pos_lock:
        return _arm_pos["x"], _arm_pos["y"], _arm_pos["z"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def draw_box(frame, x1=BOX_X1, y1=BOX_Y1, x2=BOX_X2, y2=BOX_Y2):
    cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
    mid_y = (y1 + y2) // 2
    cv2.line(frame, (x1, mid_y), (x2, mid_y), (0, 255, 255), 1)
    cv2.putText(frame, "FACE", (x1 + 4, mid_y - 8),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 1)
    cv2.putText(frame, "FOOD", (x1 + 4, y2 - 8),
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


def make_cmd_server():
    """TCP server that accepts the Pi's command connection (for move commands)."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((MAC_IP, CMD_PORT))
    srv.listen(1)
    print(f"Waiting for Pi to connect for commands on port {CMD_PORT}...")
    return srv


def accept_connection(srv):
    conn, addr = srv.accept()
    print(f"Pi connected for commands from {addr}. Starting tracking.")
    return conn


# ---------------------------------------------------------------------------
# Mode 0 — Live box adjustment
# ---------------------------------------------------------------------------

def adjust_box():
    cap = open_webcam()

    x1, y1 = BOX_X1, BOX_Y1
    x2, y2 = BOX_X2, BOX_Y2
    step = 10

    print("\n=== Live Box Adjustment ===")
    print("W/A/S/D: move box | I/K: top edge | U/O: bottom edge | J/L: left/right edge")
    print("ENTER: print coords | Q: quit\n")

    controls = "W/A/S/D: move | I/K: top | U/O: bottom | J/L: sides | ENTER: print | Q: quit"

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                continue

            draw_box(frame, x1, y1, x2, y2)
            cv2.putText(frame, f"TL=({x1},{y1})  BR=({x2},{y2})",
                        (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
            cv2.putText(frame, controls, (10, frame.shape[0] - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (200, 200, 200), 1)

            cv2.imshow("Box Adjustment", frame)
            key = cv2.waitKey(1) & 0xFF

            if key == ord('q'):
                break
            elif key == 13:
                print(f"\nBOX_X1, BOX_Y1 = {x1}, {y1}")
                print(f"BOX_X2, BOX_Y2 = {x2}, {y2}\n")
            elif key == ord('w'): y1 -= step; y2 -= step
            elif key == ord('s'): y1 += step; y2 += step
            elif key == ord('a'): x1 -= step; x2 -= step
            elif key == ord('d'): x1 += step; x2 += step
            elif key == ord('i'): y1 -= step
            elif key == ord('k'): y1 += step
            elif key == ord('u'): y2 -= step
            elif key == ord('o'): y2 += step
            elif key == ord('j'): x1 -= step
            elif key == ord('l'): x1 += step

            x1 = max(0, x1); y1 = max(0, y1)
            x2 = max(x1 + 20, x2); y2 = max(y1 + 20, y2)

    finally:
        cap.release()
        cv2.destroyAllWindows()

    print(f"BOX_X1, BOX_Y1 = {x1}, {y1}")
    print(f"BOX_X2, BOX_Y2 = {x2}, {y2}")


# ---------------------------------------------------------------------------
# Mode A — Homography calibration
# ---------------------------------------------------------------------------

def calibrate():
    corners = [
        ("TL", (BOX_X1, BOX_Y1)),
        ("TR", (BOX_X2, BOX_Y1)),
        ("BL", (BOX_X1, BOX_Y2)),
        ("BR", (BOX_X2, BOX_Y2)),
    ]
    corner_names = ["TOP-LEFT", "TOP-RIGHT", "BOTTOM-LEFT", "BOTTOM-RIGHT"]

    # Start arm position listener — Pi streams live position to us
    pos_srv, pos_conn = start_arm_pos_listener()

    print("\n=== 4-Corner Homography Calibration ===")
    print("Jog the arm claw tip to align with each highlighted corner dot on screen.")
    print("Press SPACE to record each corner.\n")

    cap = open_webcam()
    arm_points = []
    step = 0

    try:
        while step < 4:
            ret, frame = cap.read()
            if not ret:
                continue

            draw_box(frame)

            # Draw all corner dots — dim ones already done, bright current target
            for i, (label, (cx, cy)) in enumerate(corners):
                if i < step:
                    # already recorded — green check
                    cv2.circle(frame, (cx, cy), 8, (0, 200, 0), -1)
                    cv2.putText(frame, f"{label} ✓", (cx + 10, cy - 8),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 200, 0), 1)
                elif i == step:
                    # current target — flashing yellow, larger
                    cv2.circle(frame, (cx, cy), 14, (0, 255, 255), 3)
                    cv2.circle(frame, (cx, cy), 5, (0, 255, 255), -1)
                    cv2.putText(frame, f"{label} <- AIM HERE", (cx + 10, cy - 8),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 255), 2)
                else:
                    # upcoming — grey
                    cv2.circle(frame, (cx, cy), 8, (120, 120, 120), -1)
                    cv2.putText(frame, label, (cx + 10, cy - 8),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (120, 120, 120), 1)

            # Show current arm position live on frame
            ax, ay, az = get_arm_pos()
            if ax is not None:
                pos_text = f"Arm: X={ax}  Y={ay}  Z={az}"
                cv2.putText(frame, pos_text, (10, frame.shape[0] - 40),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)

            # Instruction overlay
            cv2.putText(frame,
                        f"[{step+1}/4] Align claw with {corner_names[step]} dot. SPACE=record  Q=cancel",
                        (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 255), 2)

            cv2.imshow("Cura Calibration", frame)
            key = cv2.waitKey(1) & 0xFF

            if key == ord(' '):
                ax, ay, az = get_arm_pos()
                if ax is None:
                    print("No arm position yet — is Pi running pi_arm_server.py --calibrate?")
                    continue
                label, pixel = corners[step]
                arm_points.append([ax, ay])
                print(f"  Recorded {label}: pixel={pixel}, arm=({ax}, {ay})")
                step += 1
            elif key == ord('q'):
                print("Calibration cancelled.")
                return
    finally:
        cap.release()
        cv2.destroyAllWindows()
        pos_conn.close()
        pos_srv.close()

    if len(arm_points) < 4:
        print("Calibration incomplete.")
        return

    print("\nAll 4 corners recorded. Computing homography...")
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

    face_width_px = None
    _latest_face_width = [None]

    base_opts = mp_python.BaseOptions(model_asset_path=_download_model("face_landmarker"))
    opts = mp_vision.FaceLandmarkerOptions(
        base_options=base_opts,
        running_mode=RunningMode.VIDEO,
        num_faces=1,
    )
    detector = mp_vision.FaceLandmarker.create_from_options(opts)

    print("\n=== Z Depth Calibration ===")
    print("Have the patient sit at the EXACT delivery position.")
    print("Press SPACE when ready.")

    try:
        ts = 0
        while True:
            ret, frame = cap.read()
            if not ret:
                continue

            h, w = frame.shape[:2]
            ts += 33
            mp_img = mp.Image(image_format=mp.ImageFormat.SRGB,
                              data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            result = detector.detect_for_video(mp_img, ts)

            if result.face_landmarks:
                fl = result.face_landmarks[0]
                lm234 = fl[234]; lm454 = fl[454]
                fw = abs(lm234.x - lm454.x) * w
                _latest_face_width[0] = fw
                cv2.putText(frame, f"Face width: {fw:.0f}px", (10, 30),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 1)

            cv2.putText(frame, "Press SPACE to capture Z reference", (10, 60),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
            cv2.imshow("Cura Z Calibration", frame)
            key = cv2.waitKey(1) & 0xFF

            if key == ord(' '):
                fw = _latest_face_width[0]
                if fw is None or fw < 10:
                    print("No face detected — make sure patient is visible.")
                    continue
                cv2.destroyAllWindows()
                cap.release()
                try:
                    z_mm = float(input("\nEnter measured Z distance from arm base to mouth in mm: "))
                except ValueError:
                    print("Invalid input.")
                    return
                focal_length_px = fw * z_mm / KNOWN_FACE_WIDTH_MM
                cal["focal_length_px"] = focal_length_px
                cal["reference_face_width_px"] = fw
                cal["reference_z_mm"] = z_mm
                save_calibration(cal)
                print(f"Z calibration saved. Reference face width: {fw:.0f}px at Z={z_mm}mm")
                return
            elif key == ord('q'):
                print("Z calibration cancelled.")
                break
    finally:
        if cap.isOpened():
            cap.release()
        cv2.destroyAllWindows()
        detector.close()


# ---------------------------------------------------------------------------
# Mode C — Hand tracking
# ---------------------------------------------------------------------------

def track_hand():
    cal = load_calibration()
    H = np.array(cal["H"], dtype=np.float64)

    cap = open_webcam()
    srv = make_cmd_server()

    base_opts = mp_python.BaseOptions(model_asset_path=_download_model("hand_landmarker"))
    opts = mp_vision.HandLandmarkerOptions(
        base_options=base_opts,
        running_mode=RunningMode.VIDEO,
        num_hands=1,
        min_hand_detection_confidence=0.6,
        min_tracking_confidence=0.5,
    )
    detector = mp_vision.HandLandmarker.create_from_options(opts)

    conn = None
    last_x, last_y = None, None
    ts = 0

    try:
        conn = accept_connection(srv)

        while True:
            ret, frame = cap.read()
            if not ret:
                continue

            h, w = frame.shape[:2]
            draw_box(frame)
            ts += 33

            mp_img = mp.Image(image_format=mp.ImageFormat.SRGB,
                              data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            result = detector.detect_for_video(mp_img, ts)

            if result.hand_landmarks:
                lms = result.hand_landmarks[0]
                # Draw all landmarks
                for lm in lms:
                    cx, cy = int(lm.x * w), int(lm.y * h)
                    cv2.circle(frame, (cx, cy), 3, (200, 200, 200), -1)

                lm9 = lms[9]
                px = int(lm9.x * w)
                py = int(lm9.y * h)
                cv2.circle(frame, (px, py), 10, (255, 255, 0), -1)

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
                            "x": arm_x, "y": arm_y, "z": arm_z,
                            "rx": 0, "ry": ARM_RY_DELIVERY, "rz": 0,
                        }
                        try:
                            conn.sendall((json.dumps(cmd) + "\n").encode())
                            last_x, last_y = arm_x, arm_y
                        except (BrokenPipeError, OSError) as e:
                            print(f"TCP error: {e}. Waiting for reconnect...")
                            conn.close()
                            conn = accept_connection(srv)
                            last_x, last_y = None, None

                    cv2.putText(frame,
                        f"ARM: X={arm_x//1000}mm Y={arm_y//1000}mm Z={arm_z//1000}mm",
                        (10, h - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 0), 1)

            cv2.imshow("Cura Vision", frame)
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break
    finally:
        detector.close()
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
    srv = make_cmd_server()

    base_opts = mp_python.BaseOptions(model_asset_path=_download_model("face_landmarker"))
    opts = mp_vision.FaceLandmarkerOptions(
        base_options=base_opts,
        running_mode=RunningMode.VIDEO,
        num_faces=1,
    )
    detector = mp_vision.FaceLandmarker.create_from_options(opts)

    conn = None
    last_x, last_y = None, None
    ts = 0

    try:
        conn = accept_connection(srv)

        while True:
            ret, frame = cap.read()
            if not ret:
                continue

            h, w = frame.shape[:2]
            draw_box(frame)
            ts += 33

            mp_img = mp.Image(image_format=mp.ImageFormat.SRGB,
                              data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            result = detector.detect_for_video(mp_img, ts)

            if result.face_landmarks:
                fl = result.face_landmarks[0]

                lm13 = fl[13]; lm14 = fl[14]
                px = int((lm13.x + lm14.x) / 2 * w)
                py = int((lm13.y + lm14.y) / 2 * h)
                cv2.circle(frame, (px, py), 10, (255, 0, 255), -1)

                lm234 = fl[234]; lm454 = fl[454]
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
                            "x": arm_x, "y": arm_y, "z": arm_z,
                            "rx": 0, "ry": ARM_RY_DELIVERY, "rz": 0,
                        }
                        try:
                            conn.sendall((json.dumps(cmd) + "\n").encode())
                            last_x, last_y = arm_x, arm_y
                        except (BrokenPipeError, OSError) as e:
                            print(f"TCP error: {e}. Waiting for reconnect...")
                            conn.close()
                            conn = accept_connection(srv)
                            last_x, last_y = None, None

                    cv2.putText(frame,
                        f"ARM: X={arm_x//1000}mm Y={arm_y//1000}mm Z={arm_z//1000}mm",
                        (10, h - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 0, 255), 1)

            cv2.imshow("Cura Vision", frame)
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break
    finally:
        detector.close()
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
    group.add_argument("--adjust-box", action="store_true")
    group.add_argument("--calibrate", action="store_true")
    group.add_argument("--calibrate-z", action="store_true")
    group.add_argument("--track-hand", action="store_true")
    group.add_argument("--track-mouth", action="store_true")
    args = parser.parse_args()

    if args.adjust_box:
        adjust_box()
    elif args.calibrate:
        calibrate()
    elif args.calibrate_z:
        calibrate_z()
    elif args.track_hand:
        track_hand()
    elif args.track_mouth:
        track_mouth()


if __name__ == "__main__":
    main()
