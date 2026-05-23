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
    V2: Orbbec RGB-D camera provides depth frames; call detect() with
        depth_frame to get real 3-D coordinates via set_intrinsics().
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

        # Orbbec G330 default intrinsics — override with set_intrinsics()
        self._fx: float = 605.0  # focal length x (pixels)
        self._fy: float = 605.0  # focal length y (pixels)
        self._cx: float = 320.0  # principal point x (pixels)
        self._cy: float = 240.0  # principal point y (pixels)

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

    def set_intrinsics(self, fx: float, fy: float, cx: float, cy: float) -> None:
        """Store camera intrinsics obtained from the Orbbec pipeline.

        Call this after the pipeline starts and provides calibration data so
        that depth-to-3D back-projection uses accurate values instead of the
        built-in Orbbec G330 defaults.

        Args:
            fx: Focal length along the x-axis in pixels.
            fy: Focal length along the y-axis in pixels.
            cx: Principal point x-coordinate in pixels.
            cy: Principal point y-coordinate in pixels.
        """
        self._fx = fx
        self._fy = fy
        self._cx = cx
        self._cy = cy
        logger.info(
            "ObjectDetector: intrinsics updated fx=%.2f fy=%.2f cx=%.2f cy=%.2f",
            fx,
            fy,
            cx,
            cy,
        )

    def detect(
        self,
        frame: np.ndarray,
        depth_frame: np.ndarray | None = None,
    ) -> list[DetectedObject]:
        """Run inference on a single BGR frame.

        Args:
            frame: BGR image as a numpy array (H x W x 3, uint8).
            depth_frame: Optional float32 array of depth values in mm with the
                same spatial dimensions as *frame* (H x W).  When provided,
                each detected object's ``position_3d`` is populated using the
                stored camera intrinsics and the depth reading at the detection
                centre.  Passing ``None`` preserves the original behaviour
                (``position_3d`` is ``None`` for every detection).

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

                position_3d: tuple[float, float, float] | None = None
                if depth_frame is not None:
                    position_3d = self._depth_to_3d(cx, cy, depth_frame)

                detections.append(
                    DetectedObject(
                        label=label,
                        confidence=conf,
                        bbox=(x1, y1, x2, y2),
                        center_px=(cx, cy),
                        position_3d=position_3d,
                    )
                )

        return detections

    def _depth_to_3d(
        self,
        cx_px: int,
        cy_px: int,
        depth_frame: np.ndarray,
    ) -> tuple[float, float, float] | None:
        """Back-project a pixel centre to 3-D camera-space coordinates.

        Samples a small patch around ``(cx_px, cy_px)`` in the depth frame and
        takes the median of valid readings to reduce noise.  Returns ``None``
        when no valid depth samples exist in the patch.

        Args:
            cx_px: Pixel x-coordinate of the object centre.
            cy_px: Pixel y-coordinate of the object centre.
            depth_frame: Float32 array of depth values in mm (H x W).

        Returns:
            ``(x_mm, y_mm, z_mm)`` in camera space, or ``None`` if depth is
            unavailable or out of the valid range.
        """
        patch_size = 5
        y1 = max(0, cy_px - patch_size)
        y2 = min(depth_frame.shape[0], cy_px + patch_size)
        x1 = max(0, cx_px - patch_size)
        x2 = min(depth_frame.shape[1], cx_px + patch_size)
        patch = depth_frame[y1:y2, x1:x2]
        valid = patch[(patch > 50) & (patch < 3000)]  # filter noise
        if len(valid) == 0:
            logger.debug(
                "ObjectDetector: no valid depth in patch around (%d, %d)", cx_px, cy_px
            )
            return None
        z_mm = float(np.median(valid))
        x_mm = (cx_px - self._cx) * z_mm / self._fx
        y_mm = (cy_px - self._cy) * z_mm / self._fy
        return (x_mm, y_mm, z_mm)
