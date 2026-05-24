from piper_sdk import *
import time

piper = C_PiperInterface_V2("can0")
piper.ConnectPort()

while not piper.EnablePiper():
    time.sleep(0.01)
print("Enabled!")

def move_xyz(x, y, z, ry=85000, secs=5.0, speed=30):
    """Move to XYZ in 0.001mm units, RY in 0.001deg"""
    print(f"  Moving to X={x/1000:.0f}mm Y={y/1000:.0f}mm Z={z/1000:.0f}mm")
    start = time.time()
    started = False
    while time.time() - start < secs:
        piper.MotionCtrl_2(0x01, 0x00, speed, 0x00)
        piper.EndPoseCtrl(x, y, z, 0, ry, 0)
        time.sleep(0.02)
        motion = piper.GetArmStatus().arm_status.motion_status
        if not started and motion != 0:
            started = True
        elif started and motion == 0:
            print("  ✅ Reached!")
            return
    print("  ⚠️ Timeout")

def move_joints(j1=0, j2=0, j3=0, j4=0, j5=0, j6=0, secs=5.0, speed=30):
    start = time.time()
    started = False
    while time.time() - start < secs:
        piper.MotionCtrl_2(0x01, 0x01, speed, 0x00)
        piper.JointCtrl(j1, j2, j3, j4, j5, j6)
        time.sleep(0.02)
        motion = piper.GetArmStatus().arm_status.motion_status
        if not started and motion != 0:
            started = True
        elif started and motion == 0:
            return

# Zero first
print("→ Going to zero...")
move_joints(0, 0, 0, 0, 0, 0)

# Move to each position one at a time
print("→ Center")
move_xyz(200000, 0, 300000)

print("→ Right 10cm")
move_xyz(200000, 300000, 300000)

print("→ Left 10cm")
move_xyz(200000, -300000, 300000)

print("→ Forward 10cm")
move_xyz(500000, 0, 300000)

print("→ Up 10cm")
move_xyz(200000, 0, 500000)

print("→ Back to zero")
move_joints(0, 0, 0, 0, 0, 0)
print("Done!")