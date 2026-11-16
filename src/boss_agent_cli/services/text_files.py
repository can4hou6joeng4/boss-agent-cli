"""导出文本的原子写入。

`Path.write_text` / `open("w")` 会先截断目标文件再写入：写到一半被中断
（Ctrl-C、进程被杀、磁盘写满）就留下半截报表，并破坏上一次的完整导出。
职位、日报、统计 HTML 与聊天记录导出都由人或 Agent 直接打开，统一走
「同目录临时文件 + os.replace」，失败时保留原文件。

JSON 状态走 `json_files.write_json_atomic`；本模块只服务 `-o` 导出的文本。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from boss_agent_cli.services.output_path import refuse_symlink_target


def write_text_atomic(
	path: str | os.PathLike[str],
	body: str,
	*,
	encoding: str = "utf-8",
	newline: str | None = None,
) -> Path:
	"""原子替换写入文本文件；失败时保留原文件，不留下临时文件。"""
	target = Path(path)
	# os.replace 替换符号链接本身而不是写穿目标；入口直接拒绝，避免静默破坏链接。
	refuse_symlink_target(target)
	target.parent.mkdir(parents=True, exist_ok=True)
	fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
	temporary = Path(temporary_name)
	try:
		# 与 json_files 同口径：孤立代理字符无法按 UTF-8 编码，严格模式会让
		# 导出在写盘时抛 UnicodeEncodeError、命令丢掉信封（Issue #1294）。
		# backslashreplace 写出 ``\udcff`` 字面转义，导出文件仍可正常打开。
		with os.fdopen(fd, "w", encoding=encoding, newline=newline, errors="backslashreplace") as handle:
			handle.write(body)
			handle.flush()
			os.fsync(handle.fileno())
		os.replace(temporary, target)
	finally:
		temporary.unlink(missing_ok=True)
	return target
