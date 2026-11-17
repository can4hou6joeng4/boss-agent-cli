import errno
import json
import math
import re
import sys
from typing import Any

import click

from boss_agent_cli.schema.error_codes import ERROR_CODES

#: 日志级别取值域：`--log-level` 的 Choice 与 `config set log_level` 共用，
#: 未知级别不会被 Logger 拒绝，而是静默回退到 error 阈值。
LOG_LEVELS: tuple[str, ...] = ("error", "warning", "info", "debug")
_LEVEL_ORDER = {"debug": 0, "info": 1, "warning": 2, "error": 3}
_REDACTED = "[REDACTED]"
#: 脱敏递归的深度上限。平台响应或本地坏数据可以嵌套到 Python 递归上限之外，
#: 无上限遍历会在出口抛 RecursionError，CLI 的 stdout 变成空文本、整个七键信封
#: 丢失（Issue #1474）。超过上限的子树整体按敏感值处理，不展开、不泄漏。
_MAX_REDACTION_DEPTH = 64
_SENSITIVE_KEY_PARTS = (
	"api_key",
	"apikey",
	"authorization",
	"cookie",
	"credential",
	"password",
	"private",
	"secret",
	"session",
	"stoken",
	"token",
)
_PUBLIC_METADATA_KEYS = {
	"private_fields",
}
# 凭据必然是字符串或容器。数值 / 布尔 / None 命中关键词一律是子串误报——
# 例如 ai_max_tokens（AI 生成长度上限）、token_expires_in（过期秒数）都含
# 子串 "token"，脱敏后用户永远看不到自己配的值，None 更会被说成藏着秘密。
# 已扫描全仓：不存在数值型真凭据，因此放宽不会漏脱敏。
_NON_CREDENTIAL_TYPES = (bool, int, float, type(None))
SchemaPath = tuple[str | int, ...]
SchemaParameterPaths = dict[SchemaPath, str]
_SCHEMA_VALUE_FIELDS = {"default", "example", "examples", "const", "enum"}
# 凭据值在这些字符处结束：ASCII 空白 / 逗号 / 分号，以及全角标点。只排除
# ASCII 分隔符时，`stoken=abc）` 会把右括号算进值里，`Bearer x，然后`
# 连后续中文说明一起被替换成 [REDACTED]，诊断文案就此残缺。
_TEXT_VALUE_TERMINATORS = r"\s,;，。、；：（）「」『』【】《》！？…—"
_SENSITIVE_TEXT_PATTERNS = tuple(
	re.compile(pattern, re.IGNORECASE)
	for pattern in (
		rf"\b(authorization)\b(\s*[:=]\s*)(bearer\s+)?([^{_TEXT_VALUE_TERMINATORS}]+)",
		rf"\b(api[_-]?key|authorization|cookie|credential|password|secret|session(?:[_-]?id)?|stoken|token)\b(\s*[:=]\s*)([^{_TEXT_VALUE_TERMINATORS}]+)",
		rf"\b(bearer)\s+([^{_TEXT_VALUE_TERMINATORS}]+)",
	)
)


def _is_error_code_metadata(value: Any) -> bool:
	"""Schema error-code metadata is public even when the code contains TOKEN."""
	if not isinstance(value, dict):
		return False
	keys = set(value)
	return {"message", "recoverable", "recovery_action"}.issubset(keys) and keys.issubset({
		"message",
		"recoverable",
		"recovery_action",
	})


_FILESYSTEM_ERROR_TYPES: tuple[type[OSError], ...] = (
	FileNotFoundError,
	PermissionError,
	FileExistsError,
	IsADirectoryError,
	NotADirectoryError,
)
# fd 级写入（write / flush / fsync）失败时异常不带 filename：磁盘满、配额耗尽、
# 设备 I/O 错误只可能来自本地文件系统，兜成可重试的 NETWORK_ERROR 会让 Agent
# 无限重试同一条命令（Issue #1400）。Windows 的 errno 模块不一定定义 EDQUOT。
_LOCAL_IO_ERRNOS: frozenset[int] = frozenset(
	code for code in (errno.ENOSPC, errno.EIO, getattr(errno, "EDQUOT", None)) if code is not None
)


