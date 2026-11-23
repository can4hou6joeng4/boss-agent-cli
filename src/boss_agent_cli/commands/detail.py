from pathlib import Path
from typing import Any

import click

from boss_agent_cli.api._base_client import RequestTextEncodingError, parameter_status_code
from boss_agent_cli.api.client import PlatformRateLimitError, RISK_ERROR_BY_CODE
from boss_agent_cli.api.request_budget import is_request_stop
from boss_agent_cli.api.models import employment_type_from_raw, field_dict, field_text
from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.cache.store import CacheBusyError, CacheDatabaseInvalid, CacheStore, CacheWriteRejected
from boss_agent_cli.commands._platform import account_scope, get_platform_instance
from boss_agent_cli.commands._text_guard import reject_unencodable_params
from boss_agent_cli.display import (
	boss_command_for_ctx,
	handle_auth_errors,
	handle_error_output,
	handle_output,
	platform_error_contract,
	platform_error_message,
	read_only_platform_code,
	render_job_detail,
)
from boss_agent_cli.platforms import Platform
from boss_agent_cli.services.job_card import build_job_from_card, scalar_or_none, text_items
from boss_agent_cli.services.text_files import is_utf8_encodable

DetailError = tuple[str, str, dict[str, Any] | None]

# 响应字典形态的停止码：风控码与限流都不能换通道再取一次（Issue #2548）。
_DETAIL_STOP_CODES = frozenset(RISK_ERROR_BY_CODE) | {"RATE_LIMITED"}


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


def _handle_parameter_status(ctx: click.Context, status: int) -> None:
	"""参数类 4xx 是确定性失败：换通道与重试都会复现（Issue #2548）。"""
	handle_error_output(
		ctx,
		"detail",
		code="INVALID_PARAM",
		message=f"平台拒绝了请求参数（HTTP {status}），换通道或重试不会改变结果",
		recovery_action=f"核对 security_id / lid / job-id 后重试；编号可从 {_hint_command(ctx, 'search <关键词>')} 重新获取",
		hints={"next_actions": [_hint_command(ctx, "search <关键词>")]},
	)


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
	# 不可编码的标识在 httpx 构造请求时才会失败，而 detail 会把该失败当作
	# 「快速通道不可用」降级到浏览器通道；入口先按参数错误拒绝（Issue #2533）。
	if reject_unencodable_params(ctx, "detail", security_id=security_id, job_id=job_id, lid=lid):
		return
	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]

	auth = AuthManager(data_dir, logger=logger, platform=ctx.obj.get("platform", "zhipin"))
	account = account_scope(auth)
	with get_platform_instance(ctx, auth) as platform:
		# 优先走 httpx 快速通道：显式传入 > 缓存查找 > 降级浏览器通道
		# 纯空白的 --job-id 按没传处理；缓存里非字符串的 job_id 按未命中处理
		# （Issue #1817）。
		if not job_id.strip():
			with CacheStore(data_dir / "cache" / "boss_agent.db") as cache:
				cached_job_id = cache.get_job_id(security_id)
			job_id = cached_job_id if isinstance(cached_job_id, str) and cached_job_id.strip() else ""
			# 缓存里的 job_id 可能是坏的历史值（surrogatepass 还原出的孤立代理项）：
			# 它来自本地而不是用户输入，按未命中处理并继续用 security_id 走卡片
			# 通道；显式 --job-id 仍由入口按参数错误拒绝（Issue #2549）。
			if job_id and not is_utf8_encodable(job_id):
				logger.info("缓存中的 job_id 无法按 UTF-8 编码，按未命中处理")
				job_id = ""
			if job_id:
				logger.info("从缓存命中 job_id，走 httpx 快速通道")

		result = None
		last_error: DetailError | None = None
		if job_id:
			try:
				result, last_error = _detail_via_httpx(platform, security_id, job_id, data_dir, account=account)
			except Exception as e:
				# 风控、预算与超时都是停止信号：继续降级会再发一次请求。
				# 请求参数不可编码同理：换浏览器通道仍会带同一组坏参数
				# （Issue #2533）。限流耗尽后换通道会立刻再撞限流窗口
				# （Issue #2548）。
				if is_request_stop(e) or isinstance(e, (RequestTextEncodingError, PlatformRateLimitError)):
					raise
				status = parameter_status_code(e)
				if status is not None:
					_handle_parameter_status(ctx, status)
					return
				# 本地缓存异常不是「httpx 通道不可用」：换通道会再读同一把锁，
				# 也会把 CACHE_BUSY 掩盖成浏览器侧的错误（Issue #2548）。
				if isinstance(e, (CacheBusyError, CacheDatabaseInvalid, CacheWriteRejected)):
					raise
				logger.info(f"httpx 快速通道失败（{e}），降级到浏览器通道")
				result = None
		# 风控码与限流以响应字典形态返回时同样终止，不换通道再取一次
		if result is None and not (last_error and last_error[0] in _DETAIL_STOP_CODES):
			try:
				result, browser_error = _detail_via_browser(
					platform, security_id, lid, data_dir, job_id=job_id, account=account
				)
			except Exception as e:
				status = parameter_status_code(e)
				if status is not None:
					_handle_parameter_status(ctx, status)
					return
				raise
			if browser_error and (last_error is None or browser_error[0] != "NOT_SUPPORTED"):
				last_error = browser_error

	if result is None:
		if last_error:
			# detail 只读：未识别的平台响应按 NETWORK_ERROR 归一，允许安全重试，
			# 而不是沿用写操作的「核对操作是否生效」（Issue #1550）。风控码与
			# 已知错误码不受影响。
			code = read_only_platform_code("detail", last_error[0])
			recoverable, recovery_action, hints = platform_error_contract(code, ctx=ctx, read_only=True)
			handle_error_output(
				ctx, "detail",
				code=code,
				message=platform_error_message(code, last_error[1], "职位详情获取失败"),
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
		"skills": text_items(job_info, "jobLabels") or text_items(job_info, "skills"),
		"boss_name": field_text(boss_info, "name"),
		"boss_title": field_text(boss_info, "title"),
		"boss_active": field_text(boss_info, "activeTimeDesc", "离线"),
		"security_id": security_id,
		"raw_job_type": scalar_or_none(raw_job_type),
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
