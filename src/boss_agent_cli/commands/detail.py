from pathlib import Path
from typing import Any

import click

from boss_agent_cli.api.client import RISK_ERROR_BY_CODE
from boss_agent_cli.api.request_budget import is_request_stop
from boss_agent_cli.api.models import employment_type_from_raw, field_dict, field_list, field_text
from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.commands._platform import account_scope, get_platform_instance
from boss_agent_cli.display import (
	boss_command_for_ctx,
	handle_auth_errors,
	handle_error_output,
	handle_output,
	platform_error_contract,
	platform_error_message,
	render_job_detail,
)
from boss_agent_cli.platforms import Platform
from boss_agent_cli.services.job_card import build_job_from_card

DetailError = tuple[str, str, dict[str, Any] | None]


def _hint_command(ctx: click.Context, command: str) -> str:
	"""详情的后继操作沿用当前登录态与缓存目录。"""
	return boss_command_for_ctx(ctx, command, include_data_dir=True)


def _platform_error_details(response: Any) -> dict[str, Any] | None:
	if isinstance(response, dict):
		error = response.get("error")
		if isinstance(error, dict):
			details = error.get("details")
			if isinstance(details, dict):
				return details
	return None


@click.command("detail")
@click.argument("security_id")
@click.option("--lid", default="", help="列表项 ID（从 search 结果获取，可选）")
@click.option("--job-id", default="", help="职位加密 ID（提供时走 httpx 快速通道，跳过浏览器）")
@click.pass_context
@handle_auth_errors("detail")
def detail_cmd(ctx: click.Context, security_id: str, lid: str, job_id: str) -> None:
	"""查看职位完整信息（职位描述、地址、招聘者信息）"""
	# 空 security_id 不是「查不到」，而是参数本身就构造错了：照常发请求只会
	# 换来一次无意义的平台往返，Agent 还会以为是缓存或登录的问题。
	if not security_id.strip():
		handle_error_output(
			ctx,
			"detail",
			code="INVALID_PARAM",
			message="security_id 不能为空；请先搜索职位或查看已有编号",
			recovery_action=f"先执行 {_hint_command(ctx, 'search <关键词>')} 或 {_hint_command(ctx, 'show <编号>')} 取到编号后重试",
			hints={"next_actions": [_hint_command(ctx, "search <关键词>"), _hint_command(ctx, "show <编号>")]},
		)
		return
	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]

	auth = AuthManager(data_dir, logger=logger, platform=ctx.obj.get("platform", "zhipin"))
	account = account_scope(auth)
	with get_platform_instance(ctx, auth) as platform:
		# 优先走 httpx 快速通道：显式传入 > 缓存查找 > 降级浏览器通道
		if not job_id:
			with CacheStore(data_dir / "cache" / "boss_agent.db") as cache:
				job_id = cache.get_job_id(security_id) or ""
			if job_id:
				logger.info("从缓存命中 job_id，走 httpx 快速通道")

		result = None
		last_error: DetailError | None = None
		if job_id:
			try:
				result, last_error = _detail_via_httpx(platform, security_id, job_id, data_dir, account=account)
			except Exception as e:
				# 风控、预算与超时都是停止信号：继续降级会再发一次请求。
				if is_request_stop(e):
					raise
				logger.info(f"httpx 快速通道失败（{e}），降级到浏览器通道")
				result = None
		# 风控码以响应字典形态返回时同样终止，不换通道再取一次
		if result is None and not (last_error and last_error[0] in RISK_ERROR_BY_CODE):
			result, browser_error = _detail_via_browser(platform, security_id, lid, data_dir, job_id=job_id, account=account)
			if browser_error and (last_error is None or browser_error[0] != "NOT_SUPPORTED"):
				last_error = browser_error

	if result is None:
		if last_error:
			recoverable, recovery_action, hints = platform_error_contract(last_error[0])
			handle_error_output(
				ctx, "detail",
				code=last_error[0],
				message=platform_error_message(last_error[0], last_error[1], "职位详情获取失败"),
				recoverable=recoverable,
				recovery_action=recovery_action,
				details=last_error[2],
				hints=hints,
			)
			return
		handle_error_output(
			ctx, "detail",
			code="JOB_NOT_FOUND",
			message="职位不存在或已下架",
			# 职位没了不等于线索断了：Agent 要能直接换关键词重搜，而不是停在
			# 「下架」上（与 show 的同类提示保持一致）。
			hints={"next_actions": [_hint_command(ctx, "search <关键词>"), _hint_command(ctx, "recommend")]},
		)
		return

	# TTY 的 next 行可以写「A 或 B」，但 hints.next_actions 的每条都必须能直接执行。
	next_action = (
		f"{_hint_command(ctx, 'apply <security_id> <job_id>')} 或 "
		f"{_hint_command(ctx, 'greet <security_id> <job_id>')}"
	)
	hints = {
		"next_actions": [
			_hint_command(ctx, "apply <security_id> <job_id>"),
			_hint_command(ctx, "greet <security_id> <job_id>"),
			_hint_command(ctx, "search <query>"),
		],
	}
	handle_output(
		ctx,
		"detail",
		result,
		render=lambda data: render_job_detail(data, greet_command=next_action),
		hints=hints,
	)


