"""智联招聘内部 HTTP 客户端。

Week 2 P0 先落地只读链路：基础 httpx client + 4 个读接口，
保持 ``BossClient`` 风格的资源生命周期和重试语义。

**当前范围**：
- ``search_jobs`` / ``job_detail`` / ``recommend_jobs`` / ``user_info``
- Cookie + User-Agent + ``x-zp-client-id`` 透传
- 401/403 刷新、429 退避

**暂不包含**：
- CSRF token 获取
- greet / apply 写操作
- BrowserSession / Bridge 写通道
"""

from __future__ import annotations

import atexit
from html.parser import HTMLParser
import random
import sys
import time
import weakref
from types import TracebackType
from typing import TYPE_CHECKING, Any

import httpx

from boss_agent_cli.api.throttle import RequestThrottle

if TYPE_CHECKING:
	from boss_agent_cli.auth.manager import AuthManager


_MAX_RETRIES = 3

SEARCH_URL = "https://fe-api.zhaopin.com/api/c/salesman-search/v2"
DETAIL_URL_TEMPLATE = "https://fe-api.zhaopin.com/api/c/jobs/{job_id}/info"
RECOMMEND_URL = "https://fe-api.zhaopin.com/api/c/recom-position"
USER_INFO_URL = "https://i.zhaopin.com/api/c/account/profile"
CSRF_BOOTSTRAP_URL = "https://www.zhaopin.com/"
GREET_URL = "https://fe-api.zhaopin.com/c/i/liaoliao/startConversation"
APPLY_URL = "https://fe-api.zhaopin.com/c/i/sou/deliverPosition"

_DEFAULT_HEADERS: dict[str, str] = {
	"Accept": "application/json, text/plain, */*",
	"X-Requested-With": "XMLHttpRequest",
	"Referer": "https://www.zhaopin.com/",
}

_REFERER_MAP: dict[str, str] = {
	SEARCH_URL: "https://sou.zhaopin.com/",
	RECOMMEND_URL: "https://www.zhaopin.com/",
	USER_INFO_URL: "https://i.zhaopin.com/",
}


# atexit safeguard：类比 BossClient 的管理方式
_OPEN_CLIENTS: weakref.WeakSet["ZhilianClient"] = weakref.WeakSet()


class _CsrfMetaParser(HTMLParser):
	"""从 HTML meta 标签中提取 csrf-token。"""

	def __init__(self) -> None:
		super().__init__()
		self.csrf_token: str | None = None

	def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
		if tag.lower() != "meta" or self.csrf_token:
			return
		attr_map = {key.lower(): value for key, value in attrs}
		if attr_map.get("name") == "csrf-token" and attr_map.get("content"):
			self.csrf_token = attr_map["content"]


def _extract_csrf_token(html: str) -> str | None:
	parser = _CsrfMetaParser()
	parser.feed(html)
	return parser.csrf_token


def _close_open_clients() -> None:
	for client in list(_OPEN_CLIENTS):
		try:
			client.close()
		except Exception:
			pass


atexit.register(_close_open_clients)


