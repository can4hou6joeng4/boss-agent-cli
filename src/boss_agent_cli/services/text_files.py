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

from boss_agent_cli.services.output_path import refuse_non_regular_target


def is_utf8_encodable(text: str) -> bool:
	"""文本能否按严格 UTF-8 编码（Issue #2499）。

	argv 里的非法 UTF-8 字节经 surrogateescape 解码成孤立代理项；这类值作为
	sqlite 参数绑定时抛 ``UnicodeEncodeError``，被全局兜成带磁盘指引的
	``LOCAL_IO_ERROR``。命令入口用它把「输入非法」与「本地存储故障」分开。
	"""
	try:
		text.encode("utf-8")
	except UnicodeEncodeError:
		return False
	return True


def sanitize_surrogates(text: str) -> str:
	"""孤立代理项按 backslashreplace 归一，stdout / 文件路径同口径（Issue #2017）。

	Linux 上 argv 里的非 UTF-8 字节（如数据目录路径）经 surrogateescape 解码为
	孤立代理字符；严格 UTF-8 编码会抛 ``UnicodeEncodeError``，把已经成功的
	文件写出报成失败（Issue #2109）。HTML / JSON / CSV 等文本产物统一先过这里。
	"""
	if is_utf8_encodable(text):
		return text
	return text.encode("utf-8", "backslashreplace").decode("utf-8")


def write_text_atomic(
	path: str | os.PathLike[str],
	body: str,
	*,
	encoding: str = "utf-8",
	newline: str | None = None,
) -> Path:
	"""原子替换写入文本文件；失败时保留原文件，不留下临时文件。"""
	target = Path(path)
	refuse_non_regular_target(target)
	target.parent.mkdir(parents=True, exist_ok=True)
	temporary: Path | None = None
	try:
		# 与 json_files 同口径：临时名不含目标文件名，避免长文件名的 ENAMETOOLONG
		# （Issue #1985）。
		fd, temporary_name = tempfile.mkstemp(prefix=".boss-text-", dir=target.parent)
		temporary = Path(temporary_name)
		# 与 json_files 同口径：孤立代理字符无法按 UTF-8 编码，严格模式会让
		# 导出在写盘时抛 UnicodeEncodeError、命令丢掉信封（Issue #1294）。
		# backslashreplace 写出 ``\udcff`` 字面转义，导出文件仍可正常打开。
		with os.fdopen(fd, "w", encoding=encoding, newline=newline, errors="backslashreplace") as handle:
			handle.write(body)
			handle.flush()
			os.fsync(handle.fileno())
		refuse_non_regular_target(target)
		os.replace(temporary, target)
	except OSError as error:
		# fd 级写入（write / flush / fsync）失败的异常没有 filename：补上目标路径，
		# 调用方才会把它归成本地文件错误，而不是一次值得重试的网络异常
		# （与 file_lock._acquire 同口径，Issue #1400）。
		if error.filename is None:
			error.filename = str(target)
		raise
	finally:
		if temporary is not None:
			temporary.unlink(missing_ok=True)
	return target
