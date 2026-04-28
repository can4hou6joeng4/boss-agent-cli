"""招聘者 — 候选人简历查看与联系方式交换。"""
import click

from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.commands._recruiter_platform import get_recruiter_platform_instance
from boss_agent_cli.commands.recruiter.resume_parser import parse_resume
from boss_agent_cli.display import handle_auth_errors, handle_error_output, handle_output


@click.command("resume")
@click.argument("geek_id")
@click.option("--job-id", default="", help="职位 ID")
@click.option("--security-id", default=None, help="安全 ID")
@click.option("--exchange", "exchange_contact", is_flag=True, default=False, help="请求交换联系方式")
@click.option("--uid", default=None, type=int, help="候选人 uid（交换联系方式时需要）")
@click.option("--gid", default=None, type=int, help="会话 gid（交换联系方式时需要）")
@click.option("--raw", "show_raw", is_flag=True, default=False, help="输出原始 API 数据（不解析）")
@click.pass_context
@handle_auth_errors("recruiter-resume")
def resume_cmd(ctx: click.Context, geek_id: str, job_id: str, security_id: str | None, exchange_contact: bool, uid: int | None, gid: int | None, show_raw: bool) -> None:
	"""查看候选人简历或请求交换联系方式"""
	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]

	auth = AuthManager(data_dir, logger=logger, platform=ctx.obj.get("platform", "zhipin"))
	with get_recruiter_platform_instance(ctx, auth) as platform:
		if exchange_contact:
			if not uid or not gid or not job_id:
				handle_error_output(
					ctx, "recruiter-resume",
					code="INVALID_PARAM",
					message="交换联系方式需要 --uid、--gid 和 --job-id 参数",
					recoverable=False,
				)
				return
			result = platform.exchange_request(1, uid, int(job_id), gid)
			if not platform.is_success(result):
				code, message = platform.parse_error(result)
				handle_error_output(
					ctx, "recruiter-resume",
					code=code,
					message=message or "联系方式交换请求失败",
					recoverable=False,
				)
				return
			data = platform.unwrap_data(result) or {}
			data["message"] = "联系方式交换请求已发送"
		elif security_id and job_id:
			result = platform.view_geek(geek_id, job_id, security_id=security_id)
			if not platform.is_success(result):
				code, message = platform.parse_error(result)
				handle_error_output(
					ctx, "recruiter-resume",
					code=code,
					message=message or "候选人简历获取失败",
					recoverable=False,
				)
				return
			data = result if show_raw else parse_resume(result)
		else:
			handle_error_output(
				ctx, "recruiter-resume",
				code="INVALID_PARAM",
				message="查看简历需要 --job-id 和 --security-id 参数",
				recoverable=False,
			)
			return

		handle_output(
			ctx, "recruiter-resume", data,
			hints={"next_actions": [
				"boss hr applications — 返回候选人列表",
			]},
		)
