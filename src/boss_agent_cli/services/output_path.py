"""输出路径准备：展开 `~` 并创建父目录。

各命令的 `-o` 语义必须一致。MCP 通过子进程调用 CLI（`mcp_server._run_boss`），
参数不经 shell，`~` 不会被展开；不在命令里处理，`-o ~/report.md` 就会在当前
目录建出一个字面量 `~` 目录，用户以为写到了家目录。
"""

from __future__ import annotations

import errno
import os
from enum import Enum
from pathlib import Path


class OutputPathProblem(Enum):
	"""`-o` 目标不可写的原因（内部判定，不是信封错误码）。"""

	IS_DIRECTORY = "is_directory"
	NOT_REGULAR = "not_regular"
	PARENT_NOT_DIR = "parent_not_dir"


def prepare_output_path(path: str | os.PathLike[str]) -> Path:
	"""返回可直接写入的目标路径，必要时创建父目录。

	显式指定的写入位置由调用方负责选择，这里只做「能写进去」的准备，不校验
	目标是否位于工作目录或数据目录内。创建失败（父路径是普通文件、无权限等）
	由 OSError 冒到命令层，统一落进 `LOCAL_IO_ERROR` 信封。
	"""
	target = Path(os.path.expanduser(os.fspath(path)))
	target.parent.mkdir(parents=True, exist_ok=True)
	return target


def inspect_output_path(path: str | os.PathLike[str]) -> tuple[Path, OutputPathProblem | None]:
	"""只读地检查 `-o` 目标，返回 ``(展开后的路径, 问题)``。

	``export`` / ``chat --export`` 此前把路径问题留到抓完所有分页、写完快照之后
	才由 ``prepare_output_path`` 抛出：``-o`` 指向目录时，``count=40`` 会先翻 5 页
	平台数据（触发风控、消耗请求预算），再回一个与真正原因无关的信封。参数错误
	必须在副作用之前暴露。

	本函数**不建目录、不写文件**，只回答「这个路径能不能写」。``None`` 表示未
	发现问题；仍需交给 ``prepare_output_path`` 建父目录。

	保持纯函数（不依赖 click / display），出信封由命令层负责。
	"""
	target = Path(os.path.expanduser(os.fspath(path)))
	# 符号链接不是普通文件：``is_file()`` 会跟随链接把「链接指向普通文件」判成
	# 可写，而原子写入的 ``os.replace`` 替换的是链接本身，真实目标保持旧内容
	# （Issue #1355）。悬空链接的 ``exists()`` 为 False，同样必须按非普通文件
	# 拒绝，否则会被当成「尚不存在的新文件」。
	if target.is_symlink():
		return target, OutputPathProblem.NOT_REGULAR
	if target.is_dir():
		return target, OutputPathProblem.IS_DIRECTORY
	if target.exists() and not target.is_file():
		return target, OutputPathProblem.NOT_REGULAR
	# 缺失的父目录可以创建，但创建链上任意一级被文件或悬空符号链接占用都不行。
	for parent in target.parents:
		if parent.is_dir():
			break
		if parent.exists() or parent.is_symlink():
			return target, OutputPathProblem.PARENT_NOT_DIR
	return target, None


def refuse_symlink_target(target: Path) -> None:
	"""原子写入入口的兜底：目标本身是符号链接时拒绝写入。

	``write_text_atomic`` / ``write_json_atomic`` / ``write_private_bytes`` 都靠
	同目录临时文件 + ``os.replace`` 落盘，替换的是链接本身，不会写穿到真实目标。
	命令层的 ``-o`` 预检已覆盖一部分调用方，其余内部状态写入也必须走这里，避免
	静默破坏用户的链接布局（Issue #1355）。

	抛出的 ``OSError`` 带 ``filename``，顶层网兜会把它归成 ``LOCAL_IO_ERROR``。
	"""
	if target.is_symlink():
		raise OSError(errno.EINVAL, "拒绝写入符号链接目标", str(target))
