"""探测 patchright 所需 Chromium 是否已安装（doctor 检查与 wizard 预检共用）。"""

from __future__ import annotations

import json
import os
import re
import shlex
import stat
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
	# 文件形状漂移（顶层是数组、browsers 不是数组、混入 null）时按「读不到」
	# 处理，不能让 doctor 因本地 JSON 结构异常丢掉信封（Issue #1318）。
	browsers = data.get("browsers") if isinstance(data, dict) else None
	if not isinstance(browsers, list):
		return None
	for browser in browsers:
		if not isinstance(browser, dict):
			continue
		if browser.get("name") == "chromium":
			revision = browser.get("revision")
			# 形状漂移（bool / dict / list）时 str() 会生成 "True" / repr 作为
			# 伪修订版，让 doctor 按不存在的版本比对（Issue #2318）。
			if isinstance(revision, bool) or not isinstance(revision, (str, int)):
				return None
			text = str(revision).strip()
			return text or None
	return None


def _default_browser_cache_dirs() -> list[Path]:
	"""与驱动 ``computeDefaultCacheDirectory`` 同优先级选择唯一缓存根。

	驱动只会读取当前平台对应的一个目录；把多个平台候选混在一起会让 doctor
	在错误的根上命中并报「已安装」（Issue #2318）：

	- Windows：``LOCALAPPDATA``，缺失时回退 ``~/AppData/Local``；
	- macOS：``~/Library/Caches``（不读 XDG）；
	- 其它（Linux）：``XDG_CACHE_HOME``，缺失时回退 ``~/.cache``。
	"""
	if sys.platform == "win32":
		base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
	elif sys.platform == "darwin":
		base = Path.home() / "Library" / "Caches"
	else:
		base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
	return [base / "ms-playwright"]


def patchright_browser_cache_dirs() -> list[Path]:
	"""按驱动的缓存位置检测浏览器，显式配置时不混入默认缓存。"""
	if configured := os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
		if configured == "0":
			try:
				import patchright

				package_root = Path(patchright.__file__).resolve().parent / "driver" / "package"
			except Exception:
				# 无法定位驱动包时不借用默认缓存冒充包内安装。
				return []
			return [package_root / ".local-browsers"]
		cache = Path(configured)
		if not cache.is_absolute():
			# 与驱动一致：相对路径以 INIT_CWD（未设置时为当前目录）为基准。
			base = Path(os.environ.get("INIT_CWD") or Path.cwd())
			cache = Path(os.path.abspath(base / cache))
		return [cache]
	return _default_browser_cache_dirs()


#: 驱动安装完成后写入的标记文件；缺失说明目录只是残留或安装未完成。
_INSTALLATION_MARKER = "INSTALLATION_COMPLETE"


def _is_usable_browser_entry(entry: Path) -> bool:
	"""缓存条目是否为完整安装：必须是目录且带安装完成标记。

	只看目录名会把悬空符号链接、空目录或同名普通文件当成「已安装」，doctor
	通过后 ``chromium.launch()`` 仍报 ``Executable doesn't exist``（Issue #2318）。
	``stat()`` 跟随符号链接：指向完整安装的链接仍可用，悬空链接按 OSError 拒绝。
	"""
	try:
		mode = entry.stat().st_mode
	except (OSError, ValueError, RuntimeError):
		return False
	if not stat.S_ISDIR(mode):
		return False
	try:
		return (entry / _INSTALLATION_MARKER).is_file()
	except (OSError, ValueError, RuntimeError):
		return False


