"""Shared hybrid HTTP client base for BOSS candidate/recruiter clients.

`BossClient` and `BossRecruiterClient` share ~90% of their request plumbing:
the httpx-channel retry loop (403/安全验证 refresh, stoken-expired refresh,
rate-limit cooldown), lazy client/browser construction, and lifecycle.
The per-platform differences (base URL / headers / referer map, auth-error
class, response codes, whether to stamp `__cli_endpoint_hint__`) are exposed
as class attributes so each subclass only sets data, not behavior.
"""

from __future__ import annotations

import random
import time
from pathlib import Path
from types import TracebackType
from typing import TYPE_CHECKING, Any, TypeVar, cast

import httpx

from boss_agent_cli.api.httpx_helpers import (
	add_stoken_to_get_params,
	browser_headers,
	merge_response_cookies,
	referer_header,
)
from boss_agent_cli.api.throttle import RequestThrottle

if TYPE_CHECKING:
	from boss_agent_cli.api.browser_client import BrowserSession
	from boss_agent_cli.auth.manager import AuthManager

_MAX_RETRIES = 3


class BrowserChannelRequired(RuntimeError):
	"""CDP / 显式浏览器来源下，该操作只有 httpx 实现，拒绝用 httpx 携带浏览器凭据发送。"""

_SelfT = TypeVar("_SelfT", bound="_BaseHttpClient")


