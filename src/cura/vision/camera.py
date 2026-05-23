"""Camera abstractions for Cura's two-camera setup."""
import logging
import urllib.request

import cv2
import numpy as np

logger = logging.getLogger(__name__)


def _frame_to_bgr(frame) -> np.ndarray | None:
    """Convert an Orbbec VideoFrame to a BGR numpy array.

    Handles RGB, BGR, MJPG, and YUYV pixel formats.  Returns None for any
    format that is not recognised.
    """
    from pyorbbecsdk import OBFormat  # local import to keep top-level optional

    w, h = frame.get_width(), frame.get_height()
    data = np.asanyarray(frame.get_data())
    fmt = frame.get_format()

    if fmt == OBFormat.RGB:
        return cv2.cvtColor(np.resize(data, (h, w, 3)), cv2.COLOR_RGB2BGR)
    elif fmt == OBFormat.BGR:
        return np.resize(data, (h, w, 3))
    elif fmt == OBFormat.MJPG:
        return cv2.imdecode(data, cv2.IMREAD_COLOR)
    elif fmt == OBFormat.YUYV:
        return cv2.cvtColor(np.resize(data, (h, w, 2)), cv2.COLOR_YUV2BGR_YUYV)

    logger.warning("ArmCamera: unsupported color frame format %s", fmt)
    return None


class ArmCamera:
    """Orbbec depth camera mounted on the Piper robot arm, facing the table.

    Uses the Orbbec Pipeline which auto-detects the connected USB device.
    Provides both color (BGR) and depth (float32 mm) frames.
    """

    def __init__(self) -> None:
        """Initialise the camera handle.  No hardware access until open() is called."""
        self._pipeline = None
        self._open: bool = False

    def open(self) -> bool:
        """Open the Orbbec camera and start streaming.

        The Orbbec SDK auto-detects the connected device; no device index is
        required.  The camera must be connected via USB before calling this
        method.

        Returns:
            True if the pipeline started successfully, False otherwise.
        """
        try:
            from pyorbbecsdk import OBError, Pipeline
        except ImportError:
            logger.error(
                "ArmCamera: pyorbbecsdk2 is not installed — "
                "run 'pip install pyorbbecsdk2'"
            )
            return False

        try:
            pipeline = Pipeline()
            pipeline.start()
            self._pipeline = pipeline
            self._open = True
            logger.info("ArmCamera: Orbbec pipeline started")
            return True
        except OBError as exc:
            logger.error("ArmCamera: OBError starting pipeline: %s", exc)
            self._pipeline = None
            self._open = False
            return False
        except Exception:
            logger.exception("ArmCamera: unexpected error starting pipeline")
            self._pipeline = None
            self._open = False
            return False

    def close(self) -> None:
        """Stop the Orbbec pipeline and release hardware resources."""
        if self._open and self._pipeline is not None:
            try:
                self._pipeline.stop()
            except Exception:
                logger.exception("ArmCamera: error stopping pipeline")
            finally:
                self._pipeline = None
                self._open = False
                logger.info("ArmCamera: Orbbec pipeline stopped")

    def get_frame(self) -> np.ndarray | None:
        """Return the latest color frame as a BGR numpy array.

        Uses a 100 ms wait so callers can poll at real-time rates without
        blocking.

        Returns:
            BGR uint8 ndarray, or None if the camera is not open or the read
            timed out / failed.
        """
        if not self._open or self._pipeline is None:
            logger.warning("ArmCamera: get_frame called but camera is not open")
            return None
        try:
            frames = self._pipeline.wait_for_frames(100)
            if frames is None:
                return None
            color_frame = frames.get_color_frame()
            if color_frame is None:
                return None
            return _frame_to_bgr(color_frame)
        except Exception:
            logger.exception("ArmCamera: error reading color frame")
            return None

    def get_depth_frame(self) -> np.ndarray | None:
        """Return the latest depth frame as a float32 numpy array in millimetres.

        Uses a 100 ms wait so callers can poll at real-time rates without
        blocking.

        Returns:
            float32 ndarray shaped (H, W) with values in mm, or None on
            failure.
        """
        if not self._open or self._pipeline is None:
            logger.warning("ArmCamera: get_depth_frame called but camera is not open")
            return None
        try:
            frames = self._pipeline.wait_for_frames(100)
            if frames is None:
                return None
            depth_frame = frames.get_depth_frame()
            if depth_frame is None:
                return None
            return _depth_frame_to_mm(depth_frame)
        except Exception:
            logger.exception("ArmCamera: error reading depth frame")
            return None

    def get_rgbd(self) -> "tuple[np.ndarray, np.ndarray] | None":
        """Return a (color_bgr, depth_mm) pair from a single frame capture.

        More efficient than calling get_frame() and get_depth_frame()
        separately because only one wait_for_frames() call is made.

        Returns:
            Tuple of (BGR uint8 ndarray, float32 depth ndarray in mm), or
            None if either frame is unavailable.
        """
        if not self._open or self._pipeline is None:
            logger.warning("ArmCamera: get_rgbd called but camera is not open")
            return None
        try:
            frames = self._pipeline.wait_for_frames(100)
            if frames is None:
                return None
            color_frame = frames.get_color_frame()
            depth_frame = frames.get_depth_frame()
            if color_frame is None or depth_frame is None:
                return None
            color_bgr = _frame_to_bgr(color_frame)
            depth_mm = _depth_frame_to_mm(depth_frame)
            if color_bgr is None or depth_mm is None:
                return None
            return color_bgr, depth_mm
        except Exception:
            logger.exception("ArmCamera: error reading RGBD frame")
            return None

    def is_open(self) -> bool:
        """Return True if the Orbbec pipeline is currently running."""
        return self._open

    def __enter__(self) -> "ArmCamera":
        self.open()
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        self.close()


def _depth_frame_to_mm(depth_frame) -> np.ndarray:
    """Convert an Orbbec depth VideoFrame to a float32 array in millimetres."""
    w, h = depth_frame.get_width(), depth_frame.get_height()
    scale = depth_frame.get_depth_scale()
    raw = np.frombuffer(depth_frame.get_data(), dtype=np.uint16).reshape((h, w))
    return raw.astype(np.float32) * scale


class PatientCamera:
    """MJPEG camera stream from the T5AI companion device, facing the patient.

    Receives the feed over Wi-Fi using cv2.VideoCapture with an HTTP URL.
    """

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

        Performs a lightweight URL reachability probe before handing off to
        OpenCV so that connection failures surface as clear log messages rather
        than silent timeouts.

        Returns:
            True if the stream opened successfully, False otherwise.
        """
        try:
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
            BGR uint8 ndarray, or None if the stream is not connected or the
            read failed.
        """
        if self._cap is None or not self._cap.isOpened():
            logger.warning(
                "PatientCamera: get_frame called but stream is not open"
            )
            return None
        ret, frame = self._cap.read()
        if not ret:
            logger.warning(
                "PatientCamera: frame read failed for %s", self._stream_url
            )
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
