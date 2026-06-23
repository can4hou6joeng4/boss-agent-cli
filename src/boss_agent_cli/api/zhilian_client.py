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
import time
import weakref
from types import TracebackType
from typing import TYPE_CHECKING, Any

import httpx

from boss_agent_cli.api.endpoints_loader import get_zhilian_spec
from boss_agent_cli.api.httpx_helpers import browser_headers, merge_response_cookies, referer_header
from boss_agent_cli.api.throttle import RequestThrottle

if TYPE_CHECKING:
	from boss_agent_cli.auth.manager import AuthManager


_MAX_RETRIES = 3

_SPEC = get_zhilian_spec()
SEARCH_URL = _SPEC.endpoints["search"].url
DETAIL_URL_TEMPLATE = _SPEC.endpoints["detail"].url
RECOMMEND_URL = _SPEC.endpoints["recommend"].url
USER_INFO_URL = _SPEC.endpoints["user_info"].url
CSRF_BOOTSTRAP_URL = _SPEC.endpoints["csrf_bootstrap"].url
GREET_URL = _SPEC.endpoints["greet"].url
APPLY_URL = _SPEC.endpoints["apply"].url
JOB_CARD_URL_TEMPLATE = _SPEC.endpoints["job_card"].url
JOB_HISTORY_URL = _SPEC.endpoints["job_history"].url
DELIVER_LIST_URL = _SPEC.endpoints["deliver_list"].url
RESUME_BASEINFO_URL = _SPEC.endpoints["resume_baseinfo"].url
RESUME_EXPECT_URL = _SPEC.endpoints["resume_expect"].url
INTERVIEW_DATA_URL = _SPEC.endpoints["interview_data"].url
_DEFAULT_HEADERS: dict[str, str] = dict(_SPEC.default_headers)
_REFERER_MAP: dict[str, str] = {ep.url: ep.referer for ep in _SPEC.endpoints.values()}


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
			headers = browser_headers(_DEFAULT_HEADERS, token, include_client_id=True, default_platform="macOS")
			self._client = httpx.Client(
				cookies=token.get("cookies", {}),
				headers=headers,
				follow_redirects=True,
				timeout=30,
			)
		return self._client

	def _headers_for(self, url: str) -> dict[str, str]:
		return referer_header(url, _REFERER_MAP, f"{_SPEC.base_url}/")

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
		merge_response_cookies(self._get_client(), resp)

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
		if raw_params := filters.get("raw_params"):
			params.update(raw_params)
		if page_size := filters.get("page_size"):
			params["pageSize"] = page_size
		filter_map = {
			"city": ("cityId", "city_code"),
			"salary": ("salary", "salary_code"),
			"experience": ("workExp", "experience_code"),
			"education": ("education", "education_code"),
			"degree": ("education", "degree_code"),
			"scale": ("companySize", "scale_code"),
			"industry": ("industry", "industry_code"),
			"stage": ("financingStage", "stage_code"),
			"job_type": ("jobType", "job_type_code"),
		}
		for source_key, (target_key, code_key) in filter_map.items():
			value = filters.get(code_key) or filters.get(source_key)
			if value:
				params[target_key] = value
		return self._request("GET", SEARCH_URL, params=params)

	def job_detail(self, job_id: str) -> dict[str, Any]:
		return self._request("GET", DETAIL_URL_TEMPLATE.format(job_id=job_id))

	def recommend_jobs(self, page: int = 1) -> dict[str, Any]:
		return self._request("GET", RECOMMEND_URL, params={"pageNum": page})

	def user_info(self) -> dict[str, Any]:
		return self._request("GET", USER_INFO_URL)

	def job_card(self, security_id: str, lid: str = "") -> dict[str, Any]:
		params: dict[str, Any] = {}
		if lid:
			params["lid"] = lid
		return self._request("GET", JOB_CARD_URL_TEMPLATE.format(security_id=security_id), params=params)

	def job_history(self, page: int = 1) -> dict[str, Any]:
		return self._request("GET", JOB_HISTORY_URL, params={"pageNum": page})

	def deliver_list(self, page: int = 1) -> dict[str, Any]:
		return self._request("GET", DELIVER_LIST_URL, params={"pageNum": page})

	def resume_baseinfo(self) -> dict[str, Any]:
		return self._request("GET", RESUME_BASEINFO_URL)

	def resume_expect(self) -> dict[str, Any]:
		return self._request("GET", RESUME_EXPECT_URL)

	def interview_data(self) -> dict[str, Any]:
		return self._request("GET", INTERVIEW_DATA_URL)
