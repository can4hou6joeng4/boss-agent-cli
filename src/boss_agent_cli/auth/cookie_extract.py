import glob
import sqlite3
import sys
from pathlib import Path
from typing import Any, Callable


_PLATFORM_COOKIE_CONFIG: dict[str, dict[str, str]] = {
	"zhipin": {
		"domain": ".zhipin.com",
		"required_cookie": "wt2",
		"stoken_cookie": "__zp_stoken__",
	},
}

# 元组顺序即自动检测优先级；新增浏览器追加在末尾作为后备，
# 不改变既有浏览器的优先级。macOS Safari 需要完全磁盘访问，
# 读取失败会被 _try_extract 静默跳过并继续下一个。
_BROWSER_LOADER_NAMES: tuple[str, ...] = (
	"chrome",
	"firefox",
	"edge",
	"brave",
	"opera",
	"chromium",
	"vivaldi",
	"arc",
	"librewolf",
	"safari",
)

# macOS 上 browser_cookie3 构造 Chromium 系 loader 时，先调 /usr/bin/security 读
# 「<浏览器> Safe Storage」，之后才去找 Cookie 文件（0.20.x 的
# ChromiumBased.__add_key_and_cookie_file），而且不设超时。自动检测因此会让每个装了的
# Chromium 浏览器各弹一次钥匙串授权，哪怕它从没登录过目标站点。
# Cookie 库的 host_key / name 两列是明文：以只读方式确认库里有没有目标 Cookie，
# 确定没有才跳过 loader；判断不了时照旧交给 loader。
# 下面是各浏览器在 ~/Library/Application Support 下的数据根目录，须覆盖 browser_cookie3
# 的 osx_cookies（tests/test_cookie_extract_regression.py 逐条核对）。
_MACOS_CHROMIUM_DATA_ROOTS: dict[str, tuple[str, ...]] = {
	"chrome": ("Google/Chrome", "Google/Chrome Beta", "Google/Chrome Dev"),
	"edge": ("Microsoft Edge", "Microsoft Edge Beta", "Microsoft Edge Dev", "Microsoft Edge Canary"),
	"brave": (
		"BraveSoftware/Brave-Browser",
		"BraveSoftware/Brave-Browser-Beta",
		"BraveSoftware/Brave-Browser-Dev",
		"BraveSoftware/Brave-Browser-Nightly",
	),
	"opera": ("com.operasoftware.Opera", "com.operasoftware.OperaNext", "com.operasoftware.OperaDeveloper"),
	"chromium": ("Chromium",),
	"vivaldi": ("Vivaldi",),
	"arc": ("Arc/User Data",),
}
# 数据根目录下 Cookie 库的位置：Opera 直接放在根目录，其余在 Default / Profile N 下，
# 新版 Chromium 还会放进 Network/ 子目录。宁可多查，不能漏查。
_CHROMIUM_COOKIE_DB_GLOBS: tuple[str, ...] = ("Cookies", "*/Cookies", "*/Network/Cookies")


def extract_cookies(source: str | None = None, *, platform: str = "zhipin") -> dict[str, Any] | None:
	"""
	从本地浏览器提取指定平台的 Cookie。
	source: 指定浏览器名（如 "chrome"），None 则自动检测。
	返回 {"cookies": {...}, "user_agent": "", "stoken": ""} 或 None。
	"""
	try:
		import browser_cookie3
	except ImportError:
		return None
	config = _PLATFORM_COOKIE_CONFIG.get(platform)
	if config is None:
		print(f"不支持的平台: {platform}", file=sys.stderr)
		return None
	domain_name = config["domain"]
	required_cookie = config["required_cookie"]
	stoken_cookie = config["stoken_cookie"]

	# 按名字取 loader，旧版 browser_cookie3 缺的（0.20 前没有 arc）不进字典，
	# 自动检测跳过它，而不是在这里抛 AttributeError 打断整条登录降级链。
	loaders: dict[str, Callable[..., Any]] = {}
	for name in _BROWSER_LOADER_NAMES:
		loader = getattr(browser_cookie3, name, None)
		if callable(loader):
			loaders[name] = loader

	if source:
		# 指定浏览器
		loader = loaders.get(source.lower())
		if loader is None:
			if source.lower() in _BROWSER_LOADER_NAMES:
				print(
					f"当前 browser_cookie3 版本不提供 {source} 加载器，请升级 browser-cookie3",
					file=sys.stderr,
				)
				return None
			print(f"不支持的浏览器: {source}，支持: {', '.join(_BROWSER_LOADER_NAMES)}", file=sys.stderr)
			return None
		if _keychain_read_avoidable(source.lower(), domain_name=domain_name, cookie_name=required_cookie):
			print(
				f"{source} 的 Cookie 库中没有 {domain_name.lstrip('.')} 的登录记录，未读取钥匙串",
				file=sys.stderr,
			)
			return None
		return _try_extract(
			loader,
			domain_name=domain_name,
			required_cookie=required_cookie,
			stoken_cookie=stoken_cookie,
		)

	# 自动检测：按优先级尝试
	skipped: list[str] = []
	for name, loader in loaders.items():
		if _keychain_read_avoidable(name, domain_name=domain_name, cookie_name=required_cookie):
			skipped.append(name)
			continue
		result = _try_extract(
			loader,
			domain_name=domain_name,
			required_cookie=required_cookie,
			stoken_cookie=stoken_cookie,
		)
		if result:
			print(f"从 {name} 提取到 {platform} Cookie", file=sys.stderr)
			return result

	if skipped:
		print(
			f"{', '.join(skipped)} 的 Cookie 库中没有 {domain_name.lstrip('.')} 的登录记录，已跳过钥匙串读取",
			file=sys.stderr,
		)
	return None


