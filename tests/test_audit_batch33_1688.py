"""共享 salt 生命周期的 fail-closed 回归测试（Issue #1688）。

`AIConfigStore` 与 `TokenStore` 共用 `auth/salt`；存在性判断失败时绝不能
生成第二份 salt，否则旧的 session.enc / api_key.enc 会永久无法解密。
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pytest

from boss_agent_cli.ai.config import AIConfigStore
from boss_agent_cli.auth.token_store import TokenStore
from boss_agent_cli.services import private_files


@contextmanager
def _lock_unavailable(_path: Path) -> Iterator[bool]:
	yield False


def _salt_path(data_dir: Path) -> Path:
	return data_dir / "auth" / "salt"


def _store(data_dir: Path) -> AIConfigStore:
	return AIConfigStore(data_dir)


def test_ai_salt_is_not_regenerated_when_exists_lies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	data_dir = tmp_path / "data"
	store = _store(data_dir)
	salt_path = _salt_path(data_dir)
	salt_path.parent.mkdir(parents=True, exist_ok=True)
	original = b"original-salt-16"
	salt_path.write_bytes(original)

	real_exists = Path.exists

	def exists_without_salt(self: Path) -> bool:
		if self == salt_path:
			return False
		return real_exists(self)

	monkeypatch.setattr(Path, "exists", exists_without_salt)

	assert store._get_salt() == original
	assert salt_path.read_bytes() == original


def test_ai_and_token_store_reuse_the_same_salt(tmp_path: Path) -> None:
	data_dir = tmp_path / "data"
	ai_salt = _store(data_dir)._get_salt()

	assert TokenStore(data_dir / "auth")._get_salt() == ai_salt
	assert _salt_path(data_dir).read_bytes() == ai_salt


def test_unreadable_salt_is_not_regenerated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	data_dir = tmp_path / "data"
	store = _store(data_dir)
	salt_path = _salt_path(data_dir)
	salt_path.parent.mkdir(parents=True, exist_ok=True)
	original = b"unreadable-salt!"
	salt_path.write_bytes(original)

	real_read_bytes = Path.read_bytes

	def read_bytes_fails(self: Path) -> bytes:
		if self == salt_path:
			raise PermissionError("permission denied")
		return real_read_bytes(self)

	monkeypatch.setattr(Path, "read_bytes", read_bytes_fails)

	with pytest.raises(PermissionError):
		store._get_salt()
	monkeypatch.setattr(Path, "read_bytes", real_read_bytes)
	assert salt_path.read_bytes() == original


def test_dangling_salt_and_busy_lock_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	data_dir = tmp_path / "data"
	store = _store(data_dir)
	salt_path = _salt_path(data_dir)
	salt_path.parent.mkdir(parents=True, exist_ok=True)
	salt_path.symlink_to(tmp_path / "missing")
	monkeypatch.setattr(private_files, "exclusive_file_lock", _lock_unavailable)

	with pytest.raises(RuntimeError, match="salt"):
		store._get_salt()
	assert salt_path.is_symlink()


def test_existing_salt_is_reused_when_lock_is_busy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	data_dir = tmp_path / "data"
	store = _store(data_dir)
	salt_path = _salt_path(data_dir)
	salt_path.parent.mkdir(parents=True, exist_ok=True)
	original = b"busy-lock-salt!!"
	salt_path.write_bytes(original)
	monkeypatch.setattr(private_files, "exclusive_file_lock", _lock_unavailable)

	assert store._get_salt() == original


def test_api_key_round_trips_after_shared_salt_reuse(tmp_path: Path) -> None:
	data_dir = tmp_path / "data"
	store = _store(data_dir)
	first = store._get_salt()
	store.save_api_key("offline-placeholder-key")

	reopened = _store(data_dir)
	assert reopened._get_salt() == first
	assert reopened.get_api_key() == "offline-placeholder-key"


def test_new_salt_has_private_permissions(tmp_path: Path) -> None:
	import os

	if os.name == "nt":
		pytest.skip("Windows chmod 不表达 POSIX 权限位")

	data_dir = tmp_path / "data"
	salt = _store(data_dir)._get_salt()

	assert len(salt) == 16
	assert _salt_path(data_dir).stat().st_mode & 0o077 == 0
