import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


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


def parse_envelope(stdout: str) -> tuple[dict | None, str | None]:
	try:
		payload = json.loads(stdout.strip())
	except json.JSONDecodeError:
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
		completed = subprocess.run(
			command,
			check=check,
			cwd=cwd,
			capture_output=capture_output,
			text=text,
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
							detail = contract_error
						else:
							ok = payload["ok"]
							result_error = validate_envelope_result(payload, returncode)
							if result_error:
								status = "contract_error"
								detail = result_error
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


def main() -> None:
	platform = os.environ.get("BOSS_SMOKE_PLATFORM", "zhipin").strip() or "zhipin"
	query = os.environ.get("BOSS_SMOKE_QUERY", "golang").strip() or "golang"
	security_id = os.environ.get("BOSS_SMOKE_SECURITY_ID", "demo-security-id").strip() or "demo-security-id"
	timeout_seconds = int(os.environ.get("BOSS_SMOKE_TIMEOUT", "30"))
	dry_run = os.environ.get("BOSS_SMOKE_DRY_RUN", "").strip() in {"1", "true", "yes"}
	runner = SmokeRunner(
		build_default_steps(platform, query=query, security_id=security_id),
		timeout_seconds=timeout_seconds,
		dry_run=dry_run,
	)
	print(json.dumps(runner.run(), ensure_ascii=False))


if __name__ == "__main__":
	main()
