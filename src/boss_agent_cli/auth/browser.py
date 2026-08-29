import logging
import os
import sys
import time
from typing import Any, cast
from urllib.parse import urlparse

from patchright.sync_api import sync_playwright

LOGIN_PAGE_URL = "https://www.zhipin.com/web/user/"
HOME_URL = "https://www.zhipin.com/"
_DEFAULT_CDP_URL = "http://localhost:9222"
_logger = logging.getLogger("boss_agent_cli.auth.browser")

# 超时常量（秒/毫秒）
_CDP_PROBE_TIMEOUT = 3  # CDP 探测 HTTP 超时（秒）
_NAV_TIMEOUT_MS = 15000  # 页面导航超时（毫秒）
_NETWORKIDLE_GRACE_MS = 3000  # 首页进入 networkidle 的额外宽限（毫秒）
_POST_LOGIN_WAIT = 3  # 登录成功后等待 cookie 传播（秒）
_STOKEN_GENERATION_WAIT = 2  # stoken 生成等待（秒）
_HOME_NAV_RETRIES = 2  # 首页导航超时后的重试次数（含首次）

_PLATFORM_BROWSER_CONFIG: dict[str, dict[str, str]] = {
	"zhipin": {
		"login_page_url": LOGIN_PAGE_URL,
		"home_url": HOME_URL,
		"cookie_domain": "zhipin.com",
		"success_cookie": "wt2",
	},
	"zhilian": {
		"login_page_url": "https://rd6.zhaopin.com/app/im",
		"home_url": "https://rd6.zhaopin.com/app/im",
		"cookie_domain": "zhaopin.com",
		"success_cookie": "at",
	},
}
_ZHILIAN_HOST = "zhaopin.com"


def _get_platform_config(platform: str) -> dict[str, str]:
	config = _PLATFORM_BROWSER_CONFIG.get(platform)
	if config is None:
		raise ValueError(f"unsupported platform: {platform}")
	return config


def _extract_zhilian_client_id(page: Any) -> str:
	try:
		return cast(
			"str",
			page.evaluate("""
			() => {
				const keys = ["x-zp-client-id", "x_zp_client_id", "clientId"];
				for (const key of keys) {
					const value = window.localStorage.getItem(key) || window.sessionStorage.getItem(key);
					if (value) return value;
				}
				return '';
			}
		"""),
		)
	except Exception:
		return ""


def _is_zhilian_url(url: str) -> bool:
	host = urlparse(url).hostname
	if host is None:
		return False
	host = host.rstrip(".").lower()
	return host == _ZHILIAN_HOST or host.endswith(f".{_ZHILIAN_HOST}")


def _find_zhilian_recruiter_page(pages: list[Any]) -> Any | None:
	for page in pages:
		url = getattr(page, "url", "")
		if _is_zhilian_url(url) and any(path in url for path in ("/app/im", "/app/recommend")):
			return page
	for page in pages:
		if _is_zhilian_url(getattr(page, "url", "")):
			return page
	return None


_ZHIPIN_HOST = "zhipin.com"


def _is_platform_url(url: str, expected_host: str) -> bool:
	"""精确 hostname 校验：只接受该 host 及其子域，拒绝子串陷阱。"""
	host = urlparse(url).hostname
	if host is None:
		return False
	host = host.rstrip(".").lower()
	return host == expected_host or host.endswith(f".{expected_host}")


def _is_zhipin_url(url: str) -> bool:
	return _is_platform_url(url, _ZHIPIN_HOST)


def _find_zhipin_page(pages: list[Any]) -> Any | None:
	for page in pages:
		if _is_zhipin_url(getattr(page, "url", "")):
			return page
	return None


def _is_cookie_domain(domain: str, expected_domain: str) -> bool:
	normalized = domain.lstrip(".").rstrip(".").lower()
	expected = expected_domain.lstrip(".").rstrip(".").lower()
	return normalized == expected or normalized.endswith(f".{expected}")


def _matching_cookies(context: Any, *, cookie_domain: str) -> list[dict[str, Any]]:
	try:
		return [
			cookie
			for cookie in context.cookies()
			if _is_cookie_domain(cookie.get("domain", ""), cookie_domain)
		]
	except Exception:
		return []


def _find_logged_in_context(
	contexts: list[Any], *, cookie_domain: str, success_cookie: str
) -> tuple[Any | None, list[dict[str, Any]]]:
	"""跨所有 browser context 搜索非空成功 cookie，返回 (匹配 context, 其平台 cookie)。"""
	for context in contexts:
		cookies = _matching_cookies(context, cookie_domain=cookie_domain)
		if any(cookie.get("name") == success_cookie and cookie.get("value") for cookie in cookies):
			return context, cookies
	return None, []


