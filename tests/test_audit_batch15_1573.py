"""第三轮审计批次：Issue #1573–#1578 的回归测试。"""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.commands.mark import _resolve_label
from boss_agent_cli.crawler.service import CrawlSettings
from boss_agent_cli.main import cli
from boss_agent_cli.schema.data import SCHEMA_DATA
from boss_agent_cli.schema.tool_catalog import build_tool_catalog
from boss_agent_cli.services.file_lock import exclusive_file_lock, run_lock_path


def _settings(tmp_path: Path, run_id: str = "run-1") -> CrawlSettings:
	return CrawlSettings(
		query="AI",
		city_code="101210100",
		pages=1,
		with_detail=True,
		profile_path=tmp_path / "profile",
		chrome_path=None,
		cdp_port=9222,
		hook_profile="none",
	)


def _seed_run(tmp_path: Path, run_id: str, status: str) -> None:
	with CacheStore(tmp_path / "cache" / "boss_agent.db") as cache:
		cache.create_crawl_run(run_id, _settings(tmp_path, run_id).as_dict(), str(tmp_path / "crawl" / "runs" / run_id))
		cache.update_crawl_run(run_id, status=status, next_page=1)


# ── Issue #1573：preset 不得覆盖显式空筛选 ─────────────────────────


def _ctx_mock(mock_cls):
	instance = mock_cls.return_value
	instance.__enter__ = lambda self: self
	instance.__exit__ = lambda self, *a: None
	return instance


@patch("boss_agent_cli.index_cache.save_index")
@patch("boss_agent_cli.commands.search.account_scope", return_value="acct")
@patch("boss_agent_cli.commands.search.run_search_pipeline")
@patch("boss_agent_cli.commands.search.get_platform_instance")
@patch("boss_agent_cli.commands.search.AuthManager")
def test_search_explicit_empty_city_overrides_preset(
	mock_auth_cls, mock_client_cls, mock_pipeline, mock_scope, mock_save_index, tmp_path,
):
	_ctx_mock(mock_client_cls)
	mock_pipeline.return_value = SimpleNamespace(
		items=[],
		has_more=False,
		has_more_reported=False,
		total=0,
		stats=SimpleNamespace(pages_scanned=1, jobs_seen=0, jobs_prefiltered=0, detail_checks=0),
	)

	runner = CliRunner()
	added = runner.invoke(
		cli,
		["--data-dir", str(tmp_path), "--json", "preset", "add", "preset-city", "any", "--city", "上海"],
	)
	assert added.exit_code == 0, added.output

	result = runner.invoke(
		cli,
		["--data-dir", str(tmp_path), "--json", "search", "python", "--preset", "preset-city", "--city", "", "--no-cache"],
	)
	assert result.exit_code == 0, result.output
	criteria = mock_pipeline.call_args.kwargs["criteria"]
	assert criteria.city not in ("上海",), "显式空城市不能被预设覆盖"
	# 预设补齐的其它项仍生效。
	assert criteria.query == "python"


# ── Issue #1574：终态 run 不接受 stop ─────────────────────────────


@pytest.mark.parametrize("status", ["completed", "failed", "stopped", "risk_stopped"])
def test_crawl_stop_rejects_finished_run(tmp_path, status):
	_seed_run(tmp_path, "done-1", status)
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "crawl", "stop", "done-1"])
	assert result.exit_code == 1, result.output
	payload = json.loads(result.output)
	assert payload["error"]["code"] == "CRAWL_RUN_FINISHED"
	assert payload["error"]["recoverable"] is False
	assert status in payload["error"]["message"]
	assert payload["hints"]["next_actions"]
	# 请求没有落成 stop_requested。
	with CacheStore(tmp_path / "cache" / "boss_agent.db") as cache:
		run = cache.get_crawl_run("done-1")
	assert run is not None
	assert run["stop_requested"] is False


@pytest.mark.parametrize("status", ["queued", "running"])
def test_crawl_stop_accepts_active_run(tmp_path, status):
	_seed_run(tmp_path, "active-1", status)
	# running 必须真的持有执行锁才算「有执行器」（Issue #2465）：锁文件存在且
	# 被持有时 stop 仍只写停止标记，交给执行器在下一安全点生效。
	lock = exclusive_file_lock(run_lock_path(tmp_path / "cache" / "crawl-locks", "active-1"))
	with lock as acquired:
		assert acquired
		result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "crawl", "stop", "active-1"])
	assert result.exit_code == 0, result.output
	payload = json.loads(result.output)
	assert payload["data"]["status"] == "stop_requested"
	with CacheStore(tmp_path / "cache" / "boss_agent.db") as cache:
		run = cache.get_crawl_run("active-1")
	assert run is not None
	assert run["stop_requested"] is True
	assert run["status"] == status