class ZhilianClient:
	"""智联招聘内部 HTTP 客户端骨架。

	签名对齐 ``BossClient``，Week 2 填充真实实现。
	"""

	def __init__(
		self,
		auth_manager: "AuthManager",
		*,
		delay: tuple[float, float] = (1.5, 3.0),
		cdp_url: str | None = None,
	) -> None:
		self._auth = auth_manager
		self._delay = delay
		self._cdp_url = cdp_url
		self._client: httpx.Client | None = None
		self._csrf_token: str | None = None
		self._throttle = RequestThrottle(delay)
		self._closed = False
		_OPEN_CLIENTS.add(self)

	def _get_client(self) -> httpx.Client:
		if self._client is None:
			token = self._auth.get_token()
			headers = dict(_DEFAULT_HEADERS)
			if ua := token.get("user_agent"):
				headers["User-Agent"] = str(ua)
			if client_id := token.get("x_zp_client_id") or token.get("client_id"):
				headers["x-zp-client-id"] = str(client_id)
			if sys.platform == "win32":
				headers["sec-ch-ua-platform"] = '"Windows"'
			elif sys.platform == "linux":
				headers["sec-ch-ua-platform"] = '"Linux"'
			else:
				headers["sec-ch-ua-platform"] = '"macOS"'
			self._client = httpx.Client(
				cookies=token.get("cookies", {}),
				headers=headers,
				follow_redirects=True,
				timeout=30,
			)
		return self._client

	def _headers_for(self, url: str) -> dict[str, str]:
		return {"Referer": _REFERER_MAP.get(url, "https://www.zhaopin.com/")}

	def _fetch_csrf_token(self) -> str:
		for attempt in range(_MAX_RETRIES + 1):
			client = self._get_client()
			self._throttle.wait()
			resp = client.get(
				CSRF_BOOTSTRAP_URL,
				headers={"Referer": CSRF_BOOTSTRAP_URL, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"},
			)
			self._throttle.mark()
			self._merge_cookies(resp)

			if resp.status_code in (401, 403) and attempt < _MAX_RETRIES:
				backoff = (2 ** attempt) + random.uniform(0.3, 0.9)
				time.sleep(backoff)
				self._auth.force_refresh(cdp_url=self._cdp_url)
				self._client = None
				self._csrf_token = None
				continue

			resp.raise_for_status()
			token = _extract_csrf_token(resp.text)
			if token:
				return token
			raise RuntimeError("智联页面未找到 csrf-token")

		raise RuntimeError("智联 csrf-token 获取失败，已达最大重试次数")

	def _merge_cookies(self, resp: httpx.Response) -> None:
		for name, value in resp.cookies.items():
			if value:
				self._get_client().cookies.set(name, value)

	def _request(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
		for attempt in range(_MAX_RETRIES + 1):
			client = self._get_client()
			self._throttle.wait()
			extra_headers = kwargs.pop("headers", {})
			headers = {**self._headers_for(url), **extra_headers}
			resp = client.request(method, url, headers=headers, **kwargs)
			self._throttle.mark()
			self._merge_cookies(resp)

			status_code = resp.status_code
			if status_code in (401, 403) and attempt < _MAX_RETRIES:
				backoff = (2 ** attempt) + random.uniform(0.3, 0.9)
				time.sleep(backoff)
				self._auth.force_refresh(cdp_url=self._cdp_url)
				self._client = None
				continue
			if status_code == 429 and attempt < _MAX_RETRIES:
				time.sleep(min(30, 5 * (2 ** attempt)))
				continue

			resp.raise_for_status()
			data = resp.json()
			if not isinstance(data, dict):
				raise RuntimeError("智联响应格式异常：期望 JSON object")
			code = data.get("code")
			if code in (401, 403) and attempt < _MAX_RETRIES:
				backoff = (2 ** attempt) + random.uniform(0.3, 0.9)
				time.sleep(backoff)
				self._auth.force_refresh(cdp_url=self._cdp_url)
				self._client = None
				continue
			if code == 429 and attempt < _MAX_RETRIES:
				time.sleep(min(30, 5 * (2 ** attempt)))
				continue
			return data

		raise RuntimeError("智联请求失败，已达最大重试次数")

	# ── 资源生命周期 ───────────────────────────────

	def close(self) -> None:
		"""释放底层资源。"""
		if self._closed:
			return
		self._closed = True
		if self._client is not None:
			self._client.close()
			self._client = None
		_OPEN_CLIENTS.discard(self)

	def __enter__(self) -> "ZhilianClient":
		return self

	def __exit__(
		self,
		exc_type: type[BaseException] | None,
		exc_val: BaseException | None,
		exc_tb: TracebackType | None,
	) -> None:
		self.close()

	# ── P0 只读 ────────────────────────────────────

	def get_csrf_token(self, *, force_refresh: bool = False) -> str:
		"""获取并缓存智联写操作所需的 csrf-token。"""
		if force_refresh or not self._csrf_token:
			self._csrf_token = self._fetch_csrf_token()
		return self._csrf_token

	def greet(self, security_id: str, job_id: str, message: str = "") -> dict[str, Any]:
		payload: dict[str, Any] = {
			"securityId": security_id,
			"jobId": job_id,
		}
		if message:
			payload["message"] = message
		return self._request(
			"POST",
			GREET_URL,
			data=payload,
			headers={"csrf-token": self.get_csrf_token()},
		)

	def apply(self, security_id: str, job_id: str, lid: str = "") -> dict[str, Any]:
		payload: dict[str, Any] = {
			"securityId": security_id,
			"jobId": job_id,
		}
		if lid:
			payload["lid"] = lid
		return self._request(
			"POST",
			APPLY_URL,
			data=payload,
			headers={"csrf-token": self.get_csrf_token()},
		)

	def search_jobs(self, query: str, **filters: Any) -> dict[str, Any]:
		params: dict[str, Any] = {
			"keyword": query,
			"pageNum": filters.get("page", 1),
		}
		if page_size := filters.get("page_size"):
			params["pageSize"] = page_size
		filter_map = {
			"city": "cityId",
			"salary": "salary",
			"experience": "workExp",
			"education": "education",
			"scale": "companySize",
			"industry": "industry",
			"stage": "financingStage",
			"job_type": "jobType",
		}
		for source_key, target_key in filter_map.items():
			if value := filters.get(source_key):
				params[target_key] = value
		return self._request("GET", SEARCH_URL, params=params)

	def job_detail(self, job_id: str) -> dict[str, Any]:
		return self._request("GET", DETAIL_URL_TEMPLATE.format(job_id=job_id))

	def recommend_jobs(self, page: int = 1) -> dict[str, Any]:
		return self._request("GET", RECOMMEND_URL, params={"pageNum": page})

	def user_info(self) -> dict[str, Any]:
		return self._request("GET", USER_INFO_URL)
