"""
Integration tests for the Cura V1 state machine.

All hardware is mocked; these tests run without a physical arm or CAN bus.
"""

import threading
import time
import unittest
from unittest.mock import MagicMock, patch, call

from cura.interface.models import SystemState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_arm_mock(wait_result: bool = True) -> MagicMock:
    """Return a fully configured ArmController mock."""
    arm = MagicMock()
    arm.connect.return_value = True
    arm.execute_sequence.return_value = True
    arm.wait_for_completion.return_value = wait_result
    arm.is_moving.return_value = False
    return arm


def _make_server_mock() -> MagicMock:
    """Return a CuraServer mock that accepts calls without side effects."""
    server = MagicMock()
    server.get_next_command.return_value = None
    return server


# ---------------------------------------------------------------------------
# Test suite
# ---------------------------------------------------------------------------

class TestStateMachineTransitions(unittest.TestCase):
    """Verify state transitions produced by the orchestrator state machine."""

    def _make_orchestrator(
        self,
        arm: MagicMock | None = None,
        server: MagicMock | None = None,
    ):
        """Construct a CuraOrchestrator with mocked dependencies."""
        from cura.main import CuraOrchestrator

        arm = arm or _make_arm_mock()
        server = server or _make_server_mock()

        with (
            patch("cura.main.ArmController", return_value=arm),
            patch("cura.main.CuraServer", return_value=server),
            patch("cura.main.load_waypoints", return_value={}),
        ):
            orchestrator = CuraOrchestrator()

        # Inject the mocks directly so tests can inspect them.
        orchestrator._arm = arm
        orchestrator._server = server
        return orchestrator

    # ------------------------------------------------------------------
    # IDLE → APPROACHING when SPACE is pressed
    # ------------------------------------------------------------------

    def test_idle_to_approaching_on_space(self) -> None:
        """Pressing SPACE in IDLE must transition to APPROACHING."""
        orchestrator = self._make_orchestrator()

        # Simulate SPACE being pressed shortly after _handle_idle starts.
        def _press_space() -> None:
            time.sleep(0.05)
            orchestrator._space_pressed.set()

        presser = threading.Thread(target=_press_space, daemon=True)
        presser.start()

        orchestrator._handle_idle()
        presser.join(timeout=1.0)

        self.assertEqual(orchestrator._state, SystemState.APPROACHING)
        orchestrator._server.update_state.assert_called_with(SystemState.APPROACHING)

    # ------------------------------------------------------------------
    # Emergency stop → ERROR from any state
    # ------------------------------------------------------------------

    def test_estop_transitions_to_error(self) -> None:
        """Triggering _handle_estop must call arm.emergency_stop and enter ERROR."""
        orchestrator = self._make_orchestrator()
        orchestrator._state = SystemState.LIFTING  # arbitrary mid-sequence state

        orchestrator._handle_estop()

        orchestrator._arm.emergency_stop.assert_called_once()
        self.assertEqual(orchestrator._state, SystemState.ERROR)
        orchestrator._server.update_state.assert_called_with(SystemState.ERROR)

    def test_estop_from_drinking_state(self) -> None:
        """E-stop must work from DRINKING state as well."""
        orchestrator = self._make_orchestrator()
        orchestrator._state = SystemState.DRINKING

        orchestrator._handle_estop()

        orchestrator._arm.emergency_stop.assert_called_once()
        self.assertEqual(orchestrator._state, SystemState.ERROR)

    # ------------------------------------------------------------------
    # Full V1 sequence: IDLE → … → IDLE
    # ------------------------------------------------------------------

    def test_full_v1_sequence(self) -> None:
        """Running the full V1 loop must cycle IDLE → APPROACHING → … → IDLE."""
        arm = _make_arm_mock(wait_result=True)
        orchestrator = self._make_orchestrator(arm=arm)

        states_visited: list[SystemState] = []
        original_transition = orchestrator._transition

        def _recording_transition(new_state: SystemState) -> None:
            states_visited.append(new_state)
            original_transition(new_state)

        orchestrator._transition = _recording_transition

        # Press SPACE immediately (start feeding).
        orchestrator._space_pressed.set()

        # Run all states except IDLE (which blocks waiting for input) and
        # DRINKING (which also blocks).  Drive each handler directly.
        orchestrator._handle_idle()          # IDLE → APPROACHING
        orchestrator._handle_approaching()   # APPROACHING → GRASPING
        orchestrator._handle_grasping()      # GRASPING → LIFTING
        orchestrator._handle_lifting()       # LIFTING → DELIVERING
        orchestrator._handle_delivering()    # DELIVERING → DRINKING

        # Press SPACE again (done drinking).
        orchestrator._space_pressed.set()
        orchestrator._handle_drinking()      # DRINKING → RETRACTING

        orchestrator._handle_retracting()    # RETRACTING → RELEASING
        orchestrator._handle_releasing()     # RELEASING → IDLE

        expected = [
            SystemState.APPROACHING,
            SystemState.GRASPING,
            SystemState.LIFTING,
            SystemState.DELIVERING,
            SystemState.DRINKING,
            SystemState.RETRACTING,
            SystemState.RELEASING,
            SystemState.IDLE,
        ]
        self.assertEqual(states_visited, expected)

    # ------------------------------------------------------------------
    # Arm failure mid-sequence → ERROR
    # ------------------------------------------------------------------

    def test_arm_failure_enters_error(self) -> None:
        """If wait_for_completion returns False, the state machine enters ERROR."""
        arm = _make_arm_mock(wait_result=False)
        orchestrator = self._make_orchestrator(arm=arm)

        orchestrator._space_pressed.set()
        orchestrator._handle_idle()         # → APPROACHING
        orchestrator._handle_approaching()  # arm reports failure → ERROR

        self.assertEqual(orchestrator._state, SystemState.ERROR)

    def test_arm_failure_during_grasping(self) -> None:
        """Arm failure in GRASPING must land in ERROR, not LIFTING."""
        arm = _make_arm_mock(wait_result=False)
        orchestrator = self._make_orchestrator(arm=arm)
        orchestrator._state = SystemState.GRASPING

        orchestrator._handle_grasping()

        self.assertEqual(orchestrator._state, SystemState.ERROR)

    def test_arm_failure_during_lifting(self) -> None:
        """Arm failure in LIFTING must land in ERROR."""
        arm = _make_arm_mock(wait_result=False)
        orchestrator = self._make_orchestrator(arm=arm)
        orchestrator._state = SystemState.LIFTING

        orchestrator._handle_lifting()

        self.assertEqual(orchestrator._state, SystemState.ERROR)

    def test_arm_failure_during_delivering(self) -> None:
        """Arm failure in DELIVERING must land in ERROR."""
        arm = _make_arm_mock(wait_result=False)
        orchestrator = self._make_orchestrator(arm=arm)
        orchestrator._state = SystemState.DELIVERING

        orchestrator._handle_delivering()

        self.assertEqual(orchestrator._state, SystemState.ERROR)

    def test_arm_failure_during_retracting(self) -> None:
        """Arm failure in RETRACTING must land in ERROR."""
        arm = _make_arm_mock(wait_result=False)
        orchestrator = self._make_orchestrator(arm=arm)
        orchestrator._state = SystemState.RETRACTING

        orchestrator._handle_retracting()

        self.assertEqual(orchestrator._state, SystemState.ERROR)

    # ------------------------------------------------------------------
    # Arm API call assertions
    # ------------------------------------------------------------------

    def test_approaching_opens_gripper(self) -> None:
        """_handle_approaching must call open_gripper before running the sequence."""
        arm = _make_arm_mock()
        orchestrator = self._make_orchestrator(arm=arm)
        orchestrator._state = SystemState.IDLE

        orchestrator._space_pressed.set()
        orchestrator._handle_idle()
        orchestrator._handle_approaching()

        arm.open_gripper.assert_called_once()
        arm.execute_sequence.assert_called_once_with(["home", "pre_grasp"], {})

    def test_grasping_closes_gripper(self) -> None:
        """_handle_grasping must call close_gripper after reaching the grasp waypoint."""
        arm = _make_arm_mock()
        orchestrator = self._make_orchestrator(arm=arm)
        orchestrator._state = SystemState.APPROACHING

        with patch("cura.main.time.sleep"):
            orchestrator._handle_grasping()

        arm.close_gripper.assert_called_once()
        arm.execute_sequence.assert_called_once_with(["grasp"], {})

    def test_retracting_opens_gripper(self) -> None:
        """_handle_retracting must open the gripper when back at the grasp position."""
        arm = _make_arm_mock()
        orchestrator = self._make_orchestrator(arm=arm)
        orchestrator._state = SystemState.DRINKING

        orchestrator._space_pressed.set()
        orchestrator._handle_drinking()
        orchestrator._handle_retracting()

        arm.open_gripper.assert_called_once()

    def test_server_updated_on_every_transition(self) -> None:
        """server.update_state must be called with the new state on every transition."""
        arm = _make_arm_mock()
        server = _make_server_mock()
        orchestrator = self._make_orchestrator(arm=arm, server=server)

        orchestrator._space_pressed.set()
        orchestrator._handle_idle()

        server.update_state.assert_called_with(SystemState.APPROACHING)


