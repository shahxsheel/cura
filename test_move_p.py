"""
Minimal test: connect to Piper arm, read pose, move 20mm up, confirm change.
Run on Pi with can_piper active.
"""
import time
from pyAgxArm import create_agx_arm_config, AgxArmFactory, ArmModel, PiperFW

cfg = create_agx_arm_config(
    robot=ArmModel.PIPER,
    firmeware_version=PiperFW.DEFAULT,
    interface="socketcan",
    channel="can0",
)
robot = AgxArmFactory.create_arm(cfg)
robot.connect()
time.sleep(1.0)

while not robot.enable():
    time.sleep(0.01)
print("Arm enabled.")
robot.set_speed_percent(20)
time.sleep(0.5)

fp = robot.get_flange_pose()
if fp is None:
    print("ERROR: get_flange_pose returned None — arm not ready")
    exit(1)

x, y, z, roll, pitch, yaw = fp.msg
print(f"Current pose: x={x:.4f} y={y:.4f} z={z:.4f} roll={roll:.4f} pitch={pitch:.4f} yaw={yaw:.4f}")

target = [x, y, z + 0.02, roll, pitch, yaw]
print(f"Moving to z={z+0.02:.4f} (20mm up)...")
try:
    robot.move_p(target)
    print("move_p called OK")
except Exception as e:
    print(f"move_p ERROR: {type(e).__name__}: {e}")
    exit(1)

time.sleep(3.0)

fp2 = robot.get_flange_pose()
if fp2 is None:
    print("ERROR: get_flange_pose returned None after move")
    exit(1)

x2, y2, z2 = fp2.msg[0], fp2.msg[1], fp2.msg[2]
dz = z2 - z
print(f"Pose after: x={x2:.4f} y={y2:.4f} z={z2:.4f}")
if abs(dz) > 0.001:
    print(f"SUCCESS: z changed by {dz*1000:.1f}mm")
else:
    print(f"FAIL: z barely changed ({dz*1000:.2f}mm) — move_p not working")
