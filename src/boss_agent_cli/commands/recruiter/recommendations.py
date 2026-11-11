"""招聘者 — 推荐候选人与首次招呼。"""

from __future__ import annotations

from typing import Any

import click

from boss_agent_cli.api.browser_source import BrowserSourceUnavailable
from boss_agent_cli.api.client import PlatformRiskError
from boss_agent_cli.api.recruiter_client import RecruiterAuthError
from boss_agent_cli.api.request_budget import RequestBudgetExceeded
from boss_agent_cli.auth.manager import AuthManager, AuthRequired, TokenRefreshFailed
from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.compliance import require_compliance_allowed
from boss_agent_cli.commands._recruiter_platform import get_recruiter_platform_instance
from boss_agent_cli.display import (
	RISK_ERROR_CONTRACTS,
	boss_command_for_ctx,
	handle_auth_errors,
	handle_error_output,
	handle_output,
	handle_platform_error_output,
	risk_error_contract,
	shell_argument,
)
from boss_agent_cli.services.pagination import (
	UNKNOWN_HAS_MORE_OPERATOR_ACTION,
	list_pagination,
	rows_from,
)


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
		pagination = list_pagination(data, page=page, rows=rows_from(data, "geekList"))
		next_actions = [
			boss_command_for_ctx(
				ctx,
				"hr greet --geek-id <geek_id> --job-id <id> --expect-id <id> --lid <id> "
				"--security-id <id> --message <话术> --dry-run",
			),
		]
		if pagination["has_more"] is True:
			next_actions.append(
				boss_command_for_ctx(
					ctx,
					f"hr recommendations --job-id {shell_argument(job_id)} --page {pagination['page'] + 1}",
				)
			)
		operator_actions = ["先 dry-run 预览候选人和话术，由操作者确认后去掉 --dry-run 并加 --yes 再发送"]
		if pagination["has_more"] is None:
			operator_actions.append(UNKNOWN_HAS_MORE_OPERATOR_ACTION)
		handle_output(
			ctx, "recruiter-recommendations", data,
			pagination=pagination,
			hints={"next_actions": next_actions, "operator_actions": operator_actions},
		)


def _send_error(ctx: click.Context, *, code: str, job_id: str, details: dict[str, Any]) -> None:
	"""保留发送状态并停止工作流，禁止把写失败解释为自动重发。"""
	check = f"boss hr chat --job-id {shell_argument(job_id)}"
	message = "招呼已发送，但本地状态保存失败；停止后续建联" if details.get("sent") else "首次招呼未确认成功；停止发送并核对会话"
	if code == "ALREADY_GREETED":
		message = "该候选人在此职位下已发送过招呼，禁止重复发送"
	if code == "GREET_LIMIT":
		message = str(details.get("platform_message") or "该职位的主动沟通权益已用完，本次未发送")
		action = "停止该职位的新招呼，等待平台权益恢复；不要自动重发本次招呼"
		hints = {"operator_actions": [action]}
	elif code in RISK_ERROR_CONTRACTS:
		# 风控码（含环境风险与本地风险锁）统一取登记表的停止指引；
		# 命令自身的「不要重发本次招呼」作为操作者补充，不覆盖机器可读的 next_actions。
		recovery, risk_hints = risk_error_contract(code)
		action = f"{recovery}；不要自动重发本次招呼"
		hints = {
			**(risk_hints or {}),
			"operator_actions": [action, "不要刷新 Token、重新登录或换通道重试该请求"],
		}
	else:
		action = f"先用 {check} 确认该候选人会话是否已建立；不要自动重发"
		hints = {"operator_actions": [message, action], "next_actions": [check]}
	handle_error_output(
		ctx, "recruiter-greet", code=code, message=message, recoverable=False,
		recovery_action=action, details=details, hints=hints,
	)


