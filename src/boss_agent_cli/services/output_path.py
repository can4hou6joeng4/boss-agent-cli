"""输出路径准备：展开 `~` 并创建父目录。

各命令的 `-o` 语义必须一致。MCP 通过子进程调用 CLI（`mcp_server._run_boss`），
参数不经 shell，`~` 不会被展开；不在命令里处理，`-o ~/report.md` 就会在当前
目录建出一个字面量 `~` 目录，用户以为写到了家目录。
"""

from __future__ import annotations

import errno
import os
import stat
from enum import Enum
from pathlib import Path


class OutputPathProblem(Enum):
	"""`-o` 目标不可写的原因（内部判定，不是信封错误码）。"""

	IS_DIRECTORY = "is_directory"
	NOT_REGULAR = "not_regular"
	PARENT_NOT_DIR = "parent_not_dir"
	NOT_WRITABLE = "not_writable"
	UNEXPANDABLE_USER = "unexpandable_user"


def _expand_user(path: str | os.PathLike[str]) -> Path:
	"""展开 `~` / `~user`，未知用户抛 ``RuntimeError``。

	不能用 ``os.path.expanduser``：``~某用户`` 在系统里查不到时它原样返回字面
	量，随后 ``mkdir`` 会在当前目录下建出一个名叫 ``~某用户`` 的目录、把报告写进
	用户没想到的位置（Issue #2289）。``Path.expanduser`` 在同一情形抛
	``RuntimeError``，与 ``--data-dir`` / ``request_budget_dir`` 的口径一致。
	"""
	return Path(os.fspath(path)).expanduser()


class OutputPathExpansionError(ValueError):
	"""`-o` 的家目录写法无法展开（系统里查不到该用户）。"""


def _lstat_mode(path: Path) -> int | None:
	"""返回路径自身的 lstat 类型位；只有 ``FileNotFoundError`` 算缺失。

	3.14 的 ``exists()`` / ``is_file()`` / ``is_symlink()`` 会把权限等
	``OSError`` 吞成 ``False``，预检会据此报告「未发现问题」，把失败留给
	平台请求之后的真正写入（Issue #1716）。
	"""
	try:
		return path.lstat().st_mode
	except (FileNotFoundError, NotADirectoryError):
		# ENOTDIR 表示某一级父路径是文件：该路径不可能存在，交给父路径循环
		# 报 PARENT_NOT_DIR，而不是把预检变成泛化 IO 错误。
		return None
	except ValueError as exc:
		error = OSError(f"无法读取路径状态：{exc}")
		error.filename = str(path)
		raise error from exc
	except OSError as exc:
		if exc.filename is None:
			exc.filename = str(path)
		raise


def _stat_mode(path: Path) -> int | None:
	"""跟随符号链接取目标类型位；悬空链接返回 None，其余错误上抛。"""
	try:
		return path.stat().st_mode
	except (FileNotFoundError, NotADirectoryError):
		return None
	except ValueError as exc:
		error = OSError(f"无法读取路径状态：{exc}")
		error.filename = str(path)
		raise error from exc
	except OSError as exc:
		if exc.filename is None:
			exc.filename = str(path)
		raise


def prepare_output_path(path: str | os.PathLike[str]) -> Path:
	"""返回可直接写入的目标路径，必要时创建父目录。

	显式指定的写入位置由调用方负责选择，这里只做「能写进去」的准备，不校验
	目标是否位于工作目录或数据目录内。创建失败（父路径是普通文件、无权限等）
	由 OSError 冒到命令层，统一落进 `LOCAL_IO_ERROR` 信封。
	"""
	try:
		target = _expand_user(path)
	except RuntimeError as exc:
		# 未知用户的 ``~user`` 不能按字面量落盘；这里抛类型化异常，命令层在
		# 正常链路上已经由 ``inspect_output_path`` 先行拒绝（Issue #2289）。
		raise OutputPathExpansionError(
			f"-o / --output 的家目录写法无法展开（{exc}）：{os.fspath(path)!r}；"
			"请改用绝对路径或 ~/ 开头的当前用户路径"
		) from exc
	try:
		target.parent.mkdir(parents=True, exist_ok=True)
	except ValueError as exc:
		# 内嵌 NUL 的父路径在 mkdir 处只抛裸 ValueError；与 _stat_mode 同口径
		# 归一为带 filename 的 OSError（Issue #2375）。
		error = OSError(errno.EINVAL, str(exc))
		error.filename = str(target)
		raise error from exc
	# 原子写入的 ``os.replace`` 会替换符号链接本身，真实目标保持旧内容
	# （Issue #1355）；目录 / FIFO / 设备也不能当文件写入。共享写入器统一在
	# 准备阶段拒绝，不再依赖各命令是否先走 ``inspect_output_path``（Issue #1849）。
	refuse_non_regular_target(target)
	return target


