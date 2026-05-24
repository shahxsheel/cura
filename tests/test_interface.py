"""
Tests for CuraServer (Member D — interface layer).

All tests start the server on a free port and hit it over real HTTP,
mirroring exactly what the T5AI board and web dashboard do.
No mocks for the HTTP layer.
"""

import json
import socket
import time
import unittest
import urllib.request

from cura.interface.models import SystemState
from cura.interface.server import CuraServer


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _get(url: str) -> dict:
    raw = urllib.request.urlopen(url, timeout=3).read()
    return json.loads(raw)


def _post(url: str, payload: dict) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    raw = urllib.request.urlopen(req, timeout=3).read()
    return json.loads(raw)


class TestCuraServer(unittest.TestCase):

    def setUp(self) -> None:
        self._port = _free_port()
        self._server = CuraServer()
        self._server.run("127.0.0.1", self._port)
        time.sleep(0.4)
        self._base = f"http://127.0.0.1:{self._port}"

    def tearDown(self) -> None:
        self._server.stop()

    # GET /status

    def test_status_idle_on_startup(self) -> None:
        data = _get(f"{self._base}/status")
        self.assertEqual(data["state"], "IDLE")
        self.assertEqual(data["state_label"], "Ready")
        self.assertFalse(data["is_moving"])
        self.assertFalse(data["estop_active"])

    def test_status_reflects_update_state(self) -> None:
        self._server.update_state(SystemState.DELIVERING)
        time.sleep(0.05)
        data = _get(f"{self._base}/status")
        self.assertEqual(data["state"], "DELIVERING")
        self.assertTrue(data["is_moving"])

    def test_drinking_state_is_not_moving(self) -> None:
        self._server.update_state(SystemState.DRINKING)
        time.sleep(0.05)
        data = _get(f"{self._base}/status")
        self.assertFalse(data["is_moving"])

    def test_error_state_is_not_moving(self) -> None:
        self._server.update_state(SystemState.ERROR)
        time.sleep(0.05)
        data = _get(f"{self._base}/status")
        self.assertFalse(data["is_moving"])

    def test_status_has_timestamp(self) -> None:
        data = _get(f"{self._base}/status")
        self.assertGreater(data["timestamp"], 0)

    # POST /command

    def test_post_command_returns_ok(self) -> None:
        resp = _post(
            f"{self._base}/command",
            {"action": "start_feeding", "source": "t5ai"},
        )
        self.assertTrue(resp["ok"])

    def test_posted_command_in_queue(self) -> None:
        _post(f"{self._base}/command", {"action": "emergency_stop", "source": "dashboard"})
        time.sleep(0.05)
        cmd = self._server.get_next_command()
        self.assertIsNotNone(cmd)
        self.assertEqual(cmd.action, "emergency_stop")
        self.assertEqual(cmd.source, "dashboard")

    def test_command_queue_is_fifo(self) -> None:
        _post(f"{self._base}/command", {"action": "start_feeding", "source": "t5ai"})
        _post(f"{self._base}/command", {"action": "done_drinking", "source": "t5ai"})
        time.sleep(0.05)
        self.assertEqual(self._server.get_next_command().action, "start_feeding")
        self.assertEqual(self._server.get_next_command().action, "done_drinking")

    def test_empty_queue_returns_none(self) -> None:
        self.assertIsNone(self._server.get_next_command())

    # GET /health

    def test_health_ok(self) -> None:
        data = _get(f"{self._base}/health")
        self.assertEqual(data["status"], "ok")

    # GET /meal

    def test_meal_defaults(self) -> None:
        data = _get(f"{self._base}/meal")
        self.assertEqual(data["phase"], "water")
        self.assertEqual(data["bites_given"], 0)
        self.assertEqual(data["drinks_given"], 0)

    # estop_active flag persistence

    def test_estop_flag_preserved_on_subsequent_update(self) -> None:
        self._server.update_state(SystemState.ERROR, estop_active=True)
        time.sleep(0.05)
        self._server.update_state(SystemState.ERROR)  # no estop_active arg
        time.sleep(0.05)
        data = _get(f"{self._base}/status")
        self.assertTrue(data["estop_active"])


if __name__ == "__main__":
    unittest.main()
