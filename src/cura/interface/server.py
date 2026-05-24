"""
FastAPI server for the Cura robotic feeding assistant.

The T5AI board polls GET /status every 500 ms and posts commands via
POST /command. The web dashboard connects via WebSocket /ws and receives
push updates whenever state changes.

Usage (from main.py)::

    from cura.interface.server import CuraServer
    server = CuraServer()
    server.run(host=settings.server_host, port=settings.server_port)
    # ... orchestrator loop ...
    server.update_state(SystemState.APPROACHING)
"""
import asyncio
import logging
import threading
import time
from collections.abc import Coroutine
from typing import Any

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from cura.interface.models import (
    MealProgress,
    PatientCommand,
    STATE_LABELS,
    SystemState,
    SystemStatus,
)

logger = logging.getLogger(__name__)


class CuraServer:
    """Manages the FastAPI application and its shared state.

    This class is the single source of truth for system status and the
    incoming command queue. It is designed to be used from two threads:

    * The orchestrator (main.py) calls :meth:`update_state` and
      :meth:`get_next_command` from its own thread.
    * FastAPI / uvicorn serves HTTP and WebSocket requests from worker threads
      (or an event loop).

    All shared state access is guarded by ``threading.Lock``.
    """

    def __init__(self) -> None:
        """Initialise shared state, command queue, and register FastAPI routes."""
        self._status = SystemStatus(
            state=SystemState.IDLE,
            state_label=STATE_LABELS[SystemState.IDLE],
            timestamp=time.time(),
        )
        self._meal = MealProgress()
        self._command_queue: list[PatientCommand] = []
        self._ws_clients: list[WebSocket] = []
        self._lock = threading.Lock()

        # uvicorn server handle — populated by run()
        self._uvicorn_server: uvicorn.Server | None = None
        self._server_thread: threading.Thread | None = None

        # The event loop that uvicorn runs in, needed for thread-safe WebSocket
        # broadcasts initiated from outside the loop.
        self._loop: asyncio.AbstractEventLoop | None = None

        self.app = FastAPI(title="Cura", version="1.0")
        self._register_routes()

    # ------------------------------------------------------------------
    # Route registration
    # ------------------------------------------------------------------

    def _register_routes(self) -> None:
        """Attach all HTTP and WebSocket routes to ``self.app``."""

        app = self.app

        @app.get("/status", response_model=SystemStatus)
        async def get_status() -> SystemStatus:
            """Return the current system status snapshot."""
            with self._lock:
                return self._status

        @app.post("/command")
        async def post_command(command: PatientCommand) -> dict[str, bool]:
            """Accept a command from the T5AI board or dashboard and enqueue it."""
            logger.info(
                "Received command action=%r source=%r",
                command.action,
                command.source,
            )
            with self._lock:
                self._command_queue.append(command)
            return {"ok": True}

        @app.get("/meal", response_model=MealProgress)
        async def get_meal() -> MealProgress:
            """Return the current meal progress."""
            with self._lock:
                return self._meal

        @app.get("/health")
        async def health() -> dict[str, str]:
            """Simple liveness probe."""
            return {"status": "ok"}

        @app.websocket("/ws")
        async def websocket_endpoint(websocket: WebSocket) -> None:
            """WebSocket endpoint for real-time status push to the dashboard."""
            await websocket.accept()
            with self._lock:
                self._ws_clients.append(websocket)
                # Send current status immediately on connect.
                current = self._status

            logger.info("WebSocket client connected; total=%d", len(self._ws_clients))
            try:
                await websocket.send_text(current.model_dump_json())
                # Keep the connection alive — we only push; clients can close.
                while True:
                    # Wait for any message (ping/close frame) from the client.
                    await websocket.receive_text()
            except WebSocketDisconnect:
                logger.info("WebSocket client disconnected")
            finally:
                with self._lock:
                    try:
                        self._ws_clients.remove(websocket)
                    except ValueError:
                        pass

    # ------------------------------------------------------------------
    # Orchestrator-facing API
    # ------------------------------------------------------------------

    def update_state(
        self,
        state: SystemState,
        message: str = "",
        is_moving: bool | None = None,
        estop_active: bool | None = None,
    ) -> None:
        """Update the system state and broadcast to all connected WebSocket clients.

        Thread-safe: may be called from any thread.

        Args:
            state: The new :class:`SystemState`.
            message: Optional free-form detail string (e.g. error description).
            is_moving: Override the ``is_moving`` flag. If ``None``, inferred
                from *state* (any non-IDLE, non-ERROR, non-DRINKING state is
                considered moving).
            estop_active: Override the ``estop_active`` flag. If ``None``, the
                existing value is preserved.
        """
        _moving_states = {
            SystemState.APPROACHING,
            SystemState.GRASPING,
            SystemState.LIFTING,
            SystemState.DELIVERING,
            SystemState.RETRACTING,
            SystemState.RELEASING,
        }

        with self._lock:
            prev_estop = self._status.estop_active
            new_status = SystemStatus(
                state=state,
                state_label=STATE_LABELS.get(state, state.value),
                message=message,
                is_moving=is_moving if is_moving is not None else state in _moving_states,
                estop_active=estop_active if estop_active is not None else prev_estop,
                timestamp=time.time(),
            )
            self._status = new_status
            clients = list(self._ws_clients)

        logger.debug("State → %s (%s)", state.value, message or "no detail")

        # Broadcast asynchronously without blocking the caller.
        if clients and self._loop is not None:
            payload = new_status.model_dump_json()
            asyncio.run_coroutine_threadsafe(
                self._broadcast(payload, clients), self._loop
            )

    def get_next_command(self) -> PatientCommand | None:
        """Pop and return the next command from the queue, or ``None`` if empty.

        Thread-safe: may be called from any thread.

        Returns:
            The oldest :class:`PatientCommand` in the queue, or ``None``.
        """
        with self._lock:
            if self._command_queue:
                return self._command_queue.pop(0)
            return None

    def update_meal(self, meal: MealProgress) -> None:
        """Replace the current meal progress snapshot.

        Thread-safe: may be called from any thread.

        Args:
            meal: The new :class:`MealProgress` to store.
        """
        with self._lock:
            self._meal = meal
        logger.debug(
            "Meal progress updated: phase=%s bites=%d drinks=%d",
            meal.phase,
            meal.bites_given,
            meal.drinks_given,
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def run(self, host: str = "0.0.0.0", port: int = 8000) -> None:
        """Start the uvicorn server in a background daemon thread.

        Returns immediately so the caller (main.py) can continue with the
        orchestrator loop. The server runs until :meth:`stop` is called or
        the process exits.

        Args:
            host: Bind address (default ``'0.0.0.0'``).
            port: Bind port (default ``8000``).
        """
        config = uvicorn.Config(
            app=self.app,
            host=host,
            port=port,
            log_level="info",
            # Disable uvicorn's own signal handlers so main.py keeps control.
            loop="asyncio",
        )
        self._uvicorn_server = uvicorn.Server(config=config)

        def _run() -> None:
            # Create a fresh event loop for this thread and store a reference
            # so update_state() can schedule coroutines onto it.
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            try:
                loop.run_until_complete(self._uvicorn_server.serve())  # type: ignore[union-attr]
            finally:
                self._loop = None
                loop.close()

        self._server_thread = threading.Thread(
            target=_run, name="cura-server", daemon=True
        )
        self._server_thread.start()
        logger.info("Cura server started on http://%s:%d", host, port)

    def stop(self) -> None:
        """Signal uvicorn to shut down gracefully.

        Does nothing if the server was never started.
        """
        if self._uvicorn_server is not None:
            logger.info("Stopping Cura server…")
            self._uvicorn_server.should_exit = True
        if self._server_thread is not None:
            self._server_thread.join(timeout=5.0)
            self._server_thread = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _broadcast(self, payload: str, clients: list[WebSocket]) -> None:
        """Send *payload* to all *clients*, silently dropping broken connections.

        Args:
            payload: JSON string to send.
            clients: Snapshot of connected WebSocket clients.
        """
        for ws in clients:
            try:
                await ws.send_text(payload)
            except Exception as exc:  # noqa: BLE001
                logger.debug("WebSocket send failed (client likely gone): %s", exc)
