"""Recruiter-side API client.

Dual-channel like BossClient: httpx for low-risk reads, browser for high-risk writes.
Endpoints sourced from newboss/boss-cli project (confirmed via reverse engineering).
"""

import atexit
import json
import re
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
	history_self_message_state,
	incoming_message,
	message_mid,
	new_action_cards_matching,
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
// 换电话/换微信按钮在招聘者没发过消息前是置灰的，此时 handleExChange 不会真的发请求。
// 只看组件自身状态和它自己的按钮元素：命中任一禁用信号 → disabled；
// 根元素可见且没有禁用信号 → enabled；其余（拿不到元素、不可见）→ unknown。
const EXCHANGE_DISABLED_CLASSES = ['disabled', 'is-disabled', 'disable', 'btn-disabled', 'unable'];
const EXCHANGE_DISABLED_ROOT_CLASSES = ['gray', 'grey'];
const EXCHANGE_DISABLED_KEYS = ['disabled', 'isDisabled', 'btnDisabled'];
const EXCHANGE_ENABLED_KEYS = ['canExchange', 'enable', 'enabled', 'available', 'isAvailable', 'canClick'];
const exchangeAvailability = (vm) => {
	const signals = [];
	for (const [label, state] of [['props', vm.$props], ['data', vm.$data]]) {
		if (!state || typeof state !== 'object') continue;
		for (const key of EXCHANGE_DISABLED_KEYS) if (state[key] === true) signals.push(label + '.' + key + '=true');
		for (const key of EXCHANGE_ENABLED_KEYS) if (state[key] === false) signals.push(label + '.' + key + '=false');
	}
	const root = vm.$el;
	if (!root || root.nodeType !== 1) return {state: signals.length ? 'disabled' : 'unknown', signals};
	const classTokens = (el) => Array.from(el.classList || []).map((token) => String(token).toLowerCase());
	const rootTokens = classTokens(root);
	for (const token of EXCHANGE_DISABLED_ROOT_CLASSES) if (rootTokens.includes(token)) signals.push('root.class=' + token);
	try {
		if (window.getComputedStyle(root).pointerEvents === 'none') signals.push('root.pointer-events=none');
	} catch (e) {}
	const elements = [root, ...Array.from(root.querySelectorAll('button, a, [class*="btn"]'))];
	for (const el of elements) {
		const tag = el === root ? 'root' : 'btn';
		if (el.disabled === true || (el.hasAttribute && el.hasAttribute('disabled'))) signals.push(tag + '.disabled');
		if (el.getAttribute && el.getAttribute('aria-disabled') === 'true') signals.push(tag + '.aria-disabled');
		for (const token of classTokens(el)) {
			if (EXCHANGE_DISABLED_CLASSES.includes(token)) signals.push(tag + '.class=' + token);
		}
	}
	if (signals.length) return {state: 'disabled', signals: Array.from(new Set(signals))};
	return {state: isVisibleElement(root) ? 'enabled' : 'unknown', signals};
};
"""

# 只读聊天页 geek-list 组件里已经加载好的会话列表（页面自己拉过的数据），不发任何请求。
# 字段名没有官方文档：按常见命名白名单挑字段，避免把整条大对象（简历等）带出来。
_CHAT_LIST_SNAPSHOT_JS = """
() => {
	const chatUser = document.querySelector('.chat-user');
	const root = chatUser && chatUser.__vue__;
	if (!root) return {ok: false, error: 'geek-list Vue component not found'};
	const KEYS = ['friendId', 'uid', 'friendSource', 'newMsgCount', 'unreadMsgCount', 'unreadCount', 'unread',
		'lastMsg', 'lastText', 'lastTime', 'lastTS', 'lastMsgTime', 'lastMsgStatus', 'lastIsSelf', 'updateTime', 'time'];
	const INFO_KEYS = ['showText', 'text', 'status', 'msgTime', 'fromId'];
	const looksLikeFriends = (value) => Array.isArray(value) && value.length > 0
		&& value.some((item) => item && typeof item === 'object' && ('friendId' in item || 'uid' in item));
	const candidates = [];
	const consider = (path, value) => { if (looksLikeFriends(value)) candidates.push([path, value]); };
	const scan = (prefix, obj, depth) => {
		if (!obj || typeof obj !== 'object' || depth > 3) return;
		for (const key of Object.keys(obj)) {
			let value;
			try { value = obj[key]; } catch (e) { continue; }
			consider(prefix + key, value);
			if (value && typeof value === 'object' && !Array.isArray(value) && depth < 3) scan(prefix + key + '.', value, depth + 1);
		}
	};
	// 实测会话列表在 vue-rx 订阅里：geek-list 的 list$、父组件 chat 的 allList$（实例属性 + $observables），
	// 不在 $data / computed / $store；虚拟列表 dataSources 也是同一份已加载数据（不只是可见行）。
	const scanRx = (prefix, vm) => {
		if (!vm) return;
		for (const key of Object.keys(vm)) {
			if (!key.endsWith('$')) continue;
			try { consider(prefix + key, vm[key]); } catch (e) {}
		}
		for (const key of Object.keys(vm.$observables || {})) {
			if (key in vm) continue;
			try { consider(prefix + 'observables.' + key, vm.$observables[key]._value); } catch (e) {}
		}
	};
	scanRx('rx.', root);
	scanRx('parent.rx.', root.$parent);
	scan('data.', root.$data, 1);
	for (const key of Object.keys(root._computedWatchers || {})) {
		try { consider('computed.' + key, root[key]); } catch (e) {}
	}
	if (root.$store && root.$store.state) scan('store.', root.$store.state, 1);
	if (!candidates.length) return {ok: false, error: 'no friend list found in geek-list'};
	candidates.sort((a, b) => b[1].length - a[1].length);
	const [source, list] = candidates[0];
	let hasMore = null;
	try { if (typeof root.hasMore$ === 'boolean') hasMore = root.hasMore$; } catch (e) {}
	// 聊天页左上角的未读总数：geek-list 的 uncountTab$（实测 {1: 总未读, 2: ?, 3: ?}），只取数字计数。
	let unreadByTab = null;
	try {
		const tabs = root.uncountTab$;
		if (tabs && typeof tabs === 'object' && !Array.isArray(tabs)) {
			unreadByTab = {};
			for (const key of Object.keys(tabs)) {
				if (typeof tabs[key] === 'number' && Number.isFinite(tabs[key])) unreadByTab[key] = tabs[key];
			}
		}
	} catch (e) {}
	const items = [];
	for (const raw of list) {
		if (!raw || typeof raw !== 'object') continue;
		const item = {};
		for (const key of KEYS) {
			const value = raw[key];
			if (value === undefined || value === null) continue;
			if (typeof value === 'object') continue;
			item[key] = value;
		}
		for (const infoKey of ['lastMsgInfo', 'lastMessageInfo']) {
			const info = raw[infoKey];
			if (!info || typeof info !== 'object') continue;
			const picked = {};
			for (const key of INFO_KEYS) if (info[key] !== undefined && info[key] !== null && typeof info[key] !== 'object') picked[key] = info[key];
			item[infoKey] = picked;
		}
		items.push(item);
	}
	return {ok: true, source, count: items.length, has_more: hasMore, unread_by_tab: unreadByTab, items};
}
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

