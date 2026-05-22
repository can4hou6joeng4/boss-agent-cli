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

from boss_agent_cli.api.httpx_helpers import (
	add_stoken_to_get_params,
	browser_headers,
	merge_response_cookies,
	referer_header,
)
from boss_agent_cli.api import recruiter_endpoints as ep
from boss_agent_cli.api.throttle import RequestThrottle

if TYPE_CHECKING:
	from boss_agent_cli.api.browser_client import BrowserSession
	from boss_agent_cli.auth.manager import AuthManager

_MAX_RETRIES = 3

_OPEN_CLIENTS: weakref.WeakSet["BossRecruiterClient"] = weakref.WeakSet()

_CHAT_FRONTEND_HELPERS_JS = """
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const squashText = (text) => String(text || '').replace(/\\s+/g, ' ').trim();
const escapeHtml = (text) => String(text)
	.replace(/&/g, '&amp;')
	.replace(/</g, '&lt;')
	.replace(/>/g, '&gt;');
const getGeekList = () => {
	const chatUser = document.querySelector('.chat-user');
	if (!chatUser) return [null, '.chat-user not found (chat tab not open?)'];
	const geekList = chatUser.__vue__;
	if (!geekList || geekList.$options.name !== 'geek-list') {
		return [null, 'geek-list Vue component not at .chat-user'];
	}
	return [geekList, null];
};
const getEditorState = () => {
	const input = document.querySelector('.boss-chat-editor-input');
	if (!input) return {input: null, editor: null, error: 'no .boss-chat-editor-input element'};
	const editor = input.parentElement && input.parentElement.__vue__;
	if (!editor) return {input: null, editor: null, error: 'editor parent has no __vue__ instance'};
	return {input, editor, error: null};
};
const switchConversation = async (friendData, targetFriendId, switchTimeoutMs, requireSecurityId, log) => {
	const [geekList, geekErr] = getGeekList();
	if (geekErr) return {ok: false, error: geekErr, log};
	try {
		geekList.geekClick(friendData);
		log.push('geekClick called');
	} catch (e) {
		return {ok: false, error: 'geekClick threw: ' + e.message, log};
	}

	const deadline = Date.now() + switchTimeoutMs;
	while (Date.now() < deadline) {
		await sleep(150);
		const state = getEditorState();
		if (state.error) continue;
		const conversation = state.editor && state.editor.conversation$;
		if (!conversation || conversation.friendId !== targetFriendId) continue;
		if (requireSecurityId && !conversation.securityId) continue;
		log.push('editor switched to target after ' + (switchTimeoutMs - (deadline - Date.now())) + 'ms');
		return {ok: true, input: state.input, editor: state.editor, conversation};
	}

	const prefix = requireSecurityId
		? 'conversation$ not ready for target friend in '
		: 'editor did not switch to target friend in ';
	return {ok: false, error: prefix + switchTimeoutMs + 'ms', log};
};
const findVueComponent = (name) => {
	const seen = new Set();
	const queue = [];
	for (const el of document.querySelectorAll('*')) {
		if (el.__vue__) queue.push(el.__vue__);
	}
	while (queue.length) {
		const vm = queue.shift();
		if (!vm || seen.has(vm)) continue;
		seen.add(vm);
		try {
			for (const child of vm.$children || []) queue.push(child);
		} catch (e) {}
		const vmName = vm.$options && (vm.$options.name || vm.$options._componentTag);
		if (vmName === name) return vm;
	}
	return null;
};
const clickPrimaryConfirm = () => {
	const roots = Array.from(document.querySelectorAll('.exchange-tooltip, .popover, .ui-dialog'));
	roots.push(document.body);
	const candidates = [];
	for (const root of roots) {
		const rootText = squashText(root.innerText || root.textContent || '');
		if (!rootText || !/确定|取消|请求|交换|简历|电话|手机|微信/.test(rootText)) continue;
		for (const el of root.querySelectorAll('button, a, span, div')) {
			const text = squashText(el.innerText || el.textContent || '');
			const cls = String(el.className || '');
			if (text === '确定' || /confirm|sure|primary/.test(cls)) {
				candidates.push({el, text});
			}
		}
	}
	const candidate = candidates.find((item) => item.text === '确定') || candidates[0];
	if (!candidate) return false;
	candidate.el.click();
	return true;
};
const chatConversationText = () => squashText(document.querySelector('.chat-conversation')?.innerText || '');
"""

