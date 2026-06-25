#!/usr/bin/env python3
"""Run the local P0 quality baseline for boss-agent-cli.

The baseline is intentionally offline and deterministic: it checks linting,
the full offline test suite, and type checking in one place so agents, humans,
and CI can verify a change before opening a PR.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STEPS: tuple[tuple[str, tuple[str, ...]], ...] = (
	("ruff", ("ruff", "check", "src/boss_agent_cli", "tests", "--output-format=concise")),
	("pytest", ("pytest", "-q")),
	("mypy", ("mypy", "src/boss_agent_cli")),
)


@dataclass(frozen=True)
class StepResult:
	name: str
	command: tuple[str, ...]
	status: str
	returncode: int | None
	stdout: str
	stderr: str

	def as_dict(self) -> dict[str, object]:
		return {
			"name": self.name,
			"command": list(self.command),
			"status": self.status,
			"returncode": self.returncode,
			"stdout": self.stdout,
			"stderr": self.stderr,
		}


def _resolve_command(command: Sequence[str]) -> tuple[str, ...]:
	if shutil.which(command[0]):
		return tuple(command)
	uv = shutil.which("uv")
	if uv:
		return (uv, "run", *command)
	return tuple(command)


def run_step(name: str, command: Sequence[str]) -> StepResult:
	resolved = _resolve_command(command)
	if not shutil.which(resolved[0]):
		return StepResult(
			name, tuple(command), "missing", None, "", f"{command[0]} is not installed and uv is unavailable"
		)
	env = os.environ.copy()
	env.setdefault("PYTHONUTF8", "1")
	proc = subprocess.run(
		resolved,
		cwd=ROOT,
		env=env,
		text=True,
		stdout=subprocess.PIPE,
		stderr=subprocess.PIPE,
		check=False,
	)
	return StepResult(
		name, tuple(resolved), "ok" if proc.returncode == 0 else "failed", proc.returncode, proc.stdout, proc.stderr
	)


def run_baseline(steps: Sequence[tuple[str, tuple[str, ...]]] = DEFAULT_STEPS) -> list[StepResult]:
	return [run_step(name, command) for name, command in steps]


def main(argv: Sequence[str] | None = None) -> int:
	parser = argparse.ArgumentParser(description="Run the boss-agent-cli P0 quality baseline")
	parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
	parser.add_argument("--skip-mypy", action="store_true", help="skip the mypy step for fast local iteration")
	args = parser.parse_args(argv)

	steps = tuple(step for step in DEFAULT_STEPS if not (args.skip_mypy and step[0] == "mypy"))
	results = run_baseline(steps)
	failed = [item for item in results if item.status != "ok"]

	if args.json:
		print(
			json.dumps(
				{"ok": not failed, "results": [item.as_dict() for item in results]}, ensure_ascii=False, indent=2
			)
		)
	else:
		for item in results:
			print(f"[{item.status}] {item.name}: {' '.join(item.command)}")
			if item.stdout.strip():
				print(item.stdout.rstrip())
			if item.stderr.strip():
				print(item.stderr.rstrip(), file=sys.stderr)
	return 0 if not failed else 1


if __name__ == "__main__":
	raise SystemExit(main())
