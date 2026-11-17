"""Automation execution primitives."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from typing import Any, Final

from boss_agent_cli.api.browser_client import ChatTabCdpUnreachable, RecruiterChatTabRequired
from boss_agent_cli.api.browser_source import BrowserSourceUnavailable
from boss_agent_cli.api.request_budget import RequestBudgetExceeded
from boss_agent_cli.automation.adapters import RecruiterAutomationPlatform
from boss_agent_cli.automation.config import AutomationConfig
from boss_agent_cli.automation.decision import decide_action
from boss_agent_cli.automation.events import make_event, now_iso
from boss_agent_cli.automation.models import (
	AutomationEvent,
	CandidateKey,
	ConversationRef,
	Decision,
	EventStatus,
	PendingAction,
	PlatformAction,
)
from boss_agent_cli.automation.reply_ai import apply_reply_strategy
from boss_agent_cli.automation.safety import SafetyGuard
from boss_agent_cli.automation.storage import AutomationStore, queued_action

#: `state.json` 里每个会话的在途动作标记。直接扫描路径没有待执行队列行可以
#: 提前标记，必须在派发前把「可能已发出」的动作落进 state（Issue #1511）。
_INFLIGHT_KEY: Final = "inflight_action"
_INFLIGHT_EXECUTING: Final = "executing"
_INFLIGHT_VERIFICATION_REQUIRED: Final = "verification-required"
_INFLIGHT_STATUSES: Final = frozenset({_INFLIGHT_EXECUTING, _INFLIGHT_VERIFICATION_REQUIRED})
#: 标记推进到 verification-required 后，「操作者已收到核对要求」是否已经落盘。
#: 旧版本写下的标记没有该字段，按已通知处理以保持兼容；本版本在打开熔断前
#: 显式写 False，熔断落盘成功后才改 True（Issue #1517）。
_INFLIGHT_NOTIFIED_KEY: Final = "notified"


@dataclass(frozen=True, slots=True)
class InflightAction:
	"""从 `state.json` 读出的会话在途动作（字段形状不可信，缺失字段按空串处理）。"""

	status: str
	action: PlatformAction | None
	started_at: str
	conversation_ref: str
	message_hash: str


def process_pending(
	adapter: RecruiterAutomationPlatform,
	store: AutomationStore,
	guard: SafetyGuard,
	platform: str,
	dry_run: bool,
	*,
	state: dict[str, Any] | None = None,
	dispatched: list[bool] | None = None,
) -> list[AutomationEvent]:
	actions = store.read_pending()
	events: list[AutomationEvent] = []
	for index, item in enumerate(actions):
		if guard.stop_reason() is not None:
			break
		if item.status == "executing":
			# 上一次执行在写请求已发出后中断：结果未知，不能自动重发（Issue #1245）。
			# 转成 verification-required 并打开熔断，先让操作者核对官方页面。
			if item.platform != platform:
				continue
			reason = f"pending action {item.id!r} was interrupted while executing"
			if dry_run:
				# 预览不是执行：dry-run 不改写队列、不打开熔断（Issue #1257）。
				# 只报告真实运行会怎么处理这条在途记录，操作者预览后不需要额外
				# `--resume` 才能继续。
				event = make_event(
					platform,
					item.candidate_key,
					queued_action(item, store.pending_path),
					EventStatus.DRY_RUN,
					item.confidence,
					f"dry-run: {reason}；真实运行会把它转为 verification-required 并要求人工核对，不会自动重发",
				)
				events.append(event)
				store.append_event(event)
				break
			prior = _conversation_prior(state, item.candidate_key)
			if prior is not None and state is not None:
				# 队列行与扫描路径共享同一份会话在途标记：否则 `--resume` 后
				# 扫描路径看不到这条结果未知的队列动作，会按 transcript 再发一次
				# （Issue #1515）。标记先落盘，熔断成功后才置为已通知（#1517）。
				_record_queue_inflight(prior, item)
				store.write_state(state)
			guard.open_circuit_breaker(f"platform verification required: {reason}")
			if prior is not None and state is not None:
				_mark_inflight_notified(prior)
				_try_write_state(store, state)
			actions[index] = _pending_with_status(item, "verification-required")
			# 队列标记先落盘，再写事件：事件写失败也不会让该行回到可重放状态。
			store.write_pending(actions)
			event = make_event(
				platform,
				item.candidate_key,
				queued_action(item, store.pending_path),
				EventStatus.PLATFORM_VERIFICATION_REQUIRED,
				item.confidence,
				reason,
			)
			events.append(event)
			store.append_event(event)
			break
		if item.status != "pending":
			continue
		action = queued_action(item, store.pending_path)
		ref = ConversationRef(id=item.candidate_key, tab="pending")
		decision = Decision(
			action=action,
			confidence=item.confidence,
			reason=item.reason,
			candidate_key=CandidateKey(item.candidate_key),
			message=item.message,
		)
		if item.platform != platform:
			continue
		prior = _conversation_prior(state, item.candidate_key)
		if prior is not None and state is not None:
			blocked = _queue_inflight_gate(store, guard, state, platform, dry_run, item.candidate_key, prior)
			if blocked is not None:
				event, stop = blocked
				events.append(event)
				if stop:
					break
				continue
		if not dry_run:
			# 先落盘「执行中」再派发：进程在写请求发出后中断时，下一轮看到
			# executing 行就知道结果未知，不会把可能已生效的动作重发一遍
			# （Issue #1245）。写盘失败时动作尚未发出，行仍是 pending，可安全重试。
			actions[index] = _pending_with_status(item, "executing")
			store.write_pending(actions)
			actions[index] = item
			if prior is not None and state is not None:
				# 队列路径同样在派发前落会话级在途标记：队列行只保护队列自己，
				# 扫描路径读不到它；两边共用标记后任意路径中断都不会重发
				# （Issue #1514）。
				_start_inflight(prior, decision, ref)
				try:
					store.write_state(state)
				except Exception:
					# 状态写不进去（控制锁被占 / 磁盘故障）不能挡住派发：队列行
					# 已落盘为 executing，本身就是重放屏障；进程中断后下一轮会
					# 把它转成 verification-required 并补写标记。发送成功而状态
					# 仍未落盘时，队列行保持 executing，不会退回 pending 重放。
					_clear_inflight(prior)
			# 队列已把这一行标成 executing：接下来动作可能真的发出，控制锁
			# 冲突的语义要按「可能已生效」处理（Issue #1307）。
			if dispatched is not None:
				dispatched[0] = True
		try:
			status, event_reason = execute_or_dry_run(adapter, guard, decision, ref, dry_run)
		except (ChatTabCdpUnreachable, RecruiterChatTabRequired, BrowserSourceUnavailable):
			# 这三类异常都表示请求尚未发出（聊天页未就绪 / CDP 端点不可达 / 浏览器
			# 通道全部不可用）：队列行必须回到 pending，否则下一轮会把一个从未派发
			# 的动作当成结果未知，转成 verification-required 并打开熔断（Issue #1469）。
			# 会话级在途标记同样释放（Issue #1514）。
			if not dry_run:
				if prior is not None and state is not None:
					_release_inflight(prior, store, state)
				actions[index] = _pending_with_status(item, "pending")
				try:
					store.write_pending(actions)
				except Exception:
					# 还原写盘失败不能掩盖原始的「请求未发出」异常：调用方仍要看到
					# 真正的失败原因，而不是一条本地写错误；行留在 executing 时下一轮
					# 仍按结果未知处理，保守但不会重发。
					pass
			raise
		except RequestBudgetExceeded as exc:
			# 预算 / 时限在动作发出前抛出的停止（details.request_sent=False）可以安全
			# 恢复 pending；其它异常（含中断与响应风控）请求可能已发出，保守保留
			# executing，下一轮按结果未知处理（Issue #1245）。
			if exc.details.get("request_sent") is False:
				if not dry_run and prior is not None and state is not None:
					_release_inflight(prior, store, state)
				actions[index] = _pending_with_status(item, "pending")
				store.write_pending(actions)
			raise
		next_status = _next_pending_status(status)
		if not dry_run:
			if prior is not None and state is not None:
				# 先持久化 state（prior / 在途标记），再把队列行推进到最终状态：
				# 平台已确认发送但 state 没写下去时，队列行仍是 executing，下一轮
				# 按结果未知处理，不会因为 prior 丢失而重发（Issue #1516）。
				if status is EventStatus.AUTO_EXECUTED:
					update_prior(prior, decision)
					_clear_inflight(prior)
				elif status is EventStatus.PLATFORM_VERIFICATION_REQUIRED:
					_mark_inflight_verification_required(prior)
					# 熔断已由 execute_or_dry_run 落盘，标记可以直接算作已通知。
					_mark_inflight_notified(prior)
				else:
					# 安全停止 / 平台确定性拒绝发生在确认发送之前或没有产生写入：
					# 释放在途标记，后续轮次可以重新决策。
					_clear_inflight(prior)
				store.write_state(state)
			actions[index] = _pending_with_status(item, next_status)
			# Commit each confirmed queue effect before attempting another item. A
			# later risk/error must not leave a successful action pending for replay.
			store.write_pending(actions)
		# 预览不是执行：dry-run 期间写 prior 会让下一次真实运行误以为动作已发生
		# （Issue #889），因此只有真正执行成功才更新业务状态。
		guard.checkpoint()
		event = make_event(
			platform,
			item.candidate_key,
			action,
			status,
			decision.confidence,
			event_reason,
		)
		events.append(event)
		store.append_event(event)
	return events


def process_ref(
	adapter: RecruiterAutomationPlatform,
	store: AutomationStore,
	config: AutomationConfig,
	guard: SafetyGuard,
	state: dict[str, Any],
	platform: str,
	dry_run: bool,
	ref: ConversationRef,
	*,
	dispatched: list[bool] | None = None,
) -> AutomationEvent:
	conversation = adapter.read_conversation(ref)
	candidate_key = conversation.title or str(conversation.fingerprint) or ref.id
	prior = state.setdefault("conversations", {}).setdefault(candidate_key, {})
	inflight = _read_inflight(prior)
	if inflight is not None:
		# 直接扫描路径没有队列行可以恢复：上一轮在发送前后中断时，这里必须按
		# 「结果未知」停下，绝不凭 transcript 重新决策（Issue #1511）。
		return _process_inflight(store, guard, state, platform, dry_run, candidate_key, prior, inflight)
	decision = decide_action(conversation, config, prior)
	decision = apply_reply_strategy(decision, conversation, config, store.root.parent)
	status = status_for_decision(config, decision, dry_run)
	event_reason = decision.reason
	if decision.action is PlatformAction.CREATE_INTERVIEW_LEAD and status in {
		EventStatus.AUTO_EXECUTED,
		EventStatus.DRY_RUN,
	}:
		safety = guard.before_action(decision)
		if safety.allowed:
			# 预览不落盘线索记录（Issue #889）：dry-run 后重复运行不能看见
			# 一份从未真实产生过的面试线索清单。
			if status is EventStatus.AUTO_EXECUTED:
				store.append_interview_lead(candidate_key, decision.interview_time, decision.reason)
		else:
			status = EventStatus.CIRCUIT_BREAKER_OPEN if safety.circuit_breaker else EventStatus.STOPPED_BY_SAFETY
			event_reason = safety.reason
	elif status is EventStatus.AUTO_EXECUTED or status is EventStatus.DRY_RUN:
		if status is EventStatus.AUTO_EXECUTED:
			# 直接扫描路径没有待执行队列行：先把这个可能发出的动作落进 state，
			# 进程在发送后被杀 / checkpoint 撞控制锁时，下一轮看到未 settle 的
			# 在途标记就转 verification-required，不再凭 transcript 重发
			# （Issue #1511）。标记写盘失败时动作尚未发出，可安全重试。
			_start_inflight(prior, decision, ref)
			store.write_state(state)
		# 真实执行会向平台发请求：先标记，之后控制锁冲突按「可能已生效」处理
		# （Issue #1307）。
		if dispatched is not None and not dry_run:
			dispatched[0] = True
		try:
			status, event_reason = execute_or_dry_run(adapter, guard, decision, ref, dry_run)
		except (ChatTabCdpUnreachable, RecruiterChatTabRequired, BrowserSourceUnavailable):
			# 请求尚未发出：释放在途标记再抛出，避免下一轮把从未派发的动作
			# 转成 verification-required（与队列 #1469 同口径，Issue #1511）。
			_release_inflight(prior, store, state)
			raise
		except RequestBudgetExceeded as exc:
			# details.request_sent=False 明确表示请求未发出，可以安全释放；
			# 其它预算停止与超时 / 风控一样保留标记，下一轮按结果未知处理。
			if exc.details.get("request_sent") is False:
				_release_inflight(prior, store, state)
			raise
		except Exception:
			# 超时、风控、控制锁冲突等异常都无法证明请求未发出：保留在途标记，
			# 下一轮转 verification-required 等人工核对，绝不自动重发。
			raise
		if status is EventStatus.AUTO_EXECUTED:
			# 只有真正执行成功才把动作记进对话阶段；dry-run 写 prior 会让下一次
			# 真实运行误判「问卷已发」并跳过它（Issue #889）。
			update_prior(prior, decision)
			# 发送已确认：完成字段与在途标记在同一次 checkpoint 里原子落盘。
			_clear_inflight(prior)
		elif status is EventStatus.PLATFORM_VERIFICATION_REQUIRED:
			# 动作可能已生效：标记推进到 verification-required，随 checkpoint 落盘。
			_mark_inflight_verification_required(prior)
		else:
			# STOPPED_BY_SAFETY / CIRCUIT_BREAKER_OPEN 都发生在确认发送之前，
			# 或由平台的确定性拒绝产生：释放在途标记，后续轮次可以重新决策。
			_clear_inflight(prior)
	guard.checkpoint()
	event = make_event(
		platform,
		candidate_key,
		decision.action,
		status,
		decision.confidence,
		event_reason,
	)
	store.append_event(event)
	return event


def status_for_decision(
	config: AutomationConfig,
	decision: Decision,
	dry_run: bool,
) -> EventStatus:
	match decision.action:
		case PlatformAction.SKIP | PlatformAction.QUEUE_REVIEW:
			return EventStatus.SKIPPED
		case PlatformAction.CREATE_INTERVIEW_LEAD:
			return _lead_status(config, decision, dry_run)
		case _:
			return _action_status(config, decision, dry_run)


def execute_or_dry_run(
	adapter: RecruiterAutomationPlatform,
	guard: SafetyGuard,
	decision: Decision,
	ref: ConversationRef,
	dry_run: bool,
) -> tuple[EventStatus, str]:
	stop = guard.stop_reason()
	if stop is not None:
		return EventStatus.CIRCUIT_BREAKER_OPEN, stop
	safety = guard.before_action(decision, adapter.detect_safety_warning() or "")
	if not safety.allowed:
		if safety.circuit_breaker:
			if not dry_run:
				# 预览不是执行：dry-run 只报告真实运行会打开熔断，不落 stop.json /
				# 状态闩锁，否则一次演练会把后续真实运行全部挡到显式 --resume
				# 之后（Issue #1459）。
				guard.open_circuit_breaker(safety.reason)
			return EventStatus.CIRCUIT_BREAKER_OPEN, safety.reason
		return EventStatus.STOPPED_BY_SAFETY, safety.reason
	if dry_run:
		return EventStatus.DRY_RUN, decision.reason
	result = adapter.execute_action(decision.action, decision.message, ref)
	if result.status == "unconfirmed":
		# 结果可能已生效：不重试、不继续处理其他候选人，
		# 先停下来让操作者在官方页面核对后再显式 --resume。
		reason = str(result.details.get("reason") or "ACTION_UNCONFIRMED")
		guard.open_circuit_breaker(f"platform verification required: {reason}")
		return EventStatus.PLATFORM_VERIFICATION_REQUIRED, reason
	if result.status != "executed":
		reason = str(result.details.get("reason", result.status))
		guard.record_failure(reason)
		return EventStatus.STOPPED_BY_SAFETY, reason
	guard.after_action(decision)
	return EventStatus.AUTO_EXECUTED, decision.reason


def update_prior(prior: dict[str, Any], decision: Decision) -> None:
	match decision.action:
		case PlatformAction.SEND_QUESTIONNAIRE:
			prior["questionnaire_sent_at"] = now_iso()
		case PlatformAction.SEND_FOLLOW_UP:
			prior["follow_up_sent_at"] = now_iso()
		case PlatformAction.EXCHANGE_CONTACT:
			prior["exchange_contact_at"] = now_iso()
		case _:
			return


def _read_inflight(prior: dict[str, Any]) -> InflightAction | None:
	"""读取会话在途动作标记；字段缺失表示没有在途动作。

	`state.json` 可能来自旧版本或手工编辑：非对象、非字符串动作、未知状态都按
	「存在未确认动作」fail-closed，让这一轮停下等人工核对，而不是凭 transcript
	重新决策后把可能已发出的消息再发一遍（Issue #1511）。
	"""
	if _INFLIGHT_KEY not in prior:
		return None
	raw = prior[_INFLIGHT_KEY]
	if not isinstance(raw, dict):
		return InflightAction(
			status=_INFLIGHT_EXECUTING,
			action=None,
			started_at="",
			conversation_ref="",
			message_hash="",
		)
	status_raw = raw.get("status", _INFLIGHT_EXECUTING)
	status = (
		status_raw
		if isinstance(status_raw, str) and status_raw in _INFLIGHT_STATUSES
		else _INFLIGHT_EXECUTING
	)
	action: PlatformAction | None = None
	action_raw = raw.get("action")
	if isinstance(action_raw, str):
		try:
			action = PlatformAction(action_raw)
		except ValueError:
			action = None
	return InflightAction(
		status=status,
		action=action,
		started_at=_text_field(raw, "started_at"),
		conversation_ref=_text_field(raw, "conversation_ref"),
		message_hash=_text_field(raw, "message_hash"),
	)


def _text_field(raw: dict[str, Any], key: str) -> str:
	value = raw.get(key)
	return value if isinstance(value, str) else ""


def _start_inflight(prior: dict[str, Any], decision: Decision, ref: ConversationRef) -> None:
	"""派发前把可能发出的动作写进会话状态。

	去重判据是会话 + 动作 + 标记本身，与消息文本无关：本地 AI 改写后的文案不会
	出现在 transcript 模板匹配里，这里记录的 hash 只用于人工核对发了哪条消息。
	"""
	digest = hashlib.sha256(decision.message.encode("utf-8", errors="backslashreplace")).hexdigest()
	prior[_INFLIGHT_KEY] = {
		"action": decision.action.value,
		"message_hash": f"sha256:{digest}",
		"started_at": now_iso(),
		"conversation_ref": ref.id,
		"status": _INFLIGHT_EXECUTING,
	}


def _clear_inflight(prior: dict[str, Any]) -> None:
	prior.pop(_INFLIGHT_KEY, None)


def _mark_inflight_verification_required(prior: dict[str, Any]) -> None:
	raw = prior.get(_INFLIGHT_KEY)
	record = dict(raw) if isinstance(raw, dict) else {}
	record["status"] = _INFLIGHT_VERIFICATION_REQUIRED
	record["updated_at"] = now_iso()
	if record.get(_INFLIGHT_NOTIFIED_KEY) is not True:
		# 打开熔断前先显式记下「操作者还没收到核对要求」：两步之间进程被杀时，
		# 下一轮必须重开熔断，而不是把标记误当成已通知（Issue #1517）。
		record[_INFLIGHT_NOTIFIED_KEY] = False
	prior[_INFLIGHT_KEY] = record


def _mark_inflight_notified(prior: dict[str, Any]) -> None:
	raw = prior.get(_INFLIGHT_KEY)
	if isinstance(raw, dict):
		raw[_INFLIGHT_NOTIFIED_KEY] = True


def _inflight_needs_notification(prior: dict[str, Any]) -> bool:
	"""verification-required 标记是否还没成功通知操作者。"""
	raw = prior.get(_INFLIGHT_KEY)
	return isinstance(raw, dict) and raw.get(_INFLIGHT_NOTIFIED_KEY) is False


def _try_write_state(store: AutomationStore, state: dict[str, Any]) -> None:
	"""尽力写回 state：失败时磁盘上的「未通知」标记只会让下一轮重开一次熔断。"""
	try:
		store.write_state(state)
	except Exception:
		pass


def _conversation_prior(state: dict[str, Any] | None, candidate_key: str) -> dict[str, Any] | None:
	"""取出（必要时创建）候选人的会话状态；没有 state 时返回 None。"""
	if state is None:
		return None
	conversations = state.get("conversations")
	if not isinstance(conversations, dict):
		conversations = {}
		state["conversations"] = conversations
	prior = conversations.get(candidate_key)
	if not isinstance(prior, dict):
		prior = {}
		conversations[candidate_key] = prior
	return prior


def _record_queue_inflight(prior: dict[str, Any], item: PendingAction) -> None:
	"""把中断的队列行同步成会话级在途标记，供扫描路径共用（Issue #1515）。"""
	if _read_inflight(prior) is not None:
		# 已有标记（例如扫描路径上次写到一半）：只推进状态，不覆盖原始动作信息。
		_mark_inflight_verification_required(prior)
		return
	message_hash = ""
	if item.message:
		digest = hashlib.sha256(item.message.encode("utf-8", errors="backslashreplace")).hexdigest()
		message_hash = f"sha256:{digest}"
	prior[_INFLIGHT_KEY] = {
		"action": item.action,
		"message_hash": message_hash,
		"started_at": item.ts or now_iso(),
		"conversation_ref": item.candidate_key,
		"status": _INFLIGHT_VERIFICATION_REQUIRED,
	}


