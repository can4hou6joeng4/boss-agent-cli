"""salt 创建的硬链接回退（Issue #2200）。

不支持硬链接的文件系统（exFAT / 部分 SMB / FUSE）上 ``os.link`` 抛
``ENOTSUP``：回退为独占创建；读取端在锁忙窗口内等待写满的 salt，
半写窗口不能变成「长度异常」的永久损坏。
"""

import errno
import os
from contextlib import contextmanager

import pytest

from boss_agent_cli.services import private_files
from boss_agent_cli.services.private_files import (
	SALT_BYTES,
	_create_salt_exclusive,
	load_or_create_private_salt,
)


def test_link_unsupported_falls_back_to_exclusive_create(tmp_path, monkeypatch):
	auth = tmp_path / "auth"

	def no_link(src, dst, **kwargs):
		raise OSError(errno.ENOTSUP, "Operation not supported")

	monkeypatch.setattr(os, "link", no_link)

	salt = load_or_create_private_salt(auth)

	assert len(salt) == SALT_BYTES
	assert (auth / "salt").read_bytes() == salt


def test_link_unsupported_with_existing_salt_reads_winner(tmp_path, monkeypatch):
	auth = tmp_path / "auth"
	existing = b"w" * SALT_BYTES
	auth.mkdir()
	(auth / "salt").write_bytes(existing)

	def no_link(src, dst, **kwargs):
		raise OSError(errno.EOPNOTSUPP, "Operation not supported")

	monkeypatch.setattr(os, "link", no_link)

	assert load_or_create_private_salt(auth) == existing


def test_exclusive_fallback_reads_winner_on_conflict(tmp_path):
	salt_path = tmp_path / "salt"
	winner = b"w" * SALT_BYTES
	salt_path.write_bytes(winner)

	assert _create_salt_exclusive(salt_path, b"p" * SALT_BYTES) == winner


def test_exclusive_fallback_removes_partial_on_write_failure(tmp_path, monkeypatch):
	salt_path = tmp_path / "salt"

	def broken_write(fd, data):
		raise OSError(errno.ENOSPC, "No space left on device")

	monkeypatch.setattr(os, "write", broken_write)

	with pytest.raises(OSError):
		_create_salt_exclusive(salt_path, b"p" * SALT_BYTES)

	assert not salt_path.exists()


def test_lock_busy_waits_for_partial_then_full_salt(tmp_path, monkeypatch):
	auth = tmp_path / "auth"
	auth.mkdir()
	full = b"s" * SALT_BYTES
	states = iter([b"", full])

	@contextmanager
	def busy_lock(path):
		yield False

	monkeypatch.setattr(private_files, "exclusive_file_lock", busy_lock)
	monkeypatch.setattr(private_files, "_read_salt", lambda path: next(states))
	monkeypatch.setattr(private_files.time, "sleep", lambda seconds: None)

	assert load_or_create_private_salt(auth) == full
