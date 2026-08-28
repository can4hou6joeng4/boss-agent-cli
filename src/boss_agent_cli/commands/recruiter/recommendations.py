"""Recruiter recommended candidates and first-contact commands."""

from __future__ import annotations

from typing import Any

import click

from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.compliance import require_compliance_allowed
from boss_agent_cli.commands._recruiter_platform import get_recruiter_platform_instance
from boss_agent_cli.display import handle_auth_errors, handle_error_output, handle_output, handle_platform_error_output


def _nested_dicts(value: Any) -> list[dict[str, Any]]:
	items: list[dict[str, Any]] = []
	if isinstance(value, dict):
		items.append(value)
		for child in value.values():
			items.extend(_nested_dicts(child))
	elif isinstance(value, list):
		for child in value:
			items.extend(_nested_dicts(child))
	return items


def _positive_int(item: dict[str, Any], *keys: str) -> int | None:
	for key in keys:
		value = item.get(key)
		if value in (None, ""):
			continue
		try:
			parsed = int(str(value))
		except (TypeError, ValueError):
			continue
		if parsed > 0:
			return parsed
	return None


def _nested_positive_int(value: Any, *keys: str) -> int | None:
	for item in _nested_dicts(value):
		if parsed := _positive_int(item, *keys):
			return parsed
	return None


def _candidate_record(value: Any, *, geek_id: str, security_id: str) -> dict[str, Any] | None:
	records = _nested_dicts(value)
	for record in records:
		if security_id and str(record.get("securityId") or "") == security_id:
			return record
	for record in records:
		candidate_ids = (record.get("encryptGeekId"), record.get("encryptUid"), record.get("geekId"))
		if geek_id and geek_id in {str(candidate_id) for candidate_id in candidate_ids if candidate_id not in (None, "")}:
			return record
	return None


def _message_record(value: Any, *, friend_id: int) -> dict[str, Any] | None:
	records = _nested_dicts(value)
	for record in records:
		if _positive_int(record, "uid", "friendId", "friend_id", "gid") == friend_id:
			return record
	return records[0] if records else None


def _unread_count(item: dict[str, Any] | None) -> int | None:
	if item is None:
		return None
	for key in ("unread", "unreadMsgCount", "newMsgCount"):
		value = item.get(key)
		if value in (None, ""):
			continue
		try:
			return max(0, int(str(value)))
		except (TypeError, ValueError):
			continue
	return None


def _read_state_after_greet(
	platform: Any,
	*,
	start_result: dict[str, Any],
	geek_id: str,
	job_id: str,
	security_id: str,
) -> dict[str, Any]:
	"""Best-effort red-dot cleanup after a confirmed first contact."""
	try:
		start_data = platform.unwrap_data(start_result) or {}
		conversation = _candidate_record(start_data, geek_id=geek_id, security_id=security_id)
		needs_lookup = (
			conversation is None
			or _positive_int(conversation, "friendId", "friend_id", "uid", "gid") is None
			or _unread_count(conversation) is None
		)
		if needs_lookup:
			friend_result = platform.friend_list(page=1, label_id=0, job_id=job_id)
			if not platform.is_success(friend_result) and conversation is None:
				return {"status": "failed", "reason": "conversation_lookup_failed"}
			if platform.is_success(friend_result):
				friend_data = platform.unwrap_data(friend_result) or {}
				conversation = _candidate_record(friend_data, geek_id=geek_id, security_id=security_id) or conversation
		if conversation is None:
			return {"status": "failed", "reason": "conversation_not_found"}

		unread = _unread_count(conversation)
		if unread == 0:
			return {"status": "not_needed", "unread": 0}

		friend_id = _positive_int(conversation, "friendId", "friend_id", "uid", "gid")
		peer_uid = _positive_int(conversation, "uid", "friendId", "friend_id", "gid")
		message_id = _positive_int(conversation, "messageId", "msgId", "mid", "lastMsgId")
		message_record: dict[str, Any] | None = conversation
		if friend_id is not None and message_id is None:
			message_result = platform.last_messages([friend_id])
			if not platform.is_success(message_result):
				return {"status": "failed", "reason": "last_message_lookup_failed"}
			message_record = _message_record(platform.unwrap_data(message_result) or {}, friend_id=friend_id)
			if message_record is not None:
				message_id = _nested_positive_int(message_record, "messageId", "msgId", "mid", "lastMsgId", "id")
				peer_uid = peer_uid or _nested_positive_int(message_record, "uid", "friendId", "friend_id", "gid")

		if peer_uid is None or message_id is None:
			return {"status": "failed", "reason": "read_receipt_parameters_missing"}
		user_source = _positive_int(message_record, "userSource", "friendSource", "source") if message_record else None
		read_result = platform.mark_read(peer_uid=peer_uid, message_id=message_id, user_source=user_source or 0)
		if not platform.is_success(read_result):
			return {"status": "failed", "reason": "read_receipt_rejected"}
		return {"status": "cleared"}
	except Exception as exc:
		return {"status": "failed", "reason": "read_receipt_error", "error_type": exc.__class__.__name__}


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
		data = platform.unwrap_data(result) or {}
		handle_output(
			ctx,
			"recruiter-recommendations",
			data,
			hints={"next_actions": ["boss hr greet --help — 给人工确认的候选人首次打招呼"]},
		)


@click.command("greet")
@click.option("--geek-id", required=True, help="recommendations 返回的 encryptGeekId")
@click.option("--job-id", required=True, help="recommendations 返回的 encryptJobId")
@click.option("--expect-id", required=True, help="recommendations 返回的 expectId")
@click.option("--lid", required=True, help="recommendations 返回的 lid")
@click.option("--security-id", required=True, help="recommendations 返回的 securityId")
@click.option("--suid", default="", help="可选 suid；当前推荐接口通常为空")
@click.option("--message", required=True, help="首次招呼内容")
@click.option("--yes", is_flag=True, help="确认真实建联并发送首次招呼")
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
) -> None:
	"""建立候选人会话并发送首次招呼。"""
	if not yes:
		handle_error_output(
			ctx,
			"recruiter-greet",
			code="CONFIRMATION_REQUIRED",
			message="该命令会真实建联并发送消息；确认后重新执行并加 --yes",
			recoverable=True,
			recovery_action="检查候选人和话术后加 --yes",
		)
		return
	if not require_compliance_allowed(ctx, "recruiter-greet"):
		return
	auth = AuthManager(ctx.obj["data_dir"], logger=ctx.obj["logger"], platform=ctx.obj.get("platform", "zhipin"))
	with get_recruiter_platform_instance(ctx, auth) as platform:
		result = platform.start_chat(
			geek_id=geek_id,
			job_id=job_id,
			expect_id=expect_id,
			lid=lid,
			security_id=security_id,
			message=message,
			suid=suid,
		)
		if not platform.is_success(result):
			handle_platform_error_output(ctx, "recruiter-greet", platform, result, fallback_message="首次招呼发送失败")
			return
		read_state = _read_state_after_greet(
			platform,
			start_result=result,
			geek_id=geek_id,
			job_id=job_id,
			security_id=security_id,
		)
		data: dict[str, Any] = {
			"sent": True,
			"geek_id": geek_id,
			"job_id": job_id,
			"read_state": read_state,
			"partial_success": read_state["status"] == "failed",
		}
		if data["partial_success"]:
			data["warning"] = "招呼已发送成功，但红点处理失败；禁止重新发送招呼"
		handle_output(ctx, "recruiter-greet", data)
