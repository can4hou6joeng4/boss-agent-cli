"""数据目录与 AI 密钥文件的本地权限：只有当前用户可读。

`auth/` 的同类收紧见 `test_token_store_permissions.py`；这里覆盖数据根目录
与 `AIConfigStore`（ai/ 目录、api_key.enc、auth/salt）。
"""

import os

import pytest

from boss_agent_cli.ai.config import AIConfigStore
from boss_agent_cli.main import _resolve_data_dir
from boss_agent_cli.services.private_files import chmod_best_effort, tighten_private_directory

pytestmark = pytest.mark.skipif(os.name == "nt", reason="Windows chmod 不表达 POSIX 权限位")


def _mode(path) -> int:
	return os.stat(path).st_mode & 0o777


# ── 数据根目录 ─────────────────────────────────────────────


def test_new_data_dir_is_private(tmp_path):
	data_dir = _resolve_data_dir(str(tmp_path / "jobs" / "boss-data"))

	assert data_dir.is_dir()
	assert _mode(data_dir) == 0o700


def test_existing_loose_data_dir_is_tightened(tmp_path):
	"""历史安装的 ~/.boss-agent 可能是 0755；启动时尽力收紧。"""
	data_dir = tmp_path / "boss-data"
	data_dir.mkdir()
	os.chmod(data_dir, 0o755)

	assert _resolve_data_dir(str(data_dir)) == data_dir
	assert _mode(data_dir) == 0o700


def test_world_writable_data_dir_is_left_alone(tmp_path):
	"""--data-dir /tmp 这类共享目录不是本程序的数据目录，收紧会破坏其他进程。"""
	shared = tmp_path / "shared"
	shared.mkdir()
	os.chmod(shared, 0o777)

	tighten_private_directory(shared)

	assert _mode(shared) == 0o777


def test_read_only_data_dir_is_left_alone(tmp_path):
	"""只读目录是用户的显式选择；收紧会恢复写权限并掩盖本该报的本地 IO 错误。"""
	frozen = tmp_path / "frozen"
	frozen.mkdir()
	os.chmod(frozen, 0o500)
	try:
		tighten_private_directory(frozen)
		assert _mode(frozen) == 0o500
	finally:
		os.chmod(frozen, 0o700)


def test_tighten_tolerates_chmod_failure(tmp_path, monkeypatch):
	"""不支持 chmod 的挂载点（网络盘、部分容器卷）不能阻断启动。"""
	data_dir = tmp_path / "boss-data"
	data_dir.mkdir()
	os.chmod(data_dir, 0o755)

	def _raise(_path, _mode):
		raise PermissionError("chmod not supported")

	monkeypatch.setattr(os, "chmod", _raise)
	chmod_best_effort(data_dir, 0o700)
	tighten_private_directory(data_dir)

	assert _mode(data_dir) == 0o755


# ── AI 密钥 ────────────────────────────────────────────────


def test_ai_key_files_are_private(tmp_path, monkeypatch):
	monkeypatch.setenv("BOSS_AGENT_MACHINE_ID", "test-machine")
	store = AIConfigStore(tmp_path)

	store.save_api_key("sk-test-not-real")

	assert _mode(tmp_path / "ai") == 0o700
	assert _mode(tmp_path / "ai" / "api_key.enc") == 0o600
	assert _mode(tmp_path / "auth") == 0o700
	assert _mode(tmp_path / "auth" / "salt") == 0o600
	assert store.get_api_key() == "sk-test-not-real"


def test_existing_loose_ai_key_files_are_tightened(tmp_path, monkeypatch):
	monkeypatch.setenv("BOSS_AGENT_MACHINE_ID", "test-machine")
	AIConfigStore(tmp_path).save_api_key("sk-test-not-real")
	os.chmod(tmp_path / "ai", 0o755)
	os.chmod(tmp_path / "ai" / "api_key.enc", 0o644)
	os.chmod(tmp_path / "auth", 0o755)
	os.chmod(tmp_path / "auth" / "salt", 0o644)

	reopened = AIConfigStore(tmp_path)

	assert _mode(tmp_path / "ai") == 0o700
	assert _mode(tmp_path / "ai" / "api_key.enc") == 0o600
	assert _mode(tmp_path / "auth" / "salt") == 0o600
	assert reopened.get_api_key() == "sk-test-not-real"
