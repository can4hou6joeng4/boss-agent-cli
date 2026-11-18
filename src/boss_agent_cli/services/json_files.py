"""本地 JSON 状态的原子写入。

`Path.write_text` 写到一半被中断（Ctrl-C、进程被杀、磁盘写满）会留下半截文件，
下一次读取直接失败；本项目的本地状态（config.json、简历、模型登记表、索引缓存）
都依赖整份 JSON 才可用，所以统一走「同目录临时文件 + os.replace」。
"""

from __future__ import annotations

import errno
import json
import math
import os
import stat
import tempfile
from pathlib import Path
from typing import Any

from boss_agent_cli.services.output_path import refuse_symlink_target


def path_mode(path: Path) -> int | None:
	"""仅对缺失路径返回 None；悬空链接保留链接自身的类型（Issue #1695）。"""
	# 3.14 的 Path.exists() / is_file() 会把权限等 OSError 吞掉并返回 False，
	# 配置读取会因此把「读不到」当成「文件不存在」并覆盖写回默认值。
	try:
		mode = path.lstat().st_mode
	except FileNotFoundError:
		return None
	if stat.S_ISLNK(mode):
		try:
			return path.stat().st_mode
		except FileNotFoundError:
			pass
	return mode


def read_bytes_if_regular(path: Path) -> bytes | None:
	"""读取普通文件；缺失返回 None，元数据错误或非普通文件上抛 OSError。

	目录、FIFO / 设备与悬空链接不能直接 ``read_bytes()``：FIFO 会阻塞在打开
	阶段，目录只会抛裸 ``IsADirectoryError``。调用方据 ``None`` 区分「确实
	没有配置文件」与「状态未知」，后者禁止整文件覆盖写回（Issue #1695）。
	"""
	mode = path_mode(path)
	if mode is None:
		return None
	if not stat.S_ISREG(mode):
		raise OSError(errno.EINVAL, "路径不是普通文件", str(path))
	return path.read_bytes()


def write_json_atomic(
	path: Path,
	value: Any,
	*,
	indent: int | None = 2,
	trailing_newline: bool = False,
) -> None:
	"""原子替换写入 JSON 文件；失败时保留原文件，不留下临时文件。"""
	# os.replace 替换符号链接本身而不是写穿目标；入口直接拒绝，避免静默破坏链接。
	refuse_symlink_target(path)
	path.parent.mkdir(parents=True, exist_ok=True)
	temporary: Path | None = None
	try:
		fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
		temporary = Path(temporary_name)
		# 孤立代理字符（如 argv 里的非法 UTF-8 字节解码出的 U+DCFF）无法按 UTF-8
		# 编码，严格模式会让整条命令在写盘时抛 UnicodeEncodeError、stdout 无信封
		# （Issue #1294）。backslashreplace 把它写成 JSON 合法的 ``\udcff`` 转义，
		# 读回值不变，写入不再失败。
		with os.fdopen(fd, "w", encoding="utf-8", errors="backslashreplace") as handle:
			json.dump(value, handle, ensure_ascii=False, indent=indent)
			if trailing_newline:
				handle.write("\n")
			handle.flush()
			os.fsync(handle.fileno())
		os.replace(temporary, path)
	except OSError as error:
		# fd 级写入（write / flush / fsync）失败的异常没有 filename：补上目标路径，
		# 调用方才会把它归成本地文件错误，而不是一次值得重试的网络异常
		# （与 file_lock._acquire 同口径，Issue #1400）。
		if error.filename is None:
			error.filename = str(path)
		raise
	finally:
		if temporary is not None:
			temporary.unlink(missing_ok=True)


def strict_json_text(value: Any, *, indent: int | None = 2) -> str:
	"""Serialize a product JSON artifact under the same strict rules as stdout.

	``json.dump`` in :func:`write_json_atomic` still allows NaN / Infinity and
	fails on lone surrogates unless the file handle uses ``backslashreplace``.
	Export artifacts are read as strict JSON by other tools, so normalize
	non-finite numbers to null and forbid non-standard constants explicitly.
	Lone surrogates are escaped by the caller's text writer and remain valid
	JSON escapes.
	"""
	def normalize(item: Any) -> Any:
		if isinstance(item, float):
			return item if math.isfinite(item) else None
		if isinstance(item, dict):
			return {key: normalize(child) for key, child in item.items()}
		if isinstance(item, (list, tuple)):
			return [normalize(child) for child in item]
		return item

	return json.dumps(normalize(value), ensure_ascii=False, allow_nan=False, indent=indent)
