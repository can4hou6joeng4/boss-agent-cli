"""批次五十二审计修复（Issue #1734）：独占文件锁按路径状态打开。

`_acquire()` 直接 `open("a+b")`：锁文件被换成 FIFO / 设备会永久阻塞，
悬空链接与目录也不能当锁文件。现在与其它本地路径状态同口径。
"""

import os
import stat

import pytest

from boss_agent_cli.services.file_lock import exclusive_file_lock


@pytest.mark.skipif(os.name == "nt", reason="Windows 没有 POSIX FIFO")
def test_fifo_lock_file_fails_closed_instead_of_blocking(tmp_path):
	path = tmp_path / "locks" / "run.lock"
	path.parent.mkdir(parents=True)
	os.mkfifo(path)

	with pytest.raises(OSError) as exc_info:
		with exclusive_file_lock(path):
			pytest.fail("FIFO 锁文件不能被当成可加锁目标")

	assert exc_info.value.filename == str(path)
	assert stat.S_ISFIFO(path.lstat().st_mode), "FIFO 不能被替换或删除"


def test_symlink_lock_file_is_refused_and_kept(tmp_path):
	target = tmp_path / "real.lock"
	target.write_bytes(b"")
	path = tmp_path / "run.lock"
	try:
		path.symlink_to(target)
	except (OSError, NotImplementedError):
		pytest.skip("当前文件系统不支持符号链接")

	with pytest.raises(OSError) as exc_info:
		with exclusive_file_lock(path):
			pytest.fail("锁必须落在普通文件上，不能锁住链接目标")

	assert exc_info.value.filename == str(path)
	assert path.is_symlink()
	assert target.exists()


def test_dangling_symlink_lock_file_is_refused(tmp_path):
	path = tmp_path / "run.lock"
	try:
		path.symlink_to(tmp_path / "missing.lock")
	except (OSError, NotImplementedError):
		pytest.skip("当前文件系统不支持符号链接")

	with pytest.raises(OSError):
		with exclusive_file_lock(path):
			pytest.fail("悬空链接不能当锁文件")


def test_directory_lock_path_is_refused(tmp_path):
	path = tmp_path / "run.lock"
	path.mkdir()

	with pytest.raises(OSError):
		with exclusive_file_lock(path):
			pytest.fail("目录不能当锁文件")


def test_missing_lock_file_is_created_and_removed(tmp_path):
	path = tmp_path / "locks" / "run.lock"

	with exclusive_file_lock(path) as acquired:
		assert acquired
		assert path.is_file()

	assert not path.exists()


def test_regular_lock_file_still_works(tmp_path):
	path = tmp_path / "run.lock"
	path.write_bytes(b"")

	with exclusive_file_lock(path) as acquired:
		assert acquired

	assert not path.exists()
