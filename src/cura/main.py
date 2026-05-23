"""
Cura Orchestrator — V1 state machine for water bottle feeding.

Keyboard controls:
  SPACE  — start feeding (when IDLE) / done drinking (when DRINKING)
  ESC    — emergency stop (any state)

V1 flow: IDLE → APPROACHING → GRASPING → LIFTING → DELIVERING → DRINKING → RETRACTING → RELEASING → IDLE
"""

import select
import signal
import sys
import termios
import threading
import time
import tty
import logging

from cura.arm.controller import ArmController
from cura.arm.trajectories import WAYPOINTS, PICKUP_SEQUENCE, RETURN_SEQUENCE, load_waypoints
from cura.interface.server import CuraServer
from cura.interface.models import SystemState
from cura.config.settings import settings

logger = logging.getLogger(__name__)

_KEY_SPACE = b" "
_KEY_ESC = b"\x1b"


def _keyboard_listener(
    space_event: threading.Event,
    estop_event: threading.Event,
    stop_flag: threading.Event,
) -> None:
    """Read raw keystrokes from stdin and set the appropriate events.

    Runs in a daemon thread. Restores terminal settings on exit.
    """
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        while not stop_flag.is_set():
            ready, _, _ = select.select([sys.stdin], [], [], 0.1)
            if not ready:
                continue
            ch = sys.stdin.buffer.read(1)
            if ch == _KEY_SPACE:
                logger.debug("SPACE key pressed")
                space_event.set()
            elif ch == _KEY_ESC:
                logger.debug("ESC key pressed")
                estop_event.set()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


