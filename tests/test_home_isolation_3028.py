"""测试进程的家目录必须隔离，默认 ``--data-dir`` 不能落到真实 ``~/.boss-agent``（Issue #3028）。"""

import getpass
import json
import os
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from boss_agent_cli.main import cli


def _real_home_from_passwd() -> Path | None:
	"""系统真实家目录：``expanduser("~user")`` 走 pwd 数据库，不受 ``HOME`` 影响。"""
	if sys.platform.startswith("win"):
		return None
	try:
		user = getpass.getuser()
	except (KeyError, OSError):
		return None
	if not user or os.sep in user:
		return None
	expanded = os.path.expanduser(f"~{user}")
	if expanded in {f"~{user}", ""}:
		return None
	return Path(expanded)


def test_test_suite_home_is_not_real_user_home():
	"""autouse fixture 必须把 ``Path.home()`` 指向临时目录，测试才不会读写真实家目录。"""
	real_home = _real_home_from_passwd()
	if real_home is None:
		pytest.skip("无法从 pwd 数据库确定系统真实家目录")
	assert Path.home().resolve() != real_home.resolve()


def test_cli_default_data_dir_lives_under_isolated_home():
	"""未传 ``--data-dir`` 的 CLI 根调用必须使用隔离 HOME 下的默认目录。"""
	home = Path.home().resolve()
	result = CliRunner().invoke(cli, ["--json", "config", "list"])

	assert result.exit_code == 0, result.output
	parsed = json.loads(result.stdout)
	config_path = Path(parsed["data"]["config_path"]).resolve()
	assert config_path == home / ".boss-agent" / "config.json"
