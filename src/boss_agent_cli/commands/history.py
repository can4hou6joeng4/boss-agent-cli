from typing import Any

import click

from boss_agent_cli.api.models import dict_items, JobItem, field_list
from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.commands._platform import get_platform_instance
from boss_agent_cli.display import boss_command_for_ctx, handle_auth_errors, handle_not_supported, handle_output, handle_platform_error_output, render_job_table
from boss_agent_cli.services.pagination import MAX_TRUSTED_PAGE


_HISTORY_ID_KEYS = ("encryptJobId", "securityId", "lid")
_HISTORY_TEXT_KEYS = (
	"jobName",
	"brandName",
	"salaryDesc",
	"cityName",
	"areaDistrict",
	"jobExperience",
	"jobDegree",
	"brandIndustry",
	"brandScaleName",
	"brandStageName",
	"bossName",
	"bossTitle",
	"daysPerWeekDesc",
	"leastMonthDesc",
)
_HISTORY_LIST_KEYS = ("skills", "welfareList", "jobLabels")


def _normalize_history_job(raw_item: dict[str, Any]) -> dict[str, Any]:
	"""把类型漂移收敛成 `JobItem` 可安全消费的形状（Issue #1855）。

	ID / 文本字段里的 dict / list 会被 ``field_text`` 的 ``str()`` 变成 Python
	repr 并作为 job_id / security_id / job_url 写进信封，Agent 再拿它去跑
	detail / apply / greet 只会得到参数错误。容器一律按缺失处理；列表字段
	只保留字符串元素。
	"""
	item = dict(raw_item)
	for key in (*_HISTORY_ID_KEYS, *_HISTORY_TEXT_KEYS):
		if isinstance(item.get(key), (dict, list, tuple, set)):
			item[key] = ""
	for key in _HISTORY_LIST_KEYS:
		value = item.get(key)
		if isinstance(value, list):
			item[key] = [element for element in value if isinstance(element, str)]
	return item


def _hint_command(ctx: click.Context, command: str) -> str:
	"""浏览历史的详情、投递与翻页命令沿用产生这份列表的数据目录。"""
	return boss_command_for_ctx(ctx, command, include_data_dir=True)


@click.command("history")
@click.option(
	"--page", default=1, type=click.IntRange(min=1, max=MAX_TRUSTED_PAGE),
	help="页码",
)
@click.pass_context
@handle_auth_errors("history")
def history_cmd(ctx: click.Context, page: int) -> None:
	"""查看最近浏览过的职位"""
	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]

	auth = AuthManager(data_dir, logger=logger, platform=ctx.obj.get("platform", "zhipin"))
	with get_platform_instance(ctx, auth) as platform:
		try:
			raw = platform.job_history(page)
		except NotImplementedError as exc:
			handle_not_supported(ctx, "history", exc, fallback_message="当前平台不支持浏览历史能力")
			return
		if not platform.is_success(raw):
			handle_platform_error_output(
				ctx, "history", platform, raw,
				fallback_message="浏览历史获取失败",
			)
			return
		platform_data = platform.unwrap_data(raw) or {}
		# 非对象载荷（字符串 / 数组 / 数字）与 null 同口径按空页处理：后续
		# `hasMore` 读取和分页证据都按「平台没给」输出，而不是 INTERNAL_ERROR。
		if not isinstance(platform_data, dict):
			platform_data = {}
		job_list = dict_items(field_list(platform_data, "jobList"))

		items = [JobItem.from_api(_normalize_history_job(raw_item)).to_dict() for raw_item in job_list]

	# 平台缺失 hasMore 时不能当成 false：那会让 Agent 把「平台没说」读成
	# 「确实没有下一页」并停止翻页。与 recommend / chat 同一套保守语义。
	raw_has_more = platform_data.get("hasMore")
	has_more = raw_has_more if isinstance(raw_has_more, bool) else None
	pagination = {
		"page": page,
		"has_more": has_more,
		"has_more_source": "platform_hasMore" if has_more is not None else "unknown",
		"total": len(items),
		"total_kind": "returned_page_count",
	}
	next_actions = [
		_hint_command(ctx, "detail <security_id>"),
		_hint_command(ctx, "apply <security_id> <job_id>"),
		_hint_command(ctx, "greet <security_id> <job_id>"),
	]
	# 没有下一页时不能建议 --page N+1：Agent 照着执行只会拿到空页，
	# 与 TTY 的 hint_next 守卫（hasMore 为假时不显示 more）保持一致。
	at_page_limit = page >= MAX_TRUSTED_PAGE
	if pagination["has_more"] is True and not at_page_limit:
		next_actions.append(_hint_command(ctx, f"history --page {page + 1}"))
	hints = {"next_actions": next_actions}
	if has_more is None:
		hints["operator_actions"] = ["平台未提供可靠的下一页标记，不能仅凭本页数量判断是否还有记录"]
	elif has_more is True and at_page_limit:
		# 上限页再 +1 的提示自己就过不了 --page 校验（Issue #2012）：
		# 告诉 Agent 已到可信上限，而不是给一条不可执行的命令。
		hints["operator_actions"] = [
			f"已达可信页码上限（--page {MAX_TRUSTED_PAGE}），平台仍报告有更多记录；"
			"如需继续，请缩小筛选条件后重新搜索"
		]

	handle_output(
		ctx, "history", items,
		render=lambda data: render_job_table(
			data, "history",
			page=page,
			hint_next=(
				f"more: {_hint_command(ctx, f'history --page {page + 1}')}"
				if pagination["has_more"] and not at_page_limit else ""
			),
		),
		pagination=pagination, hints=hints,
	)
