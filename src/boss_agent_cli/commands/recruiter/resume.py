"""招聘者 — 候选人简历查看与联系方式交换。"""
import click

from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.compliance import require_compliance_allowed
from boss_agent_cli.commands._recruiter_platform import get_recruiter_platform_instance
from boss_agent_cli.commands.recruiter.resume_parser import parse_resume
from boss_agent_cli.display import error_contract_for_code, handle_auth_errors, handle_error_output, handle_output


@click.command("resume")
@click.argument("geek_id", required=False)
@click.option("--job-id", default="", help="职位 ID")
@click.option("--security-id", default=None, help="安全 ID")
@click.option("--exchange", "exchange_contact", is_flag=True, default=False, help="请求交换联系方式")
@click.option("--type", "exchange_type", default="phone", type=click.Choice(["phone", "wechat"]), help="交换类型：phone=手机号 / wechat=微信")
@click.option("--uid", default=None, type=int, help="候选人 uid（交换联系方式时需要）")
@click.option("--gid", default=None, type=int, help="会话 gid（交换联系方式时需要）")
@click.option("--friend-id", default=None, type=int, help="候选人 friendId（交换联系方式新路径; issue #217）")
@click.option("--raw", "show_raw", is_flag=True, default=False, help="输出原始 API 数据（不解析）")
@click.pass_context
@handle_auth_errors("recruiter-resume")
def resume_cmd(ctx: click.Context, geek_id: str | None, job_id: str, security_id: str | None, exchange_contact: bool, exchange_type: str, uid: int | None, gid: int | None, friend_id: int | None, show_raw: bool) -> None:
	"""查看候选人简历或请求交换联系方式"""
	if not require_compliance_allowed(ctx, "recruiter-resume"):
		return

	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]

	auth = AuthManager(data_dir, logger=logger, platform=ctx.obj.get("platform", "zhipin"))
	with get_recruiter_platform_instance(ctx, auth) as platform:
		if exchange_contact:
			# issue #217 修复路径：只要 friend_id 即可，不再要求占位 geek_id。
			# 旧的 uid/gid/jobId 路径已 121 弃用，保留参数仅作过渡兼容。
			if friend_id is None:
				handle_error_output(
					ctx, "recruiter-resume",
					code="INVALID_PARAM",
					message="交换联系方式需要 --friend-id 参数（从 hr chat 获取）",
					recoverable=False,
				)
				return
			type_id = 2 if exchange_type == "wechat" else 1
			result = platform.exchange_request_by_friend(friend_id, exchange_type=type_id)
			if not platform.is_success(result):
				code, message = platform.parse_error(result)
				recoverable, recovery_action = error_contract_for_code(code)
				handle_error_output(
					ctx, "recruiter-resume",
					code=code,
					message=message or "联系方式交换请求失败",
					recoverable=recoverable,
					recovery_action=recovery_action,
				)
				return
			data = platform.unwrap_data(result) or {}
			data["message"] = "联系方式交换请求已发送"
		elif geek_id and security_id and job_id:
			result = platform.view_geek(geek_id, job_id, security_id=security_id)
			if not platform.is_success(result):
				code, message = platform.parse_error(result)
				recoverable, recovery_action = error_contract_for_code(code)
				handle_error_output(
					ctx, "recruiter-resume",
					code=code,
					message=message or "候选人简历获取失败",
					recoverable=recoverable,
					recovery_action=recovery_action,
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
