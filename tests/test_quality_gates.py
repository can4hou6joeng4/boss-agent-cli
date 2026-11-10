"""Quality gate configuration tests.

本地门禁与 CI 必须是同一套工具。pre-commit 的远程钩子会自己装一份工具，
一旦版本与 `uv.lock` 漂移，本地提交时和 CI 复核时跑的就是两个不同的版本
（ruff-pre-commit v0.8.6 与 uv.lock 的 0.17.4 曾长期并存）。
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
PRE_COMMIT_CONFIG = ROOT / ".pre-commit-config.yaml"
PYPROJECT = ROOT / "pyproject.toml"
SOURCE_ROOT = ROOT / "src" / "boss_agent_cli"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def _ruff_hooks() -> list[tuple[dict[str, Any], dict[str, Any]]]:
	config = yaml.safe_load(PRE_COMMIT_CONFIG.read_text(encoding="utf-8"))
	return [
		(repo, hook)
		for repo in config["repos"]
		for hook in repo.get("hooks", [])
		if hook.get("id") == "ruff"
	]


def _strict_mypy_modules() -> set[str]:
	"""Return the module names that opt into mypy's per-module strict checks."""
	config = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
	modules: set[str] = set()
	for override in config["tool"]["mypy"].get("overrides", []):
		if override.get("disallow_untyped_defs") is True:
			modules.update(override.get("module", []))
	return modules


def _source_modules() -> set[str]:
	modules: set[str] = set()
	for path in SOURCE_ROOT.rglob("*.py"):
		module = ".".join(path.relative_to(ROOT / "src").with_suffix("").parts)
		modules.add(module.removesuffix(".__init__"))
	return modules


def _strict_covers(module: str, strict: set[str]) -> bool:
	if module in strict:
		return True
	# mypy 的 per-module 配置不会被子模块继承：`foo.bar` 只管 `foo.bar` 本身，
	# 覆盖子模块必须显式写 `foo.bar.*`。守卫按同一语义判断，避免清单看起来
	# 很全、实际上漏掉新模块。
	return any(entry.endswith(".*") and module.startswith(entry[:-1]) for entry in strict)


def test_ruff_hook_runs_the_project_copy() -> None:
	"""ruff 只允许有项目环境这一份，钩子不得另装会与 uv.lock 漂移的副本。"""
	hooks = _ruff_hooks()

	assert len(hooks) == 1, "ruff 钩子只应定义一次"
	repo, hook = hooks[0]

	assert repo["repo"] == "local", "ruff 钩子不得指向 ruff-pre-commit（版本会与 uv.lock 漂移）"
	assert hook["language"] == "system"
	assert hook["entry"].split()[:3] == ["uv", "run", "ruff"]


def test_mypy_strict_overrides_cover_every_source_module() -> None:
	"""每个源码模块都必须进 mypy 严格清单，漏登记会悄悄退回默认检查。"""
	strict = _strict_mypy_modules()
	modules = _source_modules()

	uncovered = sorted(module for module in modules if not _strict_covers(module, strict))
	assert not uncovered, f"以下模块未纳入 mypy 严格 overrides：{uncovered}"

	stale = sorted(entry for entry in strict if not entry.endswith(".*") and entry not in modules)
	assert not stale, f"mypy 严格 overrides 登记了不存在的模块：{stale}"


def test_ci_test_matrix_covers_declared_python_classifiers() -> None:
	"""CI 必须实测 pyproject 声明的每个 Python 3.x 版本。

	3.13 曾在 CI 重写时被无声丢掉，而 classifiers 仍声明支持；声明了却不测，
	3.13 用户会先于 CI 撞上兼容性问题。
	"""
	config = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))
	matrix = set(config["jobs"]["test"]["strategy"]["matrix"]["python-version"])
	classifiers = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["classifiers"]
	declared = {
		classifier.rsplit("::", 1)[1].strip()
		for classifier in classifiers
		if classifier.startswith("Programming Language :: Python :: 3.")
	}

	assert declared <= matrix, f"CI 矩阵缺少已声明支持的 Python 版本：{sorted(declared - matrix)}"
