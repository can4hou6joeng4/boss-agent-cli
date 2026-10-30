import sys
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
		return _try_extract(
			loader,
			domain_name=domain_name,
			required_cookie=required_cookie,
			stoken_cookie=stoken_cookie,
		)

	# 自动检测：按优先级尝试
	for name, loader in loaders.items():
		result = _try_extract(
			loader,
			domain_name=domain_name,
			required_cookie=required_cookie,
			stoken_cookie=stoken_cookie,
		)
		if result:
			print(f"从 {name} 提取到 {platform} Cookie", file=sys.stderr)
			return result

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
