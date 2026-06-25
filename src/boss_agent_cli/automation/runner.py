"""Recruiter automation runner."""

from __future__ import annotations

from typing import Any

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
	guard = SafetyGuard(config, state, dry_run=dry_run)
	events: list[AutomationEvent] = []
	if config.mode is AutomationMode.PAUSED:
		return RunReport(
			status="PAUSED",
			events=(),
			dry_run=dry_run,
			platform=platform,
			mode=config.mode,
		)

	startup_warning = adapter.detect_safety_warning()
	if startup_warning:
		return _stop_for_warning(
			store,
			guard,
			state,
			platform,
			config,
			dry_run,
			startup_warning,
		)

	adapter.ensure_session()
	events.extend(process_pending(adapter, store, guard, platform, dry_run))
	refs = adapter.scan_conversations(list(config.tabs), limit or config.max_per_tab)
	for ref in refs:
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
		events.append(
			process_ref(
				adapter,
				store,
				config,
				guard,
				state,
				platform,
				dry_run,
				ref,
			)
		)
	store.write_state(state)
	return RunReport(
		status="OK",
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
