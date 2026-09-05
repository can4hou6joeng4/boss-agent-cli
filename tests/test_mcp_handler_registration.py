"""MCP handler 注册守卫。

`mcp_server` 为了保住 `mcp-server/server.py` wrapper 的导入路径，把 handler 写进 `__all__`
并原样再导出。副作用是：一个**忘记加装饰器**的 handler 在 ruff / mypy 眼里完全正常——
它被 `__all__` 声明、被 wrapper 引用，静态检查认为它「有人用」。issue #377 里的
`list_tools` 就是这样带着 v1.18.0 发了出去，MCP host 一列工具就拿到 -32601。

所以这里不查装饰器语法，直接问运行时注册表：`server.request_handlers` 里有没有它。

两条测试都刻意在**子进程**里跑。`tests/test_mcp_server.py` 会在导入时
`sys.modules.setdefault("mcp", <mock 模块>)`，而 pytest 单进程收集全部测试模块——
一旦本文件拿到那个 mock，`request_handlers` 就是个 MagicMock，任何断言都恒真，
守卫会变成装饰用的。子进程保证拿到真实 SDK。
"""

import json
import subprocess
import sys
from typing import Any

# 不在测试里硬编码「本服务该注册哪些 method」：从 `mcp_server` 自己的源码里 AST 扫出
# 所有 `add_request_handler("<method>", ...)` 的字面量，再逐个问运行时注册表。新增
# handler 时守卫覆盖面自动跟上，不需人工维护——这正是 #377 那类问题最需要的性质。
#
# mcp 2.x 说明：1.x 时这段是「在全新 Server 上应用每个装饰器、diff request_handlers
# 反推映射」。2.0 移除了全部装饰器工厂（实测 `Server` 上无参公开方法数为 0），且
# `request_handlers` 变成私有 `_request_handlers`、公开访问器是 `get_request_handler`。
# 因此反推的对象从「SDK 的装饰器」换成「我们自己声明的 method 字符串」，断言仍然是
# 运行时注册表说了算，不看语法。
_REGISTRATION_PROBE = """
import ast
import inspect
import json

import boss_agent_cli.mcp_server as mcp_server

tree = ast.parse(inspect.getsource(mcp_server))
declared = sorted({
	node.args[0].value
	for node in ast.walk(tree)
	if isinstance(node, ast.Call)
	and isinstance(node.func, ast.Attribute)
	and node.func.attr == "add_request_handler"
	and node.args
	and isinstance(node.args[0], ast.Constant)
	and isinstance(node.args[0].value, str)
})

unregistered = [m for m in declared if mcp_server.server.get_request_handler(m) is None]

print(json.dumps({
	"declared_handlers": declared,
	"unregistered_handlers": unregistered,
}))
"""

# 协议层实际发出的工具集合，必须与 mcp_tools.TOOLS 逐名相等。用集合相等而不是数量
# 断言：既抓「工具漏发」，也抓「协议层发出了目录外的工具」，且不引入 73 这个硬编码
# 数字——CLAUDE.md 要求计数只有一个真源。
_CATALOG_PROBE = """
import asyncio
import json
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from boss_agent_cli.mcp_tools import TOOLS


async def main():
	params = StdioServerParameters(command=sys.executable, args=["-m", "boss_agent_cli.mcp_server"])
	async with stdio_client(params) as (read_stream, write_stream):
		async with ClientSession(read_stream, write_stream) as session:
			await session.initialize()
			listed = await session.list_tools()
	print(json.dumps({
		"over_protocol": sorted(tool.name for tool in listed.tools),
		"in_catalog": sorted(tool.name for tool in TOOLS),
	}))


asyncio.run(main())
"""


def _run_probe(source: str) -> dict[str, Any]:
	result = subprocess.run(
		[sys.executable, "-c", source],
		capture_output=True,
		text=True,
		encoding="utf-8",
		timeout=60,
	)
	assert result.returncode == 0, f"探针进程失败：\n{result.stderr}"
	parsed: dict[str, Any] = json.loads(result.stdout)
	return parsed


def test_every_declared_mcp_handler_is_registered() -> None:
	report = _run_probe(_REGISTRATION_PROBE)

	# 探针自身的健全性检查。少了这条，一旦 SDK 或本模块改了结构导致探针什么都发现不了，
	# unregistered 会是空列表，守卫就静默变成恒真——比没有守卫更糟。
	assert report["declared_handlers"], "没能从 mcp_server 源码里扫出任何 add_request_handler 调用：探针坏了"
	assert {"tools/list", "tools/call"} <= set(report["declared_handlers"]), (
		f"mcp_server 少注册了已知的 MCP 方法，实际扫到 {report['declared_handlers']}；"
		"这两个是 MCP host 列工具与调工具的最低要求"
	)

	assert report["unregistered_handlers"] == [], (
		f"{report['unregistered_handlers']} 出现在 add_request_handler 调用里，"
		"但运行时 server.get_request_handler() 查不到。"
		"MCP host 调用对应方法会收到 -32601 Method not found。"
	)


def test_tools_over_protocol_match_the_catalog() -> None:
	report = _run_probe(_CATALOG_PROBE)

	assert report["in_catalog"], "工具目录为空：探针坏了"
	assert report["over_protocol"] == report["in_catalog"], (
		"tools/list 发出的工具集合与 mcp_tools.TOOLS 不一致；"
		f"只在协议层：{sorted(set(report['over_protocol']) - set(report['in_catalog']))}；"
		f"只在目录里：{sorted(set(report['in_catalog']) - set(report['over_protocol']))}"
	)
