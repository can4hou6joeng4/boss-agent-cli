"""可选依赖组安装指引：补装一个不能丢掉另一个（Issue #1068）。"""

from __future__ import annotations

import pytest

from boss_agent_cli.services import install_extras
from boss_agent_cli.services.install_extras import (
	extras_install_hint,
	extras_requirement,
	installed_extras,
)


def _only(monkeypatch: pytest.MonkeyPatch, *modules: str) -> None:
	monkeypatch.setattr(install_extras, "module_available", lambda name: name in modules)


def test_requirement_names_only_the_needed_extra_on_a_plain_install(monkeypatch: pytest.MonkeyPatch) -> None:
	_only(monkeypatch)

	assert installed_extras() == ()
	assert extras_requirement("crawl") == "boss-agent-cli[crawl]"
	assert extras_requirement("mcp") == "boss-agent-cli[mcp]"


def test_adding_crawl_keeps_an_installed_mcp_extra(monkeypatch: pytest.MonkeyPatch) -> None:
	"""`uv tool install "boss-agent-cli[crawl]"` 会重建环境并移除 MCP SDK。"""
	_only(monkeypatch, "mcp")

	assert extras_requirement("crawl") == "boss-agent-cli[crawl,mcp]"
	assert extras_install_hint("crawl") == (
		'pip install "boss-agent-cli[crawl,mcp]" 或 uv tool install "boss-agent-cli[crawl,mcp]"'
	)


def test_adding_mcp_keeps_an_installed_crawl_extra(monkeypatch: pytest.MonkeyPatch) -> None:
	_only(monkeypatch, "DrissionPage", "openpyxl")

	assert extras_requirement("mcp") == "boss-agent-cli[crawl,mcp]"


def test_partially_installed_extra_is_not_reported_as_installed(monkeypatch: pytest.MonkeyPatch) -> None:
	"""只装了依赖组的一部分时不能声称它已安装，否则指引会漏掉真正要补的组。"""
	_only(monkeypatch, "DrissionPage")

	assert installed_extras() == ()
	assert extras_requirement("mcp") == "boss-agent-cli[mcp]"


def test_module_probe_does_not_import_and_survives_broken_specs(monkeypatch: pytest.MonkeyPatch) -> None:
	import sys

	monkeypatch.setitem(sys.modules, "boss_extra_probe_blocked", None)
	assert install_extras.module_available("boss_extra_probe_blocked") is False
	assert install_extras.module_available("boss_extra_probe_absent") is False
	assert "boss_extra_probe_absent" not in sys.modules
	assert install_extras.module_available("json") is True


def test_extra_module_table_matches_declared_optional_dependencies() -> None:
	"""登记的依赖组必须是 pyproject 里真实存在的 extra。"""
	import tomllib
	from pathlib import Path

	project = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8"))
	declared = project["project"]["optional-dependencies"]
	assert set(install_extras.EXTRA_MODULES) <= set(declared)
