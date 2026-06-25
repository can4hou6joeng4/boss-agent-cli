"""Safety guard for autonomous recruiter actions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from boss_agent_cli.automation.config import AutomationConfig
from boss_agent_cli.automation.models import Decision, PlatformAction

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

	def __init__(self, config: AutomationConfig, state: dict, *, dry_run: bool) -> None:
		self._config = config
		self._state = state
		self._dry_run = dry_run
		self._run_actions = 0
		state.setdefault("autonomy", {})
		state.setdefault("safety", {})

	def before_action(self, decision: Decision, warning: str = "") -> SafetyDecision:
		if self._state.get("autonomy", {}).get("circuit_breaker", {}).get("open"):
			return SafetyDecision(False, "circuit breaker is open", circuit_breaker=True)
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
			self.open_circuit_breaker(f"max consecutive errors reached: {reason}")

	def open_circuit_breaker(self, reason: str) -> None:
		self._state.setdefault("autonomy", {})["circuit_breaker"] = {
			"open": True,
			"reason": reason,
			"opened_at": datetime.now(timezone.utc).isoformat(),
		}