class _BaseHttpClient:
	"""Hybrid API client base: httpx channel for low-risk ops, browser for high-risk ops."""

	# ── Per-platform data (set by subclasses) ────────────────────────
	_BASE_URL: str
	_DEFAULT_HEADERS: dict[str, str]
	_REFERER_MAP: dict[str, str]
	_AUTH_ERROR_CLS: type[Exception]
	_CODE_STOKEN_EXPIRED: int
	_CODE_RATE_LIMITED: int
	_ADD_ENDPOINT_HINT: bool = False

	def __init__(
		self,
		auth_manager: "AuthManager",
		*,
		delay: tuple[float, float] = (1.5, 3.0),
		cdp_url: str | None = None,
		browser_source: str | None = None,
	) -> None:
		self._auth = auth_manager
		self._delay = delay
		self._client: httpx.Client | None = None
		self._browser_session: "BrowserSession | None" = None
		self._throttle = RequestThrottle(delay)
		self._cdp_url = cdp_url
		# 浏览器通道来源意图；None = auto（默认路径行为不变）。
		# 只在此处保存并透传给唯一的 BrowserSession 构造点，不在本类上派生
		# 任何模式布尔——见 api/browser_source.py 的模块文档。
		self._browser_source = browser_source
		self._closed = False
		self._register()

	# ── Registry hooks (subclass keeps its own module-level WeakSet) ──

	def _register(self) -> None:
		"""Track this instance for the atexit safeguard. Overridden per module."""

	def _unregister(self) -> None:
		"""Drop this instance from the atexit safeguard. Overridden per module."""

	# ── Lazy channels ────────────────────────────────────────────────

	# ── Channel selection ────────────────────────────────────────────

	def is_browser_only(self) -> bool:
		"""平台请求是否只能走浏览器通道。

		配了 ``--cdp-url``、显式非 auto 的浏览器来源，或本进程的浏览器会话已经接上
		CDP Chrome 时为 True。此时 Chrome 里的 ``__zp_stoken__`` 由页面 JS 维护，
		再用 httpx 带着从浏览器拷来的 Cookie + stoken 并发打平台，会让这个 stoken
		被判异常（之后浏览器通道一律 code 37）。所以这些模式下所有平台读写都走浏览器。
		纯 httpx / headless 场景不受影响。
		"""
		# getattr 兜底：部分调用方 / 测试用 __new__ 构造实例，不经 __init__。
		if getattr(self, "_cdp_url", None):
			return True
		from boss_agent_cli.api.browser_source import resolve_policy

		if resolve_policy(getattr(self, "_browser_source", None)).name != "auto":
			return True
		session = getattr(self, "_browser_session", None)
		return session is not None and getattr(session, "_is_cdp", False) is True

	def _browser_request(
		self,
		method: str,
		url: str,
		*,
		params: dict[str, Any] | None = None,
		data: dict[str, Any] | None = None,
	) -> dict[str, Any]:  # pragma: no cover - 子类实现
		raise NotImplementedError

	def _request_via_browser(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
		"""browser-only 模式下 ``_request`` 的落点：同一请求改走浏览器通道，单次、不重试。

		``extra_headers``（Referer）在页面 fetch 里本来就设不了，``follow_redirects``
		由浏览器处理；其余 httpx 专属参数说明该调用没有浏览器等价实现，直接拒绝。
		"""
		kwargs.pop("extra_headers", None)
		kwargs.pop("follow_redirects", None)
		params = kwargs.pop("params", None)
		data = kwargs.pop("data", None)
		if kwargs:
			raise BrowserChannelRequired(
				f"CDP 模式下该请求无法改走浏览器通道（不支持参数: {', '.join(sorted(kwargs))}）"
			)
		return self._browser_request(method, url, params=params, data=data)

	# ── CDP code 37 lock ─────────────────────────────────────────────

	def _risk_lock_dir(self) -> Path | None:
		data_dir = getattr(getattr(self, "_auth", None), "data_dir", None)
		if isinstance(data_dir, Path):
			return data_dir
		if isinstance(data_dir, str) and data_dir:
			return Path(data_dir)
		return None

	def _check_cdp_risk_lock(self, browser: Any) -> None:
		"""有 code 37 锁时，发请求前核对 Chrome 当前 stoken 摘要；没变就拒绝发送。"""
		from boss_agent_cli.api import cdp_risk_lock

		data_dir = self._risk_lock_dir()
		if data_dir is None:
			return
		lock = cdp_risk_lock.read_lock(data_dir)
		if lock is None:
			return
		ensure_started = getattr(browser, "ensure_started", None)
		if callable(ensure_started):
			ensure_started()
		if getattr(browser, "_is_cdp", False) is not True:
			# 锁针对的是 CDP Chrome 里的 stoken；headless 会话不受影响，也不解锁。
			return
		read_hash = getattr(browser, "current_stoken_hash", None)
		current = read_hash() if callable(read_hash) else None
		if lock.matches(current):
			from boss_agent_cli.api.client import EnvironmentRiskLockedError

			raise EnvironmentRiskLockedError.from_lock(lock)
		cdp_risk_lock.clear_lock(data_dir)

	def _record_cdp_risk_lock(self, browser: Any) -> None:
		"""CDP 浏览器请求命中环境类 code 37：记下当前 stoken 的摘要（不存原值）。"""
		from boss_agent_cli.api import cdp_risk_lock

		data_dir = self._risk_lock_dir()
		if data_dir is None or getattr(browser, "_is_cdp", False) is not True:
			return
		read_hash = getattr(browser, "current_stoken_hash", None)
		current = read_hash() if callable(read_hash) else None
		try:
			cdp_risk_lock.write_lock(data_dir, current if isinstance(current, str) else None, cdp_url=self._cdp_url)
		except OSError:
			pass

	def _cdp_http_url(self) -> str:
		from boss_agent_cli.api.browser_urls import DEFAULT_CDP_URL

		return getattr(self, "_cdp_url", None) or DEFAULT_CDP_URL

	def _check_cdp_risk_lock_raw(self) -> None:
		"""聊天页动作（裸 CDP、不附着 patchright）前的锁检查：经 ``Storage.getCookies`` 读 stoken 摘要。

		与 ``_check_cdp_risk_lock`` 同一契约：摘要没变（或读不到）就拒绝，变了就解锁。
		"""
		from boss_agent_cli.api import cdp_risk_lock

		data_dir = self._risk_lock_dir()
		if data_dir is None:
			return
		lock = cdp_risk_lock.read_lock(data_dir)
		if lock is None:
			return
		from boss_agent_cli.api.browser_client import read_cdp_stoken_hash

		if lock.matches(read_cdp_stoken_hash(self._cdp_http_url())):
			from boss_agent_cli.api.client import EnvironmentRiskLockedError

			raise EnvironmentRiskLockedError.from_lock(lock)
		cdp_risk_lock.clear_lock(data_dir)

	def _record_cdp_risk_lock_raw(self) -> None:
		"""聊天页动作期间页面请求命中环境类 code 37：经裸 CDP 读摘要后记锁（不存原值）。"""
		from boss_agent_cli.api import cdp_risk_lock
		from boss_agent_cli.api.browser_client import read_cdp_stoken_hash

		data_dir = self._risk_lock_dir()
		if data_dir is None:
			return
		try:
			cdp_risk_lock.write_lock(
				data_dir, read_cdp_stoken_hash(self._cdp_http_url()), cdp_url=getattr(self, "_cdp_url", None),
			)
		except OSError:
			pass

	def _get_client(self) -> httpx.Client:
		if self.is_browser_only():
			# 兜底闸门：任何漏网的 httpx 路径在 CDP 模式下都不能带着浏览器凭据发出去。
			raise BrowserChannelRequired(
				"CDP 模式下不会用 httpx 携带浏览器登录态访问平台，而该操作暂无浏览器通道实现；"
				"如确需执行，请去掉 --cdp-url / --browser-source 后在非 CDP 模式下运行"
			)
		if self._client is None:
			token = self._auth.get_token()
			headers = browser_headers(self._DEFAULT_HEADERS, token)
			self._client = httpx.Client(
				base_url=self._BASE_URL,
				cookies=token.get("cookies", {}),
				headers=headers,
				follow_redirects=True,
				timeout=30,
			)
		return self._client

	def _get_browser(self, *, browser_source: str | None = None) -> "BrowserSession":
		"""Return the single live browser session for the requested source.

		A source that forbids stored credentials is constructed with an empty
		cookie/UA payload without calling ``AuthManager.get_token()``. Switching
		sources closes the previous session first, so the client never keeps the
		parallel auto/existing-browser slots that #410 removed.
		"""
		from boss_agent_cli.api.browser_source import resolve_policy

		policy = resolve_policy(self._browser_source if browser_source is None else browser_source)
		if self._browser_session is not None:
			current_name = getattr(getattr(self._browser_session, "_policy", None), "name", None)
			# BrowserSession always exposes a string policy name. Test doubles and
			# downstream injected sessions may predate the source seam; keep reusing
			# those instead of silently replacing them with a real browser process.
			if isinstance(current_name, str) and current_name != policy.name:
				self._browser_session.close()
				self._browser_session = None

		if self._browser_session is None:
			from boss_agent_cli.api.browser_client import BrowserSession

			token = self._auth.get_token() if policy.use_stored_credentials else {}
			self._browser_session = BrowserSession(
				cookies=token.get("cookies", {}),
				user_agent=token.get("user_agent", ""),
				delay=self._delay,
				cdp_url=self._cdp_url,
				logger=getattr(self._auth, "_logger", None),
				browser_source=policy.name,
			)
		return self._browser_session

	def _headers_for(self, url: str) -> dict[str, str]:
		return referer_header(url, self._REFERER_MAP, f"{self._BASE_URL}/")

	def _merge_cookies(self, resp: httpx.Response) -> None:
		merge_response_cookies(self._get_client(), resp)

	def _should_refresh_token_response(self, data: dict[str, Any]) -> bool:
		"""决定该响应是否应触发 token 刷新重试；子类按平台语义覆写。"""
		return data.get("code") == self._CODE_STOKEN_EXPIRED

	# ── httpx request with retry (low-risk ops) ──────────────────────

	def _request(self, method: str, url: str, *, retry: bool = True, **kwargs: Any) -> dict[str, Any]:
		"""httpx 请求，循环重试（最多 _MAX_RETRIES 次）。"""
		# extra_headers overrides yaml-driven defaults from _headers_for(url); candidate
		# client never passes it, so the pop is a no-op there (behavior preserved).
		if self.is_browser_only():
			return self._request_via_browser(method, url, **kwargs)
		extra_headers_override: dict[str, str] = kwargs.pop("extra_headers", {})
		max_retries = _MAX_RETRIES if retry else 0
		for attempt in range(max_retries + 1):
			client = self._get_client()
			token = self._auth.get_token()
			stoken = token.get("stoken", "")

			add_stoken_to_get_params(method, kwargs, stoken)

			self._throttle.wait()

			headers = {**self._headers_for(url), **extra_headers_override}
			resp = client.request(method, url, headers=headers, **kwargs)
			self._throttle.mark()
			self._merge_cookies(resp)

			# 403 或安全验证 → 刷新 token 重试
			if resp.status_code == 403 or "安全验证" in resp.text:
				if attempt >= max_retries:
					message = (
						"Token 刷新后仍被拒绝，请重新登录"
						if retry
						else "请求被拒绝；为避免重复写入未自动重试，请检查登录态"
					)
					raise self._AUTH_ERROR_CLS(message)
				backoff = (2**attempt) + random.uniform(0.5, 1.5)
				time.sleep(backoff)
				self._auth.force_refresh(cdp_url=self._cdp_url, browser_source=self._browser_source)
				self._client = None
				continue

			resp.raise_for_status()
			data = resp.json()
			code = data.get("code")

			# stoken 过期 → 刷新重试（BOSS 客户端按语境分类，语义不明不刷新）
			if self._should_refresh_token_response(data) and attempt < max_retries:
				backoff = (2**attempt) + random.uniform(0.5, 1.5)
				time.sleep(backoff)
				self._auth.force_refresh(cdp_url=self._cdp_url, browser_source=self._browser_source)
				self._client = None
				continue

			# 频率限制 → 冷却重试
			if code == self._CODE_RATE_LIMITED and attempt < max_retries:
				cooldown = min(60, 10 * (2**attempt))
				time.sleep(cooldown)
				continue

			if self._ADD_ENDPOINT_HINT and isinstance(data, dict):
				data.setdefault("__cli_endpoint_hint__", url)
			return cast("dict[str, Any]", data)

		raise self._AUTH_ERROR_CLS("请求失败，已达最大重试次数")

	# ── Lifecycle ────────────────────────────────────────────────────

	def close(self) -> None:
		"""Release httpx client and browser session. Idempotent."""
		if self._closed:
			return
		self._closed = True
		if self._browser_session:
			self._browser_session.close()
			self._browser_session = None
		if self._client:
			self._client.close()
			self._client = None
		self._unregister()

	def __enter__(self: _SelfT) -> _SelfT:
		return self

	def __exit__(
		self,
		exc_type: type[BaseException] | None,
		exc_val: BaseException | None,
		exc_tb: TracebackType | None,
	) -> None:
		self.close()
