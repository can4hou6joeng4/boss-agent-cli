import sys
from typing import Any, Callable


def extract_cookies(source: str | None = None) -> dict[str, Any] | None:
	"""
	从本地浏览器提取 zhipin.com 的 Cookie。
	source: 指定浏览器名（如 "chrome"），None 则自动检测。
	返回 {"cookies": {...}, "user_agent": "", "stoken": ""} 或 None。
	"""
	try:
		import browser_cookie3
	except ImportError:
		return None

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
		return _try_extract(loader)

	# 自动检测：按优先级尝试
	for name, loader in loaders.items():
		result = _try_extract(loader)
		if result:
			print(f"从 {name} 提取到 BOSS 直聘 Cookie", file=sys.stderr)
			return result

	return None


def _try_extract(loader: Callable[..., Any]) -> dict[str, Any] | None:
	"""尝试从单个浏览器提取 zhipin.com cookies"""
	try:
		cj = loader(domain_name=".zhipin.com")
		cookies = {c.name: c.value for c in cj if "zhipin.com" in (c.domain or "")}
		if not cookies or "wt2" not in cookies:
			return None
		return {
			"cookies": cookies,
			"user_agent": "",  # browser-cookie3 无法获取 UA，后续由 httpx 默认 UA 补充
			"stoken": cookies.get("__zp_stoken__", ""),
		}
	except Exception:
		# 故意捕获所有异常：browser_cookie3 在浏览器未安装、profile 路径不可访问、
		# 数据库被锁定等场景会抛库自定义的 BrowserCookieError 等异常，这些不在 stdlib
		# 异常树里。本函数是 best-effort 降级链路的一环，任何失败都应静默返回 None
		# 让上层尝试下一个浏览器/通道，而非中断整条调用链。
		return None
