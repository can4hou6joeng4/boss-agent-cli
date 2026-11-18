"""批次二十四回归测试：Issue #1636–#1641。

全部测试离线运行；不调用平台 API、浏览器或真实网络。
"""

from __future__ import annotations

import asyncio
import copy
import sys
import tempfile
from pathlib import Path

import pytest

from boss_agent_cli.ai.config import AIConfigStore
from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.api.egress import evaluate_egress, timezone_region
from boss_agent_cli.display import open_file_command


def test_local_configure_rejects_invalid_base_url(tmp_path: Path) -> None:
	from click.testing import CliRunner

	from boss_agent_cli.main import cli

	result = CliRunner().invoke(
		cli,
		["--data-dir", str(tmp_path), "--json", "ai", "local", "configure", "--runtime", "ollama",
		 "--model", "audit-model", "--base-url", "not-a-url"],
	)
	assert result.exit_code == 1
	assert '"code": "INVALID_PARAM"' in result.output


def test_local_smoke_reports_invalid_persisted_base_url(tmp_path: Path) -> None:
	from click.testing import CliRunner

	from boss_agent_cli.main import cli

	store = AIConfigStore(tmp_path)
	store.save_config(
		ai_provider="ollama", ai_model="audit-model", ai_base_url="not-a-url", ai_temperature=0.3, ai_max_tokens=512
	)
	store.save_api_key("local")
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "ai", "local", "smoke"])
	assert result.exit_code == 1
	assert '"code": "CONFIG_INVALID"' in result.output


def _cache_job_with_at_prefix(data_dir: Path) -> None:
	with CacheStore(data_dir / "cache" / "boss_agent.db") as cache:
		cache.put_job_desc("job1", "@nonexistent-audit-jd.txt 负责后端开发，要求 Python")


@pytest.mark.parametrize("command", ["cover-letter", "resume-optimize"])
def test_cached_job_description_is_not_expanded_as_file(tmp_path: Path, command: str) -> None:
	from click.testing import CliRunner

	from boss_agent_cli.main import cli

	store = AIConfigStore(tmp_path)
	store.save_config(
		ai_provider="ollama", ai_model="audit-model", ai_base_url="http://127.0.0.1:11434/v1",
		ai_temperature=0.3, ai_max_tokens=512,
	)
	store.save_api_key("local")
	_cache_job_with_at_prefix(tmp_path)
	result = CliRunner().invoke(
		cli, ["--data-dir", str(tmp_path), "--json", "ai", command, "audit", "--job-id", "job1"]
	)
	assert result.exit_code == 1
	assert "nonexistent-audit-jd.txt" not in result.output


def test_non_us_americas_timezone_is_unknown() -> None:
	assert timezone_region("America/New_York") == "US"
	for timezone in ("America/Toronto", "America/Sao_Paulo", "America/Mexico_City"):
		assert timezone_region(timezone) == ""
		verdict = evaluate_egress(exit_country="US", accept_language="en-US", timezone=timezone)
		assert verdict.status == "unknown"


def test_known_us_timezone_still_ok() -> None:
	verdict = evaluate_egress(exit_country="US", accept_language="en-US", timezone="America/New_York")
	assert verdict.status == "ok"


class _Ctx:
	def __init__(self, data_dir: Path) -> None:
		self.data_dir = data_dir
		self.workflow_control = None

	def with_workflow_control(self, control: object) -> "_Ctx":
		other = copy.copy(self)
		other.workflow_control = control
		return other


@pytest.mark.parametrize("failure", [TimeoutError("timed out"), RuntimeError("boom")])
def test_wizard_write_marker_survives_generic_exceptions(failure: Exception) -> None:
	from boss_agent_cli.wizard.models import WorkflowPlan
	from boss_agent_cli.wizard.runner import WorkflowRunner
	from boss_agent_cli.wizard.store import WorkflowStore

	plan = WorkflowPlan(
		role="recruiter", platform="zhipin", goal="offline", inputs={},
		requested_steps=("recruiter_reply",), mode="headless",
	)
	data_dir = Path(tempfile.mkdtemp(prefix="wf-batch24-"))
	attempts: list[str] = []

	def first(ctx: _Ctx, inputs: object, prior: object) -> None:
		attempts.append("attempt")
		ctx.workflow_control.begin_write("boss hr chatmsg 42")
		raise failure

	def replay(ctx: _Ctx, inputs: object, prior: object) -> None:
		attempts.append("REPLAY")
		raise AssertionError("write replayed")

	with WorkflowStore(data_dir) as store:
		run = WorkflowRunner(store, {"recruiter_reply": first}).run(plan, _Ctx(data_dir), run_id="r", max_retries=0)
	assert run["error"]["replay_safe"] is False
	assert run["error"]["recoverable"] is False
	with WorkflowStore(data_dir) as store:
		resumed = WorkflowRunner(store, {"recruiter_reply": replay}).run(plan, _Ctx(data_dir), run_id="r")
	assert "REPLAY" not in attempts
	assert resumed["error"]["replay_safe"] is False


def test_mcp_config_empty_value_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
	from boss_agent_cli import mcp_server

	calls: list[list[str]] = []

	class Params:
		name = "boss_config"
		arguments = {"action": "set", "key": "export_dir", "value": ""}

	async def fake_run(*args: str, **kwargs: object) -> dict[str, object]:
		calls.append(list(args))
		return {"ok": True, "data": {"key": args[2], "value": None}}

	monkeypatch.setattr(mcp_server, "_run_boss_async", fake_run)
	result = asyncio.run(mcp_server.call_tool(None, Params()))
	assert result.is_error is False
	assert calls == [["config", "set", "export_dir", ""]]


@pytest.mark.parametrize("platform", ["win32", "darwin", "linux"])
def test_open_file_command_uses_platform_quoting(monkeypatch: pytest.MonkeyPatch, platform: str) -> None:
	monkeypatch.setattr(sys, "platform", platform)
	path = "/tmp/my report.html"
	if platform == "win32":
		assert open_file_command(r"C:\Users\bob\My Files\report.html") == (
			'cmd /c start "" "C:\\Users\\bob\\My Files\\report.html"'
		)
		assert "%TEMP%" in open_file_command(r"C:\Users\bob\%TEMP%\report.html")
	elif platform == "darwin":
		assert open_file_command(path) == "open '/tmp/my report.html'"
	else:
		assert open_file_command(path) == "xdg-open '/tmp/my report.html'"
