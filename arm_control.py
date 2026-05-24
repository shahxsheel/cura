import time, signal, sys
from piper_sdk import *

# ── Connect ──────────────────────────────────────
piper = C_PiperInterface_V2("can0")
piper.ConnectPort()

def emergency_stop(sig=None, frame=None):
    print("\n⚠️  Emergency stop!")
    piper.EmergencyStop(0x01)
    sys.exit(0)
signal.signal(signal.SIGINT, emergency_stop)

while not piper.EnablePiper():
    time.sleep(0.01)
print("✅ Arm enabled")

# ── Helpers ──────────────────────────────────────
def move(j1=0, j2=0, j3=0, j4=0, j5=0, j6=0, secs=3.0, speed=20):
    """Move joints. Values in degrees * 1000 (e.g. 60° = 60000)"""
    start = time.time()
    while time.time() - start < secs:
        piper.ModeCtrl(0x01, 0x01, speed, 0x00)
        piper.JointCtrl(j1, j2, j3, j4, j5, j6)
        time.sleep(0.02)

def gripper(angle, secs=2.0):
    """angle: 0=closed, 70000=fully open"""
    start = time.time()
    while time.time() - start < secs:
        piper.ModeCtrl(0x01, 0x01, 20, 0x00)
        piper.GripperCtrl(angle, 1000, 0x01, 0)
        time.sleep(0.02)

def home(secs=3.0):
    move(0, 0, 0, 0, 0, 0, secs)

def status():
    j = piper.GetArmJointMsgs().joint_state
    print(f"J1:{j.joint_1/1000:.1f} J2:{j.joint_2/1000:.1f} J3:{j.joint_3/1000:.1f} J4:{j.joint_4/1000:.1f} J5:{j.joint_5/1000:.1f} J6:{j.joint_6/1000:.1f}")

# ── Your sequence here ───────────────────────────
home()
status()

move(j2=60000, j3=-40000)
status()

gripper(70000)   # open
gripper(0)       # close

home()
print("Done!")
