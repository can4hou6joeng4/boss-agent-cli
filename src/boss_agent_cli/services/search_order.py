"""搜索结果的本地排序：CLI `search --sort` 与向导 `candidate_search` 共用。"""

from typing import Any

#: 与 `--sort` 选项及 MCP schema 的枚举保持一致。
SEARCH_SORT_MODES: tuple[str, str] = ("relevance", "score")


def sort_search_items(items: list[dict[str, Any]], sort: str) -> list[dict[str, Any]]:
	"""按平台顺序（``relevance``）或本地匹配分降序（``score``）返回结果。

	``match_score`` 由本工具写成整数；缓存被改坏成字符串 / 布尔时会与整数
	混合比较抛 ``TypeError``（Issue #1318），这里按 0 处理。未知取值等价于
	``relevance``；枚举校验由入口负责。
	"""
	if sort != "score":
		return items

	def _score(item: dict[str, Any]) -> int:
		value = item.get("match_score")
		return value if type(value) is int else 0

	return sorted(items, key=_score, reverse=True)
