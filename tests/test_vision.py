import sys
import types
import unittest
from unittest.mock import MagicMock, patch

import numpy as np


# ---------------------------------------------------------------------------
# DetectedObject
# ---------------------------------------------------------------------------

class TestDetectedObject(unittest.TestCase):
    """Tests for the DetectedObject dataclass."""

    def setUp(self) -> None:
        from cura.vision.detector import DetectedObject
        self.DetectedObject = DetectedObject

    def test_fields_stored_correctly(self) -> None:
        obj = self.DetectedObject(
            label="bottle",
            confidence=0.9,
            bbox=(10, 20, 110, 120),
            center_px=(60, 70),
        )
        self.assertEqual(obj.label, "bottle")
        self.assertAlmostEqual(obj.confidence, 0.9)
        self.assertEqual(obj.bbox, (10, 20, 110, 120))
        self.assertEqual(obj.center_px, (60, 70))
        self.assertIsNone(obj.position_3d)

    def test_position_3d_can_be_set(self) -> None:
        obj = self.DetectedObject(
            label="fork",
            confidence=0.75,
            bbox=(0, 0, 50, 50),
            center_px=(25, 25),
            position_3d=(100.0, 200.0, 0.0),
        )
        self.assertEqual(obj.position_3d, (100.0, 200.0, 0.0))


# ---------------------------------------------------------------------------
# ObjectDetector
# ---------------------------------------------------------------------------

class TestObjectDetector(unittest.TestCase):
    """Tests for ObjectDetector with ultralytics mocked out."""

    def _make_fake_ultralytics(self) -> types.ModuleType:
        """Build a minimal fake ultralytics module tree."""
        fake_ultralytics = types.ModuleType("ultralytics")
        fake_YOLO = MagicMock(name="YOLO")
        fake_ultralytics.YOLO = fake_YOLO
        return fake_ultralytics

    def test_load_returns_true_on_success(self) -> None:
        from cura.vision.detector import ObjectDetector

        fake_ultralytics = self._make_fake_ultralytics()
        with patch.dict(sys.modules, {"ultralytics": fake_ultralytics}):
            det = ObjectDetector()
            self.assertTrue(det.load())

    def test_load_returns_false_when_import_fails(self) -> None:
        from cura.vision.detector import ObjectDetector

        with patch.dict(sys.modules, {"ultralytics": None}):
            det = ObjectDetector()
            self.assertFalse(det.load())

    def test_detect_returns_empty_when_model_not_loaded(self) -> None:
        from cura.vision.detector import ObjectDetector

        det = ObjectDetector()
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        self.assertEqual(det.detect(frame), [])

    def _make_fake_result(
        self,
        label: str,
        confidence: float,
        bbox: tuple[int, int, int, int],
    ) -> MagicMock:
        """Build a fake YOLO result object for a single detection."""
        x1, y1, x2, y2 = bbox
        box = MagicMock()
        box.conf = [confidence]
        box.cls = [0]
        box.xyxy = [np.array([x1, y1, x2, y2], dtype=float)]

        result = MagicMock()
        result.names = {0: label}
        result.boxes = [box]
        return result

    def test_detect_filters_by_confidence(self) -> None:
        from cura.vision.detector import ObjectDetector

        fake_ultralytics = self._make_fake_ultralytics()
        with patch.dict(sys.modules, {"ultralytics": fake_ultralytics}):
            det = ObjectDetector(confidence_threshold=0.6)
            det.load()

            high_conf_result = self._make_fake_result("bottle", 0.9, (0, 0, 100, 100))
            low_conf_result = self._make_fake_result("bottle", 0.3, (0, 0, 100, 100))
            det._model.return_value = [high_conf_result, low_conf_result]  # type: ignore[union-attr]

            frame = np.zeros((480, 640, 3), dtype=np.uint8)
            detections = det.detect(frame)

        self.assertEqual(len(detections), 1)
        self.assertEqual(detections[0].label, "bottle")
        self.assertAlmostEqual(detections[0].confidence, 0.9)

    def test_detect_filters_by_label(self) -> None:
        from cura.vision.detector import ObjectDetector

        fake_ultralytics = self._make_fake_ultralytics()
        with patch.dict(sys.modules, {"ultralytics": fake_ultralytics}):
            det = ObjectDetector(
                confidence_threshold=0.5,
                target_labels=["bottle"],
            )
            det.load()

            bottle_result = self._make_fake_result("bottle", 0.9, (0, 0, 100, 100))
            chair_result = self._make_fake_result("chair", 0.9, (0, 0, 50, 50))
            det._model.return_value = [bottle_result, chair_result]  # type: ignore[union-attr]

            frame = np.zeros((480, 640, 3), dtype=np.uint8)
            detections = det.detect(frame)

        self.assertEqual(len(detections), 1)
        self.assertEqual(detections[0].label, "bottle")

    def test_detect_computes_center_correctly(self) -> None:
        from cura.vision.detector import ObjectDetector

        fake_ultralytics = self._make_fake_ultralytics()
        with patch.dict(sys.modules, {"ultralytics": fake_ultralytics}):
            det = ObjectDetector(confidence_threshold=0.5)
            det.load()

            result = self._make_fake_result("cup", 0.8, (10, 20, 110, 120))
            det._model.return_value = [result]  # type: ignore[union-attr]

            frame = np.zeros((480, 640, 3), dtype=np.uint8)
            detections = det.detect(frame)

        self.assertEqual(len(detections), 1)
        self.assertEqual(detections[0].center_px, (60, 70))

    def test_detect_position_3d_none_without_calibration(self) -> None:
        from cura.vision.detector import ObjectDetector

        fake_ultralytics = self._make_fake_ultralytics()
        with patch.dict(sys.modules, {"ultralytics": fake_ultralytics}):
            det = ObjectDetector(confidence_threshold=0.5)
            det.load()

            result = self._make_fake_result("bottle", 0.8, (0, 0, 100, 100))
            det._model.return_value = [result]  # type: ignore[union-attr]

            frame = np.zeros((480, 640, 3), dtype=np.uint8)
            detections = det.detect(frame)

        self.assertIsNone(detections[0].position_3d)

    def test_set_calibration_and_pixel_to_world(self) -> None:
        from cura.vision.detector import ObjectDetector

        det = ObjectDetector()
        identity = np.eye(3, dtype=np.float64)
        det.set_calibration(identity)

        result = det._pixel_to_world(100, 200)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertAlmostEqual(result[0], 100.0)
        self.assertAlmostEqual(result[1], 200.0)
        self.assertAlmostEqual(result[2], 0.0)

    def test_pixel_to_world_returns_none_without_calibration(self) -> None:
        from cura.vision.detector import ObjectDetector

        det = ObjectDetector()
        self.assertIsNone(det._pixel_to_world(50, 50))


