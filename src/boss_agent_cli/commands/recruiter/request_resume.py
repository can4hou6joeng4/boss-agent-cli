"""招聘者 — 请求候选人附件简历。"""
import click

from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.compliance import require_compliance_allowed
from boss_agent_cli.commands._recruiter_platform import get_recruiter_platform_instance
from boss_agent_cli.display import error_contract_for_code, handle_auth_errors, handle_error_output, handle_output


@click.command("request-resume")
@click.argument("friend_id", type=int)
@click.pass_context
@handle_auth_errors("recruiter-request-resume")
def request_resume_cmd(ctx: click.Context, friend_id: int) -> None:
	"""请求候选人分享附件简历（issue #217 修复）

	不再需要 --job-id 参数 — 内部从 friend_detail 自动取出 securityId/jobId/name。
	"""
	if not require_compliance_allowed(ctx, "recruiter-request-resume"):
		return

	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]

	auth = AuthManager(data_dir, logger=logger, platform=ctx.obj.get("platform", "zhipin"))
	with get_recruiter_platform_instance(ctx, auth) as platform:
		# type=4 是抓包实证的"求附件简历"类型；旧代码用 type=3 是错的
		result = platform.exchange_request_by_friend(friend_id, exchange_type=4)
		if not platform.is_success(result):
			code, error_message = platform.parse_error(result)
			recoverable, recovery_action = error_contract_for_code(code)
			handle_error_output(
				ctx, "recruiter-request-resume",
				code=code,
				message=error_message or "附件简历请求失败",
				recoverable=recoverable,
				recovery_action=recovery_action,
			)
			return
		data = {
			"friend_id": friend_id,
			"requested": True,
			"message": "附件简历请求已发送",
		}
		handle_output(
			ctx, "recruiter-request-resume", data,
			hints={"next_actions": [
				"boss hr resume <geek_id> --job-id <id> --security-id <id> — 查看简历",
			]},
		)
