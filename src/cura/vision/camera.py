import logging
import urllib.request

import cv2
import numpy as np

logger = logging.getLogger(__name__)


class ArmCamera:
    """USB camera mounted on the robot arm, facing the table."""

    def __init__(self, index: int = 0) -> None:
        """
        Args:
            index: OpenCV device index for the USB camera.
        """
        self._index = index
        self._cap: cv2.VideoCapture | None = None

    def open(self) -> bool:
        """Open the camera device.

        Returns:
            True if the camera opened successfully, False otherwise.
        """
        try:
            self._cap = cv2.VideoCapture(self._index)
            if not self._cap.isOpened():
                logger.error("ArmCamera: failed to open device index %d", self._index)
                self._cap = None
                return False
            logger.info("ArmCamera: opened device index %d", self._index)
            return True
        except Exception:
            logger.exception("ArmCamera: exception opening device index %d", self._index)
            self._cap = None
            return False

    def close(self) -> None:
        """Release the camera device."""
        if self._cap is not None:
            self._cap.release()
            self._cap = None
            logger.info("ArmCamera: closed device index %d", self._index)

    def get_frame(self) -> np.ndarray | None:
        """Read the latest frame from the camera.

        Returns:
            BGR frame as a numpy array, or None if the read failed.
        """
        if self._cap is None or not self._cap.isOpened():
            logger.warning("ArmCamera: get_frame called but camera is not open")
            return None
        ret, frame = self._cap.read()
        if not ret:
            logger.warning("ArmCamera: frame read failed")
            return None
        return frame

    def is_open(self) -> bool:
        """Return True if the camera device is currently open."""
        return self._cap is not None and self._cap.isOpened()

    def __enter__(self) -> "ArmCamera":
        self.open()
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        self.close()


class PatientCamera:
    """MJPEG camera stream from the T5AI companion device, facing the patient."""

    def __init__(self, stream_url: str) -> None:
        """
        Args:
            stream_url: MJPEG stream URL served by T5AI, e.g.
                        "http://192.168.1.x:8080/stream".
        """
        self._stream_url = stream_url
        self._cap: cv2.VideoCapture | None = None

    def open(self) -> bool:
        """Open the MJPEG stream.

        Tries cv2.VideoCapture first; falls back to a lightweight URL probe
        via urllib so we can surface a clear error when the host is unreachable.

        Returns:
            True if the stream opened successfully, False otherwise.
        """
        try:
            # Quick reachability check before handing off to OpenCV so we get a
            # meaningful error message rather than a silent timeout.
            urllib.request.urlopen(self._stream_url, timeout=3)
        except Exception as exc:
            logger.error(
                "PatientCamera: stream URL %s unreachable: %s",
                self._stream_url,
                exc,
            )
            return False

        try:
            self._cap = cv2.VideoCapture(self._stream_url)
            if not self._cap.isOpened():
                logger.error(
                    "PatientCamera: cv2.VideoCapture could not open %s",
                    self._stream_url,
                )
                self._cap = None
                return False
            logger.info("PatientCamera: opened stream %s", self._stream_url)
            return True
        except Exception:
            logger.exception(
                "PatientCamera: exception opening stream %s", self._stream_url
            )
            self._cap = None
            return False

    def close(self) -> None:
        """Release the MJPEG stream."""
        if self._cap is not None:
            self._cap.release()
            self._cap = None
            logger.info("PatientCamera: closed stream %s", self._stream_url)

    def get_frame(self) -> np.ndarray | None:
        """Grab the latest frame from the MJPEG stream.

        Returns:
            BGR frame as a numpy array, or None if the stream is not connected
            or the read failed.
        """
        if self._cap is None or not self._cap.isOpened():
            logger.warning(
                "PatientCamera: get_frame called but stream is not open"
            )
            return None
        ret, frame = self._cap.read()
        if not ret:
            logger.warning("PatientCamera: frame read failed for %s", self._stream_url)
            return None
        return frame

    def is_open(self) -> bool:
        """Return True if the MJPEG stream is currently open."""
        return self._cap is not None and self._cap.isOpened()

    def __enter__(self) -> "PatientCamera":
        self.open()
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        self.close()