def is_local_io_error(exc: BaseException) -> bool:
	"""判断异常来自本地文件系统，还是套接字层。

	`OSError` 同时覆盖文件和网络：`ConnectionError` / `TimeoutError` / `socket.gaierror`
	/ `ssl.SSLError` 是网络问题，重试有意义；`open()` / `os.makedirs()` 抛出的
	`PermissionError` / `IsADirectoryError` 等带 `filename`，重试同一条命令只会再失败一次。
	"""
	if not isinstance(exc, OSError) or isinstance(exc, (ConnectionError, TimeoutError)):
		return False
	if isinstance(exc, _FILESYSTEM_ERROR_TYPES) or getattr(exc, "filename", None) is not None:
		return True
	return exc.errno in _LOCAL_IO_ERRNOS


def redact_sensitive(value: Any) -> Any:
	"""Return a JSON-safe copy with credential-like fields redacted."""
	return _redact_sensitive(value, {}, (), 0)


def _redact_sensitive(
	value: Any,
	schema_parameters: SchemaParameterPaths,
	path: SchemaPath,
	depth: int,
) -> Any:
	if depth >= _MAX_REDACTION_DEPTH:
		# 超限子树不再展开：既避免递归爆栈，也避免把深处的凭据逐层带到信封里。
		return _REDACTED
	if isinstance(value, dict):
		redacted: dict[Any, Any] = {}
		parameter_name = schema_parameters.get(path, "").lower().replace("-", "_")
		sensitive_parameter = any(part in parameter_name for part in _SENSITIVE_KEY_PARTS)
		for key, item in value.items():
			key_text = str(key).lower()
			item_path = (*path, key)
			if sensitive_parameter and key_text in _SCHEMA_VALUE_FIELDS and not isinstance(item, _NON_CREDENTIAL_TYPES):
				redacted[key] = _REDACTED
			elif item_path in schema_parameters or _is_error_code_metadata(item) or key_text in _PUBLIC_METADATA_KEYS:
				redacted[key] = _redact_sensitive(item, schema_parameters, item_path, depth + 1)
			elif any(part in key_text for part in _SENSITIVE_KEY_PARTS) and not isinstance(item, _NON_CREDENTIAL_TYPES):
				redacted[key] = _REDACTED
			else:
				redacted[key] = _redact_sensitive(item, schema_parameters, item_path, depth + 1)
		return redacted
	if isinstance(value, list):
		return [_redact_sensitive(item, schema_parameters, (*path, index), depth + 1) for index, item in enumerate(value)]
	if isinstance(value, tuple):
		return [_redact_sensitive(item, schema_parameters, (*path, index), depth + 1) for index, item in enumerate(value)]
	if isinstance(value, str):
		return redact_sensitive_text(value)
	return value


def redact_sensitive_text(message: str) -> str:
	"""Redact credential-like values embedded in free-form text."""
	redacted = message
	for pattern in _SENSITIVE_TEXT_PATTERNS:
		if pattern.pattern.startswith("\\b(bearer)"):
			redacted = pattern.sub(r"\1 [REDACTED]", redacted)
		else:
			redacted = pattern.sub(r"\1\2[REDACTED]", redacted)
	return redacted


#: ``json.dumps`` 对整数做十进制转换时受 ``sys.get_int_max_str_digits()`` 限制
#: （Python 3.11+ 默认 4300 位）。bit_length <= 14284 的整数一定小于
#: ``2**14284 < 10**4300``，最多 4300 位；bit_length 14285 起就可能已是 4301 位
#: （``10**4300``），转换会抛 ValueError。用位长度估算避免真的去数位数。
_MAX_SAFE_INT_BITS = 14284


