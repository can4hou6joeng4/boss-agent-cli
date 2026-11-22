"""审计批次 128：向导 watch 动作与 CLI 的入口 / 读取校验对齐（Issue #2511）。

向导的 ``candidate_watch`` 之前用 ``_required_str`` / ``_required`` 读取名称与
关键词，只拒绝空串：纯空白名称与关键词能写进本地记录，随后 CLI
``watch run/remove`` 按空白名称报 ``INVALID_PARAM``，``watch run --all`` 又给出
无法执行的删除指引。读取侧也未复用 CLI 的关键词必填 / 类型校验（#1798、#1338）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.output import Logger
from boss_agent_cli.wizard.actions import DEFAULT_ACTIONS, ActionContext
from boss_agent_cli.wizard.runner import WorkflowActionError


def _context(tmp_path: Path) -> ActionContext:
	return ActionContext(data_dir=tmp_path, platform="zhipin", role="candidate", logger=Logger())


def _watch(tmp_path: Path, inputs: dict[str, object]) -> object:
	return DEFAULT_ACTIONS["candidate_watch"](_context(tmp_path), inputs, {})


def _list(tmp_path: Path) -> list[dict[str, object]]:
	with CacheStore(tmp_path / "cache" / "boss_agent.db") as cache:
		return cache.list_saved_searches()


def _seed(tmp_path: Path, name: str, params: dict[str, object]) -> None:
	with CacheStore(tmp_path / "cache" / "boss_agent.db") as cache:
		cache.save_saved_search(name, params)


def test_add_rejects_blank_name(tmp_path: Path) -> None:
	with pytest.raises(WorkflowActionError) as exc_info:
		_watch(tmp_path, {"action": "add", "name": "   ", "query": "Python"})
	assert exc_info.value.code == "INVALID_PARAM"
	assert "name" in str(exc_info.value)
	assert _list(tmp_path) == []


def test_add_rejects_blank_query(tmp_path: Path) -> None:
	with pytest.raises(WorkflowActionError) as exc_info:
		_watch(tmp_path, {"action": "add", "name": "daily", "query": "   "})
	assert exc_info.value.code == "INVALID_PARAM"
	assert "query" in str(exc_info.value)
	assert _list(tmp_path) == []


def test_remove_rejects_blank_name(tmp_path: Path) -> None:
	with pytest.raises(WorkflowActionError) as exc_info:
		_watch(tmp_path, {"action": "remove", "name": "   "})
	assert exc_info.value.code == "INVALID_PARAM"
	assert "name" in str(exc_info.value)


def test_run_rejects_stored_blank_query(tmp_path: Path) -> None:
	_seed(tmp_path, "legacy", {"query": "   "})

	with pytest.raises(WorkflowActionError) as exc_info:
		_watch(tmp_path, {"action": "run", "name": "legacy"})
	assert exc_info.value.code == "SAVED_SEARCH_INVALID"
	assert "关键词不能为空" in str(exc_info.value)
	# 恢复指引必须能用：删除命令引用触发本次运行的数据目录。
	assert "watch" in exc_info.value.recovery_action
	assert str(tmp_path.resolve()) in exc_info.value.recovery_action


def test_run_rejects_drifted_param_type(tmp_path: Path) -> None:
	_seed(tmp_path, "legacy", {"query": "Python", "city": ["北京"]})

	with pytest.raises(WorkflowActionError) as exc_info:
		_watch(tmp_path, {"action": "run", "name": "legacy"})
	assert exc_info.value.code == "SAVED_SEARCH_INVALID"
	assert "city" in str(exc_info.value)


def test_run_rejects_unknown_active_level(tmp_path: Path) -> None:
	_seed(tmp_path, "legacy", {"query": "Python", "active": "不存在"})

	with pytest.raises(WorkflowActionError) as exc_info:
		_watch(tmp_path, {"action": "run", "name": "legacy"})
	assert exc_info.value.code == "SAVED_SEARCH_INVALID"
	assert "活跃度" in str(exc_info.value)


def test_normal_add_still_stores_params(tmp_path: Path) -> None:
	added = DEFAULT_ACTIONS["candidate_watch"](
		_context(tmp_path),
		{"action": "add", "name": "daily", "query": "Python", "welfare": ["双休"]},
		{},
	)

	assert added.data["params"]["welfare"] == "双休"
	items = _list(tmp_path)
	assert [item["name"] for item in items] == ["daily"]
	assert items[0]["params"]["query"] == "Python"
