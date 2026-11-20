import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TIMEOUT_SECONDS = 30
#: 视为「开启离线计划」的取值（大小写不敏感）；未知非空取值拒绝执行。
_TRUTHY = frozenset({"1", "true", "yes", "on", "y"})
_FALSY = frozenset({"", "0", "false", "no", "off", "n"})


class SmokeConfigError(Exception):
	"""环境变量取值不合法：拒绝执行，不退回到默认值或联网模式。"""


@dataclass(frozen=True)
class SmokeStep:
	name: str
	platform: str
	purpose: str
	preconditions: list[str]
	failure_classification: str
	command: list[str]


@dataclass(frozen=True)
class CommandResult:
	returncode: int
	stdout: str
	stderr: str


def build_default_steps(
	platform: str = "zhipin",
	*,
	query: str = "golang",
	security_id: str = "demo-security-id",
) -> list[SmokeStep]:
	platform_args = ["--platform", platform] if platform != "zhipin" else []
	return [
		SmokeStep(
			name="doctor",
			platform=platform,
			purpose="验证本地环境、自检和网络前提",
			preconditions=["command:boss"],
			failure_classification="env_error",
			command=["boss", *platform_args, "doctor"],
		),
		SmokeStep(
			name="status",
			platform=platform,
			purpose="验证本地登录态是否存在且可读取",
			preconditions=["command:boss"],
			failure_classification="env_error",
			command=["boss", *platform_args, "status"],
		),
		SmokeStep(
			name="search",
			platform=platform,
			purpose="验证最小职位发现路径可执行",
			preconditions=["command:boss", "env:BOSS_SMOKE_QUERY"],
			failure_classification="command_error",
			command=["boss", *platform_args, "search", query],
		),
		SmokeStep(
			name="detail",
			platform=platform,
			purpose="验证职位详情路径具备可测试入口",
			preconditions=["command:boss", "env:BOSS_SMOKE_SECURITY_ID"],
			failure_classification="command_error",
			command=["boss", *platform_args, "detail", security_id],
		),
	]


def parse_timeout(raw: str) -> int:
	"""``BOSS_SMOKE_TIMEOUT``：空值走默认 30，非正整数或无法解析时报配置错误。"""
	value = raw.strip()
	if not value:
		return DEFAULT_TIMEOUT_SECONDS
	try:
		seconds = int(value)
	except ValueError:
		seconds = 0
	if seconds <= 0 or seconds > 86400:
		raise SmokeConfigError(f"BOSS_SMOKE_TIMEOUT 必须是 1-86400 之间的整数秒，实际是 {value[:40]!r}")
	return seconds


def parse_dry_run(raw: str) -> bool:
	"""``BOSS_SMOKE_DRY_RUN``：大小写不敏感；无法识别的取值拒绝执行而不是联网。"""
	value = raw.strip().lower()
	if value in _FALSY:
		return False
	if value in _TRUTHY:
		return True
	raise SmokeConfigError(
		f"BOSS_SMOKE_DRY_RUN 取值无法识别：{value[:40]!r}；离线计划请设为 1，确需真实冒烟请清空该变量"
	)


def stderr_tail(stderr: str, *, secret: str = "", limit: int = 400) -> str:
	"""stderr 末尾片段：单行化、抹掉已知敏感参数、按尾部截断。"""
	lines = [line.strip() for line in stderr.splitlines() if line.strip()]
	if not lines:
		return ""
	text = " ⏎ ".join(lines)
	if secret:
		text = text.replace(secret, "<redacted>")
	return text[-limit:]


def parse_envelope(stdout: str) -> tuple[dict | None, str | None]:
	try:
		payload = json.loads(stdout.strip())
	except (ValueError, RecursionError):
		# 超长整数让 json.loads 抛裸 ValueError，过深嵌套抛 RecursionError。
		return None, "stdout was not a JSON envelope"
	required = {"ok", "schema_version", "command", "data", "pagination", "error", "hints"}
	if not isinstance(payload, dict) or set(payload) != required:
		return None, "stdout JSON did not match the envelope shape"
	if payload.get("schema_version") != "1.0":
		return None, "stdout envelope schema_version was not 1.0"
	if not isinstance(payload.get("ok"), bool):
		return None, "stdout envelope ok was not a boolean"
	return payload, None


def validate_envelope_result(payload: dict, returncode: int) -> str | None:
	ok = payload["ok"]
	expected_returncode = 0 if ok else 1
	if returncode != expected_returncode:
		return "stdout envelope ok did not match process exit code"
	if not ok:
		error = payload.get("error")
		required = {"code", "recoverable", "recovery_action"}
		if not isinstance(error, dict) or not required.issubset(error):
			return "stdout envelope error did not match the error shape"
		if not isinstance(error.get("code"), str) or not isinstance(error.get("recoverable"), bool):
			return "stdout envelope error did not match the error shape"
	return None


def reported_command(step: SmokeStep) -> list[str]:
	if step.name == "detail" and step.command:
		return [*step.command[:-1], "<redacted>"]
	return step.command


DEFAULT_STEPS = build_default_steps()


