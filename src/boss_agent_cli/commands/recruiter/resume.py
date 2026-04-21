"""招聘者 — 简历查看与请求。"""
import click

from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.commands._recruiter_platform import get_recruiter_platform_instance
from boss_agent_cli.display import handle_auth_errors, handle_error_output, handle_output


@click.command("resume")
@click.argument("geek_id")
@click.option("--security-id", default="", help="安全 ID")
@click.option("--request", "request_resume", is_flag=True, default=False, help="请求候选人分享简历")
@click.pass_context
@handle_auth_errors("recruiter-resume")
def resume_cmd(ctx: click.Context, geek_id: str, security_id: str, request_resume: bool) -> None:
	"""查看或请求候选人简历"""
	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]

	auth = AuthManager(data_dir, logger=logger)
	with get_recruiter_platform_instance(ctx, auth) as platform:
		if request_resume:
			platform.request_resume(geek_id)
			data = {"geek_id": geek_id, "message": "简历请求已发送"}
		elif security_id:
			result = platform.get_resume(geek_id, security_id)
			data = result.get("zpData", {})
		else:
			handle_error_output(
				ctx, "recruiter-resume",
				code="INVALID_PARAM",
				message="查看简历需要 --security-id 参数",
				recoverable=False,
			)
			return

		handle_output(
			ctx, "recruiter-resume", data,
			hints={"next_actions": [
				"boss --role recruiter recruiter applications — 返回申请列表",
			]},
		)
