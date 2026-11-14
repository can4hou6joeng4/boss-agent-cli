"""安装后的 boss-mcp 入口在可选 SDK 缺失时给出可执行的修复说明。"""

import json
import subprocess
import sys
from pathlib import Path
from textwrap import dedent
from types import ModuleType

import pytest

from boss_agent_cli.mcp_entrypoint import run


@pytest.mark.parametrize("arguments", [[], ["--help"]])
def test_installed_entrypoint_explains_missing_mcp_without_writing_stdout(arguments):
	# 从打包元数据读取实际入口，避免测试了辅助函数却漏掉仍然指向旧模块的脚本。
	probe = dedent("""\
		import importlib
		import importlib.abc
		import json
		import sys
		import tomllib
		from pathlib import Path

		class WithoutMCP(importlib.abc.MetaPathFinder):
			def find_spec(self, fullname, path=None, target=None):
				if fullname == "mcp":
					raise ModuleNotFoundError("No module named 'mcp'", name="mcp")

		sys.meta_path.insert(0, WithoutMCP())
		project = tomllib.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
		module_name, function_name = project["project"]["scripts"]["boss-mcp"].split(":")
		getattr(importlib.import_module(module_name), function_name)(json.loads(sys.argv[2]))
	""")
	result = subprocess.run(
		[sys.executable, "-c", probe, str(Path(__file__).resolve().parents[1] / "pyproject.toml"), json.dumps(arguments)],
		capture_output=True, text=True, encoding="utf-8", timeout=30,
	)
	assert result.returncode == 1
	assert result.stdout == "", "启动诊断不能污染 MCP stdio 的 stdout"
	assert 'pip install "boss-agent-cli[mcp]"' in result.stderr
	assert 'uv tool install "boss-agent-cli[mcp]"' in result.stderr
	assert "uv sync --extra mcp" in result.stderr
	assert "Traceback" not in result.stderr


def test_importing_entrypoint_does_not_load_the_optional_sdk():
	probe = dedent("""\
		import importlib.abc
		import sys

		class WithoutMCP(importlib.abc.MetaPathFinder):
			def find_spec(self, fullname, path=None, target=None):
				if fullname == "mcp" or fullname.startswith("mcp."):
					raise AssertionError("入口模块不应在导入时加载 SDK")

		sys.meta_path.insert(0, WithoutMCP())
		import boss_agent_cli.mcp_entrypoint
	""")
	result = subprocess.run(
		[sys.executable, "-c", probe], capture_output=True, text=True, encoding="utf-8", timeout=30,
	)
	assert result.returncode == 0, result.stderr
	assert result.stdout == result.stderr == ""


@pytest.mark.parametrize("arguments", [None, [], ["--transport", "http", "--data-dir", "/tmp/boss-mcp-test"]])
def test_entrypoint_forwards_arguments_to_the_existing_server(monkeypatch, capsys, arguments):
	server = ModuleType("boss_agent_cli.mcp_server")
	seen = []
	server.run = lambda argv: seen.append(argv)
	monkeypatch.setitem(sys.modules, "boss_agent_cli.mcp_server", server)

	run(arguments)

	assert seen == [arguments]
	assert seen[0] is arguments
	captured = capsys.readouterr()
	assert captured.out == captured.err == ""


def test_entrypoint_preserves_a_missing_transitive_dependency_error():
	probe = dedent("""\
		import importlib.abc
		import sys

		class BrokenSDK(importlib.abc.MetaPathFinder):
			def find_spec(self, fullname, path=None, target=None):
				if fullname == "mcp":
					raise ModuleNotFoundError("No module named 'starlette'", name="starlette")

		sys.meta_path.insert(0, BrokenSDK())
		from boss_agent_cli.mcp_entrypoint import run
		run([])
	""")
	result = subprocess.run(
		[sys.executable, "-c", probe], capture_output=True, text=True, encoding="utf-8", timeout=30,
	)
	assert result.returncode == 1
	assert result.stdout == ""
	assert "ModuleNotFoundError: No module named 'starlette'" in result.stderr
	assert "boss-agent-cli[mcp]" not in result.stderr, "SDK 内部其他故障不能伪装成未安装 SDK"
