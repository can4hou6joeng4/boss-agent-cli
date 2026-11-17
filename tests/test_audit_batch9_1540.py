"""批次九审计修复（Issue #1540–#1543）。

四条都是「控制路径按真值判断 / 输出路径按 eager 预检」导致的确定行为缺陷：

- #1540：wizard 的 crawl 恢复在确认无剩余工作之前就要求登录 / crawl 依赖；
- #1541：`resume export --format json -o ""` 返回 ok=true 但不写文件；
- #1542：`resume export` 的默认 html/pdf 输出名绕过存储层的文件名清洗；
- #1543：`BROWSER_KERNEL_MISSING` / `CRAWL_UNAVAILABLE` 仍进入自动重试。
"""

import json
from pathlib import Path
from types import SimpleNamespace

from click.testing import CliRunner
import pytest

from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.crawler.controls import CrawlRuntimeUnavailable
from boss_agent_cli.crawler.service import CrawlSettings
from boss_agent_cli.main import cli
from boss_agent_cli.output import Logger
from boss_agent_cli.resume.store import _safe_filename
from boss_agent_cli.schema.error_codes import ERROR_CODES
from boss_agent_cli.wizard.actions import ActionContext, _crawl_unavailable_error
from boss_agent_cli.wizard.runner import WorkflowActionError, _browser_kernel_missing_error


def _run(tmp_path: Path, *args: str):
	return CliRunner().invoke(cli, ["--json", "--data-dir", str(tmp_path), *args])


def _envelope(result) -> dict:
	return json.loads(result.stdout)


# ── #1541 / #1542：resume export ─────────────────────────────────────


def _init_resume(tmp_path: Path, name: str) -> None:
	result = _run(tmp_path, "resume", "init", "--name", name)
	assert result.exit_code == 0, result.stdout


def test_resume_export_json_rejects_empty_output(tmp_path):
	"""显式空 -o 不能静默跳过写盘却返回 ok=true（Issue #1541）。"""
	_init_resume(tmp_path, "default")

	result = _run(tmp_path, "resume", "export", "default", "--format", "json", "-o", "")

	assert result.exit_code == 1, result.stdout
	error = _envelope(result)["error"]
	assert error["code"] == "INVALID_PARAM"
	assert "-o" in error["message"]


def test_resume_export_json_omitted_output_keeps_inline_result(tmp_path):
	"""省略 -o 仍返回信封内简历内容，不写文件。"""
	_init_resume(tmp_path, "default")

	result = _run(tmp_path, "resume", "export", "default", "--format", "json")

	assert result.exit_code == 0, result.stdout
	payload = _envelope(result)
	assert payload["ok"] is True
	assert payload["data"]["data"]["name"] == "default"


def test_resume_export_json_writes_file_when_output_given(tmp_path):
	_init_resume(tmp_path, "default")
	target = tmp_path / "out" / "resume.json"

	result = _run(tmp_path, "resume", "export", "default", "--format", "json", "-o", str(target))

	assert result.exit_code == 0, result.stdout
	assert target.is_file()


@pytest.mark.parametrize("name", ["../x", "..", "CON"])
def test_resume_export_default_path_sanitizes_name(tmp_path, monkeypatch, name):
	"""默认输出名必须与存储层共用清洗：不能写到 cwd 之外或保留设备名（Issue #1542）。"""
	_init_resume(tmp_path, name)

	workdir = tmp_path / "cwd"
	workdir.mkdir()
	monkeypatch.chdir(workdir)

	result = _run(tmp_path, "resume", "export", name, "--format", "html")

	assert result.exit_code == 0, result.stdout
	written = Path(_envelope(result)["data"]["path"])
	assert written.parent == workdir.resolve(), written
	# 与存储层共用同一份清洗：默认输出名等于清洗后的简历名，不会带出目录部分。
	assert written.name == f"{_safe_filename(name)}.html"


# ── #1540：已完成 run 不要求登录 / crawl 依赖 ────────────────────────


