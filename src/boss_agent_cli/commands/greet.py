import random
import time
from typing import Any

import click

from boss_agent_cli.api.client import RISK_ERROR_BY_CODE, PlatformRiskError
from boss_agent_cli.api.endpoints import (
	INDUSTRY_CODES,
	JOB_TYPE_CODES,
	SCALE_CODES,
	STAGE_CODES,
)
from boss_agent_cli.api.models import JobItem, field_list
from boss_agent_cli.api.request_budget import is_request_stop
from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.compliance import require_compliance_allowed
from boss_agent_cli.commands._platform import account_scope, get_platform_instance
from boss_agent_cli.display import (
	boss_command_for_ctx,
	error_contract_for_code,
	handle_auth_errors,
	handle_error_output,
	handle_output,
	handle_platform_error_output,
	render_batch_operation_summary,
	render_message_panel,
	risk_error_contract,
	risk_error_message,
	shell_argument,
)
from boss_agent_cli.search_filters import (
	SearchFilterCriteria,
	ensure_supported_city,
	prefilter_platform_job_type,
	resolve_search_code_params,
)
from boss_agent_cli.commands._filter_errors import emit_filter_error
from boss_agent_cli.services.job_identity import deduplicate_job_result


def _hint_command(ctx: click.Context, command: str) -> str:
	"""招呼完成或被本地去重拦截后，继续在原目录中操作。"""
	return boss_command_for_ctx(ctx, command, include_data_dir=True)


@click.command("greet")
@click.argument("security_id")
@click.argument("job_id")
@click.option("--message", default="", help="自定义打招呼消息")
@click.pass_context
@handle_auth_errors("greet")
def greet_cmd(ctx: click.Context, security_id: str, job_id: str, message: str) -> None:
	"""向指定招聘者打招呼"""
	if not require_compliance_allowed(ctx, "greet"):
		return

	# 空 ID 无法定位任何职位或招聘者：继续下去只会先查一次本地缓存、再带着空参数
	# 发平台请求，错误看起来像网络或登录问题，真正的原因（argv 拼错）被掩盖。
	if not security_id.strip() or not job_id.strip():
		search_cmd = _hint_command(ctx, "search <关键词>")
		handle_error_output(
			ctx,
			"greet",
			code="INVALID_PARAM",
			message=f"security_id 与 job_id 不能为空；先用 {search_cmd} 取到完整职位参数再打招呼",
			recovery_action=f"补上两个编号后重试；完整职位参数可从 {search_cmd} 的结果中取到",
			hints={"next_actions": [search_cmd]},
		)
		return

	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]

	auth = AuthManager(data_dir, logger=logger, platform=ctx.obj.get("platform", "zhipin"))
	account = account_scope(auth)

	with CacheStore(data_dir / "cache" / "boss_agent.db") as cache:
		if cache.is_greeted(security_id, job_id=job_id, account=account):
			chat_cmd = _hint_command(ctx, "chat")
			search_cmd = _hint_command(ctx, "search <query>")
			handle_error_output(
				ctx, "greet",
				code="ALREADY_GREETED",
				message="已向该招聘者打过招呼",
				recovery_action=f"该招聘者已打过招呼：用 {chat_cmd} 查看会话，或用 {search_cmd} 换一批职位",
				hints={"next_actions": [search_cmd]},
			)
			return

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
					recoverable, recovery_action = error_contract_for_code("HOOK_BLOCKED")
					handle_error_output(
						ctx, "greet", code="HOOK_BLOCKED",
						message=f"打招呼被钩子阻止: {veto}",
						recoverable=recoverable,
						recovery_action=recovery_action,
					)
					return

			resp = platform.greet(security_id, job_id, message)
			if not platform.is_success(resp):
				error_code, _ = platform.parse_error(resp)
				if error_code == "ACTION_UNCONFIRMED":
					# 求职者侧结果未知：登记表的恢复文案是给招聘者的
					# `boss hr chatmsg`，这里换成核对自己的会话（Issue #990）。
					chat_cmd = _hint_command(ctx, "chat")
					handle_error_output(
						ctx, "greet",
						code="ACTION_UNCONFIRMED",
						message="请求结果未确认；消息可能已发出，请先核对，勿直接重试",
						recoverable=False,
						recovery_action=f"动作可能已生效：先用 {chat_cmd} 核对会话，不要直接重试",
						details={
							"outcome_unknown": True,
							**_cli_error_details(resp),
						},
						hints={
							"next_actions": [chat_cmd],
							"operator_actions": ["先到 BOSS 直聘会话列表核对这条招呼是否已送达；不要自动重发"],
						},
					)
					return
				# 风控码必须走 RISK_ERROR_CONTRACTS：不可恢复、不提示重试，
				# 与 apply / batch-greet 的风险终止语义保持一致。UNKNOWN 也按登记表
				# 的「先核对、勿重试」输出，不能再升级成可重试的 NETWORK_ERROR
				# （Issue #990）。
				handle_platform_error_output(
					ctx,
					"greet",
					platform,
					resp,
					fallback_message="打招呼失败",
				)
				return

			try:
				cache.record_greet(security_id, job_id, account=account)
			except Exception:
				# 平台已确认招呼发出，本地记账失败不能降级成 NETWORK_ERROR「重试」：
				# 重跑会绕过 is_greeted 检查，把同一个人再打一次招呼。
				recoverable, recovery_action = error_contract_for_code("LOCAL_STATE_WRITE_FAILED")
				handle_error_output(
					ctx, "greet",
					code="LOCAL_STATE_WRITE_FAILED",
					message="已向该招聘者打招呼，但本地状态保存失败；不要自动重发",
					recoverable=recoverable,
					recovery_action=recovery_action,
					details={"security_id": security_id, "job_id": job_id, "greeted": True, "recorded": False},
					hints={"operator_actions": ["先在 BOSS 直聘官方页面核对是否已打过招呼；不要自动重发本次招呼"]},
				)
				return

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
					_hint_command(ctx, "search <query>"),
					_hint_command(ctx, "recommend"),
				],
			}
			handle_output(
				ctx, "greet", data,
				render=lambda d: render_message_panel(d, title="greet"),
				hints=hints,
			)


