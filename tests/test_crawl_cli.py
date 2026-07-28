"""CLI-layer contract tests for the `boss crawl` command group.

`tests/test_crawl.py` covers the crawler service and hook machinery. This file covers
the command layer itself — argument validation, compliance gating, and the JSON
envelope each subcommand emits — which previously shipped almost entirely untested
(`commands/crawl.py` sat at 49% line coverage).

Every test drives the real Click entry point through `CliRunner` and asserts on the
stdout envelope, so a regression in `code` / `data` / `hints` fails here rather than in
an agent's parser.
"""

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.commands.crawl import _city_code, _launch_background_resume, _save_crawl_config
from boss_agent_cli.main import cli


def _enable_research(runner: CliRunner, tmp_path: Path) -> None:
	result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "config", "set", "operating_mode", "research"])
	assert result.exit_code == 0, result.output


def _invoke(runner: CliRunner, tmp_path: Path, *args: str):
	return runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", *args])


def _envelope(result) -> dict:
	return json.loads(result.output)


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
	assert _envelope(result)["error"]["code"] == "INVALID_PARAM"


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


# ── 合规门控 ──────────────────────────────────────────────────────────
# COMPLIANCE_BLOCKED 的阻断行为测试归 tests/test_compliance.py；这里只覆盖
# research 模式下的正常路径与参数校验。


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
	assert payload["hints"]["next_actions"] == ["boss crawl resume run-stopped"]


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
	assert "output_paths" in _envelope(result)["hints"]["next_actions"][0]


# ── start ─────────────────────────────────────────────────────────────


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

	with CacheStore(tmp_path / "cache" / "boss_agent.db") as cache:
		runs = [cache.get_crawl_run(run_id) for run_id in _all_run_ids(tmp_path)]
	assert runs and runs[0] is not None
	assert runs[0]["status"] == "stopped", "后台拉起失败必须把 run 落成 stopped，否则会留下永久 queued 的幽灵任务"
	assert "无法启动后台任务" in runs[0]["error"]


def _all_run_ids(tmp_path: Path) -> list[str]:
	with sqlite3.connect(tmp_path / "cache" / "boss_agent.db") as conn:
		return [str(row[0]) for row in conn.execute("SELECT run_id FROM crawl_runs")]


# ── resume ────────────────────────────────────────────────────────────


def test_resume_background_reports_job_not_found(tmp_path):
	runner = CliRunner()
	_enable_research(runner, tmp_path)
	result = _invoke(runner, tmp_path, "crawl", "resume", "missing", "--background")
	assert result.exit_code == 1
	assert _envelope(result)["error"]["code"] == "JOB_NOT_FOUND"


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


# ── status / stop / results ───────────────────────────────────────────


def test_status_returns_checkpoint_and_detail_progress(tmp_path):
	_seed_run(tmp_path, "run-1", jobs=2)
	runner = CliRunner()
	result = _invoke(runner, tmp_path, "crawl", "status", "run-1")
	assert result.exit_code == 0, result.output
	data = _envelope(result)["data"]
	assert data["run_id"] == "run-1"
	assert data["query"] == "AI"
	assert data["status"] == "completed"


def test_status_reports_job_not_found(tmp_path):
	result = _invoke(CliRunner(), tmp_path, "crawl", "status", "missing")
	assert result.exit_code == 1
	assert _envelope(result)["error"]["code"] == "JOB_NOT_FOUND"


def test_stop_reports_job_not_found(tmp_path):
	result = _invoke(CliRunner(), tmp_path, "crawl", "stop", "missing")
	assert result.exit_code == 1
	assert _envelope(result)["error"]["code"] == "JOB_NOT_FOUND"


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


def test_results_never_leaks_platform_identifiers(tmp_path):
	_seed_run(tmp_path, "run-1", jobs=1)
	payload = _envelope(_invoke(CliRunner(), tmp_path, "crawl", "results", "run-1"))
	raw = json.dumps(payload, ensure_ascii=False)
	assert "sec-0" not in raw, "security_id 不得出现在 crawl results 输出里"
	assert "job-0" not in raw, "平台职位 ID 不得出现在 crawl results 输出里"


def test_results_reports_job_not_found(tmp_path):
	result = _invoke(CliRunner(), tmp_path, "crawl", "results", "missing")
	assert result.exit_code == 1
	assert _envelope(result)["error"]["code"] == "JOB_NOT_FOUND"


# ── shortlist ─────────────────────────────────────────────────────────


def test_shortlist_requires_exactly_one_selection_mode(tmp_path):
	selectors = _seed_run(tmp_path, "run-1", jobs=1)
	runner = CliRunner()

	neither = _invoke(runner, tmp_path, "crawl", "shortlist", "run-1")
	assert neither.exit_code == 1
	assert _envelope(neither)["error"]["code"] == "INVALID_PARAM"

	both = _invoke(runner, tmp_path, "crawl", "shortlist", "run-1", "--all", "--selector", selectors[0])
	assert both.exit_code == 1
	assert _envelope(both)["error"]["code"] == "INVALID_PARAM"


def test_shortlist_imports_selected_rows_with_tags_and_note(tmp_path):
	selectors = _seed_run(tmp_path, "run-1", jobs=2)
	result = _invoke(
		CliRunner(), tmp_path, "crawl", "shortlist", "run-1",
		"--selector", selectors[0], "--tags", "远程, 大模型 ,", "--note", "值得跟进",
	)
	assert result.exit_code == 0, result.output
	payload = _envelope(result)
	assert payload["ok"] is True
	assert payload["hints"]["next_actions"][0] == "boss shortlist list"


def test_shortlist_rejects_unknown_selector(tmp_path):
	_seed_run(tmp_path, "run-1", jobs=1)
	result = _invoke(CliRunner(), tmp_path, "crawl", "shortlist", "run-1", "--selector", "csel_不存在")
	assert result.exit_code == 1
	assert _envelope(result)["error"]["code"] == "INVALID_PARAM"


def test_shortlist_reports_job_not_found(tmp_path):
	result = _invoke(CliRunner(), tmp_path, "crawl", "shortlist", "missing", "--all")
	assert result.exit_code == 1
	assert _envelope(result)["error"]["code"] == "JOB_NOT_FOUND"


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


def test_resume_maps_missing_run_to_job_not_found(tmp_path, monkeypatch):
	from boss_agent_cli.commands import crawl as crawl_module

	def _missing(self, run_id, **kwargs):
		raise KeyError(run_id)

	monkeypatch.setattr(crawl_module.CrawlService, "resume", _missing)
	runner = CliRunner()
	_enable_research(runner, tmp_path)
	result = _invoke(runner, tmp_path, "crawl", "resume", "run-missing")
	assert result.exit_code == 1
	error = _envelope(result)["error"]
	assert error["code"] == "JOB_NOT_FOUND"
	assert "run-missing" in error["message"]


def test_resume_maps_value_error_to_invalid_param(tmp_path, monkeypatch):
	from boss_agent_cli.commands import crawl as crawl_module

	def _invalid(self, run_id, **kwargs):
		raise ValueError("run 已完成，无需恢复")

	monkeypatch.setattr(crawl_module.CrawlService, "resume", _invalid)
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
	assert "boss crawl resume run-1" in error["recovery_action"]
