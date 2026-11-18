"""共享 salt 创建与校验的 fail-closed 回归测试（Issue #1699）。

空/半截 salt 会让 PBKDF2 派生出一把新密钥，已保存的 session.enc /
api_key.enc 永久解不开；FIFO / 目录 / 悬空链接也不能直接 read_bytes()。
"""

from __future__ import annotations

import errno
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from boss_agent_cli.services import private_files
from boss_agent_cli.services.private_files import SALT_BYTES, load_or_create_private_salt


def _auth_dir(tmp_path: Path) -> Path:
	return tmp_path / "auth"


def _salt_path(tmp_path: Path) -> Path:
	return _auth_dir(tmp_path) / "salt"


def test_empty_salt_fails_closed(tmp_path: Path) -> None:
	auth_dir = _auth_dir(tmp_path)
	auth_dir.mkdir()
	salt_path = auth_dir / "salt"
	salt_path.write_bytes(b"")

	with pytest.raises(RuntimeError, match="长度异常"):
		load_or_create_private_salt(auth_dir)
	assert salt_path.read_bytes() == b""


def test_partial_salt_fails_closed(tmp_path: Path) -> None:
	auth_dir = _auth_dir(tmp_path)
	auth_dir.mkdir()
	salt_path = auth_dir / "salt"
	salt_path.write_bytes(b"short")

	with pytest.raises(RuntimeError, match="长度异常"):
		load_or_create_private_salt(auth_dir)
	assert salt_path.read_bytes() == b"short"


def test_empty_salt_is_not_replaced_even_when_lock_is_unavailable(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
	auth_dir = _auth_dir(tmp_path)
	auth_dir.mkdir()
	salt_path = auth_dir / "salt"
	salt_path.write_bytes(b"")

	from contextlib import contextmanager
	from typing import Iterator

	@contextmanager
	def lock_unavailable(_path: Path) -> Iterator[bool]:
		yield False

	monkeypatch.setattr(private_files, "exclusive_file_lock", lock_unavailable)

	with pytest.raises(RuntimeError, match="长度异常"):
		load_or_create_private_salt(auth_dir)
	assert salt_path.read_bytes() == b""


def test_directory_salt_is_rejected(tmp_path: Path) -> None:
	auth_dir = _auth_dir(tmp_path)
	salt_path = auth_dir / "salt"
	salt_path.mkdir(parents=True)

	with pytest.raises(RuntimeError, match="路径状态异常"):
		load_or_create_private_salt(auth_dir)
	assert salt_path.is_dir()


def test_dangling_salt_is_rejected_without_creating_target(tmp_path: Path) -> None:
	auth_dir = _auth_dir(tmp_path)
	auth_dir.mkdir()
	target = tmp_path / "outside-salt"
	(_salt_path(tmp_path)).symlink_to(target)

	with pytest.raises(RuntimeError, match="salt"):
		load_or_create_private_salt(auth_dir)
	assert not target.exists()
	assert _salt_path(tmp_path).is_symlink()


def test_fifo_salt_is_rejected_without_reading(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	auth_dir = _auth_dir(tmp_path)
	auth_dir.mkdir()
	salt_path = auth_dir / "salt"
	salt_path.write_bytes(b"placeholder")

	def read_fails(path: Path) -> bytes | None:
		if path == salt_path:
			raise OSError(errno.EINVAL, "路径不是普通文件", str(path))
		return b"x" * SALT_BYTES

	monkeypatch.setattr(private_files, "read_bytes_if_regular", read_fails)

	with pytest.raises(RuntimeError, match="路径状态异常"):
		load_or_create_private_salt(auth_dir)


def test_new_salt_is_complete_and_leaves_no_temporary_file(tmp_path: Path) -> None:
	auth_dir = _auth_dir(tmp_path)

	salt = load_or_create_private_salt(auth_dir)

	assert len(salt) == SALT_BYTES
	assert (auth_dir / "salt").read_bytes() == salt
	assert [entry.name for entry in auth_dir.iterdir() if entry.name.startswith(".salt.")] == []


def test_concurrent_creator_wins_and_value_is_reused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	auth_dir = _auth_dir(tmp_path)
	auth_dir.mkdir()
	salt_path = auth_dir / "salt"
	winner = b"winner-salt-16!!"
	salt_path.write_bytes(winner)

	# 模拟「读的时候还没有、写完后发现另一个创建者已落位」的竞态。
	monkeypatch.setattr(private_files, "_read_salt", MagicMock(side_effect=[None, winner, winner]))
	monkeypatch.setattr(
		private_files.os,
		"link",
		MagicMock(side_effect=FileExistsError(errno.EEXIST, "exists", str(salt_path))),
	)

	assert load_or_create_private_salt(auth_dir) == winner
	assert salt_path.read_bytes() == winner


def test_permission_error_still_propagates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	auth_dir = _auth_dir(tmp_path)
	auth_dir.mkdir()
	salt_path = auth_dir / "salt"
	salt_path.write_bytes(b"x" * SALT_BYTES)
	real_read_bytes = Path.read_bytes

	def read_bytes_fails(self: Path) -> bytes:
		if self == salt_path:
			raise PermissionError("permission denied")
		return real_read_bytes(self)

	monkeypatch.setattr(Path, "read_bytes", read_bytes_fails)

	with pytest.raises(PermissionError):
		load_or_create_private_salt(auth_dir)
	assert real_read_bytes(salt_path) == b"x" * SALT_BYTES