class SmokeRunner:
	def __init__(
		self,
		steps: list[SmokeStep],
		*,
		run_command=None,
		timeout_seconds: int = 30,
		dry_run: bool = False,
	):
		self.steps = steps
		self._run_command = run_command or self._default_run_command
		self._timeout_seconds = timeout_seconds
		self._dry_run = dry_run

	def _default_run_command(self, command, cwd, capture_output, text, timeout, check):
		# 父子进程固定按 UTF-8 交换：父进程区域与子进程输出编码不一致时，
		# 严格按区域解码会抛 UnicodeDecodeError，整份报告丢失。
		env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
		completed = subprocess.run(
			command,
			check=check,
			cwd=cwd,
			capture_output=capture_output,
			text=text,
			encoding="utf-8" if text else None,
			errors="replace" if text else None,
			env=env,
			timeout=timeout,
		)
		return CommandResult(
			returncode=completed.returncode,
			stdout=completed.stdout,
			stderr=completed.stderr,
		)

	def _check_preconditions(self, step: SmokeStep) -> str | None:
		for item in step.preconditions:
			if item.startswith("env:"):
				key = item.split(":", 1)[1]
				if not os.environ.get(key):
					return "env_error"
		return None

	def run(self) -> dict:
		results = []
		for step in self.steps:
			status = None
			detail = ""
			ok = None
			error_code = None
			recovery_action = None
			returncode = None
			if self._dry_run:
				status = "dry_run"
				detail = "command not executed"
			else:
				status = self._check_preconditions(step)
				if status is None:
					try:
						completed = self._run_command(
							step.command,
							cwd=ROOT,
							capture_output=True,
							text=True,
							timeout=self._timeout_seconds,
							check=False,
						)
						returncode = completed.returncode
						payload, contract_error = parse_envelope(completed.stdout)
						if contract_error:
							status = "contract_error"
							detail = _with_stderr(contract_error, step, completed.stderr)
						else:
							ok = payload["ok"]
							result_error = validate_envelope_result(payload, returncode)
							if result_error:
								status = "contract_error"
								detail = _with_stderr(result_error, step, completed.stderr)
							elif ok:
								status = "pass"
							else:
								status = step.failure_classification
								error = payload.get("error") or {}
								error_code = error.get("code")
								recovery_action = error.get("recovery_action")
								detail = error.get("message") or ""
					except subprocess.TimeoutExpired:
						status = "timeout"
						detail = f"command exceeded {self._timeout_seconds}s"
					except UnicodeDecodeError as e:
						status = "contract_error"
						detail = f"command output was not valid UTF-8: {e}"
					except OSError as e:
						status = "env_error"
						detail = str(e)
			results.append(
				{
					"name": step.name,
					"purpose": step.purpose,
					"platform": step.platform,
					"preconditions": step.preconditions,
					"failure_classification": step.failure_classification,
					"command": reported_command(step),
					"status": status,
					"ok": ok,
					"error_code": error_code,
					"recovery_action": recovery_action,
					"returncode": returncode,
					"detail": detail,
				}
			)
		return {"steps": results}


def _with_stderr(detail: str, step: SmokeStep, stderr: str) -> str:
	"""``contract_error`` 带上 stderr 尾部：区分「形状不对」与「进程崩溃」。

	``detail`` 步骤的 security_id 仍按既有约定抹掉，不进入输出。
	"""
	secret = step.command[-1] if step.name == "detail" and step.command else ""
	tail = stderr_tail(stderr, secret=secret)
	return f"{detail}；stderr: {tail}" if tail else detail


def write_report(report: dict, *, stream=None) -> None:
	stream = stream or sys.stdout
	text = json.dumps(report, ensure_ascii=False) + "\n"
	try:
		stream.write(text)
	except UnicodeEncodeError:
		# 终端/重定向编码不是 UTF-8 时保证报告仍以 ASCII 转义写出。
		stream.write(json.dumps(report) + "\n")


def exit_code(report: dict) -> int:
	return 0 if all(step["status"] in {"pass", "dry_run"} for step in report["steps"]) else 1


def main() -> int:
	platform = os.environ.get("BOSS_SMOKE_PLATFORM", "zhipin").strip() or "zhipin"
	query = os.environ.get("BOSS_SMOKE_QUERY", "golang").strip() or "golang"
	security_id = os.environ.get("BOSS_SMOKE_SECURITY_ID", "demo-security-id").strip() or "demo-security-id"
	try:
		timeout_seconds = parse_timeout(os.environ.get("BOSS_SMOKE_TIMEOUT", ""))
		dry_run = parse_dry_run(os.environ.get("BOSS_SMOKE_DRY_RUN", ""))
	except SmokeConfigError as exc:
		print(f"error: {exc}", file=sys.stderr)
		return 2
	runner = SmokeRunner(
		build_default_steps(platform, query=query, security_id=security_id),
		timeout_seconds=timeout_seconds,
		dry_run=dry_run,
	)
	report = runner.run()
	write_report(report)
	return exit_code(report)


if __name__ == "__main__":
	raise SystemExit(main())
