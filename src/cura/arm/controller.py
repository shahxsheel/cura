import logging
import threading
import time

from cura.arm.safety import SafetyModule
from cura.arm.trajectories import JointConfig, WAYPOINTS

logger = logging.getLogger(__name__)

_POSITION_TOLERANCE: float = 500.0   # 0.001 deg — ~0.5 degrees
_REACH_TIMEOUT: float = 10.0          # seconds to wait for a waypoint to be reached
_REACH_POLL_INTERVAL: float = 0.05   # seconds between position polls


class ArmController:
    """High-level interface for the AgileX Piper 6-DOF arm.

    Wraps C_PiperInterface with safety validation, threaded sequence execution,
    and an emergency stop mechanism.
    """

    def __init__(self, can_port: str = "can0", speed: int = 50, bustype: str = "auto") -> None:
        """
        Parameters
        ----------
        can_port:
            CAN bus device name. Used for socketcan ("can0" on Linux) and slcan
            ("/dev/cu.usbmodemXXX" on macOS). Not used for gs_usb — that always
            uses channel "0" via libusb.
        speed:
            Motion speed passed to MotionCtrl_2, 0-100.
        bustype:
            CAN bus type: "auto" detects the OS ("gs_usb" on Darwin for the
            candleLight adapter, "socketcan" on Linux). Explicit values
            "socketcan", "gs_usb", or "slcan" override auto-detection.
        """
        self._can_port = can_port
        self._speed = speed
        self._bustype = bustype
        self._safety = SafetyModule()
        self._piper: object | None = None
        self._stop_event = threading.Event()
        self._movement_thread: threading.Thread | None = None
        self._connected = False

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        """Initialise and enable the arm over CAN.

        Supports Linux (SocketCAN, auto-init) and macOS (gs_usb via candleLight
        USB-to-CAN adapter, or slcan via serial port). The bus type is resolved
        from ``self._bustype``: when set to "auto" the OS is detected at runtime
        — "gs_usb" on Darwin, "socketcan" elsewhere. Explicit values
        "socketcan", "gs_usb", or "slcan" bypass detection.

        Returns True on success, False if an exception occurs.
        """
        try:
            import platform as _platform
            from piper_sdk import C_PiperInterface

            bustype = self._bustype
            if bustype == "auto":
                bustype = "gs_usb" if _platform.system() == "Darwin" else "socketcan"

            if bustype in ("gs_usb", "slcan"):
                # macOS: manual CAN bus init (gs_usb for candleLight, slcan for serial adapters)
                self._piper = C_PiperInterface(
                    can_name=self._can_port,
                    judge_flag=False,
                    can_auto_init=False,
                )
                can_name = "0" if bustype == "gs_usb" else self._can_port
                self._piper.CreateCanBus(
                    can_name=can_name,
                    bustype=bustype,
                    expected_bitrate=1000000,
                    judge_flag=False,
                )
            else:
                # Linux: SocketCAN auto-init
                self._piper = C_PiperInterface(
                    can_name=self._can_port,
                    judge_flag=False,
                    can_auto_init=True,
                )

            self._piper.ConnectPort()
            self._piper.EnableArm(7)
            self._piper.MotionCtrl_2(0x01, 0x01, self._speed)
            self._connected = True
            logger.info("Connected to Piper arm (bustype=%s)", bustype)
            return True
        except Exception as e:
            logger.error("Failed to connect to arm: %s", e)
            self._connected = False
            return False

    def disconnect(self) -> None:
        """Emergency-stop the arm and mark it as disconnected."""
        self.emergency_stop()
        self._connected = False
        logger.info("Arm disconnected")

    # ------------------------------------------------------------------
    # Motion primitives
    # ------------------------------------------------------------------

    def home(self) -> bool:
        """Move the arm to the 'home' waypoint and block until complete."""
        return self.execute_waypoint("home", WAYPOINTS)

    def execute_waypoint(self, name: str, waypoints: dict[str, JointConfig]) -> bool:
        """Send the arm to a named waypoint and wait until it arrives.

        Returns False if the stop event is set before the arm reaches the target
        or if safety validation fails.
        """
        if self._stop_event.is_set():
            logger.warning("execute_waypoint called while stop_event is set — aborting")
            return False

        if name not in waypoints:
            logger.error("Unknown waypoint %r", name)
            return False

        cfg = waypoints[name]
        joints = cfg.as_list()

        if not self._safety.validate_joints(joints):
            logger.error("Waypoint %r failed safety validation", name)
            return False

        if self._piper is None:
            logger.error("Arm not connected — cannot execute waypoint %r", name)
            return False

        self._piper.JointCtrl(
            cfg.j1, cfg.j2, cfg.j3, cfg.j4, cfg.j5, cfg.j6
        )
        logger.info("Moving to waypoint %r", name)

        deadline = time.monotonic() + _REACH_TIMEOUT
        while time.monotonic() < deadline:
            if self._stop_event.is_set():
                logger.warning("Stop event set while waiting for waypoint %r", name)
                return False
            current = self.get_joint_positions()
            if all(
                abs(current[i] - joints[i]) <= _POSITION_TOLERANCE
                for i in range(6)
            ):
                logger.debug("Reached waypoint %r", name)
                return True
            time.sleep(_REACH_POLL_INTERVAL)

        logger.warning("Timed out waiting to reach waypoint %r", name)
        return False

    def execute_sequence(self, sequence: list[str], waypoints: dict[str, JointConfig]) -> bool:
        """Execute a list of named waypoints in a background thread.

        Returns True if the full sequence completed, False if aborted.
        The method launches the thread and returns immediately; use
        wait_for_completion() to block until done.
        """
        self._stop_event.clear()
        result: list[bool] = []

        def _run() -> None:
            for step in sequence:
                if self._stop_event.is_set():
                    logger.info("Sequence aborted before waypoint %r", step)
                    result.append(False)
                    return
                ok = self.execute_waypoint(step, waypoints)
                if not ok:
                    logger.warning("Sequence aborted at waypoint %r", step)
                    result.append(False)
                    return
            result.append(True)

        self._movement_thread = threading.Thread(target=_run, daemon=True, name="arm-sequence")
        self._movement_thread.start()
        return True

    # ------------------------------------------------------------------
    # Gripper control
    # ------------------------------------------------------------------

    def open_gripper(self, position: int = 70000) -> None:
        """Open the gripper to *position* (0.001 mm units, max 70000)."""
        if self._piper is None:
            logger.error("Arm not connected — cannot open gripper")
            return
        self._piper.GripperCtrl(position, 500)
        logger.debug("Gripper opened to position %d", position)

    def close_gripper(self, position: int = 5000, effort: int = 800) -> None:
        """Close the gripper to *position* with *effort* (0-1000)."""
        if self._piper is None:
            logger.error("Arm not connected — cannot close gripper")
            return
        self._piper.GripperCtrl(position, effort)
        logger.debug("Gripper closed to position %d with effort %d", position, effort)

    # ------------------------------------------------------------------
    # Safety
    # ------------------------------------------------------------------

    def emergency_stop(self) -> None:
        """Immediately halt all motion and set the stop event."""
        self._stop_event.set()
        if self._piper is not None and self._connected:
            try:
                self._piper.MotionCtrl_2(0x00, 0x00, 0)
            except Exception:
                logger.exception("Error sending stop command to arm")
        logger.critical("Emergency stop executed")

    def reset_stop(self) -> None:
        """Clear the stop event so the arm can accept new commands after an e-stop."""
        self._stop_event.clear()
        logger.info("Stop event cleared — arm ready for new commands")

    # ------------------------------------------------------------------
    # Status queries
    # ------------------------------------------------------------------

    def get_joint_positions(self) -> list[float]:
        """Return the current six joint positions in 0.001-degree units.

        Returns [0.0] * 6 if the arm is not connected or reading fails.
        """
        if self._piper is None:
            return [0.0] * 6
        try:
            msgs = self._piper.GetArmJointMsgs()
            return [float(v) for v in msgs.joint_state.position[:6]]
        except Exception:
            logger.exception("Failed to read joint positions")
            return [0.0] * 6

    def is_moving(self) -> bool:
        """Return True if a movement thread is currently running."""
        return self._movement_thread is not None and self._movement_thread.is_alive()

    def wait_for_completion(self, timeout: float = 30.0) -> bool:
        """Block until the current movement thread finishes or *timeout* elapses.

        Returns True if the thread completed normally, False on timeout.
        """
        if self._movement_thread is None:
            return True
        self._movement_thread.join(timeout=timeout)
        completed = not self._movement_thread.is_alive()
        if not completed:
            logger.warning("wait_for_completion timed out after %.1fs", timeout)
        return completed