class CuraOrchestrator:
    """Main V1 orchestrator: drives the state machine for water bottle delivery.

    Owns the ArmController and CuraServer. The state machine runs on the main
    thread; the arm trajectory and server each run on background threads.
    """

    def __init__(self) -> None:
        """Initialise hardware interfaces, server, and threading primitives."""
        self._arm = ArmController(
            can_port=settings.can_port,
            speed=settings.arm_speed,
            bustype=settings.can_bustype,
        )
        self._server = CuraServer()
        self._waypoints = load_waypoints(settings.waypoints_file)
        self._state = SystemState.IDLE

        self._space_pressed = threading.Event()
        self._estop_pressed = threading.Event()
        self._stop_flag = threading.Event()

        self._running = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Connect hardware, start server and keyboard listener, then run the loop."""
        logger.info("Cura starting up")

        connected = self._arm.connect()
        if not connected:
            logger.warning("Arm not connected — running in disconnected mode")

        self._server.run(host=settings.server_host, port=settings.server_port)
        self._server.update_state(SystemState.IDLE)

        kb_thread = threading.Thread(
            target=_keyboard_listener,
            args=(self._space_pressed, self._estop_pressed, self._stop_flag),
            daemon=True,
            name="keyboard-listener",
        )
        kb_thread.start()

        self._running = True
        try:
            self.run()
        finally:
            self.stop()

    def run(self) -> None:
        """Main state machine loop. Runs until _running is False."""
        _handlers = {
            SystemState.IDLE: self._handle_idle,
            SystemState.APPROACHING: self._handle_approaching,
            SystemState.GRASPING: self._handle_grasping,
            SystemState.LIFTING: self._handle_lifting,
            SystemState.DELIVERING: self._handle_delivering,
            SystemState.DRINKING: self._handle_drinking,
            SystemState.RETRACTING: self._handle_retracting,
            SystemState.RELEASING: self._handle_releasing,
            SystemState.ERROR: self._handle_error,
        }

        while self._running:
            if self._estop_pressed.is_set():
                self._estop_pressed.clear()
                self._handle_estop()
                continue

            handler = _handlers.get(self._state)
            if handler is None:
                logger.error("No handler for state %s — transitioning to ERROR", self._state)
                self._transition(SystemState.ERROR)
                continue

            handler()

    def stop(self) -> None:
        """Clean up: stop the keyboard thread, server, and disconnect the arm."""
        logger.info("Cura shutting down")
        self._running = False
        self._stop_flag.set()
        self._arm.disconnect()
        self._server.stop()

    # ------------------------------------------------------------------
    # State management
    # ------------------------------------------------------------------

    def _transition(self, new_state: SystemState) -> None:
        """Log and apply a state transition, then push it to the server."""
        logger.info("State: %s → %s", self._state.value, new_state.value)
        self._state = new_state
        self._server.update_state(new_state)

    def _handle_estop(self) -> None:
        """Handle emergency stop: halt the arm and enter ERROR state."""
        logger.critical("Emergency stop triggered in state %s", self._state.value)
        self._arm.emergency_stop()
        self._transition(SystemState.ERROR)

    # ------------------------------------------------------------------
    # State handlers
    # ------------------------------------------------------------------

    def _handle_idle(self) -> None:
        """Wait for the operator to press SPACE to begin a feeding cycle."""
        print("Press SPACE to start feeding, ESC to quit")
        self._space_pressed.clear()

        while self._running:
            if self._estop_pressed.is_set():
                return
            if self._space_pressed.wait(timeout=0.1):
                self._space_pressed.clear()
                self._transition(SystemState.APPROACHING)
                return

    def _handle_approaching(self) -> None:
        """Open the gripper and move to the pre-grasp position."""
        self._arm.open_gripper()

        approach_steps = PICKUP_SEQUENCE[:2]  # ["home", "pre_grasp"]
        self._arm.execute_sequence(approach_steps, self._waypoints)
        completed = self._arm.wait_for_completion(timeout=60.0)

        if self._estop_pressed.is_set():
            return
        if not completed:
            logger.error("Approaching sequence did not complete — entering ERROR")
            self._transition(SystemState.ERROR)
            return

        self._transition(SystemState.GRASPING)

    def _handle_grasping(self) -> None:
        """Descend to the bottle and close the gripper."""
        self._arm.execute_sequence(["grasp"], self._waypoints)
        completed = self._arm.wait_for_completion(timeout=30.0)

        if self._estop_pressed.is_set():
            return
        if not completed:
            logger.error("Grasp move failed — entering ERROR")
            self._transition(SystemState.ERROR)
            return

        self._arm.close_gripper()
        time.sleep(0.5)

        self._transition(SystemState.LIFTING)

    def _handle_lifting(self) -> None:
        """Lift the bottle clear of the cradle."""
        self._arm.execute_sequence(["lift"], self._waypoints)
        completed = self._arm.wait_for_completion(timeout=30.0)

        if self._estop_pressed.is_set():
            return
        if not completed:
            logger.error("Lift move failed — entering ERROR")
            self._transition(SystemState.ERROR)
            return

        self._transition(SystemState.DELIVERING)

    def _handle_delivering(self) -> None:
        """Move to the pre-deliver staging pose then extend to the patient's mouth."""
        self._arm.execute_sequence(["pre_deliver", "deliver"], self._waypoints)
        completed = self._arm.wait_for_completion(timeout=60.0)

        if self._estop_pressed.is_set():
            return
        if not completed:
            logger.error("Delivery sequence failed — entering ERROR")
            self._transition(SystemState.ERROR)
            return

        self._transition(SystemState.DRINKING)

    def _handle_drinking(self) -> None:
        """Hold position while the patient drinks; wait for SPACE to retract."""
        print("Patient is drinking. Press SPACE when done.")
        self._space_pressed.clear()

        while self._running:
            if self._estop_pressed.is_set():
                return
            if self._space_pressed.wait(timeout=0.1):
                self._space_pressed.clear()
                self._transition(SystemState.RETRACTING)
                return

    def _handle_retracting(self) -> None:
        """Retract from delivery position back to the grasp pose, then open gripper."""
        # deliver → pre_deliver → lift → grasp
        retract_steps = ["deliver", "pre_deliver", "lift", "grasp"]
        self._arm.execute_sequence(retract_steps, self._waypoints)
        completed = self._arm.wait_for_completion(timeout=60.0)

        if self._estop_pressed.is_set():
            return
        if not completed:
            logger.error("Retract sequence failed — entering ERROR")
            self._transition(SystemState.ERROR)
            return

        self._arm.open_gripper()
        self._transition(SystemState.RELEASING)

    def _handle_releasing(self) -> None:
        """Complete the return path from grasp back to home."""
        release_steps = ["pre_grasp", "home"]
        self._arm.execute_sequence(release_steps, self._waypoints)
        completed = self._arm.wait_for_completion(timeout=60.0)

        if self._estop_pressed.is_set():
            return
        if not completed:
            logger.error("Release/return sequence failed — entering ERROR")
            self._transition(SystemState.ERROR)
            return

        self._transition(SystemState.IDLE)

    def _handle_error(self) -> None:
        """Hold in ERROR state; operator must reset the system to continue."""
        logger.error("System is in ERROR state. Operator intervention required.")
        print("ERROR — Press ESC to quit or reset and restart")

        while self._running:
            if self._estop_pressed.is_set():
                self._estop_pressed.clear()
                logger.info("ESC received in ERROR state — shutting down")
                self._running = False
                return
            time.sleep(0.1)


def main() -> None:
    """Entry point: configure logging, create orchestrator, and run."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    orchestrator = CuraOrchestrator()
    signal.signal(signal.SIGINT, lambda s, f: orchestrator.stop())
    orchestrator.start()


if __name__ == "__main__":
    main()
