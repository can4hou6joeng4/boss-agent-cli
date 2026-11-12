"""CLI-layer contract tests for the `boss crawl` command group.

`tests/test_crawl.py` covers the crawler service and hook machinery. This file covers
the command layer itself — argument validation, capability entry, and the JSON
envelope each subcommand emits — which previously shipped almost entirely untested
(`commands/crawl.py` sat at 49% line coverage).

Every test drives the real Click entry point through `CliRunner` and asserts on the
stdout envelope, so a regression in `code` / `data` / `hints` fails here rather than in
an agent's parser.
"""

import json
import shlex
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.commands.crawl import _city_code, _launch_background_resume, _save_crawl_config
from boss_agent_cli.crawler.service import CrawlSettings
from boss_agent_cli.main import cli


def _enable_research(runner: CliRunner, tmp_path: Path) -> None:
	result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "config", "set", "operating_mode", "research"])
	assert result.exit_code == 0, result.output


def _seed_login(tmp_path: Path) -> None:
	"""Crawl injects boss-login cookies into its isolated Chrome profile."""
	from boss_agent_cli.auth.token_store import TokenStore

	TokenStore(tmp_path / "auth").save(
		{
			"cookies": {"wt2": "test-wt2", "wbg": "1", "zp_at": "1"},
			"user_agent": "test-agent",
			"stoken": "",
		}
	)


def _invoke(runner: CliRunner, tmp_path: Path, *args: str):
	_seed_login(tmp_path)
	return runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", *args])


def _envelope(result) -> dict:
	return json.loads(result.output)


def _assert_state_commands(actions: list[str], data_dir: Path, *commands: str) -> None:
	assert [shlex.split(action) for action in actions] == [
		["boss", "--data-dir", str(data_dir.resolve()), *shlex.split(command)] for command in commands
	]


def _seed_run(tmp_path: Path, run_id: str = "run-1", *, status: str = "completed", jobs: int = 2) -> list[str]:
	"""Create a crawl run plus job rows straight through CacheStore.

	Returns the generated selectors, which is what `crawl results` exposes and what
	`crawl shortlist --selector` consumes.
	"""
	db_path = tmp_path / "cache" / "boss_agent.db"
	db_path.parent.mkdir(parents=True, exist_ok=True)
	selectors: list[str] = []
	with CacheStore(db_path) as cache:
		cache.create_crawl_run(
			run_id,
			{"query": "AI", "city_code": "101210100", "pages": 5},
			str(tmp_path / "crawl" / run_id),
			status=status,
		)
		for index in range(jobs):
			job_key = f"job-{index}"
			cache.put_crawl_job(
				run_id,
				job_key,
				page_no=index + 1,
				payload={
					"encryptJobId": job_key,
					"securityId": f"sec-{index}",
					"jobName": "AI 工程师",
					"salaryDesc": "20-30K",
					"cityName": "杭州",
					"brandName": "测试公司",
				},
				detail_done=index == 0,
			)
			stored = cache.get_crawl_job(run_id, job_key)
			assert stored is not None
			selectors.append(str(stored["selector"]))
	return selectors


# ── 纯函数 ────────────────────────────────────────────────────────────


def test_city_code_accepts_name_and_numeric_code_and_rejects_others():
	assert _city_code("杭州") == "101210100"
	assert _city_code("101210100") == "101210100"
	with pytest.raises(ValueError, match="city 必须是支持的城市名称或数字城市代码"):
		_city_code("不存在的城市")


def test_save_crawl_config_merges_and_drops_none(tmp_path):
	first = _save_crawl_config(tmp_path, {"cdp_port": 9333, "max_requests": None})
	assert first == {"cdp_port": 9333}

	second = _save_crawl_config(tmp_path, {"max_requests": 30, "cdp_port": None})
	assert second == {"cdp_port": 9333, "max_requests": 30}, "None 值不应清掉已有配置"

	saved = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
	assert saved["crawl"] == {"cdp_port": 9333, "max_requests": 30}


def test_save_crawl_config_survives_corrupted_config_file(tmp_path):
	(tmp_path / "config.json").write_text("{ not json", encoding="utf-8")
	assert _save_crawl_config(tmp_path, {"cdp_port": 9444}) == {"cdp_port": 9444}


# ── configure ─────────────────────────────────────────────────────────


def test_configure_without_any_option_is_invalid_param(tmp_path):
	result = _invoke(CliRunner(), tmp_path, "crawl", "configure")
	assert result.exit_code == 1
	parsed = _envelope(result)
	assert parsed["error"]["code"] == "INVALID_PARAM"
	# 没给配置项时也要有可执行的下一步：列出可配置项。
	assert parsed["hints"]["next_actions"] == ["boss crawl configure --help"]
	assert "boss crawl configure --help" in parsed["error"]["recovery_action"]


def test_configure_persists_budget_overrides(tmp_path):
	result = _invoke(
		CliRunner(), tmp_path, "crawl", "configure",
		"--port", "9222", "--max-requests", "12", "--max-details", "8", "--max-seconds", "60", "--max-retries", "0",
	)
	assert result.exit_code == 0, result.output
	payload = _envelope(result)
	assert payload["ok"] is True
	assert payload["data"]["crawl"] == {
		"cdp_port": 9222,
		"max_requests": 12,
		"max_details": 8,
		"max_seconds": 60,
		"max_retries": 0,
	}
	saved = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
	assert saved["crawl"]["max_retries"] == 0


# ── 运行与参数校验 ────────────────────────────────────────────────────
# assisted/research 均可进入业务层；这里只覆盖 service 结果映射与参数校验。


def test_run_rejects_unknown_city_before_touching_the_browser(tmp_path, monkeypatch):
	def _explode(settings):  # pragma: no cover - 不应被调用
		raise AssertionError("非法城市不应进入 transport 构造")

	monkeypatch.setattr("boss_agent_cli.commands.crawl._transport_factory", _explode)
	runner = CliRunner()
	_enable_research(runner, tmp_path)
	result = _invoke(runner, tmp_path, "crawl", "run", "AI", "--city", "火星")
	assert result.exit_code == 1
	error = _envelope(result)["error"]
	assert error["code"] == "INVALID_PARAM"
	assert "城市" in error["message"]
	assert "boss crawl configure --help" in error["recovery_action"]
	assert _envelope(result)["hints"]["next_actions"] == ["boss crawl configure --help"]


