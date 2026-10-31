import click

from boss_agent_cli.api.models import JobItem, field_list
from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.compliance import require_compliance_allowed
from boss_agent_cli.commands._platform import get_platform_instance
from boss_agent_cli.display import handle_auth_errors, handle_not_supported, handle_output, handle_platform_error_output, render_job_table
from boss_agent_cli.index_cache import try_save_index
from boss_agent_cli.match_score import score_job_dict
from boss_agent_cli.services.job_identity import deduplicate_job_result


@click.command("recommend")
@click.option("--page", default=1, type=int, help="页码")
@click.option("--with-score", is_flag=True, default=False, help="附加匹配分和原因")
@click.pass_context
@handle_auth_errors("recommend")
def recommend_cmd(ctx: click.Context, page: int, with_score: bool) -> None:
	"""基于简历的个性化职位推荐"""
	if not require_compliance_allowed(ctx, "recommend"):
		ctx.exit(1)

	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]

	auth = AuthManager(data_dir, logger=logger, platform=ctx.obj.get("platform", "zhipin"))
	with get_platform_instance(ctx, auth) as platform:
		with CacheStore(data_dir / "cache" / "boss_agent.db") as cache:
			expect_data = None
			if with_score:
				try:
					expect_resp = platform.resume_expect()
				except NotImplementedError as exc:
					handle_not_supported(ctx, "recommend", exc, fallback_message="当前平台不支持求职期望能力")
					return
				else:
					if not platform.is_success(expect_resp):
						handle_platform_error_output(ctx, "recommend", platform, expect_resp, fallback_message="求职期望获取失败")
						return
					expect_data = platform.unwrap_data(expect_resp) or {}

			raw = platform.recommend_jobs(page=page)
			if not platform.is_success(raw):
				handle_platform_error_output(ctx, "recommend", platform, raw, fallback_message="推荐职位获取失败")
				return
			platform_data = platform.unwrap_data(raw) or {}
			job_list = field_list(platform_data, "jobList")

			items = []
			for raw_item in deduplicate_job_result(job_list):
				item = JobItem.from_api(raw_item)
				item.greeted = cache.is_greeted(item.security_id)
				item_dict = item.to_dict()
				if with_score:
					item_dict = score_job_dict(item_dict, criteria=None, expect_data=expect_data)
				items.append(item_dict)

		try_save_index(data_dir, items, source="recommend", logger=logger)

		raw_has_more = platform_data.get("hasMore")
		has_more = raw_has_more if isinstance(raw_has_more, bool) else None
		pagination = {
			"page": page,
			"has_more": has_more,
			"has_more_source": "platform_hasMore" if has_more is not None else "unknown",
			"total": len(items),
			"total_kind": "returned_page_count",
		}
		hints = {
			"next_actions": [
				"使用 boss detail <security_id> 查看职位详情",
				"使用 boss apply <security_id> <job_id> 建立会话，或 boss greet <security_id> <job_id> 沟通",
			],
		}
		if has_more is True:
			hints["next_actions"].append(f"使用 boss recommend --page {page + 1} 查看下一页")
		elif has_more is None:
			hints["operator_actions"] = ["平台未提供可靠的下一页标记，不能仅凭本页数量判断是否还有结果"]
		handle_output(
			ctx, "recommend", items,
			render=lambda data: render_job_table(data, "recommend", page=page),
			pagination=pagination, hints=hints,
		)
