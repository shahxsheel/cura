import json
import logging
import math
from dataclasses import asdict, dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# pyAgxArm exposes joint angles in radians; JointConfig stores them in
# 0.001-degree units (legacy piper_sdk convention, also matches the limits
# enforced by SafetyModule.JOINT_LIMITS).
_UNIT_PER_RAD: float = 180000.0 / math.pi  # radians -> 0.001-deg units


@dataclass
class JointConfig:
    """Six joint angles for the AgileX Piper arm, all in 0.001-degree units."""

    j1: float = 0.0
    j2: float = 0.0
    j3: float = 0.0
    j4: float = 0.0
    j5: float = 0.0
    j6: float = 0.0

    def as_list(self) -> list[float]:
        """Return joint values as an ordered list [j1 … j6]."""
        return [self.j1, self.j2, self.j3, self.j4, self.j5, self.j6]


WAYPOINTS: dict[str, JointConfig] = {
    "home": JointConfig(
        j1=0.0,
        j2=0.0,
        j3=0.0,
        j4=0.0,
        j5=0.0,
        j6=0.0,
    ),
    "pre_grasp": JointConfig(
        j1=0.0,
        j2=0.0,
        j3=0.0,
        j4=0.0,
        j5=0.0,
        j6=0.0,
    ),
    "grasp": JointConfig(
        j1=0.0,
        j2=0.0,
        j3=0.0,
        j4=0.0,
        j5=0.0,
        j6=0.0,
    ),
    "lift": JointConfig(
        j1=0.0,
        j2=0.0,
        j3=0.0,
        j4=0.0,
        j5=0.0,
        j6=0.0,
    ),
    "pre_deliver": JointConfig(
        j1=0.0,
        j2=0.0,
        j3=0.0,
        j4=0.0,
        j5=0.0,
        j6=0.0,
    ),
    "deliver": JointConfig(
        j1=0.0,
        j2=0.0,
        j3=0.0,
        j4=0.0,
        j5=0.0,
        j6=0.0,
    ),
}

PICKUP_SEQUENCE: list[str] = [
    "home",
    "pre_grasp",
    "grasp",
    "lift",
    "pre_deliver",
    "deliver",
]

RETURN_SEQUENCE: list[str] = list(reversed(PICKUP_SEQUENCE))


def load_waypoints(path: str | Path) -> dict[str, JointConfig]:
    """Load waypoints from *path* and merge them into WAYPOINTS.

    Unknown waypoint names in the file are ignored.  If the file does not
    exist the unmodified module-level WAYPOINTS dict is returned.
    """
    p = Path(path)
    if not p.exists():
        logger.warning("Waypoints file not found at %s — using placeholder zeros", p)
        return WAYPOINTS

    with p.open() as fh:
        data: dict[str, dict[str, float]] = json.load(fh)

    for name, joints in data.items():
        if name in WAYPOINTS:
            WAYPOINTS[name] = JointConfig(**joints)
            logger.debug("Loaded waypoint %r from %s", name, p)
        else:
            logger.warning("Unknown waypoint %r in %s — skipping", name, p)

    return WAYPOINTS


def save_waypoints(waypoints: dict[str, JointConfig], path: str | Path) -> None:
    """Serialise *waypoints* to JSON at *path*, creating parent directories as needed."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    serialisable = {name: asdict(cfg) for name, cfg in waypoints.items()}
    with p.open("w") as fh:
        json.dump(serialisable, fh, indent=2)
    logger.info("Saved %d waypoints to %s", len(waypoints), p)


def teach_and_save(piper: object, waypoint_name: str, path: str | Path) -> None:
    """Read the arm's current joint positions and store them as *waypoint_name*.

    The existing file at *path* is loaded first so that other waypoints are
    preserved. *piper* is a pyAgxArm Driver instance (connect() already
    called). Joint angles are read in radians from pyAgxArm and converted
    into JointConfig's 0.001-deg units.
    """
    msg = piper.get_joint_angles()  # type: ignore[attr-defined]
    if msg is None:
        raise RuntimeError(
            "pyAgxArm returned no joint angle feedback yet — "
            "wait a moment after connect() and try again"
        )
    rads = msg.msg  # list[float] of length 6, radians

    config = JointConfig(
        j1=float(rads[0]) * _UNIT_PER_RAD,
        j2=float(rads[1]) * _UNIT_PER_RAD,
        j3=float(rads[2]) * _UNIT_PER_RAD,
        j4=float(rads[3]) * _UNIT_PER_RAD,
        j5=float(rads[4]) * _UNIT_PER_RAD,
        j6=float(rads[5]) * _UNIT_PER_RAD,
    )

    existing = load_waypoints(path)
    existing[waypoint_name] = config
    save_waypoints(existing, path)

    logger.info(
        "Taught waypoint %r: j1=%.1f j2=%.1f j3=%.1f j4=%.1f j5=%.1f j6=%.1f",
        waypoint_name,
        config.j1,
        config.j2,
        config.j3,
        config.j4,
        config.j5,
        config.j6,
    )
