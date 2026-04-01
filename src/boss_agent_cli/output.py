import json
import sys

_LEVEL_ORDER = {"debug": 0, "info": 1, "warning": 2, "error": 3}


def envelope_success(
	command: str,
	data,
	*,
	pagination: dict | None = None,
	hints: dict | None = None,
) -> str:
	return json.dumps(
		{
			"ok": True,
			"schema_version": "1.0",
			"command": command,
			"data": data,
			"pagination": pagination,
			"error": None,
			"hints": hints,
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
	hints: dict | None = None,
) -> str:
	return json.dumps(
		{
			"ok": False,
			"schema_version": "1.0",
			"command": command,
			"data": None,
			"pagination": None,
			"error": {
				"code": code,
				"message": message,
				"recoverable": recoverable,
				"recovery_action": recovery_action,
			},
			"hints": hints,
		},
		ensure_ascii=False,
	)


def emit_success(command: str, data, **kwargs) -> None:
	print(envelope_success(command, data, **kwargs))


def emit_error(command: str, **kwargs) -> None:
	print(envelope_error(command, **kwargs))
	sys.exit(1)


class Logger:
	def __init__(self, level: str = "error"):
		self._threshold = _LEVEL_ORDER.get(level, 3)

	def _log(self, level: str, message: str):
		if _LEVEL_ORDER.get(level, 0) >= self._threshold:
			import datetime
			ts = datetime.datetime.now().strftime("%H:%M:%S")
			print(f"[{level.upper()} {ts}] {message}", file=sys.stderr)

	def debug(self, message: str):
		self._log("debug", message)

	def info(self, message: str):
		self._log("info", message)

	def warning(self, message: str):
		self._log("warning", message)

	def error(self, message: str):
		self._log("error", message)
