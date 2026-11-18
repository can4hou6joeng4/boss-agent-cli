"""request-budget.sqlite3 损坏时的确定性本地错误（Issue #1336）。"""

import os
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


@pytest.mark.skipif(os.name == "nt", reason="Windows 的 chmod 不阻止写入")
def test_readonly_budget_database_keeps_original_io_error(tmp_path):
	"""库本身完好、只是不可写时不能报「库损坏」并提示删库（Issue #1721）。"""
	db_path = tmp_path / "request-budget.sqlite3"
	sqlite3.connect(db_path).close()
	os.chmod(db_path, 0o444)
	try:
		with pytest.raises(sqlite3.OperationalError) as exc_info:
			with _budget(tmp_path).attempt():
				pass
	finally:
		os.chmod(db_path, 0o600)

	message = str(exc_info.value)
	assert "readonly" in message.lower()
	assert "不是可用的 SQLite 数据库" not in message
	assert "备份" not in message
	assert "自动重建" not in message


@pytest.mark.parametrize(
	"detail",
	["unable to open database file", "database or disk is full", "disk I/O error"],
)
def test_budget_database_local_io_errors_keep_original_error(tmp_path, detail):
	"""只读目录 / 磁盘满 / 设备故障都不是库损坏，不能附加删除重建指引。"""
	with patch(
		"boss_agent_cli.api.request_budget.sqlite3.connect",
		side_effect=sqlite3.OperationalError(detail),
	):
		with pytest.raises(sqlite3.OperationalError) as exc_info:
			with _budget(tmp_path).attempt():
				pass

	assert str(exc_info.value) == detail


def test_decorated_command_uses_generic_io_guidance_for_readonly_budget_db(tmp_path):
	"""装饰器命令拿到原始本地 IO 错误时，恢复指引不能是「删除预算库」。"""
	ctx = MagicMock()
	ctx.obj = {"json_output": True, "data_dir": tmp_path}

	@handle_auth_errors("search")
	def impl(ctx):
		with patch(
			"boss_agent_cli.api.request_budget.sqlite3.connect",
			side_effect=sqlite3.OperationalError("attempt to write a readonly database"),
		):
			with _budget(tmp_path).attempt():
				pass

	with patch("boss_agent_cli.display.handle_error_output") as mock_err:
		impl(ctx)

	kwargs = mock_err.call_args.kwargs
	assert kwargs["code"] == "LOCAL_IO_ERROR"
	assert "备份后删除 request-budget.sqlite3" not in kwargs["recovery_action"]
	assert "目录权限" in kwargs["recovery_action"]