def _browser_diag(message: str) -> None:
	"""Diagnostic browser messages: debug by default; stderr only when BOSS_BROWSER_VERBOSE=1."""
	_logger.debug(message)
	if os.environ.get("BOSS_BROWSER_VERBOSE", "").strip() in {"1", "true", "yes", "on"}:
		print(message, file=sys.stderr)


def _safe_user_agent(page: Any) -> str:
	"""获取 UA；仅在页面已确认加载（domcontentloaded 已到达）后调用。

	页面处于未完成导航时，patchright 的 page.evaluate 会因执行上下文
	无法建立而永久挂起（不抛异常），因此绝不能在导航卡住后调用本函数。
	"""
	try:
		return cast("str", page.evaluate("navigator.userAgent"))
	except Exception:
		return ""


def _warm_home_for_runtime(page: Any, home_url: str, *, stage: str) -> bool:
	"""预热首页运行时；返回首页是否成功进入 domcontentloaded。

	networkidle 只尽力等待，不作为必须条件。返回 False 表示首页导航
	超时/失败、页面仍处于未完成导航状态——此时调用方必须避免对其执行
	evaluate（patchright 会因执行上下文无法建立而永久挂起）。

	首页在自动化下偶发进入风控验证页或加载缓慢导致 goto 超时；超时后
	先跳 about:blank 终止卡住的导航，再重试一次。
	"""
	loaded = False
	for attempt in range(_HOME_NAV_RETRIES):
		try:
			page.goto(home_url, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT_MS)
			loaded = True
			break
		except Exception as e:
			_browser_diag(f"[boss] {stage}：首页导航未在预期时间完成（{e}），重试 {attempt + 1}/{_HOME_NAV_RETRIES}...")
			try:
				page.goto("about:blank", wait_until="domcontentloaded", timeout=_NAV_TIMEOUT_MS)
			except Exception:
				pass
	try:
		page.wait_for_load_state("networkidle", timeout=_NETWORKIDLE_GRACE_MS)
	except Exception as e:
		# Expected on zhipin (long-polling). Do not dump to TTY for normal users.
		_browser_diag(f"[boss] {stage}：首页未进入 networkidle（{e}），继续提取凭证")
	return loaded


def probe_cdp(cdp_url: str | None = None) -> str | None:
	"""探测 CDP 是否可用，返回 WebSocket URL 或 None。"""
	import httpx

	base = cdp_url or _DEFAULT_CDP_URL
	try:
		resp = httpx.get(f"{base}/json/version", timeout=_CDP_PROBE_TIMEOUT)
		return cast("str | None", resp.json().get("webSocketDebuggerUrl"))
	except (httpx.HTTPError, ValueError, KeyError):
		return None


