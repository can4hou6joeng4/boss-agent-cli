import atexit
from typing import Any

from boss_agent_cli.api import endpoints
from boss_agent_cli.api._base_client import _BaseHttpClient
from boss_agent_cli.api.httpx_helpers import make_client_registry
from boss_agent_cli.api.zhipin_errors import classify_code_37, response_message

# atexit safeguard: close any BossClient instances not explicitly closed
_OPEN_CLIENTS, _close_open_clients = make_client_registry()


atexit.register(_close_open_clients)


class AuthError(Exception):
	pass


class PlatformRiskError(Exception):
	"""平台风控类异常的共同基类：命中即终止，不重试、不换通道、不继续下一项。

	执行器（福利筛选线程池、batch-greet、wizard runner）统一按本基类捕获，
	新增一个风控码只需在此登记子类，不必逐个执行器加分支（Issue #419）。
	``code`` 是信封里的稳定错误码，与 ``SCHEMA_DATA["error_codes"]`` 一一对应。
	"""

	code: str = "ACCOUNT_RISK"

	def __init__(self, message: str = "", is_cdp: bool = False):
		self.is_cdp = is_cdp
		super().__init__(message)


class AccountRiskError(PlatformRiskError):
	"""BOSS 直聘风控拦截（code 36）：检测到异常行为。"""

	code = "ACCOUNT_RISK"


class EnvironmentRiskError(PlatformRiskError):
	"""BOSS 直聘访问环境风控（code 37），不等同于登录过期。"""

	code = "ENVIRONMENT_RISK"


#: 风控错误码 → 异常类。平台 adapter 的 ``parse_error`` 走响应字典路径时返回的是码，
#: 执行器用这张表把码升格为异常，与浏览器通道抛出的异常形态走同一条终止分支。
RISK_ERROR_BY_CODE: dict[str, type[PlatformRiskError]] = {
	AccountRiskError.code: AccountRiskError,
	EnvironmentRiskError.code: EnvironmentRiskError,
}


