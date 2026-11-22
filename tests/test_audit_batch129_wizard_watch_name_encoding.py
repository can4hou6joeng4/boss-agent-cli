"""审计批次 129：向导 watch 动作按 CLI 同口径拒绝不可编码名称（Issue #2512）。

向导的 ``candidate_watch`` 之前只检查名称非空：JSON 输入里的孤立代理项
（如 ``"\\udcff"``）会一路走到 SQLite 绑定参数，抛 ``UnicodeEncodeError``，
被顶层兜成带磁盘指引的 ``LOCAL_IO_ERROR``。CLI 的 ``watch add/remove/run``
用 ``saved_search_name_error`` 在入口拒绝同一名称。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.output import Logger
from boss_agent_cli.wizard.actions import DEFAULT_ACTIONS, ActionContext
from boss_agent_cli.wizard.runner import WorkflowActionError

_BAD_NAME = "\udcff"


def _context(tmp_path: Path) -> ActionContext:
	return ActionContext(data_dir=tmp_path, platform="zhipin", role="candidate", logger=Logger())


def _watch(tmp_path: Path, inputs: dict[str, object]) -> object:
	return DEFAULT_ACTIONS["candidate_watch"](_context(tmp_path), inputs, {})


def _list(tmp_path: Path) -> list[dict[str, object]]:
	with CacheStore(tmp_path / "cache" / "boss_agent.db") as cache:
		return cache.list_saved_searches()


@pytest.mark.parametrize(
	"inputs",
	[
		{"action": "add", "name": _BAD_NAME, "query": "Python"},
		{"action": "run", "name": _BAD_NAME},
		{"action": "remove", "name": _BAD_NAME},
	],
)
def test_actions_reject_unencodable_name(tmp_path: Path, inputs: dict[str, object]) -> None:
	with pytest.raises(WorkflowActionError) as exc_info:
		_watch(tmp_path, inputs)
	assert exc_info.value.code == "INVALID_PARAM"
	assert "无法保存" in str(exc_info.value)
	assert _list(tmp_path) == []


def test_normal_unicode_name_still_works(tmp_path: Path) -> None:
	added = _watch(
		tmp_path,
		{"action": "add", "name": "每日 监控", "query": "Python"},
	)

	assert added.data["name"] == "每日 监控"
	assert [item["name"] for item in _list(tmp_path)] == ["每日 监控"]
