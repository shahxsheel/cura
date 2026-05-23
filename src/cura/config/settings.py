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
    """CAN bus interface name (e.g. 'can0')."""

    arm_speed: int = 50
    """Arm movement speed in piper_sdk units (0–100)."""

    arm_camera_index: int = 0
    """USB camera index for the wrist/arm camera."""

    patient_camera_url: str = ""
    """MJPEG stream URL for the T5AI patient-facing camera. Empty = not configured."""

    # Vision
    detection_confidence: float = 0.5
    """YOLO object-detection confidence threshold (0.0–1.0)."""

    depth_min_mm: float = 50.0
    """Ignore depth readings closer than this value (mm)."""

    depth_max_mm: float = 3000.0
    """Ignore depth readings farther than this value (mm)."""

    orbbec_color_width: int = 640
    """Width of the Orbbec color stream in pixels."""

    orbbec_color_height: int = 480
    """Height of the Orbbec color stream in pixels."""

    orbbec_depth_width: int = 640
    """Width of the Orbbec depth stream in pixels."""

    orbbec_depth_height: int = 480
    """Height of the Orbbec depth stream in pixels."""

    orbbec_fps: int = 30
    """Target frame rate for both Orbbec color and depth streams."""

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
    """Read environment variables and return a populated :class:`Settings` instance.

    Each field maps to a ``CURA_<UPPER_FIELD_NAME>`` environment variable.
    Values are cast to the appropriate type; invalid values are logged and the
    default is kept.

    Returns:
        A :class:`Settings` instance with any env-var overrides applied.
    """
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
                env_key,
                raw,
                exc,
                default,
            )
            return default

    return Settings(
        can_port=str(
            os.environ.get("CURA_CAN_PORT", defaults.can_port)
        ),
        arm_speed=int(
            _get("CURA_ARM_SPEED", defaults.arm_speed, int)
        ),
        arm_camera_index=int(
            _get("CURA_ARM_CAMERA_INDEX", defaults.arm_camera_index, int)
        ),
        patient_camera_url=str(
            os.environ.get("CURA_PATIENT_CAMERA_URL", defaults.patient_camera_url)
        ),
        detection_confidence=float(
            _get("CURA_DETECTION_CONFIDENCE", defaults.detection_confidence, float)
        ),
        depth_min_mm=float(
            _get("CURA_DEPTH_MIN_MM", defaults.depth_min_mm, float)
        ),
        depth_max_mm=float(
            _get("CURA_DEPTH_MAX_MM", defaults.depth_max_mm, float)
        ),
        orbbec_color_width=int(
            _get("CURA_ORBBEC_COLOR_WIDTH", defaults.orbbec_color_width, int)
        ),
        orbbec_color_height=int(
            _get("CURA_ORBBEC_COLOR_HEIGHT", defaults.orbbec_color_height, int)
        ),
        orbbec_depth_width=int(
            _get("CURA_ORBBEC_DEPTH_WIDTH", defaults.orbbec_depth_width, int)
        ),
        orbbec_depth_height=int(
            _get("CURA_ORBBEC_DEPTH_HEIGHT", defaults.orbbec_depth_height, int)
        ),
        orbbec_fps=int(
            _get("CURA_ORBBEC_FPS", defaults.orbbec_fps, int)
        ),
        waypoints_file=Path(
            os.environ.get("CURA_WAYPOINTS_FILE", str(defaults.waypoints_file))
        ),
        server_host=str(
            os.environ.get("CURA_SERVER_HOST", defaults.server_host)
        ),
        server_port=int(
            _get("CURA_SERVER_PORT", defaults.server_port, int)
        ),
        estop_timeout_seconds=float(
            _get("CURA_ESTOP_TIMEOUT_SECONDS", defaults.estop_timeout_seconds, float)
        ),
        waypoint_reach_tolerance=float(
            _get(
                "CURA_WAYPOINT_REACH_TOLERANCE",
                defaults.waypoint_reach_tolerance,
                float,
            )
        ),
        waypoint_timeout_seconds=float(
            _get(
                "CURA_WAYPOINT_TIMEOUT_SECONDS",
                defaults.waypoint_timeout_seconds,
                float,
            )
        ),
        gripper_open_position=int(
            _get(
                "CURA_GRIPPER_OPEN_POSITION",
                defaults.gripper_open_position,
                int,
            )
        ),
        gripper_close_position=int(
            _get(
                "CURA_GRIPPER_CLOSE_POSITION",
                defaults.gripper_close_position,
                int,
            )
        ),
        gripper_effort=int(
            _get("CURA_GRIPPER_EFFORT", defaults.gripper_effort, int)
        ),
    )


# Module-level singleton — import with:
#   from cura.config.settings import settings
settings: Settings = load_settings()
