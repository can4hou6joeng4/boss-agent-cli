"""采集配置目录路径不可用是确定性本地失败（Issue #1290）。

`<data-dir>/crawl/chrome-profile` 被同名文件占用或目录不可写时，原先的
`OSError` 被 `_run` 兜成 `status=stopped`，`crawl run` 返回 ok=true 与永远
复现的 `crawl resume` 指引。现在 transport 抛出 `CrawlProfilePathInvalid`，
service 原样上抛，CLI 与 wizard 都按 `LOCAL_IO_ERROR`（recoverable=false）回报。
"""

import json
import sqlite3
from pathlib import Path

import pytest
from click.testing import CliRunner

from boss_agent_cli.auth.token_store import TokenStore
from boss_agent_cli.cache.store import CacheRecordInvalid, CacheStore
from boss_agent_cli.crawler.controls import CrawlProfilePathInvalid
from boss_agent_cli.crawler.service import CrawlBudget, CrawlService, CrawlSettings
from boss_agent_cli.crawler.transport import DrissionCrawlerSession
from boss_agent_cli.main import cli


class _NoDelayBudget(CrawlBudget):
	def wait(self, kind: str) -> None:
		pass


class _ExplodingTransport:
	def open(self):
		raise CrawlProfilePathInvalid("bad profile")


def _settings(tmp_path: Path) -> CrawlSettings:
	return CrawlSettings(
		query="AI",
		city_code="101210100",
		pages=1,
		with_detail=False,
		profile_path=tmp_path / "crawl" / "chrome-profile",
		chrome_path=None,
		cdp_port=9333,
		hook_profile="none",
		operating_mode="research",
	)


def _seed_login(tmp_path: Path) -> None:
	TokenStore(tmp_path / "auth").save(
		{"cookies": {"wt2": "test-wt2", "wbg": "1", "zp_at": "1"}, "user_agent": "test-agent"}
	)


def _block_profile(tmp_path: Path) -> None:
	(tmp_path / "crawl").mkdir(parents=True, exist_ok=True)
	(tmp_path / "crawl" / "chrome-profile").write_text("not a directory", encoding="utf-8")


def test_transport_open_rejects_profile_path_that_is_a_file(tmp_path):
	_block_profile(tmp_path)
	session = DrissionCrawlerSession(
		profile_path=tmp_path / "crawl" / "chrome-profile",
		chrome_path=None,
		cdp_port=9333,
		hook_profile="none",
		hook_dir=None,
	)

	with pytest.raises(CrawlProfilePathInvalid):
		session.open()


def test_service_does_not_swallow_profile_path_error(tmp_path):
	cache = CacheStore(tmp_path / "cache" / "boss_agent.db")
	service = CrawlService(
		cache,
		data_dir=tmp_path,
		transport_factory=lambda settings: _ExplodingTransport(),
		budget_factory=_NoDelayBudget,
	)
	with cache:
		with pytest.raises(CrawlProfilePathInvalid):
			service.create_and_run(_settings(tmp_path))


# ── NUL / 控制字符路径（Issue #1482） ──────────────────────────────


@pytest.mark.parametrize("control", ["\x00", "\n", "\t", "\x1f"])
def test_from_dict_rejects_control_characters_in_profile_path(tmp_path, control):
	"""控制字符能通过「非空绝对路径」检查，但 transport 会在 resolve() 抛 ValueError。"""
	params = _settings(tmp_path).as_dict()
	params["browser"]["profile_path"] = str(tmp_path / f"chrome{control}profile")

	with pytest.raises(CacheRecordInvalid) as excinfo:
		CrawlSettings.from_dict(params, run_id="run-1")

	assert "profile_path" in str(excinfo.value)
	assert "控制字符" in str(excinfo.value)


def test_transport_open_rejects_nul_in_profile_path(tmp_path):
	"""Path.resolve() / mkdir() 的 ValueError 必须是 CrawlProfilePathInvalid。"""
	session = DrissionCrawlerSession(
		profile_path=Path(f"{tmp_path}/crawl/chrome\x00profile"),
		chrome_path=None,
		cdp_port=9333,
		hook_profile="none",
		hook_dir=None,
	)

	with pytest.raises(CrawlProfilePathInvalid) as excinfo:
		session.open()

	assert "控制字符" in str(excinfo.value)


class _ReadonlyTransport:
	def open(self):
		raise sqlite3.OperationalError("attempt to write a readonly database")


def test_service_does_not_swallow_local_sqlite_io_error(tmp_path):
	"""本地只读 / 磁盘满不能被 `_run` 兜成普通 stopped（Issue #1467）。"""
	cache = CacheStore(tmp_path / "cache" / "boss_agent.db")
	service = CrawlService(
		cache,
		data_dir=tmp_path,
		transport_factory=lambda settings: _ReadonlyTransport(),
		budget_factory=_NoDelayBudget,
	)
	with cache:
		with pytest.raises(sqlite3.OperationalError, match="readonly"):
			service.create_and_run(_settings(tmp_path))


def test_crawl_run_reports_local_io_error_for_blocked_profile(tmp_path):
	_block_profile(tmp_path)
	_seed_login(tmp_path)
	runner = CliRunner()
	runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "config", "set", "operating_mode", "research"])

	result = runner.invoke(cli, ["--json", "--data-dir", str(tmp_path), "crawl", "run", "AI", "--city", "杭州"])

	assert result.exit_code == 1, result.stdout
	payload = json.loads(result.stdout)
	assert payload["ok"] is False
	error = payload["error"]
	assert error["code"] == "LOCAL_IO_ERROR"
	assert error["recoverable"] is False
	assert "chrome-profile" in error["message"]


def test_wizard_crawl_start_maps_blocked_profile_to_local_io(tmp_path):
	_block_profile(tmp_path)
	_seed_login(tmp_path)

	result = CliRunner().invoke(
		cli,
		[
			"--json", "--data-dir", str(tmp_path), "wizard", "--input-json",
			json.dumps({
				"role": "candidate",
				"platform": "zhipin",
				"goal": "crawl_start",
				"inputs": {"query": "AI", "city": "杭州"},
			}),
		],
	)

	assert result.exit_code == 1, result.stdout
	payload = json.loads(result.stdout)
	error = payload["error"]
	assert error["code"] == "LOCAL_IO_ERROR"
	assert error["recoverable"] is False
