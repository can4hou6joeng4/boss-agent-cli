"""crawl 断点的范围 / 枚举 / 文本校验（Issue #1325）。

坏断点必须在读取边界抛 ``CacheRecordInvalid``，让 ``crawl status`` 与
``crawl resume`` 对同一行给出一致的确定性错误，而不是 status 报健康、
resume 被吞成 ``ok: true`` 的 stopped，或落成误导性的 ``INVALID_PARAM``。
"""

import json

import pytest
from click.testing import CliRunner

from boss_agent_cli.cache.store import CacheRecordInvalid, CacheStore
from boss_agent_cli.crawler.service import CrawlSettings
from boss_agent_cli.main import cli


def _params(tmp_path, **overrides):
	params = {
		"query": "python",
		"city_code": "101210100",
		"pages": 1,
		"with_detail": False,
		"browser": {"profile_path": str(tmp_path / "profile"), "cdp_port": 9333},
		"chrome_path": None,
		"hook": {"profile": "none", "source": "user-supplied", "directory": None},
		"limits": {"max_requests": 20, "max_details": 50, "max_seconds": 600, "max_retries": 1},
		"operating_mode": "assisted",
	}
	params.update(overrides)
	return params


def _browser(tmp_path, **overrides):
	browser = {"profile_path": str(tmp_path / "profile"), "cdp_port": 9333}
	browser.update(overrides)
	return browser


def _seed_run(tmp_path, params):
	cache = CacheStore(tmp_path / "cache" / "boss_agent.db")
	try:
		cache.create_crawl_run("run-1", params, str(tmp_path / "crawl" / "run-1"))
	finally:
		cache.close()


def _exec(tmp_path, sql, values=()):
	cache = CacheStore(tmp_path / "cache" / "boss_agent.db")
	try:
		cache._conn.execute(sql, values)
		cache._conn.commit()
	finally:
		cache.close()


def _invoke(tmp_path, *args):
	return CliRunner().invoke(cli, ["--json", "--data-dir", str(tmp_path), "crawl", *args])


# ── from_dict：范围与枚举 ──────────────────────────────────────────


@pytest.mark.parametrize(
	("overrides", "field"),
	[
		({"pages": 0}, "params.pages"),
		({"pages": -1}, "params.pages"),
		({"limits": {"max_requests": 0, "max_details": 50, "max_seconds": 600, "max_retries": 1}},
		 "params.limits.max_requests"),
		({"limits": {"max_requests": 20, "max_details": 50, "max_seconds": -1, "max_retries": 1}},
		 "params.limits.max_seconds"),
		({"limits": {"max_requests": 20, "max_details": 50, "max_seconds": 600, "max_retries": -1}},
		 "params.limits.max_retries"),
		({"hook": {"profile": "bogus", "directory": None}}, "params.hook.profile"),
		({"hook": {"profile": "screenshot-full", "directory": None}}, "params.hook.directory"),
	],
)
def test_from_dict_rejects_out_of_range_checkpoint(tmp_path, overrides, field):
	with pytest.raises(CacheRecordInvalid) as excinfo:
		CrawlSettings.from_dict(_params(tmp_path, **overrides), run_id="run-1")

	assert field in str(excinfo.value)


@pytest.mark.parametrize("cdp_port", [70000, 65536, -1, True, 3.7, "70000", "abc"])
def test_from_dict_rejects_invalid_cdp_port(tmp_path, cdp_port):
	params = _params(tmp_path, browser=_browser(tmp_path, cdp_port=cdp_port))

	with pytest.raises(CacheRecordInvalid) as excinfo:
		CrawlSettings.from_dict(params, run_id="run-1")

	assert "params.browser.cdp_port" in str(excinfo.value)


def test_from_dict_accepts_valid_ranges(tmp_path):
	params = _params(
		tmp_path,
		max_retries=0,
		browser=_browser(tmp_path, cdp_port="9333"),
		hook={"profile": "screenshot-full", "directory": str(tmp_path / "hooks")},
	)

	settings = CrawlSettings.from_dict(params, run_id="run-1")

	assert settings.cdp_port == 9333
	assert settings.max_retries == 0
	assert settings.hook_profile == "screenshot-full"
	assert settings.hook_dir == tmp_path / "hooks"


# ── get_crawl_run：负页码 / 负计数器 / 非文本列 ─────────────────────


@pytest.mark.parametrize(
	("column", "value"),
	[
		("next_page", -3),
		("requests_attempted", -1),
		("detail_requests_attempted", -2),
		("elapsed_seconds", -5),
	],
)
def test_crawl_status_rejects_negative_numeric_columns(tmp_path, column, value):
	_seed_run(tmp_path, _params(tmp_path))
	_exec(tmp_path, f"UPDATE crawl_runs SET {column} = ? WHERE run_id = 'run-1'", (value,))

	result = _invoke(tmp_path, "status", "run-1")

	assert result.exit_code == 1, result.output
	payload = json.loads(result.stdout)
	assert payload["error"]["code"] == "CACHE_RECORD_INVALID"
	assert column in payload["error"]["message"]


@pytest.mark.parametrize("column", ["status", "output_dir", "error"])
def test_crawl_status_rejects_blob_text_columns(tmp_path, column):
	_seed_run(tmp_path, _params(tmp_path))
	_exec(tmp_path, f"UPDATE crawl_runs SET {column} = x'00ff' WHERE run_id = 'run-1'")

	result = _invoke(tmp_path, "status", "run-1")

	assert result.exit_code == 1, result.output
	payload = json.loads(result.stdout)
	assert payload["error"]["code"] == "CACHE_RECORD_INVALID"
	assert column in payload["error"]["message"]
