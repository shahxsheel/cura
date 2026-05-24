import logging
import math
import threading
import time

from cura.arm.safety import SafetyModule
from cura.arm.trajectories import JointConfig, WAYPOINTS

logger = logging.getLogger(__name__)

# JointConfig stores joint values in 0.001-degree units (legacy piper_sdk
# convention preserved so safety.JOINT_LIMITS and the orchestrator keep
# working without modification). pyAgxArm expects/returns radians, so we
# convert at the controller boundary.
#
# 1 degree = 1000 * 0.001-deg units = (pi/180) rad
# Therefore 1 unit (0.001 deg) = (pi/180000) rad
_UNIT_PER_RAD: float = 180000.0 / math.pi   # radians  -> 0.001-deg units
_RAD_PER_UNIT: float = math.pi / 180000.0   # 0.001-deg units -> radians

_POSITION_TOLERANCE: float = 500.0    # 0.001-deg units (~0.5 degrees)
_REACH_TIMEOUT: float = 10.0          # seconds to wait for a waypoint to be reached
_REACH_POLL_INTERVAL: float = 0.05    # seconds between position polls
_ENABLE_TIMEOUT: float = 5.0          # seconds to spend polling robot.enable()

# Gripper unit translation: the orchestrator passes "piper-style" integer
# units (position is 0.001 mm, max 70000 = 70 mm = 0.07 m; effort is 0..1000
# mapping onto pyAgxArm's [0.0, 3.0] N gripping-force range).
_GRIPPER_M_PER_UNIT: float = 1e-6              # 0.001 mm -> m
_GRIPPER_FORCE_PER_UNIT: float = 3.0 / 1000.0  # piper 0..1000 -> N


