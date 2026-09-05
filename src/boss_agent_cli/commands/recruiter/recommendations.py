"""招聘者 — 推荐候选人与首次招呼。"""

from __future__ import annotations

import time
from typing import Any

import click
import httpx

from boss_agent_cli.api.browser_source import BrowserSourceUnavailable
from boss_agent_cli.api.client import AccountRiskError
from boss_agent_cli.api.httpx_helpers import remaining_timeout
from boss_agent_cli.api.recruiter_client import RecruiterAuthError
from boss_agent_cli.auth.manager import AuthManager, AuthRequired, TokenRefreshFailed
from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.compliance import require_compliance_allowed
from boss_agent_cli.commands._recruiter_platform import get_recruiter_platform_instance
from boss_agent_cli.display import handle_auth_errors, handle_error_output, handle_output, handle_platform_error_output
from boss_agent_cli.platforms.recruiter_base import RecruiterPlatform


def _records(value: Any, *keys: str) -> list[dict[str, Any]]:
	"""只读取明确的结果数组，不搜索卡片内嵌的推荐或职位结构。"""
	if isinstance(value, dict):
		for key in keys:
			if isinstance(value.get(key), list):
				return [item for item in value[key] if isinstance(item, dict)]
		return [value]
	return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _nonnegative_int(item: dict[str, Any], *keys: str) -> int | None:
	for key in keys:
		value = item.get(key)
		if isinstance(value, bool) or value in (None, ""):
			continue
		try:
			parsed = int(str(value))
		except (TypeError, ValueError):
			continue
		if parsed >= 0:
			return parsed
	return None


def _positive_int(item: dict[str, Any], *keys: str) -> int | None:
	value = _nonnegative_int(item, *keys)
	return value if value is not None and value > 0 else None


def _candidate_record(value: Any, *, geek_id: str, security_id: str) -> dict[str, Any] | None:
	matches: list[dict[str, Any]] = []
	for record in _records(value, "friendList", "result", "list"):
		ids = {str(record[key]) for key in ("encryptGeekId", "encryptUid", "geekId") if record.get(key)}
		matched = geek_id in ids if ids else bool(security_id and record.get("securityId") == security_id)
		if matched:
			matches.append(record)
	return matches[0] if len(matches) == 1 else None


def _message_record(value: Any, *, friend_id: int) -> dict[str, Any] | None:
	matches = [
		item for item in _records(value, "lastMessageList", "messages", "messageList", "result", "friendList", "list")
		if _positive_int(item, "uid", "friendId", "friend_id", "gid") == friend_id
	]
	return matches[0] if len(matches) == 1 else None


def _message_id(item: dict[str, Any]) -> int | None:
	for record in (item, item.get("lastMsgInfo"), item.get("lastMessageInfo")):
		if isinstance(record, dict):
			message_id = _positive_int(record, "messageId", "msgId", "mid", "lastMsgId")
			if message_id is not None:
				return message_id
	return None


def _unread_count(item: dict[str, Any] | None) -> int | None:
	return _nonnegative_int(item, "unread", "unreadMsgCount", "newMsgCount") if item else None


def _response_failure(platform: RecruiterPlatform, result: dict[str, Any], reason: str) -> dict[str, Any]:
	code, _ = platform.parse_error(result)
	return {"status": "failed", "reason": reason, "error_code": code}


