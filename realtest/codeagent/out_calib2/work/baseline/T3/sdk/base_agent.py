import queue
import time
import asyncio
from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional


class BaseAHIAgent(ABC):
    """Minimal agent interface.

    Agent developers implement the 3 abstract methods.
    The SDK's AgentRunner provides FastAPI, lifecycle, and dependencies.
    """

    def __init__(self):
        self.agent_id: str = "unknown"
        self.agent_name: str = ""
        self.ahi_bus = None          # AHIBus instance (injected by AgentRunner)
        self.shell = None            # BaseAHIShell instance (injected by AgentRunner)
        self.db = None               # AgentDB instance (injected by AgentRunner)
        self._output_queue: queue.Queue = queue.Queue()
        self._shutdown_requested = False

    # ── Abstract methods (must implement) ──

    @abstractmethod
    def process_input(self, input_data: dict) -> None:
        """Process an incoming input. May call self._put_action() to produce output."""

    @abstractmethod
    def get_outputs(self) -> list:
        """Drain and return all pending output actions."""

    @abstractmethod
    def get_state(self) -> dict:
        """Return current agent state snapshot for heartbeat/resource monitoring."""

    # ── Optional hooks ──

    def on_start(self) -> None:
        """Called after agent is initialized and before first process_input."""

    def on_stop(self) -> None:
        """Called when agent is shutting down."""

    def on_wakeup(self) -> None:
        """Called by the autonomous loop scheduler on each wakeup tick.
        Subclasses override this to implement autonomous behavior."""

    # ── SDK built-in ──

    def _put_action(self, action: dict) -> None:
        """Enqueue an action and notify main process (with retry fallback)."""
        action.setdefault("timestamp", time.time())
        action.setdefault("from_agent", f"agent:{self.agent_id}")

        self._output_queue.put(action)

        if self.ahi_bus:
            try:
                self._run_async(self.ahi_bus.notify_has_outputs())
            except Exception:
                # Async notification failed, try sync with retries
                self.ahi_bus.notify_has_outputs_sync(retries=2)

    @property
    def shutdown_requested(self) -> bool:
        return self._shutdown_requested

    def _request_shutdown(self):
        self._shutdown_requested = True

    @staticmethod
    def _run_async(coro):
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(coro)
            else:
                asyncio.run(coro)
        except RuntimeError:
            try:
                asyncio.run(coro)
            except Exception:
                pass
