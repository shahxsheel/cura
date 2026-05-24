import time
from pyagxarm import *

piper = C_PiperInterface(
    can_name="can_piper",
    judge_flag=False,
    can_auto_init=True
)

piper.ConnectPort()
time.sleep(0.5)

# Switch to slave/servant mode
piper.MasterSlaveConfig(0xFC, 0, 0, 0)

print("Sent slave/servant mode command on can_piper")
