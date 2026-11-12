"""``boss search`` 的下一页提示必须保留本次调用条件，且能被原样复制执行。"""

import json
import shlex
from types import SimpleNamespace
from unittest.mock import patch

from click.testing import CliRunner

from boss_agent_cli.commands.search import _more_hint
from boss_agent_cli.main import cli


def test_more_hint_only_prefixes_the_given_command() -> None:
	"""表尾提示与信封必须是同一条命令：TTY 只贴前缀，不再自己拼参数。"""
	assert _more_hint("boss search golang --page 2") == "more: boss search golang --page 2"


def _invoke_search(*args, tmp_path, has_more=True, has_more_reported="same"):
	with (
		patch("boss_agent_cli.commands.search.AuthManager"),
		patch("boss_agent_cli.commands.search.get_platform_instance"),
		patch("boss_agent_cli.commands.search.CacheStore") as mock_cache_cls,
		patch("boss_agent_cli.commands.search.run_search_pipeline") as mock_pipeline,
	):
		mock_cache = mock_cache_cls.return_value
		mock_cache.__enter__ = lambda s: mock_cache
		mock_cache.__exit__ = lambda s, *a: None
		mock_cache.get_search.return_value = None
		mock_cache.is_greeted.return_value = False
		mock_pipeline.return_value = SimpleNamespace(
			items=[{
				"job_id": "j1",
				"title": "Go 开发",
				"company": "TestCo",
				"salary": "20K",
				"city": "北京",
				"security_id": "sec_001",
				"greeted": False,
			}],
			has_more=has_more,
			has_more_reported=has_more if has_more_reported == "same" else has_more_reported,
			total=1,
			stats=SimpleNamespace(
				pages_scanned=1,
				jobs_seen=1,
				jobs_prefiltered=0,
				detail_checks=0,
			),
		)
		with patch("boss_agent_cli.index_cache.save_index"):
			result = CliRunner().invoke(
				cli, ["--data-dir", str(tmp_path), "--json", "search", *args],
			)
	assert result.exit_code == 0, result.output
	return json.loads(result.stdout)


def _prefix(tmp_path) -> list[str]:
	"""提示命令的前缀：保留当前数据目录（Issue #919）。"""
	return ["boss", "--data-dir", str(tmp_path.resolve())]


def _next_page(payload: dict) -> list[str]:
	"""取出下一页提示并按 shell 解析成 argv。"""
	actions = [action for action in payload["hints"]["next_actions"] if "search" in action and "--page" in action]
	assert len(actions) == 1
	return shlex.split(actions[0])


def test_next_page_keeps_plain_query_unquoted(tmp_path) -> None:
	assert _next_page(_invoke_search("golang", tmp_path=tmp_path)) == [
		*_prefix(tmp_path), "search", "golang", "--page", "2",
	]


def test_next_page_keeps_every_filter_and_switch(tmp_path) -> None:
	"""只写 ``search <query> --page N+1`` 会让翻页落到另一组结果集。"""
	payload = _invoke_search(
		"后端开发",
		"--city", "北京",
		"--salary", "20-50K",
		"--experience", "3-5年",
		"--education", "本科",
		"--industry", "互联网",
		"--scale", "100-499人",
		"--stage", "已上市",
		"--job-type", "全职",
		"--with-score",
		"--sort", "score",
		"--no-cache",
		tmp_path=tmp_path,
	)
	assert _next_page(payload) == [
		*_prefix(tmp_path), "search", "后端开发",
		"--city", "北京",
		"--salary", "20-50K",
		"--experience", "3-5年",
		"--education", "本科",
		"--industry", "互联网",
		"--scale", "100-499人",
		"--stage", "已上市",
		"--job-type", "全职",
		"--no-cache",
		"--with-score",
		"--sort", "score",
		"--page", "2",
	]


def test_next_page_quotes_query_with_shell_metacharacters(tmp_path) -> None:
	"""手写双引号挡不住查询词里的 ``"`` / ``$`` / ``*``。

	``a"b`` 会把提示截成 ``boss search "a"b" --page 3``（引号不配对），
	``$HOME`` 在双引号里照样展开，``全天*`` 会被 glob 成文件名。统一走
	``shell_argument`` 后，按 shell 解析要还原成同一个查询词。
	"""
	for query in ('a"b', "$HOME", "全天*"):
		parsed = _next_page(_invoke_search(query, tmp_path=tmp_path))
		assert parsed == [*_prefix(tmp_path), "search", query, "--page", "2"]


def test_next_page_keeps_url_parameters(tmp_path) -> None:
	"""``--url`` 里的网页筛选不在任何 flag 上；丢掉 URL 就丢了整组条件。

	有效关键词会同时以位置参数出现：显式 query 优先于 URL 里的 query，
	两者再一起与 URL 白名单参数合并，结果与本次调用一致。
	"""
	url = "https://www.zhipin.com/web/geek/jobs?query=Python&city=101280100&degree=203"
	parsed = _next_page(_invoke_search("--url", url, tmp_path=tmp_path))
	assert parsed == [*_prefix(tmp_path), "search", "Python", "--url", url, "--page", "2"]


def test_next_page_hint_is_accepted_by_the_cli(tmp_path) -> None:
	"""提示要真能被 CLI 解析，而不只是看起来像命令。"""
	payload = _invoke_search("golang", "--city", "北京", "--sort", "score", tmp_path=tmp_path)
	# 提示自带数据目录，去掉开头的 boss 即可执行。
	argv = _next_page(payload)[1:]
	result = CliRunner().invoke(cli, [*argv, "--help"])
	assert result.exit_code == 0, result.output


def test_no_next_page_hint_when_platform_has_no_more(tmp_path) -> None:
	payload = _invoke_search("golang", has_more=False, tmp_path=tmp_path)
	assert not any("--page" in action for action in payload["hints"]["next_actions"])
	assert payload["pagination"]["has_more"] is False
	assert payload["pagination"]["has_more_source"] == "platform_hasMore"
	assert payload["pagination"]["total_kind"] == "returned_page_count"


def test_unknown_has_more_is_reported_as_null_not_false(tmp_path) -> None:
	"""平台没给 hasMore 时信封必须是 null + unknown，不能谎报 false（Issue #887）。"""
	payload = _invoke_search("golang", has_more=False, has_more_reported=None, tmp_path=tmp_path)

	pagination = payload["pagination"]
	assert pagination["has_more"] is None
	assert pagination["has_more_source"] == "unknown"
	assert pagination["total_kind"] == "returned_page_count"
	assert not any("--page" in action for action in payload["hints"]["next_actions"])
	assert any("下一页" in action for action in payload["hints"].get("operator_actions") or [])
