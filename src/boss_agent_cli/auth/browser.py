import sys
import time

from patchright.sync_api import sync_playwright

LOGIN_PAGE_URL = "https://www.zhipin.com/web/user/"
HOME_URL = "https://www.zhipin.com/"
_DEFAULT_CDP_URL = "http://localhost:9222"

# 超时常量（秒/毫秒）
_CDP_PROBE_TIMEOUT = 3           # CDP 探测 HTTP 超时（秒）
_NAV_TIMEOUT_MS = 15000          # 页面导航超时（毫秒）
_POST_LOGIN_WAIT = 3             # 登录成功后等待 cookie 传播（秒）
_STOKEN_GENERATION_WAIT = 2      # stoken 生成等待（秒）


def probe_cdp(cdp_url: str | None = None) -> str | None:
	"""探测 CDP 是否可用，返回 WebSocket URL 或 None。"""
	import httpx
	base = cdp_url or _DEFAULT_CDP_URL
	try:
		resp = httpx.get(f"{base}/json/version", timeout=_CDP_PROBE_TIMEOUT)
		return resp.json().get("webSocketDebuggerUrl")
	except Exception:
		return None


def login_via_cdp(*, cdp_url: str | None = None, timeout: int = 120) -> dict:
	"""
	通过 CDP 连接用户 Chrome 扫码登录。
	返回 token dict，失败抛异常。
	"""
	ws_url = probe_cdp(cdp_url)
	if not ws_url:
		raise ConnectionError("CDP 不可用，请先运行 boss-chrome 启动带调试端口的 Chrome")

	print("[boss] 正在 CDP Chrome 中打开登录页...", file=sys.stderr)
	pw = sync_playwright().start()
	browser = pw.chromium.connect_over_cdp(ws_url)
	ctx = browser.contexts[0] if browser.contexts else browser.new_context()
	page = ctx.new_page()

	try:
		page.goto(
			f"{LOGIN_PAGE_URL}?ka=header-login",
			wait_until="commit", timeout=_NAV_TIMEOUT_MS,
		)
	except Exception:
		pass

	print(f"[boss] 请在 Chrome 中扫码登录，等待中...（超时 {timeout}s）", file=sys.stderr)

	for i in range(timeout):
		time.sleep(1)
		cookies = ctx.cookies()
		wt2 = [c for c in cookies if c["name"] == "wt2" and "zhipin" in c.get("domain", "")]
		if wt2:
			print("[boss] 检测到登录成功！", file=sys.stderr)
			break
		if i > 0 and i % 15 == 0:
			print(f"[boss] 等待中... {i}s", file=sys.stderr)
	else:
		page.close()
		pw.stop()
		raise TimeoutError(f"CDP 扫码登录超时（{timeout}s）")

	all_cookies = {c["name"]: c["value"] for c in ctx.cookies() if "zhipin" in c.get("domain", "")}
	ua = page.evaluate("navigator.userAgent")

	page.close()
	pw.stop()

	return {"cookies": all_cookies, "stoken": "", "user_agent": ua}


def login_via_browser(*, timeout: int = 120) -> dict:
	"""
	使用 patchright（Playwright 反检测 fork）打开登录页。
	双重检测登录成功：监听 API 响应 + 轮询 wt2 cookie。
	"""
	with sync_playwright() as p:
		browser = p.chromium.launch(headless=False)
		context = browser.new_context(
			viewport={"width": 1280, "height": 800},
			locale="zh-CN",
			timezone_id="Asia/Shanghai",
		)
		page = context.new_page()

		page.goto(LOGIN_PAGE_URL, wait_until="domcontentloaded")
		print("已打开 BOSS 直聘登录页。", file=sys.stderr)
		print(f"请扫码或手机号登录（超时 {timeout} 秒）...", file=sys.stderr)

		# 双重检测：API 响应 或 wt2 cookie 出现，任一触发即认为登录成功
		login_detected = False

		def _on_response(response):
			nonlocal login_detected
			url = response.url
			if (url.startswith("https://www.zhipin.com/wapi/zppassport/qrcode/loginConfirm")
				or url.startswith("https://www.zhipin.com/wapi/zppassport/qrcode/dispatcher")
				or url.startswith("https://www.zhipin.com/wapi/zppassport/login/phoneV2")):
				login_detected = True

		page.on("response", _on_response)

		deadline = time.time() + timeout
		while time.time() < deadline and not login_detected:
			# 也通过 cookie 检测（覆盖 API 匹配不上的情况）
			try:
				cookies_list = context.cookies()
				if any(c["name"] == "wt2" for c in cookies_list):
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

		# 跳转主站提取完整 cookies 和 stoken
		page.goto(HOME_URL, wait_until="domcontentloaded")
		page.wait_for_load_state("networkidle")

		cookies_list = context.cookies()
		cookies = {c["name"]: c["value"] for c in cookies_list}
		user_agent = page.evaluate("navigator.userAgent")
		stoken = _extract_stoken(page)

		browser.close()

	return {
		"cookies": cookies,
		"stoken": stoken,
		"user_agent": user_agent,
	}


def refresh_stoken_via_cdp(cdp_url: str | None = None) -> str:
	"""通过 CDP Chrome 刷新 stoken（指纹一致，不会被拒）。"""
	ws_url = probe_cdp(cdp_url)
	if not ws_url:
		raise ConnectionError("CDP 不可用")

	pw = sync_playwright().start()
	browser = pw.chromium.connect_over_cdp(ws_url)
	ctx = browser.contexts[0] if browser.contexts else browser.new_context()
	page = ctx.new_page()

	try:
		page.goto(HOME_URL, wait_until="commit", timeout=15000)
	except Exception:
		pass
	time.sleep(_STOKEN_GENERATION_WAIT)

	stoken = _extract_stoken(page)
	page.close()
	pw.stop()

	if not stoken:
		raise RuntimeError("CDP 刷新 stoken 失败：页面未生成 stoken")
	return stoken


def refresh_stoken(cookies: dict, user_agent: str) -> str:
	"""通过 headless patchright 刷新 stoken（兜底方案）。"""
	with sync_playwright() as p:
		browser = p.chromium.launch(headless=True)
		context = browser.new_context(user_agent=user_agent)
		context.add_cookies([
			{"name": name, "value": value, "domain": ".zhipin.com", "path": "/"}
			for name, value in cookies.items()
		])
		page = context.new_page()
		page.goto(HOME_URL)
		page.wait_for_load_state("networkidle")
		stoken = _extract_stoken(page)
		browser.close()

	return stoken


def _extract_stoken(page) -> str:
	try:
		stoken = page.evaluate("""
			() => {
				const match = document.cookie.match(/__zp_stoken__=([^;]+)/);
				return match ? match[1] : '';
			}
		""")
		if not stoken:
			stoken = page.evaluate("() => window.__zp_stoken__ || ''")
		return stoken
	except Exception:
		return ""
