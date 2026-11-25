"""招聘者 — 候选人简历查看与联系方式交换。"""
import click

from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.compliance import require_compliance_allowed
from boss_agent_cli.commands._recruiter_platform import get_recruiter_platform_instance
from boss_agent_cli.commands._text_guard import reject_unencodable_params
from boss_agent_cli.services.resume_parser import parse_resume
from boss_agent_cli.commands.recruiter._hints import hint_command
from boss_agent_cli.commands.recruiter._render import render_resume
from boss_agent_cli.display import handle_auth_errors, handle_error_output, handle_output, handle_platform_error_output


@click.command("resume")
@click.argument("geek_id", required=False)
@click.option("--job-id", default="", help="职位 ID")
@click.option("--security-id", default=None, help="安全 ID")
@click.option("--exchange", "exchange_contact", is_flag=True, default=False, help="请求交换联系方式")
@click.option("--type", "exchange_type", default="phone", type=click.Choice(["phone", "wechat"]), help="交换类型：phone=手机号 / wechat=微信")
@click.option("--uid", default=None, type=int, help="已弃用：旧的交换联系方式参数，当前被忽略；请改用 --friend-id")
@click.option("--gid", default=None, type=int, help="已弃用：旧的交换联系方式参数，当前被忽略；请改用 --friend-id")
@click.option("--friend-id", default=None, type=click.IntRange(min=1), help="候选人 friendId（交换联系方式新路径; issue #217）")
@click.option("--raw", "show_raw", is_flag=True, default=False, help="输出原始 API 数据（不解析）")
@click.pass_context
@handle_auth_errors("recruiter-resume")
def resume_cmd(ctx: click.Context, geek_id: str | None, job_id: str, security_id: str | None, exchange_contact: bool, exchange_type: str, uid: int | None, gid: int | None, friend_id: int | None, show_raw: bool) -> None:
	"""查看候选人简历或请求交换联系方式。

	在线简历的 geek_id 使用候选人 encryptGeekId（请求参数 uid），
	--job-id 使用 encryptJobId（请求参数 encryptJid）。
	"""
	if not require_compliance_allowed(ctx, "recruiter-resume"):
		return

	# view_geek 把这些标识原样拼进 httpx 查询参数：不可编码的字符会在发送
	# 阶段失败，入口先按参数错误拒绝（Issue #2533）。
	if reject_unencodable_params(
		ctx, "recruiter-resume", geek_id=geek_id, job_id=job_id, security_id=security_id
	):
		return

	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]

	auth = AuthManager(data_dir, logger=logger, platform=ctx.obj.get("platform", "zhipin"))
	with get_recruiter_platform_instance(ctx, auth) as platform:
		if exchange_contact:
			# issue #217 修复路径：只要 friend_id 即可，不再要求占位 geek_id。
			# 旧的 uid/gid/jobId 路径已 121 弃用，保留参数仅作过渡兼容。
			if friend_id is None:
				chat_cmd = hint_command(ctx, "hr chat")
				handle_error_output(
					ctx, "recruiter-resume",
					code="INVALID_PARAM",
					message="交换联系方式需要 --friend-id 参数（从 hr chat 获取）",
					recoverable=False,
					recovery_action=f"用 {chat_cmd} 取到候选人的 friendId，再补 --friend-id 重试",
					hints={"next_actions": [chat_cmd]},
				)
				return
			type_id = 2 if exchange_type == "wechat" else 1
			result = platform.exchange_request_by_friend(friend_id, exchange_type=type_id)
			if not platform.is_success(result):
				handle_platform_error_output(
					ctx, "recruiter-resume", platform, result, fallback_message="联系方式交换请求失败", read_only_operation=False
				)
				return
			data = platform.unwrap_data(result)
			if not isinstance(data, dict):
				data = {}
			data["message"] = "联系方式交换请求已发送"
		elif geek_id and geek_id.strip() and security_id and security_id.strip() and job_id.strip():
			result = platform.view_geek(geek_id, job_id, security_id=security_id)
			if not platform.is_success(result):
				handle_platform_error_output(ctx, "recruiter-resume", platform, result, fallback_message="候选人简历获取失败")
				return
			data = result if show_raw else parse_resume(result)
		else:
			# 按实际缺失项生成提示：漏了位置参数 geek_id 时仍让人补
			# --job-id / --security-id，Agent 会拿同一组参数重试循环
			# （Issue #2069）。
			apps_cmd = hint_command(ctx, "hr applications")
			candidates_cmd = hint_command(ctx, "hr candidates")
			missing: list[str] = []
			sources: list[str] = []
			if not (geek_id and geek_id.strip()):
				missing.append("geek_id")
				sources.append(f"geek_id 可从 {candidates_cmd} 的结果中取到")
			job_missing = not job_id.strip()
			security_missing = not (security_id and security_id.strip())
			if job_missing:
				missing.append("--job-id")
			if security_missing:
				missing.append("--security-id")
			if job_missing or security_missing:
				sources.append(f"--job-id 与 --security-id 可从 {apps_cmd} 的结果中取到")
			handle_error_output(
				ctx, "recruiter-resume",
				code="INVALID_PARAM",
				message=f"查看简历缺少参数：{', '.join(missing)}",
				recoverable=False,
				recovery_action=f"补上 {' 与 '.join(missing)} 后重试；{'；'.join(sources)}",
				hints={"next_actions": [apps_cmd if (job_missing or security_missing) else candidates_cmd]},
			)
			return

		next_actions = [hint_command(ctx, "hr applications")]
		handle_output(
			ctx, "recruiter-resume", data,
			render=lambda d: render_resume(d, title="hr resume", next_steps=next_actions),
			hints={"next_actions": next_actions},
		)
