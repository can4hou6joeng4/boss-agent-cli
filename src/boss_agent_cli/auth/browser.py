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
	return _is_platform_url(url, _ZHILIAN_HOST)


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


def _ensure_page_evaluable(page: Any) -> bool:
	"""有界确认页面可对 ``page.evaluate``（停在未完成导航时 evaluate 会永久挂起，#390 规则）。

	返回 True 表示已就绪（可安全 evaluate）；False 表示有界等待超时，调用方须容忍并跳过
	页面提取。统一三处「有界 wait_for_load_state → 就绪门禁」的重复逻辑。
	"""
	try:
		page.wait_for_load_state("domcontentloaded", timeout=_NAV_TIMEOUT_MS)
	except Exception:
		return False
	return True


class ReusedSessionStaleError(RuntimeError):
	"""复用的 CDP 登录态在服务端已失效：cookie 还在，但只读探测返回未登录。

	由 ``AuthManager.login`` 捕获后改走 ``reuse_existing=False``（即 ``--force`` 路径）
	重新登录，不把死凭证落盘（Issue #424 第二步）。
	"""


_PROBE_TIMEOUT_MS = 10000  # 复用登录态只读探测的 fetch 超时（毫秒），JS 侧 AbortController 兜底

_PROBE_SCRIPT = """
async ({url, timeoutMs}) => {
	const controller = new AbortController();
	const timer = setTimeout(() => controller.abort(), timeoutMs);
	try {
		const resp = await fetch(url, {
			method: 'GET',
			credentials: 'include',
			signal: controller.signal,
			headers: {
				'Accept': 'application/json, text/plain, */*',
				'X-Requested-With': 'XMLHttpRequest',
			},
		});
		return await resp.json();
	} catch (e) {
		return {code: -1, message: String((e && e.message) || e)};
	} finally {
		clearTimeout(timer);
	}
}
"""


def _probe_reused_session(page: Any, *, platform: str) -> str:
	"""在已就绪的页面里用同一浏览器会话做一次只读探测，判断复用的登录态是否仍有效。

	返回 ``"valid"`` / ``"stale"`` / ``"unverified"``；命中风控（code 36 / 环境类 code 37）
	直接抛 ``AccountRiskError`` / ``EnvironmentRiskError``，由调用链按风控契约立即终止。

	- 探测经页面自身的 ``fetch``（``credentials: include``）发出：用的是浏览器里的会话，
	  不读本地凭据，与 ``existing-browser`` 来源「不读本地凭据」的约束一致。
	- 只探测 zhipin：智联 ``getUserInfo`` 需要 ``x-zp-client-id`` 且成功码语义未在本地
	  核实，暂按 ``unverified`` 处理（Issue #424 登记）。
	- fetch 自身失败（网络 / 超时，``code == -1``）视为 ``unverified``：无法证明失效就不
	  阻断登录，但会在 stderr 提示；真失效的兜底是 ``boss login --force``。
	"""
	if platform != "zhipin":
		return "unverified"
	from boss_agent_cli.api import endpoints
	from boss_agent_cli.api.client import AccountRiskError, EnvironmentRiskError
	from boss_agent_cli.api.zhipin_errors import classify_code_37, response_message

	try:
		data = page.evaluate(_PROBE_SCRIPT, {"url": endpoints.USER_INFO_URL, "timeoutMs": _PROBE_TIMEOUT_MS})
	except Exception as exc:  # evaluate 本身失败（页面被关等）：无法验证，不阻断
		_logger.debug("复用登录态探测 evaluate 失败: %s", exc)
		return "unverified"
	if not isinstance(data, dict):
		return "unverified"
	code = data.get("code")
	if code == endpoints.CODE_SUCCESS:
		return "valid"
	if code == -1:
		return "unverified"
	if code == endpoints.CODE_ACCOUNT_RISK:
		raise AccountRiskError(f"BOSS 直聘风控拦截 (code 36): {response_message(data)}", is_cdp=True)
	if code == endpoints.CODE_STOKEN_EXPIRED:
		if classify_code_37(data) == "environment_risk":
			raise EnvironmentRiskError(f"BOSS 直聘访问环境风控 (code 37): {response_message(data)}", is_cdp=True)
		return "stale"
	return "stale"


