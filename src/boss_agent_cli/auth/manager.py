from pathlib import Path
from typing import Any

from boss_agent_cli.api.client import PlatformRiskError
from boss_agent_cli.auth.browser import (
	ReusedSessionStaleError,
	login_via_browser,
	login_via_cdp,
	probe_cdp,
	refresh_stoken,
	refresh_stoken_via_cdp,
)
from boss_agent_cli.auth.cookie_extract import extract_cookies
from boss_agent_cli.auth.curl_import import parse_curl_auth
from boss_agent_cli.auth.qr_login import qr_login_httpx
from boss_agent_cli.auth.token_store import TokenStore
from boss_agent_cli.output import Logger


class AuthRequired(Exception):
	pass


class TokenRefreshFailed(Exception):
	pass


class AuthManager:
	def __init__(self, data_dir: Path, *, logger: Logger | None = None, platform: str = "zhipin") -> None:
		self._data_dir = data_dir
		self._platform = platform or "zhipin"
		auth_dir = data_dir / "auth" if self._platform == "zhipin" else data_dir / "auth" / self._platform
		self._store = TokenStore(auth_dir)
		self._token: dict[str, Any] | None = None
		self._logger = logger or Logger()

	@property
	def data_dir(self) -> Path:
		"""CLI 数据目录（CDP 风控锁等进程间状态落在这里）。"""
		return self._data_dir

	def _cdp_login(self, *, cdp_url: str | None, timeout: int, reuse_existing: bool) -> dict[str, Any]:
		"""CDP 登录；复用的登录态被在线探测判定失效时，自动改走不复用路径重登一次。

		登录过程中的只读探测命中环境类 code 37 时，与其他 CDP 请求一样记下风控锁。
		"""
		from boss_agent_cli.api.client import EnvironmentRiskError, EnvironmentRiskLockedError

		try:
			try:
				return login_via_cdp(cdp_url=cdp_url, timeout=timeout, platform=self._platform, reuse_existing=reuse_existing)
			except ReusedSessionStaleError as exc:
				self._logger.warning(f"{exc}")
				return login_via_cdp(cdp_url=cdp_url, timeout=timeout, platform=self._platform, reuse_existing=False)
		except EnvironmentRiskError as exc:
			if not isinstance(exc, EnvironmentRiskLockedError):
				self._record_cdp_risk_lock(cdp_url)
			raise

	def _record_cdp_risk_lock(self, cdp_url: str | None) -> None:
		from boss_agent_cli.api import cdp_risk_lock
		from boss_agent_cli.api.browser_client import read_cdp_stoken_hash
		from boss_agent_cli.api.browser_urls import DEFAULT_CDP_URL

		try:
			cdp_risk_lock.write_lock(self._data_dir, read_cdp_stoken_hash(cdp_url or DEFAULT_CDP_URL), cdp_url=cdp_url)
		except OSError:
			pass

	def _check_cdp_login_risk_lock(self, cdp_url: str | None) -> None:
		"""CDP 登录前核对 code 37 风控锁。

		登录会打开平台页面并做一次在线只读探测，同样会带着那个被拦的 stoken 访问平台。
		Chrome 里的 stoken 已经变了（用户在页面里恢复过）就自动解锁继续；没变或读不到就拒绝，
		提示先手动浏览恢复、再 ``boss clean --risk-lock``，或显式加 ``--ignore-risk-lock``。
		"""
		from boss_agent_cli.api import cdp_risk_lock
		from boss_agent_cli.api.browser_client import read_cdp_stoken_hash
		from boss_agent_cli.api.browser_urls import DEFAULT_CDP_URL
		from boss_agent_cli.api.client import EnvironmentRiskLockedError

		lock = cdp_risk_lock.read_lock(self._data_dir)
		if lock is None:
			return
		if not lock.matches(read_cdp_stoken_hash(cdp_url or DEFAULT_CDP_URL)):
			cdp_risk_lock.clear_lock(self._data_dir)
			return
		raise EnvironmentRiskLockedError(
			"CDP 浏览器此前命中访问环境风控 (code 37)，Chrome 里的 __zp_stoken__ 还没更新，"
			"login 已停止（未打开登录页、未做在线探测）。请先在这个 Chrome 里手动打开 BOSS 直聘职位列表页，"
			"确认能正常加载并等几分钟；stoken 变化后再登录会自动解锁，或执行 boss clean --risk-lock 手动解除。"
			"确需立即登录可加 --ignore-risk-lock（不解除锁）。",
			is_cdp=True,
		)

	def _login_action(self) -> str:
		return "boss login" if self._platform == "zhipin" else f"boss --platform {self._platform} login"

	def get_token(self) -> dict[str, Any]:
		if self._token is not None:
			return self._token
		self._token = self._store.load()
		if self._token is None:
			raise AuthRequired(f"未登录，请先执行 {self._login_action()}")
		return self._token

	def login(
		self,
		*,
		timeout: int = 120,
		cookie_source: str | None = None,
		cdp_url: str | None = None,
		force_cdp: bool = False,
		force_relogin: bool = False,
		ignore_risk_lock: bool = False,
	) -> dict[str, Any]:
		"""三级降级登录：Cookie 提取 → CDP 自动探测 → patchright 扫码。

		Args:
			force_cdp: 为 True 时跳过 Cookie 提取，CDP 不可用直接报错。
			force_relogin: 为 True 时（``--force``）不复用任何既有登录态：跳过本地
				浏览器 Cookie 提取，CDP 路径不扫描已登录 context 并清掉目标平台域 cookie
				后重新登录。与 ``force_cdp`` 正交。
			ignore_risk_lock: 为 True 时（``--ignore-risk-lock``）跳过 CDP 风控锁检查；锁本身保留。
		"""
		method = "未知"
		token: dict[str, Any] | None = None
		reuse_existing = not force_relogin

		if force_cdp:
			# --cdp 强制模式：跳过 Cookie，CDP 不可用直接抛异常
			self._logger.info("强制 CDP 模式，跳过 Cookie 提取")
			if not ignore_risk_lock and probe_cdp(cdp_url):
				self._check_cdp_login_risk_lock(cdp_url)
			token = self._cdp_login(cdp_url=cdp_url, timeout=timeout, reuse_existing=reuse_existing)
			method = "CDP 扫码"
			self._store.save(token)
			self._token = token
			return {**token, "_method": method}

		# 第一步：尝试从本地浏览器提取 Cookie（--force 下跳过：本地 Cookie 正是要放弃的旧登录态）
		if force_relogin:
			self._logger.info("--force：跳过本地浏览器 Cookie 提取")
			token = None
		else:
			self._logger.info("尝试从本地浏览器提取 Cookie...")
			token = extract_cookies(cookie_source, platform=self._platform)
		if token and self._has_primary_cookie(token):
			if self._verify_cookie(token):
				self._store.save(token)
				self._token = token
				self._logger.info("Cookie 提取成功，已保存")
				return {**token, "_method": "Cookie 提取"}
			self._logger.info("提取的 Cookie 已失效，降级到 CDP")
		else:
			self._logger.info("未能从浏览器提取 Cookie，降级到 CDP")

		# 第二步：CDP 自动探测
		if probe_cdp(cdp_url):
			self._logger.info("检测到 CDP 可用，尝试 CDP 登录...")
			if not ignore_risk_lock:
				self._check_cdp_login_risk_lock(cdp_url)
			try:
				token = self._cdp_login(cdp_url=cdp_url, timeout=timeout, reuse_existing=reuse_existing)
				method = "CDP 扫码"
				self._store.save(token)
				self._token = token
				return {**token, "_method": method}
			except PlatformRiskError:
				# 风控：立即停止，绝不降级到 patchright 再登一次（#419 / #422 契约）
				raise
			except Exception as e:
				self._logger.info(f"CDP 登录失败（{e}），降级到 patchright")
		else:
			self._logger.info("CDP 不可用，尝试 QR 纯 httpx 登录")

		# 第三步：QR 纯 httpx 登录（仅 zhipin）
		if self._platform == "zhipin":
			try:
				self._logger.info("尝试 QR 纯 httpx 登录...")
				token = qr_login_httpx(timeout=timeout)
				method = "QR httpx 登录"
				self._store.save(token)
				self._token = token
				return {**token, "_method": method}
			except Exception as e:
				self._logger.info(f"QR httpx 登录失败（{e}），降级到 patchright")

		# 第四步：patchright 扫码（兜底）
		token = login_via_browser(timeout=timeout, platform=self._platform)
		method = "扫码登录"
		self._store.save(token)
		self._token = token
		return {**token, "_method": method}

	def import_curl(self, command: str) -> dict[str, Any]:
		"""验证导入的 Cookie 后替换原生登录态；失败不降级、不覆盖旧会话。"""
		if self._platform != "zhipin":
			raise ValueError("cURL 导入目前仅支持 BOSS 直聘")
		token = parse_curl_auth(command)
		if not self._verify_cookie(token):
			raise AuthRequired("导入登录态未通过验证")
		self._store.save(token)
		self._token = token
		return {**token, "_method": "cURL 导入"}

	def _has_primary_cookie(self, token: dict[str, Any]) -> bool:
		cookies = token.get("cookies", {})
		primary_cookie = "wt2"
		return bool(cookies.get(primary_cookie))

	def _verify_cookie(self, token: dict[str, Any]) -> bool:
		"""验证 Cookie 是否有效。"""
		try:
			import httpx
			from boss_agent_cli.api import endpoints
			resp = httpx.get(
				endpoints.USER_INFO_URL,
				cookies=token.get("cookies", {}),
				headers={
					"User-Agent": token.get("user_agent") or "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
					"Referer": "https://www.zhipin.com/",
				},
				timeout=10,
			)
			data = resp.json()
			return bool(data.get("code") == 0)
		except (httpx.HTTPError, ValueError, KeyError):
			return False

	def force_refresh(self, cdp_url: str | None = None, browser_source: str | None = None) -> None:
		"""静默刷新登录态。

		``browser_source`` 与 ``api/browser_source.py`` 的策略表同源：刷新走的是
		httpx 通道之外的浏览器（CDP 或 headless），所以它同样受策略约束——
		否则 ``stored-cookie`` 只锁住了浏览器通道，stoken 过期时这里照样会背着
		用户起一个 headless Chromium 带着本地 Cookie 访问平台（Issue #387 / PR #404
		review 第 10 条）。``None`` / ``auto`` 行为与引入该参数前逐字一致。
		"""
		from boss_agent_cli.api.browser_source import CHANNEL_CDP, BrowserSourceUnavailable, resolve_policy

		policy = resolve_policy(browser_source)
		with self._store.refresh_lock():
			current = self._store.load()
			if current is None:
				raise TokenRefreshFailed("无法刷新 Token，请重新登录")
			# fail-closed 来源：不自动探测默认端口、不启动 headless、不触发登录。
			# 判定放在 try 之外，让策略错误码原样上抛，而不是被兜底包成 TokenRefreshFailed。
			if policy.fail_closed and not policy.auto_probe_cdp and not cdp_url:
				raise BrowserSourceUnavailable(
					policy, attempted=(), detail="stoken 刷新需要 --cdp-url，该来源不会探测默认端口"
				)
			self._logger.info("Token 过期，正在静默刷新...")
			try:
				# CDP 优先：指纹一致，不会被 BOSS 直聘拒绝
				if policy.allows(CHANNEL_CDP) and probe_cdp(cdp_url):
					self._logger.info("检测到 CDP，使用 CDP 刷新 stoken")
					new_stoken = refresh_stoken_via_cdp(cdp_url)
				elif not policy.allow_browser_launch:
					raise BrowserSourceUnavailable(
						policy,
						attempted=(CHANNEL_CDP,) if policy.allows(CHANNEL_CDP) else (),
						detail="stoken 刷新不会降级到 headless",
					)
				else:
					self._logger.info("CDP 不可用，降级到 headless 刷新 stoken")
					new_stoken = refresh_stoken(
						current["cookies"],
						current.get("user_agent", ""),
					)
				refreshed = {**current, "stoken": new_stoken}
				self._store.save(refreshed)
				self._token = refreshed
			except BrowserSourceUnavailable:
				raise
			except Exception as e:
				raise TokenRefreshFailed(f"Token 刷新失败: {e}") from e

	def check_status(self) -> dict[str, Any] | None:
		return self._store.load()

	def logout(self) -> None:
		"""清除本地登录态"""
		self._store.clear()
		self._token = None
