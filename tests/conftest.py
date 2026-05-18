import json
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _restricted_surface_open_for_existing_contract_tests(request, monkeypatch):
	"""Keep historical command contract tests focused on pre-guard behavior."""
	if request.node.path.name == "test_compliance.py":
		return
	from boss_agent_cli.config import DEFAULTS

	monkeypatch.setitem(DEFAULTS, "low_risk_mode", False)


@pytest.fixture
def restricted_surface_data_dir(tmp_path: Path) -> Path:
	"""Data dir for tests that intentionally exercise pre-guard command contracts."""
	(tmp_path / "config.json").write_text(
		json.dumps({"low_risk_mode": False}, ensure_ascii=False),
		encoding="utf-8",
	)
	return tmp_path


@pytest.fixture
def restricted_surface_args(restricted_surface_data_dir: Path) -> list[str]:
	return ["--data-dir", str(restricted_surface_data_dir)]


@pytest.fixture
def legacy_args(restricted_surface_args: list[str]) -> list[str]:
	return restricted_surface_args
