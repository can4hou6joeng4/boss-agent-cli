import click

from boss_agent_cli.api.client import BossClient
from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.display import handle_auth_errors, handle_error_output, handle_output, render_simple_list
from typing import Any


@click.command("interviews")
@click.pass_context
@handle_auth_errors("interviews")
def interviews_cmd(ctx: click.Context) -> None:
	"""查看面试邀请列表"""
	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]
	delay = ctx.obj["delay"]
	cdp_url = ctx.obj.get("cdp_url")
	auth = AuthManager(data_dir, logger=logger)

	token = auth.check_status()
	if token is None:
		handle_error_output(
			ctx, "interviews",
			code="AUTH_REQUIRED",
			message="未登录，请先执行 boss login",
			recoverable=True, recovery_action="boss login",
		)
		return

	with BossClient(auth, delay=delay, cdp_url=cdp_url) as client:
		raw = client.interview_data()
		interview_list = raw.get("zpData", {}).get("interviewList", [])

	items = [
		{
			"jobName": it.get("jobName", "-"),
			"brandName": it.get("brandName", "-"),
			"interviewTime": it.get("interviewTime", "-"),
			"address": it.get("address", "-"),
			"statusDesc": it.get("statusDesc", "-"),
		}
		for it in interview_list
	]

	def _render(data: list[dict[str, Any]]) -> None:
		render_simple_list(
			data,
			"interviews",
			columns=[
				("job", "jobName", "bold cyan"),
				("company", "brandName", "green"),
				("time", "interviewTime", "yellow"),
				("address", "address", ""),
				("status", "statusDesc", "blue"),
			],
		)

	handle_output(
		ctx, "interviews", items,
		render=_render,
		hints={"next_actions": ["boss detail <job_id>"]},
	)