def _queue_inflight_gate(
	store: AutomationStore,
	guard: SafetyGuard,
	state: dict[str, Any],
	platform: str,
	dry_run: bool,
	candidate_key: str,
	prior: dict[str, Any],
) -> tuple[AutomationEvent, bool] | None:
	"""队列派发前检查会话在途标记；返回 (事件, 是否停止本周期)。"""
	inflight = _read_inflight(prior)
	if inflight is None:
		return None
	action = inflight.action or PlatformAction.SKIP
	reason = _inflight_reason(candidate_key, inflight)
	if inflight.status == _INFLIGHT_VERIFICATION_REQUIRED and not _inflight_needs_notification(prior):
		# 操作者已收到过核对要求：只跳过该候选人的队列动作，其他候选人照常处理。
		event = make_event(
			platform,
			candidate_key,
			action,
			EventStatus.SKIPPED,
			0.0,
			f"等待人工核对在途动作，已跳过队列动作：{reason}",
		)
		store.append_event(event)
		return event, False
	if dry_run:
		event = make_event(
			platform,
			candidate_key,
			action,
			EventStatus.DRY_RUN,
			0.0,
			f"dry-run: {reason}；真实运行会打开熔断并要求人工核对，不会派发这条队列动作",
		)
		store.append_event(event)
		return event, False
	# 标记先落盘、熔断成功后才置为已通知（Issue #1517）；队列行保持 pending，
	# 人工核对并清理标记后这条动作仍可继续（Issue #1514）。
	_mark_inflight_verification_required(prior)
	store.write_state(state)
	guard.open_circuit_breaker(f"platform verification required: {reason}")
	_mark_inflight_notified(prior)
	_try_write_state(store, state)
	event = make_event(
		platform,
		candidate_key,
		action,
		EventStatus.PLATFORM_VERIFICATION_REQUIRED,
		0.0,
		reason,
	)
	store.append_event(event)
	return event, True


