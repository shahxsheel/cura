import socket, time
from pyAgxArm import create_agx_arm_config, AgxArmFactory, ArmModel, PiperFW

cfg = create_agx_arm_config(
    robot=ArmModel.PIPER,
    firmeware_version=PiperFW.DEFAULT,
    interface="socketcan",
    channel="can0",
)
robot = AgxArmFactory.create_arm(cfg)
robot.connect()
time.sleep(2)
while not robot.enable():
    time.sleep(0.01)
print("Arm ready!")

robot.set_speed_percent(20)

def wave():
    start = time.time()
    while time.time() - start < 3.0:
        robot.move_j([0.0, 0.5, -0.5, 0.0, 0.5, 0.0])
        time.sleep(0.02)
    start = time.time()
    while time.time() - start < 3.0:
        robot.move_j([0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
        time.sleep(0.02)

server = __import__('socket').socket()
server.setsockopt(__import__('socket').SOL_SOCKET, __import__('socket').SO_REUSEADDR, 1)
server.bind(("0.0.0.0", 9999))
server.listen(1)
print("Waiting for trigger on port 9999...")

while True:
    conn, addr = server.accept()
    data = conn.recv(1024).decode().strip()
    conn.close()
    print(f"Got: {data}")
    if data == "GO":
        wave()