_SEND_MESSAGE_ACTION_JS = """
const escaped = escapeHtml(args.content);
editor.disabled = false;
editor.conversationLoading$ = false;
editor.draft[editor.uniqueId] = args.content;
input.innerHTML = escaped;
log.push('editbox html set, calling sendText');
try {
	const ret = editor.sendText();
	log.push('sendText returned ' + (ret === undefined ? 'undefined' : String(ret)));
} catch (e) {
	return {ok: false, error: 'sendText threw: ' + e.message, log};
}
await sleep(args.postSendUiWaitMs);
return {ok: true, log};
"""

_EXCHANGE_ACTION_JS = """
const vm = findVueComponent(args.componentName);
if (!vm) return {ok: false, error: args.componentName + ' Vue component not found', log};
log.push('found ' + args.componentName + ' type=' + vm.type);

try {
	const ret = vm.handleExChange();
	if (ret && typeof ret.then === 'function') await ret;
	log.push('handleExChange returned');
} catch (e) {
	return {ok: false, error: 'handleExChange threw: ' + e.message, log};
}

await sleep(args.preConfirmUiWaitMs);
const confirmed = clickPrimaryConfirm();
log.push('confirm clicked=' + confirmed);
if (!confirmed) {
	return {ok: false, error: args.componentName + ' confirm button not found', log, confirmed, componentName: args.componentName};
}
await sleep(args.postConfirmUiWaitMs);
return {
	ok: true,
	error: null,
	log,
	confirmed,
	componentName: args.componentName,
};
"""