@pytest.mark.skipif(sys.platform == "win32", reason="Windows 的 Path.expanduser 不解析 ~user")
def test_run_rejects_unknown_home_hook_dir_before_touching_the_browser(tmp_path, monkeypatch):
	"""`--hook-dir "~未知用户/…"` 是参数问题，不能兜成可重试的网络错误。"""

	def _explode(settings):  # pragma: no cover - 不应被调用
		raise AssertionError("非法 --hook-dir 不应进入 transport 构造")

	monkeypatch.setattr("boss_agent_cli.commands.crawl._transport_factory", _explode)
	runner = CliRunner()
	_enable_research(runner, tmp_path)
	result = _invoke(
		runner,
		tmp_path,
		"crawl",
		"run",
		"AI",
		"--city",
		"杭州",
		"--hook-profile",
		"screenshot-full",
		"--hook-dir",
		"~boss_cli_no_such_user_zzz/hooks",
	)
	assert result.exit_code == 1
	error = _envelope(result)["error"]
	assert error["code"] == "INVALID_PARAM"
	assert "无法展开 --hook-dir 的家目录写法" in error["message"]
	assert error["recoverable"] is False


def test_run_honors_configured_zero_max_retries(tmp_path, monkeypatch):
	"""configure 接受 --max-retries 0；运行时不能被 `or` 悄悄改回 1。"""
	from boss_agent_cli.commands import crawl as crawl_module

	captured = {}

	class _Outcome:
		run_id = "run-ok"
		status = "completed"

		def as_dict(self):
			return {"run_id": self.run_id, "status": self.status, "output_paths": {}}

	def _capture(self, settings):
		captured["settings"] = settings
		return _Outcome()

	monkeypatch.setattr(crawl_module.CrawlService, "create_and_run", _capture)
	runner = CliRunner()
	_enable_research(runner, tmp_path)
	configured = _invoke(runner, tmp_path, "crawl", "configure", "--max-retries", "0")
	assert configured.exit_code == 0, configured.output
	result = _invoke(runner, tmp_path, "crawl", "run", "AI", "--city", "杭州")
	assert result.exit_code == 0, result.output
	assert captured["settings"].max_retries == 0


@pytest.mark.parametrize(
	("crawl_config", "field"),
	[
		({"max_requests": [1]}, "max_requests"),
		({"max_requests": 0}, "max_requests"),
		({"max_retries": -1}, "max_retries"),
		({"cdp_port": "abc"}, "cdp_port"),
		({"cdp_port": 70000}, "cdp_port"),
		({"chrome_path": 5}, "chrome_path"),
	],
)
def test_run_rejects_invalid_crawl_config(tmp_path, crawl_config, field):
	"""手工 / config set 写入的错误值要按 INVALID_PARAM 返回，而不是 INTERNAL_ERROR。"""
	_save_crawl_config(tmp_path, crawl_config)
	runner = CliRunner()
	_enable_research(runner, tmp_path)
	result = _invoke(runner, tmp_path, "crawl", "run", "AI", "--city", "杭州")
	assert result.exit_code == 1
	error = _envelope(result)["error"]
	assert error["code"] == "INVALID_PARAM"
	assert field in error["message"]


def test_run_maps_service_runtime_error_to_crawl_unavailable(tmp_path, monkeypatch):
	from boss_agent_cli.commands import crawl as crawl_module

	def _raise(self, settings):
		raise RuntimeError("DrissionPage 未安装")

	monkeypatch.setattr(crawl_module.CrawlService, "create_and_run", _raise)
	runner = CliRunner()
	_enable_research(runner, tmp_path)
	result = _invoke(runner, tmp_path, "crawl", "run", "AI", "--city", "杭州")
	assert result.exit_code == 1
	error = _envelope(result)["error"]
	assert error["code"] == "CRAWL_UNAVAILABLE"
	assert error["recoverable"] is True
	assert "crawl configure" in error["recovery_action"]


def test_run_emits_resume_hint_when_run_stops_early(tmp_path, monkeypatch):
	from boss_agent_cli.commands import crawl as crawl_module

	class _Outcome:
		run_id = "run-stopped"
		status = "stopped"

		def as_dict(self):
			return {"run_id": self.run_id, "status": self.status, "output_paths": {}}

	monkeypatch.setattr(crawl_module.CrawlService, "create_and_run", lambda self, settings: _Outcome())
	runner = CliRunner()
	_enable_research(runner, tmp_path)
	result = _invoke(runner, tmp_path, "crawl", "run", "AI", "--city", "杭州")
	assert result.exit_code == 0, result.output
	payload = _envelope(result)
	assert payload["data"]["status"] == "stopped"
	_assert_state_commands(payload["hints"]["next_actions"], tmp_path, "crawl resume run-stopped")


def test_run_hints_point_at_output_files_when_completed(tmp_path, monkeypatch):
	from boss_agent_cli.commands import crawl as crawl_module

	class _Outcome:
		run_id = "run-done"
		status = "completed"

		def as_dict(self):
			return {"run_id": self.run_id, "status": self.status, "output_paths": {"json": "a.json"}}

	monkeypatch.setattr(crawl_module.CrawlService, "create_and_run", lambda self, settings: _Outcome())
	runner = CliRunner()
	_enable_research(runner, tmp_path)
	result = _invoke(runner, tmp_path, "crawl", "run", "AI", "--city", "杭州")
	assert result.exit_code == 0, result.output
	payload = _envelope(result)
	assert "output_paths" in payload["hints"]["operator_actions"][0]
	# 跑完后唯一能把结果落地的下一步就是按 run_id 读；空列表会让 Agent 自己猜命令。
	_assert_state_commands(payload["hints"]["next_actions"], tmp_path, "crawl results run-done")


# ── start ─────────────────────────────────────────────────────────────


def test_start_hints_cover_status_results_and_stop(tmp_path, monkeypatch):
	"""启动后 Agent 要能查看进度、取结果、必要时中止；终端 next_steps 与信封同口径。"""
	monkeypatch.setattr("boss_agent_cli.commands.crawl._launch_background_resume", lambda data_dir, run_id, **kwargs: None)
	runner = CliRunner()
	_enable_research(runner, tmp_path)
	result = _invoke(runner, tmp_path, "crawl", "start", "AI", "--city", "杭州")
	assert result.exit_code == 0, result.output
	run_id = _envelope(result)["data"]["run_id"]
	_assert_state_commands(
		_envelope(result)["hints"]["next_actions"], tmp_path,
		f"crawl status {run_id}", f"crawl results {run_id}", f"crawl stop {run_id}",
	)
	_assert_state_commands([_envelope(result)["data"]["checkpoint"]["resume_command"]], tmp_path, f"crawl resume {run_id}")


