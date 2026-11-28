import click

from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.compliance import require_compliance_allowed
from boss_agent_cli.commands._platform import account_scope, get_platform_instance
from boss_agent_cli.display import boss_command_for_ctx, handle_auth_errors, handle_not_supported, handle_output, handle_platform_error_output, render_job_table, show_command_for_ctx
from boss_agent_cli.index_cache import try_save_index
from boss_agent_cli.services.pagination import (
	MAX_TRUSTED_PAGE,
	clamp_trusted,
	dict_rows_from,
	list_pagination,
	page_can_advance,
)
from boss_agent_cli.services.recommend_items import build_recommend_items


def _hint_command(ctx: click.Context, command: str) -> str:
	"""推荐翻页与后继操作沿用当前登录态和索引目录。"""
	return boss_command_for_ctx(ctx, command, include_data_dir=True)


@click.command("recommend")
@click.option("--page", default=1, type=click.IntRange(min=1, max=MAX_TRUSTED_PAGE), help="页码")
@click.option("--with-score", is_flag=True, default=False, help="附加匹配分和原因")
@click.pass_context
@handle_auth_errors("recommend")
def recommend_cmd(ctx: click.Context, page: int, with_score: bool) -> None:
	"""基于简历的个性化职位推荐"""
	if not require_compliance_allowed(ctx, "recommend"):
		ctx.exit(1)

	# 直接调用回调时也保护平台请求、信封与索引写入（Issue #2550）。
	page = clamp_trusted(page)

	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]

	auth = AuthManager(data_dir, logger=logger, platform=ctx.obj.get("platform", "zhipin"))
	account = account_scope(auth)
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
					expect_data = platform.unwrap_data(expect_resp)
					if not isinstance(expect_data, dict):
						expect_data = {}

			raw = platform.recommend_jobs(page=page)
			if not platform.is_success(raw):
				handle_platform_error_output(ctx, "recommend", platform, raw, fallback_message="推荐职位获取失败")
				return
			platform_data = platform.unwrap_data(raw)
			if not isinstance(platform_data, dict):
				platform_data = {}
			job_list = dict_rows_from(
				platform_data, "recommendList", "jobList", "list", "items", "result"
			) or []

			# 归一、问候态与匹配分与向导 recommendations 共用同一份实现，
			# 两处入口的字段集合与账号口径不会各自漂移（Issue #2662）。
			items = build_recommend_items(
				job_list,
				cache=cache,
				account=account,
				with_score=with_score,
				expect_data=expect_data,
			)

		# 页码证据与向导共用 list_pagination：平台纠正页码时按平台为准，
		# 索引快照、翻页提示与表格页码都必须用同一份证据（Issue #3101）。
		pagination = list_pagination(platform_data, page=page, rows=items)
		current_page = pagination["page"]
		snapshot = try_save_index(data_dir, items, source="recommend", page=current_page, logger=logger)
		saved = bool(snapshot)

		has_more = pagination["has_more"]
		# 信封里的快照身份取本次写入的值（Issue #2292）。
		if isinstance(snapshot, dict):
			pagination["index_snapshot"] = snapshot
		hints = {
			"next_actions": [
				_hint_command(ctx, "detail <security_id>"),
				_hint_command(ctx, "apply <security_id> <job_id>"),
				_hint_command(ctx, "greet <security_id> <job_id>"),
			],
		}
		if has_more is True and page_can_advance(pagination):
			# 翻页命令必须保留本次的 --with-score：只写 --page 时下一页的
			# match_score 全部消失，Agent 会以为排序依据变了。
			next_page = f"recommend --page {current_page + 1}"
			if with_score:
				next_page += " --with-score"
			hints["next_actions"].append(_hint_command(ctx, next_page))
		elif has_more is None:
			hints["operator_actions"] = ["平台未提供可靠的下一页标记，不能仅凭本页数量判断是否还有结果"]
		handle_output(
			ctx, "recommend", items,
			render=lambda data: render_job_table(
				data, "recommend", page=current_page, show_command=show_command_for_ctx(ctx, index_saved=saved),
			),
			pagination=pagination, hints=hints,
		)
