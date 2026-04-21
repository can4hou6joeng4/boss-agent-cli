"""Recruiter-side API client.

Dual-channel like BossClient: httpx for low-risk reads, browser for high-risk writes.
Endpoints sourced from newboss/boss-cli project (confirmed via reverse engineering).
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

	# ── 候选人列表与筛选 ────────────────────────────────

	def friend_list(self, page: int = 1, label_id: int = 0, job_id: str | None = None) -> dict[str, Any]:
		data: dict[str, Any] = {"labelId": label_id, "page": page}
		if job_id:
			data["encJobId"] = job_id
		return self._request("POST", ep.BOSS_FRIEND_LIST_URL, data=data)

	def friend_detail(self, friend_ids: list[int]) -> dict[str, Any]:
		data = {"friendIds": ",".join(str(i) for i in friend_ids)}
		return self._request("POST", ep.BOSS_FRIEND_DETAIL_URL, data=data)

	def friend_labels(self) -> dict[str, Any]:
		return self._request("GET", ep.BOSS_FRIEND_LABELS_URL)

	# ── 打招呼 / 新招呼列表 ──────────────────────────────

	def greet_list(self, page: int = 1, job_id: str | None = None) -> dict[str, Any]:
		params: dict[str, Any] = {"page": page}
		if job_id:
			params["encJobId"] = job_id
		return self._request("GET", ep.BOSS_GREET_LIST_URL, params=params)

	def greet_rec_list(self, page: int = 1, job_id: str | None = None) -> dict[str, Any]:
		params: dict[str, Any] = {"page": page}
		if job_id:
			params["encJobId"] = job_id
		return self._request("GET", ep.BOSS_GREET_REC_LIST_URL, params=params)

	# ── 候选人搜索与简历 ──────────────────────────────────

	def search_geeks(self, query: str, *, city: str | None = None, page: int = 1, job_id: str | None = None, experience: str | None = None, degree: str | None = None) -> dict[str, Any]:
		params: dict[str, Any] = {"query": query, "page": page}
		if city:
			params["city"] = city
		if job_id:
			params["encryptJobId"] = job_id
		if experience:
			params["experience"] = experience
		if degree:
			params["degree"] = degree
		return self._request("GET", ep.BOSS_SEARCH_GEEK_URL, params=params)

	def view_geek(self, geek_id: str, job_id: str, security_id: str | None = None) -> dict[str, Any]:
		params: dict[str, Any] = {"encryptGeekId": geek_id, "encryptJobId": job_id}
		if security_id:
			params["securityId"] = security_id
		return self._request("GET", ep.BOSS_VIEW_GEEK_URL, params=params)

	def chat_geek_info(self, geek_id: str, security_id: str, job_id: int) -> dict[str, Any]:
		params = {"encryptGeekId": geek_id, "securityId": security_id, "jobId": job_id}
		return self._request("GET", ep.BOSS_CHAT_GEEK_INFO_URL, params=params)

	# ── 消息 / 聊天 ──────────────────────────────────────

	def last_messages(self, friend_ids: list[int]) -> dict[str, Any]:
		data = {"friendIds": ",".join(str(i) for i in friend_ids), "src": 0}
		return self._request("POST", ep.BOSS_LAST_MESSAGES_URL, data=data)

	def chat_history(self, gid: int, *, count: int = 20, max_msg_id: int | None = None) -> dict[str, Any]:
		params: dict[str, Any] = {"gid": gid, "c": count, "src": 0}
		if max_msg_id:
			params["maxMsgId"] = max_msg_id
		return self._request("GET", ep.BOSS_CHAT_HISTORY_URL, params=params)

	def send_message(self, gid: int, content: str) -> dict[str, Any]:
		data = {"gid": gid, "content": content}
		return self._browser_request("POST", ep.BOSS_SEND_MESSAGE_URL, data=data)

	def session_enter(self, geek_id: str, expect_id: str, job_id: str, security_id: str) -> dict[str, Any]:
		data = {"geekId": geek_id, "expectId": expect_id, "jobId": job_id, "securityId": security_id}
		return self._browser_request("POST", ep.BOSS_SESSION_ENTER_URL, data=data)

	# ── 职位管理 ──────────────────────────────────────────

	def list_jobs(self) -> dict[str, Any]:
		return self._request("GET", ep.BOSS_JOB_LIST_URL)

	def job_offline(self, job_id: str) -> dict[str, Any]:
		data = {"encryptJobId": job_id}
		return self._browser_request("POST", ep.BOSS_JOB_OFFLINE_URL, data=data)

	def job_online(self, job_id: str) -> dict[str, Any]:
		data = {"encryptJobId": job_id}
		return self._browser_request("POST", ep.BOSS_JOB_ONLINE_URL, data=data)

	# ── 交换联系方式（手机/微信/简历）─────────────────────

	def exchange_request(self, exchange_type: int, uid: int, job_id: int, gid: int) -> dict[str, Any]:
		data = {"type": exchange_type, "uid": uid, "jobId": job_id, "gid": gid}
		return self._browser_request("POST", ep.BOSS_EXCHANGE_REQUEST_URL, data=data)

	def exchange_content(self, uid: int) -> dict[str, Any]:
		data = {"uid": uid}
		return self._request("POST", ep.BOSS_EXCHANGE_CONTENT_URL, data=data)

	# ── 面试 ──────────────────────────────────────────────

	def interview_list(self) -> dict[str, Any]:
		return self._request("GET", ep.BOSS_INTERVIEW_LIST_URL)

	def interview_invite(self, geek_id: str, job_id: str, security_id: str, **kwargs: Any) -> dict[str, Any]:
		data: dict[str, Any] = {"encryptGeekId": geek_id, "encryptJobId": job_id, "securityId": security_id}
		data.update(kwargs)
		return self._browser_request("POST", ep.BOSS_INTERVIEW_INVITE_URL, data=data)

	# ── 候选人操作 ────────────────────────────────────────

	def mark_unsuitable(self, geek_id: str, job_id: str) -> dict[str, Any]:
		data = {"encryptGeekId": geek_id, "encryptJobId": job_id}
		return self._browser_request("POST", ep.BOSS_MARK_UNSUITABLE_URL, data=data)

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
