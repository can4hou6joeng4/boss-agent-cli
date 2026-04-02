import click

from boss_agent_cli.api.client import BossClient
from boss_agent_cli.api.models import JobItem
from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.display import handle_auth_errors, handle_output, render_job_table


@click.command("history")
@click.option("--page", default=1, help="页码")
@click.pass_context
@handle_auth_errors("history")
def history_cmd(ctx, page):
	"""查看最近浏览过的职位"""
	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]
	delay = ctx.obj["delay"]
	cdp_url = ctx.obj.get("cdp_url")

	auth = AuthManager(data_dir, logger=logger)
	client = BossClient(auth, delay=delay, cdp_url=cdp_url)

	raw = client.job_history(page)
	zp_data = raw.get("zpData", {})
	job_list = zp_data.get("jobList", [])

	items = [JobItem.from_api(raw_item).to_dict() for raw_item in job_list]

	pagination = {
		"page": page,
		"has_more": zp_data.get("hasMore", False),
		"total": len(items),
	}
	hints = {
		"next_actions": [
			"使用 boss detail <security_id> 查看职位详情",
			"使用 boss greet <security_id> <job_id> 打招呼",
			"使用 boss history --page {} 查看下一页".format(page + 1),
		],
	}

	handle_output(
		ctx, "history", items,
		render=lambda data: render_job_table(
			data, "history",
			page=page,
			hint_next=f"more: boss history --page {page + 1}" if zp_data.get("hasMore") else "",
		),
		pagination=pagination, hints=hints,
	)
