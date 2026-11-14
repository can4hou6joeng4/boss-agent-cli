"""可选依赖组（extras）的安装指引：补装一个时不能丢掉已有的另一个。

``uv tool install "boss-agent-cli[crawl]"`` 每次按给定 requirement 重建工具环境，
不保留之前选过的 extra：已装 ``[mcp]`` 的用户照做后 MCP SDK 被移除，再按
``boss-mcp`` 的提示补 ``[mcp]`` 又会把采集依赖顶掉（Issue #1068）。因此指引里的
requirement 要把当前解释器里已经可用的依赖组一并列出。``pip install`` 是增量
安装，列全同样成立。
"""

from __future__ import annotations

import importlib.util
import sys

PACKAGE_NAME = "boss-agent-cli"

#: 每个面向用户的依赖组对应的探测模块；全部可定位才算该组已安装。
EXTRA_MODULES: dict[str, tuple[str, ...]] = {
	"crawl": ("DrissionPage", "openpyxl"),
	"mcp": ("mcp",),
}


def module_available(name: str) -> bool:
	"""模块能否在当前解释器定位到；只探测，不导入。"""
	if name in sys.modules:
		return sys.modules[name] is not None
	try:
		return importlib.util.find_spec(name) is not None
	except (ImportError, ValueError):
		return False


def installed_extras() -> tuple[str, ...]:
	"""当前解释器里已经完整可用的依赖组，按名称排序。"""
	return tuple(
		extra
		for extra, modules in sorted(EXTRA_MODULES.items())
		if all(module_available(name) for name in modules)
	)


def extras_requirement(*needed: str) -> str:
	"""返回 ``boss-agent-cli[a,b]``：要补的依赖组加上已经可用的依赖组。"""
	extras = sorted({*needed, *installed_extras()})
	return f"{PACKAGE_NAME}[{','.join(extras)}]"


def extras_install_hint(*needed: str) -> str:
	"""pip / uv tool 两种安装方式的可照抄命令，写法固定供各入口复用。"""
	requirement = extras_requirement(*needed)
	return f'pip install "{requirement}" 或 uv tool install "{requirement}"'
