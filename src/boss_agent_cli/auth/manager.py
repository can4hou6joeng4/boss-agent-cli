from pathlib import Path
from typing import Any

from boss_agent_cli.auth.browser import login_via_browser, login_via_cdp, probe_cdp, refresh_stoken, refresh_stoken_via_cdp
from boss_agent_cli.auth.cookie_extract import extract_cookies
from boss_agent_cli.auth.qr_login import qr_login_httpx
from boss_agent_cli.auth.token_store import TokenStore
from boss_agent_cli.output import Logger


class AuthRequired(Exception):
	pass


class TokenRefreshFailed(Exception):
	pass


class AuthManager:
	def __init__(self, data_dir: Path, *, logger: Logger | None = None, platform: str = "zhipin") -> None:
		self._platform = platform or "zhipin"
		auth_dir = data_dir / "auth" if self._platform == "zhipin" else data_dir / "auth" / self._platform
		self._store = TokenStore(auth_dir)
		self._token: dict[str, Any] | None = None
		self._logger = logger or Logger()

	def _login_action(self) -> str:
		return "boss --platform zhilian login" if self._platform == "zhilian" else "boss login"

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
	) -> dict[str, Any]:
		"""三级降级登录：Cookie 提取 → CDP 自动探测 → patchright 扫码。

		Args:
			force_cdp: 为 True 时跳过 Cookie 提取，CDP 不可用直接报错。
		"""
		method = "未知"
		token: dict[str, Any] | None = None

		if force_cdp:
			# --cdp 强制模式：跳过 Cookie，CDP 不可用直接抛异常
			self._logger.info("强制 CDP 模式，跳过 Cookie 提取")
			token = login_via_cdp(cdp_url=cdp_url, timeout=timeout, platform=self._platform)
			method = "CDP 扫码"
			self._store.save(token)
			self._token = token
			return {**token, "_method": method}

		# 第一步：尝试从本地浏览器提取 Cookie
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
				token = login_via_cdp(cdp_url=cdp_url, timeout=timeout, platform=self._platform)
				method = "CDP 扫码"
				self._store.save(token)
				self._token = token
				return {**token, "_method": method}
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

	def _has_primary_cookie(self, token: dict[str, Any]) -> bool:
		cookies = token.get("cookies", {})
		primary_cookie = "wt2" if self._platform == "zhipin" else "zp_token"
		return bool(cookies.get(primary_cookie))

	def _verify_cookie(self, token: dict[str, Any]) -> bool:
		"""验证 Cookie 是否有效。"""
		try:
			import httpx
			if self._platform == "zhilian":
				from boss_agent_cli.api.zhilian_client import USER_INFO_URL
				headers = {
					"User-Agent": token.get("user_agent") or "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
					"Referer": "https://i.zhaopin.com/",
				}
				if client_id := token.get("x_zp_client_id") or token.get("client_id"):
					headers["x-zp-client-id"] = str(client_id)
				resp = httpx.get(
					USER_INFO_URL,
					cookies=token.get("cookies", {}),
					headers=headers,
					timeout=10,
				)
				data = resp.json()
				return bool(data.get("code") == 200)

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

	def force_refresh(self, cdp_url: str | None = None) -> None:
		with self._store.refresh_lock():
			current = self._store.load()
			if current is None:
				raise TokenRefreshFailed("无法刷新 Token，请重新登录")
			self._logger.info("Token 过期，正在静默刷新...")
			try:
				if self._platform == "zhilian":
					refreshed = extract_cookies(None, platform=self._platform)
					if not refreshed or not self._verify_cookie(refreshed):
						refreshed = login_via_cdp(cdp_url=cdp_url, timeout=30, platform=self._platform)
					if not refreshed or not self._verify_cookie(refreshed):
						raise TokenRefreshFailed("智联登录态刷新失败，请重新登录")
					self._store.save(refreshed)
					self._token = refreshed
					return

				# CDP 优先：指纹一致，不会被 BOSS 直聘拒绝
				if probe_cdp(cdp_url):
					self._logger.info("检测到 CDP，使用 CDP 刷新 stoken")
					new_stoken = refresh_stoken_via_cdp(cdp_url)
				else:
					self._logger.info("CDP 不可用，降级到 headless 刷新 stoken")
					new_stoken = refresh_stoken(
						current["cookies"],
						current.get("user_agent", ""),
					)
				refreshed = {**current, "stoken": new_stoken}
				self._store.save(refreshed)
				self._token = refreshed
			except Exception as e:
				raise TokenRefreshFailed(f"Token 刷新失败: {e}") from e

	def check_status(self) -> dict[str, Any] | None:
		return self._store.load()

	def logout(self) -> None:
		"""清除本地登录态"""
		self._store.clear()
		self._token = None
