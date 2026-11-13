"""独占文件锁：不等待、按路径互斥，持有者消失后不需要任何清理。"""

from __future__ import annotations

import errno
import os
import subprocess
import sys
import textwrap
import time

import pytest

from boss_agent_cli.output import is_local_io_error
from boss_agent_cli.services import file_lock
from boss_agent_cli.services.file_lock import exclusive_file_lock, try_lock_exclusive

# Windows 不能删除打开中的文件，下面这些「路径被换成另一个文件」的竞争在那里不会出现。
posix_only = pytest.mark.skipif(os.name == "nt", reason="竞争只存在于允许删除打开中文件的系统")


def test_try_lock_is_exclusive_across_handles_of_the_same_file(tmp_path):
	path = tmp_path / "lease"
	with path.open("a+b") as first, path.open("a+b") as second:
		assert try_lock_exclusive(first)
		# 同一进程里的另一个句柄也拿不到：互斥按「打开的文件」算，不按进程算。
		assert not try_lock_exclusive(second)
	with path.open("a+b") as third:
		assert try_lock_exclusive(third)


def test_second_holder_is_rejected_until_the_first_releases(tmp_path):
	path = tmp_path / "locks" / "run.lock"
	with exclusive_file_lock(path) as first:
		assert first
		with exclusive_file_lock(path) as second:
			assert not second
		# 被拒绝的一方退出时不能动持有者的锁文件，否则第三个人会在新文件上拿到锁。
		assert path.exists()
		with exclusive_file_lock(path) as third:
			assert not third
	with exclusive_file_lock(path) as again:
		assert again


def test_release_removes_the_lock_file(tmp_path):
	path = tmp_path / "run.lock"
	with exclusive_file_lock(path) as acquired:
		assert acquired
		assert path.exists()
	assert not path.exists()


def test_lock_is_released_when_the_body_raises(tmp_path):
	path = tmp_path / "run.lock"
	with pytest.raises(RuntimeError, match="offline failure"):
		with exclusive_file_lock(path):
			raise RuntimeError("offline failure")
	assert not path.exists()
	with exclusive_file_lock(path) as acquired:
		assert acquired


def test_different_paths_do_not_block_each_other(tmp_path):
	with exclusive_file_lock(tmp_path / "a.lock") as first, exclusive_file_lock(tmp_path / "b.lock") as second:
		assert first
		assert second


def test_failure_to_lock_is_reported_against_the_lock_file_and_never_yields(tmp_path, monkeypatch):
	"""加锁设施本身失败（如文件系统不支持文件锁）不是「已被占用」，更不能当成「已取得」。"""
	path = tmp_path / "run.lock"

	def unsupported(handle):
		raise OSError(errno.ENOLCK, "No locks available")

	monkeypatch.setattr(file_lock, "try_lock_exclusive", unsupported)
	entered = []
	with pytest.raises(OSError) as raised:
		with exclusive_file_lock(path) as acquired:
			entered.append(acquired)

	assert entered == []
	assert raised.value.errno == errno.ENOLCK
	# 加锁按文件描述操作，异常里原本没有路径；没有路径会被归成值得重试的网络异常。
	assert raised.value.filename == str(path)
	assert is_local_io_error(raised.value)


@posix_only
def test_lock_on_a_deleted_file_is_not_mistaken_for_the_lock(tmp_path, monkeypatch):
	"""上一个持有者在「打开」与「加锁」之间释放并删除了文件，路径随即被别人重建并持有。

	这时加锁会成功——锁住的是已经删除的孤儿文件。必须发现路径换了文件并让出，
	否则两个执行器各持一把锁，同时认为自己独占。
	"""
	path = tmp_path / "run.lock"
	real_try_lock = file_lock.try_lock_exclusive
	successors = []

	def racing_try_lock(handle):
		if not successors:
			path.unlink()
			successor = path.open("a+b")
			assert real_try_lock(successor)
			successors.append(successor)
		return real_try_lock(handle)

	monkeypatch.setattr(file_lock, "try_lock_exclusive", racing_try_lock)
	try:
		with exclusive_file_lock(path) as acquired:
			assert not acquired
		# 让出的一方不能把后来者的锁文件删掉。
		assert path.exists()
	finally:
		for successor in successors:
			successor.close()


@posix_only
def test_acquire_retries_on_the_current_path_when_the_file_vanished(tmp_path, monkeypatch):
	path = tmp_path / "run.lock"
	real_try_lock = file_lock.try_lock_exclusive
	attempts = []

	def racing_try_lock(handle):
		attempts.append(handle)
		if len(attempts) == 1:
			path.unlink()
		return real_try_lock(handle)

	monkeypatch.setattr(file_lock, "try_lock_exclusive", racing_try_lock)
	with exclusive_file_lock(path) as acquired:
		assert acquired
		assert len(attempts) == 2
		# 持有的是当前路径上的文件：后来者会被它挡住。
		with exclusive_file_lock(path) as second:
			assert not second
	assert not path.exists()


@posix_only
def test_acquire_gives_up_as_busy_when_the_file_keeps_vanishing(tmp_path, monkeypatch):
	path = tmp_path / "run.lock"
	real_try_lock = file_lock.try_lock_exclusive
	attempts = []

	def always_racing_try_lock(handle):
		attempts.append(handle)
		path.unlink()
		return real_try_lock(handle)

	monkeypatch.setattr(file_lock, "try_lock_exclusive", always_racing_try_lock)
	with exclusive_file_lock(path) as acquired:
		assert not acquired
	assert 1 < len(attempts) <= 16


@posix_only
def test_release_leaves_a_replaced_path_alone(tmp_path):
	"""持锁期间路径被外部换成了别的文件：那不是我们的锁，不能删。"""
	path = tmp_path / "run.lock"
	with exclusive_file_lock(path) as acquired:
		assert acquired
		path.unlink()
		path.write_bytes(b"someone else")
	assert path.read_bytes() == b"someone else"


_HOLDER = textwrap.dedent("""\
import sys
from pathlib import Path
from boss_agent_cli.services.file_lock import exclusive_file_lock
with exclusive_file_lock(Path(sys.argv[1])) as acquired:
    pending = Path(sys.argv[2] + ".tmp")
    pending.write_text("held" if acquired else "busy", encoding="utf-8")
    pending.replace(sys.argv[2])
    sys.stdin.read()
""")


def test_lock_held_by_another_process_is_released_when_it_is_killed(tmp_path):
	path = tmp_path / "run.lock"
	ready = tmp_path / "ready"
	holder = subprocess.Popen(
		[sys.executable, "-c", _HOLDER, str(path), str(ready)],
		stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
	)
	try:
		deadline = time.monotonic() + 30
		while not ready.exists() and holder.poll() is None and time.monotonic() < deadline:
			time.sleep(0.02)
		if not ready.exists():
			detail = holder.stderr.read().decode(errors="replace") if holder.poll() is not None else "未在时限内就绪"
			pytest.fail(f"持锁进程没有就绪: {detail}")
		assert ready.read_text(encoding="utf-8") == "held"

		with exclusive_file_lock(path) as acquired:
			assert not acquired

		holder.kill()
		holder.wait(timeout=10)
		# 没有清理步骤：进程消失时内核已经放掉了锁，留下的文件不代表占用。
		assert path.exists()
		with exclusive_file_lock(path) as acquired:
			assert acquired
		assert not path.exists()
	finally:
		if holder.poll() is None:
			holder.kill()
			holder.wait(timeout=10)
		holder.stdin.close()
		holder.stderr.close()
