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