def test_start_marks_run_stopped_when_background_launch_fails(tmp_path, monkeypatch):
	def _fail(data_dir, run_id, **kwargs):
		raise OSError("no such executable")

	monkeypatch.setattr("boss_agent_cli.commands.crawl._launch_background_resume", _fail)
	runner = CliRunner()
	_enable_research(runner, tmp_path)
	result = _invoke(runner, tmp_path, "crawl", "start", "AI", "--city", "杭州")
	assert result.exit_code == 1
	error = _envelope(result)["error"]
	assert error["code"] == "CRAWL_UNAVAILABLE"
	assert error["recoverable"] is True
	# operating_mode 已不再限制能力，恢复指引不能要求用户改它。
	assert "operating_mode" not in error["recovery_action"]

	with CacheStore(tmp_path / "cache" / "boss_agent.db") as cache:
		run_ids = _all_run_ids(tmp_path)
		runs = [cache.get_crawl_run(run_id) for run_id in run_ids]
	assert runs and runs[0] is not None
	assert f"crawl resume {run_ids[0]}" in error["recovery_action"]
	assert str(tmp_path.resolve()) in error["recovery_action"]
	assert runs[0]["status"] == "stopped", "后台拉起失败必须把 run 落成 stopped，否则会留下永久 queued 的幽灵任务"
	assert "无法启动后台任务" in runs[0]["error"]


def _all_run_ids(tmp_path: Path) -> list[str]:
	with sqlite3.connect(tmp_path / "cache" / "boss_agent.db") as conn:
		return [str(row[0]) for row in conn.execute("SELECT run_id FROM crawl_runs")]


@pytest.mark.parametrize("query", ["", "   "])
def test_start_rejects_blank_query_without_creating_a_run(tmp_path, query):
	"""空关键词等于不筛选：会抓城市默认列表，消耗预算并放大风控面。"""
	runner = CliRunner()
	_enable_research(runner, tmp_path)
	result = _invoke(runner, tmp_path, "crawl", "start", query, "--city", "杭州")
	assert result.exit_code == 1
	error = _envelope(result)["error"]
	assert error["code"] == "INVALID_PARAM"
	assert "关键词" in error["message"]
	assert "boss crawl configure --help" in error["recovery_action"]
	db = tmp_path / "cache" / "boss_agent.db"
	if db.exists():
		assert _all_run_ids(tmp_path) == [], "空关键词不该留下 run 或拉起后台进程"


def test_run_rejects_blank_query(tmp_path):
	runner = CliRunner()
	_enable_research(runner, tmp_path)
	result = _invoke(runner, tmp_path, "crawl", "run", "", "--city", "杭州")
	assert result.exit_code == 1
	assert _envelope(result)["error"]["code"] == "INVALID_PARAM"
	assert "关键词" in _envelope(result)["error"]["message"]
	assert _envelope(result)["hints"]["next_actions"] == ["boss crawl configure --help"]


# ── resume ────────────────────────────────────────────────────────────


def test_resume_background_reports_run_not_found(tmp_path):
	runner = CliRunner()
	_enable_research(runner, tmp_path)
	result = _invoke(runner, tmp_path, "crawl", "resume", "missing", "--background")
	assert result.exit_code == 1
	assert _envelope(result)["error"]["code"] == "CRAWL_RUN_NOT_FOUND"


def test_resume_missing_run_reports_not_found_before_requiring_login(tmp_path):
	"""run 记录在本地：ID 不存在时不该先把人送去登录。

	`_run_service` 会先注入登录 Cookie；登录态缺失时，一个写错的 run_id 会报
	``AUTH_REQUIRED``，登录回来才发现任务不存在。
	"""
	runner = CliRunner()
	_enable_research(runner, tmp_path)

	result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "crawl", "resume", "missing"])

	assert result.exit_code == 1
	payload = _envelope(result)
	assert payload["error"]["code"] == "CRAWL_RUN_NOT_FOUND"
	assert "missing" in payload["error"]["message"]
	_assert_state_commands(payload["hints"]["next_actions"], tmp_path, "crawl start <query>", "crawl status <run_id>")


def test_resume_existing_run_without_login_still_requires_auth(tmp_path):
	"""存在性检查不能把「已登录才可恢复」这条前置条件吞掉。"""
	_seed_run(tmp_path)
	runner = CliRunner()
	_enable_research(runner, tmp_path)

	result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "crawl", "resume", "run-1"])

	assert result.exit_code == 1
	assert _envelope(result)["error"]["code"] == "AUTH_REQUIRED"


def test_empty_run_id_is_invalid_param_not_run_not_found(tmp_path):
	"""空 run_id 是 argv 少传了值，不是「任务不存在」。

	原样查缓存会报 ``CRAWL_RUN_NOT_FOUND``「未找到 crawl run: 」（冒号后面空着），
	Agent 会以为 run 丢了而重建；``crawl resume ""`` 更会因为先做登录校验而报
	``AUTH_REQUIRED``，把参数写错的人送去重新登录。
	"""
	runner = CliRunner()
	for command in ("status", "stop", "results", "resume"):
		result = _invoke(runner, tmp_path, "crawl", command, "")
		assert result.exit_code == 1, command
		payload = _envelope(result)
		assert payload["error"]["code"] == "INVALID_PARAM", command
		assert "run_id 不能为空" in payload["error"]["message"], command
		_assert_state_commands(payload["hints"]["next_actions"], tmp_path, "crawl start <query>", "crawl status <run_id>")
		# 恢复动作要说明「补上已存在的 run_id」或新建任务，而不是「修正参数」。
		assert "run_id" in payload["error"]["recovery_action"], command


def test_resume_background_relaunches_stopped_run(tmp_path, monkeypatch):
	launched: list[tuple[str, int | None, bool]] = []
	monkeypatch.setattr(
		"boss_agent_cli.commands.crawl._launch_background_resume",
		lambda data_dir, run_id, **kwargs: launched.append((run_id, kwargs.get("pages"), kwargs.get("with_detail", False))),
	)
	_seed_run(tmp_path, "run-stopped", status="stopped")
	runner = CliRunner()
	_enable_research(runner, tmp_path)
	result = _invoke(runner, tmp_path, "crawl", "resume", "run-stopped", "--background", "--pages", "3", "--with-detail")
	assert result.exit_code == 0, result.output
	payload = _envelope(result)
	assert payload["data"] == {"run_id": "run-stopped", "status": "queued", "background": True}
	assert launched == [("run-stopped", 3, True)]