def _scan_browser_cache() -> tuple[list[Path], list[Path]]:
	"""展开缓存条目，并收集「存在但无法读取」的缓存根 / 条目。

	``Path.exists()`` 在 3.14 只吞部分 errno，权限错误会抛到 doctor 顶层，
	整份自检报告变成 LOCAL_IO_ERROR（Issue #2103）。按 lstat 口径只把
	``FileNotFoundError`` 当缺失；其余 OSError 记为不可读——既不能让 doctor
	崩溃，也不能当成「未安装内核」引导用户重复下载。

	缓存根枚举在 cwd 已删除 / HOME 无法解析 / 环境变量含 NUL 时也会抛
	``OSError`` / ``RuntimeError`` / ``ValueError``：统一按不可读兜底，不能让
	确定性的本地配置问题打穿整份自检（Issue #2317）。
	"""
	try:
		bases = patchright_browser_cache_dirs()
	except (OSError, ValueError, RuntimeError):
		return [], []

	entries: list[Path] = []
	unreadable: list[Path] = []
	for base in bases:
		try:
			mode = base.stat().st_mode
		except FileNotFoundError:
			continue
		except (OSError, ValueError, RuntimeError):
			unreadable.append(base)
			continue
		if not stat.S_ISDIR(mode):
			# 缓存根本身是普通文件 / 悬空链接：glob 会返回空，不能报成「未安装」
			# 并引导用户重复下载。
			unreadable.append(base)
			continue
		try:
			candidates = list(base.glob("chromium-*")) + list(base.glob("chromium_headless_shell-*"))
		except (OSError, ValueError, RuntimeError):
			# 目录存在但不可读（权限）：glob 在部分平台上直接抛错。
			unreadable.append(base)
			continue
		for entry in candidates:
			if _is_usable_browser_entry(entry):
				entries.append(entry)
			else:
				# 名称匹配但安装不完整（残留 / 悬空链接 / 同名文件）：单独上报，
				# 不当作可用条目。
				unreadable.append(entry)
	return entries, unreadable


def installed_browser_dirs() -> list[Path]:
	"""展开缓存根目录下的完整浏览器安装（``chromium-*`` / ``chromium_headless_shell-*``）。

	``evaluate_patchright_chromium`` 按条目名精确匹配修订版，只能接收展开后的
	条目；直接把缓存根目录传进去永远匹配不上（见 Issue #1060 的向导预检）。
	只有带 ``INSTALLATION_COMPLETE`` 标记的目录才算已安装：悬空链接、空目录
	与同名普通文件都会让 ``chromium.launch()`` 报 Executable doesn't exist
	（Issue #2318）。
	"""
	return _scan_browser_cache()[0]


def unreadable_browser_cache_dirs() -> list[Path]:
	"""存在但无法读取的缓存根 / 名称匹配但安装不完整的条目。

	doctor 据此提示「无法确认内核是否已安装」，而不是当成缺失去重装；
	预检与登录路径行为不变（Issue #2103、#2318、#2317）。
	"""
	return _scan_browser_cache()[1]


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


_WINDOWS_BARE_PATH = re.compile(r"[A-Za-z0-9_.:\\/+=-]+")


def interpreter_invocation(executable: str | None = None, *, windows: bool | None = None) -> str:
	"""把解释器路径写成可照抄执行的命令前缀。

	POSIX 用 ``shlex.quote``。Windows 不能套用：它的安全字符集不含反斜杠，任何
	路径都会被包上单引号——PowerShell 把引号开头的行当表达式（缺调用运算符
	``&``），cmd.exe 根本不把单引号当引号，两边都执行不了（Issue #1072）。
	Windows 上路径不含需转义字符时原样输出（PowerShell 与 cmd 均可执行），否则
	按 PowerShell 写成 ``& '<路径>'``，与 doctor 命令提示的引用约定一致。
	"""
	path = sys.executable if executable is None else executable
	on_windows = os.name == "nt" if windows is None else windows
	if not on_windows:
		return shlex.quote(path)
	if _WINDOWS_BARE_PATH.fullmatch(path):
		return path
	return "& '" + path.replace("'", "''") + "'"


def patchright_install_command() -> str:
	"""返回当前安装形态下可直接执行的内核安装命令。

	``patchright install chromium`` 要求 ``patchright`` 在 PATH 上，而
	``uv tool install`` / ``pipx install`` 只暴露 ``boss`` 与 ``boss-mcp`` 两个入口，
	依赖的可执行文件留在工具环境里——按该提示执行会得到 ``command not found``
	（Issue #1060）。改用运行本 CLI 的同一个解释器调模块入口：它必然能导入
	patchright，且版本与启动浏览器时一致，不会装成另一个修订版。
	"""
	return f"{interpreter_invocation()} -m patchright install chromium"


def patchright_headless_shell_command() -> str:
	""":func:`patchright_install_command` 的 headless shell 变体。"""
	return f"{interpreter_invocation()} -m patchright install chromium-headless-shell"


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
