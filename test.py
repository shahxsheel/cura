import time
from pyAgxArm import create_agx_arm_config, AgxArmFactory, ArmModel, PiperFW

cfg = create_agx_arm_config(
    robot=ArmModel.PIPER,
    firmeware_version=PiperFW.DEFAULT,  # change to PiperFW.V183 or PiperFW.V188 if your firmware needs it
    interface="socketcan",
    channel="can0",
)

robot = AgxArmFactory.create_arm(cfg)
robot.connect()

time.sleep(1)

# servant/slave mode = follower mode
robot.set_follower_mode()

time.sleep(1)

status = robot.get_arm_status()
print("Set Piper to follower/servant mode.")
if status is not None:
    print(status.msg)

robot.disconnect()