def _explicit_global_options(ctx: click.Context) -> list[str]:
	"""重建本次调用显式给出的全局选项，供预演后继命令复用。

	数据目录总是保留：省略时会读默认目录的登录态和去重缓存，预览结论不再成立。
	浏览器通道、CDP 地址和节流区间只在命令行显式给出时保留；来自 config.json
	的值会随同一数据目录被重新读取，不需要重复下发。
	"""
	options = ["--data-dir", str(ctx.obj["data_dir"])]
	root = ctx.find_root()
	raw = root.params if root is not None else {}
	for flag, key in (("--browser-source", "browser_source"), ("--cdp-url", "cdp_url"), ("--delay", "delay")):
		value = raw.get(key)
		if value:
			options.extend([flag, str(value)])
	return options


def _cli_error_details(response: Any) -> dict[str, Any]:
	"""提取平台层挂在响应上的 ``__cli_error_details__``（如 ``replay_safe=false``）。

	未知结果的信封必须保留「操作可能已生效」的语义字段，否则 Agent 只看到一个
	字符串错误码，会当成普通失败重试（Issue #984）。
	"""
	if not isinstance(response, dict):
		return {}
	details = response.get("__cli_error_details__")
	if not isinstance(details, dict):
		return {}
	return {str(key): value for key, value in details.items() if not str(key).startswith("__")}


def _batch_greet_dry_run_rerun(ctx: click.Context, query: str) -> str:
	"""dry-run 之后真正要执行的那条命令：与本次调用同参数，只去掉 --dry-run。"""
	params = ctx.params or {}
	argv = boss_command_for_ctx(ctx, "batch-greet").split(" ")
	argv = [*argv[:-1], *_explicit_global_options(ctx), argv[-1], query]
	for flag, key in (
		("--city", "city"),
		("--salary", "salary"),
		("--experience", "experience"),
		("--education", "education"),
		("--industry", "industry"),
		("--scale", "scale"),
		("--stage", "stage"),
		("--job-type", "job_type"),
	):
		value = params.get(key)
		if value:
			argv.extend([flag, str(value)])
	count = params.get("count")
	if count is not None:
		argv.extend(["--count", str(count)])
	return " ".join(shell_argument(part) for part in argv)


