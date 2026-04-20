from pathlib import Path
from typing import Any

import click

from boss_agent_cli.api.client import BossClient
from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.display import handle_auth_errors, handle_error_output, handle_output, render_job_detail


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
	delay = ctx.obj["delay"]
	cdp_url = ctx.obj.get("cdp_url")

	auth = AuthManager(data_dir, logger=logger)
	with BossClient(auth, delay=delay, cdp_url=cdp_url) as client:
		# 优先走 httpx 快速通道：显式传入 > 缓存查找 > 降级浏览器通道
		if not job_id:
			with CacheStore(data_dir / "cache" / "boss_agent.db") as cache:
				job_id = cache.get_job_id(security_id) or ""
			if job_id:
				logger.info("从缓存命中 job_id，走 httpx 快速通道")

		result = None
		if job_id:
			try:
				result = _detail_via_httpx(client, security_id, job_id, data_dir)
			except Exception as e:
				logger.info(f"httpx 快速通道失败（{e}），降级到浏览器通道")
				result = None
		if result is None:
			result = _detail_via_browser(client, security_id, lid, data_dir)

	if result is None:
		handle_error_output(
			ctx, "detail",
			code="JOB_NOT_FOUND",
			message="职位不存在或已下架",
		)
		return

	greet_target = f"boss greet {security_id} {result['job_id']}"
	hints = {"next_actions": [greet_target, "boss search <query>"]}
	handle_output(ctx, "detail", result, render=render_job_detail, hints=hints)


def _detail_via_httpx(client: BossClient, security_id: str, job_id: str, data_dir: Path) -> dict[str, Any] | None:
	"""快速通道：通过 httpx 获取职位详情（不需要浏览器）"""
	raw = client.job_detail(job_id)
	zp = raw.get("zpData", {})
	job_info = zp.get("jobInfo", {})
	boss_info = zp.get("bossInfo", {})
	brand_info = zp.get("brandComInfo", {})

	if not job_info:
		return None

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
		"description": zp.get("jobDetail", "") or job_info.get("postDescription", ""),
		"address": job_info.get("address", ""),
		"skills": job_info.get("jobLabels", []) or job_info.get("skills", []),
		"boss_name": boss_info.get("name", ""),
		"boss_title": boss_info.get("title", ""),
		"boss_active": boss_info.get("activeTimeDesc", "离线"),
		"security_id": security_id,
		"greeted": greeted,
	}


def _detail_via_browser(client: BossClient, security_id: str, lid: str, data_dir: Path) -> dict[str, Any] | None:
	"""兜底通道：通过浏览器 job_card 获取职位详情"""
	raw = client.job_card(security_id, lid)
	card = raw.get("zpData", {}).get("jobCard", {})
	if not card:
		return None

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
	}
