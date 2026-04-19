"""cookie_extract.py 回归测试 — 守护 PR #74 的 broad except 不被未来误改回去。"""

from unittest.mock import MagicMock, patch

from boss_agent_cli.auth.cookie_extract import _try_extract, extract_cookies


def _fake_cookie(name: str, value: str, domain: str = ".zhipin.com"):
	c = MagicMock()
	c.name = name
	c.value = value
	c.domain = domain
	return c


# ── _try_extract 异常路径 ────────────────────────────────────────


def test_try_extract_returns_none_on_custom_browser_error():
	"""browser_cookie3 自定义异常（如 BrowserCookieError）应被静默吃掉返回 None。

	Regression for PR #74: 之前 except 列表只覆盖 OSError/KeyError/ValueError/PermissionError，
	未安装 Edge 的 Linux 上 browser_cookie3 抛 BrowserCookieError 时整条调用链会崩溃。
	"""

	class BrowserCookieError(Exception):
		"""模拟 browser_cookie3 自定义异常，故意不继承 stdlib 异常类。"""
		pass

	def broken_loader(domain_name=None):
		raise BrowserCookieError("browser not installed")

	# 不应抛异常
	result = _try_extract(broken_loader)
	assert result is None


def test_try_extract_returns_none_on_runtime_error():
	def broken_loader(domain_name=None):
		raise RuntimeError("profile locked")

	assert _try_extract(broken_loader) is None


def test_try_extract_returns_none_on_oserror():
	"""保底覆盖原有 OSError 分支不被破坏。"""

	def broken_loader(domain_name=None):
		raise OSError("permission denied")

	assert _try_extract(broken_loader) is None


def test_try_extract_returns_none_when_no_wt2():
	"""提取到 cookies 但缺关键 wt2 应返回 None。"""

	def loader(domain_name=None):
		return [_fake_cookie("__cf_bm", "x")]  # 没有 wt2

	assert _try_extract(loader) is None


def test_try_extract_returns_none_when_empty():
	def loader(domain_name=None):
		return []

	assert _try_extract(loader) is None


def test_try_extract_success_returns_token_dict():
	def loader(domain_name=None):
		return [
			_fake_cookie("wt2", "wt2-value"),
			_fake_cookie("__zp_stoken__", "stoken-value"),
			_fake_cookie("foreign", "x", domain=".other.com"),  # 应被过滤
		]

	result = _try_extract(loader)
	assert result is not None
	assert result["cookies"]["wt2"] == "wt2-value"
	assert result["stoken"] == "stoken-value"
	assert "foreign" not in result["cookies"]
	assert result["user_agent"] == ""


def test_try_extract_success_without_stoken_returns_empty_string():
	def loader(domain_name=None):
		return [_fake_cookie("wt2", "v")]

	result = _try_extract(loader)
	assert result["stoken"] == ""


# ── extract_cookies 自动检测降级链 ───────────────────────────────


def test_extract_cookies_auto_detect_skips_failed_browsers():
	"""自动检测模式遇到崩溃浏览器应跳过继续下一个，不中断。

	这是 PR #74 修复的核心场景：在未安装 Edge 的 Linux 上，
	浏览器自动检测顺序里 Edge 抛 BrowserCookieError，应被吃掉继续尝试下一个。
	"""

	class BrowserCookieError(Exception):
		pass

	def chrome_loader(domain_name=None):
		raise BrowserCookieError("chrome not installed")

	def firefox_loader(domain_name=None):
		raise BrowserCookieError("firefox not installed")

	def edge_loader(domain_name=None):
		# 这个也崩
		raise BrowserCookieError("edge not installed")

	def brave_loader(domain_name=None):
		# 这个有 cookies！
		return [_fake_cookie("wt2", "from-brave"), _fake_cookie("__zp_stoken__", "s")]

	fake_module = MagicMock()
	fake_module.chrome = chrome_loader
	fake_module.firefox = firefox_loader
	fake_module.edge = edge_loader
	fake_module.brave = brave_loader
	fake_module.opera = chrome_loader  # 也崩
	fake_module.chromium = chrome_loader

	with patch.dict("sys.modules", {"browser_cookie3": fake_module}):
		result = extract_cookies()

	assert result is not None
	assert result["cookies"]["wt2"] == "from-brave"


def test_extract_cookies_returns_none_when_all_browsers_fail():
	"""所有浏览器都不可用时应返回 None 而非抛异常。"""

	class BrowserCookieError(Exception):
		pass

	def broken(domain_name=None):
		raise BrowserCookieError("not installed")

	fake_module = MagicMock()
	for name in ("chrome", "firefox", "edge", "brave", "opera", "chromium"):
		setattr(fake_module, name, broken)

	with patch.dict("sys.modules", {"browser_cookie3": fake_module}):
		result = extract_cookies()

	assert result is None


def test_extract_cookies_explicit_unsupported_browser_returns_none():
	fake_module = MagicMock()
	fake_module.chrome = lambda domain_name=None: []

	with patch.dict("sys.modules", {"browser_cookie3": fake_module}):
		result = extract_cookies(source="netscape-navigator")

	assert result is None


def test_extract_cookies_explicit_browser_with_success():
	def chrome_loader(domain_name=None):
		return [_fake_cookie("wt2", "specific-chrome"), _fake_cookie("__zp_stoken__", "")]

	fake_module = MagicMock()
	fake_module.chrome = chrome_loader

	with patch.dict("sys.modules", {"browser_cookie3": fake_module}):
		result = extract_cookies(source="chrome")

	assert result["cookies"]["wt2"] == "specific-chrome"


def test_extract_cookies_returns_none_when_browser_cookie3_not_installed():
	"""browser_cookie3 包本身没装时应优雅返回 None。"""
	import sys as _sys

	# 临时禁用 browser_cookie3 import
	saved = _sys.modules.pop("browser_cookie3", None)
	try:
		with patch.dict("sys.modules", {"browser_cookie3": None}):
			result = extract_cookies()
		assert result is None
	finally:
		if saved is not None:
			_sys.modules["browser_cookie3"] = saved