class TestOrchestratorStartup(unittest.TestCase):
    """Verify that CuraOrchestrator wires up components correctly at startup."""

    def test_arm_connected_on_start(self) -> None:
        """start() must call arm.connect() before the state machine runs."""
        from cura.main import CuraOrchestrator

        arm = _make_arm_mock()
        server = _make_server_mock()

        # Make run() return immediately so the test doesn't block.
        with (
            patch("cura.main.ArmController", return_value=arm),
            patch("cura.main.CuraServer", return_value=server),
            patch("cura.main.load_waypoints", return_value={}),
        ):
            orchestrator = CuraOrchestrator()
            orchestrator._arm = arm
            orchestrator._server = server
            orchestrator.run = lambda: None  # type: ignore[method-assign]

            with patch("threading.Thread"):
                orchestrator.start()

        arm.connect.assert_called_once()

    def test_server_started_on_start(self) -> None:
        """start() must call server.run() before the state machine runs."""
        from cura.main import CuraOrchestrator
        from cura.config.settings import settings

        arm = _make_arm_mock()
        server = _make_server_mock()

        with (
            patch("cura.main.ArmController", return_value=arm),
            patch("cura.main.CuraServer", return_value=server),
            patch("cura.main.load_waypoints", return_value={}),
        ):
            orchestrator = CuraOrchestrator()
            orchestrator._arm = arm
            orchestrator._server = server
            orchestrator.run = lambda: None  # type: ignore[method-assign]

            with patch("threading.Thread"):
                orchestrator.start()

        server.run.assert_called_once_with(
            host=settings.server_host, port=settings.server_port
        )


if __name__ == "__main__":
    unittest.main()
