"""批次十审计修复（Issue #1546–#1550）。

- #1546：stats / digest 在默认 json 格式下静默忽略 `-o`，空串落到 LOCAL_IO_ERROR；
- #1547：clean 把 SQLite 锁冲突 / 只读错误归为缓存损坏并指引删库；
- #1548：clean --risk-lock 被无关的损坏缓存库提前阻断；
- #1549：联系人分页上限是确定性失败，却返回 recoverable=true（CLI 与 wizard）；
- #1550：detail 的 UNKNOWN 未按只读命令归一。
"""

import json
import sqlite3
from pathlib import Path

from click.testing import CliRunner
import pytest

from boss_agent_cli.api import cdp_risk_lock
from boss_agent_cli.cache.store import CacheDatabaseInvalid
from boss_agent_cli.commands import clean as clean_mod
from boss_agent_cli.commands import detail as detail_mod
from boss_agent_cli.display import _READ_ONLY_COMMANDS, read_only_platform_code
from boss_agent_cli.main import cli


def _invoke(tmp_path: Path, *args: str):
	return CliRunner().invoke(cli, ["--json", "--data-dir", str(tmp_path), *args])


def _envelope(result) -> dict:
	return json.loads(result.stdout)


# ── #1546：stats / digest 的 -o ─────────────────────────────────────


def test_stats_rejects_output_without_html_format(tmp_path):
	"""默认 json 下 -o 被静默忽略：文件不存在却返回 ok=true（Issue #1546）。"""
	target = tmp_path / "report.html"

	result = _invoke(tmp_path, "stats", "-o", str(target))

	assert result.exit_code == 1, result.stdout
	error = _envelope(result)["error"]
	assert error["code"] == "INVALID_PARAM"
	assert "--format html" in error["message"]
	assert not target.exists()


def test_stats_rejects_empty_output_with_html_format(tmp_path):
	"""空 -o 经 click.Path 落成 Path('.')，不能变成 LOCAL_IO_ERROR（Issue #1546）。"""
	result = _invoke(tmp_path, "stats", "--format", "html", "-o", "")

	assert result.exit_code == 1, result.stdout
	error = _envelope(result)["error"]
	assert error["code"] == "INVALID_PARAM"
	assert "不能为空" in error["message"]


def test_stats_html_output_still_writes_file(tmp_path):
	"""显式 html + 路径的既有行为不变。"""
	target = tmp_path / "report.html"

	result = _invoke(tmp_path, "stats", "--format", "html", "-o", str(target))

	assert result.exit_code == 0, result.stdout
	assert target.is_file()
	assert _envelope(result)["data"]["path"] == str(target)


def test_digest_rejects_output_without_md_format_without_login(tmp_path):
	"""-o 与默认 json 同时出现时，先于登录检查返回 INVALID_PARAM。"""
	result = _invoke(tmp_path, "digest", "-o", str(tmp_path / "d.md"))

	assert result.exit_code == 1, result.stdout
	error = _envelope(result)["error"]
	assert error["code"] == "INVALID_PARAM"
	assert "--format md" in error["message"]


def test_digest_rejects_empty_output_with_md_format(tmp_path):
	result = _invoke(tmp_path, "digest", "--format", "md", "-o", "")

	assert result.exit_code == 1, result.stdout
	error = _envelope(result)["error"]
	assert error["code"] == "INVALID_PARAM"
	assert "不能为空" in error["message"]


# ── #1547：clean 的 SQLite 锁冲突 / 只读错误 ────────────────────────


@pytest.mark.parametrize(
	"message",
	["database is locked", "attempt to write a readonly database", "disk I/O error"],
)
def test_open_cache_db_keeps_busy_and_local_io_errors(monkeypatch, tmp_path, message):
	"""锁冲突与本地 IO 不是库内容损坏：原样上抛交给 main 分流（Issue #1547）。"""

	def _boom(*args, **kwargs):
		raise sqlite3.OperationalError(message)

	monkeypatch.setattr(clean_mod.sqlite3, "connect", _boom)

	with pytest.raises(sqlite3.OperationalError):
		clean_mod._open_cache_db(tmp_path / "boss_agent.db")


def test_open_cache_db_still_maps_corruption(tmp_path):
	corrupt = tmp_path / "boss_agent.db"
	corrupt.write_bytes(b"not a sqlite database")

	with pytest.raises(CacheDatabaseInvalid):
		clean_mod._open_cache_db(corrupt)


