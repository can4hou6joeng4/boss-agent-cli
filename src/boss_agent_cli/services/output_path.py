"""输出路径准备：展开 `~` 并创建父目录。

各命令的 `-o` 语义必须一致。MCP 通过子进程调用 CLI（`mcp_server._run_boss`），
参数不经 shell，`~` 不会被展开；不在命令里处理，`-o ~/report.md` 就会在当前
目录建出一个字面量 `~` 目录，用户以为写到了家目录。
"""

from __future__ import annotations

import os
from pathlib import Path


def prepare_output_path(path: str | os.PathLike[str]) -> Path:
	"""返回可直接写入的目标路径，必要时创建父目录。

	显式指定的写入位置由调用方负责选择，这里只做「能写进去」的准备，不校验
	目标是否位于工作目录或数据目录内。创建失败（父路径是普通文件、无权限等）
	由 OSError 冒到命令层，统一落进 `LOCAL_IO_ERROR` 信封。
	"""
	target = Path(os.path.expanduser(os.fspath(path)))
	target.parent.mkdir(parents=True, exist_ok=True)
	return target
