"""向导采集状态面板与导出截断证据（Issue #3053 / #3054）。"""

from __future__ import annotations

import io
from typing import Any

import pytest
from rich.console import Console

import boss_agent_cli.display as display
from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.output import Logger
from boss_agent_cli.wizard.actions import ActionContext, _crawl_status, _crawl_stop
from boss_agent_cli.wizard.renderer import render_run

RESUME = "boss --data-dir /tmp/demo crawl resume crawl-1"


def _run(
	goal: str,
	status: str,
	data: dict[str, Any],
	*,
	next_action: str | None = None,
	operator_actions: tuple[str, ...] = (),
) -> dict[str, Any]:
	last = {
		"data": data,
		"status": "completed" if status != "waiting_input" else "waiting_input",
		"next_action": next_action,
		"artifacts": [],
		"operator_actions": list(operator_actions),
	}
	return {
		"run_id": "wf-1",
		"role": "candidate",
		"goal": goal,
		"status": status,
		"params": {"inputs": {"query": "Python", "run_id": "crawl-1"}},
		"last_result": last,
	}


@pytest.fixture
def tty(monkeypatch):
	buffer = io.StringIO()
	console = Console(file=buffer, width=160, force_terminal=False, color_system=None)
	monkeypatch.setattr(display, "console", console)
	return buffer


def _render(run: dict[str, Any], tty: io.StringIO) -> str:
	render_run(run)
	return tty.getvalue()


# ── Issue #3053：查询型 run 的 completed 不能当成采集完成 ────────────


def test_crawl_status_running_stale_is_not_reported_as_completed(tty) -> None:
	run = _run(
		"crawl_status",
		"completed",
		{
			"run_id": "crawl-1",
			"status": "running",
			"query": "Python",
			"city_code": "101010100",
			"next_page": 3,
			"pages_completed": 2,
			"jobs_seen": 12,
			"details_completed": 0,
			"details_pending": 12,
			"running_stale": True,
			"executor_active": False,
			"checkpoint": {"next_page": 3, "resume_command": RESUME},
		},
		next_action=RESUME + " --background",
	)

	out = _render(run, tty)

	assert "采集已完成" not in out
	assert "分页查看" not in out
	assert "正在运行" in out
	assert "已有 12 个职位" in out
	assert "下一步" in out
	assert RESUME + " --background" in out


def test_crawl_status_risk_stopped_shows_unlock_guidance(tty) -> None:
	clean_hint = "确认风险已解除后，由用户执行 boss --data-dir /tmp/demo clean --risk-lock 解除本地阻断"
	run = _run(
		"crawl_status",
		"completed",
		{
			"run_id": "crawl-1",
			"status": "risk_stopped",
			"query": "Python",
			"jobs_seen": 12,
			"error": "环境异常（安全校验页）",
			"checkpoint": {
				"resume_command": RESUME,
				"resume_requires_operator_unlock": True,
			},
		},
		operator_actions=(
			"在原会话的 BOSS 直聘官方页面完成验证",
			clean_hint,
			f"完成人工恢复后，显式执行 {RESUME} 从原断点继续",
		),
	)

	out = _render(run, tty)

	assert "采集已完成" not in out
	assert "分页查看" not in out
	assert "风控暂停" in out
	assert "clean --risk-lock" in out
	assert RESUME in out


def test_crawl_stop_stop_requested_has_label_and_not_unknown(tty) -> None:
	run = _run(
		"crawl_stop",
		"completed",
		{"run_id": "crawl-1", "status": "stop_requested"},
	)

	out = _render(run, tty)

	assert "状态未知" not in out
	assert "停止请求已受理" in out


def test_crawl_status_queued_has_readable_label(tty) -> None:
	run = _run(
		"crawl_status",
		"completed",
		{"run_id": "crawl-1", "status": "queued", "jobs_seen": 0},
	)

	out = _render(run, tty)

	assert "状态未知" not in out
	assert "排队中" in out


