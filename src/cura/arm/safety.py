import logging
import threading
from collections.abc import Callable

logger = logging.getLogger(__name__)


class SafetyModule:
    """Validates arm commands and manages emergency stop callbacks."""

    JOINT_LIMITS: dict[int, tuple[float, float]] = {
        1: (-150000.0, 150000.0),
        2: (-90000.0, 90000.0),
        3: (-90000.0, 90000.0),
        4: (-150000.0, 150000.0),
        5: (-150000.0, 150000.0),
        6: (-150000.0, 150000.0),
    }

    MAX_SPEED: int = 80

    WORKSPACE_BOUNDS: dict[str, float] = {
        "x_min": -300.0,
        "x_max": 600.0,
        "y_min": -300.0,
        "y_max": 300.0,
        "z_min": 0.0,
        "z_max": 600.0,
    }

    def __init__(self) -> None:
        """Initialise with an empty callback registry."""
        self._estop_callbacks: list[Callable[[], None]] = []

    def validate_joints(self, joints: list[float]) -> bool:
        """Return True if all six joint values fall within JOINT_LIMITS."""
        if len(joints) != 6:
            logger.warning("Expected 6 joint values, got %d", len(joints))
            return False
        for idx, value in enumerate(joints, start=1):
            lo, hi = self.JOINT_LIMITS[idx]
            if not (lo <= value <= hi):
                logger.warning(
                    "Joint %d value %.1f is outside limits [%.1f, %.1f]",
                    idx,
                    value,
                    lo,
                    hi,
                )
                return False
        return True

    def validate_speed(self, speed: int) -> bool:
        """Return True if speed does not exceed MAX_SPEED."""
        if speed > self.MAX_SPEED:
            logger.warning(
                "Requested speed %d exceeds MAX_SPEED %d", speed, self.MAX_SPEED
            )
            return False
        return True

    def validate_position(self, x: float, y: float, z: float) -> bool:
        """Return True if the Cartesian position lies within WORKSPACE_BOUNDS."""
        b = self.WORKSPACE_BOUNDS
        if not (b["x_min"] <= x <= b["x_max"]):
            logger.warning("X position %.1f out of bounds [%.1f, %.1f]", x, b["x_min"], b["x_max"])
            return False
        if not (b["y_min"] <= y <= b["y_max"]):
            logger.warning("Y position %.1f out of bounds [%.1f, %.1f]", y, b["y_min"], b["y_max"])
            return False
        if not (b["z_min"] <= z <= b["z_max"]):
            logger.warning("Z position %.1f out of bounds [%.1f, %.1f]", z, b["z_min"], b["z_max"])
            return False
        return True

    def register_estop_callback(self, callback: Callable[[], None]) -> None:
        """Register a function to be called when emergency stop is triggered."""
        self._estop_callbacks.append(callback)

    def trigger_estop(self) -> None:
        """Fire all registered emergency stop callbacks and log a CRITICAL event."""
        logger.critical("Emergency stop triggered — notifying %d callbacks", len(self._estop_callbacks))
        for cb in self._estop_callbacks:
            try:
                cb()
            except Exception:
                logger.exception("Error in e-stop callback %r", cb)


class Watchdog:
    """Fires a callback if not pet()-ted within timeout_seconds."""

    def __init__(self, timeout_seconds: float, callback: Callable[[], None]) -> None:
        """
        Parameters
        ----------
        timeout_seconds:
            How long to wait between pets before firing the callback.
        callback:
            Called on the timer thread when the watchdog expires.
        """
        self._timeout = timeout_seconds
        self._callback = callback
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

    def _fire(self) -> None:
        logger.warning("Watchdog expired after %.1fs — invoking callback", self._timeout)
        self._callback()

    def start(self) -> None:
        """Start (or restart) the watchdog timer."""
        with self._lock:
            self._cancel_timer()
            self._timer = threading.Timer(self._timeout, self._fire)
            self._timer.daemon = True
            self._timer.start()

    def pet(self) -> None:
        """Reset the countdown — must be called before timeout to suppress the callback."""
        self.start()

    def stop(self) -> None:
        """Cancel the watchdog without firing the callback."""
        with self._lock:
            self._cancel_timer()

    def _cancel_timer(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