def test_resume_background_does_not_relaunch_an_already_running_job(tmp_path, monkeypatch):
	launched: list[str] = []
	monkeypatch.setattr(
		"boss_agent_cli.commands.crawl._launch_background_resume",
		lambda data_dir, run_id, **kwargs: launched.append(run_id),
	)
	_seed_run(tmp_path, "run-active", status="running")
	runner = CliRunner()
	_enable_research(runner, tmp_path)
	result = _invoke(runner, tmp_path, "crawl", "resume", "run-active", "--background")
	assert result.exit_code == 0, result.output
	assert _envelope(result)["data"]["status"] == "running"
	assert launched == [], "已在运行的任务重复拉起会产生并发采集，属风控风险"


def test_resume_background_returns_completed_without_relaunch(tmp_path, monkeypatch):
	launched: list[str] = []
	monkeypatch.setattr(
		"boss_agent_cli.commands.crawl._launch_background_resume",
		lambda data_dir, run_id, **kwargs: launched.append(run_id),
	)
	_seed_run(tmp_path, "run-done", status="completed")
	runner = CliRunner()
	_enable_research(runner, tmp_path)
	result = _invoke(runner, tmp_path, "crawl", "resume", "run-done", "--background")
	assert result.exit_code == 0, result.output
	payload = _envelope(result)
	assert payload["data"] == {"run_id": "run-done", "status": "completed", "background": True}
	_assert_state_commands(payload["hints"]["next_actions"], tmp_path, "crawl results run-done")
	assert launched == [], "已完成的 run 没有可恢复的工作，拉起后台进程只会立即退出并谎报 queued"


def test_resume_background_relaunches_completed_run_when_detail_pending(tmp_path, monkeypatch):
	launched: list[tuple[str, bool]] = []
	monkeypatch.setattr(
		"boss_agent_cli.commands.crawl._launch_background_resume",
		lambda data_dir, run_id, **kwargs: launched.append((run_id, bool(kwargs.get("with_detail")))),
	)
	_seed_run(tmp_path, "run-done", status="completed", jobs=2)
	runner = CliRunner()
	_enable_research(runner, tmp_path)
	result = _invoke(runner, tmp_path, "crawl", "resume", "run-done", "--background", "--with-detail")
	assert result.exit_code == 0, result.output
	assert _envelope(result)["data"]["status"] == "queued"
	assert launched == [("run-done", True)], "还有待补详情时必须真正恢复"


def test_resume_background_relaunches_completed_run_when_more_pages_requested(tmp_path, monkeypatch):
	launched: list[tuple[str, int | None]] = []
	monkeypatch.setattr(
		"boss_agent_cli.commands.crawl._launch_background_resume",
		lambda data_dir, run_id, **kwargs: launched.append((run_id, kwargs.get("pages"))),
	)
	_seed_run(tmp_path, "run-done", status="completed")
	runner = CliRunner()
	_enable_research(runner, tmp_path)
	result = _invoke(runner, tmp_path, "crawl", "resume", "run-done", "--background", "--pages", "10")
	assert result.exit_code == 0, result.output
	assert _envelope(result)["data"]["status"] == "queued"
	assert launched == [("run-done", 10)], "列表未采完且请求了更多页时必须真正恢复"


# ── status / stop / results ───────────────────────────────────────────


@pytest.mark.parametrize(
	"directory_name, run_id",
	[("custom-state", "run-1"), ("state 'one'; $STATE", "run 'one'; $RUN")],
)
def test_task_actions_stay_in_original_data_directory(tmp_path, monkeypatch, directory_name, run_id):
	"""默认目录中即使有同名任务，结果、停止和状态链仍要指向原任务。"""
	data_dir = tmp_path / directory_name
	default_home = tmp_path / "default-home"
	default_dir = default_home / ".boss-agent"
	_seed_run(data_dir, run_id, status="running", jobs=2)
	_seed_run(default_dir, run_id, status="running", jobs=1)
	monkeypatch.setenv("HOME", str(default_home))
	runner = CliRunner()
	result = runner.invoke(cli, ["--data-dir", str(data_dir), "--json", "crawl", "status", run_id])
	assert result.exit_code == 0, result.output
	payload = _envelope(result)
	results_action, stop_action = payload["hints"]["next_actions"]
	results = runner.invoke(cli, shlex.split(results_action)[1:])
	assert results.exit_code == 0, results.output
	assert _envelope(results)["data"]["count"] == 2
	stopped = runner.invoke(cli, shlex.split(stop_action)[1:])
	assert stopped.exit_code == 0, stopped.output
	status_action = _envelope(stopped)["hints"]["next_actions"][0]
	status = runner.invoke(cli, shlex.split(status_action)[1:])
	assert status.exit_code == 0, status.output
	assert _envelope(status)["data"]["jobs_seen"] == 2
	assert shlex.split(payload["data"]["checkpoint"]["resume_command"]) == [
		"boss", "--data-dir", str(data_dir.resolve()), "crawl", "resume", run_id,
	]
	with CacheStore(data_dir / "cache" / "boss_agent.db") as cache:
		assert cache.get_crawl_run(run_id)["stop_requested"] is True
	with CacheStore(default_dir / "cache" / "boss_agent.db") as cache:
		assert cache.get_crawl_run(run_id)["stop_requested"] is False


@pytest.mark.parametrize("directory_name", ["custom-state", "state 'one'; $STATE"])
def test_checkpoint_resume_uses_original_directory_after_chdir(tmp_path, monkeypatch, directory_name):
	"""相对目录的断点命令换到其他工作目录后仍读取原任务。"""
	data_dir = tmp_path / directory_name
	default_home = tmp_path / "default-home"
	_seed_login(data_dir)
	_seed_run(data_dir, "run-1", jobs=2)
	with CacheStore(data_dir / "cache" / "boss_agent.db") as cache:
		cache.update_crawl_run_params("run-1", CrawlSettings(
			query="AI", city_code="101210100", pages=5, with_detail=False,
			profile_path=data_dir / "crawl" / "chrome-profile", chrome_path=None,
			cdp_port=9222, hook_profile="none",
		).as_dict())
	_seed_run(default_home / ".boss-agent", "run-1", jobs=1)
	monkeypatch.setenv("HOME", str(default_home))
	monkeypatch.chdir(tmp_path)
	runner = CliRunner()
	result = runner.invoke(cli, ["--data-dir", directory_name, "--json", "crawl", "status", "run-1"])
	assert result.exit_code == 0, result.output
	resume_command = _envelope(result)["data"]["checkpoint"]["resume_command"]
	other_directory = tmp_path / "elsewhere"
	other_directory.mkdir()
	monkeypatch.chdir(other_directory)
	resumed = runner.invoke(cli, shlex.split(resume_command)[1:])
	assert resumed.exit_code == 0, resumed.output
	payload = _envelope(resumed)
	assert payload["data"]["jobs_seen"] == 2
	_assert_state_commands([payload["data"]["checkpoint"]["resume_command"]], data_dir, "crawl resume run-1")