def _clear_platform_cookies(context: Any, *, cookie_domain: str) -> None:
	"""只清除该 context 内目标平台域（含子域）的 cookie，其他站点 cookie 原样保留。

	``clear_cookies(domain=...)`` 的 domain 参数按精确值匹配，而登录态 cookie 的 domain
	既可能是 ``.zhipin.com`` 也可能是 ``www.zhipin.com``，所以先按 ``_is_cookie_domain``
	枚举出实际存在的 domain 值再逐一清除，不用正则（避免误伤 ``evil-zhipin.com``）。
	"""
	domains = {
		str(cookie.get("domain", ""))
		for cookie in context.cookies()
		if _is_cookie_domain(str(cookie.get("domain", "")), cookie_domain)
	}
	for domain in sorted(domains):
		context.clear_cookies(domain=domain)


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
) -> tuple[Any | None, int, str]:
	"""跨所有 browser context 搜索非空成功 cookie。

	返回 ``(匹配的 context, 其序号 index, 成功 cookie 的不可逆指纹)``；未命中返回
	``(None, -1, "")``。指纹用于多 context 账号歧义时让用户区分选中的是哪个账号
	（见 ``_account_fingerprint``）。cookie 一律由调用方在导航后重读，不在此处返回
	早期快照，避免调用方误用过期值。
	"""
	for index, context in enumerate(contexts):
		cookies = _matching_cookies(context, cookie_domain=cookie_domain)
		for cookie in cookies:
			if cookie.get("name") == success_cookie and cookie.get("value"):
				return context, index, _account_fingerprint(cookie["value"])
	return None, -1, ""


def _account_fingerprint(cookie_value: str) -> str:
	"""对登录态 cookie 值做不可逆指纹（SHA-256 前 8 位十六进制），用于账号区分。

	zhipin 的 ``wt2`` 等成功 cookie 是加密 token，没有可直接展示的明文账号字段；
	但同一账号的 cookie 值稳定、不同账号不同，故用哈希前缀作为「账号指纹」——
	既能让用户在多 context 下区分选中的是不是目标账号，又不泄露 cookie 本身。
	"""
	import hashlib

	return hashlib.sha256(cookie_value.encode("utf-8")).hexdigest()[:8]


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