# ---------------------------------------------------------------------------
# MouthPosition
# ---------------------------------------------------------------------------

class TestMouthPosition(unittest.TestCase):
    """Tests for the MouthPosition dataclass."""

    def setUp(self) -> None:
        from cura.vision.mouth_tracker import MouthPosition
        self.MouthPosition = MouthPosition

    def test_fields_stored_correctly(self) -> None:
        mp = self.MouthPosition(
            center_px=(320, 240),
            position_3d=None,
            confidence=0.85,
        )
        self.assertEqual(mp.center_px, (320, 240))
        self.assertIsNone(mp.position_3d)
        self.assertAlmostEqual(mp.confidence, 0.85)

    def test_position_3d_can_be_provided(self) -> None:
        mp = self.MouthPosition(
            center_px=(100, 150),
            position_3d=(50.0, 60.0, 70.0),
            confidence=0.9,
        )
        self.assertEqual(mp.position_3d, (50.0, 60.0, 70.0))


# ---------------------------------------------------------------------------
# MouthTracker
# ---------------------------------------------------------------------------

class TestMouthTracker(unittest.TestCase):
    """Tests for MouthTracker with MediaPipe mocked out."""

    def test_track_returns_none_when_not_loaded(self) -> None:
        from cura.vision.mouth_tracker import MouthTracker

        tracker = MouthTracker()
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        self.assertIsNone(tracker.track(frame))

    def test_load_returns_false_when_mediapipe_missing(self) -> None:
        from cura.vision.mouth_tracker import MouthTracker

        with patch.dict(sys.modules, {"mediapipe": None}):
            tracker = MouthTracker()
            self.assertFalse(tracker.load())

    def test_track_returns_none_on_blank_frame(self) -> None:
        """A blank (all-zero) frame should yield no face detections."""
        from cura.vision.mouth_tracker import MouthTracker

        fake_mp = types.ModuleType("mediapipe")
        fake_solutions = types.SimpleNamespace()
        fake_face_mesh_mod = types.SimpleNamespace()

        fake_mesh_instance = MagicMock()
        fake_mesh_instance.process.return_value = MagicMock(
            multi_face_landmarks=None
        )

        fake_face_mesh_cls = MagicMock(return_value=fake_mesh_instance)
        fake_face_mesh_mod.FaceMesh = fake_face_mesh_cls
        fake_solutions.face_mesh = fake_face_mesh_mod
        fake_mp.solutions = fake_solutions  # type: ignore[attr-defined]

        with patch.dict(sys.modules, {"mediapipe": fake_mp}):
            tracker = MouthTracker()
            tracker.load()
            frame = np.zeros((480, 640, 3), dtype=np.uint8)
            result = tracker.track(frame)

        self.assertIsNone(result)

    def test_track_returns_mouth_position_on_detected_face(self) -> None:
        from cura.vision.mouth_tracker import MouthTracker, MouthPosition

        fake_mp = types.ModuleType("mediapipe")
        fake_solutions = types.SimpleNamespace()
        fake_face_mesh_mod = types.SimpleNamespace()

        def _make_landmark(x: float, y: float) -> MagicMock:
            lm = MagicMock()
            lm.x = x
            lm.y = y
            lm.visibility = 1.0
            return lm

        landmarks = [MagicMock()] * 15
        # Landmark 13: upper lip center at (0.5, 0.5) in normalised coords
        landmarks[13] = _make_landmark(0.5, 0.5)
        # Landmark 14: lower lip center at (0.5, 0.55)
        landmarks[14] = _make_landmark(0.5, 0.55)

        fake_face_landmark = MagicMock()
        fake_face_landmark.landmark = landmarks

        fake_mesh_instance = MagicMock()
        fake_mesh_instance.process.return_value = MagicMock(
            multi_face_landmarks=[fake_face_landmark]
        )

        fake_face_mesh_cls = MagicMock(return_value=fake_mesh_instance)
        fake_face_mesh_mod.FaceMesh = fake_face_mesh_cls
        fake_solutions.face_mesh = fake_face_mesh_mod
        fake_mp.solutions = fake_solutions  # type: ignore[attr-defined]

        with patch.dict(sys.modules, {"mediapipe": fake_mp}):
            tracker = MouthTracker()
            tracker.load()
            frame = np.zeros((480, 640, 3), dtype=np.uint8)
            result = tracker.track(frame)

        self.assertIsInstance(result, MouthPosition)
        assert result is not None
        # Expected center_px: x = int((0.5+0.5)/2 * 640) = 320
        #                      y = int((0.5+0.55)/2 * 480) = int(0.525 * 480) = 252
        self.assertEqual(result.center_px[0], 320)
        self.assertEqual(result.center_px[1], 252)
        self.assertIsNone(result.position_3d)
        self.assertGreater(result.confidence, 0.0)


