import click

from boss_agent_cli.api.models import JobItem
from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.commands._platform import get_platform_instance
from boss_agent_cli.display import error_contract_for_code, handle_auth_errors, handle_error_output, handle_output, render_job_table
from boss_agent_cli.index_cache import try_save_index
from boss_agent_cli.match_score import score_job_dict

NOT_SUPPORTED_RECOVERY_ACTION = "切换平台或调整命令参数后重试"


@click.command("recommend")
@click.option("--page", default=1, type=int, help="页码")
@click.option("--with-score", is_flag=True, default=False, help="附加匹配分和原因")
@click.pass_context
@handle_auth_errors("recommend")
def recommend_cmd(ctx: click.Context, page: int, with_score: bool) -> None:
	"""基于简历的个性化职位推荐"""
	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]

	auth = AuthManager(data_dir, logger=logger, platform=ctx.obj.get("platform", "zhipin"))
	with get_platform_instance(ctx, auth) as platform:
		with CacheStore(data_dir / "cache" / "boss_agent.db") as cache:
			expect_data = None
			if with_score:
				try:
					expect_resp = platform.resume_expect()
				except NotImplementedError:
					handle_error_output(
						ctx, "recommend",
						code="NOT_SUPPORTED",
						message="当前平台暂不支持带期望加权的推荐评分，请去掉 --with-score 或切换平台后重试",
						recoverable=True,
						recovery_action=NOT_SUPPORTED_RECOVERY_ACTION,
					)
					return
				else:
					if not platform.is_success(expect_resp):
						code, message = platform.parse_error(expect_resp)
						recoverable, recovery_action = error_contract_for_code(code)
						handle_error_output(
							ctx, "recommend",
							code=code,
							message=message or "求职期望获取失败",
							recoverable=recoverable,
							recovery_action=recovery_action,
						)
						return
					expect_data = platform.unwrap_data(expect_resp) or {}

			raw = platform.recommend_jobs(page=page)
			if not platform.is_success(raw):
				code, message = platform.parse_error(raw)
				recoverable, recovery_action = error_contract_for_code(code)
				handle_error_output(
					ctx, "recommend",
					code=code,
					message=message or "推荐职位获取失败",
					recoverable=recoverable,
					recovery_action=recovery_action,
				)
				return
			platform_data = platform.unwrap_data(raw) or {}
			job_list = platform_data.get("jobList", [])

			items = []
			for raw_item in job_list:
				item = JobItem.from_api(raw_item)
				item.greeted = cache.is_greeted(item.security_id)
				item_dict = item.to_dict()
				if with_score:
					item_dict = score_job_dict(item_dict, criteria=None, expect_data=expect_data)
				items.append(item_dict)

		try_save_index(data_dir, items, source="recommend", logger=logger)

		pagination = {
			"page": page,
			"has_more": platform_data.get("hasMore", False),
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
