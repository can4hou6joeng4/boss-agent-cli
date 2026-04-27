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


def build_default_steps(platform: str = "zhipin") -> list[SmokeStep]:
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
			command=["boss", *platform_args, "search", "golang"],
		),
		SmokeStep(
			name="detail",
			platform=platform,
			purpose="验证职位详情路径具备可测试入口",
			preconditions=["command:boss", "env:BOSS_SMOKE_SECURITY_ID"],
			failure_classification="command_error",
			command=["boss", *platform_args, "detail", "demo-security-id"],
		),
	]


DEFAULT_STEPS = build_default_steps()


class SmokeRunner:
	def __init__(self, steps: list[SmokeStep]):
		self.steps = steps

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
			status = self._check_preconditions(step)
			if status is None:
				try:
					subprocess.run(
						step.command,
						check=True,
						cwd=ROOT,
						stdout=subprocess.DEVNULL,
						stderr=subprocess.DEVNULL,
					)
					status = "pass"
				except Exception:
					status = step.failure_classification
			results.append(
				{
					"name": step.name,
					"purpose": step.purpose,
					"preconditions": step.preconditions,
					"failure_classification": step.failure_classification,
					"command": step.command,
					"status": status,
				}
			)
		return {"steps": results}


def main():
	platform = os.environ.get("BOSS_SMOKE_PLATFORM", "zhipin").strip() or "zhipin"
	runner = SmokeRunner(build_default_steps(platform))
	print(json.dumps(runner.run(), ensure_ascii=False))


if __name__ == "__main__":
	main()
