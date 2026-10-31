from pathlib import Path
from typing import Any

from boss_agent_cli.api.client import PlatformRiskError
from boss_agent_cli.api.zhipin_errors import normalize_risk_code
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
		self._unpersisted_risk_lock: Any = None

	@property
	def data_dir(self) -> Path:
		"""CLI 数据目录（CDP 风控锁等进程间状态落在这里）。"""
		return self._data_dir

	def _cdp_login(self, *, cdp_url: str | None, timeout: int, reuse_existing: bool) -> dict[str, Any]:
		"""CDP 登录；复用的登录态被在线探测判定失效时，自动改走不复用路径重登一次。

		登录过程中的账号/环境风险与其他平台请求一样锁存，不再降级。
		"""
		self._check_cdp_login_risk_lock(cdp_url)
		try:
			try:
				return login_via_cdp(
					cdp_url=cdp_url, timeout=timeout, platform=self._platform, reuse_existing=reuse_existing
				)
			except ReusedSessionStaleError as exc:
				self._logger.warning(f"{exc}")
				return login_via_cdp(cdp_url=cdp_url, timeout=timeout, platform=self._platform, reuse_existing=False)
		except PlatformRiskError as exc:
			self._record_cdp_risk_lock(cdp_url, error=exc)
			raise

	def _record_cdp_risk_lock(
		self, cdp_url: str | None = None, *, error: PlatformRiskError | None = None, token: dict[str, Any] | None = None,
	) -> None:
		from boss_agent_cli.api import cdp_risk_lock
		import time

		if self._platform != "zhipin":
			return
		reason = "ACCOUNT_RISK" if error is not None and error.code == "ACCOUNT_RISK" else "ENVIRONMENT_RISK"
		details = error.details if error is not None else {}
		prior_lock = details.get("risk_lock", {})
		raw_code = details.get("platform_code", prior_lock.get("platform_code") if isinstance(prior_lock, dict) else None) if error is not None else 37
		saved_token = token or self._token or {}
		stoken = saved_token.get("stoken") or saved_token.get("cookies", {}).get(cdp_risk_lock.STOKEN_COOKIE)
		lock = cdp_risk_lock.RiskLock(
			stoken_sha256=cdp_risk_lock.hash_stoken(stoken) if isinstance(stoken, str) else None,
			locked_at=time.time(), cdp_url=cdp_url, reason=reason,
			platform_code=normalize_risk_code(raw_code),
		)
		self._unpersisted_risk_lock = lock
		try:
			cdp_risk_lock.write_lock(
				self._data_dir, lock.stoken_sha256, cdp_url=lock.cdp_url, now=lock.locked_at,
				reason=lock.reason, platform_code=lock.platform_code,
			)
			self._unpersisted_risk_lock = None
		except OSError:
			pass
		from boss_agent_cli.api.request_budget import budget_directory
		root = budget_directory(self._data_dir)
		if root != self._data_dir.resolve():
			try:
				cdp_risk_lock.write_lock(root, None, now=lock.locked_at, reason=lock.reason, platform_code=lock.platform_code)
			except OSError:
				self._unpersisted_risk_lock = lock
		if error is not None:
			error.details["risk_lock"] = {
				"locked": True, "reason": lock.reason, "platform_code": lock.platform_code,
				"explicit_resume_required": True, "persisted": self._unpersisted_risk_lock is None,
			}

	def _check_cdp_login_risk_lock(self, cdp_url: str | None) -> None:
		"""登录、导入、刷新前只查 BOSS 本地锁；不连接 CDP，也不因新令牌解锁。"""
		from boss_agent_cli.api import cdp_risk_lock

		if self._platform != "zhipin":
			return
		lock = cdp_risk_lock.read_lock(self._data_dir) or self._unpersisted_risk_lock
		if lock is not None:
			raise PlatformRiskError.from_lock(lock)
		from boss_agent_cli.api.request_budget import budget_directory, check_request_control
		root = budget_directory(self._data_dir)
		if root != self._data_dir.resolve():
			cdp_risk_lock.raise_if_locked(root)
		check_request_control()

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
			ignore_risk_lock: 历史兼容参数，不再跳过锁检查；仅用户显式清除本地锁后允许恢复。
		"""
		self._check_cdp_login_risk_lock(cdp_url)
		method = "未知"
		token: dict[str, Any] | None = None
		reuse_existing = not force_relogin

		if force_cdp:
			# --cdp 强制模式：跳过 Cookie，CDP 不可用直接抛异常
			self._logger.info("强制 CDP 模式，跳过 Cookie 提取")
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
			self._logger.info("CDP 不可用，改用浏览器扫码登录")

		# 第三步：patchright 扫码（兜底）
		try:
			token = login_via_browser(timeout=timeout, platform=self._platform)
		except PlatformRiskError as error:
			self._record_cdp_risk_lock(cdp_url, error=error)
			raise
		method = "扫码登录"
		self._store.save(token)
		self._token = token
		return {**token, "_method": method}

	def import_curl(self, command: str) -> dict[str, Any]:
		"""验证导入的 Cookie 后替换原生登录态；失败不降级、不覆盖旧会话。"""
		if self._platform != "zhipin":
			raise ValueError("cURL 导入目前仅支持 BOSS 直聘")
		self._check_cdp_login_risk_lock(None)
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
		self._check_cdp_login_risk_lock(None)
		try:
			import httpx
			from boss_agent_cli.api import endpoints
			from boss_agent_cli.api.client import AccountRiskError
			from boss_agent_cli.api.zhipin_errors import platform_risk_page_response, raise_platform_risk_response

			resp = httpx.get(
				endpoints.USER_INFO_URL,
				cookies=token.get("cookies", {}),
				headers={
					"User-Agent": token.get("user_agent")
					or "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
					"Referer": "https://www.zhipin.com/",
				},
				timeout=10,
			)
			try:
				data = resp.json()
			except ValueError:
				data = None
			if isinstance(data, dict):
				raise_platform_risk_response(data)
			try:
				final_url = str(resp.url)
			except (AttributeError, RuntimeError):
				final_url = ""
			page_risk = platform_risk_page_response(
				url=final_url,
				content_type=str(getattr(resp, "headers", {}).get("content-type", "")),
				body=resp.text if isinstance(resp.text, str) else "",
			)
			if page_risk is not None:
				raise_platform_risk_response(page_risk)
			if resp.status_code == 403:
				raise AccountRiskError(
					"Cookie 验证遇到平台安全页，请先在官方页面完成验证",
					details={"platform_code": None, "risk_source": "security_page_or_forbidden"},
				)
			resp.raise_for_status()
			return bool(isinstance(data, dict) and data.get("code") == 0)
		except PlatformRiskError as error:
			self._record_cdp_risk_lock(error=error, token=token)
			raise
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

		self._check_cdp_login_risk_lock(cdp_url)
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
				# 按来源策略优先复用 CDP；风险仍须原样停止，不保证平台接受。
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
			except PlatformRiskError as error:
				self._record_cdp_risk_lock(cdp_url, error=error, token=current)
				raise
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
