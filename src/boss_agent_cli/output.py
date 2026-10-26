import json
import re
import sys
from typing import Any

import click

_LEVEL_ORDER = {"debug": 0, "info": 1, "warning": 2, "error": 3}
_REDACTED = "[REDACTED]"
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
_SENSITIVE_TEXT_PATTERNS = tuple(
	re.compile(pattern, re.IGNORECASE)
	for pattern in (
		r"\b(authorization)\b(\s*[:=]\s*)(bearer\s+)?([^\s,;]+)",
		r"\b(api[_-]?key|authorization|cookie|credential|password|secret|session(?:[_-]?id)?|stoken|token)\b(\s*[:=]\s*)([^\s,;]+)",
		r"\b(bearer)\s+([^\s,;]+)",
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


def redact_sensitive(value: Any) -> Any:
	"""Return a JSON-safe copy with credential-like fields redacted."""
	return _redact_sensitive(value, {}, ())


def _redact_sensitive(value: Any, schema_parameters: SchemaParameterPaths, path: SchemaPath) -> Any:
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
				redacted[key] = _redact_sensitive(item, schema_parameters, item_path)
			elif any(part in key_text for part in _SENSITIVE_KEY_PARTS) and not isinstance(item, _NON_CREDENTIAL_TYPES):
				redacted[key] = _REDACTED
			else:
				redacted[key] = _redact_sensitive(item, schema_parameters, item_path)
		return redacted
	if isinstance(value, list):
		return [_redact_sensitive(item, schema_parameters, (*path, index)) for index, item in enumerate(value)]
	if isinstance(value, tuple):
		return [_redact_sensitive(item, schema_parameters, (*path, index)) for index, item in enumerate(value)]
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
	return json.dumps(
		{
			"ok": True,
			"schema_version": "1.0",
			"command": command,
			"data": _redact_sensitive(data, public_parameters, ()),
			"pagination": redact_sensitive(pagination),
			"error": None,
			"hints": redact_sensitive(hints),
		},
		ensure_ascii=False,
	)


def envelope_error(
	command: str,
	*,
	code: str,
	message: str,
	recoverable: bool = False,
	recovery_action: str | None = None,
	details: dict[str, Any] | None = None,
	hints: dict[str, Any] | None = None,
) -> str:
	error = {
		"code": code,
		"message": redact_sensitive_text(message),
		"recoverable": recoverable,
		"recovery_action": recovery_action,
	}
	if details is not None:
		error["details"] = redact_sensitive(details)
	return json.dumps(
		{
			"ok": False,
			"schema_version": "1.0",
			"command": command,
			"data": None,
			"pagination": None,
			"error": error,
			"hints": redact_sensitive(hints),
		},
		ensure_ascii=False,
	)


def emit_success(command: str, data: Any, **kwargs: Any) -> None:
	click.echo(envelope_success(command, data, **kwargs))


# ── 可选语义化退出码（--exit-codes sysexits）────────────────────────────
# 默认 envelope 模式保持「exit 0 ⇔ ok=true，exit 1 ⇔ ok=false」契约不变。
# 键必须存在于 schema/error_codes.ERROR_CODES（tests/test_exit_codes.py 守卫）；
# 未列出的错误码与未知码一律回退 1。刻意不用 66（空结果）：本项目空结果 = ok=true。
SYSEXITS_EXIT_CODES: dict[str, int] = {
	"INVALID_PARAM": 78,  # EX_CONFIG
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
	"ACCOUNT_RISK": 69,  # EX_UNAVAILABLE
	"ENVIRONMENT_RISK": 69,
	"ENVIRONMENT_RISK_LOCKED": 69,
	"LOGIN_RISK_CONTROL": 69,
	"NOT_SUPPORTED": 69,
	"PLATFORM_NOT_SUPPORTED": 69,
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
			print(f"[{level.upper()} {ts}] {redact_sensitive_text(message)}", file=sys.stderr)

	def debug(self, message: str) -> None:
		self._log("debug", message)

	def info(self, message: str) -> None:
		self._log("info", message)

	def warning(self, message: str) -> None:
		self._log("warning", message)

	def error(self, message: str) -> None:
		self._log("error", message)