def test_clean_reports_busy_cache_instead_of_corruption(tmp_path, monkeypatch):
	"""另一连接持有写事务时，clean 报可重试的 CACHE_BUSY，不能引导删库。"""
	data_dir = tmp_path
	db_path = data_dir / "cache" / "boss_agent.db"
	db_path.parent.mkdir(parents=True)
	holder = sqlite3.connect(db_path)
	holder.execute("CREATE TABLE search_cache (created_at REAL, response TEXT)")
	holder.execute("INSERT INTO search_cache VALUES (0, 'x')")
	holder.commit()
	holder.execute("BEGIN EXCLUSIVE")

	real_connect = sqlite3.connect
	monkeypatch.setattr(
		clean_mod.sqlite3,
		"connect",
		lambda path, **kwargs: real_connect(path, timeout=0.05, **kwargs),
	)
	try:
		result = _invoke(data_dir, "clean")
	finally:
		holder.rollback()
		holder.close()

	assert result.exit_code == 1, result.stdout
	error = _envelope(result)["error"]
	assert error["code"] == "CACHE_BUSY"
	assert error["recoverable"] is True
	assert "不需要删除" in error["recovery_action"]
	assert "CACHE_DATABASE_INVALID" not in result.stdout


# ── #1548：clean --risk-lock 不被损坏库阻断 ────────────────────────


def test_clean_risk_lock_is_cleared_even_when_cache_db_is_corrupt(tmp_path):
	data_dir = tmp_path
	(db_path := data_dir / "cache" / "boss_agent.db").parent.mkdir(parents=True)
	db_path.write_bytes(b"not a sqlite database")
	cdp_risk_lock.write_lock(data_dir, None, reason="ENVIRONMENT_RISK", platform_code=37)
	lock_file = data_dir / cdp_risk_lock.LOCK_FILENAME
	assert lock_file.exists()

	result = _invoke(data_dir, "clean", "--risk-lock")

	# 搜索缓存步骤仍会因真正的坏库失败，但风险锁必须在它之前被清除
	# （Issue #1548）：锁清理不读缓存库。
	assert not lock_file.exists(), result.stdout
	assert result.exit_code == 1, result.stdout
	assert _envelope(result)["error"]["code"] == "CACHE_DATABASE_INVALID"


# ── #1549：联系人分页上限是确定性失败 ──────────────────────────────


def test_friend_lookup_limit_is_not_recoverable():
	from unittest.mock import patch

	from boss_agent_cli.services.contact_lookup import FriendLookupLimitExceeded

	with patch(
		"boss_agent_cli.commands._contact_lookup.find_friend",
		side_effect=FriendLookupLimitExceeded("沟通列表分页遍历超过上限，未能确认联系人是否存在，请重试"),
	), patch("boss_agent_cli.commands.chatmsg.get_platform_instance"), \
			patch("boss_agent_cli.commands.chatmsg.AuthManager"):
		result = CliRunner().invoke(cli, ["--data-dir", str(Path("/tmp/does-not-matter")), "chatmsg", "10001"])

	assert result.exit_code == 1, result.stdout
	error = json.loads(result.stdout)["error"]
	assert error["code"] == "NETWORK_ERROR"
	# 重放同一段查询不会改变结果，按 recoverable 决策的自动重试会再翻 50 页。
	assert error["recoverable"] is False


# ── #1550：detail 的 UNKNOWN 按只读归一 ─────────────────────────────


def test_detail_is_registered_as_read_only():
	assert "detail" in _READ_ONLY_COMMANDS
	assert read_only_platform_code("detail", "UNKNOWN") == "NETWORK_ERROR"
	assert read_only_platform_code("detail", "ACCOUNT_RISK") == "ACCOUNT_RISK"


def test_detail_unknown_platform_error_is_retryable(tmp_path, monkeypatch):
	"""detail 未收到可识别响应时按只读命令归一，不引导核对「操作是否生效」。"""
	monkeypatch.setattr(
		detail_mod, "_detail_via_httpx",
		lambda *args, **kwargs: (None, ("UNKNOWN", "平台返回了未识别的错误", None)),
	)
	monkeypatch.setattr(detail_mod, "_detail_via_browser", lambda *args, **kwargs: (None, None))

	with monkeypatch.context() as patch_ctx:
		from unittest.mock import MagicMock

		patch_ctx.setattr(detail_mod, "AuthManager", MagicMock())
		patch_ctx.setattr(detail_mod, "get_platform_instance", MagicMock())
		patch_ctx.setattr(detail_mod, "CacheStore", MagicMock())
		result = _invoke(tmp_path, "detail", "sec_001", "--job-id", "j1")

	assert result.exit_code == 1, result.stdout
	error = _envelope(result)["error"]
	assert error["code"] == "NETWORK_ERROR"
	assert error["recoverable"] is True
	assert "是否生效" not in (error["recovery_action"] or "")