@click.command("batch-greet")
@click.argument("query")
@click.option("--city", default=None, help="城市名称")
@click.option("--salary", default=None, help="薪资范围")
@click.option("--experience", default=None, help="经验要求（如 3-5年）")
@click.option("--education", default=None, help="学历要求（如 本科）")
@click.option("--industry", default=None, type=click.Choice(list(INDUSTRY_CODES.keys()), case_sensitive=False), metavar="INDUSTRY", help="行业类型（可选值见 boss schema）")
@click.option("--scale", default=None, type=click.Choice(list(SCALE_CODES.keys()), case_sensitive=False), help="公司规模（如 100-499人）")
@click.option("--stage", default=None, type=click.Choice(list(STAGE_CODES.keys()), case_sensitive=False), help="融资阶段（如 已上市、A轮）")
@click.option("--job-type", default=None, type=click.Choice(list(JOB_TYPE_CODES.keys()), case_sensitive=False), help="职位类型（全职/兼职/实习）")
@click.option("--count", default=10, type=click.IntRange(0), help="打招呼数量上限（最大 10）")
@click.option("--dry-run", is_flag=True, default=False, help="仅模拟执行，不实际打招呼")
@click.pass_context
@handle_auth_errors("batch-greet")
def batch_greet_cmd(ctx: click.Context, query: str, city: str | None, salary: str | None, experience: str | None, education: str | None, industry: str | None, scale: str | None, stage: str | None, job_type: str | None, count: int, dry_run: bool) -> None:
	"""搜索后批量打招呼（上限 10）"""
	if not require_compliance_allowed(ctx, "batch-greet"):
		return

	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]

	count = min(count, 10)

	# 筛选条件必须在搜索之前校验：非法城市会抛 ValueError 被兜成可重试的
	# NETWORK_ERROR；未知薪资 / 经验 / 学历会被静默丢弃，等于不筛选就直接打招呼。
	try:
		ensure_supported_city(city)
	except ValueError as exc:
		emit_filter_error(ctx, "batch-greet", exc)
		return
	try:
		resolve_search_code_params(
			salary=salary, experience=experience, education=education,
			industry=industry, scale=scale, stage=stage, job_type=job_type,
		)
	except ValueError as exc:
		emit_filter_error(ctx, "batch-greet", exc)
		return

	auth = AuthManager(data_dir, logger=logger, platform=ctx.obj.get("platform", "zhipin"))
	account = account_scope(auth)

	with CacheStore(data_dir / "cache" / "boss_agent.db") as cache:
		with get_platform_instance(ctx, auth) as platform:
			raw = platform.search_jobs(
				query, city=city, salary=salary, experience=experience,
				education=education, industry=industry, scale=scale,
				stage=stage, job_type=job_type,
			)
			if not platform.is_success(raw):
				# 搜索阶段就命中风控时同样要走风险契约：不可恢复、不提示重试。
				handle_platform_error_output(
					ctx,
					"batch-greet",
					platform,
					raw,
					fallback_message="搜索结果获取失败",
				)
				return
			platform_data = platform.unwrap_data(raw) or {}
			job_list = field_list(platform_data, "jobList")
			criteria = SearchFilterCriteria(
				query=query,
				city=city,
				salary=salary,
				experience=experience,
				education=education,
				industry=industry,
				scale=scale,
				stage=stage,
				job_type=job_type,
			)

			candidates: list[JobItem] = []
			for raw_item in deduplicate_job_result(job_list):
				# 上限必须在下一次收集之前判断：放在 append 之后时 --count 0 仍会
				# 收进 1 个候选并真实发出招呼（写操作）。
				if len(candidates) >= count:
					break
				matches, _ = prefilter_platform_job_type(raw_item, criteria, platform_name=platform.name)
				if not matches:
					continue
				item = JobItem.from_api(raw_item)
				if not cache.is_greeted(item.security_id, job_id=item.job_id, account=account):
					candidates.append(item)

			if dry_run:
				items = [item.to_dict() for item in candidates]
				# dry-run 的意义是「先看候选、再决定发不发」；不给如何去重的命令，
				# Agent 预览完 30 个候选人就断了，不知道去掉 --dry-run 就能真发。
				handle_output(
					ctx, "batch-greet", {
						"dry_run": True,
						"candidates": items,
						"count": len(items),
					},
					render=lambda d: render_batch_operation_summary(d, title="batch-greet"),
					hints={
						"next_actions": [_batch_greet_dry_run_rerun(ctx, query)],
						"operator_actions": ["确认候选名单后去掉 --dry-run 重新执行，才会真实发出招呼"],
					},
				)
				return

			results = []
			stopped_reason = None
			risk_error: PlatformRiskError | None = None
			stop_error: Exception | None = None
			unconfirmed: dict[str, Any] | None = None
			cache_write_failed = False

			for idx, item in enumerate(candidates):
				retry_count = 0
				success = False

				while retry_count <= 1:
					try:
						resp = platform.greet(item.security_id, item.job_id)
						if not platform.is_success(resp):
							error_code, error_message = platform.parse_error(resp)
							risk_cls = RISK_ERROR_BY_CODE.get(error_code)
							if risk_cls is not None:
								# 响应字典形态的风控与异常形态走同一条终止分支，
								# 不再靠 message 里的「频率」字样误判成 RATE_LIMITED（Issue #419）。
								raise risk_cls(error_message or error_code)
							if error_code == "ACTION_UNCONFIRMED":
								# 请求已发出、结果未知（`__cli_error_details__.replay_safe=false`）：
								# 重试等于在不知道是否已送达时再发一次招呼。立即停批，
								# 把阻止重放的详情原样带回（Issue #984）。
								unconfirmed = {
									**item.to_dict(),
									"status": "unconfirmed",
									"error": error_code,
									"details": _cli_error_details(resp),
								}
								results.append(unconfirmed)
								stopped_reason = error_code
								logger.warning(
									f"招呼结果未确认，停止批量打招呼: {item.title} @ {item.company}"
								)
								break
							if error_code == "UNKNOWN":
								# 未识别的响应同样无法证明招呼没发出去：sleep 后重发就是
								# 在不确定的情况下重复写操作（Issue #990）。
								unconfirmed = {
									**item.to_dict(),
									"status": "unconfirmed",
									"error": error_code,
									"details": {"outcome_unknown": True, "replay_safe": False, **_cli_error_details(resp)},
								}
								results.append(unconfirmed)
								stopped_reason = "UNKNOWN"
								logger.warning(
									f"招呼结果未确认（平台响应未识别），停止批量打招呼: {item.title} @ {item.company}"
								)
								break
							raise RuntimeError(error_code)
						entry: dict[str, Any] = {
							"security_id": item.security_id,
							"job_id": item.job_id,
							"title": item.title,
							"company": item.company,
							"status": "success",
						}
						try:
							cache.record_greet(item.security_id, item.job_id, account=account)
						except Exception:
							# 记账失败不能落进下面的通用失败分支：那会 sleep 后重发同一个招呼。
							# 标记后停止批次，由收尾逻辑输出不可重试的本地状态错误。
							entry["recorded"] = False
							cache_write_failed = True
							logger.warning(f"本地记账失败，停止批量打招呼: {item.title} @ {item.company}")
						results.append(entry)
						success = True
						logger.info(f"打招呼成功: {item.title} @ {item.company}")
						break
					except PlatformRiskError as exc:
						# 风控：立即停批，不重试、不 sleep、不继续下一个候选人。
						risk_error = exc
						stopped_reason = exc.code
						logger.warning(f"平台风控，停止批量打招呼: {item.title} @ {item.company}")
						break
					except Exception as e:
						if is_request_stop(e):
							# 预算/超时不是普通失败：不 sleep、不重试、不继续下一个候选人。
							stop_error = e
							stopped_reason = getattr(e, "code", None) or "REQUEST_BUDGET_EXHAUSTED"
							logger.warning(f"请求预算耗尽，停止批量打招呼: {item.title} @ {item.company}")
							break
						error_msg = str(e)
						if "ACTION_UNCONFIRMED" in error_msg:
							# 异常形态的未知结果（平台层直接 raise 时）同样不能重试（Issue #984）。
							unconfirmed = {
								**item.to_dict(),
								"status": "unconfirmed",
								"error": "ACTION_UNCONFIRMED",
								"details": {"outcome": "unknown", "replay_safe": False},
							}
							results.append(unconfirmed)
							stopped_reason = "ACTION_UNCONFIRMED"
							logger.warning(f"招呼结果未确认，停止批量打招呼: {item.title} @ {item.company}")
							break
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

				if cache_write_failed:
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

			if cache_write_failed:
				# 已发出的招呼不能因为本地记账失败被重发；先停下来让操作者核对。
				data["stopped_reason"] = "LOCAL_STATE_WRITE_FAILED"
				recoverable, recovery_action = error_contract_for_code("LOCAL_STATE_WRITE_FAILED")
				handle_error_output(
					ctx, "batch-greet",
					code="LOCAL_STATE_WRITE_FAILED",
					message="部分招呼已发送，但本地状态保存失败；已停止剩余候选人，不要自动重发",
					recoverable=recoverable,
					recovery_action=recovery_action,
					details=data,
					hints={
						"operator_actions": [
							"先在 BOSS 直聘官方页面核对已发送的招呼；不要自动重发本次批次",
							f"确认磁盘空间与缓存目录权限后，再用 {_hint_command(ctx, 'chat')} 核对会话",
						]
					},
				)
				return

			if stop_error is not None:
				# 预算/时限停止必须回 ok:false，不能让 Agent 把 stopped_reason 当成「稍后重试」。
				stop_code = getattr(stop_error, "code", None) or "REQUEST_BUDGET_EXHAUSTED"
				recoverable, recovery_action = error_contract_for_code(stop_code)
				handle_error_output(
					ctx, "batch-greet",
					code=stop_code,
					message=str(stop_error),
					recoverable=recoverable,
					recovery_action=recovery_action,
					details=data,
				)
				return

			if risk_error is not None:
				# 停批原因是风控时信封必须是 ok:false + 对应错误码 + recoverable=false，
				# 而不是 ok:true + stopped_reason——后者会让 Agent 当成「等一会儿重试」。
				# 已成功的部分随 details 带回，避免 Agent 对同一批候选人重复打招呼。
				recovery_action, hints = risk_error_contract(risk_error.code, ctx=ctx)
				handle_error_output(
					ctx, "batch-greet",
					code=risk_error.code,
					message=risk_error_message(risk_error.code),
					recoverable=False,
					recovery_action=recovery_action,
					details=data,
					hints=hints or None,
				)
				return

			if unconfirmed is not None:
				# 结果未知的招呼不能以 ok:true 回报：Agent 会当成普通失败重发（Issue #984）。
				# UNKNOWN 与 ACTION_UNCONFIRMED 同义——响应无法证明招呼没发出去（Issue #990）。
				unconfirmed_code = str(unconfirmed.get("error") or "ACTION_UNCONFIRMED")
				recoverable, _ = error_contract_for_code(unconfirmed_code)
				chat_cmd = _hint_command(ctx, "chat")
				recovery_action = f"动作可能已生效：先用 {chat_cmd} 核对会话，不要自动重试"
				handle_error_output(
					ctx, "batch-greet",
					code=unconfirmed_code,
					message="部分招呼的发送结果未确认；已停止剩余候选人，不要自动重发",
					recoverable=recoverable,
					recovery_action=recovery_action,
					details={
						**data,
						"outcome_unknown": True,
						"replay_safe": False,
						# 未知项的完整详情（含平台层给出的 replay_safe / outcome）随批次带回。
						"unconfirmed": unconfirmed,
					},
					hints={
						"next_actions": [_hint_command(ctx, "chat")],
						"operator_actions": [
							"先在 BOSS 直聘官方会话列表核对这条招呼是否已送达；不要自动重发",
						],
					},
				)
				return

			handle_output(
				ctx, "batch-greet", data,
				render=lambda d: render_batch_operation_summary(d, title="batch-greet"),
				# 招呼已经发出，下一步是跟进会话；`me` 没有 greet 分区，不能提示去那里看。
				hints={
					"next_actions": [_hint_command(ctx, "chat")],
					"operator_actions": ["到 BOSS 直聘会话列表跟进已打招呼的招聘者"],
				},
			)
