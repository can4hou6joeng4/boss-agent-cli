"""招聘者 — 向已有会话发送职位工作地点卡片。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import click

from boss_agent_cli.api.client import PlatformRiskError
from boss_agent_cli.api.recruiter_client import RecruiterAuthError
from boss_agent_cli.api.request_budget import RequestBudgetExceeded
from boss_agent_cli.auth.manager import AuthManager, AuthRequired, TokenRefreshFailed
from boss_agent_cli.automation.storage import AutomationStore
from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.commands._recruiter_platform import get_recruiter_platform_instance
from boss_agent_cli.compliance import require_compliance_allowed
from boss_agent_cli.commands.recruiter._hints import hint_command
from boss_agent_cli.display import (
	error_contract_for_code,
	handle_auth_errors,
	handle_error_output,
	handle_output,
	handle_platform_error_output,
)


class _TargetError(Exception):
	def __init__(self, reason: str) -> None:
		super().__init__(reason)
		self.reason = reason


def _cache_path(data_dir: Path) -> Path:
	return data_dir / "cache" / "boss_agent.db"


def _stop_reason(data_dir: Path) -> str | None:
	"""读取招聘自动化停止开关。文件不存在时不创建目录。"""
	if not (data_dir / "automation" / "stop.json").is_file():
		return None
	loaded = AutomationStore(data_dir).read_stop()
	if loaded.get("open") is not True:
		return None
	reason = loaded.get("reason")
	if isinstance(reason, str) and reason.strip():
		return reason.strip()
	return "招聘自动化已停止"


def _location_status(data_dir: Path, friend_id: int) -> str | None:
	path = _cache_path(data_dir)
	if not path.is_file():
		return None
	with CacheStore(path) as cache:
		return cache.location_send_status(friend_id)


def _release(cache: CacheStore, friend_id: int) -> None:
	try:
		cache.release_location_send(friend_id)
	except Exception:
		pass


def _fail(
	ctx: click.Context,
	*,
	code: str,
	message: str,
	details: dict[str, Any] | None = None,
	recovery_action: str | None = None,
	hints: dict[str, Any] | None = None,
) -> None:
	recoverable, registered = error_contract_for_code(code)
	handle_error_output(
		ctx,
		"recruiter-location",
		code=code,
		message=message,
		recoverable=recoverable,
		recovery_action=recovery_action if recovery_action is not None else registered,
		details=details,
		hints=hints,
	)


def _duplicate_failure(ctx: click.Context, friend_id: int, status: str) -> None:
	chatmsg = hint_command(ctx, "hr chatmsg <friend_id>")
	details: dict[str, Any] = {"friend_id": friend_id, "sent": True if status == "sent" else None}
	hints = {"next_actions": [chatmsg]}
	if status == "sent":
		_fail(
			ctx,
			code="LOCATION_ALREADY_SENT",
			message="该会话已发送过工作地点，禁止重复发送",
			details=details,
			hints={**hints, "operator_actions": ["用聊天记录核对地点卡，不要删除本地记录后重发"]},
		)
		return
	_fail(
		ctx,
		code="LOCATION_RESULT_UNKNOWN",
		message="该会话的工作地点发送结果未确认，禁止自动重发",
		details=details,
		hints={**hints, "operator_actions": ["先在官方页面或聊天记录核对地点卡是否已发出，不要自动重发"]},
	)


def _target_from_detail(detail: dict[str, Any], friend_id: int) -> tuple[str | int, str, dict[str, Any]]:
	"""从本次 friend_detail 取出唯一会话。不用推荐卡片上的 securityId。"""
	data = detail.get("zpData")
	items = data.get("friendList") if isinstance(data, dict) else None
	items = items if isinstance(items, list) else []
	matches = [
		item
		for item in items
		if isinstance(item, dict) and str(item.get("uid", item.get("friendId", ""))) == str(friend_id)
	]
	if len(matches) != 1:
		raise _TargetError("friend_not_found")
	friend = matches[0]
	security_id = friend.get("securityId")
	if not isinstance(security_id, str) or not security_id.strip():
		raise _TargetError("missing_security_id")
	job_id = friend.get("jobId")
	if isinstance(job_id, bool) or not isinstance(job_id, (str, int)):
		raise _TargetError("missing_job_id")
	if isinstance(job_id, str) and not job_id.strip():
		raise _TargetError("missing_job_id")
	if isinstance(job_id, int) and job_id <= 0:
		raise _TargetError("missing_job_id")
	return job_id, security_id, friend


def _reject_target(ctx: click.Context, friend_id: int, reason: str) -> None:
	chat_cmd = hint_command(ctx, "hr chat")
	details = {"friend_id": friend_id, "sent": False}
	if reason == "friend_not_found":
		_fail(
			ctx,
			code="CONTACT_NOT_FOUND",
			message="当前会话里没有这个候选人，未发送工作地点",
			details=details,
			recovery_action=f"用 {chat_cmd} 核对 friend_id 后重试",
			hints={"next_actions": [chat_cmd]},
		)
		return
	missing = "securityId" if reason == "missing_security_id" else "jobId"
	_fail(
		ctx,
		code="INVALID_PARAM",
		message=f"当前会话缺少{missing}，未发送工作地点；不会使用推荐卡片上的 securityId",
		details=details,
		recovery_action=f"用 {chat_cmd} 刷新沟通列表后重试；securityId 必须来自本次 friend_detail",
		hints={"next_actions": [chat_cmd]},
	)


@click.command("location")
@click.argument("friend_id", type=click.IntRange(min=1))
@click.option("--yes", is_flag=True, help="操作者已明确批准向该会话发送工作地点")
@click.option("--dry-run", is_flag=True, help="只预览目标，不请求平台、不写发送记录")
@click.pass_context
@handle_auth_errors("recruiter-location")
def location_cmd(ctx: click.Context, friend_id: int, yes: bool, dry_run: bool) -> None:
	"""向已有会话发送该职位的工作地点卡片。

	securityId 与内部 jobId 只从本次 friend_detail 读取。默认不发送：
	``--dry-run`` 只预览，``--yes`` 才发出。同一 friend_id 已发送或结果未知时拒绝。
	``boss agent stop`` 打开的停止开关会在任何请求之前拦住本命令。
	"""
	if not require_compliance_allowed(ctx, "recruiter-location"):
		return
	data_dir = Path(ctx.obj["data_dir"])
	base: dict[str, Any] = {"friend_id": friend_id, "sent": False}
	stop = _stop_reason(data_dir)
	if stop is not None:
		resume_cmd = hint_command(ctx, "agent run --dry-run --resume")
		_fail(
			ctx,
			code="CIRCUIT_BREAKER_OPEN",
			message=f"停止开关已打开（{stop}），本次不会发送工作地点",
			details=base,
			recovery_action=f"核对未确认动作后执行 {resume_cmd} 关闭熔断，再由操作者显式重试；不要自动发送",
			hints={"operator_actions": ["停止开关打开时不要发送工作地点"], "next_actions": [resume_cmd]},
		)
		return
	previous = _location_status(data_dir, friend_id)
	if previous is not None:
		_duplicate_failure(ctx, friend_id, previous)
		return
	if dry_run:
		handle_output(
			ctx,
			"recruiter-location",
			{**base, "dry_run": True},
			hints={
				"operator_actions": [
					"预览不会读取会话。核对候选人后才可加 --yes；"
					"securityId 与 jobId 取自当前会话的 friend_detail，不使用推荐卡片上的 securityId",
				],
			},
		)
		return
	if not yes:
		_fail(
			ctx,
			code="CONFIRMATION_REQUIRED",
			message="尚未获得操作者向该会话发送工作地点的明确批准",
			details=base,
		)
		return

	auth = AuthManager(data_dir, logger=ctx.obj["logger"], platform=ctx.obj.get("platform", "zhipin"))
	with get_recruiter_platform_instance(ctx, auth) as platform, CacheStore(_cache_path(data_dir)) as cache:
		auth.get_token()
		claimed = cache.claim_location_send(friend_id)
		if claimed is not None:
			_duplicate_failure(ctx, friend_id, claimed)
			return
		try:
			detail = platform.friend_detail([friend_id], retry=False)
		except (RequestBudgetExceeded, PlatformRiskError, AuthRequired, RecruiterAuthError, TokenRefreshFailed):
			_release(cache, friend_id)
			raise
		except NotImplementedError:
			_release(cache, friend_id)
			_fail(ctx, code="NOT_SUPPORTED", message="当前平台不支持发送工作地点", details=base)
			return
		except Exception:
			_release(cache, friend_id)
			raise
		if not platform.is_success(detail):
			_release(cache, friend_id)
			handle_platform_error_output(
				ctx,
				"recruiter-location",
				platform,
				detail,
				fallback_message="读取会话失败，未发送工作地点",
			)
			return
		try:
			job_id, security_id, friend = _target_from_detail(detail, friend_id)
		except _TargetError as exc:
			_release(cache, friend_id)
			_reject_target(ctx, friend_id, exc.reason)
			return
		try:
			result = platform.send_job_location(job_id=job_id, to_user_id=friend_id, security_id=security_id)
		except (RequestBudgetExceeded, PlatformRiskError, AuthRequired, RecruiterAuthError, TokenRefreshFailed):
			_release(cache, friend_id)
			raise
		except NotImplementedError:
			_release(cache, friend_id)
			_fail(
				ctx,
				code="NOT_SUPPORTED",
				message="当前平台不支持发送工作地点",
				details={**base, "job_id": job_id},
			)
			return
		except Exception:
			_fail(
				ctx,
				code="LOCATION_RESULT_UNKNOWN",
				message="工作地点是否已发出无法确认，禁止自动重发",
				details={**base, "sent": None, "job_id": job_id},
				hints={"next_actions": [hint_command(ctx, "hr chatmsg <friend_id>")]},
			)
			return
		if not platform.is_success(result):
			code, _message = platform.parse_error(result)
			if code != "ACTION_UNCONFIRMED":
				_release(cache, friend_id)
			handle_platform_error_output(
				ctx,
				"recruiter-location",
				platform,
				result,
				fallback_message="工作地点发送失败",
			)
			return
		raw_name = friend.get("name")
		name = raw_name if isinstance(raw_name, str) else ""
		raw_job_name = friend.get("jobName")
		job_name = raw_job_name if isinstance(raw_job_name, str) else ""
		sent = {**base, "sent": True, "job_id": job_id, "job_name": job_name, "name": name}
		try:
			cache.record_location_send(friend_id, job_id=str(job_id))
		except Exception:
			_fail(
				ctx,
				code="LOCAL_STATE_WRITE_FAILED",
				message="工作地点已发送，但本地记录保存失败，禁止自动重发",
				details=sent,
			)
			return
		handle_output(
			ctx,
			"recruiter-location",
			sent,
			hints={"next_actions": [hint_command(ctx, f"hr chatmsg {friend_id}")]},
		)
