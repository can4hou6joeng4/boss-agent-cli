"""cookie_extract.py 回归测试 — 守护 PR #74 的 broad except 不被未来误改回去。"""

import fnmatch
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from boss_agent_cli.auth import cookie_extract
from boss_agent_cli.auth.cookie_extract import _try_extract, extract_cookies


@pytest.fixture(autouse=True)
def _no_real_browser_profiles(monkeypatch, tmp_path):
	"""钥匙串预检会在 macOS 上读真实的 ~/Library/Application Support；
	统一指向空目录（判断不了 → 照旧交给 loader），用例只验证假 loader。"""
	monkeypatch.setattr(cookie_extract, "_macos_app_support_dir", lambda: tmp_path / "no-browser-profiles")


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


def test_try_extract_supports_custom_domain_without_stoken_cookie():
	def loader(domain_name=None):
		return [_fake_cookie("example_token", "example-value", domain=".example.com")]

	result = _try_extract(
		loader,
		domain_name=".example.com",
		required_cookie="example_token",
		stoken_cookie="",
	)
	assert result is not None
	assert result["cookies"]["example_token"] == "example-value"
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


def test_extract_cookies_rejects_removed_zhilian_platform():
	def chrome_loader(domain_name=None):
		return [_fake_cookie("wt2", "should-not-be-read", domain=".zhipin.com")]

	fake_module = MagicMock()
	fake_module.chrome = chrome_loader

	with patch.dict("sys.modules", {"browser_cookie3": fake_module}):
		assert extract_cookies(source="chrome", platform="zhilian") is None


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


# ── 2026-10 扩展浏览器集合（对齐 boss-cli 的 10+ 覆盖） ─────────


def test_extract_cookies_supports_extended_browser_set():
	"""vivaldi / arc / librewolf / safari 均可显式选择，大小写不敏感。"""

	def make_loader(value):
		def loader(domain_name=None):
			return [_fake_cookie("wt2", value), _fake_cookie("__zp_stoken__", "s")]

		return loader

	fake_module = MagicMock()
	for attr, value in (
		("vivaldi", "from-vivaldi"),
		("arc", "from-arc"),
		("librewolf", "from-librewolf"),
		("safari", "from-safari"),
	):
		setattr(fake_module, attr, make_loader(value))

	with patch.dict("sys.modules", {"browser_cookie3": fake_module}):
		for source, expected in (
			("vivaldi", "from-vivaldi"),
			("Arc", "from-arc"),
			("LibreWolf", "from-librewolf"),
			("Safari", "from-safari"),
		):
			result = extract_cookies(source=source)
			assert result is not None, source
			assert result["cookies"]["wt2"] == expected


# ── 旧版 browser_cookie3 缺 loader（arc 自 0.20.0 起，librewolf 自 0.19.0 起） ──


def _old_browser_cookie3(**loaders):
	"""只带指定属性的假模块；MagicMock 会凭空造出任何属性，模拟不了旧版缺函数。"""
	return SimpleNamespace(**loaders)


def test_extract_cookies_auto_detect_skips_loaders_missing_from_old_browser_cookie3():
	"""0.19.x 没有 arc / librewolf：构造 loader 表不能抛 AttributeError，
	自动检测越过缺的浏览器，继续找到排在它们后面的 safari。"""

	def no_cookie(domain_name=None):
		return []

	def safari_loader(domain_name=None):
		return [_fake_cookie("wt2", "from-safari"), _fake_cookie("__zp_stoken__", "s")]

	old_module = _old_browser_cookie3(
		chrome=no_cookie,
		firefox=no_cookie,
		edge=no_cookie,
		brave=no_cookie,
		opera=no_cookie,
		chromium=no_cookie,
		vivaldi=no_cookie,
		safari=safari_loader,
	)

	with patch.dict("sys.modules", {"browser_cookie3": old_module}):
		result = extract_cookies()

	assert result is not None
	assert result["cookies"]["wt2"] == "from-safari"


def test_extract_cookies_explicit_loader_missing_from_old_browser_cookie3(capsys):
	"""显式指定旧版没有的浏览器：返回 None 并提示升级，而不是报「不支持的浏览器」。"""
	old_module = _old_browser_cookie3(chrome=lambda domain_name=None: [])

	with patch.dict("sys.modules", {"browser_cookie3": old_module}):
		assert extract_cookies(source="Arc") is None

	err = capsys.readouterr().err
	assert "升级 browser-cookie3" in err
	assert "不支持的浏览器" not in err


def test_extract_cookies_unknown_browser_lists_full_supported_set(capsys):
	old_module = _old_browser_cookie3(chrome=lambda domain_name=None: [])

	with patch.dict("sys.modules", {"browser_cookie3": old_module}):
		assert extract_cookies(source="netscape-navigator") is None

	err = capsys.readouterr().err
	assert "不支持的浏览器: netscape-navigator" in err
	assert "arc" in err and "safari" in err


