"""Quality baseline script tests."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock


ROOT = Path(__file__).resolve().parents[1]


def _load_quality_module() -> ModuleType:
	spec = importlib.util.spec_from_file_location(
		"quality_baseline",
		ROOT / "scripts" / "quality_baseline.py",
	)
	assert spec is not None
	assert spec.loader is not None
	module = importlib.util.module_from_spec(spec)
	sys.modules["quality_baseline"] = module
	spec.loader.exec_module(module)
	return module


def test_run_step_sets_pythonutf8_for_subprocess(monkeypatch) -> None:
	"""Windows 中文系统下子进程测试默认使用 UTF-8。"""
	module = _load_quality_module()
	seen_env: dict[str, str] = {}

	def fake_which(name: str) -> str | None:
		return "/usr/bin/pytest" if name == "pytest" else None

	def fake_run(*args, **kwargs):
		seen_env.update(kwargs["env"])
		return MagicMock(returncode=0, stdout="", stderr="")

	monkeypatch.setattr(module.shutil, "which", fake_which)
	monkeypatch.setattr(module.subprocess, "run", fake_run)
	monkeypatch.delenv("PYTHONUTF8", raising=False)

	result = module.run_step("pytest", ("pytest", "-q"))

	assert result.status == "ok"
	assert seen_env["PYTHONUTF8"] == "1"
