"""Recruiter-side API client.

Dual-channel like BossClient: httpx for low-risk reads, browser for high-risk writes.
Shares BrowserSession pattern and RequestThrottle with BossClient.
"""
import atexit
import random
import time
import weakref
from types import TracebackType
from typing import TYPE_CHECKING, Any, cast

import httpx

from boss_agent_cli.api import recruiter_endpoints as ep
from boss_agent_cli.api.throttle import RequestThrottle

if TYPE_CHECKING:
	from boss_agent_cli.api.browser_client import BrowserSession
	from boss_agent_cli.auth.manager import AuthManager

_MAX_RETRIES = 3

_OPEN_CLIENTS: weakref.WeakSet["BossRecruiterClient"] = weakref.WeakSet()


def _close_open_clients() -> None:
	for client in list(_OPEN_CLIENTS):
		try:
			client.close()
		except Exception:
			pass


atexit.register(_close_open_clients)


class RecruiterAuthError(Exception):
	pass


class BossRecruiterClient:
	"""Recruiter-side hybrid API client."""

	def __init__(self, auth_manager: "AuthManager", *, delay: tuple[float, float] = (1.5, 3.0), cdp_url: str | None = None) -> None:
		self._auth = auth_manager
		self._delay = delay
		self._client: httpx.Client | None = None
		self._browser_session: "BrowserSession | None" = None
		self._throttle = RequestThrottle(delay)
		self._cdp_url = cdp_url
		self._closed = False
		_OPEN_CLIENTS.add(self)

	def _get_client(self) -> httpx.Client:
		if self._client is None:
			token = self._auth.get_token()
			headers = dict(ep.DEFAULT_HEADERS)
			if ua := token.get("user_agent"):
				headers["User-Agent"] = ua
			import sys
			if sys.platform == "win32":
				headers["sec-ch-ua-platform"] = '"Windows"'
			elif sys.platform == "linux":
				headers["sec-ch-ua-platform"] = '"Linux"'
			self._client = httpx.Client(
				base_url=ep.BASE_URL,
				cookies=token.get("cookies", {}),
				headers=headers,
				follow_redirects=True,
				timeout=30,
			)
		return self._client

	def _get_browser(self) -> "BrowserSession":
		if self._browser_session is None:
			from boss_agent_cli.api.browser_client import BrowserSession
			token = self._auth.get_token()
			self._browser_session = BrowserSession(
				cookies=token.get("cookies", {}),
				user_agent=token.get("user_agent", ""),
				delay=self._delay,
				cdp_url=self._cdp_url,
				logger=getattr(self._auth, '_logger', None),
			)
		return self._browser_session

	def _headers_for(self, url: str) -> dict[str, str]:
		referer = ep.REFERER_MAP.get(url, f"{ep.BASE_URL}/")
		return {"Referer": referer}

	def _merge_cookies(self, resp: httpx.Response) -> None:
		for name, value in resp.cookies.items():
			if value:
				self._get_client().cookies.set(name, value)

	def _request(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
		"""httpx request with retry loop."""
		for attempt in range(_MAX_RETRIES + 1):
			client = self._get_client()
			token = self._auth.get_token()
			stoken = token.get("stoken", "")

			if method == "GET":
				params = kwargs.get("params", {})
				params["__zp_stoken__"] = stoken
				kwargs["params"] = params

			self._throttle.wait()

			extra_headers = self._headers_for(url)
			resp = client.request(method, url, headers=extra_headers, **kwargs)
			self._throttle.mark()
			self._merge_cookies(resp)

			if resp.status_code == 403 or "安全验证" in resp.text:
				if attempt >= _MAX_RETRIES:
					raise RecruiterAuthError("Token 刷新后仍被拒绝，请重新登录")
				backoff = (2 ** attempt) + random.uniform(0.5, 1.5)
				time.sleep(backoff)
				self._auth.force_refresh(cdp_url=self._cdp_url)
				self._client = None
				continue

			resp.raise_for_status()
			data = resp.json()
			code = data.get("code")

			if code == ep.CODE_STOKEN_EXPIRED and attempt < _MAX_RETRIES:
				backoff = (2 ** attempt) + random.uniform(0.5, 1.5)
				time.sleep(backoff)
				self._auth.force_refresh(cdp_url=self._cdp_url)
				self._client = None
				continue

			if code == ep.CODE_RATE_LIMITED and attempt < _MAX_RETRIES:
				cooldown = min(60, 10 * (2 ** attempt))
				time.sleep(cooldown)
				continue

			return cast("dict[str, Any]", data)

		raise RecruiterAuthError("请求失败，已达最大重试次数")

	def _browser_request(self, method: str, url: str, *, params: dict[str, Any] | None = None, data: dict[str, Any] | None = None) -> dict[str, Any]:
		return self._get_browser().request(method, url, params=params, data=data)

	# ── Public API ───────────────────────────────────────────────────

	# Application management (high-risk → browser)
	def list_applications(self, job_id: str | None = None, status: str | None = None, keyword: str | None = None, page: int = 1) -> dict[str, Any]:
		params: dict[str, Any] = {"page": page}
		if job_id:
			params["jobId"] = job_id
		if status:
			params["status"] = status
		if keyword:
			params["keyword"] = keyword
		return self._browser_request("GET", ep.BOSS_RECOMMEND_GEEKS_URL, params=params)

	def application_detail(self, application_id: str) -> dict[str, Any]:
		params = {"id": application_id}
		return self._request("GET", ep.BOSS_RECOMMEND_GEEKS_URL, params=params)

	# Resume (low-risk → httpx, high-risk request → browser)
	def get_resume(self, geek_id: str, security_id: str) -> dict[str, Any]:
		params = {"geekId": geek_id, "securityId": security_id}
		return self._request("GET", ep.BOSS_GEEK_RESUME_URL, params=params)

	def request_resume(self, geek_id: str) -> dict[str, Any]:
		data = {"geekId": geek_id}
		return self._browser_request("POST", ep.BOSS_REQUEST_RESUME_URL, data=data)

	# Job management (low-risk → httpx, high-risk → browser)
	def list_jobs(self, page: int = 1) -> dict[str, Any]:
		params = {"page": page}
		return self._request("GET", ep.BOSS_JOB_LIST_URL, params=params)

	def job_detail(self, job_id: str) -> dict[str, Any]:
		params = {"jobId": job_id}
		return self._request("GET", ep.BOSS_JOB_DETAIL_URL, params=params)

	def create_job(self, **params: Any) -> dict[str, Any]:
		return self._browser_request("POST", ep.BOSS_JOB_PUBLISH_URL, data=params)

	def update_job(self, job_id: str, **params: Any) -> dict[str, Any]:
		params["jobId"] = job_id
		return self._browser_request("POST", ep.BOSS_JOB_EDIT_URL, data=params)

	def close_job(self, job_id: str) -> dict[str, Any]:
		data = {"jobId": job_id}
		return self._browser_request("POST", ep.BOSS_JOB_CLOSE_URL, data=data)

	# Chat (recruiter-side)
	def friend_list(self, page: int = 1) -> dict[str, Any]:
		params = {"page": page}
		return self._request("GET", ep.BOSS_FRIEND_LIST_URL, params=params)

	def chat_history(self, friend_id: str, page: int = 1) -> dict[str, Any]:
		params = {"friendId": friend_id, "page": page}
		return self._request("GET", ep.BOSS_CHAT_HISTORY_URL, params=params)

	def send_message(self, friend_id: str, message: str) -> dict[str, Any]:
		data = {"friendId": friend_id, "message": message}
		return self._browser_request("POST", ep.BOSS_SEND_MESSAGE_URL, data=data)

	# ── Lifecycle ────────────────────────────────────────────────────

	def close(self) -> None:
		if self._closed:
			return
		self._closed = True
		if self._browser_session:
			self._browser_session.close()
			self._browser_session = None
		if self._client:
			self._client.close()
			self._client = None
		_OPEN_CLIENTS.discard(self)

	def __enter__(self) -> "BossRecruiterClient":
		return self

	def __exit__(
		self,
		exc_type: type[BaseException] | None,
		exc_val: BaseException | None,
		exc_tb: TracebackType | None,
	) -> None:
		self.close()
