#!/usr/bin/env python3
"""
mac_vision.py — Cura vision client (runs on Mac)

Usage:
    python3 mac_vision.py --adjust-box      # live box position adjustment
    python3 mac_vision.py --calibrate       # 4-point homography calibration
    python3 mac_vision.py --track-hand      # hand tracking prototype (landmark 9)
    python3 mac_vision.py --track-mouth     # mouth tracking for real demo
"""
import argparse
import json
import os
import socket
import sys
import threading
import time

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
HARDCODED_ARM_X = int(os.environ.get("CURA_ARM_X_MM", "400")) * 1000  # forward distance, fixed
KNOWN_FACE_WIDTH_MM = 150    # average adult face width at cheekbones
SEND_THRESHOLD = 20000       # minimum change in 0.001mm before re-sending (20mm)
MIN_SEND_INTERVAL_S = 0.8    # rate-limit command sends — at most once every 0.8s

# Preset (Y, Z) targets (in mm) for auto-calibration. Spread in a rectangle.
CALIB_POINTS_YZ_MM = [
    (-120, 200),   # bottom-left
    ( 120, 200),   # bottom-right
    (-120, 400),   # top-left
    ( 120, 400),   # top-right
]
CALIB_SETTLE_S = 3.5         # seconds to wait after each move before prompting click
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
    index = int(os.environ.get("CURA_CAM_INDEX", "0"))
    cap = cv2.VideoCapture(index, cv2.CAP_AVFOUNDATION)
    if not cap.isOpened():
        cap = cv2.VideoCapture(index)
    if not cap.isOpened():
        print(f"ERROR: Could not open webcam at index {index}.")
        print("Try: CURA_CAM_INDEX=1 python3 mac_vision.py ...")
        sys.exit(1)
    # warm up — first few frames on Mac are often empty
    for _ in range(5):
        cap.read()
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
    """Manual-drag homography calibration.

    Pi (run with --calibrate) puts the arm in drag-teach and streams its
    live end-pose to Mac:9998. You physically move the arm to 4 spread
    positions; at each one, click the claw tip in the camera window. The
    Mac captures (pixel, live arm Y, live arm Z) for each click.
    """
    print("\n=== Manual-Drag 4-Point Calibration ===")
    print("On the Pi (separate terminal):  python3 pi_arm_server.py --calibrate")
    print("Arm should go limp (drag-teach). Move it to 4 well-spread positions.")
    print(f"Forward distance is fixed at X = {HARDCODED_ARM_X//1000} mm — try to")
    print("keep the claw roughly at that depth for each point.\n")

    # Open pose-stream listener (Pi connects in as client).
    pos_srv, pos_conn = start_arm_pos_listener()

    cap = open_webcam()
    pixel_points = []
    arm_points_yz_mm = []

    click_state = {"pt": None}

    def _on_click(event, x, y, flags, param):
        if event == cv2.EVENT_LBUTTONDOWN:
            click_state["pt"] = (x, y)

    cv2.namedWindow("Cura Calibration")
    cv2.setMouseCallback("Cura Calibration", _on_click)

    try:
        step = 0
        while step < 4:
            ret, frame = cap.read()
            if not ret:
                continue
            draw_box(frame)

            for i, (px, py) in enumerate(pixel_points):
                cv2.circle(frame, (px, py), 8, (0, 200, 0), -1)
                yy, zz = arm_points_yz_mm[i]
                cv2.putText(frame, f"{i+1}: Y={yy:.0f} Z={zz:.0f}mm",
                            (px + 10, py - 8), cv2.FONT_HERSHEY_SIMPLEX,
                            0.5, (0, 200, 0), 1)

            ax, ay, az = get_arm_pos()
            if ax is None:
                pose_txt = "Waiting for Pi pose stream on :9998 ..."
                pose_color = (0, 0, 255)
            else:
                pose_txt = (f"LIVE: X={ax/1000:.1f}  Y={ay/1000:.1f}  Z={az/1000:.1f} mm")
                pose_color = (255, 255, 0)
            cv2.putText(frame, pose_txt, (10, frame.shape[0] - 15),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, pose_color, 2)

            cv2.putText(frame,
                        f"[{step+1}/4] Drag arm to a spot, CLICK claw tip. Q=cancel",
                        (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)

            cv2.imshow("Cura Calibration", frame)
            key = cv2.waitKey(1) & 0xFF
            if key == ord('q'):
                print("Calibration cancelled.")
                return

            if click_state["pt"] is not None:
                ax, ay, az = get_arm_pos()
                if ay is None or az is None:
                    print("  ⚠️ No arm pose yet — is the Pi running --calibrate?")
                    click_state["pt"] = None
                    continue
                px, py = click_state["pt"]
                click_state["pt"] = None
                y_mm = ay / 1000.0
                z_mm = az / 1000.0
                pixel_points.append((px, py))
                arm_points_yz_mm.append((y_mm, z_mm))
                print(f"  ▶ [{step+1}/4]  pixel=({px}, {py})  "
                      f"arm=Y={y_mm:.1f}mm Z={z_mm:.1f}mm  (live X={ax/1000:.1f}mm)")
                step += 1
    finally:
        cap.release()
        cv2.destroyAllWindows()
        try: pos_conn.close()
        except Exception: pass
        try: pos_srv.close()
        except Exception: pass

    if len(pixel_points) < 4:
        print("Calibration incomplete.")
        return

    # Homography maps pixels -> (arm Y, arm Z) in 0.001mm units (Pi expects).
    image_pts = np.array(pixel_points, dtype=np.float32)
    arm_pts = np.array([[y * 1000.0, z * 1000.0] for y, z in arm_points_yz_mm],
                       dtype=np.float32)
    H, _ = cv2.findHomography(image_pts, arm_pts)
    if H is None:
        print("Homography failed — points may be collinear. Try again.")
        return

    print("\n=== Summary ===")
    for i, ((px, py), (y, z)) in enumerate(zip(pixel_points, arm_points_yz_mm)):
        print(f"  [{i+1}] pixel=({px}, {py})  arm=Y={y:.1f}mm Z={z:.1f}mm")
    print(f"\nHomography:\n{H}")

    data = {
        "H": H.tolist(),
        "pixel_points": pixel_points,
        "arm_points_yz_mm": arm_points_yz_mm,
        "hardcoded_arm_x_mm": HARDCODED_ARM_X // 1000,
    }
    save_calibration(data)
    print(f"\nSaved to {CALIBRATION_FILE}.")


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
    last_send_t = 0.0
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

                if True:
                    arm_y, arm_z = pixel_to_arm(px, py, H)
                    arm_x = HARDCODED_ARM_X

                    now = time.time()
                    changed = (
                        last_x is None
                        or abs(arm_y - last_x) > SEND_THRESHOLD
                        or abs(arm_z - last_y) > SEND_THRESHOLD
                    )
                    rate_ok = (now - last_send_t) >= MIN_SEND_INTERVAL_S

                    if changed and rate_ok:
                        cmd = {
                            "x": arm_x, "y": arm_y, "z": arm_z,
                            "rx": 0, "ry": ARM_RY_DELIVERY, "rz": 0,
                        }
                        try:
                            conn.sendall((json.dumps(cmd) + "\n").encode())
                            last_x, last_y = arm_y, arm_z
                            last_send_t = now
                        except (BrokenPipeError, OSError) as e:
                            print(f"TCP error: {e}. Waiting for reconnect...")
                            conn.close()
                            conn = accept_connection(srv)
                            last_x, last_y = None, None

                    cv2.putText(frame,
                        f"ARM: X={arm_x//1000}mm (fixed) Y={arm_y//1000}mm Z={arm_z//1000}mm",
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
    last_send_t = 0.0
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

                if True:
                    arm_y, arm_z = pixel_to_arm(px, py, H)
                    arm_x = HARDCODED_ARM_X

                    now = time.time()
                    changed = (
                        last_x is None
                        or abs(arm_y - last_x) > SEND_THRESHOLD
                        or abs(arm_z - last_y) > SEND_THRESHOLD
                    )
                    rate_ok = (now - last_send_t) >= MIN_SEND_INTERVAL_S

                    if changed and rate_ok:
                        cmd = {
                            "x": arm_x, "y": arm_y, "z": arm_z,
                            "rx": 0, "ry": ARM_RY_DELIVERY, "rz": 0,
                        }
                        try:
                            conn.sendall((json.dumps(cmd) + "\n").encode())
                            last_x, last_y = arm_y, arm_z
                            last_send_t = now
                        except (BrokenPipeError, OSError) as e:
                            print(f"TCP error: {e}. Waiting for reconnect...")
                            conn.close()
                            conn = accept_connection(srv)
                            last_x, last_y = None, None

                    cv2.putText(frame,
                        f"ARM: X={arm_x//1000}mm (fixed) Y={arm_y//1000}mm Z={arm_z//1000}mm",
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
    group.add_argument("--track-hand", action="store_true")
    group.add_argument("--track-mouth", action="store_true")
    args = parser.parse_args()

    if args.adjust_box:
        adjust_box()
    elif args.calibrate:
        calibrate()
    elif args.track_hand:
        track_hand()
    elif args.track_mouth:
        track_mouth()


if __name__ == "__main__":
    main()