# ── macOS 钥匙串预检：Cookie 库里确认没有目标 Cookie 时不调 loader ──────


def _write_cookie_db(path: Path, rows: list[tuple[str, str]]) -> Path:
	"""按 Chromium Cookie 库的列名建一个最小库；预检只读 host_key / name。"""
	path.parent.mkdir(parents=True, exist_ok=True)
	con = sqlite3.connect(path)
	con.execute("CREATE TABLE cookies (host_key TEXT, name TEXT, value TEXT, encrypted_value BLOB)")
	con.executemany(
		"INSERT INTO cookies VALUES (?, ?, '', x'7631300000')",
		rows,
	)
	con.commit()
	con.close()
	return path


@pytest.fixture
def macos_profiles(monkeypatch, tmp_path) -> Path:
	support = tmp_path / "Application Support"
	support.mkdir()
	monkeypatch.setattr(cookie_extract, "_is_macos", lambda: True)
	monkeypatch.setattr(cookie_extract, "_macos_app_support_dir", lambda: support)
	return support


def _recording_module(**loaders):
	"""loader 被调用即记名；被调用意味着真实环境会去读钥匙串。"""
	calls: list[str] = []

	def wrap(name, fn):
		def loader(domain_name=None):
			calls.append(name)
			return fn(domain_name)

		return loader

	module = SimpleNamespace(**{name: wrap(name, fn) for name, fn in loaders.items()})
	return module, calls


def _with_wt2(value):
	return lambda domain_name: [_fake_cookie("wt2", value), _fake_cookie("__zp_stoken__", "s")]


def _no_cookies(domain_name):
	return []


def test_macos_auto_detect_skips_chromium_browser_whose_db_lacks_login_cookie(macos_profiles, capsys):
	_write_cookie_db(
		macos_profiles / "Google/Chrome/Default/Cookies",
		[(".zhipin.com", "__zp_stoken__"), (".example.com", "wt2")],
	)
	module, calls = _recording_module(chrome=_with_wt2("must-not-load"), firefox=_with_wt2("from-firefox"))

	with patch.dict("sys.modules", {"browser_cookie3": module}):
		result = extract_cookies()

	assert result["cookies"]["wt2"] == "from-firefox"
	assert calls == ["firefox"]


def test_macos_calls_loader_when_any_profile_has_login_cookie(macos_profiles):
	"""Default 没有、Profile 1 有：判定为「有」，仍交给 loader 按它自己的规则读。"""
	_write_cookie_db(macos_profiles / "Google/Chrome/Default/Cookies", [(".example.com", "sid")])
	_write_cookie_db(macos_profiles / "Google/Chrome/Profile 1/Cookies", [("www.zhipin.com", "wt2")])
	module, calls = _recording_module(chrome=_with_wt2("from-chrome"))

	with patch.dict("sys.modules", {"browser_cookie3": module}):
		result = extract_cookies(source="chrome")

	assert result["cookies"]["wt2"] == "from-chrome"
	assert calls == ["chrome"]


def test_macos_recognizes_network_subdirectory_cookie_db(macos_profiles):
	_write_cookie_db(macos_profiles / "Microsoft Edge/Default/Network/Cookies", [(".zhipin.com", "wt2")])

	presence = cookie_extract._chromium_cookie_presence("edge", domain_name=".zhipin.com", cookie_name="wt2")

	assert presence is True


def test_macos_explicit_source_without_login_cookie_skips_keychain(macos_profiles, capsys):
	_write_cookie_db(macos_profiles / "Arc/User Data/Default/Cookies", [(".example.com", "wt2")])
	module, calls = _recording_module(arc=_with_wt2("must-not-load"))

	with patch.dict("sys.modules", {"browser_cookie3": module}):
		assert extract_cookies(source="Arc") is None

	assert calls == []
	assert "Arc 的 Cookie 库中没有 zhipin.com 的登录记录，未读取钥匙串" in capsys.readouterr().err


def test_macos_reports_skipped_browsers_when_nothing_found(macos_profiles, capsys):
	_write_cookie_db(macos_profiles / "Google/Chrome/Default/Cookies", [])
	_write_cookie_db(macos_profiles / "Microsoft Edge/Profile 2/Cookies", [(".zhipin.com", "lastCity")])
	module, calls = _recording_module(chrome=_no_cookies, firefox=_no_cookies, edge=_no_cookies)

	with patch.dict("sys.modules", {"browser_cookie3": module}):
		assert extract_cookies() is None

	assert calls == ["firefox"]
	assert "chrome, edge 的 Cookie 库中没有 zhipin.com 的登录记录，已跳过钥匙串读取" in capsys.readouterr().err


