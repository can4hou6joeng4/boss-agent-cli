"""session.enc / api_key.enc 路径状态的 fail-closed 回归测试（Issue #1709）。

读不出来必须上抛，不能返回 None 让命令报「未登录 / 未配置」并引导用户
重新登录或重新填 key，覆盖可能仍然有效的密文。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from boss_agent_cli.ai.config import AIConfigStore
from boss_agent_cli.auth.token_store import TokenStore


def _fail_lstat_for(path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	real_lstat = Path.lstat

	def lstat_fails(self: Path) -> Any:
		if self == path:
			raise PermissionError("permission denied")
		return real_lstat(self)

	monkeypatch.setattr(Path, "lstat", lstat_fails)


def _token_store(tmp_path: Path) -> tuple[TokenStore, Path]:
	store = TokenStore(tmp_path / "auth")
	store.save({"cookies": {"wt2": "offline-placeholder"}})
	path = tmp_path / "auth" / "session.enc"
	assert path.exists()
	return store, path


def _ai_store(tmp_path: Path) -> tuple[AIConfigStore, Path]:
	store = AIConfigStore(tmp_path)
	store.save_api_key("offline-placeholder-key")
	path = tmp_path / "ai" / "api_key.enc"
	assert path.exists()
	return store, path


def test_session_metadata_error_raises_instead_of_logged_out(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
	store, path = _token_store(tmp_path)
	original = path.read_bytes()
	_fail_lstat_for(path, monkeypatch)

	with pytest.raises(PermissionError):
		store.load()

	assert path.read_bytes() == original


def test_session_directory_raises_without_reading(tmp_path: Path) -> None:
	store = TokenStore(tmp_path / "auth")
	path = tmp_path / "auth" / "session.enc"
	path.mkdir(parents=True)

	with pytest.raises(OSError):
		store.load()

	assert path.is_dir()


def test_session_fifo_is_rejected_without_blocking(tmp_path: Path) -> None:
	if os.name == "nt":
		pytest.skip("Windows 不提供 os.mkfifo")
	store = TokenStore(tmp_path / "auth")
	path = tmp_path / "auth" / "session.enc"
	os.mkfifo(path)

	with pytest.raises(OSError):
		store.load()

	assert path.exists()


def test_session_dangling_symlink_is_rejected(tmp_path: Path) -> None:
	store = TokenStore(tmp_path / "auth")
	path = tmp_path / "auth" / "session.enc"
	target = tmp_path / "outside.enc"
	path.symlink_to(target)

	with pytest.raises(OSError):
		store.load()

	assert not target.exists()
	assert path.is_symlink()


def test_missing_session_still_means_logged_out(tmp_path: Path) -> None:
	assert TokenStore(tmp_path / "auth").load() is None


def test_corrupt_session_still_means_logged_out(tmp_path: Path) -> None:
	store = TokenStore(tmp_path / "auth")
	(tmp_path / "auth" / "session.enc").write_bytes(b"not-a-fernet-token")

	assert store.load() is None


def test_api_key_metadata_error_raises_instead_of_not_configured(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
	store, path = _ai_store(tmp_path)
	original = path.read_bytes()
	_fail_lstat_for(path, monkeypatch)

	with pytest.raises(PermissionError):
		store.get_api_key()

	assert path.read_bytes() == original


def test_api_key_fifo_is_rejected_without_blocking(tmp_path: Path) -> None:
	if os.name == "nt":
		pytest.skip("Windows 不提供 os.mkfifo")
	store = AIConfigStore(tmp_path)
	path = tmp_path / "ai" / "api_key.enc"
	os.mkfifo(path)

	with pytest.raises(OSError):
		store.get_api_key()


def test_missing_api_key_still_means_not_configured(tmp_path: Path) -> None:
	assert AIConfigStore(tmp_path).get_api_key() is None


def test_corrupt_api_key_still_means_not_configured(tmp_path: Path) -> None:
	store = AIConfigStore(tmp_path)
	(tmp_path / "ai" / "api_key.enc").write_bytes(b"not-a-fernet-token")

	assert store.get_api_key() is None
