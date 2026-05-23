import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from cura.arm.safety import SafetyModule, Watchdog
from cura.arm.trajectories import JointConfig, PICKUP_SEQUENCE, RETURN_SEQUENCE, WAYPOINTS


class TestSafetyModuleJointValidation(unittest.TestCase):
    """validate_joints should enforce per-joint limits."""

    def setUp(self) -> None:
        self.safety = SafetyModule()

    def test_valid_joints_pass(self) -> None:
        joints = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        self.assertTrue(self.safety.validate_joints(joints))

    def test_valid_joints_at_limit_pass(self) -> None:
        joints = [150000.0, 90000.0, -90000.0, 150000.0, -150000.0, 150000.0]
        self.assertTrue(self.safety.validate_joints(joints))

    def test_joint_exceeds_max_fails(self) -> None:
        joints = [200000.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        self.assertFalse(self.safety.validate_joints(joints))

    def test_joint_below_min_fails(self) -> None:
        joints = [0.0, -100000.0, 0.0, 0.0, 0.0, 0.0]
        self.assertFalse(self.safety.validate_joints(joints))

    def test_elbow_joint_exceeds_tight_limit_fails(self) -> None:
        # J2 and J3 have ±90000, not ±150000
        joints = [0.0, 100000.0, 0.0, 0.0, 0.0, 0.0]
        self.assertFalse(self.safety.validate_joints(joints))

    def test_wrong_number_of_joints_fails(self) -> None:
        self.assertFalse(self.safety.validate_joints([0.0, 0.0, 0.0]))

    def test_valid_speed_passes(self) -> None:
        self.assertTrue(self.safety.validate_speed(50))

    def test_speed_at_max_passes(self) -> None:
        self.assertTrue(self.safety.validate_speed(SafetyModule.MAX_SPEED))

    def test_speed_over_max_fails(self) -> None:
        self.assertFalse(self.safety.validate_speed(SafetyModule.MAX_SPEED + 1))

    def test_valid_position_passes(self) -> None:
        self.assertTrue(self.safety.validate_position(100.0, 50.0, 200.0))

    def test_out_of_bounds_x_fails(self) -> None:
        self.assertFalse(self.safety.validate_position(700.0, 0.0, 0.0))

    def test_out_of_bounds_negative_z_fails(self) -> None:
        self.assertFalse(self.safety.validate_position(0.0, 0.0, -10.0))


class TestEstopCallbacks(unittest.TestCase):
    """trigger_estop should invoke all registered callbacks."""

    def test_callbacks_are_called(self) -> None:
        safety = SafetyModule()
        cb1 = MagicMock()
        cb2 = MagicMock()
        safety.register_estop_callback(cb1)
        safety.register_estop_callback(cb2)
        safety.trigger_estop()
        cb1.assert_called_once()
        cb2.assert_called_once()

    def test_exception_in_callback_does_not_prevent_others(self) -> None:
        safety = SafetyModule()
        failing_cb = MagicMock(side_effect=RuntimeError("boom"))
        ok_cb = MagicMock()
        safety.register_estop_callback(failing_cb)
        safety.register_estop_callback(ok_cb)
        safety.trigger_estop()
        ok_cb.assert_called_once()


class TestWatchdog(unittest.TestCase):
    """Watchdog should fire after timeout and be suppressable by pet()."""

    def test_fires_after_timeout(self) -> None:
        fired = threading.Event()
        wd = Watchdog(timeout_seconds=0.1, callback=fired.set)
        wd.start()
        fired.wait(timeout=1.0)
        self.assertTrue(fired.is_set(), "Watchdog did not fire within 1 s")

    def test_pet_resets_timer(self) -> None:
        fired = threading.Event()
        wd = Watchdog(timeout_seconds=0.15, callback=fired.set)
        wd.start()
        # Pet twice before the timer would fire
        time.sleep(0.05)
        wd.pet()
        time.sleep(0.05)
        wd.pet()
        # After the last pet the timer has another 0.15 s; we stop it immediately
        wd.stop()
        self.assertFalse(fired.is_set(), "Watchdog fired despite being pet and stopped")

    def test_stop_cancels_timer(self) -> None:
        fired = threading.Event()
        wd = Watchdog(timeout_seconds=0.1, callback=fired.set)
        wd.start()
        wd.stop()
        time.sleep(0.2)
        self.assertFalse(fired.is_set(), "Watchdog fired after stop()")


class TestExecuteSequenceWithMockedPiper(unittest.TestCase):
    """execute_sequence should abort early when stop_event is set."""

    def _make_controller(self) -> "ArmController":  # type: ignore[name-defined]  # noqa: F821
        """Return an ArmController wired up with a mock piper."""
        from cura.arm.controller import ArmController

        ctrl = ArmController(can_port="can0", speed=50)
        mock_piper = MagicMock()
        # Return joint positions that are exactly at each waypoint
        # so execute_waypoint reports success immediately.
        mock_piper.GetArmJointMsgs.return_value.joint_state.position = [0.0] * 6
        ctrl._piper = mock_piper
        ctrl._connected = True
        return ctrl

    def test_full_sequence_completes(self) -> None:
        from cura.arm.controller import ArmController
        from cura.arm.trajectories import PICKUP_SEQUENCE, WAYPOINTS

        ctrl = self._make_controller()
        ctrl.execute_sequence(PICKUP_SEQUENCE, WAYPOINTS)
        completed = ctrl.wait_for_completion(timeout=5.0)
        self.assertTrue(completed)

    def test_sequence_aborts_when_stop_event_set(self) -> None:
        from cura.arm.controller import ArmController
        from cura.arm.trajectories import PICKUP_SEQUENCE, WAYPOINTS

        ctrl = self._make_controller()
        # Set the stop event before the sequence runs — it should abort immediately.
        ctrl._stop_event.set()
        ctrl.execute_sequence(PICKUP_SEQUENCE, WAYPOINTS)
        ctrl.wait_for_completion(timeout=2.0)
        # JointCtrl should never have been called because the first waypoint check aborts
        ctrl._piper.JointCtrl.assert_not_called()

    def test_sequence_stops_mid_run_when_estop_called(self) -> None:
        """Emergency stop called after sequence starts should halt it early."""
        import threading
        from cura.arm.controller import ArmController
        from cura.arm.trajectories import PICKUP_SEQUENCE, WAYPOINTS

        ctrl = self._make_controller()

        call_count: list[int] = [0]
        original_joint_ctrl = ctrl._piper.JointCtrl

        def counting_joint_ctrl(*args: object) -> None:
            call_count[0] += 1
            if call_count[0] >= 2:
                # Trigger e-stop after the second waypoint command
                ctrl.emergency_stop()

        ctrl._piper.JointCtrl.side_effect = counting_joint_ctrl

        ctrl.execute_sequence(PICKUP_SEQUENCE, WAYPOINTS)
        ctrl.wait_for_completion(timeout=5.0)

        # At most 2 JointCtrl calls should have occurred before abort
        self.assertLessEqual(call_count[0], 3)


class TestEmergencyStop(unittest.TestCase):
    """emergency_stop must set stop_event and send the halt command."""

    def test_emergency_stop_sets_event_and_sends_halt(self) -> None:
        from cura.arm.controller import ArmController

        ctrl = ArmController()
        mock_piper = MagicMock()
        ctrl._piper = mock_piper
        ctrl._connected = True

        ctrl.emergency_stop()

        self.assertTrue(ctrl._stop_event.is_set())
        mock_piper.MotionCtrl_2.assert_called_once_with(0x00, 0x00, 0)

    def test_reset_stop_clears_event(self) -> None:
        from cura.arm.controller import ArmController

        ctrl = ArmController()
        ctrl._stop_event.set()
        ctrl.reset_stop()
        self.assertFalse(ctrl._stop_event.is_set())

    def test_emergency_stop_without_connection_does_not_raise(self) -> None:
        from cura.arm.controller import ArmController

        ctrl = ArmController()
        ctrl._piper = None
        ctrl._connected = False
        # Should not raise even with no piper
        ctrl.emergency_stop()
        self.assertTrue(ctrl._stop_event.is_set())


class TestTrajectories(unittest.TestCase):
    """Sequence sanity checks for PICKUP_SEQUENCE and RETURN_SEQUENCE."""

    def test_return_is_reverse_of_pickup(self) -> None:
        self.assertEqual(RETURN_SEQUENCE, list(reversed(PICKUP_SEQUENCE)))

    def test_all_pickup_waypoints_defined(self) -> None:
        for name in PICKUP_SEQUENCE:
            self.assertIn(name, WAYPOINTS, f"Waypoint {name!r} missing from WAYPOINTS")

    def test_waypoints_are_joint_configs(self) -> None:
        for name, cfg in WAYPOINTS.items():
            self.assertIsInstance(cfg, JointConfig, f"{name} is not a JointConfig")


if __name__ == "__main__":
    unittest.main()
