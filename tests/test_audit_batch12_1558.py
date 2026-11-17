"""批次十二：显式参数不得静默丢弃（Issue #1558–#1561）。

覆盖四种「成功信封与真实行为不符」的入口：`agent crawl --run-id` 丢弃新采集
参数、`crawl run/start` 忽略未配 profile 的 `--hook-dir`、`crawl configure`
把空 Chrome 路径写成当前目录，以及 `watch run <name> --all` 静默运行全部。
"""

import json
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from boss_agent_cli.main import cli


def _envelope(result) -> dict:
	return json.loads(result.output)


def _invoke(runner: CliRunner, tmp_path: Path, *args: str):
	return runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", *args])


def _explode_transport(settings):  # pragma: no cover - 参数错误不应进入 transport
	raise AssertionError("参数错误不应创建采集浏览器")


# ── Issue #1558：agent crawl --run-id 丢弃新采集参数 ─────────────


def test_agent_crawl_run_id_rejects_pages(tmp_path, monkeypatch):
	monkeypatch.setattr("boss_agent_cli.commands.crawl._transport_factory", _explode_transport)
	result = _invoke(
		CliRunner(), tmp_path,
		"agent", "crawl", "--run-id", "run-1", "--pages", "3", "--resume", "test-resume",
	)

	assert result.exit_code == 1, result.output
	error = _envelope(result)["error"]
	assert error["code"] == "INVALID_PARAM"
	assert "--pages" in error["message"]
	assert "--query" in error["message"]
	assert error["recoverable"] is False


def test_agent_crawl_run_id_rejects_city_and_detail(tmp_path, monkeypatch):
	monkeypatch.setattr("boss_agent_cli.commands.crawl._transport_factory", _explode_transport)
	runner = CliRunner()
	for extra in (("--city", "杭州"), ("--with-detail",)):
		result = _invoke(
			runner, tmp_path,
			"agent", "crawl", "--run-id", "run-1", *extra, "--resume", "test-resume",
		)
		assert result.exit_code == 1, result.output
		error = _envelope(result)["error"]
		assert error["code"] == "INVALID_PARAM"
		assert extra[0] in error["message"]


def test_agent_crawl_rejects_empty_run_id(tmp_path):
	result = _invoke(
		CliRunner(), tmp_path,
		"agent", "crawl", "--run-id", "", "--resume", "test-resume",
	)

	assert result.exit_code == 1, result.output
	error = _envelope(result)["error"]
	assert error["code"] == "INVALID_PARAM"
	assert "--run-id" in error["message"]


def test_agent_crawl_rejects_run_id_and_query_together_even_when_empty(tmp_path):
	result = _invoke(
		CliRunner(), tmp_path,
		"agent", "crawl", "--run-id", "", "--query", "AI", "--resume", "test-resume",
	)

	assert result.exit_code == 1, result.output
	error = _envelope(result)["error"]
	assert error["code"] == "INVALID_PARAM"
	assert "只能使用" in error["message"]


def test_agent_crawl_rejects_empty_query(tmp_path):
	result = _invoke(
		CliRunner(), tmp_path,
		"agent", "crawl", "--query", "", "--resume", "test-resume",
	)

	assert result.exit_code == 1, result.output
	error = _envelope(result)["error"]
	assert error["code"] == "INVALID_PARAM"
	assert "--query" in error["message"]


# ── Issue #1559：--hook-dir 缺 profile 或为空 ────────────────────


def test_crawl_run_rejects_hook_dir_without_screenshot_profile(tmp_path, monkeypatch):
	monkeypatch.setattr("boss_agent_cli.commands.crawl._transport_factory", _explode_transport)
	result = _invoke(
		CliRunner(), tmp_path,
		"crawl", "run", "AI", "--city", "杭州", "--hook-dir", str(tmp_path / "hooks"),
	)

	assert result.exit_code == 1, result.output
	error = _envelope(result)["error"]
	assert error["code"] == "INVALID_PARAM"
	assert "--hook-profile screenshot-full" in error["message"]


def test_crawl_run_rejects_empty_hook_dir(tmp_path, monkeypatch):
	monkeypatch.setattr("boss_agent_cli.commands.crawl._transport_factory", _explode_transport)
	result = _invoke(
		CliRunner(), tmp_path,
		"crawl", "run", "AI", "--city", "杭州", "--hook-dir", "",
	)

	assert result.exit_code == 1, result.output
	error = _envelope(result)["error"]
	assert error["code"] == "INVALID_PARAM"
	assert "--hook-dir" in error["message"]
	assert "不能为空" in error["message"]


def test_crawl_start_rejects_hook_dir_without_screenshot_profile(tmp_path, monkeypatch):
	monkeypatch.setattr("boss_agent_cli.commands.crawl._transport_factory", _explode_transport)
	result = _invoke(
		CliRunner(), tmp_path,
		"crawl", "start", "AI", "--city", "杭州", "--hook-dir", str(tmp_path / "hooks"),
	)

	assert result.exit_code == 1, result.output
	error = _envelope(result)["error"]
	assert error["code"] == "INVALID_PARAM"
	assert "--hook-profile screenshot-full" in error["message"]


# ── Issue #1560：空 --chrome-path 不得写成当前目录 ───────────────


def test_crawl_configure_rejects_empty_chrome_path_without_writing(tmp_path):
	result = _invoke(CliRunner(), tmp_path, "crawl", "configure", "--chrome-path", "")

	assert result.exit_code == 1, result.output
	error = _envelope(result)["error"]
	assert error["code"] == "INVALID_PARAM"
	assert "--chrome-path" in error["message"]
	assert not (tmp_path / "config.json").exists()


def test_crawl_configure_still_saves_a_real_chrome_path(tmp_path):
	chrome = str(tmp_path / "chrome")
	result = _invoke(CliRunner(), tmp_path, "crawl", "configure", "--chrome-path", chrome)

	assert result.exit_code == 0, result.output
	assert _envelope(result)["data"]["crawl"]["chrome_path"] == chrome
	saved = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
	assert saved["crawl"]["chrome_path"] == chrome


def test_crawl_run_rejects_empty_chrome_path(tmp_path, monkeypatch):
	monkeypatch.setattr("boss_agent_cli.commands.crawl._transport_factory", _explode_transport)
	result = _invoke(
		CliRunner(), tmp_path,
		"crawl", "run", "AI", "--city", "杭州", "--chrome-path", "",
	)

	assert result.exit_code == 1, result.output
	error = _envelope(result)["error"]
	assert error["code"] == "INVALID_PARAM"
	assert "--chrome-path" in error["message"]


# ── Issue #1561：watch run 点名与 --all 互斥 ────────────────────


def test_watch_run_rejects_name_with_all_before_any_request(tmp_path):
	with patch(
		"boss_agent_cli.commands.watch.run_search_pipeline",
		side_effect=AssertionError("互斥参数不应发起任何 watch 请求"),
	):
		result = _invoke(CliRunner(), tmp_path, "watch", "run", "p1", "--all")

	assert result.exit_code == 1, result.output
	error = _envelope(result)["error"]
	assert error["code"] == "INVALID_PARAM"
	assert "--all" in error["message"]
	assert error["recoverable"] is False
	assert len(_envelope(result)["hints"]["next_actions"]) == 2