def test_status_returns_checkpoint_and_detail_progress(tmp_path):
	_seed_run(tmp_path, "run-1", jobs=2)
	runner = CliRunner()
	result = _invoke(runner, tmp_path, "crawl", "status", "run-1")
	assert result.exit_code == 0, result.output
	payload = _envelope(result)
	data = payload["data"]
	assert data["run_id"] == "run-1"
	assert data["query"] == "AI"
	assert data["status"] == "completed"
	# 已结束的 run 上 stop 什么都不会停：只提示读取结果。
	_assert_state_commands(payload["hints"]["next_actions"], tmp_path, "crawl results run-1")


def test_status_running_points_to_results_and_stop(tmp_path):
	_seed_run(tmp_path, "run-1", status="running")
	payload = _envelope(_invoke(CliRunner(), tmp_path, "crawl", "status", "run-1"))
	_assert_state_commands(payload["hints"]["next_actions"], tmp_path, "crawl results run-1", "crawl stop run-1")


def test_status_quotes_run_id_in_checkpoint_resume_command(tmp_path):
	"""checkpoint.resume_command 也是可执行提示，run_id 必须按 shell 参数转义。"""
	_seed_run(tmp_path, "run; echo pwn", status="running")
	payload = _envelope(_invoke(CliRunner(), tmp_path, "crawl", "status", "run; echo pwn"))
	assert payload["ok"] is True
	_assert_state_commands([payload["data"]["checkpoint"]["resume_command"]], tmp_path, "crawl resume 'run; echo pwn'")


def test_status_reports_run_not_found(tmp_path):
	result = _invoke(CliRunner(), tmp_path, "crawl", "status", "missing")
	assert result.exit_code == 1
	assert _envelope(result)["error"]["code"] == "CRAWL_RUN_NOT_FOUND"


def test_run_not_found_hint_checks_the_original_directory(tmp_path, monkeypatch):
	"""run_id 打错时，核对提示不能在默认目录里找到同名任务。"""
	data_dir = tmp_path / "custom-state"
	default_dir = tmp_path / "default-home" / ".boss-agent"
	_seed_run(data_dir, "run-1", jobs=2)
	_seed_run(default_dir, "run-1", jobs=1)
	monkeypatch.setenv("HOME", str(tmp_path / "default-home"))
	runner = CliRunner()
	result = runner.invoke(cli, ["--data-dir", str(data_dir), "--json", "crawl", "status", "missing"])
	assert result.exit_code == 1, result.output
	actions = _envelope(result)["hints"]["next_actions"]
	_assert_state_commands(actions, data_dir, "crawl start <query>", "crawl status <run_id>")
	status_hint = actions[1].replace("<run_id>", "run-1")
	status = runner.invoke(cli, shlex.split(status_hint)[1:])
	assert status.exit_code == 0, status.output
	assert _envelope(status)["data"]["jobs_seen"] == 2, "核对提示应读到原目录的任务，而不是默认目录的同名任务"


def test_status_risk_stopped_reports_operator_unlock(tmp_path, monkeypatch):
	"""risk_stopped 的 checkpoint 不能只给 resume 命令：风险锁会先阻断它。"""
	from boss_agent_cli.api import cdp_risk_lock

	default_dir = tmp_path.parent / "default-home" / ".boss-agent"
	_seed_run(tmp_path, "run-1", status="risk_stopped")
	_seed_run(default_dir, "run-1", status="risk_stopped")
	monkeypatch.setenv("HOME", str(tmp_path.parent / "default-home"))
	cdp_risk_lock.write_lock(tmp_path, None, now=1, reason="ENVIRONMENT_RISK", platform_code=37)
	cdp_risk_lock.write_lock(default_dir, None, now=1, reason="ENVIRONMENT_RISK", platform_code=37)
	runner = CliRunner()
	payload = _envelope(runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "crawl", "status", "run-1"]))
	data = payload["data"]
	assert data["status"] == "risk_stopped"
	assert data["checkpoint"]["resume_requires_operator_unlock"] is True
	_assert_state_commands([data["checkpoint"]["resume_command"]], tmp_path, "crawl resume run-1")

	actions = payload["hints"]["operator_actions"]
	clean_action = next(action for action in actions if "clean --risk-lock" in action)
	assert "boss clean --risk-lock" not in clean_action
	clean_command = clean_action.split("执行 ", 1)[1].split(" 解除本地阻断", 1)[0]
	_assert_state_commands([clean_command], tmp_path, "clean --risk-lock")
	assert any(data["checkpoint"]["resume_command"] in action for action in actions)
	assert "next_actions" not in payload["hints"]
	# 照人工指引先解锁再恢复：两步都必须作用于原目录，否则会清掉/续跑默认目录的同名任务。
	unlocked = runner.invoke(cli, shlex.split(clean_command)[1:])
	assert unlocked.exit_code == 0, unlocked.output
	assert cdp_risk_lock.read_lock(tmp_path) is None
	assert cdp_risk_lock.read_lock(default_dir) is not None
	resumed = runner.invoke(cli, shlex.split(data["checkpoint"]["resume_command"])[1:])
	assert resumed.exit_code == 1, resumed.output
	assert _envelope(resumed)["error"]["code"] == "AUTH_REQUIRED", "解锁后应继续走到登录前置条件，而不是再次撞本地锁"


def test_status_completed_has_no_unlock_flag(tmp_path):
	_seed_run(tmp_path, "run-1")
	payload = _envelope(_invoke(CliRunner(), tmp_path, "crawl", "status", "run-1"))
	assert "resume_requires_operator_unlock" not in payload["data"]["checkpoint"]
	assert "hints" not in payload or "operator_actions" not in (payload.get("hints") or {})


def test_stop_reports_run_not_found(tmp_path):
	result = _invoke(CliRunner(), tmp_path, "crawl", "stop", "missing")
	assert result.exit_code == 1
	assert _envelope(result)["error"]["code"] == "CRAWL_RUN_NOT_FOUND"


def test_stop_records_the_kill_switch(tmp_path):
	_seed_run(tmp_path, "run-1", status="running")
	result = _invoke(CliRunner(), tmp_path, "crawl", "stop", "run-1")
	assert result.exit_code == 0, result.output
	assert _envelope(result)["data"] == {"run_id": "run-1", "status": "stop_requested"}
	with CacheStore(tmp_path / "cache" / "boss_agent.db") as cache:
		assert cache.get_crawl_run("run-1")["stop_requested"] is True


