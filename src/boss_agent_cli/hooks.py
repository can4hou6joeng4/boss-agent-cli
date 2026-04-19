"""Lightweight synchronous hook bus for extensibility.

Two hook types:
- SyncHook: fire-and-forget, handler exceptions are swallowed and logged
- BailHook: handlers can veto an action by returning a truthy value
"""
import sys
from dataclasses import dataclass, field
from typing import Callable

Handler = Callable[[dict], None]
BailHandler = Callable[[dict], str | bool | None]


class SyncHook:
	"""Synchronous hook — all handlers fire, exceptions are swallowed."""

	def __init__(self) -> None:
		self._handlers: list[tuple[str, Handler]] = []

	def tap(self, name: str, handler: Handler) -> None:
		self._handlers.append((name, handler))

	def call(self, payload: dict) -> None:
		for name, handler in self._handlers:
			try:
				handler(payload)
			except Exception as e:
				print(f"[hook] {name} error: {e}", file=sys.stderr)


class BailHook:
	"""Bail hook — first handler returning truthy value vetoes the action."""

	def __init__(self) -> None:
		self._handlers: list[tuple[str, BailHandler]] = []

	def tap(self, name: str, handler: BailHandler) -> None:
		self._handlers.append((name, handler))

	def call(self, payload: dict) -> str | bool | None:
		for name, handler in self._handlers:
			result = handler(payload)
			if result:
				return result
		return None


@dataclass
class HookBus:
	search_completed: SyncHook = field(default_factory=SyncHook)
	greet_before: BailHook = field(default_factory=BailHook)
	greet_after: SyncHook = field(default_factory=SyncHook)
	auth_state_changed: SyncHook = field(default_factory=SyncHook)
	browser_session_started: SyncHook = field(default_factory=SyncHook)
	browser_session_closed: SyncHook = field(default_factory=SyncHook)


def create_hook_bus() -> HookBus:
	"""Create a fresh HookBus instance."""
	return HookBus()