def _release_inflight(prior: dict[str, Any], store: AutomationStore, state: dict[str, Any]) -> None:
	"""确定请求未发出时释放在途标记；写盘失败不能掩盖原始异常。"""
	_clear_inflight(prior)
	try:
		store.write_state(state)
	except Exception:
		# 状态写不回去时标记仍留在磁盘上：下一轮按结果未知转成
		# verification-required，保守但不会重发；调用方要继续看到原始异常。
		pass


def _inflight_reason(candidate_key: str, inflight: InflightAction) -> str:
	action = inflight.action.value if inflight.action is not None else "unknown action"
	started_at = inflight.started_at or "unknown time"
	conversation_ref = inflight.conversation_ref or "unknown ref"
	return (
		f"conversation {candidate_key!r} has an unconfirmed {action} in flight "
		f"since {started_at} (ref {conversation_ref})；"
		"请在官方聊天页核对是否已发出，确认前不会自动重发；"
		f"处理后需更新 automation/state.json 中该会话的 {_INFLIGHT_KEY} 标记，自动化才会继续处理它"
	)


def _process_inflight(
	store: AutomationStore,
	guard: SafetyGuard,
	state: dict[str, Any],
	platform: str,
	dry_run: bool,
	candidate_key: str,
	prior: dict[str, Any],
	inflight: InflightAction,
) -> AutomationEvent:
	"""未 settle 的在途动作：不再跑决策，直接停下等人工核对（Issue #1511）。"""
	action = inflight.action or PlatformAction.SKIP
	reason = _inflight_reason(candidate_key, inflight)
	if inflight.status == _INFLIGHT_VERIFICATION_REQUIRED and not _inflight_needs_notification(prior):
		# 操作者已经收到过核对要求（可能已 --resume）：保持标记，不重复开熔断。
		event = make_event(
			platform,
			candidate_key,
			action,
			EventStatus.SKIPPED,
			0.0,
			f"等待人工核对在途动作，已跳过：{reason}",
		)
	elif dry_run:
		# 预览不是执行：不改写在途标记、不打开熔断（与队列 #1257 同口径）。
		event = make_event(
			platform,
			candidate_key,
			action,
			EventStatus.DRY_RUN,
			0.0,
			f"dry-run: {reason}；真实运行会把它转为 verification-required 并要求人工核对，不会自动重发",
		)
	else:
		# 与队列动作同一顺序：标记先推进到 verification-required 并落盘，
		# 再打开熔断——即使熔断 checkpoint 失败，下一轮也不会把这条记录当成
		# 可以凭 transcript 重新决策的会话。标记上的「已通知」位只在熔断
		# 落盘成功后置位：两步之间被杀时下一轮会重开熔断，不会静默跳过让
		# 操作者收不到核对要求（Issue #1517）。
		_mark_inflight_verification_required(prior)
		store.write_state(state)
		guard.open_circuit_breaker(f"platform verification required: {reason}")
		_mark_inflight_notified(prior)
		_try_write_state(store, state)
		event = make_event(
			platform,
			candidate_key,
			action,
			EventStatus.PLATFORM_VERIFICATION_REQUIRED,
			0.0,
			reason,
		)
	store.append_event(event)
	return event


