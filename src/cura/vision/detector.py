import logging
from dataclasses import dataclass, field

import numpy as np

logger = logging.getLogger(__name__)

_DEFAULT_LABELS: list[str] = ["bottle", "fork", "spoon", "knife", "cup", "bowl"]


@dataclass
class DetectedObject:
    """A single object detected in a camera frame."""

    label: str
    confidence: float
    bbox: tuple[int, int, int, int]
    center_px: tuple[int, int]
    position_3d: tuple[float, float, float] | None = None


class ObjectDetector:
    """YOLOv8-based object detector for tabletop items.

    V1: position_3d is always None (fixed-position cradle; no calibration).
    V2: Member A will call set_calibration() with a pixel-to-world homography.
    """

    def __init__(
        self,
        model_path: str = "yolov8n.pt",
        confidence_threshold: float = 0.5,
        target_labels: list[str] | None = None,
    ) -> None:
        """
        Args:
            model_path: Path or filename of the YOLOv8 weights file.
            confidence_threshold: Minimum confidence to keep a detection.
            target_labels: Object class names to keep; defaults to common
                           tabletop items if None.
        """
        self._model_path = model_path
        self._confidence_threshold = confidence_threshold
        self._target_labels: list[str] = (
            target_labels if target_labels is not None else list(_DEFAULT_LABELS)
        )
        self._model: object | None = None
        self._calibration: np.ndarray | None = None

    def load(self) -> bool:
        """Load the YOLOv8 model from disk.

        Returns:
            True if the model loaded successfully, False otherwise.
        """
        try:
            from ultralytics import YOLO  # type: ignore[import]

            self._model = YOLO(self._model_path)
            logger.info("ObjectDetector: loaded model %s", self._model_path)
            return True
        except Exception:
            logger.exception(
                "ObjectDetector: failed to load model %s", self._model_path
            )
            self._model = None
            return False

    def detect(self, frame: np.ndarray) -> list[DetectedObject]:
        """Run inference on a single BGR frame.

        Args:
            frame: BGR image as a numpy array.

        Returns:
            List of DetectedObject instances that pass the confidence threshold
            and belong to target_labels.  Returns an empty list if the model
            is not loaded or inference fails.
        """
        if self._model is None:
            logger.warning("ObjectDetector: detect() called but model is not loaded")
            return []

        try:
            results = self._model(frame, verbose=False)  # type: ignore[operator]
        except Exception:
            logger.exception("ObjectDetector: inference failed")
            return []

        detections: list[DetectedObject] = []
        for result in results:
            boxes = result.boxes
            if boxes is None:
                continue
            for box in boxes:
                conf: float = float(box.conf[0])
                if conf < self._confidence_threshold:
                    continue

                cls_id: int = int(box.cls[0])
                label: str = result.names[cls_id]
                if label not in self._target_labels:
                    continue

                x1, y1, x2, y2 = (int(v) for v in box.xyxy[0])
                cx = (x1 + x2) // 2
                cy = (y1 + y2) // 2

                detections.append(
                    DetectedObject(
                        label=label,
                        confidence=conf,
                        bbox=(x1, y1, x2, y2),
                        center_px=(cx, cy),
                        position_3d=self._pixel_to_world(cx, cy),
                    )
                )

        return detections

    def set_calibration(self, matrix: np.ndarray) -> None:
        """Store the 3x3 pixel-to-world homography matrix.

        Args:
            matrix: A 3x3 numpy array mapping pixel coordinates to world
                    coordinates in mm relative to the arm base.
        """
        self._calibration = matrix
        logger.info("ObjectDetector: calibration matrix updated")

    def _pixel_to_world(
        self, cx: int, cy: int
    ) -> tuple[float, float, float] | None:
        """Convert a pixel center to world coordinates using the stored homography.

        Returns None when no calibration matrix has been set (V1 behaviour).
        z is always 0.0 for a flat table surface; depth estimation is V2 work.

        Args:
            cx: Pixel x-coordinate of the object center.
            cy: Pixel y-coordinate of the object center.

        Returns:
            (x, y, z) in mm relative to arm base, or None if not calibrated.
        """
        if self._calibration is None:
            return None

        point = np.array([cx, cy, 1.0], dtype=np.float64)
        world = self._calibration @ point
        if world[2] == 0.0:
            logger.warning(
                "ObjectDetector: degenerate homography (w=0) at pixel (%d, %d)", cx, cy
            )
            return None
        x = world[0] / world[2]
        y = world[1] / world[2]
        return (float(x), float(y), 0.0)
