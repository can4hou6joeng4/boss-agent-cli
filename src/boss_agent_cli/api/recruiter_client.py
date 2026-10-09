"""Recruiter-side API client.

Dual-channel like BossClient: httpx for low-risk reads, browser for high-risk writes.
Endpoints sourced from newboss/boss-cli project (confirmed via reverse engineering).
"""

import atexit
import json
import time
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote

from boss_agent_cli.api import recruiter_endpoints as ep
from boss_agent_cli.api._base_client import _BaseHttpClient
from boss_agent_cli.api.httpx_helpers import make_client_registry
from boss_agent_cli.api.recruiter_resume import (
	ResumeValidationError,
	attachment_params,
	history_messages,
	incoming_message,
	message_mid,
	new_messages_matching,
	resume_friend,
	save_resume,
)
from boss_agent_cli.api.zhipin_errors import classify_code_37

_OPEN_CLIENTS, _close_open_clients = make_client_registry()

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
const isVisibleElement = (el) => {
	if (!el || !el.isConnected) return false;
	const style = window.getComputedStyle(el);
	if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) === 0) return false;
	const rect = el.getBoundingClientRect();
	return rect.width > 0 && rect.height > 0 && el.getClientRects().length > 0;
};
const POPUP_SELECTOR = '.exchange-tooltip, .popover, .ui-dialog, .dialog-wrap, .boss-dialog, .boss-popup__wrapper, [role="dialog"]';
const visiblePopups = () => Array.from(document.querySelectorAll(POPUP_SELECTOR)).filter(isVisibleElement);
const confirmButtonsIn = (root) => {
	// 只认文字恰好是「确定」的最内层可见元素，不再按 class 名兜底。
	const found = [];
	for (const el of root.querySelectorAll('button, a, span, div')) {
		if (squashText(el.innerText || el.textContent || '') !== '确定') continue;
		if (Array.from(el.children).some((child) => squashText(child.innerText || child.textContent || '') === '确定')) continue;
		if (isVisibleElement(el)) found.push(el);
	}
	return found;
};
const snapshotConfirmState = (vm) => {
	const popups = visiblePopups();
	const buttons = new Set();
	const roots = vm && vm.$el && vm.$el.querySelectorAll ? [vm.$el, ...popups] : popups;
	for (const root of roots) {
		for (const el of confirmButtonsIn(root)) buttons.add(el);
	}
	return {popups: new Set(popups), buttons};
};
const clickPrimaryConfirm = (vm, before) => {
	// 只在当前组件和本次动作后新出现的弹层里找确认按钮；
	// 页面上其他组件（例如隐藏的附件简历确认框）一律不碰。
	const roots = [];
	if (vm && vm.$el && vm.$el.querySelectorAll) roots.push(vm.$el);
	for (const popup of visiblePopups()) {
		if (!before.popups.has(popup)) roots.push(popup);
	}
	for (const root of roots) {
		for (const el of confirmButtonsIn(root)) {
			if (before.buttons.has(el)) continue;
			el.click();
			return true;
		}
	}
	return 'not_required';
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

const beforeConfirm = snapshotConfirmState(vm);
try {
	const ret = vm.handleExChange();
	if (ret && typeof ret.then === 'function') await ret;
	log.push('handleExChange returned');
} catch (e) {
	return {ok: false, error: 'handleExChange threw: ' + e.message, log, componentName: args.componentName};
}

await sleep(args.preConfirmUiWaitMs);
const confirmed = clickPrimaryConfirm(vm, beforeConfirm);
log.push('confirm clicked=' + confirmed);
if (confirmed === true) await sleep(args.postConfirmUiWaitMs);
return {
	ok: true,
	error: null,
	log,
	confirmed,
	componentName: args.componentName,
};
"""

_EXCHANGE_COMPONENT_NAMES = {1: "ExchangePhone", 2: "ExchangeWx", 4: "ExchangeResume"}
# 平台改过文案（#443：求简历从「方便发一份简历过来吗？」变成「想要一份您的附件简历」），
# 这里按类型列出所有已知文案，任意一条命中即算有发送证据。
_CONTACT_EXCHANGE_TEXTS: tuple[str, ...] = ("请求交换联系方式",)
_RESUME_REQUEST_TEXTS: tuple[str, ...] = ("方便发一份简历过来吗", "想要一份您的附件简历")
_EXCHANGE_MESSAGE_TEXTS: dict[int, tuple[str, ...]] = {
	1: _CONTACT_EXCHANGE_TEXTS,
	2: _CONTACT_EXCHANGE_TEXTS,
	4: _RESUME_REQUEST_TEXTS,
}
_ACTION_UNCONFIRMED_HINT = "动作可能已生效，请先 `boss hr chatmsg {friend_id}` 核实，勿直接重试"
_RESUME_REQUEST_DIALOG_TYPE = 2
_RESUME_ACCEPT_TYPE = 3


atexit.register(_close_open_clients)


class RecruiterAuthError(Exception):
	pass


class BossRecruiterClient(_BaseHttpClient):
	"""Recruiter-side hybrid API client."""

	_BASE_URL = ep.BASE_URL
	_DEFAULT_HEADERS = ep.DEFAULT_HEADERS
	_REFERER_MAP = ep.REFERER_MAP
	_AUTH_ERROR_CLS = RecruiterAuthError
	_CODE_STOKEN_EXPIRED = ep.CODE_STOKEN_EXPIRED
	_CODE_RATE_LIMITED = ep.CODE_RATE_LIMITED
	_ADD_ENDPOINT_HINT = True

	def _register(self) -> None:
		_OPEN_CLIENTS.add(self)

	def _unregister(self) -> None:
		_OPEN_CLIENTS.discard(self)

	def _headers_for(self, url: str) -> dict[str, str]:
		headers = super()._headers_for(url)
		if url in (ep.BOSS_RECOMMEND_GEEK_LIST_URL, ep.BOSS_CHAT_START_URL):
			# 从当前 jar 取值，包含上次响应轮换的 bst；不改变既有端点的请求头。
			if bst := self._get_client().cookies.get("bst"):
				headers["zp_token"] = str(bst)
		return headers

	def _should_refresh_token_response(self, data: dict[str, Any]) -> bool:
		return data.get("code") == ep.CODE_STOKEN_EXPIRED and classify_code_37(data) == "token_expired"

	def _browser_request(
		self, method: str, url: str, *, params: dict[str, Any] | None = None, data: dict[str, Any] | None = None
	) -> dict[str, Any]:
		browser = self._get_browser()
		# 与候选人侧同一把 CDP code 37 锁：stoken 未换新就不发出去。
		self._check_cdp_risk_lock(browser)
		result = browser.request(method, url, params=params, data=data)
		if isinstance(result, dict):
			if result.get("code") == ep.CODE_STOKEN_EXPIRED and classify_code_37(result) == "environment_risk":
				self._record_cdp_risk_lock(browser)
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

	@staticmethod
	def _page_error_message(result: Any) -> str:
		"""从前端代劳返回中提取错误串（reply / exchange 失败信封共用）。"""
		if isinstance(result, dict):
			return str(result.get("error") or "unexpected page result")
		return f"unexpected result: {result!r}"

	@staticmethod
	def _chat_action_failure_response(
		*, message: str, payload: dict[str, Any], error_code: str | None = None
	) -> dict[str, Any]:
		"""聊天页写操作失败信封：zpData 保留完整诊断，同时作为 error.details 透出。"""
		details = {
			key: payload[key]
			for key in ("action", "error", "log", "confirmed", "componentName", "exchange_type", "ws_evidence",
				"history_checked", "history_matched_count", "history_error", "side_effects")
			if key in payload
		}
		response: dict[str, Any] = {
			"code": -1,
			"message": message,
			"zpData": payload,
			"__cli_error_details__": details,
		}
		if error_code:
			response["__cli_error_code__"] = error_code
		return response

	# ── Public API ───────────────────────────────────────────────────

	# ── 候选人列表与筛选 ────────────────────────────────

	def friend_list(self, page: int = 1, label_id: int = 0, job_id: str | None = None) -> dict[str, Any]:
		data: dict[str, Any] = {"labelId": label_id, "page": page}
		if job_id:
			data["encJobId"] = job_id
		return self._request("POST", ep.BOSS_FRIEND_LIST_URL, data=data)

	def friend_detail(self, friend_ids: list[int], *, retry: bool = True) -> dict[str, Any]:
		data = {"friendIds": ",".join(str(i) for i in friend_ids)}
		options = {"retry": False, "follow_redirects": False} if not retry else {}
		return self._request("POST", ep.BOSS_FRIEND_DETAIL_URL, data=data, **options)

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

	def recommend_geeks(self, job_id: str, page: int = 1) -> dict[str, Any]:
		"""Read the rich 推荐牛人 cards used by the first-contact endpoint."""
		params: dict[str, Any] = {
			"age": "16,-1",
			"school": "0",
			"activation": "0",
			"recentNotView": "0",
			"gender": "0",
			"exchangeResumeWithColleague": "0",
			"major": "0",
			"switchJobFrequency": "0",
			"keyword1": "-1",
			"degree": "0",
			"experience": "0",
			"intention": "0",
			"salary": "0",
			"jobId": job_id,
			"page": page,
			"coverScreenMemory": "0",
			"cardType": "0",
		}
		referer = (
			f"{ep.BASE_URL}/web/frame/recommend/?jobid={job_id}&status=0&filterParams=&t="
			"&inspectFilterGuide=&version=11211&source=0"
		)
		return self._request("GET", ep.BOSS_RECOMMEND_GEEK_LIST_URL, params=params, extra_headers={"Referer": referer})

	def start_chat(
		self,
		*,
		geek_id: str,
		job_id: str,
		expect_id: str,
		lid: str,
		security_id: str,
		message: str,
		suid: str = "",
	) -> dict[str, Any]:
		"""Create a recruiter conversation and send its first greeting."""
		data = {
			"gid": geek_id,
			"suid": suid,
			"jid": job_id,
			"expectId": expect_id,
			"lid": lid,
			"greet": message,
			"from": "",
			"securityId": security_id,
			"customGreetingGuide": "-1",
		}
		return self._request("POST", ep.BOSS_CHAT_START_URL, data=data, retry=False)

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

	def chat_history(self, gid: int, *, count: int = 20, max_msg_id: int | None = None, retry: bool = True) -> dict[str, Any]:
		params: dict[str, Any] = {"gid": gid, "c": count, "src": 0}
		if max_msg_id:
			params["maxMsgId"] = max_msg_id
		options = {"retry": False, "follow_redirects": False} if not retry else {}
		return self._request("GET", ep.BOSS_CHAT_HISTORY_URL, params=params, **options)

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
			# 页面脚本成功时会显式返回 error: null，setdefault 不会覆盖它。
			if not result.get("error"):
				result["error"] = "no confirmed chat websocket send detected"
		# Surface the page-side error in CLI envelope shape
		err_msg = self._page_error_message(result)
		return self._chat_action_failure_response(
			message=f"send_message_by_friend failed: {err_msg}",
			payload=self._chat_action_failure_data(
				action="reply",
				friend_id=friend_id,
				error=err_msg,
				expected_bits=[content],
				result=result,
				events=events,
			),
		)

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
		    （#443：平台现已改为"想要一份您的附件简历"，两种文案都认）
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

		expected_texts = _EXCHANGE_MESSAGE_TEXTS[exchange_type]
		expected_bits = list(expected_texts)
		# 动作前先记下已有消息，事后只认新出现的那条（只读请求，不影响写操作）。
		baseline_mids = self._chat_history_mids(friend_id)
		started_ms = int(time.time() * 1000)
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

		if not (isinstance(result, dict) and result.get("ok")):
			err = self._page_error_message(result)
			return self._chat_action_failure_response(
				message=f"exchange_request_by_friend failed: {err}",
				payload=self._chat_action_failure_data(
					action="exchange",
					friend_id=friend_id,
					error=err,
					expected_bits=expected_bits,
					result=result,
					events=events,
					extra={"exchange_type": exchange_type, "componentName": component_name},
				),
			)

		matched_ws = self._matching_chat_send_events(events, expected_bits)
		history = self._exchange_history_evidence(
			friend_id, expected_texts, baseline_mids=baseline_mids, since_ms=started_ms,
		)
		side_effects = self._exchange_side_effects(exchange_type, events, history)
		evidence = {
			"matched_ws_count": len(matched_ws),
			"history_checked": history["checked"],
			"history_matched_count": history["matched_count"],
		}
		if matched_ws or history["matched_count"]:
			data: dict[str, Any] = {
				"friendId": friend_id,
				"exchange_type": exchange_type,
				"componentName": result.get("componentName"),
				"confirmed": result.get("confirmed"),
				"log": result.get("log"),
				**evidence,
			}
			if side_effects:
				data["side_effects"] = side_effects
			return {"code": 0, "message": "Success", "zpData": data}

		err = "页面动作已执行，但未在聊天 WS 帧或聊天记录中确认发送"
		extra: dict[str, Any] = {
			"exchange_type": exchange_type,
			"componentName": result.get("componentName") or component_name,
			"history_checked": history["checked"],
			"history_matched_count": 0,
		}
		if history.get("error"):
			extra["history_error"] = history["error"]
		if side_effects:
			extra["side_effects"] = side_effects
		return self._chat_action_failure_response(
			message=f"exchange_request_by_friend unconfirmed: {err}；{_ACTION_UNCONFIRMED_HINT.format(friend_id=friend_id)}",
			payload=self._chat_action_failure_data(
				action="exchange",
				friend_id=friend_id,
				error=err,
				expected_bits=expected_bits,
				result=result,
				events=events,
				extra=extra,
			),
			error_code="ACTION_UNCONFIRMED",
		)

	def _chat_history_mids(self, friend_id: int) -> set[int] | None:
		"""读取动作前的消息 mid 快照；读不到时返回 None，事后改按时间判断。"""
		response = self.chat_history(friend_id, count=20, retry=False)
		if not isinstance(response, dict) or response.get("code") != 0:
			return None
		return {mid for message in history_messages(response.get("zpData")) if (mid := message_mid(message)) is not None}

	def _exchange_history_evidence(
		self,
		friend_id: int,
		texts: tuple[str, ...],
		*,
		baseline_mids: set[int] | None,
		since_ms: int,
	) -> dict[str, Any]:
		"""动作后回读聊天记录，统计新出现的请求消息；只读、失败不抛出。"""
		try:
			response = self.chat_history(friend_id, count=20, retry=False)
		except Exception as exc:  # noqa: BLE001 — 写操作已发生，回读失败只能记为未确认
			return {"checked": False, "matched_count": 0, "resume_matched_count": 0, "error": type(exc).__name__}
		if not isinstance(response, dict) or response.get("code") != 0:
			code = response.get("code") if isinstance(response, dict) else None
			return {"checked": False, "matched_count": 0, "resume_matched_count": 0, "error": f"chat_history code={code}"}
		data = response.get("zpData")
		matched = new_messages_matching(data, texts, baseline_mids=baseline_mids, since_ms=since_ms)
		resume = new_messages_matching(data, _RESUME_REQUEST_TEXTS, baseline_mids=baseline_mids, since_ms=since_ms)
		return {"checked": True, "matched_count": len(matched), "resume_matched_count": len(resume)}

	def _exchange_side_effects(
		self, exchange_type: int, events: list[dict[str, Any]], history: dict[str, Any]
	) -> list[str]:
		"""换手机/微信时如果同时出现了求简历消息，明确告诉调用方（#443）。"""
		if exchange_type == 4:
			return []
		resume_ws = self._matching_chat_send_events(events, list(_RESUME_REQUEST_TEXTS))
		if resume_ws or history.get("resume_matched_count"):
			return ["resume_request_detected"]
		return []

	def accept_resume_by_friend(self, friend_id: int, message_id: int) -> dict[str, Any]:
		"""同意指定会话中的附件简历请求；验证目标后仅发送一次 HTTP POST。

		网页 v11308：dialog.type=2 对应 agreeAction=3，不能使用求简历的 type=4。
		网页还注入动态 sigx；此处沿用原生 HTTP 认证，不伪造指纹或降级到浏览器。
		"""
		if friend_id <= 0 or message_id <= 0:
			raise ResumeValidationError("friend_id 和 message_id 必须为正整数")
		history = self.chat_history(friend_id, count=100, max_msg_id=message_id + 1, retry=False)
		if history.get("code") != 0:
			return history
		message = incoming_message(history.get("zpData"), friend_id, message_id)
		body = message.get("body") or {}
		dialog = body.get("dialog") if isinstance(body, dict) else None
		if not isinstance(dialog, dict) or body.get("type") != 7 or dialog.get("type") != _RESUME_REQUEST_DIALOG_TYPE:
			raise ResumeValidationError("该消息不是附件简历请求，不会执行联系方式交换")
		if dialog.get("operated") is not False:
			raise ResumeValidationError("该请求已处理或状态不明，不会重复同意")
		friends = self.friend_detail([friend_id], retry=False)
		if friends.get("code") != 0:
			return friends
		friend = resume_friend(friends.get("zpData"), friend_id)
		if not isinstance(friend.get("securityId"), str) or not friend["securityId"]:
			raise ResumeValidationError("无法取得指定候选人的当前会话 securityId")
		return self._request(
			"POST", ep.BOSS_EXCHANGE_ACCEPT_URL,
			data={"mid": message_id, "type": _RESUME_ACCEPT_TYPE, "securityId": friend["securityId"]},
			retry=False,
			follow_redirects=False,
		)

	def download_resume_by_friend(self, friend_id: int, message_id: int, output: Path) -> dict[str, Any]:
		"""下载已收到的指定附件；不自动同意请求，不返回临时下载凭据。"""
		if friend_id <= 0 or message_id <= 0:
			raise ResumeValidationError("friend_id 和 message_id 必须为正整数")
		if output.exists() or output.is_symlink():
			raise FileExistsError("输出文件已存在，不会覆盖")
		history = self.chat_history(friend_id, count=100, max_msg_id=message_id + 1, retry=False)
		if history.get("code") != 0:
			return history
		message = incoming_message(history.get("zpData"), friend_id, message_id)
		params = attachment_params(message)
		friends = self.friend_detail([friend_id], retry=False)
		if friends.get("code") != 0:
			return friends
		friend = resume_friend(friends.get("zpData"), friend_id)
		geek_id = friend.get("encryptUid")
		if not isinstance(geek_id, str) or not geek_id or geek_id in (".", ".."):
			raise ResumeValidationError("当前会话缺少候选人的 encryptUid")
		check = self._request("GET", ep.BOSS_RESUME_PREVIEW_CHECK_URL, params={"geekId": geek_id, **params}, retry=False, follow_redirects=False)
		if check.get("code") != 0:
			return check
		detail = check.get("zpData")
		if not isinstance(detail, dict) or detail.get("isResumeVisible") is not True or detail.get("expired") not in (None, False, 0):
			raise ResumeValidationError("附件不可访问或已过期，请在官方页面核对")
		# isCanPreview=false 仅表示无法在线预览，网页仍允许下载原附件。
		if isinstance(detail.get("d"), str) and detail["d"]:
			params["d"] = detail["d"]
		url = ep.BOSS_RESUME_DOWNLOAD_URL + quote(geek_id, safe="")
		self._throttle.wait()
		try:
			with self._get_client().stream("GET", url, params=params, headers={"Referer": ep.WEB_BOSS_CHAT}, follow_redirects=False) as response:
				file_data = save_resume(response, output)
		finally:
			self._throttle.mark()
		return {"code": 0, "zpData": file_data}

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
