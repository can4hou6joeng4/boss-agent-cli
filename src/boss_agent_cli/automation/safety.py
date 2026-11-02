"""Safety guard for autonomous recruiter actions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from boss_agent_cli.automation.config import AutomationConfig
from boss_agent_cli.automation.models import Decision, PlatformAction
from boss_agent_cli.automation.storage import AutomationStore
from boss_agent_cli.api.request_budget import RequestBudgetExceeded

_REAL_ACTIONS = {
	PlatformAction.SEND_QUESTIONNAIRE,
	PlatformAction.SEND_FOLLOW_UP,
	PlatformAction.EXCHANGE_CONTACT,
}


@dataclass(frozen=True, slots=True)
class SafetyDecision:
	allowed: bool
	reason: str = ""
	circuit_breaker: bool = False


class SafetyGuard:
	"""Run-scoped action quotas and circuit-breaker checks."""

	def __init__(
		self,
		config: AutomationConfig,
		state: dict[str, Any],
		*,
		dry_run: bool,
		store: AutomationStore | None = None,
	) -> None:
		self._config = config
		self._state = state
		self._dry_run = dry_run
		self._store = store
		self._run_actions = 0
		state.setdefault("autonomy", {})
		state.setdefault("safety", {})

	def stop_reason(self) -> str | None:
		if self._store is not None:
			breaker = self._store.read_state().get("autonomy", {}).get("circuit_breaker", {})
			if breaker.get("open"):
				self._state["autonomy"]["circuit_breaker"] = breaker
		breaker = self._state.get("autonomy", {}).get("circuit_breaker", {})
		return str(breaker.get("reason") or "熔断已打开") if breaker.get("open") else None

	def checkpoint(self) -> None:
		if self._store is not None:
			self._store.write_state(self._state)

	def raise_if_stopped(self) -> None:
		reason = self.stop_reason()
		if reason is not None:
			raise RequestBudgetExceeded(reason, code="WORKFLOW_STOPPED")

	def before_action(self, decision: Decision, warning: str = "") -> SafetyDecision:
		stop = self.stop_reason()
		if stop is not None:
			return SafetyDecision(False, stop, circuit_breaker=True)
		if warning:
			return SafetyDecision(
				False,
				f"platform safety warning detected: {warning}",
				circuit_breaker=True,
			)
		if decision.action not in _REAL_ACTIONS or self._dry_run:
			return SafetyDecision(True)
		if self._run_actions >= self._config.max_actions_per_run:
			return SafetyDecision(False, "max actions per run reached")
		return SafetyDecision(True)

	def after_action(self, decision: Decision) -> None:
		if decision.action in _REAL_ACTIONS and not self._dry_run:
			self._run_actions += 1
			self._state["safety"]["last_action_at"] = datetime.now(timezone.utc).isoformat()
			self._state["safety"]["consecutive_errors"] = 0

	def record_failure(self, reason: str) -> None:
		safety = self._state.setdefault("safety", {})
		current = int(safety.get("consecutive_errors", 0)) + 1
		safety["consecutive_errors"] = current
		safety["last_error"] = reason
		if current >= self._config.max_consecutive_errors:
			self.open_circuit_breaker(f"连续错误次数达到上限：{reason}")

	def open_circuit_breaker(self, reason: str) -> None:
		self._state.setdefault("autonomy", {})["circuit_breaker"] = {
			"open": True,
			"reason": reason,
			"opened_at": datetime.now(timezone.utc).isoformat(),
		}
		if self._store is not None:
			if not self._store.read_stop().get("open"):
				self._store.request_stop(reason)
			self.checkpoint()
