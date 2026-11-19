"""crawl resume --background 与停断点进度的交叉校验（Issue #2075）。"""

from __future__ import annotations

import json

from click.testing import CliRunner

from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.crawler.service import CrawlSettings
from boss_agent_cli.main import cli


def _cache_path(tmp_path):
	return tmp_path / "cache" / "boss_agent.db"


def _settings_dict(tmp_path) -> dict:
	return CrawlSettings(
		query="q",
		city_code="101",
		pages=12,
		with_detail=False,
		profile_path=tmp_path / "chrome-profile",
		chrome_path=None,
		cdp_port=9222,
		hook_profile="none",
	).as_dict()


def _seed_stopped_run(tmp_path, *, next_page: int = 10) -> None:
	with CacheStore(_cache_path(tmp_path)) as cache:
		cache.create_crawl_run("run-shrink", _settings_dict(tmp_path), str(tmp_path / "out"), status="stopped")
		cache.update_crawl_run("run-shrink", status="stopped", next_page=next_page, list_finished=False)


def _enable_research(runner: CliRunner, tmp_path) -> None:
	result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "config", "set", "operating_mode", "research"])
	assert result.exit_code == 0, result.output


def _invoke_background(tmp_path, monkeypatch, *, pages: int):
	launched: list[tuple] = []
	monkeypatch.setattr(
		"boss_agent_cli.commands.crawl._launch_background_resume",
		lambda *args, **kwargs: launched.append((args, kwargs)),
	)
	monkeypatch.setattr("boss_agent_cli.commands.crawl._crawl_preflight", lambda ctx: None)
	runner = CliRunner()
	_enable_research(runner, tmp_path)
	result = runner.invoke(
		cli,
		["--data-dir", str(tmp_path), "--json", "crawl", "resume", "run-shrink", "--background", "--pages", str(pages)],
	)
	return result, launched


def test_background_resume_rejects_pages_below_progress(tmp_path, monkeypatch) -> None:
	"""--background 不能先报 queued 再让 worker 静默失败（Issue #2075）。"""
	_seed_stopped_run(tmp_path)

	result, launched = _invoke_background(tmp_path, monkeypatch, pages=3)

	assert result.exit_code == 1, result.output
	payload = json.loads(result.stdout)
	assert payload["ok"] is False
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert "小于断点进度" in payload["error"]["message"]
	assert launched == [], "矛盾参数不得拉起 worker"
	with CacheStore(_cache_path(tmp_path)) as cache:
		run = cache.get_crawl_run("run-shrink")
	assert run["status"] == "stopped"
	assert run["next_page"] == 10
	assert run["params"]["pages"] == 12
	status = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "crawl", "status", "run-shrink"])
	assert status.exit_code == 0, status.output


def test_background_resume_accepts_legal_pages_boundary(tmp_path, monkeypatch) -> None:
	"""next_page == pages + 1 仍是合法组合：后台照常排队并拉起 worker。"""
	_seed_stopped_run(tmp_path)

	result, launched = _invoke_background(tmp_path, monkeypatch, pages=9)

	assert result.exit_code == 0, result.output
	payload = json.loads(result.stdout)
	assert payload["ok"] is True
	assert payload["data"]["status"] == "queued"
	assert launched == [((tmp_path, "run-shrink"), {"pages": 9, "with_detail": False})]
	with CacheStore(_cache_path(tmp_path)) as cache:
		run = cache.get_crawl_run("run-shrink")
	assert run["status"] == "queued"
