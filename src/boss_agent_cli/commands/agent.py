"""Recruiter automation commands."""

from __future__ import annotations

from dataclasses import asdict, replace
from typing import Any

import click

from boss_agent_cli.automation.adapters import build_automation_adapter
from boss_agent_cli.automation.config import automation_config_from_dict
from boss_agent_cli.automation.events import make_event, now_iso
from boss_agent_cli.automation.models import (
	AutomationMode,
	EventStatus,
	PlatformAction,
	RunReport,
)
from boss_agent_cli.automation.runner import run_automation_cycle
from boss_agent_cli.automation.storage import AutomationStore
from boss_agent_cli.display import handle_output


@click.group("agent")
def agent_group() -> None:
	"""招聘自动化入口。"""


@agent_group.command("run")
@click.option(
	"--dry-run",
	is_flag=True,
	default=False,
	help="只演练自动化决策，不执行真实平台动作",
)
@click.option("--limit", default=None, type=int, help="本轮最多处理多少个会话")
@click.pass_context
def run_cmd(ctx: click.Context, dry_run: bool, limit: int | None) -> None:
	"""运行一轮招聘自动化。"""
	report = _run_agent(ctx, dry_run=dry_run, limit=limit, force_mode=None)
	handle_output(ctx, "agent.run", _report_payload(report), hints=_agent_hints(ctx))


@agent_group.command("train")
@click.option(
	"--dry-run/--live",
	"dry_run",
	default=True,
	help="训练模式默认只写人审队列",
)
@click.option("--limit", default=None, type=int, help="本轮最多处理多少个会话")
@click.pass_context
def train_cmd(ctx: click.Context, dry_run: bool, limit: int | None) -> None:
	"""运行训练校准模式：自动判断，但动作进入人审。"""
	report = _run_agent(
		ctx,
		dry_run=dry_run,
		limit=limit,
		force_mode=AutomationMode.TRAINING,
	)
	handle_output(ctx, "agent.train", _report_payload(report), hints=_agent_hints(ctx))


@agent_group.group("review")
def review_group() -> None:
	"""人工复核队列。"""


@review_group.command("list")
@click.pass_context
def review_list_cmd(ctx: click.Context) -> None:
	store = AutomationStore(ctx.obj["data_dir"])
	handle_output(
		ctx,
		"agent.review.list",
		{"items": [asdict(item) for item in store.read_reviews()]},
		hints=_agent_hints(ctx),
	)


@review_group.command("approve")
@click.argument("review_id")
@click.pass_context
def review_approve_cmd(ctx: click.Context, review_id: str) -> None:
	"""批准一条人工复核动作，写入 pending 队列。"""
	store = AutomationStore(ctx.obj["data_dir"])
	pending = store.approve_review(review_id, now_iso())
	if pending is None:
		raise click.ClickException(f"review item not found or not reviewable: {review_id}")
	handle_output(
		ctx,
		"agent.review.approve",
		{"pending": asdict(pending)},
		hints=_agent_hints(ctx),
	)


@review_group.command("reject")
@click.argument("review_id")
@click.option("--reason", default="human-rejected", help="拒绝原因")
@click.pass_context
def review_reject_cmd(ctx: click.Context, review_id: str, reason: str) -> None:
	"""拒绝一条人工复核动作，并记录跳过事件。"""
	store = AutomationStore(ctx.obj["data_dir"])
	rejected = store.reject_review(review_id, reason, now_iso())
	if rejected is None:
		raise click.ClickException(f"review item not found or not reviewable: {review_id}")
	event = make_event(
		rejected.platform,
		rejected.candidate_key,
		PlatformAction(rejected.action),
		EventStatus.SKIPPED,
		rejected.confidence,
		f"human rejected: {reason}",
	)
	store.append_event(event)
	handle_output(
		ctx,
		"agent.review.reject",
		{"review": asdict(rejected), "event": asdict(event)},
		hints=_agent_hints(ctx),
	)


@agent_group.group("pending")
def pending_group() -> None:
	"""待执行动作队列。"""


@pending_group.command("list")
@click.pass_context
def pending_list_cmd(ctx: click.Context) -> None:
	store = AutomationStore(ctx.obj["data_dir"])
	handle_output(
		ctx,
		"agent.pending.list",
		{"items": [asdict(item) for item in store.read_pending()]},
		hints=_agent_hints(ctx),
	)


@agent_group.command("stats")
@click.pass_context
def agent_stats_cmd(ctx: click.Context) -> None:
	"""查看招聘自动化统计。"""
	store = AutomationStore(ctx.obj["data_dir"])
	handle_output(ctx, "agent.stats", store.stats(), hints=_agent_hints(ctx))


@agent_group.command("control")
@click.pass_context
def control_cmd(ctx: click.Context) -> None:
	"""返回本地控制台入口信息。"""
	handle_output(
		ctx,
		"agent.control",
		{
			"status": "available_via_cli",
			"note": (
				"首版控制台能力已统一到 agent CLI；"
				"Web 控制台将在后续接入同一状态目录"
			),
			"commands": [
				"boss agent run",
				"boss agent stats",
				"boss agent review list",
				"boss agent pending list",
			],
		},
		hints=_agent_hints(ctx),
	)


@agent_group.command("stop")
@click.option("--reason", default="manual-stop", help="熔断原因")
@click.pass_context
def stop_cmd(ctx: click.Context, reason: str) -> None:
	"""打开招聘自动化熔断。"""
	store = AutomationStore(ctx.obj["data_dir"])
	state = store.read_state()
	state.setdefault("autonomy", {})["circuit_breaker"] = {
		"open": True,
		"reason": reason,
	}
	store.write_state(state)
	handle_output(
		ctx,
		"agent.stop",
		{"status": "CIRCUIT_BREAKER_OPEN", "reason": reason},
		hints=_agent_hints(ctx),
	)


def _run_agent(
	ctx: click.Context,
	*,
	dry_run: bool,
	limit: int | None,
	force_mode: AutomationMode | None,
) -> RunReport:
	cfg = automation_config_from_dict((ctx.obj.get("config") or {}).get("automation"))
	if force_mode is not None:
		cfg = replace(cfg, mode=force_mode)
	platform = ctx.obj.get("platform") or "zhipin"
	store = AutomationStore(ctx.obj["data_dir"])
	adapter = build_automation_adapter(
		platform,
		data_dir=ctx.obj["data_dir"],
		delay=ctx.obj.get("delay", (1.5, 3.0)),
		cdp_url=ctx.obj.get("cdp_url"),
		live=not dry_run or platform == "zhilian",
	)
	return run_automation_cycle(
		adapter,
		store,
		cfg,
		platform=platform,
		dry_run=dry_run,
		limit=limit,
	)


def _report_payload(report: RunReport) -> dict[str, Any]:
	payload = asdict(report)
	payload["mode"] = report.mode.value
	return payload


def _agent_hints(ctx: click.Context) -> dict[str, Any]:
	platform = ctx.obj.get("platform") or "zhipin"
	prefix = "boss" if platform == "zhipin" else f"boss --platform {platform}"
	return {
		"next_actions": [
			f"{prefix} --role recruiter agent stats",
			f"{prefix} --role recruiter agent review list",
			f"{prefix} --role recruiter agent pending list",
		],
	}
