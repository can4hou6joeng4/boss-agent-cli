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
		failures: list[str] = []
		result = _try_extract(
			loader,
			domain_name=domain_name,
			required_cookie=required_cookie,
			stoken_cookie=stoken_cookie,
			failures=failures,
		)
		if result is None:
			print(_explain_extract_failure(source, failures, domain_name, required_cookie), file=sys.stderr)
		return result

	# 自动检测：按优先级尝试
	skipped: list[str] = []
	undecryptable: list[str] = []
	for name, loader in loaders.items():
		if _keychain_read_avoidable(name, domain_name=domain_name, cookie_name=required_cookie):
			skipped.append(name)
			continue
		attempt_failures: list[str] = []
		result = _try_extract(
			loader,
			domain_name=domain_name,
			required_cookie=required_cookie,
			stoken_cookie=stoken_cookie,
			failures=attempt_failures,
		)
		if result:
			print(f"从 {name} 提取到 {platform} Cookie", file=sys.stderr)
			return result
		# 浏览器没装、库被锁之类的失败很常见，不逐个报；只汇总拿不到解密密钥的浏览器
		if any(_is_decryption_failure(reason) for reason in attempt_failures):
			undecryptable.append(name)

	if skipped:
		print(
			f"{', '.join(skipped)} 的 Cookie 库中没有 {domain_name.lstrip('.')} 的登录记录，已跳过钥匙串读取",
			file=sys.stderr,
		)
	if undecryptable:
		print(f"{', '.join(undecryptable)} 的 Cookie 无法解密：{_decryption_hint()}", file=sys.stderr)
	return None


def _keychain_read_avoidable(browser: str, *, domain_name: str, cookie_name: str) -> bool:
	"""macOS 上已确认该浏览器的 Cookie 库里没有目标 Cookie 时返回 True，调用方跳过 loader。"""
	if not _is_macos():
		return False
	return _chromium_cookie_presence(browser, domain_name=domain_name, cookie_name=cookie_name) is False


def _is_macos() -> bool:
	return _current_platform() == "darwin"


def _current_platform() -> str:
	return sys.platform


# browser_cookie3 拿不到解密密钥时的报错特征：macOS 钥匙串被拒、Linux 密钥环未解锁、
# Windows DPAPI 或新版 Chrome / Edge 的 App-Bound 加密。其余多是浏览器没装、库被锁。
_DECRYPTION_ERROR_KEYWORDS: tuple[str, ...] = (
	"key for cookie decryption",
	"safe storage",
	"keychain",
	"secretstorage",
	"dpapi",
	"cryptunprotectdata",
	"win32crypt",
)


def _is_decryption_failure(reason: str) -> bool:
	lowered = reason.lower()
	return any(keyword in lowered for keyword in _DECRYPTION_ERROR_KEYWORDS)


def _decryption_hint() -> str:
	platform = _current_platform()
	if platform == "darwin":
		return "macOS 钥匙串未授权读取浏览器的 Safe Storage，可在授权弹窗中选择允许后重试，或改用 boss login --cdp / --curl-file"
	if platform == "win32":
		return (
			"Windows 上新版 Chrome / Edge 的 App-Bound 加密（Chrome 127 起）无法从磁盘解密，"
			"请改用 boss login --cdp 连接已登录的浏览器，或用 --curl-file 导入"
		)
	return "系统密钥环未解锁或不可用（无头 / SSH 会话常见），请先解锁密钥环，或改用 boss login --cdp / --curl-file"


def _explain_extract_failure(source: str, failures: list[str], domain_name: str, required_cookie: str) -> str:
	"""显式指定浏览器却没提取到时，说明是读取失败还是根本没有登录 Cookie。"""
	if not failures:
		return f"{source} 中没有 {domain_name.lstrip('.')} 的登录 Cookie（{required_cookie}）"
	reason = failures[0]
	message = f"{source} 的 Cookie 读取失败（{reason}）"
	if _is_decryption_failure(reason):
		message += f"：{_decryption_hint()}"
	return message


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
	failures: list[str] | None = None,
) -> dict[str, Any] | None:
	"""尝试从单个浏览器提取指定域名 cookies。

	failures 非空时追加失败原因（异常类型与截断后的消息，不含 Cookie 值），供调用方给出诊断。
	"""
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
	except Exception as exc:
		# 故意捕获所有异常：browser_cookie3 在浏览器未安装、profile 路径不可访问、
		# 数据库被锁定等场景会抛库自定义的 BrowserCookieError 等异常，这些不在 stdlib
		# 异常树里。本函数是 best-effort 降级链路的一环，任何失败都应静默返回 None
		# 让上层尝试下一个浏览器/通道，而非中断整条调用链。
		if failures is not None:
			failures.append(f"{type(exc).__name__}: {str(exc)[:160]}")
		return None
