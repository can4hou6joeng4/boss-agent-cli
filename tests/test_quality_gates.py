"""Quality gate configuration tests.

本地门禁与 CI 必须是同一套工具。pre-commit 的远程钩子会自己装一份工具，
一旦版本与 `uv.lock` 漂移，本地提交时和 CI 复核时跑的就是两个不同的版本
（ruff-pre-commit v0.8.6 与 uv.lock 的 0.17.4 曾长期并存）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
PRE_COMMIT_CONFIG = ROOT / ".pre-commit-config.yaml"


def _ruff_hooks() -> list[tuple[dict[str, Any], dict[str, Any]]]:
	config = yaml.safe_load(PRE_COMMIT_CONFIG.read_text(encoding="utf-8"))
	return [
		(repo, hook)
		for repo in config["repos"]
		for hook in repo.get("hooks", [])
		if hook.get("id") == "ruff"
	]


def test_ruff_hook_runs_the_project_copy() -> None:
	"""ruff 只允许有项目环境这一份，钩子不得另装会与 uv.lock 漂移的副本。"""
	hooks = _ruff_hooks()

	assert len(hooks) == 1, "ruff 钩子只应定义一次"
	repo, hook = hooks[0]

	assert repo["repo"] == "local", "ruff 钩子不得指向 ruff-pre-commit（版本会与 uv.lock 漂移）"
	assert hook["language"] == "system"
	assert hook["entry"].split()[:3] == ["uv", "run", "ruff"]