# ---------------------------------------------------------------------------
# ArmCamera
# ---------------------------------------------------------------------------

class TestArmCamera(unittest.TestCase):
    """Tests for ArmCamera."""

    def test_open_with_invalid_index_returns_false(self) -> None:
        """Device index 999 is virtually guaranteed not to exist on any host."""
        from cura.vision.camera import ArmCamera

        cam = ArmCamera(index=999)
        result = cam.open()
        cam.close()
        self.assertFalse(result)

    def test_get_frame_returns_none_when_not_open(self) -> None:
        from cura.vision.camera import ArmCamera

        cam = ArmCamera(index=999)
        self.assertIsNone(cam.get_frame())

    def test_is_open_false_before_open(self) -> None:
        from cura.vision.camera import ArmCamera

        cam = ArmCamera()
        self.assertFalse(cam.is_open())

    def test_context_manager_closes_on_exit(self) -> None:
        from cura.vision.camera import ArmCamera

        with ArmCamera(index=999) as cam:
            pass
        self.assertFalse(cam.is_open())


# ---------------------------------------------------------------------------
# PatientCamera
# ---------------------------------------------------------------------------

class TestPatientCamera(unittest.TestCase):
    """Tests for PatientCamera."""

    def test_open_with_unreachable_url_returns_false(self) -> None:
        from cura.vision.camera import PatientCamera

        cam = PatientCamera("http://192.0.2.1:8080/stream")
        result = cam.open()
        cam.close()
        self.assertFalse(result)

    def test_get_frame_returns_none_when_not_open(self) -> None:
        from cura.vision.camera import PatientCamera

        cam = PatientCamera("http://192.0.2.1:8080/stream")
        self.assertIsNone(cam.get_frame())

    def test_is_open_false_before_open(self) -> None:
        from cura.vision.camera import PatientCamera

        cam = PatientCamera("http://192.0.2.1:8080/stream")
        self.assertFalse(cam.is_open())

    def test_context_manager_closes_on_exit(self) -> None:
        from cura.vision.camera import PatientCamera

        with PatientCamera("http://192.0.2.1:8080/stream") as cam:
            pass
        self.assertFalse(cam.is_open())


if __name__ == "__main__":
    unittest.main()