@pytest.mark.parametrize("status", ["queued", "running"])
def test_crawl_stop_recovers_run_whose_executor_exited(tmp_path, status):
	# 执行器被 SIGKILL 后锁已释放、状态停在 running / queued：停止请求没有
	# 任何执行器会消费，继续报 stop_requested 等于假成功（Issue #2465）。
	_seed_run(tmp_path, "stale-1", status)
	if status == "queued":
		# queued 在交接宽限内按仍有 worker 处理，先让排队时间过期。
		with CacheStore(tmp_path / "cache" / "boss_agent.db") as cache:
			cache._conn.execute("UPDATE crawl_runs SET updated_at = updated_at - 120 WHERE run_id = ?", ("stale-1",))
			cache._conn.commit()
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "crawl", "stop", "stale-1"])
	assert result.exit_code == 0, result.output
	payload = json.loads(result.output)
	assert payload["data"] == {"run_id": "stale-1", "status": "stopped", "stale_recovered": True}
	with CacheStore(tmp_path / "cache" / "boss_agent.db") as cache:
		run = cache.get_crawl_run("stale-1")
	assert run is not None
	assert run["status"] == "stopped"
	assert "resume" in run["error"]
	assert payload["hints"]["next_actions"]


def test_crawl_stop_missing_run_still_not_found(tmp_path):
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "crawl", "stop", "ghost"])
	assert result.exit_code == 1, result.output
	assert json.loads(result.output)["error"]["code"] == "CRAWL_RUN_NOT_FOUND"


def test_crawl_stop_schema_documents_finished_error():
	description = SCHEMA_DATA["commands"]["crawl"]["options"]["stop"]["description"]
	assert "CRAWL_RUN_FINISHED" in description
	assert "queued" in description and "running" in description


# ── Issue #1575：mark 只接受已知数字标签 ID ────────────────────────


def test_mark_resolve_known_numeric_label():
	assert _resolve_label("11") == 11
	assert _resolve_label("1") == 1


@pytest.mark.parametrize("value", ["999", "0", "12"])
def test_mark_rejects_unknown_numeric_label(value, tmp_path):
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "mark", "uid-1", "--label", value])
	assert result.exit_code == 1, result.output
	payload = json.loads(result.output)
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert "未知标签 ID" in payload["error"]["message"] or "标签" in payload["error"]["message"]


def test_mark_schema_documents_numeric_id_range():
	label = SCHEMA_DATA["commands"]["mark"]["options"]["--label"]
	assert "1/2/3/4/5/6/7/8/11" in label["description"]
	mark_tool = next(tool for tool in build_tool_catalog(SCHEMA_DATA) if tool.name == "boss_mark")
	assert "1/2/3/4/5/6/7/8/11" in mark_tool.input_schema["properties"]["tag"]["description"]


# ── Issue #1576：AI 工具的 JD 来源必须且只能一个 ───────────────────


@pytest.mark.parametrize("tool_name", ["boss_ai_resume_optimize", "boss_ai_cover_letter"])
def test_ai_tools_schema_requires_one_jd_source(tool_name):
	tool = next(tool for tool in build_tool_catalog(SCHEMA_DATA) if tool.name == tool_name)
	schema = tool.input_schema
	assert {"required": ["jd_text"]} in schema["anyOf"]
	assert {"required": ["job_id"]} in schema["anyOf"]
	assert schema["not"] == {"required": ["jd_text", "job_id"]}


def test_ai_native_schema_documents_jd_exclusivity():
	for sub in ("resume-optimize", "cover-letter"):
		options = SCHEMA_DATA["commands"]["ai"]["options"][sub]
		assert "必须且只能给一个" in options["--jd"]["description"]
		assert "必须且只能给一个" in options["--job-id"]["description"]


# ── Issue #1577：crawl shortlist 的 selectors / all 恰好一个 ───────


def test_crawl_shortlist_schema_encodes_exactly_one_selector_source():
	raw = SCHEMA_DATA["commands"]["crawl"]["mcp_tools"]
	tool = next(item for item in raw if item["name"] == "boss_crawl_shortlist")
	schema = tool["inputSchema"]
	assert schema["properties"]["selectors"]["minItems"] == 1
	assert {"required": ["selectors"]} in schema["anyOf"]
	# 互斥按值判断：只有 all=true 与 selectors 同时出现才算冲突（Issue #2340）。
	assert schema["not"] == {"required": ["selectors"], "properties": {"all": {"const": True}}}
	# tool_catalog 暴露同一份定义。
	compiled = next(tool for tool in build_tool_catalog(SCHEMA_DATA) if tool.name == "boss_crawl_shortlist")
	assert compiled.input_schema["properties"]["selectors"]["minItems"] == 1


# ── Issue #1578：全局空参数不得回落配置 ───────────────────────────


def _write_config(tmp_path: Path) -> None:
	(tmp_path / "config.json").write_text(
		json.dumps({"request_delay": [1, 2], "cdp_url": "http://127.0.0.1:9222"}),
		encoding="utf-8",
	)


def test_delay_empty_string_is_rejected(tmp_path):
	_write_config(tmp_path)
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "--delay", "", "schema"])
	assert result.exit_code == 1, result.output
	payload = json.loads(result.output)
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert "--delay" in payload["error"]["message"]


def test_cdp_url_empty_string_is_rejected(tmp_path):
	_write_config(tmp_path)
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "--cdp-url", "", "schema"])
	assert result.exit_code == 1, result.output
	payload = json.loads(result.output)
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert "--cdp-url" in payload["error"]["message"]


def test_absent_delay_and_cdp_url_still_fall_back_to_config(tmp_path):
	_write_config(tmp_path)
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "schema"])
	assert result.exit_code == 0, result.output
	assert json.loads(result.output)["ok"] is True
