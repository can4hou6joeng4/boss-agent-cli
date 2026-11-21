"""Shared hybrid HTTP client base for BOSS candidate/recruiter clients.

`BossClient` and `BossRecruiterClient` share ~90% of their request plumbing:
	the httpx-channel retry loop (explicit stoken-expired refresh,
rate-limit cooldown), lazy client/browser construction, and lifecycle.
The per-platform differences (base URL / headers / referer map, auth-error
class, response codes, whether to stamp `__cli_endpoint_hint__`) are exposed
as class attributes so each subclass only sets data, not behavior.
"""

from __future__ import annotations

import random
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import TracebackType
from typing import TYPE_CHECKING, Any, TypeVar, cast

import httpx

from boss_agent_cli.config import DEFAULT_REQUEST_DELAY
from boss_agent_cli.api.httpx_helpers import (
	PAGE_IDENTITY_HEADERS,
	add_stoken_to_get_params,
	new_trace_id,
	browser_headers,
	merge_response_cookies,
	referer_header,
)
from boss_agent_cli.api.endpoints_loader import endpoint_for_request
from boss_agent_cli.api.throttle import RequestThrottle
from boss_agent_cli.api.request_budget import (
	_MAX_COOLDOWN_SECONDS,
	RequestBudget,
	cancellable_sleep,
	check_request_control,
	retry_after_seconds,
)
from boss_agent_cli.api.zhipin_errors import (
	normalize_risk_code,
	platform_risk_page_response,
	raise_platform_risk_response,
)

if TYPE_CHECKING:
	from boss_agent_cli.api.browser_client import BrowserSession
	from boss_agent_cli.api.browser_source import BrowserSourcePolicy
	from boss_agent_cli.api.client import PlatformRiskError
	from boss_agent_cli.auth.manager import AuthManager

_MAX_RETRIES = 3

# 确定发生在请求发出之前的传输异常：连接未建立、代理握手失败、协议或连接池
# 在选择阶段就终结，一个字节都没发出。写请求遇到它们时平台侧副作用一定没有
# 发生，不能按「结果未知」报成 ``ACTION_UNCONFIRMED`` 并禁止重试（Issue #2216）。
# 其余 ``httpx.TransportError``（读 / 写超时、RemoteProtocolError 等）发生在
# 请求可能已送出之后，仍按未知处理。
_UNSENT_TRANSPORT_ERRORS: tuple[type[httpx.TransportError], ...] = (
	httpx.ConnectError,
	httpx.ConnectTimeout,
	httpx.PoolTimeout,
	httpx.ProxyError,
	httpx.UnsupportedProtocol,
	httpx.LocalProtocolError,
)


class BrowserChannelRequired(RuntimeError):
	"""CDP / 显式浏览器来源下，该操作只有 httpx 实现，拒绝用 httpx 携带浏览器凭据发送。"""


class PlatformResponseInvalid(ValueError):
	"""平台响应无法解析成 JSON 对象（非对象响应，或深嵌套让 ``resp.json()`` 抛错）。

	同一响应重试必然复现，命令出口按 ``INTERNAL_ERROR``（recoverable=false）上报，
	不能兜成可重试的 ``NETWORK_ERROR``（Issue #1368）。子类化 ``ValueError`` 保留
	旧调用方按 ``ValueError`` 捕获的兼容性。
	"""


