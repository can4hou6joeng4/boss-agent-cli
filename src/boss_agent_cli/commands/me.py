import click

from boss_agent_cli.api.client import AuthError
from boss_agent_cli.auth.manager import AuthManager, AuthRequired, TokenRefreshFailed
from boss_agent_cli.commands._platform import get_platform_instance
from boss_agent_cli.display import boss_command_for_ctx, handle_error_output, handle_output, login_action_for_ctx, render_sectioned_record

NOT_SUPPORTED_RECOVERY_ACTION = "切换平台或调整命令参数后重试"


@click.command("me")
@click.option("--section", default=None, type=click.Choice(["user", "resume", "expect", "deliver"]),
	help="只获取指定部分（不指定则获取全部）")
@click.option("--deliver-page", default=1, type=int, help="投递记录页码")
@click.pass_context
def me_cmd(ctx: click.Context, section: str | None, deliver_page: int) -> None:
	"""获取当前登录用户的个人信息、简历、求职期望、投递记录"""
	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj.get("logger")

	try:
		auth = AuthManager(data_dir, logger=logger, platform=ctx.obj.get("platform", "zhipin"))
		with get_platform_instance(ctx, auth) as platform:
			result = {}

			sections = [section] if section else ["user", "resume", "expect", "deliver"]

			if "user" in sections:
				if logger:
					logger.info("获取用户基本信息...")
				resp = platform.user_info()
				if not platform.is_success(resp):
					code, message = platform.parse_error(resp)
					handle_error_output(
						ctx, "me",
						code=code,
						message=message or "用户基本信息获取失败",
						recoverable=False,
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
					handle_error_output(
						ctx, "me",
						code="NOT_SUPPORTED",
						message=str(exc) or "当前平台不支持简历基本信息能力",
						recoverable=True,
						recovery_action=NOT_SUPPORTED_RECOVERY_ACTION,
					)
					return
				if not platform.is_success(resp):
					code, message = platform.parse_error(resp)
					handle_error_output(
						ctx, "me",
						code=code,
						message=message or "简历基本信息获取失败",
						recoverable=False,
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
					handle_error_output(
						ctx, "me",
						code="NOT_SUPPORTED",
						message=str(exc) or "当前平台不支持求职期望能力",
						recoverable=True,
						recovery_action=NOT_SUPPORTED_RECOVERY_ACTION,
					)
					return
				if not platform.is_success(resp):
					code, message = platform.parse_error(resp)
					handle_error_output(
						ctx, "me",
						code=code,
						message=message or "求职期望获取失败",
						recoverable=False,
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
					handle_error_output(
						ctx, "me",
						code="NOT_SUPPORTED",
						message=str(exc) or "当前平台不支持投递记录能力",
						recoverable=True,
						recovery_action=NOT_SUPPORTED_RECOVERY_ACTION,
					)
					return
				if not platform.is_success(resp):
					code, message = platform.parse_error(resp)
					handle_error_output(
						ctx, "me",
						code=code,
						message=message or "投递记录获取失败",
						recoverable=False,
					)
					return
				zp_data = platform.unwrap_data(resp) or {}
				result["deliver"] = zp_data

			handle_output(
				ctx, "me", result,
				render=lambda d: render_sectioned_record(d, title="me"),
				hints={
					"next_actions": [
						boss_command_for_ctx(ctx, "search <关键词> --city <城市>"),
						boss_command_for_ctx(ctx, "recommend"),
					],
				},
			)

	except (AuthRequired, TokenRefreshFailed):
		handle_error_output(ctx, "me", code="AUTH_REQUIRED", message="未登录", recoverable=True, recovery_action=login_action_for_ctx(ctx))
	except AuthError:
		handle_error_output(ctx, "me", code="AUTH_EXPIRED", message="登录态过期", recoverable=True, recovery_action=login_action_for_ctx(ctx))
	except Exception as e:
		handle_error_output(ctx, "me", code="NETWORK_ERROR", message=str(e), recoverable=True, recovery_action="重试")