class ArmController:
    """High-level interface for the AgileX Piper 6-DOF arm.

    Wraps the pyAgxArm driver with safety validation, threaded sequence
    execution, and an emergency stop mechanism. Linux/SocketCAN only.
    """

    def __init__(self, can_port: str = "can0", speed: int = 50) -> None:
        """
        Parameters
        ----------
        can_port:
            SocketCAN device name (e.g. "can0"). The interface must already
            be brought up with `ip link set can0 up type can bitrate 1000000`.
        speed:
            Motion speed percentage passed to set_speed_percent, 0-100.
        """
        self._can_port = can_port
        self._speed = speed
        self._safety = SafetyModule()
        # _piper holds the pyAgxArm Driver instance. The attribute name is
        # kept for compatibility with existing tests and the trajectory
        # teach helper.
        self._piper: object | None = None
        self._gripper: object | None = None
        self._stop_event = threading.Event()
        self._movement_thread: threading.Thread | None = None
        self._connected = False

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        """Initialise, enable, and zero the arm over SocketCAN.

        Returns True on success, False if an exception occurs.
        """
        try:
            from pyAgxArm import (
                create_agx_arm_config,
                AgxArmFactory,
                ArmModel,
                PiperFW,
            )

            cfg = create_agx_arm_config(
                robot=ArmModel.PIPER,
                firmeware_version=PiperFW.DEFAULT,
                interface="socketcan",
                channel=self._can_port,
            )
            robot = AgxArmFactory.create_arm(cfg)
            self._piper = robot
            # init_effector must run *before* connect() so the reader thread
            # picks up gripper feedback frames as well.
            try:
                self._gripper = robot.init_effector(
                    robot.OPTIONS.EFFECTOR.AGX_GRIPPER
                )
            except Exception:
                logger.exception("Failed to init gripper effector — gripper calls will be no-ops")
                self._gripper = None

            robot.connect()
            time.sleep(0.5)  # let pyAgxArm's reader thread spin up

            # Enable all joints. enable() can return False for a few cycles
            # before motors actually power on, so poll briefly.
            enabled = False
            deadline = time.monotonic() + _ENABLE_TIMEOUT
            while time.monotonic() < deadline:
                if robot.enable():
                    enabled = True
                    break
                time.sleep(0.05)
            if not enabled:
                logger.warning("Arm enable() did not succeed within %.1fs", _ENABLE_TIMEOUT)

            robot.set_speed_percent(self._speed)
            time.sleep(0.2)
            self._connected = True
            logger.info("Connected to Piper arm on %s", self._can_port)

            # Drive to all-zeros so the motors lock in a known reference
            # position before any real motion commands.
            self.go_to_zero()
            return True
        except Exception as e:
            logger.error("Failed to connect to arm: %s", e)
            self._connected = False
            return False

    def go_to_zero(self, timeout: float = 15.0) -> bool:
        """Send all six joints to 0 and block until reached (or timeout)."""
        if self._piper is None:
            logger.error("Arm not connected — cannot zero")
            return False
        logger.info("Zeroing arm (all joints → 0)…")
        self._piper.move_j([0.0] * 6)  # type: ignore[attr-defined]
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._stop_event.is_set():
                return False
            current = self.get_joint_positions()
            if all(abs(v) <= _POSITION_TOLERANCE for v in current):
                logger.info("Arm at zero position")
                return True
            time.sleep(_REACH_POLL_INTERVAL)
        logger.warning("go_to_zero timed out after %.1fs", timeout)
        return False

    def disconnect(self) -> None:
        """Emergency-stop the arm and mark it as disconnected."""
        self.emergency_stop()
        if self._piper is not None and self._connected:
            try:
                self._piper.disconnect()  # type: ignore[attr-defined]
            except Exception:
                logger.exception("Error during pyAgxArm disconnect()")
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

        # Convert from JointConfig's 0.001-deg units to radians for pyAgxArm.
        joints_rad = [v * _RAD_PER_UNIT for v in joints]
        self._piper.move_j(joints_rad)  # type: ignore[attr-defined]
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
        wait_for_completion() to block until done. If the stop event is set
        (emergency stop) the sequence is aborted before any motion command.
        """
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
        """Open the gripper to *position* (0.001 mm units, max 70000 = 70 mm)."""
        if self._gripper is None:
            logger.error("Gripper not initialised — cannot open")
            return
        width_m = position * _GRIPPER_M_PER_UNIT
        # 500 in legacy piper units; map to a moderate force ≈1.5 N.
        force_n = 500 * _GRIPPER_FORCE_PER_UNIT
        try:
            self._gripper.move_gripper_m(value=width_m, force=force_n)  # type: ignore[attr-defined]
        except Exception:
            logger.exception("Error opening gripper")
            return
        logger.debug("Gripper opened to %.4f m (%d units)", width_m, position)

    def close_gripper(self, position: int = 5000, effort: int = 800) -> None:
        """Close the gripper to *position* (0.001 mm) with *effort* (0-1000)."""
        if self._gripper is None:
            logger.error("Gripper not initialised — cannot close")
            return
        width_m = position * _GRIPPER_M_PER_UNIT
        # Clamp effort into pyAgxArm's [0.0, 3.0] N gripping-force range.
        force_n = max(0.0, min(3.0, effort * _GRIPPER_FORCE_PER_UNIT))
        try:
            self._gripper.move_gripper_m(value=width_m, force=force_n)  # type: ignore[attr-defined]
        except Exception:
            logger.exception("Error closing gripper")
            return
        logger.debug("Gripper closed to %.4f m (%d units) with %.2f N", width_m, position, force_n)

    # ------------------------------------------------------------------
    # Safety
    # ------------------------------------------------------------------

    def emergency_stop(self) -> None:
        """Immediately halt all motion and set the stop event."""
        self._stop_event.set()
        if self._piper is not None and self._connected:
            try:
                self._piper.electronic_emergency_stop()  # type: ignore[attr-defined]
            except Exception:
                logger.exception("Error sending stop command to arm")
        logger.critical("Emergency stop executed")

    def reset_stop(self) -> None:
        """Clear the stop event and resume from emergency stop."""
        self._stop_event.clear()
        if self._piper is not None and self._connected:
            try:
                # reset() clears the e-stop state and powers off; re-enable
                # and restore the configured speed so subsequent move_j calls
                # work straight away.
                self._piper.reset()  # type: ignore[attr-defined]
                time.sleep(0.1)
                self._piper.enable()  # type: ignore[attr-defined]
                self._piper.set_speed_percent(self._speed)  # type: ignore[attr-defined]
            except Exception:
                logger.exception("Error resuming arm from e-stop")
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
            msg = self._piper.get_joint_angles()  # type: ignore[attr-defined]
            if msg is None:
                return [0.0] * 6
            rads = msg.msg  # list[float] of length 6, radians
            return [float(r) * _UNIT_PER_RAD for r in rads]
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
