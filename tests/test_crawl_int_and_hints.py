"""crawl 整数输入的溢出边界与 CRAWL_PROFILE_BUSY 提示命令（Issue #1288 / #1289）。

- 断点 params 里的 `Infinity` / `1e400` 不能落成 INTERNAL_ERROR；按记录损坏报
  `CACHE_RECORD_INVALID`，wizard 输入按 `INVALID_PARAM`；
- `crawl status` 的 run_id 是必填参数：hint 必须带占位符，否则照抄必然失败。
"""

import json

import pytest
from click.testing import CliRunner

from boss_agent_cli.auth.token_store import TokenStore
from boss_agent_cli.cache.store import CacheRecordInvalid, CacheStore
from boss_agent_cli.crawler.controls import CrawlProfileBusy
from boss_agent_cli.crawler.service import CrawlSettings
from boss_agent_cli.main import cli


def _params(tmp_path, **overrides):
	params = {
		"query": "python",
		"city_code": "101210100",
		"pages": 1,
		"with_detail": False,
		"browser": {"profile_path": str(tmp_path / "profile"), "cdp_port": 0},
		"chrome_path": None,
		"hook": {"profile": "none", "source": "user-supplied", "directory": None},
		"limits": {"max_requests": 20, "max_details": 50, "max_seconds": 600, "max_retries": 1},
		"operating_mode": "assisted",
	}
	params.update(overrides)
	return params


def _seed_run(tmp_path, params) -> None:
	with CacheStore(tmp_path / "cache" / "boss_agent.db") as cache:
		cache.create_crawl_run("run-1", params, str(tmp_path / "crawl" / "run-1"), status="running")


def _seed_login(tmp_path) -> None:
	TokenStore(tmp_path / "auth").save(
		{"cookies": {"wt2": "test-wt2", "wbg": "1", "zp_at": "1"}, "user_agent": "test-agent"}
	)


def test_from_dict_rejects_infinite_cdp_port(tmp_path):
	with pytest.raises(CacheRecordInvalid, match="params"):
		CrawlSettings.from_dict(_params(tmp_path) | {"cdp_port": float("inf")}, run_id="run-1")


def test_crawl_status_reports_infinite_cdp_port_as_corrupt(tmp_path):
	_seed_run(tmp_path, _params(tmp_path) | {"cdp_port": float("inf")})

	result = CliRunner().invoke(cli, ["--json", "--data-dir", str(tmp_path), "crawl", "status", "run-1"])

	assert result.exit_code == 1, result.stdout
	payload = json.loads(result.stdout)
	assert payload["error"]["code"] == "CACHE_RECORD_INVALID"


def test_wizard_crawl_start_rejects_infinite_pages(tmp_path):
	result = CliRunner().invoke(
		cli,
		[
			"--json", "--data-dir", str(tmp_path), "wizard", "--input-json",
			json.dumps({
				"role": "candidate",
				"platform": "zhipin",
				"goal": "crawl_start",
				"inputs": {"query": "AI", "city": "杭州", "pages": 1e400},
			}),
		],
	)

	assert result.exit_code == 1, result.stdout
	payload = json.loads(result.stdout)
	assert payload["error"]["code"] == "INVALID_PARAM"


def test_crawl_run_profile_busy_hint_carries_run_id_placeholder(tmp_path, monkeypatch):
	from boss_agent_cli.commands import crawl as crawl_module

	def _raise(self, settings):
		raise CrawlProfileBusy("采集配置目录正被另一个运行中的采集 Chrome 使用")

	monkeypatch.setattr(crawl_module.CrawlService, "create_and_run", _raise)
	_seed_login(tmp_path)
	runner = CliRunner()
	runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "config", "set", "operating_mode", "research"])
	result = runner.invoke(cli, ["--json", "--data-dir", str(tmp_path), "crawl", "run", "AI", "--city", "杭州"])

	assert result.exit_code == 1, result.stdout
	payload = json.loads(result.stdout)
	assert payload["error"]["code"] == "CRAWL_PROFILE_BUSY"
	assert any("crawl status <run_id>" in action for action in payload["hints"]["next_actions"])


def test_wizard_profile_busy_recovery_carries_run_id_placeholder(tmp_path, monkeypatch):
	class _Boom:
		def resume(self, *args, **kwargs):
			raise CrawlProfileBusy("采集配置目录正被另一个运行中的采集 Chrome 使用")

	monkeypatch.setattr(
		"boss_agent_cli.wizard.actions._crawl_service",
		lambda context, cache: _Boom(),
	)
	_seed_login(tmp_path)
	result = CliRunner().invoke(
		cli,
		[
			"--json", "--data-dir", str(tmp_path), "wizard", "--input-json",
			json.dumps({
				"role": "candidate",
				"platform": "zhipin",
				"goal": "crawl_resume",
				"inputs": {"run_id": "run-1"},
			}),
		],
	)

	assert result.exit_code == 1, result.stdout
	payload = json.loads(result.stdout)
	assert payload["error"]["code"] == "CRAWL_PROFILE_BUSY"
	assert "crawl status <run_id>" in payload["error"]["recovery_action"]
