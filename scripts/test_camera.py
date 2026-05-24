"""Smoke test for the Orbbec Gemini camera on Raspberry Pi 5.

Run with:  uv run python scripts/test_camera.py
"""

import subprocess
import sys
import time


def check_usb() -> bool:
    try:
        out = subprocess.check_output(["lsusb"], text=True)
        matches = [l for l in out.splitlines() if "2bc5" in l.lower()]
        if matches:
            print(f"[OK]  USB: {matches[0].strip()}")
            return True
        print("[FAIL] USB: Orbbec device not found — check cable and port")
        return False
    except FileNotFoundError:
        print("[SKIP] lsusb not available (not on Linux?)")
        return True  # don't block on macOS dev machines


def check_import() -> bool:
    try:
        import pyorbbecsdk  # noqa: F401
        print("[OK]  Import: pyorbbecsdk loaded")
        return True
    except ImportError as e:
        print(f"[FAIL] Import: {e}")
        print("       Run: uv sync  (or: pip install pyorbbecsdk2)")
        return False


def check_pipeline() -> bool:
    try:
        from pyorbbecsdk import OBError, Pipeline

        pipeline = Pipeline()
        pipeline.start()
        print("[OK]  Pipeline: started successfully")

        # grab up to 30 frames across both streams
        color_ok = depth_ok = False
        deadline = time.time() + 5.0
        while time.time() < deadline and not (color_ok and depth_ok):
            frames = pipeline.wait_for_frames(200)
            if frames is None:
                continue
            if not color_ok and frames.get_color_frame() is not None:
                color_ok = True
                print("[OK]  Color stream: receiving frames")
            if not depth_ok and frames.get_depth_frame() is not None:
                depth_ok = True
                print("[OK]  Depth stream: receiving frames")

        if not color_ok:
            print("[WARN] Color stream: no frames received in 5 s")
        if not depth_ok:
            print("[WARN] Depth stream: no frames received in 5 s")

        pipeline.stop()
        print("[OK]  Pipeline: stopped cleanly")
        return color_ok or depth_ok

    except OBError as e:
        print(f"[FAIL] Pipeline OBError: {e}")
        if "permission" in str(e).lower():
            print("       Fix: sudo udevadm control --reload-rules && sudo udevadm trigger")
            print("       Then log out and back in (plugdev group membership)")
        return False
    except Exception as e:
        print(f"[FAIL] Pipeline error: {e}")
        return False


def main() -> None:
    print("── Orbbec Gemini camera test ──────────────────")
    steps = [
        ("USB detection", check_usb),
        ("SDK import",    check_import),
        ("Live stream",   check_pipeline),
    ]

    results = []
    for label, fn in steps:
        print(f"\n[{label}]")
        ok = fn()
        results.append((label, ok))
        if not ok and label != "USB detection":
            # SDK import failure makes pipeline test pointless
            print("\nStopping early — fix the error above and re-run.")
            break

    print("\n── Summary ─────────────────────────────────────")
    all_ok = True
    for label, ok in results:
        status = "PASS" if ok else "FAIL"
        print(f"  {status}  {label}")
        if not ok:
            all_ok = False

    print()
    if all_ok:
        print("Camera is ready.")
    else:
        print("Camera is NOT ready — see failures above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
