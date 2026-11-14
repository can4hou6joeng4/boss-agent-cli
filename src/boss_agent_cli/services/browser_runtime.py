"""探测 patchright 所需 Chromium 是否已安装（doctor 检查与 wizard 预检共用）。"""

from __future__ import annotations

import json
import os
import shlex
import sys
from pathlib import Path

#: Playwright / patchright 报告「浏览器内核缺失」的文案。两条来自不同位置：
#: ``chromium.launch()`` 抛 ``Executable doesn't exist at <路径>``；
#: ``sync_playwright().start()`` 在驱动刚升级、内核未装时抛 ``Looks like
#: Playwright was just installed or updated`` 并要求 ``playwright install``。
#: 都是确定性失败：重试同一条命令永远不会成功。
BROWSER_KERNEL_MISSING_SIGNATURES: tuple[str, ...] = (
	"executable doesn't exist",
	"playwright was just installed",
)


def is_browser_kernel_missing(message: str) -> bool:
	"""判断异常文本是否为浏览器内核缺失（大小写不敏感）。"""
	text = (message or "").lower()
	return any(signature in text for signature in BROWSER_KERNEL_MISSING_SIGNATURES)


def patchright_chromium_revision() -> str | None:
	"""读取 patchright 自带 browsers.json 声明的 chromium 修订版。"""
	try:
		import patchright

		browsers_json = Path(patchright.__file__).resolve().parent / "driver" / "package" / "browsers.json"
		data = json.loads(browsers_json.read_text(encoding="utf-8"))
	except Exception:
		return None
	for browser in data.get("browsers", []):
		if browser.get("name") == "chromium":
			revision = browser.get("revision")
			return str(revision) if revision else None
	return None


def patchright_browser_cache_dirs() -> list[Path]:
	"""Return Playwright/Patchright browser cache directories for the current OS."""
	dirs = [
		Path.home() / ".cache" / "ms-playwright",
		Path.home() / "Library" / "Caches" / "ms-playwright",
	]
	if local_app_data := os.environ.get("LOCALAPPDATA"):
		dirs.append(Path(local_app_data) / "ms-playwright")
	return dirs


def installed_browser_dirs() -> list[Path]:
	"""展开缓存根目录下的全部浏览器条目（``chromium-*`` / ``chromium_headless_shell-*``）。

	``evaluate_patchright_chromium`` 按条目名精确匹配修订版，只能接收展开后的
	条目；直接把缓存根目录传进去永远匹配不上（见 Issue #1060 的向导预检）。
	"""
	entries: list[Path] = []
	for base in patchright_browser_cache_dirs():
		if not base.exists():
			continue
		entries.extend(base.glob("chromium-*"))
		entries.extend(base.glob("chromium_headless_shell-*"))
	return entries


def patchright_importable() -> bool:
	"""patchright 是否可在当前解释器导入。

	内核安装与浏览器启动都走这个解释器，不依赖 PATH：``uv tool install`` /
	``pipx install`` 只把 ``boss`` / ``boss-mcp`` 暴露到 PATH，把 patchright 当成
	PATH 上缺失的可执行文件是误报（Issue #1060）。
	"""
	try:
		import patchright  # noqa: F401
	except Exception:
		return False
	return True


def patchright_install_command() -> str:
	"""返回当前安装形态下可直接执行的内核安装命令。

	``patchright install chromium`` 要求 ``patchright`` 在 PATH 上，而
	``uv tool install`` / ``pipx install`` 只暴露 ``boss`` 与 ``boss-mcp`` 两个入口，
	依赖的可执行文件留在工具环境里——按该提示执行会得到 ``command not found``
	（Issue #1060）。改用运行本 CLI 的同一个解释器调模块入口：它必然能导入
	patchright，且版本与启动浏览器时一致，不会装成另一个修订版。
	"""
	return f"{shlex.quote(sys.executable)} -m patchright install chromium"


def patchright_headless_shell_command() -> str:
	""":func:`patchright_install_command` 的 headless shell 变体。"""
	return f"{shlex.quote(sys.executable)} -m patchright install chromium-headless-shell"


def evaluate_patchright_chromium(required_revision: str | None, installed: list[Path]) -> tuple[str, str]:
	"""Return (status, detail) for the patchright_chromium doctor check.

	缺内核是确定性失败：重试原命令不会成功，detail 直接给出可执行安装命令。
	"""
	if required_revision:
		expected = f"chromium-{required_revision}"
		expected_headless = f"chromium_headless_shell-{required_revision}"
		has_chromium = any(p.name == expected for p in installed)
		has_headless = any(p.name == expected_headless for p in installed)
		if has_chromium and has_headless:
			return "ok", f"已安装 patchright 所需修订版 {expected} 与 {expected_headless}"
		if has_chromium:
			return (
				"error",
				f"已安装 {expected}，但缺少 {expected_headless}；如全局 tool 环境启动失败，运行 "
				f"{patchright_headless_shell_command()}",
			)
		found = "、".join(sorted(p.name for p in installed)) or "无"
		return (
			"error",
			f"patchright 需要 {expected}，本机缓存仅有：{found}；boss login 启动内置浏览器会失败，"
			f"请运行 {patchright_install_command()}",
		)
	if installed:
		return "ok", f"检测到 {len(installed)} 个 Chromium 安装（无法确认 patchright 所需修订版）"
	return (
		"warn",
		f"未检测到 patchright/Playwright Chromium 缓存；如需登录或导出，运行 {patchright_install_command()}",
	)
