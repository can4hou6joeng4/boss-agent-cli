"""``boss search`` 终端表尾的翻页提示必须能被原样复制执行。"""

import shlex

from boss_agent_cli.commands.search import _more_hint


def test_more_hint_leaves_plain_query_unquoted() -> None:
	assert _more_hint("golang", 1) == "more: boss search golang --page 2"


def test_more_hint_quotes_query_with_shell_metacharacters() -> None:
	"""手写双引号挡不住查询词里的 ``"`` / ``$`` / ``*``。

	``a"b`` 会把提示截成 ``boss search "a"b" --page 3``（引号不配对），
	``$HOME`` 在双引号里照样展开，``全天*`` 会被 glob 成文件名。统一走
	``shell_argument`` 后，按 shell 解析要还原成同一个查询词。
	"""
	for query in ('a"b', "$HOME", "全天*"):
		hint = _more_hint(query, 2)
		assert shlex.split(hint.removeprefix("more: ")) == ["boss", "search", query, "--page", "3"]
