from pathlib import Path
from typing import Any

import click

from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.commands._platform import get_platform_instance
from boss_agent_cli.display import boss_command_for_ctx, handle_auth_errors, handle_error_output, handle_output, render_job_detail
from boss_agent_cli.platforms import Platform


@click.command("detail")
@click.argument("security_id")
@click.option("--lid", default="", help="列表项 ID（从 search 结果获取，可选）")
@click.option("--job-id", default="", help="职位加密 ID（提供时走 httpx 快速通道，跳过浏览器）")
@click.pass_context
@handle_auth_errors("detail")
def detail_cmd(ctx: click.Context, security_id: str, lid: str, job_id: str) -> None:
	"""查看职位完整信息（职位描述、地址、招聘者信息）"""
	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]

	auth = AuthManager(data_dir, logger=logger, platform=ctx.obj.get("platform", "zhipin"))
	with get_platform_instance(ctx, auth) as platform:
		# 优先走 httpx 快速通道：显式传入 > 缓存查找 > 降级浏览器通道
		if not job_id:
			with CacheStore(data_dir / "cache" / "boss_agent.db") as cache:
				job_id = cache.get_job_id(security_id) or ""
			if job_id:
				logger.info("从缓存命中 job_id，走 httpx 快速通道")

		result = None
		last_error: tuple[str, str] | None = None
		if job_id:
			try:
				result, last_error = _detail_via_httpx(platform, security_id, job_id, data_dir)
			except Exception as e:
				logger.info(f"httpx 快速通道失败（{e}），降级到浏览器通道")
				result = None
		if result is None:
			result, browser_error = _detail_via_browser(platform, security_id, lid, data_dir)
			last_error = browser_error or last_error

	if result is None:
		if last_error:
			handle_error_output(
				ctx, "detail",
				code=last_error[0],
				message=last_error[1],
				recoverable=False,
			)
			return
		handle_error_output(
			ctx, "detail",
			code="JOB_NOT_FOUND",
			message="职位不存在或已下架",
		)
		return

	greet_target = boss_command_for_ctx(ctx, f"greet {security_id} {result['job_id']}")
	search_target = boss_command_for_ctx(ctx, "search <query>")
	hints = {"next_actions": [greet_target, search_target]}
	handle_output(
		ctx,
		"detail",
		result,
		render=lambda data: render_job_detail(data, greet_command=greet_target),
		hints=hints,
	)


def _detail_via_httpx(platform: Platform, security_id: str, job_id: str, data_dir: Path) -> tuple[dict[str, Any] | None, tuple[str, str] | None]:
	"""快速通道：通过 httpx 获取职位详情（不需要浏览器）"""
	raw = platform.job_detail(job_id)
	if not platform.is_success(raw):
		code, message = platform.parse_error(raw)
		return None, (code, message or "职位详情获取失败")
	platform_data = platform.unwrap_data(raw) or {}
	job_info = platform_data.get("jobInfo", {})
	boss_info = platform_data.get("bossInfo", {})
	brand_info = platform_data.get("brandComInfo", {})

	if not job_info:
		return None, None

	with CacheStore(data_dir / "cache" / "boss_agent.db") as cache:
		greeted = cache.is_greeted(security_id)

	return {
		"job_id": job_id,
		"title": job_info.get("jobName", ""),
		"company": brand_info.get("brandName", ""),
		"salary": job_info.get("salaryDesc", ""),
		"city": job_info.get("cityName", ""),
		"experience": job_info.get("experienceName", ""),
		"education": job_info.get("degreeName", ""),
		"description": platform_data.get("jobDetail", "") or job_info.get("postDescription", ""),
		"address": job_info.get("address", ""),
		"skills": job_info.get("jobLabels", []) or job_info.get("skills", []),
		"boss_name": boss_info.get("name", ""),
		"boss_title": boss_info.get("title", ""),
		"boss_active": boss_info.get("activeTimeDesc", "离线"),
		"security_id": security_id,
		"greeted": greeted,
	}, None


def _detail_via_browser(platform: Platform, security_id: str, lid: str, data_dir: Path) -> tuple[dict[str, Any] | None, tuple[str, str] | None]:
	"""兜底通道：通过浏览器 job_card 获取职位详情"""
	raw = platform.job_card(security_id, lid)
	if not platform.is_success(raw):
		code, message = platform.parse_error(raw)
		return None, (code, message or "职位详情获取失败")
	platform_data = platform.unwrap_data(raw) or {}
	card = platform_data.get("jobCard", {})
	if not card:
		return None, None

	job_id = card.get("encryptJobId", "")

	with CacheStore(data_dir / "cache" / "boss_agent.db") as cache:
		greeted = cache.is_greeted(security_id)

	return {
		"job_id": job_id,
		"title": card.get("jobName", ""),
		"company": card.get("brandName", ""),
		"salary": card.get("salaryDesc", ""),
		"city": card.get("cityName", ""),
		"experience": card.get("experienceName", ""),
		"education": card.get("degreeName", ""),
		"description": card.get("postDescription", ""),
		"address": card.get("address", ""),
		"skills": card.get("jobLabels", []),
		"boss_name": card.get("bossName", ""),
		"boss_title": card.get("bossTitle", ""),
		"boss_active": card.get("activeTimeDesc", "离线"),
		"security_id": security_id,
		"greeted": greeted,
	}, None