class BossClient(_BaseHttpClient):
	"""Hybrid API client: browser channel for high-risk ops, httpx for low-risk ops."""

	_BASE_URL = endpoints.BASE_URL
	_DEFAULT_HEADERS = endpoints.DEFAULT_HEADERS
	_REFERER_MAP = endpoints.REFERER_MAP
	_AUTH_ERROR_CLS = AuthError
	_CODE_STOKEN_EXPIRED = endpoints.CODE_STOKEN_EXPIRED
	_CODE_RATE_LIMITED = endpoints.CODE_RATE_LIMITED

	def _register(self) -> None:
		_OPEN_CLIENTS.add(self)

	def _unregister(self) -> None:
		_OPEN_CLIENTS.discard(self)

	def _should_refresh_token_response(self, data: dict[str, Any]) -> bool:
		return data.get("code") == endpoints.CODE_STOKEN_EXPIRED and classify_code_37(data) == "token_expired"

	# ── Browser request (high-risk ops) ──────────────────────────────

	def _browser_request(
		self,
		method: str,
		url: str,
		*,
		params: dict[str, Any] | None = None,
		data: dict[str, Any] | None = None,
		browser_source: str | None = None,
	) -> dict[str, Any]:
		# 单次请求，不重试：浏览器通道的 stoken 由页面 JS 生成，force_refresh 拿不到新凭证，
		# 重试只是对已被风控拦截的高风险端点再打一次（见 docs/research/platforms/zhipin.md 红线）。
		browser = self._get_browser(browser_source=browser_source)
		result = browser.request(method, url, params=params, data=data)
		code = result.get("code")
		is_cdp = getattr(browser, "_is_cdp", False)
		mode = "CDP" if is_cdp else ("Bridge" if getattr(browser, "_is_bridge", False) else "headless patchright")
		if code == endpoints.CODE_ACCOUNT_RISK:
			msg = response_message(result) or "账户存在异常行为"
			raise AccountRiskError(
				f"BOSS 直聘风控拦截 (code {code}): {msg}。"
				f"当前浏览器模式: {mode}。"
				f"建议：停止自动化访问并回到 BOSS 直聘官方页面手动处理。",
				is_cdp=is_cdp,
			)
		if code == endpoints.CODE_STOKEN_EXPIRED and classify_code_37(result) == "environment_risk":
			msg = response_message(result) or "未知 code 37 响应"
			raise EnvironmentRiskError(
				f"BOSS 直聘访问环境风控 (code {code}): {msg}。"
				f"当前浏览器模式: {mode}。已停止且未刷新或重试；"
				"请保留当前专用 profile，在官方页面确认后降低访问频率。",
				is_cdp=is_cdp,
			)
		return result

	@staticmethod
	def _bridge_is_connected() -> bool:
		"""Treat an attached extension as slice 1's existing-browser intent."""
		from boss_agent_cli.bridge.client import BridgeClient

		status = BridgeClient().status()
		return bool(status and status.get("extensionConnected"))

	def _read_request(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
		"""Use an existing browser only when explicitly configured or Bridge is connected.

		The normal no-Bridge path keeps the established httpx semantics, including
		``AUTH_REQUIRED + boss login`` when no stored token exists. The browser path
		does not inherit httpx's stoken refresh/rate-limit retry loop; it is selected
		only for these user-triggered read calls.
		"""
		from boss_agent_cli.api.browser_source import resolve_policy

		configured = resolve_policy(self._browser_source)
		if configured.name != "auto":
			return self._browser_request(method, url, browser_source=configured.name, **kwargs)
		if self._bridge_is_connected():
			return self._browser_request(method, url, browser_source="existing-browser", **kwargs)
		return self._request(method, url, **kwargs)

	# ── Public API ───────────────────────────────────────────────────
	# High-risk: search, recommend, greet, job_card → browser channel
	# Low-risk: status, me, cities, schema, detail → httpx channel.
	# friend_list/chat_history select an attached existing-browser read channel
	# when Bridge is connected; otherwise they retain the httpx retry semantics.

	def search_jobs(self, query: str, **filters: Any) -> dict[str, Any]:
		params: dict[str, Any] = {"query": query, "page": filters.get("page", 1)}
		if raw_params := filters.get("raw_params"):
			params.update(raw_params)
		if city := filters.get("city"):
			code = endpoints.CITY_CODES.get(city)
			if code is None:
				raise ValueError(f"未知城市: {city}")
			params["city"] = code
		if salary := filters.get("salary"):
			code = filters.get("salary_code") or endpoints.SALARY_CODES.get(salary)
			if code:
				params["salary"] = code
		if exp := filters.get("experience"):
			code = filters.get("experience_code") or endpoints.EXPERIENCE_CODES.get(exp)
			if code:
				params["experience"] = code
		if edu := filters.get("education"):
			code = filters.get("education_code") or endpoints.EDUCATION_CODES.get(edu)
			if code:
				params["degree"] = code
		if scale := filters.get("scale"):
			code = filters.get("scale_code") or endpoints.SCALE_CODES.get(scale)
			if code:
				params["scale"] = code
		if industry := filters.get("industry"):
			code = filters.get("industry_code") or endpoints.INDUSTRY_CODES.get(industry)
			if code:
				params["industry"] = code
		if stage := filters.get("stage"):
			code = filters.get("stage_code") or endpoints.STAGE_CODES.get(stage)
			if code:
				params["stage"] = code
		if job_type := filters.get("job_type"):
			code = filters.get("job_type_code") or endpoints.JOB_TYPE_CODES.get(job_type)
			if code:
				params["jobType"] = code
		return self._browser_request("GET", endpoints.SEARCH_URL, params=params)

	def recommend_jobs(self, page: int = 1) -> dict[str, Any]:
		params = {"page": page}
		return self._browser_request("GET", endpoints.RECOMMEND_URL, params=params)

	def greet(self, security_id: str, job_id: str, message: str = "") -> dict[str, Any]:
		data = {
			"securityId": security_id,
			"jobId": job_id,
			"greeting": message or "您好，我对该岗位很感兴趣，希望能和您聊一聊。",
		}
		return self._browser_request("POST", endpoints.GREET_URL, data=data)

	def apply(self, security_id: str, job_id: str, lid: str = "") -> dict[str, Any]:
		"""Current minimal apply path - reuses the immediate-chat browser endpoint."""
		data = {
			"securityId": security_id,
			"jobId": job_id,
		}
		if lid:
			data["lid"] = lid
		return self._browser_request("POST", endpoints.GREET_URL, data=data)

	def job_card(self, security_id: str, lid: str = "") -> dict[str, Any]:
		"""httpx 优先 + 浏览器降级获取职位卡片信息。"""
		try:
			return self.job_card_httpx(security_id, lid)
		except Exception:
			pass
		params = {"securityId": security_id, "lid": lid}
		return self._browser_request("GET", endpoints.JOB_CARD_URL, params=params)

	def job_card_httpx(self, security_id: str, lid: str = "") -> dict[str, Any]:
		"""通过 httpx 通道获取职位卡片信息（低延迟）。"""
		params = {"securityId": security_id, "lid": lid}
		return self._request("GET", endpoints.JOB_CARD_URL, params=params)

	def job_card_browser(self, security_id: str, lid: str = "") -> dict[str, Any]:
		"""经浏览器通道获取职位卡片信息（强制浏览器，不走 httpx）。

		与 job_card() 的区别：job_card() httpx 优先，仅 httpx 抛异常才降级浏览器；
		而部分失败以响应字典返回时不抛异常，导致 httpx 通道拿不到完整 JD。
		job_card_browser() 跳过 httpx，直接走浏览器通道。
		"""
		params = {"securityId": security_id, "lid": lid}
		return self._browser_request("GET", endpoints.JOB_CARD_URL, params=params)

	# ── Low-risk: httpx channel ──────────────────────────────────────

	def job_detail(self, job_id: str) -> dict[str, Any]:
		params = {"encryptJobId": job_id}
		return self._request("GET", endpoints.DETAIL_URL, params=params)

	def user_info(self) -> dict[str, Any]:
		return self._request("GET", endpoints.USER_INFO_URL)

	def resume_baseinfo(self) -> dict[str, Any]:
		return self._request("GET", endpoints.RESUME_BASEINFO_URL)

	def resume_expect(self) -> dict[str, Any]:
		return self._request("GET", endpoints.RESUME_EXPECT_URL)

	def deliver_list(self, page: int = 1) -> dict[str, Any]:
		params = {"page": page}
		return self._request("GET", endpoints.DELIVER_LIST_URL, params=params)

	def job_favorites(self, page: int = 1, tag: int = 4, is_active: bool = True) -> dict[str, Any]:
		"""获取职位收藏列表（geekGetJob?tag=4，与 deliver_list 同级 wapi 只读 GET）。"""
		params = {"page": page, "tag": tag, "isActive": "true" if is_active else "false"}
		return self._request("GET", endpoints.GEEK_GET_JOB_URL, params=params)

	def friend_list(self, page: int = 1) -> dict[str, Any]:
		params = {"page": page}
		return self._read_request("GET", endpoints.FRIEND_LIST_URL, params=params)

	def interview_data(self) -> dict[str, Any]:
		return self._request("GET", endpoints.INTERVIEW_DATA_URL)

	def job_history(self, page: int = 1) -> dict[str, Any]:
		params = {"page": page}
		return self._request("GET", endpoints.JOB_HISTORY_URL, params=params)

	def chat_history(self, gid: str, security_id: str, *, page: int = 1, count: int = 20) -> dict[str, Any]:
		"""获取与指定好友的聊天消息历史。"""
		params = {"gid": gid, "securityId": security_id, "page": page, "c": count, "src": 0}
		return self._read_request("GET", endpoints.CHAT_HISTORY_URL, params=params)

	def friend_label(self, friend_id: str, label_id: int, friend_source: int = 0, *, remove: bool = False) -> dict[str, Any]:
		"""添加或移除好友标签。"""
		url = endpoints.FRIEND_LABEL_DELETE_URL if remove else endpoints.FRIEND_LABEL_ADD_URL
		params = {"friendId": friend_id, "friendSource": friend_source, "labelId": label_id}
		return self._request("GET", url, params=params)

	def exchange_contact(self, security_id: str, uid: str, name: str, exchange_type: int = 1) -> dict[str, Any]:
		"""请求交换联系方式（1=手机, 2=微信）。"""
		data = {"type": exchange_type, "securityId": security_id, "uniqueId": uid, "name": name}
		return self._browser_request("POST", endpoints.EXCHANGE_REQUEST_URL, data=data)

	def resume_status(self) -> dict[str, Any]:
		"""查询简历完整度和在线状态。"""
		return self._request("GET", endpoints.RESUME_STATUS_URL)

	def geek_get_job(self, security_id: str) -> dict[str, Any]:
		"""查询与某招聘者的互动关系（是否已打招呼等）。"""
		params = {"securityId": security_id}
		return self._request("GET", endpoints.GEEK_GET_JOB_URL, params=params)