def login_via_cdp(*, cdp_url: str | None = None, timeout: int = 120, platform: str = "zhipin") -> dict[str, Any]:
	"""
	通过 CDP 连接用户 Chrome 扫码登录。
	返回 token dict，失败抛异常。
	"""
	config = _get_platform_config(platform)
	login_page_url = config["login_page_url"]
	home_url = config["home_url"]
	cookie_domain = config["cookie_domain"]
	success_cookie = config["success_cookie"]
	ws_url = probe_cdp(cdp_url)
	if not ws_url:
		raise ConnectionError("CDP 不可用，请先运行 boss-chrome 启动带调试端口的 Chrome")

	pw = sync_playwright().start()
	browser = pw.chromium.connect_over_cdp(ws_url)
	# 跨所有 browser context 搜索已有登录态：命中则复用该 context，不导航登录页
	logged_in_ctx, _existing_cookies = _find_logged_in_context(
		list(browser.contexts),
		cookie_domain=cookie_domain,
		success_cookie=success_cookie,
	)
	already_logged_in = logged_in_ctx is not None
	ctx = logged_in_ctx or (browser.contexts[0] if browser.contexts else browser.new_context())
	if already_logged_in and platform == "zhipin":
		page = _find_zhipin_page(ctx.pages)
	elif platform == "zhilian":
		page = _find_zhilian_recruiter_page(ctx.pages)
	else:
		page = None
	created_page = page is None
	if page is None:
		page = ctx.new_page()

	try:
		if already_logged_in:
			print("[boss] 检测到 CDP Chrome 已登录，正在复用现有登录态...", file=sys.stderr)
		else:
			print("[boss] 正在 CDP Chrome 中打开登录页...", file=sys.stderr)
			try:
				page.goto(
					login_page_url,
					wait_until="commit",
					timeout=_NAV_TIMEOUT_MS,
				)
			except Exception:
				pass

			print(f"[boss] 请在 Chrome 中扫码登录，等待中...（超时 {timeout}s）", file=sys.stderr)

			for i in range(timeout):
				time.sleep(1)
				cookies = _matching_cookies(ctx, cookie_domain=cookie_domain)
				if any(c.get("name") == success_cookie and c.get("value") for c in cookies):
					print("[boss] 检测到登录成功！", file=sys.stderr)
					break
				if i > 0 and i % 15 == 0:
					print(f"[boss] 等待中... {i}s", file=sys.stderr)
			else:
				raise TimeoutError(f"CDP 扫码登录超时（{timeout}s）")

		if not already_logged_in and (created_page or platform != "zhilian"):
			home_loaded = _warm_home_for_runtime(page, home_url, stage="登录后回到首页")
		elif already_logged_in and created_page and platform == "zhipin":
			# 复用登录态但无既有平台页签：新建页签回首页（DOM 就绪等待）
			home_loaded = _warm_home_for_runtime(page, home_url, stage="复用登录态回首页")
		else:
			# 复用既有平台页签：不导航，避免打断用户页面
			home_loaded = False

		# 任何导航之后重新读取 cookie，不依赖早期快照
		all_cookies = {c["name"]: c["value"] for c in _matching_cookies(ctx, cookie_domain=cookie_domain)}
		# 在已确认加载的页面上记录 UA，避免导航卡住后 evaluate 永久挂起
		ua = _safe_user_agent(page)
		if platform == "zhipin":
			if created_page:
				# 首页成功加载才对其 evaluate 提取 stoken；否则回退读取 cookie jar
				stoken = _extract_stoken(page) if home_loaded else all_cookies.get("__zp_stoken__", "")
			else:
				# 复用既有页签：优先 cookie jar 的 stoken；缺失时有界检查后才求值页面
				stoken = all_cookies.get("__zp_stoken__", "")
				if not stoken:
					try:
						page.wait_for_load_state("domcontentloaded", timeout=_NAV_TIMEOUT_MS)
					except Exception:
						stoken = ""  # 页面不可用：容忍空 stoken，不挂起
					else:
						stoken = _extract_stoken(page)
		else:
			stoken = ""
		if platform == "zhilian":
			x_zp_client_id = all_cookies.get("x-zp-client-id") or _extract_zhilian_client_id(page)
		else:
			x_zp_client_id = ""

		result: dict[str, Any] = {"cookies": all_cookies, "stoken": stoken, "user_agent": ua}
		if x_zp_client_id:
			result["x_zp_client_id"] = x_zp_client_id
		return result
	finally:
		try:
			if created_page:
				page.close()
		finally:
			pw.stop()