def _status_error_without_query(exc: httpx.HTTPStatusError) -> httpx.HTTPStatusError:
	"""重建不带查询参数的 ``HTTPStatusError``，保留类型、状态码与路径。

	GET 请求统一注入 ``__zp_stoken__`` 查询参数（``add_stoken_to_get_params``），
	httpx 的异常文本默认是 ``Client error '404 Not Found' for url '<完整 URL>'``；
	原样上抛会让 stoken 经 ``handle_auth_errors`` 的兜底进入 ``error.message``，
	写进 stdout 信封与 Agent 记录（Issue #2157）。``output.py`` 只按键名脱敏，
	扫不到消息正文里的凭证。
	"""
	request = exc.request
	url = request.url
	status = exc.response.status_code
	reason = getattr(exc.response, "reason_phrase", "") or ""
	message = f"{request.method} {url.path or '/'} 返回 HTTP {status} {reason}".rstrip()
	try:
		safe_request = httpx.Request(request.method, url.copy_with(query=None))
	except (httpx.InvalidURL, httpx.UnsupportedProtocol, ValueError):
		safe_request = request
	return httpx.HTTPStatusError(message, request=safe_request, response=exc.response)


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
		delay: tuple[float, float] = DEFAULT_REQUEST_DELAY,
		cdp_url: str | None = None,
		browser_source: str | None = None,
	) -> None:
		self._auth = auth_manager
		self._delay = delay
		self._client: httpx.Client | None = None
		self._browser_session: "BrowserSession | None" = None
		self._throttle = RequestThrottle(delay, check=self._check_cdp_risk_lock)
		data_dir = self._risk_lock_dir()
		self._request_budget = RequestBudget.for_data_dir(data_dir, interval=delay[0]) if data_dir is not None else None
		self._cdp_url = cdp_url
		# 浏览器通道来源意图；None = auto（默认路径行为不变）。
		# 只在此处保存并透传给唯一的 BrowserSession 构造点，不在本类上派生
		# 任何模式布尔——见 api/browser_source.py 的模块文档。
		self._browser_source = browser_source
		self._closed = False
		self._unpersisted_risk_lock: Any = None
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
		policy = self._resolve_policy()
		if policy.name != "auto":
			# 显式来源按各自通道白名单判定：httpx-only 是空白名单，永远为 False。
			return bool(policy.channels)
		session = getattr(self, "_browser_session", None)
		return session is not None and getattr(session, "_is_cdp", False) is True

	def _resolve_policy(self) -> "BrowserSourcePolicy":
		from boss_agent_cli.api.browser_source import resolve_policy

		return resolve_policy(getattr(self, "_browser_source", None))

	def browser_enabled(self) -> bool:
		"""本来源是否可能构造浏览器会话；``httpx-only`` 恒为 False。"""
		return bool(self._resolve_policy().channels)

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

	# ── Local platform risk latch (historical CDP file/protocol) ─────

	def _risk_lock_dir(self) -> Path | None:
		auth = getattr(self, "_auth", None)
		platform = getattr(auth, "_platform", "zhipin")
		if isinstance(platform, str) and platform != "zhipin":
			return None
		data_dir = getattr(auth, "data_dir", None)
		if isinstance(data_dir, Path):
			return data_dir
		if isinstance(data_dir, str) and data_dir:
			return Path(data_dir)
		return None

	def _check_cdp_risk_lock(self, browser: Any = None) -> None:
		"""仅查本地锁，不读 Cookie、不连接浏览器；摘要变化或换通道不能恢复。"""
		from boss_agent_cli.api import cdp_risk_lock
		from boss_agent_cli.api.client import PlatformRiskError

		data_dir = self._risk_lock_dir()
		lock = cdp_risk_lock.read_lock(data_dir) if data_dir is not None else None
		lock = lock or getattr(self, "_unpersisted_risk_lock", None)
		if lock is not None:
			raise PlatformRiskError.from_lock(lock)
		budget = getattr(self, "_request_budget", None)
		if budget is not None:
			budget.check_risk()

	def _record_cdp_risk_lock(self, browser: Any = None, *, error: "PlatformRiskError | None" = None) -> None:
		"""记录任一传输通道的风险；摘要只作诊断，写盘失败仍锁存当前实例。"""
		from boss_agent_cli.api import cdp_risk_lock

		data_dir = self._risk_lock_dir()
		platform = getattr(getattr(self, "_auth", None), "_platform", "zhipin")
		if isinstance(platform, str) and platform != "zhipin":
			return
		read_hash = getattr(browser, "current_stoken_hash", None)
		try:
			current = read_hash() if callable(read_hash) else None
		except Exception:
			current = None
		reason = "ACCOUNT_RISK" if error is not None and error.code == "ACCOUNT_RISK" else "ENVIRONMENT_RISK"
		details = error.details if error is not None else {}
		prior_lock = details.get("risk_lock", {})
		raw_code = (
			details.get("platform_code", prior_lock.get("platform_code") if isinstance(prior_lock, dict) else None)
			if error is not None
			else 37
		)
		platform_code = normalize_risk_code(raw_code)
		lock = cdp_risk_lock.RiskLock(
			stoken_sha256=current if isinstance(current, str) else None,
			locked_at=time.time(),
			cdp_url=getattr(self, "_cdp_url", None),
			reason=reason,
			platform_code=platform_code,
		)
		self._unpersisted_risk_lock = lock
		if data_dir is None:
			return
		try:
			cdp_risk_lock.write_lock(
				data_dir,
				lock.stoken_sha256,
				cdp_url=lock.cdp_url,
				now=lock.locked_at,
				reason=lock.reason,
				platform_code=lock.platform_code,
			)
			self._unpersisted_risk_lock = None
		# UnicodeError 不在 OSError 之下：记录风控的分支里任何异常逃逸都会替换
		# 原始 PlatformRiskError，把风控硬边界降级成可重试错误（Issue #1487）。
		except (OSError, UnicodeError):
			pass
		budget = getattr(self, "_request_budget", None)
		if budget is not None:
			try:
				same_root = budget.root == data_dir.resolve()
			except RuntimeError:
				# data_dir 含符号链接环时 resolve() 在 3.11 / 3.12 抛 RuntimeError：
				# 记录风控的分支不能因此替换正在传播的 PlatformRiskError，按不同
				# 目录把风控写进预算目录（fail-closed，Issue #2155）。
				same_root = False
			if not same_root:
				try:
					budget.record_risk(reason=lock.reason, platform_code=lock.platform_code)
				except (OSError, UnicodeError):
					self._unpersisted_risk_lock = lock
		if error is not None:
			error.details["risk_lock"] = {
				"locked": True,
				"reason": lock.reason,
				"platform_code": lock.platform_code,
				"explicit_resume_required": True,
				"persisted": self._unpersisted_risk_lock is None,
			}

	def _raise_platform_risk_response(self, response: dict[str, Any], *, browser: Any = None) -> None:
		from boss_agent_cli.api.client import PlatformRiskError

		try:
			raise_platform_risk_response(response, is_cdp=getattr(browser, "_is_cdp", False) is True)
		except PlatformRiskError as error:
			error.details["platform_code"] = response.get("__cli_platform_code__", int(response["code"]))
			self._record_cdp_risk_lock(browser, error=error)
			raise

	def _cdp_http_url(self) -> str:
		from boss_agent_cli.api.browser_urls import DEFAULT_CDP_URL

		return getattr(self, "_cdp_url", None) or DEFAULT_CDP_URL

	def _check_cdp_risk_lock_raw(self) -> None:
		"""裸 CDP 动作也仅检查本地锁，不为核对摘要建立任何连接。"""
		self._check_cdp_risk_lock()

	def _record_cdp_risk_lock_raw(self, *, error: "PlatformRiskError | None" = None) -> None:
		"""页面风险后直接记锁，不追加连接或 Cookie 读取。"""
		self._record_cdp_risk_lock(error=error)

	def _get_client(self) -> httpx.Client:
		self._check_cdp_risk_lock()
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
				# 真机全是 HTTP/2；HTTP/1.1 的请求在协议层就与浏览器不同。
				http2=True,
			)
		return self._client

	def _get_browser(self, *, browser_source: str | None = None) -> "BrowserSession":
		"""Return the single live browser session for the requested source.

		A source that forbids stored credentials is constructed with an empty
		cookie/UA payload without calling ``AuthManager.get_token()``. Switching
		sources closes the previous session first, so the client never keeps the
		parallel auto/existing-browser slots that #410 removed.
		"""
		self._check_cdp_risk_lock()
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
				request_budget=getattr(self, "_request_budget", None),
				request_check=self._check_cdp_risk_lock,
				identity={name: token[name] for name in PAGE_IDENTITY_HEADERS if token.get(name)},
			)
		return self._browser_session

	def _headers_for(self, url: str) -> dict[str, str]:
		return referer_header(url, self._REFERER_MAP, f"{self._BASE_URL}/")

	def _merge_cookies(self, resp: httpx.Response) -> None:
		merge_response_cookies(self._get_client(), resp)

	def _should_refresh_token_response(self, data: dict[str, Any]) -> bool:
		"""决定该响应是否应触发 token 刷新重试；子类按平台语义覆写。"""
		return data.get("code") == self._CODE_STOKEN_EXPIRED

	@contextmanager
	def _request_window(self, kind: str = "api") -> Iterator[None]:
		budget = getattr(self, "_request_budget", None)
		if budget is not None:
			with budget.attempt(kind, check=self._check_cdp_risk_lock, before_wait=self._throttle.wait):
				check_request_control(self._check_cdp_risk_lock)
				yield
		else:
			self._throttle.wait()
			check_request_control(self._check_cdp_risk_lock)
			yield

	# ── httpx request with retry (low-risk ops) ──────────────────────

	def _request(self, method: str, url: str, *, retry: bool = True, **kwargs: Any) -> dict[str, Any]:
		"""httpx 请求，循环重试（最多 _MAX_RETRIES 次）。"""
		self._check_cdp_risk_lock()
		# extra_headers overrides yaml-driven defaults from _headers_for(url); candidate
		# client never passes it, so the pop is a no-op there (behavior preserved).
		if self.is_browser_only():
			return self._request_via_browser(method, url, **kwargs)
		extra_headers_override: dict[str, str] = kwargs.pop("extra_headers", {})
		operation = endpoint_for_request(method, url)
		write_possible = operation is None or operation.effect != "read"
		max_retries = _MAX_RETRIES if retry and operation is not None and operation.replay_safe else 0
		budget = getattr(self, "_request_budget", None)
		refreshed = False
		for attempt in range(max_retries + 1):
			# 凭据在预算窗口内才读取，于是未登录时也会先记一个名额、把 next_at 推后
			# 一个 interval（默认 30 秒）：请求根本没发出，却让紧接着的下一条命令
			# 阻塞到超时并以 REQUEST_BUDGET_EXHAUSTED 失败，掩盖真正的 AUTH_REQUIRED。
			# 这里先做一次取凭据的预检（成功时命中缓存，近乎零成本）。
			self._auth.get_token()
			with self._request_window():
				client = self._get_client()
				token = self._auth.get_token()
				stoken = token.get("stoken", "")

				add_stoken_to_get_params(method, kwargs, stoken)

				# traceid 是请求级的：真实页面每个请求都换一个，不能放进客户端级默认头。
				headers = {**self._headers_for(url), "traceid": new_trace_id(), **extra_headers_override}
				check_request_control(self._check_cdp_risk_lock)
				try:
					resp = client.request(method, url, headers=headers, **kwargs)
				except Exception as error:
					from boss_agent_cli.api.client import PlatformRiskError

					if isinstance(error, PlatformRiskError):
						self._record_cdp_risk_lock(error=error)
						raise
					if (
						write_possible
						and isinstance(error, (httpx.TransportError, httpx.TooManyRedirects))
						and not isinstance(error, _UNSENT_TRANSPORT_ERRORS)
					):
						# 只有「请求可能已发出」的传输异常才落成结果未知；确定未发出的
						# 连接 / 代理 / 协议错误原样上抛，由命令出口按可重试的
						# NETWORK_ERROR 收口（Issue #2216）。
						# TooManyRedirects 是 RequestError 而非 TransportError：客户端
						# 已跟随重定向多次，写请求可能已在其中一次生效，读路径仍按
						# 普通网络错误上抛（Issue #2396）。
						return self._unconfirmed_http_response(url, "transport_error")
					if write_possible and isinstance(error, httpx.DecodingError):
						return self._unconfirmed_http_response(url, "invalid_response")
					raise
				finally:
					self._throttle.mark()
				self._merge_cookies(resp)

				# HTTP 状态和“安全验证”文案不能先覆盖风险业务码，更不能触发自动换登录通道。
				try:
					payload = resp.json()
				# ValueError 覆盖 JSONDecodeError / 超长整数；RecursionError 覆盖深嵌套。
				# 两者都是「平台响应无法解析」，同一响应重试必然复现（Issue #1368）。
				except (ValueError, RecursionError):
					payload = None
				data = payload if isinstance(payload, dict) else None
				from boss_agent_cli.api.client import AccountRiskError, PlatformRiskError

				if data is not None:
					self._raise_platform_risk_response(data)
				try:
					try:
						final_url = str(resp.url)
					except (AttributeError, RuntimeError):
						final_url = ""
					response_headers = getattr(resp, "headers", {})
					page_risk = platform_risk_page_response(
						url=final_url,
						content_type=str(response_headers.get("content-type", "")),
						body=resp.text,
					)
					if page_risk is not None:
						raise_platform_risk_response(page_risk)
					if resp.status_code == 403:
						raise AccountRiskError(
							"平台拒绝访问 (HTTP 403)，未自动刷新或重试；请在官方页面核实当前会话",
							details={"platform_code": None, "http_status": 403},
						)
				except PlatformRiskError as error:
					self._record_cdp_risk_lock(error=error)
					raise
				if resp.status_code == 401:
					return {"code": 401, "message": "平台认证已失效，请重新登录", "zpData": {}}
				if resp.status_code == 429:
					data = {
						**(data or {}),
						"code": self._CODE_RATE_LIMITED,
						"message": "HTTP 429 rate limited",
						"zpData": {},
					}
				try:
					if resp.status_code != 429:
						resp.raise_for_status()
				except httpx.HTTPStatusError as exc:
					if not write_possible:
						# 读路径原样上抛：重建异常文本，查询参数里的 __zp_stoken__ 不能
						# 随 error.message 进入信封（Issue #2157）。
						raise _status_error_without_query(exc) from None
					return self._unconfirmed_http_response(url, "http_status_error")
				if data is None:
					if write_possible:
						return self._unconfirmed_http_response(url, "invalid_response")
					raise PlatformResponseInvalid("平台响应不是 JSON 对象或嵌套过深，无法解析")
				code = data.get("code")
				limited = code in (self._CODE_RATE_LIMITED, str(self._CODE_RATE_LIMITED)) or resp.status_code == 429
				cooldown = None
				if limited:
					data["code"] = self._CODE_RATE_LIMITED
					cooldown = retry_after_seconds(response_headers.get("retry-after"))
					cooldown = max(cooldown or 0.0, min(60, 10 * (2**attempt)))
					# 服务端 Retry-After 可能是数小时甚至 HTTP-date 的未来时间：无预算
					# 分支会把它原样交给 cancellable_sleep，一条 429 就能把进程挂起
					# 数天（Issue #2234）。与 RequestBudget 的本地冷却上限同口径截断。
					cooldown = min(cooldown, _MAX_COOLDOWN_SECONDS)
					if budget is not None:
						budget.cooldown(cooldown)
			# 明确过期时每个请求只刷新一次，刷新后仍过期则交由调用方处理。
			if not refreshed and attempt < max_retries and self._should_refresh_token_response(data):
				backoff = (2**attempt) + random.uniform(0.5, 1.5)
				cancellable_sleep(backoff, check=self._check_cdp_risk_lock)
				self._auth.force_refresh(cdp_url=self._cdp_url, browser_source=self._browser_source)
				self._client = None
				refreshed = True
				continue

			# 频率限制 → 冷却重试
			if limited and attempt < max_retries:
				if budget is None:
					cancellable_sleep(cooldown or 0.0, check=self._check_cdp_risk_lock)
				continue

			if self._ADD_ENDPOINT_HINT and isinstance(data, dict):
				data.setdefault("__cli_endpoint_hint__", url)
			return cast("dict[str, Any]", data)

		raise self._AUTH_ERROR_CLS("请求失败，已达最大重试次数")

	def _unconfirmed_http_response(self, url: str, reason: str) -> dict[str, Any]:
		"""响应断裂不能证明未发生副作用；只返回脱敏原因，不暴露原请求参数。"""
		response: dict[str, Any] = {
			"code": -1,
			"message": "请求结果未确认；操作可能已生效，请先核对平台实际记录，勿直接重试",
			"zpData": {},
			"__cli_error_code__": "ACTION_UNCONFIRMED",
			"__cli_error_details__": {"outcome": "unknown", "replay_safe": False, "reason": reason},
		}
		if self._ADD_ENDPOINT_HINT:
			response["__cli_endpoint_hint__"] = url
		return response

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
