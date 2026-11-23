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
import re
import stat
import tempfile
from pathlib import Path
from typing import Any

from boss_agent_cli.services.output_path import refuse_non_regular_target

#: 与 ``output._MAX_SAFE_INT_BITS`` 同口径：十进制超过 4300 位的整数在
#: ``json.dump`` / ``json.dumps`` 转字符串时抛 ValueError（Python 3.11+
#: 的整数转换上限），整份本地状态 / 导出会落成本地 IO 错误（Issue #2388）。
#: 位长度只是快速路径：14285 bit 起才可能超限，最终以十进制位数精确判定
#: （Issue #2568）。
_MAX_JSON_INT_BITS = 14284
_MAX_JSON_INT_DIGITS = 4300


def path_mode(path: Path) -> int | None:
	"""仅对缺失路径返回 None；悬空链接保留链接自身的类型（Issue #1695）。"""
	# 3.14 的 Path.exists() / is_file() 会把权限等 OSError 吞掉并返回 False，
	# 配置读取会因此把「读不到」当成「文件不存在」并覆盖写回默认值。
	try:
		mode = path.lstat().st_mode
	except FileNotFoundError:
		return None
	except ValueError as exc:
		# 含 NUL 的路径在 lstat 处抛 ValueError；与写路径 _lstat_mode 同口径
		# 归一为带 filename 的 OSError，读路径不再泄露未分类异常（Issue #2353）。
		raise OSError(errno.EINVAL, str(exc), str(path)) from exc
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
	null_oversized_ints: bool = False,
) -> None:
	"""原子替换写入 JSON 文件；失败时保留原文件，不留下临时文件。

	``null_oversized_ints`` 只给导出 / 快照产物使用：这些数据来自平台响应，
	单个数值漂移不应中止整份产物（Issue #2388）。本地状态（配置、简历、
	登记表）保持既有契约——超长整数按本地 IO 错误收口（Issue #1891）。
	"""
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
			_serialize_into(handle, value, indent=indent, null_oversized_ints=null_oversized_ints)
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


def _serialize_into(handle: Any, value: Any, *, indent: int | None, null_oversized_ints: bool = False) -> None:
	"""序列化失败（深嵌套 RecursionError、超长整数 ValueError）归成 OSError。

	``json.loads`` 能读入的深嵌套文件在写回时会耗尽递归栈；异常若原样
	上抛，命令层只兜 ``OSError``，CLI 就会 stdout 0 字节、stderr 裸
	traceback（Issue #1891）。归成本地 IO 错误后走既有 LOCAL_IO_ERROR 通道。
	"""
	try:
		json.dump(
			_json_safe_value(value, null_oversized_ints=null_oversized_ints),
			handle,
			ensure_ascii=False,
			indent=indent,
			allow_nan=False,
		)
	# TypeError 覆盖不可序列化的值（元组键、bytes、set 等）：不归成本地 IO
	# 错误就会裸穿出命令层，stdout 无信封、stderr 裸 traceback（Issue #2220，
	# 与 #1891 的深嵌套 / 超长整数同类）。
	except (RecursionError, ValueError, TypeError) as exc:
		raise OSError(errno.EINVAL, f"本地状态无法序列化（{exc}）") from exc


def _json_safe_value(value: Any, *, null_oversized_ints: bool = False) -> Any:
	"""把非有限 float 与（可选）超长整数归一为 null，其余值递归保留。

	``json.dump`` / ``json.dumps`` 默认允许 NaN / Infinity；本地状态与导出
	产物会被 jq 等严格解析器读取，必须显式归一（Issue #1851）。

	十进制位数可能超过 4300 位的整数在 ``null_oversized_ints`` 为真时归 null：
	``json.dump`` 在转换时抛 ``ValueError``，快照 / 导出会整体失败并误报成本地
	IO 错误（Issue #2388）。默认不动整数，保留本地状态写入的 EINVAL 契约
	（Issue #1891）；bool 是 int 子类，先于 int 分支返回，保留 true/false。
	"""
	if isinstance(value, float):
		return value if math.isfinite(value) else None
	if isinstance(value, bool):
		return value
	if isinstance(value, int):
		if null_oversized_ints and value.bit_length() > _MAX_JSON_INT_BITS:
			# bit_length 超阈值只说明「可能」超过 4300 位：``2**14284`` 恰好是
			# 4300 位，``json.dumps`` 能正常序列化。只有 ``str()`` 真的失败
			# （> 4300 位）才归 null，不能静默丢弃可序列化的值（Issue #2568）。
			try:
				if len(str(value)) > _MAX_JSON_INT_DIGITS:
					return None
			except ValueError:
				return None
			return value
		return value
	if isinstance(value, dict):
		return {
			key: _json_safe_value(child, null_oversized_ints=null_oversized_ints)
			for key, child in value.items()
		}
	if isinstance(value, (list, tuple)):
		return [_json_safe_value(child, null_oversized_ints=null_oversized_ints) for child in value]
	return value


#: JSON 文本里合法的代理对（两个码元）与必须单独处理的孤立代理项。
_SURROGATE_PAIR = re.compile("[\ud800-\udbff][\udc00-\udfff]")
_LONE_SURROGATE = re.compile("[\ud800-\udfff]")


def escape_lone_surrogates(text: str) -> str:
	"""把序列化文本里的孤立代理项改成字面 ``\\ud800`` 文本（Issue #2457）。

	``write_text_atomic`` 的 ``backslashreplace`` 会把孤立代理字符原样写成
	``\\ud800`` 六个字符，但那在 JSON 里是**未配对**的代理项转义：RFC 8259
	下不可移植，jq / 严格解析器直接拒绝整份产物。这里先替换成「反斜杠 +
	uXXXX」的普通文本，``json.dumps`` 会把反斜杠再转义，落盘为 ``\\\\ud800``，
	解析回字面量 ``\\ud800``，与 stdout 的 ``sanitize_surrogates`` 口径一致。

	合法代理对（``json.loads`` 对 ``\\ud83d\\ude00`` 这类转义会保留两个码元）
	先合并为单个码点，不误伤正常 emoji。
	"""
	text = _SURROGATE_PAIR.sub(
		lambda match: match.group().encode("utf-16", "surrogatepass").decode("utf-16"),
		text,
	)
	return _LONE_SURROGATE.sub(lambda match: f"\\\\u{ord(match.group()):04x}", text)


def strict_json_text(value: Any, *, indent: int | None = 2, null_oversized_ints: bool = False) -> str:
	"""Serialize a product JSON artifact under the same strict rules as stdout.

	``json.dump`` in :func:`write_json_atomic` still allows NaN / Infinity and
	fails on lone surrogates unless the file handle uses ``backslashreplace``.
	Export artifacts are read as strict JSON by other tools, so normalize
	non-finite numbers to null and forbid non-standard constants explicitly.
	Lone surrogates become the literal ``\\ud800`` text via
	:func:`escape_lone_surrogates`: a bare ``\\ud800`` escape is not a valid
	JSON string and strict parsers such as jq reject the whole artifact
	(Issue #2457).
	"""
	try:
		text = json.dumps(
			_json_safe_value(value, null_oversized_ints=null_oversized_ints),
			ensure_ascii=False,
			allow_nan=False,
			indent=indent,
		)
	except (RecursionError, ValueError, TypeError) as exc:
		raise OSError(errno.EINVAL, f"本地状态无法序列化（{exc}）") from exc
	return escape_lone_surrogates(text)