@pytest.mark.parametrize("layout", ["missing", "corrupt", "no_cookies_table"])
def test_macos_falls_back_to_loader_when_presence_is_undetermined(macos_profiles, layout):
	"""找不到库、库损坏、表不存在都判断不了，照旧调 loader，不能因预检丢掉可用的 Cookie。"""
	db = macos_profiles / "Google/Chrome/Default/Cookies"
	if layout == "corrupt":
		db.parent.mkdir(parents=True)
		db.write_bytes(b"not a sqlite database")
	elif layout == "no_cookies_table":
		db.parent.mkdir(parents=True)
		sqlite3.connect(db).close()
	module, calls = _recording_module(chrome=_with_wt2("from-chrome"))

	with patch.dict("sys.modules", {"browser_cookie3": module}):
		result = extract_cookies(source="chrome")

	assert result["cookies"]["wt2"] == "from-chrome"
	assert calls == ["chrome"]


def test_macos_unreadable_db_keeps_presence_undetermined_even_if_another_lacks_cookie(macos_profiles):
	_write_cookie_db(macos_profiles / "Google/Chrome/Default/Cookies", [(".example.com", "wt2")])
	broken = macos_profiles / "Google/Chrome/Profile 1/Cookies"
	broken.parent.mkdir(parents=True)
	broken.write_bytes(b"garbage")

	presence = cookie_extract._chromium_cookie_presence("chrome", domain_name=".zhipin.com", cookie_name="wt2")

	assert presence is None


def test_precheck_ignores_non_chromium_browsers(macos_profiles):
	for browser in ("firefox", "librewolf", "safari"):
		assert cookie_extract._chromium_cookie_presence(browser, domain_name=".zhipin.com", cookie_name="wt2") is None


def test_precheck_is_inactive_outside_macos(monkeypatch, tmp_path):
	support = tmp_path / "Application Support"
	_write_cookie_db(support / "Google/Chrome/Default/Cookies", [(".example.com", "wt2")])
	monkeypatch.setattr(cookie_extract, "_is_macos", lambda: False)
	monkeypatch.setattr(cookie_extract, "_macos_app_support_dir", lambda: support)
	module, calls = _recording_module(chrome=_with_wt2("from-chrome"))

	with patch.dict("sys.modules", {"browser_cookie3": module}):
		result = extract_cookies(source="chrome")

	assert result["cookies"]["wt2"] == "from-chrome"
	assert calls == ["chrome"]


# ── 数据根目录必须覆盖 browser_cookie3 实际读取的 macOS 路径 ──────────


class _CapturedPaths(Exception):
	pass


def _chromium_loader_classes() -> dict[str, type]:
	import browser_cookie3

	classes = {}
	for name in cookie_extract._BROWSER_LOADER_NAMES:
		cls = getattr(browser_cookie3, name.capitalize(), None)
		if isinstance(cls, type) and issubclass(cls, browser_cookie3.ChromiumBased):
			classes[name] = cls
	return classes


def _library_osx_cookie_patterns(monkeypatch, cls: type) -> list[str]:
	"""让 browser_cookie3 走 darwin 分支，在解析 Cookie 路径的那一刻截下它的候选列表。"""
	import browser_cookie3

	captured: list[str] = []

	def capture(paths, os_name):
		captured.extend(paths if isinstance(paths, list) else [paths])
		raise _CapturedPaths

	with monkeypatch.context() as m:
		m.setattr(sys, "platform", "darwin")
		m.setattr(browser_cookie3, "_get_osx_keychain_password", lambda service, user: b"stub")
		m.setattr(browser_cookie3, "_expand_paths", capture)
		with pytest.raises(_CapturedPaths):
			cls()
	return captured


def _segments_match(pattern: str, path: str) -> bool:
	pattern_parts, path_parts = pattern.split("/"), path.split("/")
	return len(pattern_parts) == len(path_parts) and all(
		fnmatch.fnmatchcase(part, pat) for part, pat in zip(path_parts, pattern_parts)
	)


def test_macos_data_roots_cover_every_chromium_loader():
	assert set(_chromium_loader_classes()) == set(cookie_extract._MACOS_CHROMIUM_DATA_ROOTS)


def test_macos_data_roots_cover_browser_cookie3_osx_paths(monkeypatch):
	prefix = "~/Library/Application Support/"
	for name, cls in _chromium_loader_classes().items():
		patterns = _library_osx_cookie_patterns(monkeypatch, cls)
		assert patterns, name
		for pattern in patterns:
			assert pattern.startswith(prefix), (name, pattern)
			relative = pattern[len(prefix):]
			covered = [
				root
				for root in cookie_extract._MACOS_CHROMIUM_DATA_ROOTS[name]
				if relative.startswith(root + "/")
				and any(
					_segments_match(db_glob, relative[len(root) + 1:])
					for db_glob in cookie_extract._CHROMIUM_COOKIE_DB_GLOBS
				)
			]
			assert covered, (name, pattern)