def json_safe_value(value: Any) -> Any:
	"""递归归一 JSON 出口无法严格表示的值（Issue #1391）。

	``json.dumps`` 默认 ``allow_nan=True``，会把 NaN / ±Infinity 写成同名裸字面量；
	RFC 8259 不接受这些字面量，Node ``JSON.parse`` 与 ``parse_constant`` 校验的严格
	解析器都会拒绝整份输出。归一策略：

	- 非有限浮点 → ``None``（与信封里「无值」的既有语义一致）；
	- 十进制位数超过 4300 位的整数 → ``None``：这类值在 Python 里无法转成字符串，
	  ``json.dumps`` 会抛 ValueError，保留数字形态就不可能输出合法 JSON；
	- dict 的键原样保留（调用方本应传字符串键；非有限浮点键由出口的最后防线兜底）；
	- tuple / list 统一输出 list，与 ``json.dumps`` 原本的数组语义一致；
	- 其余值（str / bool / None / 正常范围 int / 其他类型）原样返回，是否可序列化
	  仍交给 ``json.dumps`` 判定。

	正常值不受影响：``2**63`` 之类的大整数、有限浮点都按原值输出。
	"""
	if isinstance(value, float):
		return value if math.isfinite(value) else None
	# bool 是 int 子类，先于 int 分支返回，避免把 True/False 当数值处理。
	if isinstance(value, bool) or value is None or isinstance(value, str):
		return value
	if isinstance(value, int):
		return value if value.bit_length() <= _MAX_SAFE_INT_BITS else None
	if isinstance(value, dict):
		return {key: json_safe_value(item) for key, item in value.items()}
	if isinstance(value, list):
		return [json_safe_value(item) for item in value]
	if isinstance(value, tuple):
		return [json_safe_value(item) for item in value]
	return value


def serialization_failure_envelope(command: Any = None) -> dict[str, Any]:
	"""构造不含调用方数据、必然可严格序列化的错误信封。

	作为两个出口（CLI stdout / MCP 工具结果）的最后防线：归一之后仍可能有无法写成
	严格 JSON 的值（例如 NaN 作为 dict 键、bytes、循环引用）。此时报
	``INTERNAL_ERROR``，而不是让 ValueError / TypeError 穿出出口——CLI 会在
	stdout 留下非法 JSON，MCP server 则可能整个退出。
	"""
	spec = ERROR_CODES.get("INTERNAL_ERROR", {})
	return {
		"ok": False,
		"schema_version": "1.0",
		"command": command if isinstance(command, str) else "unknown",
		"data": None,
		"pagination": None,
		"error": {
			"code": "INTERNAL_ERROR",
			"message": "输出序列化失败：结果中含有无法写成严格 JSON 的值",
			"recoverable": bool(spec.get("recoverable", False)),
			"recovery_action": spec.get("recovery_action"),
		},
		"hints": None,
	}


def _dumps_strict_envelope(payload: dict[str, Any]) -> str:
	"""按严格 JSON 序列化信封；必要时归一，最后防线返回固定错误信封。"""
	try:
		# 快路径：正常信封输出与修复前逐字节一致，且不为深层合法结构多走一遍递归。
		return json.dumps(payload, ensure_ascii=False, allow_nan=False)
	except (TypeError, ValueError, RecursionError):
		pass
	try:
		return json.dumps(json_safe_value(payload), ensure_ascii=False, allow_nan=False)
	except (TypeError, ValueError, RecursionError):
		# 归一后仍无法严格序列化（NaN 键、bytes、循环引用等）：返回固定错误信封。
		command = payload.get("command") if isinstance(payload, dict) else None
		return json.dumps(serialization_failure_envelope(command), ensure_ascii=False, allow_nan=False)


def _encode_stdout_text(text: str) -> str:
	"""把序列化结果转成一定能按 UTF-8 写出的文本。"""
	return text.encode("utf-8", "backslashreplace").decode("utf-8")


def _dumps_envelope(payload: dict[str, Any]) -> str:
	"""序列化信封并保证结果一定能按 UTF-8 写出（Issue #1294）。

孤立代理字符（平台字段或本地坏文件里的 ``\\udcff`` / ``\\ud800``）会让
``json.dumps(..., ensure_ascii=False)`` 产出无法编码的 str，``click.echo``
写 stdout 时抛 UnicodeEncodeError，命令丢掉整个信封。``backslashreplace``
把它转成 JSON 合法的 ``\\udcff`` 转义，解析出的值不变，stdout 始终可读。

``allow_nan=False`` 是严格 JSON 的最后防线（Issue #1391）：NaN / ±Infinity /
超长整数先经 :func:`json_safe_value` 归一为 ``null``，归一后仍无法序列化时
输出固定的 ``INTERNAL_ERROR`` 信封，异常不会穿出 stdout 边界。
	"""
	text = _dumps_strict_envelope(payload)
	return _encode_stdout_text(text)