def login_via_cdp(
	*,
	cdp_url: str | None = None,
	timeout: int = 120,
	platform: str = "zhipin",
	reuse_existing: bool = True,
) -> dict[str, Any]:
	"""
	通过 CDP 连接用户 Chrome 扫码登录。
	返回 token dict，失败抛异常。

	Args:
		reuse_existing: 为 False 时（``boss login --force``）不扫描、不复用任何已登录
			context，并在扫码前清掉第一个 context 内目标平台域的 cookie，让平台真正
			弹出登录页——否则已失效但仍存在的 ``wt2`` 会被复用路径当成「已登录」落盘，
			用户没有任何重登入口（Issue #424）。只清目标平台域、只清当前 context，
			其他站点与其他 context 不动。
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
	all_contexts = list(browser.contexts)
	if reuse_existing:
		logged_in_ctx, ctx_index, ctx_account_fp = _find_logged_in_context(
			all_contexts,
			cookie_domain=cookie_domain,
			success_cookie=success_cookie,
		)
	else:
		logged_in_ctx, ctx_index, ctx_account_fp = None, -1, ""
	already_logged_in = logged_in_ctx is not None
	ctx = logged_in_ctx or (browser.contexts[0] if browser.contexts else browser.new_context())
	if not reuse_existing:
		# 强制重登：清掉当前 context 内目标平台域的 cookie。不清的话登录页会因残留
		# 登录态直接跳回首页，轮询立刻命中旧 wt2，--force 形同虚设。
		_clear_platform_cookies(ctx, cookie_domain=cookie_domain)
		print(f"[boss] --force：跳过复用，已清除当前 context 的 {cookie_domain} 登录态，改为重新登录", file=sys.stderr)
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
			# 多 context 账号歧义：复用的是「第一个带登录态的 context」，选中哪个取决于
			# browser.contexts 顺序，用户看不到也控制不了。打出 context 序号 + 账号指纹
			# （cookie 值的不可逆哈希前缀），让用户能区分选中的是不是目标账号。
			print(
				f"[boss] 检测到 CDP Chrome 已登录，正在复用现有登录态"
				f"（context {ctx_index + 1}/{len(all_contexts)}，账号指纹 {ctx_account_fp}）...",
				file=sys.stderr,
			)
			if len(all_contexts) > 1:
				print(
					"[boss] 提示：检测到多个浏览器 context。若账号不符，请关闭多余窗口/"
					"无痕页，或只保留目标账号的登录态后重试。",
					file=sys.stderr,
				)
		else:
			print("[boss] 正在 CDP Chrome 中打开登录页...", file=sys.stderr)
			# 智联复用用户已打开的 recruiter 页签时不导航（created_page=False），
			# 避免把用户正在筛选候选人的页面 goto 走（review #406 第 2 条）。
			if created_page or platform != "zhilian":
				try:
					page.goto(
						login_page_url,
						wait_until="commit",
						timeout=_NAV_TIMEOUT_MS,
					)
				except Exception:
					pass

			# 智联复用既有页签未登录时不导航（保护用户页面状态），页面上没有可见的扫码入口，
			# 提示「扫码」会误导且轮询必然超时；改为引导用户在既有页签内手动完成登录，
			# 轮询 at 保持不变——用户在页签里登录后轮询即可接上，超时也是真实的超时。
			if platform == "zhilian" and not created_page:
				print(f"[boss] 请在已打开的智联页签中完成登录，等待中...（超时 {timeout}s）", file=sys.stderr)
			else:
				print(f"[boss] 请在 Chrome 中扫码登录，等待中...（超时 {timeout}s）", file=sys.stderr)

			for i in range(timeout):
				time.sleep(1)
				# 裸 ctx.cookies() 让异常传播：轮询中途 CDP target 死掉时立即报真实连接错误，
				# 而不是被吞成 [] 继续空转到超时，误报成「扫码超时」（与最终读取同一规则）。
				cookies = [
					c for c in ctx.cookies() if _is_cookie_domain(c.get("domain", ""), cookie_domain)
				]
				if any(c.get("name") == success_cookie and c.get("value") for c in cookies):
					print("[boss] 检测到登录成功！", file=sys.stderr)
					break
				if i > 0 and i % 15 == 0:
					print(f"[boss] 等待中... {i}s", file=sys.stderr)
			else:
				raise TimeoutError(f"CDP 扫码登录超时（{timeout}s）")

		# UA 采集按路径 gate：_safe_user_agent 内部是 page.evaluate，页面停在未完成
		# 导航时会永久挂起（不抛异常），绝不能在导航卡住后调用（#390 规则）。
		#
		# page_ready 是「可对 page evaluate」的统一就绪门禁，覆盖所有复用既有页签
		# （not created_page）的情况——无论预先登录还是扫码后登录。新建页签的就绪由
		# _warm_home_for_runtime 的返回值 home_loaded 表达。
		ua = ""
		page_ready = False
		if not already_logged_in:
			# 未登录：登录页 goto 用 wait_until="commit"，不保证执行上下文就绪；扫码轮询
			# 在 cookie 出现时即 break，页面可能仍在导航。采 UA 前做有界就绪确认，卡住则
			# 容忍 UA 为空、不挂起。复用既有页签时（created_page=False）这里已确认就绪。
			page_ready = _ensure_page_evaluable(page)
			ua = _safe_user_agent(page) if page_ready else ""
			if created_page or platform != "zhilian":
				home_loaded = _warm_home_for_runtime(page, home_url, stage="登录后回到首页")
			else:
				home_loaded = True  # 智联复用页签：未导航，home_loaded 仅 zhipin 分支消费
		elif created_page:
			# 复用登录态但无既有平台页签：所有平台的新建页签都回各自 home
			# （去掉 platform=="zhipin" 限制，否则 zhilian 新建页签停在 about:blank，
			# _extract_zhilian_client_id 读 localStorage 必空 → TokenRefreshFailed）。
			home_loaded = _warm_home_for_runtime(page, home_url, stage="复用登录态回首页")
			page_ready = home_loaded
			ua = _safe_user_agent(page) if home_loaded else ""
		else:
			# 复用既有平台页签（已预先登录）：不导航，避免打断用户页面；有界确认就绪后采 UA。
			home_loaded = False
			page_ready = _ensure_page_evaluable(page)
			ua = _safe_user_agent(page) if page_ready else ""

		if already_logged_in:
			# 复用只证明 cookie 存在，不证明服务端仍认：wt2 已失效但仍在 jar 里时，
			# 不探测就会把死凭证当「登录成功」落盘，之后每条命令都 AUTH_REQUIRED（Issue #424）。
			probe_ready = home_loaded if created_page else page_ready
			status = _probe_reused_session(page, platform=platform) if probe_ready else "unverified"
			if status == "stale":
				raise ReusedSessionStaleError(
					"复用的登录态已在服务端失效（只读探测返回未登录），不落盘；改为重新登录"
				)
			if status == "unverified":
				print(
					"[boss] 提示：未能在线验证复用的登录态（页面未就绪或探测失败）；"
					"若之后命令报 AUTH_REQUIRED，请用 boss login --cdp --force 强制重登。",
					file=sys.stderr,
				)
			else:
				print("[boss] 复用的登录态已通过只读验证。", file=sys.stderr)

		# 任何导航之后重新读取 cookie，不依赖早期快照。这里是登录成功后的「最终读取」，
		# 不能用吞异常的 _matching_cookies：ctx.cookies() 抛错（如 CDP target closed）若返回
		# []，会被当作 cookies={} 的「成功」结果落盘（AuthManager.login 不校验主 cookie）。
		# 故直接 ctx.cookies() 让异常传播，并读完后校验主 cookie 存在，否则 raise。
		all_cookies = {
			c["name"]: c["value"]
			for c in ctx.cookies()
			if _is_cookie_domain(c.get("domain", ""), cookie_domain)
		}
		if not all_cookies.get(success_cookie):
			raise RuntimeError(
				f"登录态校验失败：最终读取未拿到主 cookie（{success_cookie}），凭证不落盘"
			)
		if platform == "zhipin":
			if created_page:
				# 首页成功加载才对其 evaluate 提取 stoken；否则回退读取 cookie jar
				stoken = _extract_stoken(page) if home_loaded else all_cookies.get("__zp_stoken__", "")
			else:
				# 复用既有页签：优先 cookie jar 的 stoken；缺失时仅页面就绪才提取。
				stoken = all_cookies.get("__zp_stoken__", "")
				if not stoken and page_ready:
					stoken = _extract_stoken(page)
		else:
			stoken = ""
		if platform == "zhilian":
			# cookie 里 x-zp-client-id 非空才直接用（空串也要落到 localStorage 兜底）；
			# 复用既有页签需页面就绪，否则在卡住的页面上 evaluate 会永久挂起（同 #390）。
			x_zp_client_id = all_cookies.get("x-zp-client-id") or ""
			# 新建页签看 home_loaded（首页卡住时不对卡住页面 evaluate），复用既有页签看 page_ready；
			# created_page 一短路就无视 home_loaded 会对卡住页面 evaluate 读 localStorage（同 #390 挂起）。
			if not x_zp_client_id and (home_loaded if created_page else page_ready):
				x_zp_client_id = _extract_zhilian_client_id(page)
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
				if any(
					c["name"] == success_cookie and _is_cookie_domain(c.get("domain", ""), cookie_domain)
					for c in cookies_list
				):
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
		cookies = {
			c["name"]: c["value"] for c in cookies_list if _is_cookie_domain(c.get("domain", ""), cookie_domain)
		}
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