def test_results_filters_by_page_and_detail_status(tmp_path):
	_seed_run(tmp_path, "run-1", jobs=2)
	runner = CliRunner()

	everything = _envelope(_invoke(runner, tmp_path, "crawl", "results", "run-1"))["data"]
	assert everything["count"] == 2
	assert len(everything["jobs"]) == 2

	page_one = _envelope(_invoke(runner, tmp_path, "crawl", "results", "run-1", "--page", "1"))["data"]
	assert page_one["page"] == 1
	assert [job["crawl_page"] for job in page_one["jobs"]] == [1]

	pending = _envelope(_invoke(runner, tmp_path, "crawl", "results", "run-1", "--detail-status", "pending"))["data"]
	assert [job["detail_done"] for job in pending["jobs"]] == [False]

	completed = _envelope(_invoke(runner, tmp_path, "crawl", "results", "run-1", "--detail-status", "completed"))["data"]
	assert [job["detail_done"] for job in completed["jobs"]] == [True]


@pytest.mark.parametrize("run_id", ["run-1", "run 'quoted'; $HOME"])
@pytest.mark.parametrize(
	"filters, selected_indexes",
	[
		((), [0, 1, 2]),
		(("--page", "2"), [1]),
		(("--detail-status", "completed"), [0]),
		(("--page", "2", "--detail-status", "pending"), [1]),
	],
)
def test_results_import_hint_runs_with_visible_selection(tmp_path, monkeypatch, run_id, filters, selected_indexes):
	"""直接执行后继命令：使用原目录，只导入当前结果中的职位。"""
	data_dir = tmp_path / "saved 'crawl'; $STATE"
	_seed_run(data_dir, run_id, jobs=0)
	with CacheStore(data_dir / "cache" / "boss_agent.db") as cache:
		for index in range(3):
			cache.put_crawl_job(
				run_id,
				f"job-{index}",
				page_no=index + 1,
				payload={"security_id": f"sec-{index}", "job_id": f"job-{index}", "title": f"测试职位 {index}"},
				detail_done=index == 0,
			)
	default_home = tmp_path / "default-home"
	monkeypatch.setenv("HOME", str(default_home))
	runner = CliRunner()
	result = runner.invoke(cli, ["--data-dir", str(data_dir), "--json", "crawl", "results", run_id, *filters])
	assert result.exit_code == 0, result.output
	payload = _envelope(result)
	assert len(payload["hints"]["next_actions"]) == 1
	argv = shlex.split(payload["hints"]["next_actions"][0])

	follow = runner.invoke(cli, argv[1:])
	assert follow.exit_code == 0, follow.output
	imported = _envelope(follow)["data"]
	assert imported["selected_count"] == imported["imported_count"] == len(selected_indexes)
	assert argv[:6] == ["boss", "--data-dir", str(data_dir), "crawl", "shortlist", run_id]
	assert argv[6:] == [part for job in payload["data"]["jobs"] for part in ("--selector", job["selector"])]
	with CacheStore(data_dir / "cache" / "boss_agent.db") as cache:
		assert {job["job_id"] for job in cache.list_shortlist()} == {f"job-{index}" for index in selected_indexes}
	assert not (default_home / ".boss-agent" / "cache" / "boss_agent.db").exists()


@pytest.mark.parametrize("jobs, filters", [(0, ()), (2, ("--page", "99"))])
def test_results_empty_hint_runs_status_in_same_directory(tmp_path, monkeypatch, jobs, filters):
	"""没有返回职位时查看同一任务状态，不能建议空选择的导入。"""
	data_dir = tmp_path / "saved 'crawl'"
	run_id = "empty 'run';"
	_seed_run(data_dir, run_id, jobs=jobs)
	monkeypatch.setenv("HOME", str(tmp_path / "default-home"))
	runner = CliRunner()
	result = runner.invoke(cli, ["--data-dir", str(data_dir), "--json", "crawl", "results", run_id, *filters])
	assert result.exit_code == 0, result.output
	payload = _envelope(result)
	assert payload["data"]["jobs"] == []
	assert len(payload["hints"]["next_actions"]) == 1
	argv = shlex.split(payload["hints"]["next_actions"][0])
	assert argv == ["boss", "--data-dir", str(data_dir), "crawl", "status", run_id]
	follow = runner.invoke(cli, argv[1:])
	assert follow.exit_code == 0, follow.output
	status = _envelope(follow)
	assert status["command"] == "crawl.status"
	assert status["data"]["run_id"] == run_id
	with CacheStore(data_dir / "cache" / "boss_agent.db") as cache:
		assert cache.list_shortlist() == []


@pytest.mark.parametrize("jobs", [0, 2])
def test_results_tty_next_steps_match_envelope(tmp_path, monkeypatch, jobs):
	"""终端 renderer 与信封接收同一条完整后继命令。"""
	_seed_run(tmp_path, jobs=jobs)
	runner = CliRunner()
	args = ["--data-dir", str(tmp_path), "crawl", "results", "run-1"]
	result = runner.invoke(cli, args)
	assert result.exit_code == 0, result.output
	expected = _envelope(result)["hints"]["next_actions"]
	shown = []
	monkeypatch.setattr("boss_agent_cli.display.is_json_mode", lambda ctx: False)
	monkeypatch.setattr(
		"boss_agent_cli.commands.crawl.render_record_result",
		lambda data, *, title, next_steps: shown.extend(next_steps),
	)
	tty_result = runner.invoke(cli, args)
	assert tty_result.exit_code == 0, tty_result.output
	assert shown == expected


def test_results_never_leaks_platform_identifiers(tmp_path):
	_seed_run(tmp_path, "run-1", jobs=1)
	payload = _envelope(_invoke(CliRunner(), tmp_path, "crawl", "results", "run-1"))
	raw = json.dumps(payload, ensure_ascii=False)
	assert "sec-0" not in raw, "security_id 不得出现在 crawl results 输出里"
	assert "job-0" not in raw, "平台职位 ID 不得出现在 crawl results 输出里"


def test_results_reports_run_not_found(tmp_path):
	result = _invoke(CliRunner(), tmp_path, "crawl", "results", "missing")
	assert result.exit_code == 1
	assert _envelope(result)["error"]["code"] == "CRAWL_RUN_NOT_FOUND"


# ── shortlist ─────────────────────────────────────────────────────────


