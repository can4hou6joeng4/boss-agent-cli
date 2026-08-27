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

# 不硬编码「装饰器名 → request 类型」映射表：在全新 Server 上应用装饰器，diff
# request_handlers 的键集合，多出来的那一个就是它负责的类型。SDK 将来新增
# handler（list_prompts / read_resource / ...）时这张表自己跟着长，守卫的覆盖面
# 不需要人工维护——这正是 #377 那类问题最需要的性质。
_REGISTRATION_PROBE = """
import inspect
import json
import warnings

from mcp.server import Server

import boss_agent_cli.mcp_server as mcp_server


async def _dummy(*args, **kwargs):
	return []


def _handler_decorators():
	discovered = {}
	for name in dir(Server):
		if name.startswith("_"):
			continue
		probe = Server("probe")
		before = set(probe.request_handlers)
		with warnings.catch_warnings():
			warnings.simplefilter("ignore")
			try:
				getattr(probe, name)()(_dummy)
			except Exception:
				# 不是 handler 装饰器工厂（run / create_initialization_options / ...）
				continue
		added = set(probe.request_handlers) - before
		if len(added) == 1:
			discovered[name] = added.pop()
	return discovered


decorators = _handler_decorators()
registered = set(mcp_server.server.request_handlers)
declared = []
unregistered = []
for name in mcp_server.__all__:
	attr = getattr(mcp_server, name, None)
	if not inspect.iscoroutinefunction(attr) or name not in decorators:
		continue
	declared.append(name)
	if decorators[name] not in registered:
		unregistered.append(name)

print(json.dumps({
	"known_decorators": sorted(decorators),
	"declared_handlers": sorted(declared),
	"unregistered_handlers": sorted(unregistered),
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

	# 探针自身的健全性检查。少了这条，一旦 SDK 改了内部结构导致探针什么都发现不了，
	# unregistered 会是空列表，守卫就静默变成恒真——比没有守卫更糟。
	assert report["known_decorators"], "没能从 SDK 反推出任何 handler 装饰器：探针坏了"
	assert {"call_tool", "list_tools"} <= set(report["declared_handlers"]), (
		f"__all__ 里少了已知的 MCP handler，实际发现 {report['declared_handlers']}；"
		"把 handler 从 __all__ 移出去会同时绕开本守卫"
	)

	assert report["unregistered_handlers"] == [], (
		f"{report['unregistered_handlers']} 写进了 __all__ 却没出现在 server.request_handlers 里，"
		"即漏了 @server.<name>() 装饰器。MCP host 调用对应方法会收到 -32601 Method not found。"
	)


def test_tools_over_protocol_match_the_catalog() -> None:
	report = _run_probe(_CATALOG_PROBE)

	assert report["in_catalog"], "工具目录为空：探针坏了"
	assert report["over_protocol"] == report["in_catalog"], (
		"tools/list 发出的工具集合与 mcp_tools.TOOLS 不一致；"
		f"只在协议层：{sorted(set(report['over_protocol']) - set(report['in_catalog']))}；"
		f"只在目录里：{sorted(set(report['in_catalog']) - set(report['over_protocol']))}"
	)
