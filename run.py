from pyAgxArm import create_agx_arm_config, AgxArmFactory, ArmModel, PiperFW
import time

cfg = create_agx_arm_config(
    robot=ArmModel.PIPER,
    firmeware_version=PiperFW.DEFAULT,
    interface="socketcan",
    channel="can0",
)
robot = AgxArmFactory.create_arm(cfg)
robot.connect()
robot.reset()
print("resetting")
time.sleep(2.0)

print("FPS:", robot.get_fps())

while not robot.enable():
    time.sleep(0.01)
print("Enabled!")

robot.set_speed_percent(30)

# position in radians [j1, j2, j3, j4, j5, j6]
position = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]

print("Moving to zero...")
robot.move_j(position)
time.sleep(5.0)

ja = robot.get_joint_angles()
if ja:
    print("Joints:", [round(a, 3) for a in ja.msg])

# gripper
end_effector = robot.init_effector(robot.OPTIONS.EFFECTOR.AGX_GRIPPER)
robot.set_motion_mode(robot.OPTIONS.MOTION_MODE.P)
end_effector.move_gripper_m(0.07)  # open
time.sleep(2.0)
end_effector.move_gripper_deg(0)   # close
time.sleep(2.0)

print("Done!")