def _read_state_after_greet(
	platform: RecruiterPlatform,
	*,
	start_result: dict[str, Any],
	geek_id: str,
	job_id: str,
	security_id: str,
	timeout: float = 25,
) -> dict[str, Any]:
	"""在同一预算内查询、发回执和回读；已发送的招呼不重试。"""
	deadline = time.monotonic() + timeout
	try:
		conversation = _candidate_record(platform.unwrap_data(start_result), geek_id=geek_id, security_id=security_id)
		if conversation is None or _unread_count(conversation) is None:
			result = platform.friend_list(job_id=job_id, deadline=deadline)
			if not platform.is_success(result):
				return _response_failure(platform, result, "conversation_lookup_failed")
			conversation = _candidate_record(platform.unwrap_data(result), geek_id=geek_id, security_id=security_id)
		unread = _unread_count(conversation)
		if unread is None:
			return {"status": "unknown", "reason": "unread_unresolved"}
		if unread == 0:
			return {"status": "not_needed", "unread": 0}
		assert conversation is not None
		peer_uid = _positive_int(conversation, "uid", "friendId", "friend_id", "gid")
		if peer_uid is None:
			return {"status": "unknown", "reason": "conversation_unresolved"}
		message_id = _message_id(conversation)
		if message_id is None:
			result = platform.last_messages([peer_uid], deadline=deadline)
			if not platform.is_success(result):
				return _response_failure(platform, result, "last_message_lookup_failed")
			message = _message_record(platform.unwrap_data(result), friend_id=peer_uid)
			message_id = _message_id(message) if message else None
		if message_id is None:
			return {"status": "unknown", "reason": "message_id_unresolved"}
		remaining_timeout(deadline)
		user_source = _nonnegative_int(conversation, "userSource", "friendSource") or 0
		result = platform.mark_read(peer_uid=peer_uid, message_id=message_id, user_source=user_source, deadline=deadline)
		if not platform.is_success(result):
			return _response_failure(platform, result, "read_receipt_rejected")
		remaining_timeout(deadline)
		result = platform.friend_list(job_id=job_id, deadline=deadline)
		if not platform.is_success(result):
			return _response_failure(platform, result, "readback_failed")
		remaining_timeout(deadline)
		verified = _candidate_record(platform.unwrap_data(result), geek_id=geek_id, security_id=security_id)
		if (
			verified is not None
			and _positive_int(verified, "uid", "friendId", "friend_id", "gid") == peer_uid
			and _unread_count(verified) == 0
		):
			return {"status": "cleared", "unread": 0}
		return {"status": "unknown", "reason": "read_receipt_unverified"}
	except BrowserSourceUnavailable as exc:
		return {"status": "failed", "error_code": exc.code, "reason": "browser_source_unavailable"}
	except AccountRiskError:
		return {"status": "failed", "error_code": "ACCOUNT_RISK", "reason": "account_risk"}
	except TokenRefreshFailed:
		return {"status": "failed", "error_code": "TOKEN_REFRESH_FAILED", "reason": "token_refresh_failed"}
	except (AuthRequired, RecruiterAuthError):
		return {"status": "failed", "error_code": "AUTH_REQUIRED", "reason": "auth_required"}
	except (TimeoutError, httpx.TimeoutException):
		return {"status": "timeout", "reason": "read_receipt_timeout"}
	except (OSError, httpx.HTTPError, RuntimeError, ValueError, LookupError) as exc:
		return {"status": "failed", "reason": "read_receipt_error", "error_type": type(exc).__name__}


@click.command("recommendations")
@click.option("--job-id", required=True, help="招聘职位的加密 ID")
@click.option("--page", default=1, type=click.IntRange(min=1), help="页码")
@click.pass_context
@handle_auth_errors("recruiter-recommendations")
def recommendations_cmd(ctx: click.Context, job_id: str, page: int) -> None:
	"""读取推荐牛人完整卡片和首次开聊参数。"""
	if not require_compliance_allowed(ctx, "recruiter-recommendations"):
		return
	auth = AuthManager(ctx.obj["data_dir"], logger=ctx.obj["logger"], platform=ctx.obj.get("platform", "zhipin"))
	with get_recruiter_platform_instance(ctx, auth) as platform:
		result = platform.recommend_geeks(job_id, page=page)
		if not platform.is_success(result):
			handle_platform_error_output(ctx, "recruiter-recommendations", platform, result, fallback_message="推荐牛人获取失败")
			return
		handle_output(
			ctx, "recruiter-recommendations", platform.unwrap_data(result) or {},
			hints={"next_actions": ["boss hr greet --help — 先预览候选人和话术，再由操作者确认"]},
		)


def _send_error(ctx: click.Context, *, code: str, job_id: str, details: dict[str, Any]) -> None:
	"""保留发送状态并停止工作流，禁止把写失败解释为自动重发。"""
	check = f"boss hr chat --job-id {job_id}"
	message = "招呼已发送，收尾失败；停止后续建联" if details.get("sent") else "首次招呼未确认成功；停止发送并核对会话"
	if code == "ALREADY_GREETED":
		message = "该候选人在此职位下已发送过招呼，禁止重复发送"
	if code == "ACCOUNT_RISK":
		action = "账号已触发风控，停止自动化访问；回到 BOSS 直聘官方页面处理"
		hints = {"operator_actions": [action, "禁止重新发送本次招呼"]}
	else:
		action = f"先用 {check} 确认该候选人会话是否已建立；不要自动重发"
		hints = {"operator_actions": [message, action], "next_actions": [check]}
	handle_error_output(
		ctx, "recruiter-greet", code=code, message=message, recoverable=False,
		recovery_action=action, details=details, hints=hints,
	)


