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
	if isinstance(value, dict):
		redacted: dict[Any, Any] = {}
		for key, item in value.items():
			key_text = str(key).lower()
			if _is_error_code_metadata(item):
				redacted[key] = redact_sensitive(item)
			elif key_text in _PUBLIC_METADATA_KEYS:
				redacted[key] = redact_sensitive(item)
			elif any(part in key_text for part in _SENSITIVE_KEY_PARTS) and not isinstance(item, bool):
				redacted[key] = _REDACTED
			else:
				redacted[key] = redact_sensitive(item)
		return redacted
	if isinstance(value, list):
		return [redact_sensitive(item) for item in value]
	if isinstance(value, tuple):
		return [redact_sensitive(item) for item in value]
	if isinstance(value, str):
		return redact_sensitive_text(value)
	return value


def redact_sensitive_text(message: str) -> str:
	"""Redact credential-like values embedded in free-form text."""
	redacted = message
	for pattern in _SENSITIVE_TEXT_PATTERNS:
		if pattern.pattern.startswith("\\b(bearer)"):
			redacted = pattern.sub(r"\1 [REDACTED]", redacted)
		elif pattern.pattern.startswith("\\b(authorization)"):
			redacted = pattern.sub(r"\1\2[REDACTED]", redacted)
		else:
			redacted = pattern.sub(r"\1\2[REDACTED]", redacted)
	return redacted


def envelope_success(
	command: str,
	data: Any,
	*,
	pagination: dict[str, Any] | None = None,
	hints: dict[str, Any] | None = None,
) -> str:
	return json.dumps(
		{
			"ok": True,
			"schema_version": "1.0",
			"command": command,
			"data": redact_sensitive(data),
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


def emit_error(command: str, **kwargs: Any) -> None:
	click.echo(envelope_error(command, **kwargs))
	sys.exit(1)


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