def test_shortlist_requires_exactly_one_selection_mode(tmp_path):
	selectors = _seed_run(tmp_path, "run-1", jobs=1)
	runner = CliRunner()

	neither = _invoke(runner, tmp_path, "crawl", "shortlist", "run-1")
	assert neither.exit_code == 1
	neither_error = _envelope(neither)["error"]
	assert neither_error["code"] == "INVALID_PARAM"
	assert "--all" in neither_error["recovery_action"]
	assert "--selector" in neither_error["recovery_action"]
	_assert_state_commands(_envelope(neither)["hints"]["next_actions"], tmp_path, "crawl results run-1")

	both = _invoke(runner, tmp_path, "crawl", "shortlist", "run-1", "--all", "--selector", selectors[0])
	assert both.exit_code == 1
	assert _envelope(both)["error"]["code"] == "INVALID_PARAM"


def test_shortlist_hint_quotes_run_id_with_shell_metacharacters(tmp_path):
	"""run_id 是自由文本，拼进 next_actions 前必须按 shell 参数转义。

	``boss crawl shortlist 'run; echo pwn'`` 原先把这段原样拼成
	``boss crawl results run; echo pwn``，Agent 照抄会把后半段当成独立命令执行。
	"""
	result = _invoke(CliRunner(), tmp_path, "crawl", "shortlist", "run; echo pwn")
	assert result.exit_code == 1
	payload = _envelope(result)
	assert payload["error"]["code"] == "INVALID_PARAM"
	_assert_state_commands(payload["hints"]["next_actions"], tmp_path, "crawl results 'run; echo pwn'")
	assert payload["hints"]["next_actions"][0] in payload["error"]["recovery_action"]


def test_shortlist_imports_selected_rows_with_tags_and_note(tmp_path):
	selectors = _seed_run(tmp_path, "run-1", jobs=2)
	result = _invoke(
		CliRunner(), tmp_path, "crawl", "shortlist", "run-1",
		"--selector", selectors[0], "--tags", "远程, 大模型 ,", "--note", "值得跟进",
	)
	assert result.exit_code == 0, result.output
	payload = _envelope(result)
	assert payload["ok"] is True
	_assert_state_commands(payload["hints"]["next_actions"][:1], tmp_path, "shortlist list")


def test_shortlist_rejects_unknown_selector(tmp_path):
	_seed_run(tmp_path, "run-1", jobs=1)
	result = _invoke(CliRunner(), tmp_path, "crawl", "shortlist", "run-1", "--selector", "csel_不存在")
	assert result.exit_code == 1
	error = _envelope(result)["error"]
	assert error["code"] == "INVALID_PARAM"
	actions = _envelope(result)["hints"]["next_actions"]
	_assert_state_commands(actions, tmp_path, "crawl results run-1")
	assert actions[0] in error["recovery_action"]


def test_shortlist_reports_run_not_found(tmp_path):
	result = _invoke(CliRunner(), tmp_path, "crawl", "shortlist", "missing", "--all")
	assert result.exit_code == 1
	assert _envelope(result)["error"]["code"] == "CRAWL_RUN_NOT_FOUND"


# ── 后台拉起的命令拼装 ────────────────────────────────────────────────
# `crawl start` 的实际执行靠这段 subprocess 命令；拼错了不会在别处暴露。


def test_background_resume_builds_a_self_contained_resume_command(tmp_path, monkeypatch):
	captured: dict = {}

	class _Popen:
		def __init__(self, command, **kwargs):
			captured["command"] = command
			captured["kwargs"] = kwargs

	monkeypatch.setattr("boss_agent_cli.commands.crawl.subprocess.Popen", _Popen)
	_launch_background_resume(tmp_path, "run-9", pages=3, with_detail=True)

	command = captured["command"]
	assert command[0] == sys.executable, "必须用当前解释器，避免落到系统 python 缺依赖"
	assert command[1:3] == ["-c", "from boss_agent_cli.main import cli; cli()"]
	assert command[command.index("--data-dir") + 1] == str(tmp_path)
	assert command[-5:] == ["resume", "run-9", "--from-queue", "--pages", "3"] or "--with-detail" in command
	assert "--json" in command
	assert "--from-queue" in command, "后台恢复必须带 --from-queue，否则会清掉用户刚下的停止请求"
	assert "--pages" in command and "3" in command
	assert "--with-detail" in command

	assert captured["kwargs"]["stdin"] is subprocess.DEVNULL
	assert captured["kwargs"]["stdout"] is subprocess.DEVNULL
	assert captured["kwargs"]["stderr"] is subprocess.DEVNULL


def test_background_resume_omits_optional_flags_when_not_requested(tmp_path, monkeypatch):
	captured: dict = {}
	monkeypatch.setattr(
		"boss_agent_cli.commands.crawl.subprocess.Popen",
		lambda command, **kwargs: captured.setdefault("command", command),
	)
	_launch_background_resume(tmp_path, "run-9")
	assert "--pages" not in captured["command"]
	assert "--with-detail" not in captured["command"]


# ── _run_service 的错误映射 ───────────────────────────────────────────


def test_resume_maps_missing_run_to_run_not_found(tmp_path, monkeypatch):
	from boss_agent_cli.commands import crawl as crawl_module

	def _missing(self, run_id, **kwargs):
		raise KeyError(run_id)

	monkeypatch.setattr(crawl_module.CrawlService, "resume", _missing)
	runner = CliRunner()
	_enable_research(runner, tmp_path)
	result = _invoke(runner, tmp_path, "crawl", "resume", "run-missing")
	assert result.exit_code == 1
	error = _envelope(result)["error"]
	assert error["code"] == "CRAWL_RUN_NOT_FOUND"
	assert "run-missing" in error["message"]


def test_resume_maps_value_error_to_invalid_param(tmp_path, monkeypatch):
	from boss_agent_cli.commands import crawl as crawl_module

	def _invalid(self, run_id, **kwargs):
		raise ValueError("run 已完成，无需恢复")

	monkeypatch.setattr(crawl_module.CrawlService, "resume", _invalid)
	_seed_run(tmp_path)
	runner = CliRunner()
	_enable_research(runner, tmp_path)
	result = _invoke(runner, tmp_path, "crawl", "resume", "run-1")
	assert result.exit_code == 1
	error = _envelope(result)["error"]
	assert error["code"] == "INVALID_PARAM"
	assert error["message"] == "run 已完成，无需恢复"


