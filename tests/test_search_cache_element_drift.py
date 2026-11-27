"""搜索缓存元素级漂移按未命中处理（Issue #1318）。

``_parse_cached_result`` 的契约是「内容损坏或结构不对时按未命中处理」，
但旧实现只校验顶层形状。``data`` 混进字符串 / null 后，命中缓存的
``--sort score`` 与索引缓存写入会直接 ``AttributeError``，把本地坏行升级成
``INTERNAL_ERROR``；本次搜索本可以覆盖同一坏行。
"""

import json

from boss_agent_cli.commands.search import _parse_cached_result, _sort_search_items


def _raw(data, pagination=None):
	return json.dumps({"data": data, "pagination": pagination})


def test_cached_result_with_non_dict_element_is_treated_as_miss():
	assert _parse_cached_result(_raw(["drifted"])) is None
	assert _parse_cached_result(_raw([None])) is None
	assert _parse_cached_result(_raw([{"title": "正常"}, 1])) is None


def test_cached_result_with_dict_elements_still_hits():
	parsed = _parse_cached_result(_raw([{"title": "正常"}], {"has_more": False}))

	assert parsed is not None
	assert parsed["data"] == [{"title": "正常"}]


def test_score_sort_ignores_non_integer_match_score():
	"""match_score 漂移成字符串时不能与整数混合比较抛 TypeError。"""
	items = [
		{"title": "a", "match_score": "99"},
		{"title": "b", "match_score": 1},
		{"title": "c"},
		{"title": "d", "match_score": True},
	]

	sorted_items = _sort_search_items(items, "score")

	assert [item["title"] for item in sorted_items] == ["b", "a", "c", "d"]
	assert _sort_search_items(items, "none") == items


def _invoke_cached_search(data_dir, pagination):
	"""在隔离数据目录写入一条缓存行并执行 search；平台请求替身断言未发生。"""
	from unittest.mock import patch

	from click.testing import CliRunner

	from boss_agent_cli.main import cli

	row = {"job_id": "j1", "title": "缓存职位", "security_id": "s1", "greeted": False}
	payload = json.dumps({"data": [row], "pagination": pagination})
	with (
		patch("boss_agent_cli.commands.search.AuthManager"),
		patch("boss_agent_cli.commands.search.get_platform_instance"),
		patch("boss_agent_cli.commands.search.CacheStore") as cache_cls,
		patch("boss_agent_cli.commands.search.run_search_pipeline") as pipeline,
		patch("boss_agent_cli.commands.search.try_save_index", return_value=None),
	):
		cache = cache_cls.return_value
		cache.__enter__ = lambda _self: cache
		cache.__exit__ = lambda _self, *_args: None
		cache.get_search.return_value = payload
		cache.is_greeted.return_value = False
		result = CliRunner().invoke(cli, ["--data-dir", str(data_dir), "--json", "search", "Go"])

	assert result.exit_code == 0, result.output
	assert not pipeline.called, "缓存应命中，不应再请求平台"
	return json.loads(result.stdout)


def test_cached_pagination_total_must_match_data_rows(tmp_path):
	"""缓存里的派生计数漂移时，信封必须按本次 data 重算（Issue #3069）。"""
	pagination = {
		"page": 1,
		"has_more": False,
		"has_more_source": "platform_hasMore",
		"total": 5,
		"total_kind": "returned_page_count",
	}
	envelope = _invoke_cached_search(tmp_path, pagination)

	assert len(envelope["data"]) == 1
	assert envelope["pagination"]["total"] == 1
	assert envelope["pagination"]["total_kind"] == "returned_page_count"
	# 平台证据（页码 / 是否有下一页）仍取缓存，只有派生计数按 data 重算。
	assert envelope["pagination"]["page"] == 1
	assert envelope["pagination"]["has_more"] is False


def test_cached_pagination_null_total_is_recomputed(tmp_path):
	pagination = {
		"page": 2,
		"has_more": None,
		"has_more_source": "unknown",
		"total": None,
		"total_kind": "returned_page_count",
	}
	envelope = _invoke_cached_search(tmp_path, pagination)

	assert envelope["pagination"]["total"] == 1
	assert envelope["pagination"]["page"] == 2
	assert envelope["pagination"]["has_more"] is None
