import sys
from typing import Any, Callable


_PLATFORM_COOKIE_CONFIG: dict[str, dict[str, str]] = {
	"zhipin": {
		"domain": ".zhipin.com",
		"required_cookie": "wt2",
		"stoken_cookie": "__zp_stoken__",
	},
	"zhilian": {
		"domain": ".zhaopin.com",
		"required_cookie": "at",
		"stoken_cookie": "",
	},
}


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

	# 浏览器加载函数映射
	loaders = {
		"chrome": browser_cookie3.chrome,
		"firefox": browser_cookie3.firefox,
		"edge": browser_cookie3.edge,
		"brave": browser_cookie3.brave,
		"opera": browser_cookie3.opera,
		"chromium": browser_cookie3.chromium,
	}

	if source:
		# 指定浏览器
		loader = loaders.get(source.lower())
		if loader is None:
			print(f"不支持的浏览器: {source}，支持: {', '.join(loaders.keys())}", file=sys.stderr)
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
		has_required_cookie = required_cookie in cookies or (required_cookie == "at" and "zp_token" in cookies)
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
