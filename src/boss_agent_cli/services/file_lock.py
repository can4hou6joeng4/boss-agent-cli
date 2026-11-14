"""跨进程独占文件锁：不等待，持有者退出时由操作系统释放。

互斥靠的是内核里的锁，而不是「锁文件存在」这件事：进程崩溃、被 kill 或断电
之后没有陈旧锁需要清理，也不必探测 PID 是否存活。POSIX 用 ``flock``（按打开
文件描述加锁，同一进程里两次 ``open`` 也互斥），Windows 用 ``msvcrt.locking``。

纯标准库，不依赖 click / display。
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, BinaryIO, cast

_WINDOWS = os.name == "nt"

#: 获取期间锁文件被上一个持有者删除时的重试上限；用尽按「已被占用」处理。
_ACQUIRE_ATTEMPTS = 8


def run_lock_path(directory: Path, run_id: str) -> Path:
	"""按 run_id 生成锁文件路径。

	run_id 是调用方给的任意字符串（本地自由文本），不能变成路径的一部分：
	文件名取它的摘要，互斥按文件描述符算，不依赖目录层级。
	"""
	digest = hashlib.sha256(run_id.encode("utf-8", "surrogatepass")).hexdigest()
	return directory / f"{digest}.lock"


def try_lock_exclusive(handle: BinaryIO) -> bool:
	"""对已打开的文件尝试加独占锁；已被其他句柄持有时返回 False，不等待。"""
	try:
		if _WINDOWS:
			import msvcrt

			handle.seek(0)
			windows_lock = cast(Any, msvcrt)
			windows_lock.locking(handle.fileno(), windows_lock.LK_NBLCK, 1)
		else:
			import fcntl

			fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
	except (BlockingIOError, PermissionError):
		return False
	except OSError as error:
		if error.errno in (11, 13, 35, 36):
			return False
		raise
	return True


@contextmanager
def exclusive_file_lock(path: Path) -> Iterator[bool]:
	"""持有 ``path`` 的独占锁期间 yield True；已被占用时 yield False，不等待。

	释放时删除锁文件，目录不会随使用次数增长。删除带来的竞争在获取端消除：
	加锁成功后必须确认 ``path`` 仍指向被锁住的那个文件，否则锁落在已删除的
	孤儿文件上，而路径可能已被别人重建并持有。

	锁文件无法准备或无法加锁（目录不可用、磁盘已满、文件系统不支持文件锁）时
	抛出带路径的 ``OSError``，不会 yield：加不了锁不等于可以不加锁。
	"""
	handle = _acquire(path)
	if handle is None:
		yield False
		return
	try:
		yield True
	finally:
		_release(path, handle)


def _acquire(path: Path) -> BinaryIO | None:
	path.parent.mkdir(parents=True, exist_ok=True)
	for _ in range(_ACQUIRE_ATTEMPTS):
		handle = path.open("a+b")
		acquired = False
		try:
			if os.fstat(handle.fileno()).st_size == 0:
				# msvcrt.locking 锁的是从第 0 字节起的 1 字节区间，先让这个字节存在
				# （与请求预算租约的获取顺序一致）。
				handle.write(b"\0")
				handle.flush()
			if not try_lock_exclusive(handle):
				return None
			acquired = _names_locked_file(path, handle)
			if acquired:
				return handle
			# 上一个持有者在我们打开之后、加锁之前删除了文件：丢掉孤儿，按当前路径重来。
		except OSError as error:
			# 写入与加锁都按文件描述操作，异常里没有路径。补上锁文件路径，调用方才会
			# 把它当成本地文件错误，而不是一次值得重试的网络异常。
			if error.filename is None:
				error.filename = str(path)
			raise
		finally:
			if not acquired:
				handle.close()
	return None


def _names_locked_file(path: Path, handle: BinaryIO) -> bool:
	"""``path`` 是否仍指向 ``handle`` 打开的那个文件。"""
	if _WINDOWS:
		# Windows 不能删除仍被打开的文件：拿着句柄期间路径换不成另一个文件。
		return True
	try:
		return os.path.samestat(os.stat(path), os.fstat(handle.fileno()))
	except FileNotFoundError:
		return False


def _release(path: Path, handle: BinaryIO) -> None:
	try:
		if not _WINDOWS:
			# 仍持锁时删除：此刻已打开旧文件的竞争者加锁后会发现路径已变并重试。
			# 路径若已被外部替换成别的文件，那不是我们的锁，留在原地。
			try:
				if _names_locked_file(path, handle):
					path.unlink()
			except OSError:
				pass
	finally:
		handle.close()
	if _WINDOWS:
		# 只能在关闭之后删除；此时若已有别的进程打开它，删除失败，文件留给对方。
		try:
			path.unlink()
		except OSError:
			pass