def _detail_via_httpx(
	platform: Platform, security_id: str, job_id: str, data_dir: Path, *, account: str = "",
) -> tuple[dict[str, Any] | None, DetailError | None]:
	"""快速通道：通过 httpx 获取职位详情（不需要浏览器）"""
	raw = platform.job_detail(job_id)
	if not platform.is_success(raw):
		code, message = platform.parse_error(raw)
		return None, (code, message or "职位详情获取失败", _platform_error_details(raw))
	platform_data = platform.unwrap_data(raw) or {}
	job_info = field_dict(platform_data, "jobInfo")
	boss_info = field_dict(platform_data, "bossInfo")
	brand_info = field_dict(platform_data, "brandComInfo")

	if not job_info:
		return None, None

	with CacheStore(data_dir / "cache" / "boss_agent.db") as cache:
		greeted = cache.is_greeted(security_id, job_id=job_id, account=account)

	raw_job_type = job_info.get("jobType")
	return {
		"job_id": job_id,
		"title": field_text(job_info, "jobName"),
		"company": field_text(brand_info, "brandName"),
		"salary": field_text(job_info, "salaryDesc"),
		"city": field_text(job_info, "cityName"),
		"experience": field_text(job_info, "experienceName"),
		"education": field_text(job_info, "degreeName"),
		"description": field_text(platform_data, "jobDetail") or field_text(job_info, "postDescription"),
		"address": field_text(job_info, "address"),
		"skills": field_list(job_info, "jobLabels") or field_list(job_info, "skills"),
		"boss_name": field_text(boss_info, "name"),
		"boss_title": field_text(boss_info, "title"),
		"boss_active": field_text(boss_info, "activeTimeDesc", "离线"),
		"security_id": security_id,
		"raw_job_type": raw_job_type,
		"employment_type": employment_type_from_raw(raw_job_type),
		"days_per_week": field_text(job_info, "daysPerWeekDesc"),
		"least_month": field_text(job_info, "leastMonthDesc"),
		"pay_type": field_text(job_info, "payTypeDesc"),
		"greeted": greeted,
	}, None


def _detail_via_browser(
	platform: Platform, security_id: str, lid: str, data_dir: Path, job_id: str = "", *, account: str = "",
) -> tuple[dict[str, Any] | None, DetailError | None]:
	"""兜底通道：通过浏览器 job_card 获取职位详情"""
	try:
		raw = platform.job_card(security_id, lid)
	except NotImplementedError as exc:
		return None, ("NOT_SUPPORTED", str(exc) or "当前平台不支持职位详情兜底能力", None)
	if not platform.is_success(raw):
		code, message = platform.parse_error(raw)
		return None, (code, message or "职位详情获取失败", _platform_error_details(raw))
	platform_data = platform.unwrap_data(raw) or {}
	card = field_dict(platform_data, "jobCard")
	if not card:
		return None, None

	with CacheStore(data_dir / "cache" / "boss_agent.db") as cache:
		greeted = cache.is_greeted(security_id, job_id=job_id, account=account)

	return build_job_from_card(card, security_id=security_id, greeted=greeted, lid=lid), None
