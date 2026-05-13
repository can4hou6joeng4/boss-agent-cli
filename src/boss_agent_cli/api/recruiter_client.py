"""Recruiter-side API client.

Dual-channel like BossClient: httpx for low-risk reads, browser for high-risk writes.
Endpoints sourced from newboss/boss-cli project (confirmed via reverse engineering).
"""

import atexit
import json
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

	def __init__(
		self, auth_manager: "AuthManager", *, delay: tuple[float, float] = (1.5, 3.0), cdp_url: str | None = None
	) -> None:
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
				logger=getattr(self._auth, "_logger", None),
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
				backoff = (2**attempt) + random.uniform(0.5, 1.5)
				time.sleep(backoff)
				self._auth.force_refresh(cdp_url=self._cdp_url)
				self._client = None
				continue

			resp.raise_for_status()
			data = resp.json()
			code = data.get("code")

			if code == ep.CODE_STOKEN_EXPIRED and attempt < _MAX_RETRIES:
				backoff = (2**attempt) + random.uniform(0.5, 1.5)
				time.sleep(backoff)
				self._auth.force_refresh(cdp_url=self._cdp_url)
				self._client = None
				continue

			if code == ep.CODE_RATE_LIMITED and attempt < _MAX_RETRIES:
				cooldown = min(60, 10 * (2**attempt))
				time.sleep(cooldown)
				continue

			if isinstance(data, dict):
				data.setdefault("__cli_endpoint_hint__", url)
			return cast("dict[str, Any]", data)

		raise RecruiterAuthError("请求失败，已达最大重试次数")

	def _browser_request(
		self, method: str, url: str, *, params: dict[str, Any] | None = None, data: dict[str, Any] | None = None
	) -> dict[str, Any]:
		result = self._get_browser().request(method, url, params=params, data=data)
		if isinstance(result, dict):
			result.setdefault("__cli_endpoint_hint__", url)
		return result

	def _evaluate_request(self, method: str, url: str, *, data: dict[str, Any] | None = None) -> dict[str, Any]:
		"""Issue an HTTP POST via raw-CDP fetch in the user's chat tab.

		Workaround for patchright's 'Frame was detached' race condition that
		fires whenever we attach to a CDP context with an active Vue re-render.
		Used by send_message_by_friend and exchange_request_by_friend which
		can't tolerate that race during a multi-step call chain.

		Uses BrowserSession.evaluate_js → _cdp_evaluate_in_chat_tab. Cookies
		flow through naturally (the chat tab is logged in). Returns the parsed
		JSON response body, mirroring _browser_request's contract.
		"""
		# Build a JS function that fetches in-page; pass body as URL-encoded form
		# matching BrowserSession.request semantics.
		js = """
			async (args) => {
				const body = new URLSearchParams();
				if (args.data) {
					for (const [k, v] of Object.entries(args.data)) {
						if (v !== null && v !== undefined) body.append(k, String(v));
					}
				}
				const opts = {
					method: args.method,
					credentials: 'include',
					headers: {
						'Accept': 'application/json, text/plain, */*',
						'X-Requested-With': 'XMLHttpRequest',
						'Referer': 'https://www.zhipin.com/web/chat/index',
					},
				};
				if (args.method === 'POST') {
					opts.headers['Content-Type'] = 'application/x-www-form-urlencoded';
					opts.body = body.toString();
				}
				try {
					const resp = await fetch(args.url, opts);
					return await resp.json();
				} catch (e) {
					return {code: -1, message: 'fetch threw: ' + e.message, zpData: {}};
				}
			}
		"""
		result = self._get_browser().evaluate_js(js, {"method": method, "url": url, "data": data})
		if isinstance(result, dict):
			result.setdefault("__cli_endpoint_hint__", url)
		return cast("dict[str, Any]", result)

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

	def search_geeks(
		self,
		query: str,
		*,
		city: str | None = None,
		page: int = 1,
		job_id: str | None = None,
		experience: str | None = None,
		degree: str | None = None,
		age: str | None = None,
		school_level: str | None = None,
		activeness: str | None = None,
		source: str | None = None,
		select: bool = False,
		salary: str | None = None,
	) -> dict[str, Any]:
		city_code = city or "-2"
		params: dict[str, Any] = {
			"page": page,
			"keywords": query or "",
			"tag": "",
			"city": city_code,
			"gender": "-1",
			"experience": experience or "-1,-1",
			"salary": salary or "-1,-1",
			"age": age or "-1,-1",
			"applyStatus": "-1",
			"degree": degree or "-1,-1",
			"switchFreq": 0,
			"manageExperience": 0,
			"geekJobRequirements": 0,
			"exchangeResume": 0,
			"viewResume": 0,
			"firstDegree": 0,
			"queryAnd": 0,
			"source": source or 4,
			"activeness": activeness or 0,
			"defaultCondition": 2,
			"hasRcd": 0,
			"filterParams": json.dumps(
				{
					"sortType": 1,
					"region": {"cityCode": city_code, "cityName": "", "areas": []},
					"overSeaWorkExperience": 0,
					"overSeaWorkLanguage": 0,
					"overSeaWorkWill": 0,
					"manageExperience": 0,
				},
				separators=(",", ":"),
			),
		}
		if school_level:
			params["schoolLevel"] = school_level
		if select:
			params["select"] = "true"
		if job_id:
			params["jobId"] = job_id
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
		"""DEPRECATED: 旧的 fastReply/sendReplyMsg 端点已被 BOSS 弃用。

		issue #217 — qianjunye 抓包确认 BOSS 招聘者侧已迁移到 WebSocket+Protobuf
		双通道（MQTT over WSS）。此方法保留是为了 callers 不破坏，但调用必返 121。

		新调用方应使用 send_message_by_friend (走 A' / Vue 前端代劳路径)。
		"""
		data = {"gid": gid, "content": content}
		return self._browser_request("POST", ep.BOSS_SEND_MESSAGE_URL, data=data)

	def send_message_by_friend(self, friend_id: int, content: str) -> dict[str, Any]:
		"""走 A' 路径发消息：让 BOSS 招聘者前端 Vue 组件代劳真正的 WS 发送。

		依赖 CDP Chrome 模式（用户已开 https://www.zhipin.com/web/chat/index 招聘者页）。

		实现路径（实证，不是猜测）：
		  1. friend_detail([friend_id])         拿 encryptUid/encryptJobId/securityId 等
		  2. JS: geekList.geekClick(friendData) 触发 BOSS 自己的会话切换链
		         → BOSS 自动调 session/bossEnter + boss/historyMsg + chat/geek/info
		         → editor.conversation$ 切换到目标 friend
		  3. JS: 轮询 editor.conversation$.friendId === target_friend_id（4s 超时）
		  4. JS: editor.disabled = false (强制绕过 UI 业务规则)
		         editor.draft[uniqueId] = content  + input.innerText = content
		         editor.sendText()                ← 真正触发 WS protobuf 帧
		  5. 等 2s，验证 WS 帧已发出（caller 端不参与，前端会自己发 zpblock 风控报备）

		失败验证记录（避免后人重走弯路）：
		  - ❌ 直接调 BOSS_SEND_MESSAGE_URL (旧路径) → 121 INVALID_PARAM (端点已弃)
		  - ❌ 调 session_enter（HTTP）后 sendText → editor 不切，仍发到上一个候选人
		  - ❌ HTTP zpblock/chat/reply/block/v2 作为前置 → 实际是事后报备，前端自动发
		  - ❌ 跟随 Editor.disabled=true → 客户端业务规则，服务端不看（绕过即可）
		  - ✅ geekList.geekClick(friendData) → BOSS 前端自己处理切会话和发消息
		"""
		# Step 1: friend_detail
		fd_resp = self.friend_detail([friend_id])
		friends = (fd_resp.get("zpData") or {}).get("friendList") or []
		if not friends:
			return {
				"code": -1,
				"message": "friend_detail 未返回候选人信息（friend_id 可能无效）",
				"zpData": {},
			}
		friend = friends[0]
		# friend_detail 用 uid 字段，前端 geekClick 期待 friendId
		friend_data: dict[str, Any] = {**friend}
		if "friendId" not in friend_data and "uid" in friend_data:
			friend_data["friendId"] = friend_data["uid"]
		friend_data["uniqueId"] = f"{friend_data['friendId']}-{friend_data.get('friendSource', 0)}"
		friend_data.setdefault("newMsgCount", 0)
		friend_data.setdefault("jumpUrl", "")

		# Step 2-4: hand off to the page's Vue runtime
		js = """
			async ({friendData, content, targetFriendId, switchTimeoutMs, ackTimeoutMs}) => {
				const log = [];
				const sleep = (ms) => new Promise(r => setTimeout(r, ms));
				const findEditor = () => {
					const input = document.querySelector('.boss-chat-editor-input');
					if (!input) return [null, null, 'no .boss-chat-editor-input element'];
					const editor = input.parentElement && input.parentElement.__vue__;
					if (!editor) return [null, null, 'editor parent has no __vue__ instance'];
					return [input, editor, null];
				};
				// Click via geek-list.geekClick — drives BOSS's own session-switch chain
				const chatUser = document.querySelector('.chat-user');
				if (!chatUser) return {ok: false, error: '.chat-user not found (chat tab not open?)', log};
				const geekList = chatUser.__vue__;
				if (!geekList || geekList.$options.name !== 'geek-list') {
					return {ok: false, error: 'geek-list Vue component not at .chat-user', log};
				}
				try {
					geekList.geekClick(friendData);
					log.push('geekClick called');
				} catch (e) {
					return {ok: false, error: 'geekClick threw: ' + e.message, log};
				}
				// Wait for editor to repoint to target
				const deadline = Date.now() + switchTimeoutMs;
				let editor = null, input = null;
				while (Date.now() < deadline) {
					await sleep(150);
					const [inp, ed, err] = findEditor();
					if (err) continue;
					if (ed.conversation$ && ed.conversation$.friendId === targetFriendId) {
						editor = ed;
						input = inp;
						log.push('editor switched to target after ' + (switchTimeoutMs - (deadline - Date.now())) + 'ms');
						break;
					}
				}
				if (!editor) return {ok: false, error: 'editor did not switch to target friend in ' + switchTimeoutMs + 'ms', log};
				// Force-bypass UI disable rule (server doesn't enforce it)
				editor.disabled = false;
				editor.draft[editor.uniqueId] = content;
				input.innerText = content;
				log.push('draft set, calling sendText');
				try {
					editor.sendText();
				} catch (e) {
					return {ok: false, error: 'sendText threw: ' + e.message, log};
				}
				// Best-effort wait so the WS frame can leave the page before
				// caller closes/disconnects the CDP session.
				await sleep(ackTimeoutMs);
				log.push('done');
				return {ok: true, log};
			}
		"""
		result = self._get_browser().evaluate_js(
			js,
			{
				"friendData": friend_data,
				"content": content,
				"targetFriendId": friend_data["friendId"],
				"switchTimeoutMs": 4000,
				"ackTimeoutMs": 2000,
			},
		)

		if isinstance(result, dict) and result.get("ok"):
			return {"code": 0, "message": "Success", "zpData": {"friendId": friend_id, "log": result.get("log")}}
		# Surface the page-side error in CLI envelope shape
		err_msg = (result or {}).get("error") if isinstance(result, dict) else f"unexpected result: {result!r}"
		return {
			"code": -1,
			"message": f"send_message_by_friend failed: {err_msg}",
			"zpData": {"log": (result or {}).get("log") if isinstance(result, dict) else None},
		}

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
		"""DEPRECATED: 旧的 (uid/jobId/gid) 参数协议已被 BOSS 弃用 → 121。

		issue #217 — qianjunye 抓包确认实际服务端要的是 securityId + name +
		前置 zpblock + 两次 exchange/test。新调用方应使用
		exchange_request_by_friend()。
		"""
		data = {"type": exchange_type, "uid": uid, "jobId": job_id, "gid": gid}
		return self._browser_request("POST", ep.BOSS_EXCHANGE_REQUEST_URL, data=data)

	def exchange_request_by_friend(self, friend_id: int, exchange_type: int) -> dict[str, Any]:
		"""请求交换联系方式（手机号/微信）或附件简历。

		issue #217 — 抓包实证的真实协议：

		  type 取值:
		    1 = 换手机号
		    4 = 求附件简历
		    （旧代码的 type=3 是错的, 已弃）

		  完整调用顺序（前端抓包顺序）：
		    1. POST /wapi/zpblock/chat/reply/block/v2
		       encryptJid=encJobId, encryptExpId=encExpId,
		       securityId=securityId, autoRelease=0, bgSource=12
		       (与 send_message 风控前置同源, autoRelease/bgSource 不同)
		    2. POST /wapi/zpchat/exchange/test  (type, securityId)
		    3. POST /wapi/zpchat/exchange/test  (二次确认)
		    4. POST /wapi/zpchat/exchange/request
		       type, securityId, name=候选人姓名(URL encoded)

		  关键字段全来自 friend_detail([friend_id]) 响应，CLI 用户只需要
		  传 friend_id + type，不必关心 securityId / name / encryptJobId。

		失败验证记录:
		  - ❌ 旧 exchange_request(type, uid, jobId, gid) → 121 (参数协议错位)
		  - ❌ 缺少 zpblock 前置 + exchange/test → 服务端拒
		"""
		fd_resp = self.friend_detail([friend_id])
		friends = (fd_resp.get("zpData") or {}).get("friendList") or []
		if not friends:
			return {
				"code": -1,
				"message": "friend_detail 未返回候选人信息（friend_id 可能无效）",
				"zpData": {},
			}
		friend = friends[0]

		# 关键：friend_detail 响应 encryptExpectId 总是 null，但 zpblock/v2
		# 需要它的加密字符串形式（如 "3a40ce7d18586f591nF739i7FlFVyg~~"）。
		# 前端 editor.conversation$.encryptExpectId 才有这个值——必须先 geekClick
		# 切到目标候选人会话，让 BOSS 内部接口填充 conversation$，再读出来。
		# 顺便也用 conversation$ 里的最新 securityId/encryptJobId/name（更可靠）。
		switch_js = """
			async (args) => {
				const sleep = ms => new Promise(r => setTimeout(r, ms));
				const chatUser = document.querySelector('.chat-user');
				if (!chatUser) return {ok: false, error: '.chat-user not found'};
				const geekList = chatUser.__vue__;
				if (!geekList || geekList.$options.name !== 'geek-list') {
					return {ok: false, error: 'geek-list Vue component not at .chat-user'};
				}
				try {
					geekList.geekClick(args.friendData);
				} catch (e) {
					return {ok: false, error: 'geekClick threw: ' + e.message};
				}
				// conversation$ is populated progressively after geekClick:
				// friendId appears first, then securityId/encryptExpectId are
				// filled by subsequent /chat/geek/info + /session/bossEnter
				// responses. We must wait for all required fields.
				const deadline = Date.now() + args.timeoutMs;
				let partial = null;
				while (Date.now() < deadline) {
					await sleep(150);
					const inp = document.querySelector('.boss-chat-editor-input');
					const ed = inp && inp.parentElement && inp.parentElement.__vue__;
					if (!ed || !ed.conversation$) continue;
					const c = ed.conversation$;
					if (c.friendId !== args.targetFriendId) continue;
					partial = {
						encryptUid: c.encryptUid,
						encryptJobId: c.encryptJobId || c.toPositionId,
						encryptExpectId: c.encryptExpectId || '',
						securityId: c.securityId,
						name: c.name,
					};
					// Only return when the write-path required fields are populated.
					if (partial.securityId && partial.encryptJobId) {
						return {ok: true, ...partial};
					}
				}
				return {
					ok: false,
					error: 'conversation$ not fully populated in ' + args.timeoutMs + 'ms',
					partial,
				};
			}
		"""
		friend_data: dict[str, Any] = {**friend}
		if "friendId" not in friend_data and "uid" in friend_data:
			friend_data["friendId"] = friend_data["uid"]
		friend_data["uniqueId"] = f"{friend_data['friendId']}-{friend_data.get('friendSource', 0)}"
		friend_data.setdefault("newMsgCount", 0)
		friend_data.setdefault("jumpUrl", "")

		switch_result = self._get_browser().evaluate_js(
			switch_js,
			{"friendData": friend_data, "targetFriendId": friend_data["friendId"], "timeoutMs": 4000},
		)
		# 等待 BOSS 前端把 geekClick 触发的内部请求（historyMsg / geek/info /
		# session/bossEnter / brandCard）跑完，避免与 CLI 后续 zpblock+exchange
		# 序列产生 race，导致 securityId 一次性令牌被前端先消费掉 → 121
		time.sleep(1.5)
		if not (isinstance(switch_result, dict) and switch_result.get("ok")):
			err = (switch_result or {}).get("error") if isinstance(switch_result, dict) else f"unexpected: {switch_result!r}"
			return {"code": -1, "message": f"无法切换到目标候选人会话: {err}", "zpData": {}}

		encrypt_job_id = switch_result.get("encryptJobId") or ""
		encrypt_exp_id = switch_result.get("encryptExpectId") or ""
		security_id = switch_result.get("securityId") or ""
		name = switch_result.get("name") or friend.get("name") or ""
		if not security_id:
			return {"code": -1, "message": "conversation$ 缺 securityId", "zpData": switch_result}

		# 所有 4 步都走 _evaluate_request (raw CDP fetch in chat tab),
		# 不能用 _browser_request：后者会触发 patchright connect_over_cdp,
		# 而招聘者 chat tab 持续 Vue 重渲染会让 patchright 在 attach 阶段崩
		# (Node driver 'Frame was detached')。
		# Step 1: zpblock 风控前置 (与 send_message 同源但参数不同)
		block_data = {
			"encryptJid": encrypt_job_id,
			"encryptExpId": encrypt_exp_id,  # 从 conversation$ 拿，friend_detail 给不了
			"securityId": security_id,
			"autoRelease": "0",  # 注意 send_message 不传此参数（默认 1）
			"bgSource": "12",    # 12 = exchange，与 reply 的 1 区分
		}
		block_resp = self._evaluate_request("POST", ep.BOSS_CHAT_REPLY_BLOCK_URL, data=block_data)
		if block_resp.get("code") != 0:
			return block_resp

		# Step 2-3: exchange/test (两次都跑，复刻前端二次确认行为)
		test_data: dict[str, Any] = {"type": exchange_type, "securityId": security_id}
		test1 = self._evaluate_request("POST", ep.BOSS_EXCHANGE_TEST_URL, data=test_data)
		if test1.get("code") != 0:
			return test1
		test2 = self._evaluate_request("POST", ep.BOSS_EXCHANGE_TEST_URL, data=test_data)
		if test2.get("code") != 0:
			return test2

		# Step 4: exchange/request
		req_data: dict[str, Any] = {
			"type": exchange_type,
			"securityId": security_id,
			"name": name,  # 服务端要 URL-encoded UTF-8 姓名；URLSearchParams 自动编码
		}
		return self._evaluate_request("POST", ep.BOSS_EXCHANGE_REQUEST_URL, data=req_data)

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
