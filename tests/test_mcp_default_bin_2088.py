"""MCP server 默认 boss 入口在模块加载时解析（Issue #2088）。

`_BOSS_BIN` 原先停留在裸名字 `"boss"`，只有 `run()` 启动服务器时才换成解释器
同目录的入口。嵌入方与测试直接调用 `_run_boss()` 时从 PATH 找 boss：PATH 上没有
入口会全部 `CLI_ERROR`，PATH 上有旧安装（或坏 shim）会被静默驱动。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from boss_agent_cli import mcp_server


def test_module_default_boss_bin_matches_interpreter_sibling() -> None:
	"""默认值必须与 run() 的解析口径一致，不能停留在裸名字。"""
	assert mcp_server._BOSS_BIN == mcp_server._default_boss_bin()


def test_run_boss_uses_resolved_entry_without_path(monkeypatch: pytest.MonkeyPatch) -> None:
	"""PATH 不可用时，同目录入口存在则默认调用仍能执行。"""
	if mcp_server._default_boss_bin() == mcp_server.DEFAULT_BOSS_BIN:
		pytest.skip("运行解释器同目录没有 boss 入口")
	monkeypatch.setenv("PATH", str(Path(sys.executable).parent / "__no_such_dir__"))
	monkeypatch.setattr(mcp_server, "_BOSS_GLOBAL_ARGS", [])

	result = mcp_server._run_boss("schema")

	assert result["ok"] is True
	assert result["command"] == "schema"