_EXCHANGE_COMPONENT_NAMES = {1: "ExchangePhone", 2: "ExchangeWx", 4: "ExchangeResume"}
_EXCHANGE_MESSAGE_TEXT = {1: "请求交换联系方式", 2: "请求交换联系方式", 4: "方便发一份简历过来吗？"}


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
			headers = browser_headers(ep.DEFAULT_HEADERS, token)
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
		return referer_header(url, ep.REFERER_MAP, f"{ep.BASE_URL}/")

	def _merge_cookies(self, resp: httpx.Response) -> None:
		merge_response_cookies(self._get_client(), resp)

	def _request(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
		"""httpx request with retry loop."""
		# extra_headers overrides yaml-driven defaults from _headers_for(url); use only when
		# a static yaml referer can't carry per-call query params (e.g. job/edit needs encryptId).
		extra_headers_override: dict[str, str] = kwargs.pop("extra_headers", {})
		for attempt in range(_MAX_RETRIES + 1):
			client = self._get_client()
			token = self._auth.get_token()
			stoken = token.get("stoken", "")

			add_stoken_to_get_params(method, kwargs, stoken)

			self._throttle.wait()

			headers = {**self._headers_for(url), **extra_headers_override}
			resp = client.request(method, url, headers=headers, **kwargs)
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

	def _require_chat_friend_data(self, friend_id: int) -> dict[str, Any]:
		"""Normalize friend_detail output into the Vue payload expected by geekClick.

		BOSS 的 friend_detail 仍用 uid，而聊天页 Vue 组件期待 friendId/uniqueId。
		把这层映射集中在一个 helper，避免 reply / exchange 各自拼字段后漂移。
		"""
		fd_resp = self.friend_detail([friend_id])
		friends = (fd_resp.get("zpData") or {}).get("friendList") or []
		if not friends:
			raise LookupError("friend_detail 未返回候选人信息（friend_id 可能无效）")

		friend_data: dict[str, Any] = {**friends[0]}
		if "friendId" not in friend_data and "uid" in friend_data:
			friend_data["friendId"] = friend_data["uid"]
		friend_data["uniqueId"] = f"{friend_data['friendId']}-{friend_data.get('friendSource', 0)}"
		friend_data.setdefault("newMsgCount", 0)
		friend_data.setdefault("jumpUrl", "")
		return friend_data

	def _run_chat_frontend_action(
		self,
		*,
		friend_data: dict[str, Any],
		action_js: str,
		require_security_id: bool,
		settle_ms: int,
		extra_args: dict[str, Any] | None = None,
		listen_ms: int | None = None,
	) -> tuple[Any, list[dict[str, Any]]]:
		"""Run a Vue-mediated action in the recruiter's existing chat tab.

		reply / request-resume / exchange 都依赖同一段前置动作：
		1. geekClick 切到目标会话
		2. 等 conversation$ 指向目标 friend
		3. 等页面把内部副作用跑完

		这层统一成一个执行器，避免每个写操作都复制一份聊天页侦察脚本。
		"""
		template = """
			async (args) => {
				const log = [];
				__HELPERS__
				const switched = await switchConversation(
					args.friendData,
					args.targetFriendId,
					args.switchTimeoutMs,
					__REQUIRE_SECURITY_ID__,
					log,
				);
				if (!switched.ok) return switched;
				await sleep(args.settleMs);
				const input = switched.input;
				const editor = switched.editor;
				const conversation = switched.conversation;
				__ACTION__
			}
		"""
		script = (
			template
			.replace("__HELPERS__", _CHAT_FRONTEND_HELPERS_JS)
			.replace("__REQUIRE_SECURITY_ID__", "true" if require_security_id else "false")
			.replace("__ACTION__", action_js)
		)
		args: dict[str, Any] = {
			"friendData": friend_data,
			"targetFriendId": friend_data["friendId"],
			"switchTimeoutMs": 5000,
			"settleMs": settle_ms,
		}
		if extra_args:
			args.update(extra_args)

		browser = self._get_browser()
		if listen_ms is None:
			return browser.evaluate_js(script, args), []

		capture = browser.evaluate_js_with_chat_events(script, args, listen_ms=listen_ms)
		result = capture.get("value") if isinstance(capture, dict) else capture
		events = capture.get("events", []) if isinstance(capture, dict) else []
		return result, cast("list[dict[str, Any]]", events)

	def _matching_chat_send_events(self, events: list[dict[str, Any]], expected_bits: list[str]) -> list[dict[str, Any]]:
		return [
			event
			for event in events
			if event.get("kind") == "ws_send"
			and int(event.get("bytes", 0)) >= 100
			and any(expected in bit for expected in expected_bits for bit in event.get("utf8_bits", []))
		]

	def _chat_ws_evidence(self, events: list[dict[str, Any]], expected_bits: list[str]) -> dict[str, Any]:
		ws_send = [event for event in events if event.get("kind") == "ws_send"]
		return {
			"event_count": len(events),
			"ws_send_count": len(ws_send),
			"matched_ws_count": len(self._matching_chat_send_events(events, expected_bits)),
		}

	def _chat_action_failure_data(
		self,
		*,
		action: str,
		friend_id: int,
		error: str,
		expected_bits: list[str],
		result: Any = None,
		events: list[dict[str, Any]] | None = None,
		extra: dict[str, Any] | None = None,
	) -> dict[str, Any]:
		payload: dict[str, Any] = {
			"action": action,
			"friendId": friend_id,
			"ok": False,
			"error": error,
			"log": result.get("log", []) if isinstance(result, dict) else [],
			"ws_evidence": self._chat_ws_evidence(events or [], expected_bits),
		}
		if isinstance(result, dict):
			for key in ("confirmed", "componentName"):
				if key in result:
					payload[key] = result[key]
		if extra:
			payload.update(extra)
		return payload

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
		  3. JS: 轮询 editor.conversation$.friendId === target_friend_id（5s 超时）
		  4. JS: editor.disabled = false + editbox.innerHTML = escaped(text)
		         editor.draft[uniqueId] = content; editor.sendText()
		  5. 原始 CDP 监听 3s，必须看到真实 chat WS 帧；只出现
		     `/message/suggest` 之类提示流量不算成功

		失败验证记录（避免后人重走弯路）：
		  - ❌ 直接调 BOSS_SEND_MESSAGE_URL (旧路径) → 121 INVALID_PARAM (端点已弃)
		  - ❌ 调 session_enter（HTTP）后 sendText → editor 不切，仍发到上一个候选人
		  - ❌ HTTP zpblock/chat/reply/block/v2 作为前置 → 实际是事后报备，前端自动发
		  - ❌ 只写 draft / innerText → 可能只触发 `/message/suggest`，并未真发
		  - ✅ geekList.geekClick(friendData) → BOSS 前端自己处理切会话和发消息
		"""
		try:
			friend_data = self._require_chat_friend_data(friend_id)
		except LookupError as exc:
			return {
				"code": -1,
				"message": str(exc),
				"zpData": self._chat_action_failure_data(
					action="reply",
					friend_id=friend_id,
					error=str(exc),
					expected_bits=[content],
				),
			}
		result, events = self._run_chat_frontend_action(
			friend_data=friend_data,
			action_js=_SEND_MESSAGE_ACTION_JS,
			require_security_id=False,
			settle_ms=2000,
			extra_args={"content": content, "postSendUiWaitMs": 1200},
			listen_ms=3000,
		)

		if isinstance(result, dict) and result.get("ok"):
			matched_ws = self._matching_chat_send_events(events, [content])
			if matched_ws:
				return {
					"code": 0,
					"message": "Success",
					"zpData": {
						"friendId": friend_id,
						"log": result.get("log"),
						"matched_ws_count": len(matched_ws),
					},
				}
			result.setdefault("error", "no confirmed chat websocket send detected")
		# Surface the page-side error in CLI envelope shape
		err_msg = (
			str((result or {}).get("error") or "unexpected page result")
			if isinstance(result, dict)
			else f"unexpected result: {result!r}"
		)
		return {
			"code": -1,
			"message": f"send_message_by_friend failed: {err_msg}",
			"zpData": self._chat_action_failure_data(
				action="reply",
				friend_id=friend_id,
				error=err_msg,
				expected_bits=[content],
				result=result,
				events=events,
			),
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

	def job_detail(self, enc_job_id: str) -> dict[str, Any]:
		params = {"encJobId": enc_job_id, "lid": "", "encAtsJobId": ""}
		referer = f"{ep.BASE_URL}/web/frame/job/edit?jobversion=9921&encryptId={enc_job_id}&jobCreateSource=0&enterSource=6"
		return self._request("GET", ep.BOSS_JOB_EDIT_URL, params=params, extra_headers={"Referer": referer})

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

		issue #217 — 走 BOSS 招聘者前端 Vue 组件代劳真实 exchange 链路。

		  type 取值:
		    1 = 换手机号
		    2 = 换微信
		    4 = 求附件简历
		    （旧代码的 type=3 是错的, 已弃）

		  实测失败路径：CLI 手写 zpblock → exchange/test → exchange/test →
		  exchange/request，第一步过、第二步仍 121。真实可用路径是先
		  geekClick 切到目标会话，再调用页面里的 ExchangePhone /
		  ExchangeResume.handleExChange()，由前端自己处理动态 securityId、
		  风控请求、确认弹窗和状态刷新。

		失败验证记录:
		  - ❌ 旧 exchange_request(type, uid, jobId, gid) → 121 (参数协议错位)
		  - ❌ CLI 复刻四步 HTTP → exchange/test 仍 121
		  - ✅ ExchangeResume.handleExChange() → 发送"方便发一份简历过来吗？"
		  - ✅ ExchangePhone.handleExChange() → 发送"请求交换联系方式"
		  - ✅ ExchangeWx.handleExChange() → 发送"请求交换联系方式"
		"""
		try:
			friend_data = self._require_chat_friend_data(friend_id)
		except LookupError as exc:
			return {
				"code": -1,
				"message": str(exc),
				"zpData": self._chat_action_failure_data(
					action="exchange",
					friend_id=friend_id,
					error=str(exc),
					expected_bits=[],
					extra={"exchange_type": exchange_type},
				),
			}

		component_name = _EXCHANGE_COMPONENT_NAMES.get(exchange_type)
		if component_name is None:
			return {
				"code": -1,
				"message": f"unsupported exchange_type={exchange_type}; expected 1(phone), 2(wechat) or 4(resume)",
				"zpData": self._chat_action_failure_data(
					action="exchange",
					friend_id=friend_id,
					error=f"unsupported exchange_type={exchange_type}; expected 1(phone), 2(wechat) or 4(resume)",
					expected_bits=[],
					extra={"exchange_type": exchange_type},
				),
			}

		expected_text = _EXCHANGE_MESSAGE_TEXT[exchange_type]
		result, events = self._run_chat_frontend_action(
			friend_data=friend_data,
			action_js=_EXCHANGE_ACTION_JS,
			require_security_id=True,
			settle_ms=1000,
			extra_args={
				"componentName": component_name,
				"preConfirmUiWaitMs": 1000,
				"postConfirmUiWaitMs": 800,
			},
			listen_ms=3000,
		)

		if isinstance(result, dict) and result.get("ok"):
			matched_ws = self._matching_chat_send_events(events, [expected_text])
			if matched_ws:
				return {
					"code": 0,
					"message": "Success",
					"zpData": {
						"friendId": friend_id,
						"exchange_type": exchange_type,
						"componentName": result.get("componentName"),
						"confirmed": result.get("confirmed"),
						"log": result.get("log"),
						"matched_ws_count": len(matched_ws),
					},
				}
			result.setdefault("error", "no confirmed chat websocket send detected")
		err = (
			str((result or {}).get("error") or "unexpected page result")
			if isinstance(result, dict)
			else f"unexpected result: {result!r}"
		)
		return {
			"code": -1,
			"message": f"exchange_request_by_friend failed: {err}",
			"zpData": self._chat_action_failure_data(
				action="exchange",
				friend_id=friend_id,
				error=err,
				expected_bits=[expected_text],
				result=result,
				events=events,
				extra={"exchange_type": exchange_type, "componentName": component_name},
			),
		}

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
