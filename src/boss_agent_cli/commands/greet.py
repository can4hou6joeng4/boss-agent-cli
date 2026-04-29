import random
import time

import click

from boss_agent_cli.api.endpoints import (
	INDUSTRY_CODES,
	JOB_TYPE_CODES,
	SCALE_CODES,
	STAGE_CODES,
)
from boss_agent_cli.api.models import JobItem
from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.commands._platform import get_platform_instance
from boss_agent_cli.display import (
	handle_auth_errors,
	handle_error_output,
	handle_output,
	render_batch_operation_summary,
	render_message_panel,
)


@click.command("greet")
@click.argument("security_id")
@click.argument("job_id")
@click.option("--message", default="", help="自定义打招呼消息")
@click.pass_context
@handle_auth_errors("greet")
def greet_cmd(ctx: click.Context, security_id: str, job_id: str, message: str) -> None:
	"""向指定招聘者打招呼"""
	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]

	with CacheStore(data_dir / "cache" / "boss_agent.db") as cache:
		if cache.is_greeted(security_id):
			handle_error_output(
				ctx, "greet",
				code="ALREADY_GREETED",
				message="已向该招聘者打过招呼",
				hints={"next_actions": ["boss search <query> — 搜索其他职位"]},
			)
			return

		auth = AuthManager(data_dir, logger=logger, platform=ctx.obj.get("platform", "zhipin"))
		with get_platform_instance(ctx, auth) as platform:
			# greet_before hook — allows veto
			hooks = ctx.obj.get("hooks")
			if hooks:
				veto = hooks.greet_before.call({
					"security_id": security_id,
					"job_id": job_id,
					"message": message,
					"source": "greet",
				})
				if veto:
					handle_error_output(
						ctx, "greet", code="HOOK_BLOCKED",
						message=f"打招呼被钩子阻止: {veto}",
						recoverable=True,
					)
					return

			resp = platform.greet(security_id, job_id, message)
			if not platform.is_success(resp):
				error_code, _ = platform.parse_error(resp)
				handle_error_output(
					ctx, "greet",
					code=error_code if error_code != "UNKNOWN" else "NETWORK_ERROR",
					message=resp.get("message") or "打招呼失败",
					recoverable=True,
					recovery_action="重试",
				)
				return

			cache.record_greet(security_id, job_id)

			# greet_after hook
			if hooks:
				hooks.greet_after.call({
					"security_id": security_id,
					"job_id": job_id,
					"success": True,
					"source": "greet",
				})

			data = {
				"security_id": security_id,
				"job_id": job_id,
				"message": "打招呼成功",
			}
			hints = {
				"next_actions": [
					"boss search <query> — 继续搜索其他职位",
					"boss recommend — 获取个性化推荐",
				],
			}
			handle_output(
				ctx, "greet", data,
				render=lambda d: render_message_panel(d, title="greet"),
				hints=hints,
			)