if (args.checkAvailability) {
	const availability = exchangeAvailability(vm);
	log.push('availability=' + availability.state + (availability.signals.length ? ' [' + availability.signals.join(',') + ']' : ''));
	const blockedByHistory = availability.state === 'unknown' && args.selfMessageInHistory === false;
	if (availability.state === 'disabled' || blockedByHistory) {
		return {
			ok: false,
			unavailable: true,
			error: 'exchange button not available',
			availability: availability.state,
			availability_signals: availability.signals,
			log,
			componentName: args.componentName,
		};
	}
}

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
# 部分请求在聊天记录/WS 帧里是不带文案的动作卡片：body = {"type": 4, "action": {"aid": N}}。
# 已实测：aid 37 = 求附件简历，aid 32 = 换微信请求。换手机号的 aid 还没抓到样本，
# 先记为 None：接受任意「自己发出、动作开始后新出现、且不是已知其他类型 aid」的动作卡片。
_RESUME_REQUEST_AID = 37
_WECHAT_EXCHANGE_AID = 32
_KNOWN_ACTION_AIDS: frozenset[int] = frozenset({_RESUME_REQUEST_AID, _WECHAT_EXCHANGE_AID})
_EXCHANGE_ACTION_AIDS: dict[int, frozenset[int] | None] = {
	1: None,
	2: frozenset({_WECHAT_EXCHANGE_AID}),
	4: frozenset({_RESUME_REQUEST_AID}),
}
_WS_AID_RE = re.compile(r'"aid"\s*:\s*"?(\d+)')
# 换电话/换微信要求招聘者先在会话里发过消息；求简历（type 4）不受限制，不做预检。
_EXCHANGE_AVAILABILITY_CHECK_TYPES = frozenset({1, 2})
_EXCHANGE_TYPE_LABELS = {1: "换电话", 2: "换微信", 4: "求简历"}
_EXCHANGE_NOT_AVAILABLE_MESSAGE = (
	"对方还未与你互相沟通，{label}按钮未解锁；请先回复候选人"
	"（如 boss hr request-resume {friend_id} 或 boss hr reply {friend_id} <消息>）后再试"
)
_HISTORY_PAGE_SIZE = 20
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
	# exchange 动作后没有 WS 证据时，聊天记录回读的重试间隔（秒）；首读 + 3 次重试，约 6s。
	_EXCHANGE_HISTORY_RETRY_DELAYS_S: tuple[float, ...] = (1.5, 2.0, 2.5)

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
		# 聊天页动作会让页面自己发平台请求：动作前先核对 code 37 风控锁（裸 CDP 读 stoken，不附着 patchright）。
		self._check_cdp_risk_lock_raw()
		if listen_ms is None:
			return browser.evaluate_js(script, args), []

		capture = browser.evaluate_js_with_chat_events(script, args, listen_ms=listen_ms)
		result = capture.get("value") if isinstance(capture, dict) else capture
		events = cast("list[dict[str, Any]]", capture.get("events", []) if isinstance(capture, dict) else [])
		risk = self._page_risk_response(events)
		if risk is not None and risk.get("code") == ep.CODE_STOKEN_EXPIRED and classify_code_37(risk) == "environment_risk":
			self._record_cdp_risk_lock_raw()
		return result, events

	@staticmethod
	def _page_risk_response(events: list[dict[str, Any]]) -> dict[str, Any] | None:
		"""页面动作期间页面请求命中的第一条风控响应（code 36/37），整理成平台响应形状。"""
		for event in events:
			if event.get("kind") == "http_risk" and event.get("code") in (ep.CODE_ACCOUNT_RISK, ep.CODE_STOKEN_EXPIRED):
				return {"code": event["code"], "message": event.get("message") or "", "path": event.get("path")}
		return None

	def _page_risk_failure(self, *, action: str, friend_id: int, risk: dict[str, Any], result: Any,
			events: list[dict[str, Any]], expected_bits: list[str], extra: dict[str, Any] | None = None) -> dict[str, Any]:
		"""页面请求命中风控：按平台风控码返回（parse_error 映射到 ENVIRONMENT_RISK / ACCOUNT_RISK），立即停止。"""
		message = risk.get("message") or ("访问环境存在异常" if risk["code"] == ep.CODE_STOKEN_EXPIRED else "账户存在异常行为")
		payload = self._chat_action_failure_data(
			action=action, friend_id=friend_id,
			error=f"页面请求 {risk.get('path')} 返回 code {risk['code']}",
			expected_bits=expected_bits, result=result, events=events, extra=extra,
		)
		payload["page_risk"] = {"path": risk.get("path"), "code": risk["code"]}
		response = self._chat_action_failure_response(
			message=f"BOSS 直聘风控拦截 (code {risk['code']}): {message}；聊天页动作已停止，勿重试",
			payload=payload,
		)
		response["code"] = risk["code"]
		response["__cli_error_details__"]["page_risk"] = payload["page_risk"]
		return response

	def _matching_chat_send_events(self, events: list[dict[str, Any]], expected_bits: list[str]) -> list[dict[str, Any]]:
		return [
			event
			for event in events
			if event.get("kind") == "ws_send"
			and int(event.get("bytes", 0)) >= 100
			and any(expected in bit for expected in expected_bits for bit in event.get("utf8_bits", []))
		]

	@staticmethod
	def _exchange_aid_filter(exchange_type: int) -> tuple[frozenset[int] | None, frozenset[int]]:
		"""返回 (aids, exclude_aids)；aid 未知的类型排除掉已知属于其他类型的 aid。"""
		aids = _EXCHANGE_ACTION_AIDS.get(exchange_type)
		exclude = _KNOWN_ACTION_AIDS if aids is None else frozenset()
		return aids, exclude

	def _matching_action_card_events(
		self, events: list[dict[str, Any]], aids: frozenset[int] | None, exclude_aids: frozenset[int] = frozenset()
	) -> list[dict[str, Any]]:
		"""WS 发送帧里如果带有动作卡片的 JSON（"aid": N），按 aid 认定发送证据。"""
		matched: list[dict[str, Any]] = []
		for event in events:
			if event.get("kind") != "ws_send" or int(event.get("bytes", 0)) < 100:
				continue
			found = {int(m) for bit in event.get("utf8_bits", []) for m in _WS_AID_RE.findall(bit)}
			found -= exclude_aids
			if found and (aids is None or found & aids):
				matched.append(event)
		return matched

	def _exchange_ws_matches(
		self, events: list[dict[str, Any]], expected_bits: list[str], exchange_type: int
	) -> list[dict[str, Any]]:
		aids, exclude = self._exchange_aid_filter(exchange_type)
		cards = self._matching_action_card_events(events, aids, exclude)
		texts = self._matching_chat_send_events(events, expected_bits)
		return texts + [event for event in cards if not any(event is t for t in texts)]

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
				"history_checked", "history_matched_count", "history_attempts", "history_error", "side_effects",
				"availability", "availability_signals", "history_self_message")
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

	def chat_list_snapshot(self) -> dict[str, Any] | None:
		"""CDP 模式下读取聊天页已加载的会话列表摘要（未读数、最后一条消息、时间）。

		只在页面里执行只读脚本，不发平台请求；非 CDP 模式、没开聊天页或页面结构不符时返回 None。
		"""
		if not self.is_browser_only():
			return None
		try:
			# 必须带参数：evaluate_js 只有 arg 不为 None 时才包成 (fn)(arg) 调用，
			# 否则 Runtime.evaluate 拿回的是函数对象本身，快照永远为空（#456 后续实测）。
			result = self._get_browser().evaluate_js(_CHAT_LIST_SNAPSHOT_JS, {})
		except Exception:  # noqa: BLE001 — 页面快照只是增强，失败时调用方退回接口数据
			return None
		return result if isinstance(result, dict) and result.get("ok") else None

	def last_messages(self, friend_ids: list[int], *, src: int = 0) -> dict[str, Any]:
		"""userLastMsg：前端按 friendSource 分组（BOSS 好友 src=0，店长直聘 src=1），每批 ≤100。

		一次塞几百个 friendId 会被平台拒绝（「未知的非法参数」），批量切分由调用方负责。
		"""
		data = {"friendIds": ",".join(str(i) for i in friend_ids), "src": src}
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

		if (risk := self._page_risk_response(events)) is not None:
			return self._page_risk_failure(
				action="reply", friend_id=friend_id, risk=risk, result=result, events=events, expected_bits=[content],
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
		    （实测聊天记录里可能只有动作卡片 body={type:4, action:{aid:32}}，没有文案，
		    所以也按自己发出的新动作卡片 aid 判定；求简历卡片是 aid 37）
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
		baseline_mids, self_message = self._chat_history_snapshot(friend_id)
		check_availability = exchange_type in _EXCHANGE_AVAILABILITY_CHECK_TYPES
		started_ms = int(time.time() * 1000)
		result, events = self._run_chat_frontend_action(
			friend_data=friend_data,
			action_js=_EXCHANGE_ACTION_JS,
			require_security_id=True,
			settle_ms=1000,
			extra_args={
				"componentName": component_name,
				"checkAvailability": check_availability,
				"selfMessageInHistory": self_message,
				"preConfirmUiWaitMs": 1000,
				"postConfirmUiWaitMs": 800,
			},
			listen_ms=3000,
		)

		if (risk := self._page_risk_response(events)) is not None:
			return self._page_risk_failure(
				action="exchange", friend_id=friend_id, risk=risk, result=result, events=events,
				expected_bits=expected_bits, extra={"exchange_type": exchange_type, "componentName": component_name},
			)
		if isinstance(result, dict) and result.get("unavailable") and check_availability:
			# 按钮未解锁：handleExChange 没有被调用，什么都没发出去，可以放心先回复再重试。
			return self._chat_action_failure_response(
				message=_EXCHANGE_NOT_AVAILABLE_MESSAGE.format(
					label=_EXCHANGE_TYPE_LABELS[exchange_type], friend_id=friend_id,
				),
				payload=self._chat_action_failure_data(
					action="exchange",
					friend_id=friend_id,
					error=self._page_error_message(result),
					expected_bits=[],
					result=result,
					events=events,
					extra={
						"exchange_type": exchange_type,
						"componentName": component_name,
						"availability": result.get("availability"),
						"availability_signals": result.get("availability_signals") or [],
						"history_self_message": self_message,
					},
				),
				error_code="EXCHANGE_NOT_AVAILABLE",
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

		matched_ws = self._exchange_ws_matches(events, expected_bits, exchange_type)
		history = self._exchange_history_evidence(
			friend_id, expected_texts, exchange_type=exchange_type, baseline_mids=baseline_mids, since_ms=started_ms,
		)
		history_attempts = 1
		if not matched_ws:
			# 消息落库可能晚于 WS 监听窗口：没有 WS 证据时短暂轮询聊天记录再下结论。
			for delay in self._EXCHANGE_HISTORY_RETRY_DELAYS_S:
				if history["matched_count"]:
					break
				time.sleep(delay)
				history_attempts += 1
				history = self._exchange_history_evidence(
					friend_id, expected_texts, exchange_type=exchange_type,
					baseline_mids=baseline_mids, since_ms=started_ms,
				)
		side_effects = self._exchange_side_effects(exchange_type, events, history)
		evidence = {
			"matched_ws_count": len(matched_ws),
			"history_checked": history["checked"],
			"history_attempts": history_attempts,
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
		if check_availability and self_message is False:
			# 按钮看起来可用但聊天记录里没有自己的消息：信号冲突时不拦截，只在提示里点出最可能的原因。
			err += "；聊天记录里还没有你发出的消息，{label}按钮可能尚未解锁".format(
				label=_EXCHANGE_TYPE_LABELS[exchange_type],
			)
		extra: dict[str, Any] = {
			"exchange_type": exchange_type,
			"componentName": result.get("componentName") or component_name,
			"history_checked": history["checked"],
			"history_matched_count": 0,
			"history_attempts": history_attempts,
		}
		if check_availability:
			extra["history_self_message"] = self_message
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

	def _chat_history_snapshot(self, friend_id: int) -> tuple[set[int] | None, bool | None]:
		"""读取动作前的聊天记录快照：(消息 mid 集合, 是否有自己发过的消息)。

		mid 读不到时返回 None，事后改按时间判断；自己是否发过消息拿不准时返回 None。
		"""
		response = self.chat_history(friend_id, count=_HISTORY_PAGE_SIZE, retry=False)
		if not isinstance(response, dict) or response.get("code") != 0:
			return None, None
		data = response.get("zpData")
		mids = {mid for message in history_messages(data) if (mid := message_mid(message)) is not None}
		return mids, history_self_message_state(data, friend_id, page_size=_HISTORY_PAGE_SIZE)

	def _exchange_history_evidence(
		self,
		friend_id: int,
		texts: tuple[str, ...],
		*,
		exchange_type: int,
		baseline_mids: set[int] | None,
		since_ms: int,
	) -> dict[str, Any]:
		"""动作后回读聊天记录，统计新出现的请求消息（文案或动作卡片）；只读、失败不抛出。"""
		try:
			response = self.chat_history(friend_id, count=20, retry=False)
		except Exception as exc:  # noqa: BLE001 — 写操作已发生，回读失败只能记为未确认
			return {"checked": False, "matched_count": 0, "resume_matched_count": 0, "error": type(exc).__name__}
		if not isinstance(response, dict) or response.get("code") != 0:
			code = response.get("code") if isinstance(response, dict) else None
			return {"checked": False, "matched_count": 0, "resume_matched_count": 0, "error": f"chat_history code={code}"}
		data = response.get("zpData")
		aids, exclude = self._exchange_aid_filter(exchange_type)

		def _new_matches(match_texts: tuple[str, ...], card_aids: frozenset[int] | None,
				card_exclude: frozenset[int]) -> int:
			found = new_messages_matching(data, match_texts, baseline_mids=baseline_mids, since_ms=since_ms)
			found += new_action_cards_matching(
				data, friend_id=friend_id, aids=card_aids, exclude_aids=card_exclude,
				baseline_mids=baseline_mids, since_ms=since_ms,
			)
			return len({id(message) for message in found})

		matched_count = _new_matches(texts, aids, exclude)
		resume_count = _new_matches(_RESUME_REQUEST_TEXTS, frozenset({_RESUME_REQUEST_AID}), frozenset())
		return {"checked": True, "matched_count": matched_count, "resume_matched_count": resume_count}

	def _exchange_side_effects(
		self, exchange_type: int, events: list[dict[str, Any]], history: dict[str, Any]
	) -> list[str]:
		"""换手机/微信时如果同时出现了求简历消息，明确告诉调用方（#443）。"""
		if exchange_type == 4:
			return []
		resume_ws = self._matching_chat_send_events(events, list(_RESUME_REQUEST_TEXTS)) or \
			self._matching_action_card_events(events, frozenset({_RESUME_REQUEST_AID}))
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
