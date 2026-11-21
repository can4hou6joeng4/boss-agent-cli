"""批次 95：completed 快速路径与 --pages 的交叉校验（Issue #2386）。"""

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


def _seed_completed_run(tmp_path) -> None:
	with CacheStore(_cache_path(tmp_path)) as cache:
		cache.create_crawl_run("run-done", _settings_dict(tmp_path), str(tmp_path / "out"), status="completed")
		cache.update_crawl_run("run-done", status="completed", next_page=6, list_finished=False)


def test_completed_resume_rejects_pages_below_progress(tmp_path) -> None:
	"""已完成任务的 --pages 矛盾必须在快速路径之前拒绝，且不改动断点。"""
	_seed_completed_run(tmp_path)
	with CacheStore(_cache_path(tmp_path)) as cache:
		service = CrawlService(cache, data_dir=tmp_path, transport_factory=lambda settings: object())
		with pytest.raises(ValueError) as excinfo:
			service.resume("run-done", pages=3)
		assert "小于断点进度" in str(excinfo.value)
		run = cache.get_crawl_run("run-done")

	assert run["status"] == "completed"
	assert run["next_page"] == 6
	assert run["params"]["pages"] == 12


def test_completed_background_resume_rejects_pages_below_progress(tmp_path, monkeypatch) -> None:
	"""后台入口同样不能把矛盾的 --pages 静默接受成已完成。"""
	launched: list[tuple] = []
	monkeypatch.setattr(
		"boss_agent_cli.commands.crawl._launch_background_resume",
		lambda *args, **kwargs: launched.append((args, kwargs)),
	)
	monkeypatch.setattr("boss_agent_cli.commands.crawl._crawl_preflight", lambda ctx: None)
	runner = CliRunner()
	enabled = runner.invoke(
		cli, ["--data-dir", str(tmp_path), "--json", "config", "set", "operating_mode", "research"]
	)
	assert enabled.exit_code == 0, enabled.output
	_seed_completed_run(tmp_path)

	result = runner.invoke(
		cli,
		["--data-dir", str(tmp_path), "--json", "crawl", "resume", "run-done", "--background", "--pages", "3"],
	)

	assert result.exit_code == 1, result.output
	payload = json.loads(result.stdout)
	assert payload["ok"] is False
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert "小于断点进度" in payload["error"]["message"]
	assert launched == [], "矛盾参数不得拉起 worker"
