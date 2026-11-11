from typing import Any

import click

from boss_agent_cli.api.client import AuthError, PlatformRiskError
from boss_agent_cli.api.request_budget import RequestBudgetExceeded
from boss_agent_cli.auth.manager import AuthManager, AuthRequired, TokenRefreshFailed
from boss_agent_cli.commands._platform import get_platform_instance
from boss_agent_cli.display import (
	INTERNAL_ERROR_TYPES,
	auth_required_hints,
	boss_command_for_ctx,
	error_contract_for_code,
	handle_error_output,
	handle_not_supported,
	handle_output,
	handle_platform_error_output,
	login_action_for_ctx,
	platform_error_contract,
	render_sectioned_record,
	risk_error_message,
)
from boss_agent_cli.services.pagination import UNKNOWN_HAS_MORE_OPERATOR_ACTION, list_pagination, rows_from


@click.command("me")
@click.option("--section", default=None, type=click.Choice(["user", "resume", "expect", "deliver"]),
	help="只获取指定部分（不指定则获取全部）")
@click.option("--deliver-page", default=1, type=click.IntRange(min=1), help="投递记录页码")
@click.pass_context
def me_cmd(ctx: click.Context, section: str | None, deliver_page: int) -> None:
	"""获取当前登录用户的个人信息、简历、求职期望、投递记录"""
	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj.get("logger")

	try:
		auth = AuthManager(data_dir, logger=logger, platform=ctx.obj.get("platform", "zhipin"))
		with get_platform_instance(ctx, auth) as platform:
			result = {}
			pagination: dict[str, Any] | None = None

			sections = [section] if section else ["user", "resume", "expect", "deliver"]

			if "user" in sections:
				if logger:
					logger.info("获取用户基本信息...")
				resp = platform.user_info()
				if not platform.is_success(resp):
					handle_platform_error_output(
						ctx, "me", platform, resp,
						fallback_message="用户基本信息获取失败",
					)
					return
				zp_data = platform.unwrap_data(resp) or {}
				result["user"] = {
					"name": zp_data.get("name", ""),
					"email": zp_data.get("email", ""),
					"phone": zp_data.get("phone", ""),
					"identity": zp_data.get("identity", ""),
					"avatar": zp_data.get("tinyAvatar", ""),
				}

			if "resume" in sections:
				if logger:
					logger.info("获取简历基本信息...")
				try:
					resp = platform.resume_baseinfo()
				except NotImplementedError as exc:
					handle_not_supported(ctx, "me", exc, fallback_message="当前平台不支持简历基本信息能力")
					return
				if not platform.is_success(resp):
					handle_platform_error_output(
						ctx, "me", platform, resp,
						fallback_message="简历基本信息获取失败",
					)
					return
				zp_data = platform.unwrap_data(resp) or {}
				result["resume"] = zp_data

			if "expect" in sections:
				if logger:
					logger.info("获取求职期望...")
				try:
					resp = platform.resume_expect()
				except NotImplementedError as exc:
					handle_not_supported(ctx, "me", exc, fallback_message="当前平台不支持求职期望能力")
					return
				if not platform.is_success(resp):
					handle_platform_error_output(
						ctx, "me", platform, resp,
						fallback_message="求职期望获取失败",
					)
					return
				zp_data = platform.unwrap_data(resp) or {}
				result["expect"] = zp_data

			if "deliver" in sections:
				if logger:
					logger.info("获取投递记录...")
				try:
					resp = platform.deliver_list(page=deliver_page)
				except NotImplementedError as exc:
					handle_not_supported(ctx, "me", exc, fallback_message="当前平台不支持投递记录能力")
					return
				if not platform.is_success(resp):
					handle_platform_error_output(
						ctx, "me", platform, resp,
						fallback_message="投递记录获取失败",
					)
					return
				zp_data = platform.unwrap_data(resp) or {}
				result["deliver"] = zp_data
				# 汇总四段信息时，投递列表的页码不能代表整份载荷。
				if section == "deliver":
					pagination = list_pagination(zp_data, page=deliver_page, rows=rows_from(zp_data, "list"))

			success_hints: dict[str, Any] = {
				"next_actions": [
					boss_command_for_ctx(ctx, "search <关键词> --city <城市>"),
					boss_command_for_ctx(ctx, "recommend"),
				],
			}
			if pagination is not None:
				if pagination["has_more"] is True:
					success_hints["next_actions"].append(boss_command_for_ctx(
						ctx, f"me --section deliver --deliver-page {pagination['page'] + 1}",
					))
				elif pagination["has_more"] is None:
					success_hints["operator_actions"] = [UNKNOWN_HAS_MORE_OPERATOR_ACTION]

			handle_output(
				ctx, "me", result,
				pagination=pagination,
				render=lambda d: render_sectioned_record(d, title="me"),
				hints=success_hints,
			)

	except (AuthRequired, TokenRefreshFailed):
		handle_error_output(ctx, "me", code="AUTH_REQUIRED", message="未登录", recoverable=True, recovery_action=login_action_for_ctx(ctx), hints=auth_required_hints(ctx))
	except AuthError:
		handle_error_output(ctx, "me", code="AUTH_EXPIRED", message="登录态过期", recoverable=True, recovery_action=login_action_for_ctx(ctx), hints=auth_required_hints(ctx))
	except PlatformRiskError as e:
		# me 没有 @handle_auth_errors 包裹：风控异常原本会掉进下面的
		# NETWORK_ERROR 兜底，变成 recoverable=true +「重试」，违反风控硬边界。
		recoverable, recovery_action, hints = platform_error_contract(e.code)
		handle_error_output(
			ctx,
			"me",
			code=e.code,
			message=risk_error_message(e.code),
			recoverable=recoverable,
			recovery_action=recovery_action,
			details=e.details or None,
			hints=hints,
		)
	except RequestBudgetExceeded as e:
		recoverable, recovery_action = error_contract_for_code(e.code)
		handle_error_output(
			ctx, "me", code=e.code, message=str(e), recoverable=recoverable,
			recovery_action=recovery_action, details=e.details,
		)
	except INTERNAL_ERROR_TYPES as e:
		recoverable, recovery_action = error_contract_for_code("INTERNAL_ERROR")
		handle_error_output(ctx, "me", code="INTERNAL_ERROR", message=f"{type(e).__name__}: {e}", recoverable=recoverable, recovery_action=recovery_action)
	except Exception as e:
		handle_error_output(ctx, "me", code="NETWORK_ERROR", message=str(e), recoverable=True, recovery_action="重试")
