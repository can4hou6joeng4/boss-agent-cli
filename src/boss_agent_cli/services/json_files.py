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

from boss_agent_cli.services.output_path import refuse_non_regular_target


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
	refuse_non_regular_target(path)
	path.parent.mkdir(parents=True, exist_ok=True)
	temporary: Path | None = None
	try:
		# 临时名不含目标文件名：NAME_MAX=255 时「.<完整名称>.<随机>」会让 241–255
		# 字节的合法目标名在这里 ENAMETOOLONG（Issue #1985）。
		fd, temporary_name = tempfile.mkstemp(prefix=".boss-json-", dir=path.parent)
		temporary = Path(temporary_name)
		# 孤立代理字符（如 argv 里的非法 UTF-8 字节解码出的 U+DCFF）无法按 UTF-8
		# 编码，严格模式会让整条命令在写盘时抛 UnicodeEncodeError、stdout 无信封
		# （Issue #1294）。backslashreplace 把它写成 JSON 合法的 ``\udcff`` 转义，
		# 读回值不变，写入不再失败。
		with os.fdopen(fd, "w", encoding="utf-8", errors="backslashreplace") as handle:
			# 本地状态与其他工具共用：非有限数必须是 null，不能写出裸
			# Infinity / NaN 让 jq 等严格解析器失败（Issue #1851）。
			_serialize_into(handle, value, indent=indent)
			if trailing_newline:
				handle.write("\n")
			handle.flush()
			os.fsync(handle.fileno())
		refuse_non_regular_target(path)
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


def _serialize_into(handle: Any, value: Any, *, indent: int | None) -> None:
	"""序列化失败（深嵌套 RecursionError、超长整数 ValueError）归成 OSError。

	``json.loads`` 能读入的深嵌套文件在写回时会耗尽递归栈；异常若原样
	上抛，命令层只兜 ``OSError``，CLI 就会 stdout 0 字节、stderr 裸
	traceback（Issue #1891）。归成本地 IO 错误后走既有 LOCAL_IO_ERROR 通道。
	"""
	try:
		json.dump(_json_safe_value(value), handle, ensure_ascii=False, indent=indent, allow_nan=False)
	# TypeError 覆盖不可序列化的值（元组键、bytes、set 等）：不归成本地 IO
	# 错误就会裸穿出命令层，stdout 无信封、stderr 裸 traceback（Issue #2220，
	# 与 #1891 的深嵌套 / 超长整数同类）。
	except (RecursionError, ValueError, TypeError) as exc:
		raise OSError(errno.EINVAL, f"本地状态无法序列化（{exc}）") from exc


def _json_safe_value(value: Any) -> Any:
	"""把非有限 float 归一为 null，其余值递归保留。

	``json.dump`` / ``json.dumps`` 默认允许 NaN / Infinity；本地状态与导出
	产物会被 jq 等严格解析器读取，必须显式归一（Issue #1851）。
	"""
	if isinstance(value, float):
		return value if math.isfinite(value) else None
	if isinstance(value, dict):
		return {key: _json_safe_value(child) for key, child in value.items()}
	if isinstance(value, (list, tuple)):
		return [_json_safe_value(child) for child in value]
	return value


def strict_json_text(value: Any, *, indent: int | None = 2) -> str:
	"""Serialize a product JSON artifact under the same strict rules as stdout.

	``json.dump`` in :func:`write_json_atomic` still allows NaN / Infinity and
	fails on lone surrogates unless the file handle uses ``backslashreplace``.
	Export artifacts are read as strict JSON by other tools, so normalize
	non-finite numbers to null and forbid non-standard constants explicitly.
	Lone surrogates are escaped by the caller's text writer and remain valid
	JSON escapes.
	"""
	try:
		return json.dumps(_json_safe_value(value), ensure_ascii=False, allow_nan=False, indent=indent)
	except (RecursionError, ValueError, TypeError) as exc:
		raise OSError(errno.EINVAL, f"本地状态无法序列化（{exc}）") from exc