def envelope_success(
	command: str,
	data: Any,
	*,
	pagination: dict[str, Any] | None = None,
	hints: dict[str, Any] | None = None,
	schema_parameter_paths: SchemaParameterPaths | None = None,
) -> str:
	# 只有 schema 命令从内置目录显式声明的参数路径可保留元数据。
	# 任意业务字典自称 type=object 不能获得豁免，参数默认值/示例仍按敏感名称脱敏。
	public_parameters = (schema_parameter_paths or {}) if command == "schema" else {}
	try:
		return _dumps_envelope(
			{
				"ok": True,
				"schema_version": "1.0",
				"command": command,
				"data": _redact_sensitive(data, public_parameters, (), 0),
				"pagination": redact_sensitive(pagination),
				"error": None,
				"hints": redact_sensitive(hints),
			}
		)
	except RecursionError:
		# 脱敏阶段仍可能因容器自身深度或调用方构造的异常结构爆栈；stdout 必须
		# 保留七键信封，不能只留空文本（Issue #1474）。
		return _dumps_envelope(serialization_failure_envelope(command))


def envelope_error(
	command: str,
	*,
	code: str,
	message: str,
	recoverable: bool | None = None,
	recovery_action: str | None = None,
	details: dict[str, Any] | None = None,
	hints: dict[str, Any] | None = None,
	data: Any = None,
) -> str:
	# ``data`` 默认 None（绝大多数错误没有可带回的结果）；批量删除这类
	# 「部分完成」失败必须让调用方看到已完成 / 失败项（Issue #1497），
	# 但仍保持七键信封不变。
	# 调用方未显式给出时按错误码登记表补齐，保证信封元数据与 schema 真源一致。
	# None 表示「未声明」，显式 False 仍表示不可恢复，不会被登记表覆盖。
	spec = ERROR_CODES.get(code, {})
	if recoverable is None:
		recoverable = bool(spec.get("recoverable", False))
	if recovery_action is None:
		recovery_action = spec.get("recovery_action")
	# 登记表声明 ``replay_safe: false`` 的码表示「禁止自动重发」，Agent 必须能从
	# ``error.details`` 读到与文案一致的判定，而不是只看到 ``sent: null``（Issue #988）。
	# ``outcome_unknown`` 只在本码确实「结果未知」时补齐：``LOCAL_STATE_WRITE_FAILED``
	# 的平台结果已确认，只是本地记账失败。调用方给出的更精确值优先。
	if spec.get("replay_safe") is False:
		implied: dict[str, Any] = {"replay_safe": False}
		if spec.get("outcome_unknown") is True:
			implied["outcome_unknown"] = True
		details = {**implied, **(details or {})}
	try:
		error = {
			"code": code,
			"message": redact_sensitive_text(message),
			"recoverable": recoverable,
			"recovery_action": recovery_action,
		}
		if details is not None:
			# 结果未知的写操作有两个来源：CLI 传输层写 ``outcome="unknown"`` +
			# ``replay_safe=false``，MCP 强制终止写 ``outcome_unknown=true``。
			# 两者含义相同（动作可能已生效，禁止自动重试），Agent 不该按入口猜键名：
			# 这里补成同一组字段，CLI 与 MCP 的错误详情都能用 ``replay_safe`` 判定。
			normalized_details = dict(details)
			if normalized_details.get("outcome") == "unknown" and normalized_details.get("replay_safe") is False:
				normalized_details.setdefault("outcome_unknown", True)
			if "outcome_unknown" in normalized_details:
				normalized_details.setdefault("replay_safe", not normalized_details["outcome_unknown"])
			error["details"] = redact_sensitive(normalized_details)
		return _dumps_envelope(
			{
				"ok": False,
				"schema_version": "1.0",
				"command": command,
				# 部分完成的清理需要带回已完成项；其余错误保持 data=null。
				"data": None if data is None else redact_sensitive(data),
				"pagination": None,
				"error": error,
				"hints": redact_sensitive(hints),
			}
		)
	except RecursionError:
		# 与成功信封同理：details / hints 或错误码登记表结构过深时也要保住七键
		# 信封，不能让异常穿出 stdout 边界（Issue #1474）。
		return _dumps_envelope(serialization_failure_envelope(command))