def _common_gate(config: AutomationConfig, decision: Decision) -> EventStatus | None:
	"""Apply decision-level risk and confidence controls without a human approval gate."""
	if decision.risk_flags:
		return EventStatus.SKIPPED
	if decision.confidence < config.human_review_threshold:
		return EventStatus.SKIPPED
	return None


def _lead_status(
	config: AutomationConfig,
	decision: Decision,
	dry_run: bool,
) -> EventStatus:
	gated = _common_gate(config, decision)
	if gated is not None:
		return gated
	return EventStatus.DRY_RUN if dry_run else EventStatus.AUTO_EXECUTED


def _action_status(
	config: AutomationConfig,
	decision: Decision,
	dry_run: bool,
) -> EventStatus:
	gated = _common_gate(config, decision)
	if gated is not None:
		return gated
	if decision.confidence < config.auto_execute_threshold:
		return EventStatus.SKIPPED
	if decision.action not in config.allowed_actions:
		return EventStatus.SKIPPED
	return EventStatus.DRY_RUN if dry_run else EventStatus.AUTO_EXECUTED


def _pending_with_status(item: PendingAction, status: str) -> PendingAction:
	values = asdict(item)
	values["status"] = status
	values["updated_at"] = now_iso()
	return PendingAction(**values)


def _next_pending_status(status: EventStatus) -> str:
	match status:
		case EventStatus.AUTO_EXECUTED:
			return "executed"
		case EventStatus.DRY_RUN:
			# 预览没有发出任何东西：队列项必须留在 pending，下一次真实运行才会执行
			# （Issue #889）。旧实现把它改成 "dry-run"，演练一次就永久消耗掉
			# 操作者批准过的待执行动作。
			return "pending"
		case EventStatus.PLATFORM_VERIFICATION_REQUIRED:
			# 动作可能已经生效：退出 pending 队列，避免下一轮自动重发。
			return "verification-required"
		case (
			EventStatus.STOPPED_BY_SAFETY
			| EventStatus.CIRCUIT_BREAKER_OPEN
			| EventStatus.SKIPPED
			| EventStatus.QUEUED_FOR_REVIEW
			| EventStatus.QUEUED_PENDING_ACTION
		):
			return "pending"