@click.command("batch-greet")
@click.argument("query")
@click.option("--city", default=None, help="城市名称")
@click.option("--salary", default=None, help="薪资范围")
@click.option("--experience", default=None, help="经验要求（如 3-5年）")
@click.option("--education", default=None, help="学历要求（如 本科）")
@click.option("--industry", default=None, type=click.Choice(list(INDUSTRY_CODES.keys()), case_sensitive=False), help="行业类型")
@click.option("--scale", default=None, type=click.Choice(list(SCALE_CODES.keys()), case_sensitive=False), help="公司规模（如 100-499人）")
@click.option("--stage", default=None, type=click.Choice(list(STAGE_CODES.keys()), case_sensitive=False), help="融资阶段（如 已上市、A轮）")
@click.option("--job-type", default=None, type=click.Choice(list(JOB_TYPE_CODES.keys()), case_sensitive=False), help="职位类型（全职/兼职/实习）")
@click.option("--count", default=10, help="打招呼数量上限（最大 10）")
@click.option("--dry-run", is_flag=True, default=False, help="仅模拟执行，不实际打招呼")
@click.pass_context
@handle_auth_errors("batch-greet")
def batch_greet_cmd(ctx: click.Context, query: str, city: str | None, salary: str | None, experience: str | None, education: str | None, industry: str | None, scale: str | None, stage: str | None, job_type: str | None, count: int, dry_run: bool) -> None:
	"""搜索后批量打招呼（上限 10）"""
	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]

	count = min(count, 10)

	with CacheStore(data_dir / "cache" / "boss_agent.db") as cache:
		auth = AuthManager(data_dir, logger=logger, platform=ctx.obj.get("platform", "zhipin"))
		with get_platform_instance(ctx, auth) as platform:
			raw = platform.search_jobs(
				query, city=city, salary=salary, experience=experience,
				education=education, industry=industry, scale=scale,
				stage=stage, job_type=job_type,
			)
			if not platform.is_success(raw):
				code, message = platform.parse_error(raw)
				handle_error_output(
					ctx, "batch-greet",
					code=code,
					message=message or "搜索结果获取失败",
					recoverable=False,
				)
				return
			platform_data = platform.unwrap_data(raw) or {}
			job_list = platform_data.get("jobList", [])

			candidates = []
			for raw_item in job_list:
				item = JobItem.from_api(raw_item)
				if not cache.is_greeted(item.security_id):
					candidates.append(item)
				if len(candidates) >= count:
					break

			if dry_run:
				items = [item.to_dict() for item in candidates]
				handle_output(
					ctx, "batch-greet", {
						"dry_run": True,
						"candidates": items,
						"count": len(items),
					},
					render=lambda d: render_batch_operation_summary(d, title="batch-greet"),
				)
				return

			results = []
			stopped_reason = None

			for idx, item in enumerate(candidates):
				retry_count = 0
				success = False

				while retry_count <= 1:
					try:
						resp = platform.greet(item.security_id, item.job_id)
						if not platform.is_success(resp):
							error_code, _ = platform.parse_error(resp)
							raise RuntimeError(error_code if error_code != "UNKNOWN" else (resp.get("message") or "greet failed"))
						cache.record_greet(item.security_id, item.job_id)
						results.append({
							"security_id": item.security_id,
							"job_id": item.job_id,
							"title": item.title,
							"company": item.company,
							"status": "success",
						})
						success = True
						logger.info(f"打招呼成功: {item.title} @ {item.company}")
						break
					except Exception as e:
						error_msg = str(e)
						if "RATE_LIMITED" in error_msg or "频率" in error_msg:
							stopped_reason = "RATE_LIMITED"
							break
						if "GREET_LIMIT" in error_msg or "上限" in error_msg:
							stopped_reason = "GREET_LIMIT"
							break
						if retry_count == 0:
							logger.warning(f"打招呼失败，重试中: {item.title}")
							retry_count += 1
							time.sleep(random.uniform(1.0, 2.0))
						else:
							results.append({
								"security_id": item.security_id,
								"job_id": item.job_id,
								"title": item.title,
								"company": item.company,
								"status": "failed",
								"error": error_msg,
							})
							break

				if stopped_reason:
					break

				if success and idx < len(candidates) - 1:
					bg_delay = ctx.obj.get("config", {}).get("batch_greet_delay", [2.0, 5.0])
					time.sleep(random.uniform(bg_delay[0], bg_delay[1]))

			data = {
				"greeted": [r for r in results if r["status"] == "success"],
				"failed": [r for r in results if r["status"] == "failed"],
				"total_greeted": sum(1 for r in results if r["status"] == "success"),
				"total_failed": sum(1 for r in results if r["status"] == "failed"),
			}
			if stopped_reason:
				data["stopped_reason"] = stopped_reason

			handle_output(
				ctx, "batch-greet", data,
				render=lambda d: render_batch_operation_summary(d, title="batch-greet"),
			)