def inspect_output_path(path: str | os.PathLike[str]) -> tuple[Path, OutputPathProblem | None]:
	"""只读地检查 `-o` 目标，返回 ``(展开后的路径, 问题)``。

	``export`` / ``chat --export`` 此前把路径问题留到抓完所有分页、写完快照之后
	才由 ``prepare_output_path`` 抛出：``-o`` 指向目录时，``count=40`` 会先翻 5 页
	平台数据（触发风控、消耗请求预算），再回一个与真正原因无关的信封。参数错误
	必须在副作用之前暴露。

	本函数**不建目录、不写文件**，只回答「这个路径能不能写」。``None`` 表示未
	发现问题；仍需交给 ``prepare_output_path`` 建父目录。最近的已存在祖先目录
	不可写时同样算问题：不这样检查，合法的只读路径会先消耗一次平台请求、更新
	当日快照，才在真正写文件时抛 ``PermissionError``（Issue #2197）。

	保持纯函数（不依赖 click / display），出信封由命令层负责。
	"""
	try:
		target = _expand_user(path)
	except RuntimeError:
		# 未知用户无法展开成真实目录：按参数错误拒绝，而不是在 cwd 建一个字面量
		# ``~user`` 目录并把产物写进去（Issue #2289）。
		return Path(os.fspath(path)), OutputPathProblem.UNEXPANDABLE_USER
	# 符号链接不是普通文件：``is_file()`` 会跟随链接把「链接指向普通文件」判成
	# 可写，而原子写入的 ``os.replace`` 替换的是链接本身，真实目标保持旧内容
	# （Issue #1355）。悬空链接的 ``exists()`` 为 False，同样必须按非普通文件
	# 拒绝，否则会被当成「尚不存在的新文件」。
	target_mode = _lstat_mode(target)
	if target_mode is not None and stat.S_ISLNK(target_mode):
		return target, OutputPathProblem.NOT_REGULAR
	if target_mode is not None and stat.S_ISDIR(target_mode):
		return target, OutputPathProblem.IS_DIRECTORY
	if target_mode is not None and not stat.S_ISREG(target_mode):
		return target, OutputPathProblem.NOT_REGULAR
	# 缺失的父目录可以创建，但创建链上任意一级被文件或悬空符号链接占用都不行。
	ancestor: Path | None = None
	for parent in target.parents:
		parent_mode = _lstat_mode(parent)
		if parent_mode is None:
			continue
		if stat.S_ISLNK(parent_mode):
			target_parent_mode = _stat_mode(parent)
			if target_parent_mode is not None and stat.S_ISDIR(target_parent_mode):
				ancestor = parent
				break
			return target, OutputPathProblem.PARENT_NOT_DIR
		if stat.S_ISDIR(parent_mode):
			ancestor = parent
			break
		return target, OutputPathProblem.PARENT_NOT_DIR
	# 原子写入的 ``os.replace`` 只需要最近祖先目录可写；目标文件自身只读不影响
	# 替换。``os.access`` 跟随符号链接并检查 ACL，与真正写入的口径一致；权限在
	# 预检之后被改动属于不可消除的 TOCTOU，由写入路径兜底。
	if ancestor is not None and not os.access(ancestor, os.W_OK | os.X_OK):
		return target, OutputPathProblem.NOT_WRITABLE
	return target, None


def refuse_symlink_target(target: Path) -> None:
	"""允许替换目录的调用方仍须拒绝符号链接目标。"""
	# ``Path.is_symlink()`` 会把权限等 OSError 吞成 False（Issue #1716）；
	# 用 lstat 类型位判定，读不到状态时上抛而不是放行（Issue #1849）。
	mode = _lstat_mode(target)
	if mode is not None and stat.S_ISLNK(mode):
		raise OSError(errno.EINVAL, "拒绝写入符号链接目标", str(target))


def refuse_non_regular_target(target: Path) -> None:
	"""文件写入器仅允许替换普通文件或创建缺失的文件。

	临时文件写好后应在提交替换前复查，以保留在写入期间出现的特殊文件。
	拒绝时携带目标路径，供命令层报告 ``LOCAL_IO_ERROR``。
	"""
	mode = _lstat_mode(target)
	if mode is None or stat.S_ISREG(mode):
		return
	if stat.S_ISLNK(mode):
		raise OSError(errno.EINVAL, "拒绝写入符号链接目标", str(target))
	if stat.S_ISDIR(mode):
		raise IsADirectoryError(errno.EISDIR, os.strerror(errno.EISDIR), str(target))
	raise OSError(errno.EINVAL, "输出目标不是普通文件", str(target))