@click.command("greet")
@click.option("--geek-id", required=True, help="recommendations 返回的 encryptGeekId")
@click.option("--job-id", required=True, help="recommendations 返回的 encryptJobId")
@click.option("--expect-id", required=True, help="recommendations 返回的 expectId")
@click.option("--lid", required=True, help="recommendations 返回的 lid")
@click.option("--security-id", required=True, help="recommendations 返回的 securityId")
@click.option("--suid", default="", help="可选 suid；当前推荐接口通常为空")
@click.option("--message", required=True, help="首次招呼内容")
@click.option("--yes", is_flag=True, help="操作者已明确批准此候选人和话术")
@click.option("--dry-run", is_flag=True, help="只预览候选人和话术，不发送")
@click.option("--read-receipt-timeout", default=25.0, type=click.FloatRange(min=1, max=60), help="清红点总预算（秒）")
@click.pass_context
@handle_auth_errors("recruiter-greet")
def greet_cmd(
	ctx: click.Context,
	geek_id: str,
	job_id: str,
	expect_id: str,
	lid: str,
	security_id: str,
	suid: str,
	message: str,
	yes: bool,
	dry_run: bool,
	read_receipt_timeout: float,
) -> None:
	"""建立候选人会话并发送首次招呼。"""
	if not require_compliance_allowed(ctx, "recruiter-greet"):
		return
	data: dict[str, Any] = {"geek_id": geek_id, "job_id": job_id, "sent": False}
	if not message.strip():
		handle_error_output(ctx, "recruiter-greet", code="INVALID_PARAM", message="首次招呼内容不能为空")
		return
	if dry_run:
		handle_output(
			ctx, "recruiter-greet", {**data, "dry_run": True, "message": message},
			hints={"operator_actions": ["核对候选人和话术；明确批准后才可加 --yes 发送"]},
		)
		return
	if not yes:
		handle_error_output(
			ctx, "recruiter-greet", code="CONFIRMATION_REQUIRED",
			message="尚未获得操作者对该候选人和话术的明确批准", recoverable=True,
			recovery_action="确认候选人和话术后重新执行并加 --yes",
		)
		return
	auth = AuthManager(ctx.obj["data_dir"], logger=ctx.obj["logger"], platform=ctx.obj.get("platform", "zhipin"))
	with get_recruiter_platform_instance(ctx, auth) as platform, CacheStore(ctx.obj["data_dir"] / "cache" / "boss_agent.db") as cache:
		auth.get_token()
		previous = cache.claim_recruiter_greet(geek_id, job_id)
		if previous is not None:
			data["sent"] = True if previous == "sent" else None
			_send_error(ctx, code="ALREADY_GREETED" if previous == "sent" else "GREET_RESULT_UNKNOWN", job_id=job_id, details=data)
			return
		# 预约后即使进程退出也不自动再发；通用异常处理不得把写失败提示为“重试”。
		data["sent"] = None
		try:
			result = platform.start_chat(
				geek_id=geek_id, job_id=job_id, expect_id=expect_id, lid=lid,
				security_id=security_id, message=message, suid=suid,
			)
		except AccountRiskError:
			_send_error(ctx, code="ACCOUNT_RISK", job_id=job_id, details=data)
			return
		except BrowserSourceUnavailable as exc:
			_send_error(ctx, code=exc.code, job_id=job_id, details=data)
			return
		except Exception:
			# 仅此不可逆写入边界兜底未知异常：响应丢失也可能已建联，保留 pending。
			_send_error(ctx, code="GREET_RESULT_UNKNOWN", job_id=job_id, details=data)
			return
		if not platform.is_success(result):
			code, _ = platform.parse_error(result)
			_send_error(ctx, code=code if code != "UNKNOWN" else "GREET_RESULT_UNKNOWN", job_id=job_id, details=data)
			return
		data["sent"] = True
		try:
			cache.record_recruiter_greet(geek_id, job_id)
		except Exception:
			_send_error(ctx, code="GREET_RESULT_UNKNOWN", job_id=job_id, details=data)
			return
		try:
			read_state = _read_state_after_greet(
				platform, start_result=result, geek_id=geek_id, job_id=job_id,
				security_id=security_id, timeout=read_receipt_timeout,
			)
		except Exception:
			# 未预期的收尾异常也必须保留 sent，不能进入通用“重试”兜底。
			_send_error(ctx, code="NETWORK_ERROR", job_id=job_id, details=data)
			return
		data.update(read_state=read_state, partial_success=read_state["status"] not in ("not_needed", "cleared"))
		if read_code := read_state.get("error_code"):
			if read_code != "UNKNOWN":
				_send_error(ctx, code=read_code, job_id=job_id, details=data)
				return
		hints = None
		if data["partial_success"]:
			hints = {
				"operator_actions": ["招呼已发送，红点未确认清除；禁止重新发送招呼"],
				"next_actions": [f"boss hr chat --job-id {job_id}"],
			}
		handle_output(ctx, "recruiter-greet", data, hints=hints)