def login_via_browser(*, timeout: int = 120, platform: str = "zhipin") -> dict[str, Any]:
	"""
	使用 patchright（Playwright 兼容 fork）打开登录页。
	双重检测登录成功：监听 API 响应 + 轮询 wt2 cookie。
	"""
	config = _get_platform_config(platform)
	login_page_url = config["login_page_url"]
	home_url = config["home_url"]
	cookie_domain = config["cookie_domain"]
	success_cookie = config["success_cookie"]
	with sync_playwright() as p:
		browser = p.chromium.launch(headless=False)
		context = browser.new_context(
			viewport={"width": 1280, "height": 800},
			locale="zh-CN",
			timezone_id="Asia/Shanghai",
		)
		page = context.new_page()

		page.goto(login_page_url, wait_until="domcontentloaded")
		print("已打开 BOSS 直聘登录页。", file=sys.stderr)
		print(f"请扫码或手机号登录（超时 {timeout} 秒）...", file=sys.stderr)

		# 双重检测：API 响应 或 wt2 cookie 出现，任一触发即认为登录成功
		login_detected = False

		def _on_response(response: Any) -> None:
			nonlocal login_detected
			url = response.url
			if (
				url.startswith("https://www.zhipin.com/wapi/zppassport/qrcode/loginConfirm")
				or url.startswith("https://www.zhipin.com/wapi/zppassport/qrcode/dispatcher")
				or url.startswith("https://www.zhipin.com/wapi/zppassport/login/phoneV2")
			):
				login_detected = True

		page.on("response", _on_response)

		deadline = time.time() + timeout
		while time.time() < deadline and not login_detected:
			# 也通过 cookie 检测（覆盖 API 匹配不上的情况）
			try:
				cookies_list = context.cookies()
				if any(c["name"] == success_cookie and cookie_domain in c.get("domain", "") for c in cookies_list):
					login_detected = True
					break
			except Exception:
				pass
			time.sleep(1)

		if not login_detected:
			browser.close()
			raise TimeoutError(f"扫码登录超时（{timeout}秒）")

		print("检测到登录成功，正在提取凭证...", file=sys.stderr)
		time.sleep(_POST_LOGIN_WAIT)

		# UA 在已加载的登录页上记录（此时 evaluate 安全）；之后再预热首页
		user_agent = _safe_user_agent(page)

		# 跳转主站提取完整 cookies 和 stoken
		home_loaded = _warm_home_for_runtime(page, home_url, stage="登录后回到首页")

		cookies_list = context.cookies()
		cookies = {c["name"]: c["value"] for c in cookies_list if cookie_domain in c.get("domain", "")}
		if platform == "zhipin":
			# 首页成功加载才对其 evaluate 提取 stoken；否则回退到 cookie jar，
			# 避免页面卡在导航中导致 page.evaluate 永久挂起
			stoken = _extract_stoken(page) if home_loaded else cookies.get("__zp_stoken__", "")
		else:
			stoken = ""
		if platform == "zhilian":
			x_zp_client_id = cookies.get("x-zp-client-id") or (_extract_zhilian_client_id(page) if home_loaded else "")
		else:
			x_zp_client_id = ""

		browser.close()

	result: dict[str, Any] = {
		"cookies": cookies,
		"stoken": stoken,
		"user_agent": user_agent,
	}
	if x_zp_client_id:
		result["x_zp_client_id"] = x_zp_client_id
	return result


def refresh_stoken_via_cdp(cdp_url: str | None = None) -> str:
	"""通过 CDP Chrome 刷新 stoken（指纹一致，不会被拒）。"""
	ws_url = probe_cdp(cdp_url)
	if not ws_url:
		raise ConnectionError("CDP 不可用")

	pw = sync_playwright().start()
	browser = pw.chromium.connect_over_cdp(ws_url)
	ctx = browser.contexts[0] if browser.contexts else browser.new_context()
	page = ctx.new_page()

	home_loaded = _warm_home_for_runtime(page, HOME_URL, stage="刷新 stoken")
	time.sleep(_STOKEN_GENERATION_WAIT)

	# 页面未成功加载时跳过 evaluate，避免 patchright 永久挂起
	stoken = _extract_stoken(page) if home_loaded else ""
	if not stoken:
		# 安全验证跳转期间 goto 可能未触发 domcontentloaded，但 cookie jar 已生成 stoken
		for cookie in ctx.cookies():
			if cookie.get("name") == "__zp_stoken__":
				stoken = cookie.get("value", "")
				break
	page.close()
	pw.stop()

	if not stoken:
		raise RuntimeError("CDP 刷新 stoken 失败：页面未生成 stoken")
	return stoken


def refresh_stoken(cookies: dict[str, Any], user_agent: str) -> str:
	"""通过 headless patchright 刷新 stoken（兜底方案）。"""
	with sync_playwright() as p:
		browser = p.chromium.launch(headless=True)
		context = browser.new_context(user_agent=user_agent)
		context.add_cookies(
			[{"name": name, "value": value, "domain": ".zhipin.com", "path": "/"} for name, value in cookies.items()]
		)
		page = context.new_page()
		home_loaded = _warm_home_for_runtime(page, HOME_URL, stage="刷新 stoken")
		stoken = _extract_stoken(page) if home_loaded else ""
		if not stoken:
			# 安全验证跳转期间 goto 可能未触发 domcontentloaded，但 cookie jar 已生成 stoken
			for cookie in context.cookies():
				if cookie.get("name") == "__zp_stoken__":
					stoken = cookie.get("value", "")
					break
		browser.close()

	return stoken


def _extract_stoken(page: Any) -> str:
	try:
		stoken = page.evaluate("""
			() => {
				const match = document.cookie.match(/__zp_stoken__=([^;]+)/);
				return match ? match[1] : '';
			}
		""")
		if not stoken:
			stoken = page.evaluate("() => window.__zp_stoken__ || ''")
		return cast("str", stoken)
	except Exception:
		return ""