def emit_success(command: str, data: Any, **kwargs: Any) -> None:
	click.echo(envelope_success(command, data, **kwargs))


# ── 可选语义化退出码（--exit-codes sysexits）────────────────────────────
# 默认 envelope 模式保持「exit 0 ⇔ ok=true，exit 1 ⇔ ok=false」契约不变。
# 键必须存在于 schema/error_codes.ERROR_CODES（tests/test_exit_codes.py 守卫）；
# 未列出的错误码与未知码一律回退 1。刻意不用 66（空结果）：本项目空结果 = ok=true。
SYSEXITS_EXIT_CODES: dict[str, int] = {
	"INVALID_PARAM": 78,  # EX_CONFIG
	"EGRESS_CONTRADICTION": 78,  # EX_CONFIG
	"AUTH_REQUIRED": 77,  # EX_NOPERM
	"AUTH_EXPIRED": 77,
	"LOGIN_EXPIRED": 77,
	"TOKEN_REFRESH_FAILED": 77,
	"LOGIN_CREDENTIAL_EXTRACTION_FAILED": 77,
	"RATE_LIMITED": 75,  # EX_TEMPFAIL
	"REQUEST_BUDGET_EXHAUSTED": 75,
	"LOGIN_TIMEOUT": 75,
	"BROWSER_NAV_TIMEOUT": 75,
	"WORKFLOW_TIMEOUT": 75,
	"WORKFLOW_BUSY": 75,
	"CRAWL_BUSY": 75,
	"ACCOUNT_RISK": 69,  # EX_UNAVAILABLE
	"ENVIRONMENT_RISK": 69,
	"ENVIRONMENT_RISK_LOCKED": 69,
	"LOGIN_RISK_CONTROL": 69,
	"NOT_SUPPORTED": 69,
	"PLATFORM_NOT_SUPPORTED": 69,
	"INTERNAL_ERROR": 70,  # EX_SOFTWARE
}
_EXIT_CODES_MODES = ("envelope", "sysexits")
_exit_codes_mode = "envelope"


def set_exit_codes_mode(mode: str) -> None:
	"""Set the process-wide exit-code style; unknown values fall back to envelope."""
	global _exit_codes_mode
	normalized = mode.strip().lower()
	_exit_codes_mode = normalized if normalized in _EXIT_CODES_MODES else "envelope"


def exit_code_for_error(code: str) -> int:
	"""Return the exit code for a failed command under the active mode."""
	if _exit_codes_mode == "sysexits":
		return SYSEXITS_EXIT_CODES.get(code, 1)
	return 1


def emit_error(command: str, **kwargs: Any) -> None:
	click.echo(envelope_error(command, **kwargs))
	sys.exit(exit_code_for_error(str(kwargs.get("code") or "")))


class Logger:
	def __init__(self, level: str = "error") -> None:
		self._threshold = _LEVEL_ORDER.get(level, 3)

	def _log(self, level: str, message: str) -> None:
		if _LEVEL_ORDER.get(level, 0) >= self._threshold:
			import datetime
			ts = datetime.datetime.now().strftime("%H:%M:%S")
			# message 已经过 Logger 的脱敏边界：token/cookie/secret 等键值对会被
			# redact_sensitive_text 替换成 [REDACTED]，stderr 日志里不会出现凭据原值。
			# 薪资、个人资料等私有数据不要拼进日志（Issue #1394）；GitHub 默认设置
			# 不消费 `# codeql[...]` 内联抑制，这类告警一律回到源头修数据流。
			print(f"[{level.upper()} {ts}] {redact_sensitive_text(message)}", file=sys.stderr)

	def debug(self, message: str) -> None:
		self._log("debug", message)

	def info(self, message: str) -> None:
		self._log("info", message)

	def warning(self, message: str) -> None:
		self._log("warning", message)

	def error(self, message: str) -> None:
		self._log("error", message)
