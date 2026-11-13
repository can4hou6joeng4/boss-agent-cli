"""招聘者 — 下载已收到的附件简历。"""

from pathlib import Path

import click

from boss_agent_cli.api.client import PlatformRiskError
from boss_agent_cli.api.request_budget import is_request_stop
from boss_agent_cli.api.recruiter_client import RecruiterAuthError
from boss_agent_cli.api.recruiter_resume import ResumeValidationError
from boss_agent_cli.auth.manager import AuthManager, AuthRequired, TokenRefreshFailed
from boss_agent_cli.commands._recruiter_platform import get_recruiter_platform_instance
from boss_agent_cli.compliance import require_compliance_allowed
from boss_agent_cli.services.output_path import prepare_output_path
from boss_agent_cli.commands.recruiter._hints import hint_command
from boss_agent_cli.display import (
	auth_required_hints,
	error_contract_for_code,
	handle_auth_errors,
	handle_error_output,
	handle_output,
	login_action_for_ctx,
	platform_error_contract,
	risk_error_message,
)


@click.command("download-resume")
@click.argument("friend_id", type=click.IntRange(min=1))
@click.option(
	"--message-id",
	required=True,
	type=click.IntRange(min=1),
	help="hr chatmsg 返回的已收到附件消息 mid（不是请求 mid）",
)
@click.option(
	"--output",
	required=True,
	type=click.Path(dir_okay=False, path_type=Path),
	help="本地文件路径，支持 PDF/Word/PNG/JPEG；不覆盖已有文件",
)
@click.pass_context
@handle_auth_errors("recruiter-download-resume")
def download_resume_cmd(ctx: click.Context, friend_id: int, message_id: int, output: Path) -> None:
	"""检查权限后下载指定附件；不自动同意、不索要简历、不发送已读回执。"""
	if not require_compliance_allowed(ctx, "recruiter-download-resume"):
		return
	command = "recruiter-download-resume"
	# MCP 子进程调用不经 shell，`~` 不会被展开；父目录也可能尚不存在。
	# 与其他 `-o` 命令一致，先展开并建好父目录；是否覆盖已有文件仍由客户端判断。
	output = prepare_output_path(output)
	auth = AuthManager(ctx.obj["data_dir"], logger=ctx.obj["logger"], platform=ctx.obj.get("platform", "zhipin"))
	with get_recruiter_platform_instance(ctx, auth) as platform:
		auth.get_token()
		try:
			result = platform.download_resume_by_friend(friend_id, message_id, output)
		except ResumeValidationError as exc:
			chat_cmd = hint_command(ctx, "hr chat")
			handle_error_output(
				ctx,
				command,
				code="INVALID_PARAM",
				message=str(exc),
				recovery_action=f"按提示核对 friend_id、message_id 与附件状态后重试；不确定时用 {chat_cmd} 核对会话",
				hints={"next_actions": [chat_cmd]},
			)
			return
		except FileExistsError:
			handle_error_output(
				ctx,
				command,
				code="INVALID_PARAM",
				message="输出文件已存在，请指定新文件名；不会覆盖",
				recovery_action="换一个 --output 文件名后重试；已有文件不会被覆盖",
			)
			return
		except NotImplementedError:
			handle_error_output(ctx, command, code="PLATFORM_NOT_SUPPORTED", message="当前平台不支持下载附件简历")
			return
		except OSError:
			handle_error_output(
				ctx,
				command,
				code="INVALID_PARAM",
				message="无法保存文件，请检查目标目录与写入权限",
				recovery_action="确认 --output 的父目录存在且可写后重试",
			)
			return
		except (AuthRequired, RecruiterAuthError):
			recoverable, _ = error_contract_for_code("AUTH_REQUIRED")
			handle_error_output(
				ctx,
				command,
				code="AUTH_REQUIRED",
				message="登录态不可用，请重新登录后下载",
				recoverable=recoverable,
				recovery_action=login_action_for_ctx(ctx),
				hints=auth_required_hints(ctx),
			)
			return
		except TokenRefreshFailed:
			recoverable, _ = error_contract_for_code("TOKEN_REFRESH_FAILED")
			handle_error_output(
				ctx,
				command,
				code="TOKEN_REFRESH_FAILED",
				message="登录态刷新失败，请重新登录后下载",
				recoverable=recoverable,
				recovery_action=login_action_for_ctx(ctx),
				hints=auth_required_hints(ctx),
			)
			return
		except PlatformRiskError as exc:
			# 风控异常是多子类结构：EnvironmentRiskError / EnvironmentRiskLockedError
			# 与 AccountRiskError 平级。只捕账号风控会把环境风控漏进下面的
			# NETWORK_ERROR 兜底，反而提示“检查网络后重试”，违反风控硬边界。
			_, recovery_action, risk_hints = platform_error_contract(exc.code, ctx=ctx)
			handle_error_output(
				ctx,
				command,
				code=exc.code,
				message=risk_error_message(exc.code),
				recoverable=False,
				recovery_action=recovery_action,
				details=exc.details or None,
				hints=risk_hints,
			)
			return
		except Exception as exc:
			# 预算/超时停止不是下载失败：不能提示「检查网络后重试」，交给装饰器按停止契约输出。
			if is_request_stop(exc):
				raise
			# httpx 异常可能带有含临时凭据的 URL，不透传异常文本。
			recoverable, recovery_action = error_contract_for_code("NETWORK_ERROR")
			handle_error_output(
				ctx,
				command,
				code="NETWORK_ERROR",
				message="附件下载未完成，请检查网络后重试",
				recoverable=recoverable,
				recovery_action=recovery_action,
			)
			return
		if not platform.is_success(result):
			code, _ = platform.parse_error(result)
			code = code if code != "UNKNOWN" else "NETWORK_ERROR"
			recoverable, recovery_action, risk_hints = platform_error_contract(code, ctx=ctx)
			handle_error_output(
				ctx,
				command,
				code=code,
				message="平台未允许获取附件，请在官方页面核对登录态与附件权限",
				recoverable=recoverable,
				recovery_action=recovery_action,
				hints=risk_hints,
			)
			return
		handle_output(
			ctx,
			command,
			{"friend_id": friend_id, "message_id": message_id, "downloaded": True, **platform.unwrap_data(result)},
			# 附件落盘后没有下一步：Agent 只能自己猜文件在哪、也不会回到会话继续沟通。
			hints={
				"next_actions": [hint_command(ctx, f"hr chatmsg {friend_id}")],
				"operator_actions": [f"附件已保存到 {output.resolve()}"],
			},
		)
