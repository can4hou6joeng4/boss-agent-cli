"""审计批次 137：saved_searches 坏记录的恢复路径（Issue #2529）。

BLOB 名称的坏记录曾以 ``b'legacy'`` 进入提示，删除命令必然失败；元数据列损坏
在 ``search --preset`` / 向导 ``candidate_watch run`` 又落成与预设无关的
``CACHE_RECORD_INVALID``。两处都要给可执行的删除重建指引。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner
import pytest

from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.main import cli
from boss_agent_cli.output import Logger
from boss_agent_cli.wizard.actions import DEFAULT_ACTIONS, ActionContext
from boss_agent_cli.wizard.runner import WorkflowActionError


def _db_path(tmp_path: Path) -> Path:
	return tmp_path / "cache" / "boss_agent.db"


def _insert_row(tmp_path: Path, name: bytes, params: str = "{not json") -> None:
	# 先建表，再插入 BLOB 存储类的 name + 损坏的 params。
	with CacheStore(_db_path(tmp_path)):
		pass
	conn = sqlite3.connect(_db_path(tmp_path))
	conn.execute(
		"INSERT INTO saved_searches (name, params, created_at, updated_at) VALUES (?, ?, ?, ?)",
		(sqlite3.Binary(name), params, 1.0, 1.0),
	)
	conn.commit()
	conn.close()


def _corrupt_timestamp(tmp_path: Path, name: str) -> None:
	conn = sqlite3.connect(_db_path(tmp_path))
	conn.execute("UPDATE saved_searches SET created_at = 'oops' WHERE name = ?", (name,))
	conn.commit()
	conn.close()


def _forbidden_factory(*args: object, **kwargs: object) -> object:
	raise AssertionError("非法输入不应构造平台实例")


def _action_context(tmp_path: Path) -> ActionContext:
	return ActionContext(
		data_dir=tmp_path,
		platform="zhipin",
		role="candidate",
		logger=Logger(),
		candidate_factory=_forbidden_factory,
		recruiter_factory=_forbidden_factory,
	)


def test_corrupt_blob_name_is_listed_as_deletable_text(tmp_path: Path) -> None:
	_insert_row(tmp_path, b"legacy")

	with CacheStore(_db_path(tmp_path)) as cache:
		items, corrupt = cache.list_saved_searches_with_corrupt()
		assert items == []
		assert corrupt == ["legacy"]
		# 提示里的名称必须能真正删掉该行（BLOB 与 TEXT 的删除口径一致）。
		assert cache.delete_saved_search("legacy") is True
		assert cache.list_saved_searches_with_corrupt() == ([], [])


def test_undecodable_blob_name_points_to_manual_cleanup(tmp_path: Path) -> None:
	_insert_row(tmp_path, b"bad\xff\xfe")

	with CacheStore(_db_path(tmp_path)) as cache:
		_, corrupt = cache.list_saved_searches_with_corrupt()

	assert len(corrupt) == 1
	assert "手工清理" in corrupt[0]


def test_watch_list_hint_uses_decoded_blob_name(tmp_path: Path) -> None:
	_insert_row(tmp_path, b"legacy")

	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "watch", "list"])

	assert result.exit_code == 0, result.output
	payload = json.loads(result.stdout)
	assert payload["hints"]["corrupt"] == ["legacy"]
	assert all("b'legacy'" not in action for action in payload["hints"]["operator_actions"])


def test_search_preset_reports_corrupt_timestamp_as_saved_search_invalid(tmp_path: Path) -> None:
	runner = CliRunner()
	added = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "preset", "add", "p", "python"])
	assert added.exit_code == 0, added.output
	_corrupt_timestamp(tmp_path, "p")

	with patch("boss_agent_cli.commands.search.AuthManager", side_effect=AssertionError("不应构造 AuthManager")):
		result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "search", "--preset", "p"])

	assert result.exit_code == 1, result.output
	payload = json.loads(result.stdout)
	assert payload["ok"] is False
	assert payload["error"]["code"] == "SAVED_SEARCH_INVALID", payload["error"]
	hints = payload["hints"]["next_actions"]
	assert any("preset remove" in action for action in hints), hints
	assert any("preset add" in action for action in hints), hints


def test_wizard_watch_run_reports_corrupt_timestamp_as_saved_search_invalid(tmp_path: Path) -> None:
	with CacheStore(_db_path(tmp_path)) as cache:
		cache.save_saved_search("w", {"query": "python"})
	_corrupt_timestamp(tmp_path, "w")

	with pytest.raises(WorkflowActionError) as exc_info:
		DEFAULT_ACTIONS["candidate_watch"](_action_context(tmp_path), {"action": "run", "name": "w"}, {})

	assert exc_info.value.code == "SAVED_SEARCH_INVALID"
	assert "watch remove" in exc_info.value.recovery_action
	assert "watch add" in exc_info.value.recovery_action
