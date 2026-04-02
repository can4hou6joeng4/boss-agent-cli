import click

from boss_agent_cli.api.client import BossClient
from boss_agent_cli.api.models import JobItem
from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.display import handle_output, render_job_table, handle_auth_errors
from boss_agent_cli.index_cache import save_index


@click.command("recommend")
@click.option("--page", default=1, type=int, help="页码")
@click.pass_context
@handle_auth_errors("recommend")
def recommend_cmd(ctx, page):
	"""基于简历的个性化职位推荐"""
	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]
	delay = ctx.obj["delay"]
	cdp_url = ctx.obj.get("cdp_url")

	auth = AuthManager(data_dir, logger=logger)
	with BossClient(auth, delay=delay, cdp_url=cdp_url) as client:
		with CacheStore(data_dir / "cache" / "boss_agent.db") as cache:

			raw = client.recommend_jobs(page=page)
			zp_data = raw.get("zpData", {})
			job_list = zp_data.get("jobList", [])

			items = []
			for raw_item in job_list:
				item = JobItem.from_api(raw_item)
				item.greeted = cache.is_greeted(item.security_id)
				items.append(item.to_dict())

		save_index(data_dir, items, source="recommend")

		pagination = {
			"page": page,
			"has_more": zp_data.get("hasMore", False),
			"total": len(items),
		}
		hints = {
			"next_actions": [
				"使用 boss detail <security_id> 查看职位详情",
				"使用 boss greet <security_id> <job_id> 打招呼",
				f"使用 boss recommend --page {page + 1} 查看下一页",
			],
		}
		handle_output(
			ctx, "recommend", items,
			render=lambda data: render_job_table(data, "recommend", page=page),
			pagination=pagination, hints=hints,
		)
