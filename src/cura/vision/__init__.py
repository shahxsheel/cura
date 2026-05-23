"""Vision subsystem for Cura.

V1: ArmCamera and PatientCamera exist as standalone testable modules and are
    NOT wired into the main loop.  ObjectDetector and MouthTracker are similarly
    decoupled so they can be exercised independently.
V2: MouthTracker becomes active; world-coordinate mapping is completed.
V3: ObjectDetector extended to food and utensil detection.
"""

from cura.vision.camera import ArmCamera, PatientCamera
from cura.vision.detector import DetectedObject, ObjectDetector
from cura.vision.mouth_tracker import MouthPosition, MouthTracker

__all__ = [
    "ArmCamera",
    "PatientCamera",
    "ObjectDetector",
    "MouthTracker",
    "DetectedObject",
    "MouthPosition",
]