def test_resume_foreground_clears_the_stop_request_but_queue_resume_does_not(tmp_path, monkeypatch):
	"""用户手动 resume 应解除停止开关；后台队列恢复不得覆盖用户刚下的 stop。"""
	from boss_agent_cli.commands import crawl as crawl_module

	seen: list[bool] = []

	class _Outcome:
		run_id = "run-1"
		status = "completed"

		def as_dict(self):
			return {"run_id": self.run_id, "status": self.status}

	def _resume(self, run_id, **kwargs):
		seen.append(kwargs["clear_stop"])
		return _Outcome()

	monkeypatch.setattr(crawl_module.CrawlService, "resume", _resume)
	_seed_run(tmp_path)
	runner = CliRunner()
	_enable_research(runner, tmp_path)

	assert _invoke(runner, tmp_path, "crawl", "resume", "run-1").exit_code == 0
	assert _invoke(runner, tmp_path, "crawl", "resume", "run-1", "--from-queue").exit_code == 0
	assert seen == [True, False]


def test_resume_background_maps_launch_failure_to_crawl_unavailable(tmp_path, monkeypatch):
	def _fail(data_dir, run_id, **kwargs):
		raise OSError("spawn failed")

	monkeypatch.setattr("boss_agent_cli.commands.crawl._launch_background_resume", _fail)
	_seed_run(tmp_path, "run-1", status="stopped")
	runner = CliRunner()
	_enable_research(runner, tmp_path)
	result = _invoke(runner, tmp_path, "crawl", "resume", "run-1", "--background")
	assert result.exit_code == 1
	error = _envelope(result)["error"]
	assert error["code"] == "CRAWL_UNAVAILABLE"
	assert error["recoverable"] is True
	assert "crawl resume run-1" in error["recovery_action"]
	assert str(tmp_path.resolve()) in error["recovery_action"]
	# OSError 多来自磁盘满或目录权限，指引必须先查这两项，与 crawl start 一致。
	assert "检查磁盘空间与数据目录权限" in error["recovery_action"]


# ── 本地记录损坏 ──────────────────────────────────────────────────────


def _break_column(
	tmp_path: Path, table: str, column: str, run_id: str = "run-1", *, value: str = "{not json"
) -> None:
	db_path = tmp_path / "cache" / "boss_agent.db"
	with sqlite3.connect(db_path) as conn:
		conn.execute(f"UPDATE {table} SET {column} = ? WHERE run_id = ?", (value, run_id))


@pytest.mark.parametrize(
	"command",
	[
		("crawl", "status", "run-1"),
		("crawl", "results", "run-1"),
		("crawl", "resume", "run-1", "--background"),
		("crawl", "shortlist", "run-1", "--all"),
	],
)
def test_corrupt_run_params_reports_envelope_instead_of_traceback(tmp_path, command):
	"""run 的 params 写坏后，每个只读子命令都要给信封，而不是 json.loads traceback。"""
	_seed_run(tmp_path, "run-1", status="stopped")
	_break_column(tmp_path, "crawl_runs", "params")
	runner = CliRunner()
	_enable_research(runner, tmp_path)
	result = _invoke(runner, tmp_path, *command)
	assert result.exit_code == 1, result.output
	error = _envelope(result)["error"]
	assert error["code"] == "CACHE_RECORD_INVALID"
	assert error["recoverable"] is True
	assert "params" in error["message"]
	_assert_state_commands(_envelope(result)["hints"]["next_actions"][:1], tmp_path, "crawl start <关键词>")


def test_corrupt_hook_results_reports_envelope(tmp_path):
	_seed_run(tmp_path, "run-1")
	_break_column(tmp_path, "crawl_runs", "hook_results")
	result = _invoke(CliRunner(), tmp_path, "crawl", "status", "run-1")
	assert result.exit_code == 1, result.output
	assert _envelope(result)["error"]["code"] == "CACHE_RECORD_INVALID"


def test_corrupt_job_payload_reports_envelope(tmp_path):
	"""职位行写坏时 results 也要给信封：payload 是唯一真源，读不出就不能假装成功。"""
	_seed_run(tmp_path, "run-1")
	db_path = tmp_path / "cache" / "boss_agent.db"
	with sqlite3.connect(db_path) as conn:
		conn.execute("UPDATE crawl_jobs SET payload = ? WHERE rowid = 1", ("{not json",))
	result = _invoke(CliRunner(), tmp_path, "crawl", "results", "run-1")
	assert result.exit_code == 1, result.output
	error = _envelope(result)["error"]
	assert error["code"] == "CACHE_RECORD_INVALID"
	assert "payload" in error["message"]


def test_corrupt_run_record_in_foreground_resume_is_not_invalid_param(tmp_path, monkeypatch):
	"""CacheRecordInvalid 是 ValueError 子类，必须排在 INVALID_PARAM 兜底之前。"""
	_seed_run(tmp_path, "run-1", status="stopped")
	_break_column(tmp_path, "crawl_runs", "params")
	runner = CliRunner()
	_enable_research(runner, tmp_path)
	result = _invoke(runner, tmp_path, "crawl", "resume", "run-1")
	assert result.exit_code == 1, result.output
	assert _envelope(result)["error"]["code"] == "CACHE_RECORD_INVALID"


def test_structurally_drifted_params_reports_envelope(tmp_path):
	"""合法 JSON 但不是 object 时，后续 .get() 会抛 AttributeError，同样必须在读取边界拦下。"""
	_seed_run(tmp_path, "run-1")
	_break_column(tmp_path, "crawl_runs", "params", value='"not-an-object"')
	result = _invoke(CliRunner(), tmp_path, "crawl", "status", "run-1")
	assert result.exit_code == 1, result.output
	error = _envelope(result)["error"]
	assert error["code"] == "CACHE_RECORD_INVALID"
	assert "JSON object" in error["message"]


def test_structurally_drifted_hook_results_reports_envelope(tmp_path):
	_seed_run(tmp_path, "run-1")
	_break_column(tmp_path, "crawl_runs", "hook_results", value='{"not": "a list"}')
	result = _invoke(CliRunner(), tmp_path, "crawl", "status", "run-1")
	assert result.exit_code == 1, result.output
	assert "JSON array" in _envelope(result)["error"]["message"]


def test_run_not_found_carries_recovery_and_hints(tmp_path):
	"""run_id 写错时给出新建与核对两条可执行下一步，而不是「职位不存在」。"""
	result = _invoke(CliRunner(), tmp_path, "crawl", "status", "missing")

	assert result.exit_code == 1
	payload = _envelope(result)
	assert payload["error"]["code"] == "CRAWL_RUN_NOT_FOUND"
	assert payload["error"]["recovery_action"]
	_assert_state_commands(payload["hints"]["next_actions"], tmp_path, "crawl start <query>", "crawl status <run_id>")
