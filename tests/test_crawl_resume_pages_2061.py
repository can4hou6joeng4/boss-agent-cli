"""crawl resume --pages 与断点进度的交叉校验（Issue #2061）。"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.crawler.service import CrawlService, CrawlSettings
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


def test_pages_below_progress_is_rejected_without_persisting(tmp_path) -> None:
	"""--pages 缩到断点进度之前：写盘前拒绝，记录保持可读（Issue #2061）。"""
	_seed_stopped_run(tmp_path)
	with CacheStore(_cache_path(tmp_path)) as cache:
		service = CrawlService(cache, data_dir=tmp_path, transport_factory=lambda settings: object())
		with pytest.raises(ValueError) as excinfo:
			service.resume("run-shrink", pages=3)
		assert "小于断点进度" in str(excinfo.value)
		# 关键回归：params.pages 没有被改写成 3，交叉校验不会把记录判成损坏。
		run = cache.get_crawl_run("run-shrink")
		assert run["status"] == "stopped"
		assert run["next_page"] == 10
		assert run["params"]["pages"] == 12


def test_pages_equal_to_completed_pages_is_allowed(tmp_path, monkeypatch) -> None:
	"""next_page == pages + 1 是合法组合（最后一页之后，Issue #2030 同口径）。"""
	_seed_stopped_run(tmp_path)
	sentinel = object()
	monkeypatch.setattr(CrawlService, "_run", lambda self, *args, **kwargs: sentinel)
	with CacheStore(_cache_path(tmp_path)) as cache:
		service = CrawlService(cache, data_dir=tmp_path, transport_factory=lambda settings: object())
		assert service.resume("run-shrink", pages=9) is sentinel
		run = cache.get_crawl_run("run-shrink")
		assert run["next_page"] == 10
		assert run["params"]["pages"] == 9


def _enable_research(runner: CliRunner, tmp_path) -> None:
	result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "config", "set", "operating_mode", "research"])
	assert result.exit_code == 0, result.output


def test_cli_resume_rejects_pages_below_progress_and_keeps_status_readable(tmp_path) -> None:
	"""CLI 端到端：INVALID_PARAM 后 crawl status 仍能读取同一任务（Issue #2061）。"""
	runner = CliRunner()
	_enable_research(runner, tmp_path)
	_seed_stopped_run(tmp_path)

	result = runner.invoke(
		cli, ["--data-dir", str(tmp_path), "--json", "crawl", "resume", "run-shrink", "--pages", "3"]
	)
	assert result.exit_code == 1, result.output
	payload = json.loads(result.stdout)
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert "小于断点进度" in payload["error"]["message"]

	status = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "crawl", "status", "run-shrink"])
	assert status.exit_code == 0, status.output
	assert json.loads(status.stdout)["ok"] is True
