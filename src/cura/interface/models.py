"""
Pydantic v2 models for all Cura API messages.

These are shared between the FastAPI server, the T5AI board client, and the
web dashboard WebSocket protocol.
"""
import logging
from enum import Enum

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class SystemState(str, Enum):
    """All valid orchestrator states, usable as JSON string values."""

    IDLE = "IDLE"
    APPROACHING = "APPROACHING"
    GRASPING = "GRASPING"
    LIFTING = "LIFTING"
    DELIVERING = "DELIVERING"
    DRINKING = "DRINKING"
    RETRACTING = "RETRACTING"
    RELEASING = "RELEASING"
    ERROR = "ERROR"


# Human-readable display labels for each state, suitable for the T5AI screen.
STATE_LABELS: dict[SystemState, str] = {
    SystemState.IDLE: "Ready",
    SystemState.APPROACHING: "Moving to Bottle",
    SystemState.GRASPING: "Picking Up",
    SystemState.LIFTING: "Lifting",
    SystemState.DELIVERING: "Delivering",
    SystemState.DRINKING: "Enjoy!",
    SystemState.RETRACTING: "Returning Bottle",
    SystemState.RELEASING: "Done",
    SystemState.ERROR: "Error — Please Help",
}


class SystemStatus(BaseModel):
    """Snapshot of the current system state, returned by GET /status and broadcast over WebSocket."""

    state: SystemState
    state_label: str
    message: str = ""
    is_moving: bool = False
    estop_active: bool = False
    timestamp: float


class PatientCommand(BaseModel):
    """A command posted by the T5AI board, dashboard, or keyboard trigger."""

    action: str
    """One of: 'start_feeding', 'done_drinking', 'emergency_stop', 'reset'."""

    source: str = "t5ai"
    """One of: 't5ai', 'dashboard', 'keyboard'."""


class MealProgress(BaseModel):
    """Tracks progress through a meal session."""

    phase: str = "water"
    bites_given: int = 0
    drinks_given: int = 0
    current_food_item: str | None = None
    utensil_in_use: str | None = None
