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

    # V1 — water delivery
    IDLE = "IDLE"
    DETECTING = "DETECTING"
    APPROACHING = "APPROACHING"
    GRASPING = "GRASPING"
    LIFTING = "LIFTING"
    DELIVERING = "DELIVERING"
    DRINKING = "DRINKING"
    RETRACTING = "RETRACTING"
    RELEASING = "RELEASING"
    ERROR = "ERROR"

    # V3 — meal / solid food delivery
    SCANNING_PLATE = "SCANNING_PLATE"
    SELECTING_UTENSIL = "SELECTING_UTENSIL"
    PICKING_UTENSIL = "PICKING_UTENSIL"
    SCOOPING = "SCOOPING"
    FEEDING = "FEEDING"
    WIPING = "WIPING"
    RETURNING_UTENSIL = "RETURNING_UTENSIL"


# Human-readable display labels for each state, suitable for the T5AI screen.
STATE_LABELS: dict[SystemState, str] = {
    SystemState.IDLE: "Ready",
    SystemState.DETECTING: "Detecting Bottle",
    SystemState.APPROACHING: "Moving to Bottle",
    SystemState.GRASPING: "Picking Up",
    SystemState.LIFTING: "Lifting",
    SystemState.DELIVERING: "Delivering",
    SystemState.DRINKING: "Enjoy!",
    SystemState.RETRACTING: "Returning Bottle",
    SystemState.RELEASING: "Done",
    SystemState.ERROR: "Error — Please Help",
    # V3 states
    SystemState.SCANNING_PLATE: "Scanning Plate",
    SystemState.SELECTING_UTENSIL: "Selecting Utensil",
    SystemState.PICKING_UTENSIL: "Picking Up Utensil",
    SystemState.SCOOPING: "Scooping Food",
    SystemState.FEEDING: "Delivering Food",
    SystemState.WIPING: "Wiping Utensil",
    SystemState.RETURNING_UTENSIL: "Returning Utensil",
}


class SystemStatus(BaseModel):
    """Snapshot of the current system state, returned by GET /status and broadcast over WebSocket."""

    state: SystemState
    """Current orchestrator state."""

    state_label: str
    """Human-readable label for display on the T5AI screen (e.g. 'Ready', 'Enjoy!')."""

    message: str = ""
    """Optional free-form detail message (e.g. error description)."""

    is_moving: bool = False
    """True while the arm is in motion."""

    estop_active: bool = False
    """True if the emergency-stop has been triggered."""

    timestamp: float
    """Unix timestamp of when this status was last updated."""


class PatientCommand(BaseModel):
    """A command posted by the T5AI board, dashboard, or keyboard trigger."""

    action: str
    """Requested action. One of: 'start_feeding', 'done_drinking', 'emergency_stop', 'reset'."""

    source: str = "t5ai"
    """Origin of the command. One of: 't5ai', 'dashboard', 'keyboard'."""


class MealProgress(BaseModel):
    """Tracks progress through a meal session."""

    phase: str = "water"
    """Current meal phase. One of: 'water', 'food', 'complete'."""

    bites_given: int = 0
    """Number of food bites delivered so far."""

    drinks_given: int = 0
    """Number of drinks/water deliveries completed so far."""

    current_food_item: str | None = None
    """Name of the food item currently being handled, or None."""

    utensil_in_use: str | None = None
    """Identifier of the utensil currently in the gripper, or None."""