def test_waiting_input_risk_guidance_not_duplicated(tty) -> None:
	run = _run(
		"crawl_start",
		"waiting_input",
		{
			"run_id": "crawl-1",
			"status": "risk_stopped",
			"error": "环境异常（安全校验页）",
			"jobs_seen": 12,
			"browser_kept_open": True,
		},
		next_action=RESUME,
		operator_actions=(
			"采集用 Chrome 已保持打开：若窗口里已是正常职位列表，直接选「继续采集」即可",
			f"命令行恢复：{RESUME}",
		),
	)

	out = _render(run, tty)

	assert out.count("命令行恢复") == 1
	assert RESUME in out


# ── Issue #3053：actions 侧风控状态查询要带解锁指引 ──────────────────


def test_crawl_status_action_risk_stopped_carries_unlock_guidance(
	tmp_path, monkeypatch
) -> None:
	payload = {
		"run_id": "crawl-1",
		"status": "risk_stopped",
		"jobs_seen": 12,
		"error": "环境异常",
		"checkpoint": {"resume_command": RESUME, "resume_requires_operator_unlock": True},
	}
	monkeypatch.setattr(
		"boss_agent_cli.wizard.actions.crawl_status",
		lambda cache, run_id, data_dir=None: dict(payload),
	)
	monkeypatch.setattr(
		"boss_agent_cli.wizard.actions._attach_wizard_jobs",
		lambda data, cache, run_id: data,
	)
	context = ActionContext(data_dir=tmp_path, platform="zhipin", role="candidate", logger=Logger())

	result = _crawl_status(context, {"run_id": "crawl-1"}, {})

	guidance = "\n".join(result.operator_actions)
	assert "clean --risk-lock" in guidance
	assert RESUME in guidance
	assert result.next_action is None


def test_crawl_stop_action_points_to_status_command(tmp_path) -> None:
	with CacheStore(tmp_path / "cache" / "boss_agent.db") as cache:
		cache.create_crawl_run("live-1", {"query": "Python"}, str(tmp_path / "crawl" / "runs" / "live-1"))
		cache.update_crawl_run("live-1", status="queued", next_page=1)
	context = ActionContext(data_dir=tmp_path, platform="zhipin", role="candidate", logger=Logger())

	stopped = _crawl_stop(context, {"run_id": "live-1"}, {})

	assert stopped.data == {"run_id": "live-1", "status": "stop_requested"}
	assert stopped.next_action is not None
	assert "crawl status live-1" in stopped.next_action
	assert str(tmp_path) in stopped.next_action


def test_crawl_stop_settled_run_also_points_to_status_command(tmp_path) -> None:
	with CacheStore(tmp_path / "cache" / "boss_agent.db") as cache:
		cache.create_crawl_run("live-1", {"query": "Python"}, str(tmp_path / "crawl" / "runs" / "live-1"))
	context = ActionContext(data_dir=tmp_path, platform="zhipin", role="candidate", logger=Logger())

	settled = _crawl_stop(context, {"run_id": "live-1"}, {})

	assert settled.data == {"run_id": "live-1", "status": "stopped", "stale_recovered": True}
	assert settled.next_action is not None
	assert "crawl status live-1" in settled.next_action


# ── Issue #3054：导出面板必须展示截断证据 ───────────────────────────


def test_export_truncated_panel_shows_evidence(tty) -> None:
	run = _run(
		"export",
		"completed",
		{
			"path": "/tmp/demo/exports/jobs.json",
			"format": "json",
			"count": 10,
			"requested": 50,
			"stop_reason": "page_budget_exhausted",
			"truncated": True,
		},
	)

	out = _render(run, tty)

	assert "导出结果被截断" in out
	assert "导出完成" not in out
	assert "10 / 请求 50" in out
	assert "页数上限" in out
	assert "缩小筛选条件" in out


def test_export_complete_panel_keeps_complete_wording(tty) -> None:
	run = _run(
		"export",
		"completed",
		{
			"path": "/tmp/demo/exports/jobs.json",
			"format": "json",
			"count": 10,
			"requested": 50,
			"stop_reason": "platform_has_more_false",
			"truncated": False,
		},
	)

	out = _render(run, tty)

	assert "导出完成" in out
	assert "被截断" not in out
	assert "页数上限" not in out
	# 请求条数仍是可核对的证据，但不再暗示结果不完整。
	assert "10 / 请求 50" in out
