"""安装后的 MCP 命令入口：缺少可选 SDK 时提供安装说明。"""

from __future__ import annotations

import sys


def run(argv: list[str] | None = None) -> None:
	try:
		from boss_agent_cli.mcp_server import run as run_server
	except ModuleNotFoundError as exc:
		if exc.name != "mcp":
			raise
		# MCP stdio 的 stdout 只承载协议消息；启动诊断必须写到 stderr。
		sys.stderr.write(
			"boss-mcp 需要可选的 MCP SDK，默认安装不包含它。\n"
			"请用原安装方式补上 mcp 依赖：\n"
			'  pip install "boss-agent-cli[mcp]"\n'
			'  或 uv tool install "boss-agent-cli[mcp]"\n'
			"源码仓库内运行 uv sync --extra mcp，安装后重新启动 boss-mcp。\n"
		)
		raise SystemExit(1) from None

	run_server(argv)


if __name__ == "__main__":
	run()
