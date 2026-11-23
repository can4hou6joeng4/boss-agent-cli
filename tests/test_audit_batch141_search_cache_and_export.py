"""审计批次 141：search 缓存读取边界、分页/URL 与 JSON 导出降级（Issue #2545 / #2546）。

- 单行 `search_cache.response` 存了非法 UTF-8 时，读取抛 `OperationalError`
  被归成「库损坏」，恢复指引要求删除整个缓存库；
- 平台标题含孤立代理项时，`ensure_ascii=False` 的缓存文本在 SQLite 绑定阶段
  抛 `UnicodeEncodeError`，被兜成磁盘故障；
- 缓存 `pagination` 字段类型漂移原样进入成功信封；
- 搜索页码无上限、URL 里的 page 未钳制，坏 URL 被报成可重试网络错误；
- 深嵌套展示字段让整份 JSON 导出失败。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from click.testing import CliRunner
import pytest

from boss_agent_cli.cache.store import CacheDatabaseInvalid, CacheRowDecodeError, CacheStore
from boss_agent_cli.commands.search import _parse_cached_result
from boss_agent_cli.main import cli
from boss_agent_cli.search_filters import SearchUrlParseError, parse_boss_search_url
from boss_agent_cli.services.job_export import write_export_file
from boss_agent_cli.services.pagination import MAX_TRUSTED_PAGE


def _ctx_mock(mock_cls):
	instance = mock_cls.return_value
	instance.__enter__ = lambda self: self
	instance.__exit__ = lambda self, *a: None
	return instance


# ── #2545：缓存读取边界 ──────────────────────────────────────────────


def test_get_search_treats_undecodable_row_as_miss(tmp_path: Path) -> None:
	params = {"query": "go", "page": 1}
	with CacheStore(tmp_path / "boss_agent.db") as cache:
		cache.put_search(params, '{"data": [], "pagination": null}')
		key = cache._make_search_key(params)
		cache._conn.execute(
			"UPDATE search_cache SET response = cast(x'ff41' as text) WHERE cache_key = ?",
			(key,),
		)
		cache._conn.commit()

		assert cache.get_search(params) is None
		# 坏行已删除：再次读取不会重复触发解码错误。
		remaining = cache._conn.execute(
			"SELECT count(*) FROM search_cache WHERE cache_key = ?", (key,)
		).fetchone()
		assert remaining[0] == 0


def test_get_search_treats_blob_response_as_miss(tmp_path: Path) -> None:
	params = {"query": "go", "page": 1}
	with CacheStore(tmp_path / "boss_agent.db") as cache:
		cache.put_search(params, '{"data": [], "pagination": null}')
		key = cache._make_search_key(params)
		cache._conn.execute(
			"UPDATE search_cache SET response = cast(x'7b7d' as blob) WHERE cache_key = ?",
			(key,),
		)
		cache._conn.commit()

		assert cache.get_search(params) is None


def test_row_decode_error_is_database_invalid_subclass() -> None:
	error = CacheRowDecodeError(Path("/tmp/x.db"), "Could not decode to UTF-8 column 'response'")

	assert isinstance(error, CacheDatabaseInvalid)
	assert error.detail


@pytest.mark.parametrize(
	"pagination",
	[
		{"page": True, "has_more": None},
		{"page": 0, "has_more": None},
		{"page": 1, "has_more": "true"},
		{"page": 1, "has_more": None, "total": "1"},
		{"page": 1, "has_more": None, "total": -1},
		{"page": 1, "has_more": None, "total_kind": 1},
		{"page": MAX_TRUSTED_PAGE + 1, "has_more": None},
	],
)
def test_parse_cached_result_rejects_drifted_pagination(pagination: dict) -> None:
	raw = json.dumps({"data": [], "pagination": pagination})

	assert _parse_cached_result(raw) is None


def test_parse_cached_result_accepts_valid_pagination() -> None:
	pagination = {
		"page": 2,
		"has_more": True,
		"has_more_source": "platform_hasMore",
		"total": 1,
		"total_kind": "returned_page_count",
	}
	raw = json.dumps({"data": [{"job_id": "j1"}], "pagination": pagination})

	parsed = _parse_cached_result(raw)

	assert parsed is not None
	assert parsed["pagination"] == pagination


# ── #2546：搜索分页 / URL / 导出 ─────────────────────────────────────


@patch("boss_agent_cli.commands.search.run_search_pipeline")
@patch("boss_agent_cli.commands.search.CacheStore")
@patch("boss_agent_cli.commands.search.AuthManager")
@patch("boss_agent_cli.commands.search.get_platform_instance")
def test_search_caches_encodable_json_for_surrogate_titles(
	mock_client_cls, mock_auth_cls, mock_cache_cls, mock_pipeline
) -> None:
	cache = _ctx_mock(mock_cache_cls)
	cache.get_search.return_value = None
	_ctx_mock(mock_client_cls)
	mock_pipeline.return_value = SimpleNamespace(
		items=[{"job_id": "j1", "title": "工程师\ud800", "company": "TestCo"}],
		has_more=False,
		has_more_reported=False,
		total=1,
		stats=SimpleNamespace(pages_scanned=1, jobs_seen=1, jobs_prefiltered=0, detail_checks=0),
	)

	result = CliRunner().invoke(cli, ["--json", "search", "go"])

	assert result.exit_code == 0, result.output
	response = cache.put_search.call_args.args[1]
	response.encode("utf-8")  # 缓存文本必须能绑定进 SQLite TEXT
	assert "\\ud800" in response


@patch("boss_agent_cli.commands.search.run_search_pipeline")
@patch("boss_agent_cli.commands.search.CacheStore")
@patch("boss_agent_cli.commands.search.AuthManager")
@patch("boss_agent_cli.commands.search.get_platform_instance")
def test_search_at_trusted_page_limit_skips_next_page_hint(
	mock_client_cls, mock_auth_cls, mock_cache_cls, mock_pipeline
) -> None:
	cache = _ctx_mock(mock_cache_cls)
	cache.get_search.return_value = None
	_ctx_mock(mock_client_cls)
	mock_pipeline.return_value = SimpleNamespace(
		items=[{"job_id": "j1", "title": "Go"}],
		has_more=True,
		has_more_reported=True,
		total=1,
		stats=SimpleNamespace(pages_scanned=1, jobs_seen=1, jobs_prefiltered=0, detail_checks=0),
	)

	result = CliRunner().invoke(
		cli, ["--json", "search", "go", "--page", str(MAX_TRUSTED_PAGE)]
	)

	assert result.exit_code == 0, result.output
	payload = json.loads(result.stdout)
	assert payload["pagination"]["has_more"] is True
	assert not [action for action in payload["hints"]["next_actions"] if "--page" in action]


@patch("boss_agent_cli.commands.search.run_search_pipeline")
@patch("boss_agent_cli.commands.search.CacheStore")
@patch("boss_agent_cli.commands.search.AuthManager")
@patch("boss_agent_cli.commands.search.get_platform_instance")
def test_search_rejects_oversized_page_before_request(
	mock_client_cls, mock_auth_cls, mock_cache_cls, mock_pipeline
) -> None:
	_ctx_mock(mock_client_cls)

	result = CliRunner().invoke(cli, ["--json", "search", "go", "--page", "9" * 4300])

	assert result.exit_code == 1, result.output
	payload = json.loads(result.stdout)
	assert payload["error"]["code"] == "INVALID_PARAM"
	mock_pipeline.assert_not_called()


@patch("boss_agent_cli.commands.search.run_search_pipeline")
@patch("boss_agent_cli.commands.search.CacheStore")
@patch("boss_agent_cli.commands.search.AuthManager")
@patch("boss_agent_cli.commands.search.get_platform_instance")
def test_search_rejects_malformed_url_as_input_error(
	mock_client_cls, mock_auth_cls, mock_cache_cls, mock_pipeline
) -> None:
	_ctx_mock(mock_client_cls)

	result = CliRunner().invoke(
		cli, ["--json", "search", "--url", "https://[invalid/web/geek/job?query=demo"]
	)

	assert result.exit_code == 1, result.output
	payload = json.loads(result.stdout)
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert payload["error"]["recoverable"] is False
	mock_pipeline.assert_not_called()


def test_parse_url_clamps_page_to_trusted_limit() -> None:
	parsed = parse_boss_search_url(
		"https://www.zhipin.com/web/geek/jobs?query=Python&page=" + "9" * 4300
	)

	assert parsed.page == MAX_TRUSTED_PAGE


def test_parse_url_wraps_urlparse_value_error() -> None:
	with pytest.raises(SearchUrlParseError):
		parse_boss_search_url("https://[invalid/web/geek/job?query=demo")


def _deeply_nested(depth: int) -> list:
	root: list = []
	current = root
	for _ in range(depth):
		inner: list = []
		current.append(inner)
		current = inner
	return root


def test_json_export_degrades_unserializable_item(tmp_path: Path) -> None:
	items = [
		{"title": "坏条目", "skills": _deeply_nested(1000)},
		{"title": "好条目", "job_id": "j2"},
	]
	target = tmp_path / "out.json"

	write_export_file(items, "json", str(target))

	payload = json.loads(target.read_text(encoding="utf-8"))
	assert payload[0]["title"] == "坏条目"
	assert "export_error" in payload[0]
	assert "skills" not in payload[0]
	assert payload[1] == {"title": "好条目", "job_id": "j2"}
