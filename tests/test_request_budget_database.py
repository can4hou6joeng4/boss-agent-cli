"""request-budget.sqlite3 损坏时的确定性本地错误（Issue #1336）。"""

import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from boss_agent_cli.api.request_budget import RequestBudget
from boss_agent_cli.display import handle_auth_errors


def _budget(tmp_path) -> RequestBudget:
	return RequestBudget(tmp_path, root=tmp_path)


def test_corrupt_budget_database_reports_path_and_rebuild_hint(tmp_path):
	(tmp_path / "request-budget.sqlite3").write_bytes(b"this is not sqlite" * 64)

	with pytest.raises(sqlite3.DatabaseError) as exc_info:
		with _budget(tmp_path).attempt():
			pass

	message = str(exc_info.value)
	assert "request-budget.sqlite3" in message
	assert "备份" in message
	assert "重建" in message


def test_budget_database_lock_keeps_original_error(tmp_path):
	"""锁冲突不是文件损坏：不能附加“删除重建”指引。"""
	with patch(
		"boss_agent_cli.api.request_budget.sqlite3.connect",
		side_effect=sqlite3.OperationalError("database is locked"),
	):
		with pytest.raises(sqlite3.OperationalError) as exc_info:
			with _budget(tmp_path).attempt():
				pass

	assert str(exc_info.value) == "database is locked"


def test_decorated_command_maps_corrupt_budget_db_to_local_io_error(tmp_path):
	(tmp_path / "request-budget.sqlite3").write_bytes(b"this is not sqlite" * 64)
	ctx = MagicMock()
	ctx.obj = {"json_output": True, "data_dir": tmp_path}

	@handle_auth_errors("search")
	def impl(ctx):
		with _budget(tmp_path).attempt():
			pass

	with patch("boss_agent_cli.display.handle_error_output") as mock_err:
		impl(ctx)

	kwargs = mock_err.call_args.kwargs
	assert kwargs["code"] == "LOCAL_IO_ERROR"
	assert kwargs["recoverable"] is False
	assert "request-budget.sqlite3" in kwargs["recovery_action"]
	assert kwargs["recovery_action"] != "重试"


def test_decorated_command_keeps_cache_busy_for_locked_budget_db(tmp_path):
	ctx = MagicMock()
	ctx.obj = {"json_output": True, "data_dir": tmp_path}

	@handle_auth_errors("search")
	def impl(ctx):
		raise sqlite3.OperationalError("database is locked")

	with patch("boss_agent_cli.display.handle_error_output") as mock_err:
		impl(ctx)

	assert mock_err.call_args.kwargs["code"] == "CACHE_BUSY"