def _release_unsent_reservation(cache: CacheStore, geek_id: str, job_id: str) -> None:
	"""撤销确定未发出请求的预约；撤销失败保留 pending（安全侧），不掩盖原始停止原因。"""
	try:
		cache.release_recruiter_greet(geek_id, job_id)
	except Exception:
		pass


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
) -> None:
	"""建立候选人会话并发送首次招呼。"""
	if not require_compliance_allowed(ctx, "recruiter-greet"):
		return
	data: dict[str, Any] = {"geek_id": geek_id, "job_id": job_id, "sent": False}
	if not message.strip():
		rec_cmd = boss_command_for_ctx(ctx, f"hr recommendations --job-id {shell_argument(job_id)}")
		handle_error_output(
			ctx,
			"recruiter-greet",
			code="INVALID_PARAM",
			message="首次招呼内容不能为空",
			recovery_action=f"补上首次招呼内容后重试；候选人与职位可用 {rec_cmd} 核对",
			hints={"next_actions": [rec_cmd]},
		)
		return
	if dry_run:
		operator_actions = ["核对候选人和话术；明确批准后才可加 --yes 发送"]
		handle_output(
			ctx, "recruiter-greet", {**data, "dry_run": True, "message": message},
			hints={"operator_actions": operator_actions},
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
			data["sent"] = True if previous == "sent" else False if previous == "quota_limited" else None
			code = "ALREADY_GREETED" if previous == "sent" else "GREET_LIMIT" if previous == "quota_limited" else "GREET_RESULT_UNKNOWN"
			_send_error(ctx, code=code, job_id=job_id, details=data)
			return
		# 预约后即使进程退出也不自动再发；通用异常处理不得把写失败提示为“重试”。
		data["sent"] = None
		try:
			result = platform.start_chat(
				geek_id=geek_id, job_id=job_id, expect_id=expect_id, lid=lid,
				security_id=security_id, message=message, suid=suid,
			)
		except PlatformRiskError as exc:
			# 按基类捕获：EnvironmentRiskError / EnvironmentRiskLockedError 与
			# AccountRiskError 平级，只捕账号风控会把环境风控误报成结果未知。
			_send_error(ctx, code=exc.code, job_id=job_id, details=data)
			return
		except BrowserSourceUnavailable:
			# 通道全部不可用意味着请求从未发出：撤销预约后交给装饰器按来源政策输出指引；
			# 保留 pending 会让修正通道后的重试被误判成「结果未知」。
			_release_unsent_reservation(cache, geek_id, job_id)
			raise
		except TokenRefreshFailed:
			_send_error(ctx, code="TOKEN_REFRESH_FAILED", job_id=job_id, details=data)
			return
		except (AuthRequired, RecruiterAuthError):
			_send_error(ctx, code="AUTH_REQUIRED", job_id=job_id, details=data)
			return
		except RequestBudgetExceeded:
			# 预算/时限只在调度前抛出（request_sent=False）：请求没有发出，撤销预约，
			# 否则一次瞬态预算耗尽就会把该候选人永久锁成「结果未知」。停止契约由装饰器输出。
			_release_unsent_reservation(cache, geek_id, job_id)
			raise
		except Exception:
			# 仅此不可逆写入边界兜底未知异常：响应丢失也可能已建联，保留 pending。
			_send_error(ctx, code="GREET_RESULT_UNKNOWN", job_id=job_id, details=data)
			return
		if not platform.is_success(result):
			code, platform_message = platform.parse_error(result)
			if code not in RISK_ERROR_CONTRACTS:
				# 风控码的平台原文可能带临时凭据，不落 details；GREET_LIMIT 等仍需原样诊断。
				data["platform_message"] = platform_message
			if type(result.get("code")) is int:
				data["platform_code"] = result["code"]
			if code == "GREET_LIMIT":
				data["sent"] = False
				try:
					cache.record_recruiter_greet(geek_id, job_id, status="quota_limited")
				except Exception:
					# 即使记账失败也保留平台明确拒绝的事实；原 pending 仍阻止重发。
					pass
			_send_error(ctx, code=code if code != "UNKNOWN" else "GREET_RESULT_UNKNOWN", job_id=job_id, details=data)
			return
		data["sent"] = True
		try:
			cache.record_recruiter_greet(geek_id, job_id)
		except Exception:
			_send_error(ctx, code="GREET_RESULT_UNKNOWN", job_id=job_id, details=data)
			return
		# 招呼发出后 Agent 需要回到沟通列表跟进候选人：成功信封原先没有 hints，
		# 终端也不渲染任何内容，整条链在「已发送」处断掉。
		handle_output(
			ctx, "recruiter-greet", data,
			hints={"next_actions": [
				boss_command_for_ctx(ctx, "hr chat"),
				boss_command_for_ctx(ctx, "hr last-messages"),
			]},
		)