def _seed_run(tmp_path: Path, run_id: str, *, status: str, pages: int) -> None:
	with CacheStore(tmp_path / "cache" / "boss_agent.db") as cache:
		cache.create_crawl_run(
			run_id,
			CrawlSettings(
				query="Python",
				city_code="101010100",
				pages=pages,
				with_detail=False,
				profile_path=tmp_path / "crawl" / "chrome-profile",
				chrome_path=None,
				cdp_port=9333,
				hook_profile="none",
			).as_dict(),
			str(tmp_path / "crawl" / "runs" / run_id),
			status=status,
		)
		if status == "completed":
			cache.update_crawl_run(run_id, status="completed", next_page=pages, list_finished=True)


def _run_wizard(tmp_path: Path, goal: str, inputs: dict):
	return _run(
		tmp_path,
		"wizard",
		"--input-json",
		json.dumps({"role": "candidate", "platform": "zhipin", "goal": goal, "inputs": inputs}),
	)


def test_wizard_crawl_resume_of_completed_run_needs_no_login(tmp_path):
	"""已完成的 run 应在 preflight 之前返回既有结果（Issue #1412 / #1540）。

	没有登录态、也没有安装 crawl 依赖（openpyxl 缺失）时，已完成 run 的恢复
	仍必须成功——这正是 CLI `crawl resume` 的行为。
	"""
	_seed_run(tmp_path, "crawl-done", status="completed", pages=3)

	result = _run_wizard(tmp_path, "crawl_resume", {"run_id": "crawl-done"})

	assert result.exit_code == 0, result.stdout
	payload = _envelope(result)
	assert payload["ok"] is True
	assert payload["data"]["status"] == "completed"
	step_result = payload["data"]["steps"][0]["result"]
	assert step_result["status"] == "completed"
	assert step_result["data"]["status"] == "completed"


def test_wizard_crawl_resume_of_running_run_still_requires_login(tmp_path):
	"""有剩余工作但登录态缺失：仍必须在真正执行前报 AUTH_REQUIRED。"""
	_seed_run(tmp_path, "crawl-running", status="running", pages=5)

	result = _run_wizard(tmp_path, "crawl_resume", {"run_id": "crawl-running"})

	assert result.exit_code == 1, result.stdout
	assert _envelope(result)["error"]["code"] == "AUTH_REQUIRED"


def test_wizard_crawl_resume_missing_run_reports_not_found_before_login(tmp_path):
	"""run_id 写错应先报 CRAWL_RUN_NOT_FOUND，而不是先把用户送去登录。"""
	result = _run_wizard(tmp_path, "crawl_resume", {"run_id": "missing"})

	assert result.exit_code == 1, result.stdout
	assert _envelope(result)["error"]["code"] == "CRAWL_RUN_NOT_FOUND"


# ── #1543：确定性失败不自动重试 ────────────────────────────────────


def test_browser_kernel_missing_is_not_auto_retried():
	"""内核缺失是确定性失败：同一次运行内重试只会再起一次浏览器。"""
	error = _browser_kernel_missing_error(RuntimeError("内核缺失"), SimpleNamespace(platform="zhipin"), None)

	assert isinstance(error, WorkflowActionError)
	assert error.code == "BROWSER_KERNEL_MISSING"
	assert error.auto_retry is False
	# 浏览器根本没有启动，不存在平台副作用。
	assert error.replay_safe is True


def test_crawl_unavailable_is_not_auto_retried():
	"""crawl 可选依赖没装：重试或恢复只会复现，不进入自动重试。"""
	context = ActionContext(data_dir=Path("/tmp/data"), platform="zhipin", role="candidate", logger=Logger("error"))

	error = _crawl_unavailable_error(context, CrawlRuntimeUnavailable("未安装"))

	assert error.code == "CRAWL_UNAVAILABLE"
	assert error.recoverable is ERROR_CODES["CRAWL_UNAVAILABLE"]["recoverable"]
	assert error.auto_retry is False
	assert "crawl configure" in (error.recovery_action or "")
