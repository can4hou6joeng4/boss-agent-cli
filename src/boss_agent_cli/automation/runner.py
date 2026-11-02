"""Recruiter automation runner."""

from __future__ import annotations

from typing import Any

from boss_agent_cli.api.client import PlatformRiskError
from boss_agent_cli.api.request_budget import RequestBudgetExceeded, request_control
from boss_agent_cli.automation.adapters import RecruiterAutomationPlatform
from boss_agent_cli.automation.config import AutomationConfig
from boss_agent_cli.automation.events import make_event
from boss_agent_cli.automation.execution import process_pending, process_ref
from boss_agent_cli.automation.models import (
	AutomationEvent,
	AutomationMode,
	EventStatus,
	PlatformAction,
	RunReport,
)
from boss_agent_cli.automation.safety import SafetyGuard
from boss_agent_cli.automation.storage import AutomationStore


def run_automation_cycle(
	adapter: RecruiterAutomationPlatform,
	store: AutomationStore,
	config: AutomationConfig,
	*,
	platform: str,
	dry_run: bool,
	limit: int | None = None,
) -> RunReport:
	"""Run one recruiter automation cycle."""
	state = store.read_state()
	guard = SafetyGuard(config, state, dry_run=dry_run, store=store)
	events: list[AutomationEvent] = []
	if config.mode is AutomationMode.PAUSED:
		return RunReport(
			status="PAUSED",
			events=(),
			dry_run=dry_run,
			platform=platform,
			mode=config.mode,
		)

	try:
		startup_warning = guard.stop_reason() or adapter.detect_safety_warning()
		if startup_warning:
			return _stop_for_warning(store, guard, state, platform, config, dry_run, startup_warning)

		with request_control(check=guard.raise_if_stopped):
			adapter.ensure_session()
			if guard.stop_reason() is None:
				events.extend(process_pending(adapter, store, guard, platform, dry_run, state=state))
			if guard.stop_reason() is None:
				# 用 is None 而不是 or：limit=0 表示「本轮不处理任何会话」，
				# 而 `0 or max_per_tab` 会静默放大成默认的 20 个；负数在这里归一为 0，
				# 否则 `items[:负数]` 会反转成「去掉最后 N 个」。
				max_per_tab = config.max_per_tab if limit is None else max(0, limit)
				refs = adapter.scan_conversations(list(config.tabs), max_per_tab)
				for ref in refs:
					if guard.stop_reason() is not None:
						break
					if ref.diagnostic:
						event = make_event(
							platform,
							ref.id,
							PlatformAction.SKIP,
							EventStatus.STOPPED_BY_SAFETY,
							0.0,
							ref.diagnostic,
						)
						events.append(event)
						store.append_event(event)
						continue
					events.append(process_ref(adapter, store, config, guard, state, platform, dry_run, ref))
	except PlatformRiskError as exc:
		guard.open_circuit_breaker(exc.code)
		store.append_event(
			make_event(platform, "", PlatformAction.SKIP, EventStatus.CIRCUIT_BREAKER_OPEN, 0.0, exc.code)
		)
		raise
	except KeyboardInterrupt:
		guard.open_circuit_breaker("执行被中断，恢复前请先核对未完成的操作")
		raise
	except RequestBudgetExceeded as exc:
		guard.open_circuit_breaker(exc.code)
		store.append_event(
			make_event(platform, "", PlatformAction.SKIP, EventStatus.CIRCUIT_BREAKER_OPEN, 0.0, exc.code)
		)
		raise
	finally:
		store.write_state(state)
	return RunReport(
		status=EventStatus.CIRCUIT_BREAKER_OPEN.value if guard.stop_reason() is not None else "OK",
		events=tuple(events),
		dry_run=dry_run,
		platform=platform,
		mode=config.mode,
	)


def _stop_for_warning(
	store: AutomationStore,
	guard: SafetyGuard,
	state: dict[str, Any],
	platform: str,
	config: AutomationConfig,
	dry_run: bool,
	warning: str,
) -> RunReport:
	guard.open_circuit_breaker(warning)
	event = make_event(
		platform,
		"",
		PlatformAction.SKIP,
		EventStatus.CIRCUIT_BREAKER_OPEN,
		0.0,
		warning,
	)
	store.append_event(event)
	store.write_state(state)
	return RunReport(
		status=EventStatus.CIRCUIT_BREAKER_OPEN.value,
		events=(event,),
		dry_run=dry_run,
		platform=platform,
		mode=config.mode,
	)
