import json
import os
import shutil
import tempfile
from pathlib import Path

import pytest

#: 本次 pytest 会话的隔离家目录。``pytest_configure`` 在测试模块导入前设置 ``HOME``，
#: 所以 ``Path.home()`` 的模块级常量（如 test_commands 的 ``_DEFAULT_DATA_DIR``）
#: 与运行时解析出的默认 ``--data-dir`` 仍然一致，同时不再触碰真实用户目录。
_SESSION_HOME: str | None = None


def pytest_configure(config):
	"""把整个测试会话的家目录隔离到临时目录（Issue #3028）。"""
	global _SESSION_HOME
	if _SESSION_HOME is not None:
		return
	_SESSION_HOME = tempfile.mkdtemp(prefix="boss-test-home-")
	os.environ["HOME"] = _SESSION_HOME
	# Windows 上 ``Path.expanduser`` 读 USERPROFILE；POSIX 读 HOME。
	os.environ["USERPROFILE"] = _SESSION_HOME


def pytest_unconfigure(config):
	"""删除会话隔离家目录，避免临时目录堆积。"""
	global _SESSION_HOME
	if _SESSION_HOME is not None:
		shutil.rmtree(_SESSION_HOME, ignore_errors=True)
		_SESSION_HOME = None


@pytest.fixture(autouse=True)
def _restricted_surface_open_for_existing_contract_tests(request, monkeypatch):
	"""Keep historical command contract tests focused on pre-guard behavior."""
	if request.node.path.name == "test_compliance.py":
		return
	from boss_agent_cli.config import DEFAULTS

	monkeypatch.setitem(DEFAULTS, "operating_mode", "research")


@pytest.fixture
def restricted_surface_data_dir(tmp_path: Path) -> Path:
	"""Data dir for tests that intentionally exercise pre-guard command contracts."""
	(tmp_path / "config.json").write_text(
		json.dumps({"operating_mode": "research"}, ensure_ascii=False),
		encoding="utf-8",
	)
	return tmp_path


@pytest.fixture
def restricted_surface_args(restricted_surface_data_dir: Path) -> list[str]:
	return ["--data-dir", str(restricted_surface_data_dir)]


@pytest.fixture
def legacy_args(restricted_surface_args: list[str]) -> list[str]:
	return restricted_surface_args
