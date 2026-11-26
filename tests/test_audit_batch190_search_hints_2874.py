"""搜索翻页提示的可重放性与缓存命中 TTY 一致性（Issue #2874 #2875）。"""

from __future__ import annotations

import io
import json
import shlex
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from click.testing import CliRunner
from rich.console import Console

from boss_agent_cli.main import cli


def _search_payload(tmp_path: Path, *args: str) -> dict:
	with (
		patch("boss_agent_cli.commands.search.AuthManager"),
		patch("boss_agent_cli.commands.search.get_platform_instance"),
		patch("boss_agent_cli.commands.search.CacheStore") as mock_cache_cls,
		patch("boss_agent_cli.commands.search.run_search_pipeline") as mock_pipeline,
	):
		cache = mock_cache_cls.return_value
		cache.__enter__ = lambda s: cache
		cache.__exit__ = lambda s, *a: None
		cache.get_search.return_value = None
		cache.is_greeted.return_value = False
		mock_pipeline.return_value = SimpleNamespace(
			items=[{"job_id": "j1", "title": "Go 开发", "greeted": False}],
			has_more=True,
			has_more_reported=True,
			total=1,
			stats=SimpleNamespace(pages_scanned=1, jobs_seen=1, jobs_prefiltered=0, detail_checks=0),
		)
		result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "search", *args])
	assert result.exit_code == 0, result.output
	return json.loads(result.stdout)


@pytest.mark.parametrize("query", ["-remote", "--help"])
def test_dashed_query_next_page_hint_is_replayable(tmp_path: Path, query: str) -> None:
	"""URL 里的连字符关键词必须作为第 2 页查询词执行，不能触发选项或帮助。"""
	url = f"https://www.zhipin.com/web/geek/jobs?query={query}"
	payload = _search_payload(tmp_path, "--url", url)
	actions = [item for item in payload["hints"]["next_actions"] if "search" in item and "--page" in item]
	assert len(actions) == 1
	prefix = ["boss", "--data-dir", str(tmp_path.resolve())]
	assert shlex.split(actions[0]) == [*prefix, "search", "--url", url, "--page", "2", "--", query]

	# 照抄提示（去掉 boss 前缀）必须仍是一次合法搜索，而不是选项解析错误。
	replay = _search_payload(tmp_path, *shlex.split(actions[0])[len(prefix) + 1:])
	assert replay["ok"] is True
	assert replay["pagination"]["page"] == 2
	assert replay["pagination"]["has_more"] is True


def test_cached_search_tty_render_keeps_next_page_hint(tmp_path: Path) -> None:
	"""缓存命中时 TTY 表尾与 JSON hints 必须是同一条件、同一条翻页命令（Issue #2875）。"""
	cached = json.dumps({
		"data": [{"job_id": "j1", "title": "缓存职位", "greeted": False}],
		"pagination": {"has_more": True, "has_more_source": "platform_hasMore"},
	})
	screen = io.StringIO()
	with (
		patch("boss_agent_cli.commands.search.AuthManager"),
		patch("boss_agent_cli.commands.search.account_scope", return_value="acct"),
		patch("boss_agent_cli.commands.search.get_platform_instance") as mock_platform,
		patch("boss_agent_cli.commands.search.CacheStore") as mock_cache_cls,
		patch("boss_agent_cli.commands.search.try_save_index", return_value=None),
		patch("boss_agent_cli.commands.search.is_json_mode", return_value=False),
		patch("boss_agent_cli.display.is_json_mode", return_value=False),
		patch("boss_agent_cli.display.console", Console(file=screen, width=500, color_system=None)),
	):
		cache = mock_cache_cls.return_value
		cache.__enter__ = lambda s: cache
		cache.__exit__ = lambda s, *a: None
		cache.get_search.return_value = cached
		cache.is_greeted.return_value = False
		result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "search", "Go", "--page", "2"])

	assert result.exit_code == 0, result.output
	mock_platform.assert_not_called()
	prefix = f"boss --data-dir {shlex.quote(str(tmp_path.resolve()))}"
	assert f"more: {prefix} search Go --page 3" in screen.getvalue()
