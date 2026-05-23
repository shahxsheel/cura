import logging
from dataclasses import dataclass
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# MediaPipe FaceMesh landmark indices for the mouth region.
_UPPER_LIP_CENTER = 13
_LOWER_LIP_CENTER = 14


@dataclass
class MouthPosition:
    """Detected position of the patient's mouth in a single frame."""

    center_px: tuple[int, int]
    position_3d: tuple[float, float, float] | None
    confidence: float


class MouthTracker:
    """MediaPipe FaceMesh-based mouth position tracker.

    V1: position_3d is always None.  World-coordinate mapping is V2 work
    once the PatientCamera is calibrated relative to the arm base frame.
    """

    def __init__(self) -> None:
        self._face_mesh: Any = None
        self._mp_face_mesh: Any = None

    def load(self) -> bool:
        """Import MediaPipe and initialise the FaceMesh solution.

        Returns:
            True if MediaPipe loaded successfully, False if it is not installed
            or initialisation fails.
        """
        try:
            import mediapipe as mp  # type: ignore[import]

            self._mp_face_mesh = mp.solutions.face_mesh
            self._face_mesh = self._mp_face_mesh.FaceMesh(
                static_image_mode=False,
                max_num_faces=1,
                refine_landmarks=True,
                min_detection_confidence=0.5,
                min_tracking_confidence=0.5,
            )
            logger.info("MouthTracker: MediaPipe FaceMesh initialised")
            return True
        except Exception:
            logger.exception("MouthTracker: failed to initialise MediaPipe FaceMesh")
            self._face_mesh = None
            self._mp_face_mesh = None
            return False

    def track(self, frame: np.ndarray) -> MouthPosition | None:
        """Detect the mouth position in a BGR frame.

        Landmark 13 (upper lip center) and 14 (lower lip center) are averaged
        to produce the mouth center in pixel coordinates.

        Args:
            frame: BGR image as a numpy array.

        Returns:
            MouthPosition with pixel coordinates, or None if no face is
            detected or the tracker has not been loaded.
        """
        if self._face_mesh is None:
            logger.warning("MouthTracker: track() called but FaceMesh is not loaded")
            return None

        try:
            import cv2

            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            results = self._face_mesh.process(rgb)
        except Exception:
            logger.exception("MouthTracker: inference failed")
            return None

        if not results.multi_face_landmarks:
            return None

        landmarks = results.multi_face_landmarks[0].landmark
        height, width = frame.shape[:2]

        upper = landmarks[_UPPER_LIP_CENTER]
        lower = landmarks[_LOWER_LIP_CENTER]

        cx = int((upper.x + lower.x) / 2.0 * width)
        cy = int((upper.y + lower.y) / 2.0 * height)

        # Visibility is not always present on all landmark types; fall back to 1.0.
        upper_vis: float = getattr(upper, "visibility", 1.0)
        lower_vis: float = getattr(lower, "visibility", 1.0)
        confidence = float((upper_vis + lower_vis) / 2.0)

        return MouthPosition(
            center_px=(cx, cy),
            position_3d=None,
            confidence=confidence,
        )

    def close(self) -> None:
        """Release MediaPipe resources."""
        if self._face_mesh is not None:
            self._face_mesh.close()
            self._face_mesh = None
            logger.info("MouthTracker: FaceMesh released")