def _keychain_read_avoidable(browser: str, *, domain_name: str, cookie_name: str) -> bool:
	"""macOS 上已确认该浏览器的 Cookie 库里没有目标 Cookie 时返回 True，调用方跳过 loader。"""
	if not _is_macos():
		return False
	return _chromium_cookie_presence(browser, domain_name=domain_name, cookie_name=cookie_name) is False


def _is_macos() -> bool:
	return sys.platform == "darwin"


def _macos_app_support_dir() -> Path:
	return Path.home() / "Library" / "Application Support"


def _chromium_cookie_presence(browser: str, *, domain_name: str, cookie_name: str) -> bool | None:
	"""不碰钥匙串，判断该浏览器的 Cookie 库里有没有目标 Cookie 行。

	True：至少一个库里有；False：找到了库且逐个确认都没有；None：判断不了
	（不是 Chromium 系、没找到任何库、有库读不了），调用方照旧交给 loader。
	"""
	roots = _MACOS_CHROMIUM_DATA_ROOTS.get(browser)
	if roots is None:
		return None
	base = _macos_app_support_dir()
	db_files = sorted({
		match
		for root in roots
		for pattern in _CHROMIUM_COOKIE_DB_GLOBS
		for match in glob.glob(f"{glob.escape(str(base / root))}/{pattern}")
	})
	if not db_files:
		return None
	undetermined = False
	for db_file in db_files:
		present = _cookie_row_present(db_file, domain_name=domain_name, cookie_name=cookie_name)
		if present:
			return True
		if present is None:
			undetermined = True
	return None if undetermined else False


def _cookie_row_present(db_file: str, *, domain_name: str, cookie_name: str) -> bool | None:
	"""只读查 host_key / name 两个明文列，不取任何 Cookie 值。

	浏览器运行时库可能被锁，按 browser_cookie3 的顺序降级打开；但 nolock / immutable
	会忽略尚未合并的 WAL，刚登录写入的行可能看不到，所以只有普通只读查询得出的
	「没有」算数，降级查询只用来确认「有」。LIKE 模式与 browser_cookie3 加载时一致。
	"""
	uri = Path(db_file).absolute().as_uri()
	for options, absence_is_conclusive in (
		("?mode=ro", True),
		("?mode=ro&nolock=1", False),
		("?mode=ro&immutable=1", False),
	):
		try:
			con = sqlite3.connect(uri + options, uri=True, timeout=0)
			try:
				row = con.execute(
					"SELECT 1 FROM cookies WHERE host_key LIKE ? AND name = ? LIMIT 1",
					(f"%{domain_name}%", cookie_name),
				).fetchone()
			finally:
				con.close()
		except sqlite3.Error:
			continue
		if row is not None:
			return True
		if absence_is_conclusive:
			return False
	return None


def _try_extract(
	loader: Callable[..., Any],
	*,
	domain_name: str = ".zhipin.com",
	required_cookie: str = "wt2",
	stoken_cookie: str = "__zp_stoken__",
) -> dict[str, Any] | None:
	"""尝试从单个浏览器提取指定域名 cookies。"""
	try:
		cj = loader(domain_name=domain_name)
		domain_fragment = domain_name.lstrip(".")
		cookies = {c.name: c.value for c in cj if domain_fragment in (c.domain or "")}
		has_required_cookie = required_cookie in cookies
		if not cookies or not has_required_cookie:
			return None
		return {
			"cookies": cookies,
			"user_agent": "",  # browser-cookie3 无法获取 UA，后续由 httpx 默认 UA 补充
			"stoken": cookies.get(stoken_cookie, "") if stoken_cookie else "",
		}
	except Exception:
		# 故意捕获所有异常：browser_cookie3 在浏览器未安装、profile 路径不可访问、
		# 数据库被锁定等场景会抛库自定义的 BrowserCookieError 等异常，这些不在 stdlib
		# 异常树里。本函数是 best-effort 降级链路的一环，任何失败都应静默返回 None
		# 让上层尝试下一个浏览器/通道，而非中断整条调用链。
		return None
