"""
Configuration settings for the Cura robotic feeding assistant.

Settings are loaded from environment variables with CURA_ prefix,
falling back to sensible defaults. Import the module-level singleton:

    from cura.config.settings import settings
"""
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class Settings:
    """All runtime configuration for Cura.

    Attributes are set to defaults and may be overridden via environment
    variables (e.g. CURA_CAN_PORT overrides can_port).
    """

    # Hardware
    can_port: str = "can0"
    """SocketCAN interface name (e.g. 'can0')."""

    arm_speed: int = 50
    """Arm movement speed in piper_sdk units (0–100)."""

    # Files
    waypoints_file: Path = field(default_factory=lambda: Path("waypoints.json"))
    """Path to the JSON file containing taught waypoints."""

    # Server
    server_host: str = "0.0.0.0"
    """FastAPI server bind host."""

    server_port: int = 8000
    """FastAPI server bind port."""

    # Safety
    estop_timeout_seconds: float = 5.0
    """Watchdog timeout in seconds; arm stops if no heartbeat within this window."""

    # Arm motion tolerances
    waypoint_reach_tolerance: float = 500.0
    """Joint position tolerance in 0.001-degree units used to decide when the arm
    has 'arrived' at a waypoint."""

    waypoint_timeout_seconds: float = 10.0
    """Maximum time (seconds) to wait for the arm to reach a waypoint before
    raising a timeout error."""

    # Gripper
    gripper_open_position: int = 70_000
    """Gripper fully-open position in 0.001 mm units."""

    gripper_close_position: int = 5_000
    """Gripper closed position (grasping bottle) in 0.001 mm units."""

    gripper_effort: int = 800
    """Gripper closing force (0–1000 piper_sdk units)."""


def load_settings() -> Settings:
    """Read environment variables and return a populated :class:`Settings` instance."""
    defaults = Settings()

    def _get(env_key: str, default: object, cast: type) -> object:
        raw = os.environ.get(env_key)
        if raw is None:
            return default
        try:
            return cast(raw)
        except (ValueError, TypeError) as exc:
            logger.warning(
                "Invalid value for %s=%r (%s); using default %r",
                env_key, raw, exc, default,
            )
            return default

    return Settings(
        can_port=str(os.environ.get("CURA_CAN_PORT", defaults.can_port)),
        arm_speed=int(_get("CURA_ARM_SPEED", defaults.arm_speed, int)),
        waypoints_file=Path(
            os.environ.get("CURA_WAYPOINTS_FILE", str(defaults.waypoints_file))
        ),
        server_host=str(os.environ.get("CURA_SERVER_HOST", defaults.server_host)),
        server_port=int(_get("CURA_SERVER_PORT", defaults.server_port, int)),
        estop_timeout_seconds=float(
            _get("CURA_ESTOP_TIMEOUT_SECONDS", defaults.estop_timeout_seconds, float)
        ),
        waypoint_reach_tolerance=float(
            _get("CURA_WAYPOINT_REACH_TOLERANCE", defaults.waypoint_reach_tolerance, float)
        ),
        waypoint_timeout_seconds=float(
            _get("CURA_WAYPOINT_TIMEOUT_SECONDS", defaults.waypoint_timeout_seconds, float)
        ),
        gripper_open_position=int(
            _get("CURA_GRIPPER_OPEN_POSITION", defaults.gripper_open_position, int)
        ),
        gripper_close_position=int(
            _get("CURA_GRIPPER_CLOSE_POSITION", defaults.gripper_close_position, int)
        ),
        gripper_effort=int(_get("CURA_GRIPPER_EFFORT", defaults.gripper_effort, int)),
    )


# Module-level singleton — import with:
#   from cura.config.settings import settings
settings: Settings = load_settings()
