"""登录态文件的本地权限：只有当前用户可读。"""

import os

import pytest

from boss_agent_cli.auth.token_store import TokenStore

pytestmark = pytest.mark.skipif(os.name == "nt", reason="Windows chmod 不表达 POSIX 权限位")


def _mode(path) -> int:
	return os.stat(path).st_mode & 0o777


def test_new_token_files_are_private(tmp_path, monkeypatch):
	monkeypatch.setenv("BOSS_AGENT_MACHINE_ID", "test-machine")
	auth_dir = tmp_path / "auth"

	store = TokenStore(auth_dir)
	store.save({"cookies": {"wt2": "secret"}})

	assert _mode(auth_dir) == 0o700
	assert _mode(auth_dir / "salt") == 0o600
	assert _mode(auth_dir / "session.enc") == 0o600
	assert store.load() == {"cookies": {"wt2": "secret"}}


def test_existing_loose_permissions_are_tightened(tmp_path, monkeypatch):
	"""历史安装可能已按默认 umask 写成 0755 / 0644；重新打开时必须收紧。"""
	monkeypatch.setenv("BOSS_AGENT_MACHINE_ID", "test-machine")
	auth_dir = tmp_path / "auth"
	TokenStore(auth_dir).save({"token": "saved"})

	os.chmod(auth_dir, 0o755)
	os.chmod(auth_dir / "salt", 0o644)
	os.chmod(auth_dir / "session.enc", 0o644)

	reopened = TokenStore(auth_dir)

	assert _mode(auth_dir) == 0o700
	assert _mode(auth_dir / "salt") == 0o600
	assert _mode(auth_dir / "session.enc") == 0o600
	assert reopened.load() == {"token": "saved"}


def test_clear_keeps_private_salt(tmp_path, monkeypatch):
	monkeypatch.setenv("BOSS_AGENT_MACHINE_ID", "test-machine")
	auth_dir = tmp_path / "auth"
	store = TokenStore(auth_dir)
	store.save({"token": "saved"})

	store.clear()

	assert not (auth_dir / "session.enc").exists()
	assert _mode(auth_dir / "salt") == 0o600
	assert TokenStore(auth_dir).load() is None
