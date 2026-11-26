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
from boss_agent_cli.api.models import dict_items, JobItem, field_list
from boss_agent_cli.api.request_budget import is_request_stop
from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.compliance import require_compliance_allowed
from boss_agent_cli.commands._platform import account_scope, get_platform_instance
from boss_agent_cli.commands._text_guard import reject_blank_options, reject_unencodable_params
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
	positional_hint,
	shell_argument,
)
from boss_agent_cli.search_filters import (
	SearchFilterCriteria,
	ensure_supported_city,
	prefilter_platform_job_type,
	resolve_search_code_params,
)
from boss_agent_cli.commands._filter_errors import emit_filter_error
from boss_agent_cli.services.candidate_actions import (
	CandidateActionBlocked,
	CandidateActionNotRecorded,
	CandidateActionReleaseFailed,
	candidate_action_provably_unsent,
	classify_platform_response,
	cli_error_details,
	outcome_unknown_cause,
	release_candidate_action,
	reserve_candidate_action,
	run_candidate_action,
	settle_candidate_action,
)
from boss_agent_cli.services.job_identity import deduplicate_job_result


def _hint_command(ctx: click.Context, command: str) -> str:
	"""招呼完成或被本地去重拦截后，继续在原目录中操作。"""
	return boss_command_for_ctx(ctx, command, include_data_dir=True)


def _emit_already_greeted(ctx: click.Context) -> None:
	chat_cmd = _hint_command(ctx, "chat")
	search_cmd = _hint_command(ctx, "search <query>")
	handle_error_output(
		ctx, "greet",
		code="ALREADY_GREETED",
		message="已向该招聘者打过招呼",
		recovery_action=f"该招聘者已打过招呼：用 {chat_cmd} 查看会话，或用 {search_cmd} 换一批职位",
		hints={"next_actions": [search_cmd]},
	)


def _emit_greet_unconfirmed(
	ctx: click.Context, message: str, *, cause: BaseException | None = None
) -> None:
	chat_cmd = _hint_command(ctx, "chat")
	handle_error_output(
		ctx, "greet",
		code="ACTION_UNCONFIRMED",
		message=message,
		recoverable=False,
		recovery_action=f"动作可能已生效：先用 {chat_cmd} 核对会话，不要直接重试",
		details={"outcome_unknown": True, "replay_safe": False, **outcome_unknown_cause(cause)},
		hints={
			"next_actions": [chat_cmd],
			"operator_actions": ["先到 BOSS 直聘会话列表核对这条招呼是否已送达；不要自动重发"],
		},
	)


@click.command("greet")
@click.argument("security_id")
@click.argument("job_id")
@click.option("--message", default=None, help="自定义打招呼消息")
@click.pass_context
@handle_auth_errors("greet")
def greet_cmd(ctx: click.Context, security_id: str, job_id: str, message: str | None) -> None:
	"""向指定招聘者打招呼"""
	if not require_compliance_allowed(ctx, "greet"):
		return

	if message is not None and not message.strip():
		# 空串与纯空白都会被平台当成招呼正文；显式传空不能退化成默认招呼语（Issue #2603）。
		handle_error_output(
			ctx,
			"greet",
			code="INVALID_PARAM",
			message="--message 不能只有空白字符",
			recovery_action="省略 --message 使用默认招呼语，或传入非空内容后重试",
		)
		return
	# 缺省仍走默认招呼语；后续调用方只拿到 str。
	message = message or ""

	# 在读缓存或联系平台前报告缺失的职位标识。
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

	# 招呼正文与标识都会进平台请求：不可编码的字符只会在发送阶段失败，
	# 入口按参数错误拒绝（Issue #2533）。
	if reject_unencodable_params(ctx, "greet", security_id=security_id, job_id=job_id, **{"--message": message}):
		return

	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]

	auth = AuthManager(data_dir, logger=logger, platform=ctx.obj.get("platform", "zhipin"))
	account = account_scope(auth)

	with CacheStore(data_dir / "cache" / "boss_agent.db") as cache:
		if cache.is_greeted(security_id, job_id=job_id, account=account):
			_emit_already_greeted(ctx)
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

			try:
				resp = run_candidate_action(
					cache,
					security_id=security_id,
					job_id=job_id,
					action="greet",
					account=account,
					send=lambda: platform.greet(security_id, job_id, message),
					classify=lambda response: classify_platform_response(platform, response),
				)
			except CandidateActionBlocked as blocked:
				# 预检查与真实发送之间，另一个进程可能已经预约 / 完成同一动作。
				if blocked.code == "ALREADY_GREETED":
					_emit_already_greeted(ctx)
				else:
					_emit_greet_unconfirmed(ctx, blocked.message)
				return
			except CandidateActionNotRecorded:
				# 平台已确认招呼发出，本地记账失败不能降级成 NETWORK_ERROR「重试」：
				# 重跑会绕过 is_greeted / 预约检查，把同一个人再打一次招呼。
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
			except CandidateActionReleaseFailed:
				# 平台拒绝了本次招呼（未产生记录），但本地 pending 预约没删掉。
				# 直接重试会被预约拦住：报本地状态错误，提示先修缓存再重试。
				handle_error_output(
					ctx, "greet",
					code="LOCAL_STATE_WRITE_FAILED",
					message="平台未接受本次打招呼，但本地预约未释放；修复本地缓存后再重试",
					recoverable=False,
					recovery_action="先确认数据目录可写（磁盘空间、缓存目录权限），再重新执行同一条打招呼命令",
					details={"security_id": security_id, "job_id": job_id, "request_sent": False, "recorded": False},
					hints={"operator_actions": ["检查磁盘空间与 ~/.boss-agent 目录权限；本次招呼没有被平台接受，修好后可重试"]},
				)
				return
			except Exception as exc:
				# 不能证明请求未发出：预约已保留，本次调用必须与下一次调用的口径
				# 一致——报结果未知，而不是可重试的 NETWORK_ERROR。否则 Agent 按
				# 「重试」再调一次只会撞上 pending 预约（Issue #2297）。预算 / 时限
				# 停止保留各自错误码（它们带 request_sent=false，预约已释放），
				# 风控保留 RISK_ERROR_CONTRACTS 的终止语义。
				if (
					not is_request_stop(exc)
					and not isinstance(exc, PlatformRiskError)
					and not candidate_action_provably_unsent(exc)
				):
					_emit_greet_unconfirmed(
						ctx,
						"打招呼结果未确认；请求可能已生效，请先到平台核对，勿直接重试",
						cause=exc,
					)
					return
				raise

			if not platform.is_success(resp):
				error_code, _ = platform.parse_error(resp)
				if error_code in ("ACTION_UNCONFIRMED", "NETWORK_ERROR"):
					# 求职者侧结果未知：登记表的恢复文案是给招聘者的
					# `boss hr chatmsg`，这里换成核对自己的会话（Issue #990）。
					# NETWORK_ERROR 覆盖 HTTP 5xx / 超时文案：写请求可能已在
					# 平台落库，不能给「重试」指引（Issue #2316）。
					chat_cmd = _hint_command(ctx, "chat")
					handle_error_output(
						ctx, "greet",
						code="ACTION_UNCONFIRMED",
						message="请求结果未确认；消息可能已发出，请先核对，勿直接重试",
						recoverable=False,
						recovery_action=f"动作可能已生效：先用 {chat_cmd} 核对会话，不要直接重试",
						details={
							"outcome_unknown": True,
							**cli_error_details(resp),
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

			# 成功路径已由 run_candidate_action 统一 settle（旧记录表 + 预约）。

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


def _batch_greet_dry_run_rerun(ctx: click.Context, query: str) -> str:
	"""dry-run 之后真正要执行的那条命令：与本次调用同参数，只去掉 --dry-run。"""
	params = ctx.params or {}
	argv = boss_command_for_ctx(ctx, "batch-greet").split(" ")
	# 以 `-` 开头的关键词必须排在全部选项之后并用 `--` 隔开，否则照抄执行会被
	# 当成选项（Issue #1815）；普通关键词保持「关键词在前」的写法。
	dashed = query.startswith("-")
	argv = [*argv[:-1], *_explicit_global_options(ctx), argv[-1]]
	if not dashed:
		argv.append(query)
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
	rendered = " ".join(shell_argument(part) for part in argv)
	return f"{rendered} {positional_hint(query)}" if dashed else rendered


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
@click.option("--count", default=10, type=click.IntRange(0, 10), help="打招呼数量上限（最大 10）")
@click.option("--dry-run", is_flag=True, default=False, help="仅模拟执行，不实际打招呼")
@click.pass_context
@handle_auth_errors("batch-greet")
def batch_greet_cmd(ctx: click.Context, query: str, city: str | None, salary: str | None, experience: str | None, education: str | None, industry: str | None, scale: str | None, stage: str | None, job_type: str | None, count: int, dry_run: bool) -> None:
	"""搜索后批量打招呼（上限 10）"""
	if not require_compliance_allowed(ctx, "batch-greet"):
		return

	# 空串 / 纯空白关键词会让搜索退化成「没有关键词」的默认列表，随后对列表里的
	# 职位真实发出招呼：用户只是漏填参数，却产生了不可撤回的写操作。与 search
	# 同口径，在任何平台调用之前按参数错误拒绝。
	if not query.strip():
		search_cmd = _hint_command(ctx, "search <关键词>")
		handle_error_output(
			ctx,
			"batch-greet",
			code="INVALID_PARAM",
			message="搜索关键词不能为空；批量打招呼需要一个明确的关键词来限定范围",
			recovery_action=f"传入非空关键词后重试；可先用 {search_cmd} 确认搜索范围",
			hints={"next_actions": [search_cmd]},
		)
		return

	# 显式传空的筛选项不能静默退化成「不筛选」，否则会在没有限定范围的情况下
	# 对搜索结果批量发出招呼（Issue #2602）。省略选项仍保持原语义。
	if reject_blank_options(ctx, "batch-greet", city=city, salary=salary, experience=experience, education=education):
		return

	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]

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
			job_list = dict_items(field_list(platform_data, "jobList"))
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

			# 先校验写请求所需的标识，避免坏条目遮住随后同职位的有效条目（Issue #1816）。
			valid_jobs = [
				job
				for job in job_list
				if all(isinstance(job.get(key), str) and job[key].strip() for key in ("securityId", "encryptJobId"))
			]
			skipped_invalid = len(job_list) - len(valid_jobs)
			if skipped_invalid:
				logger.warning(f"跳过 {skipped_invalid} 个缺少有效 security_id / job_id 的搜索结果")

			candidates: list[JobItem] = []
			for raw_item in deduplicate_job_result(valid_jobs):
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
			# 平台未接受 / 请求未发出，但本地 pending 预约没删掉：不能用「结果未知」
			# 收尾，否则下一次执行会撞上残留预约（Issue #2964）。
			release_error: Exception | None = None
			release_failed_item: JobItem | None = None

			def _release_or_stop(item: JobItem) -> bool:
				"""释放当前候选人的预约；失败时让批次按本地状态错误收尾。"""
				nonlocal release_error, release_failed_item
				try:
					release_candidate_action(cache, item.job_id, "greet", account=account)
				except Exception as exc:
					release_error = exc
					release_failed_item = item
					return False
				return True

			# 尚未落到 results 的候选人：SIGINT 落在请求或重试等待中时，它的最后一次
			# 招呼可能已经发出，不能当成未执行（Issue #1491）。
			current_item: JobItem | None = None

			try:
				for idx, item in enumerate(candidates):
					current_item = item
					success = False

					# 发送前先在本地原子预约：两个进程并发跑同名批次时，只有抢到
					# pending 的一方真正发送（Issue #1508）。预过滤后的竞态窗口在
					# 这里收口；已有 pending 说明上一次结果未知，禁止重发。
					try:
						reserve_candidate_action(
							cache, item.security_id, item.job_id, "greet", account=account
						)
					except CandidateActionBlocked as blocked:
						if blocked.code == "ALREADY_GREETED":
							logger.info(f"另一个进程已打过招呼，跳过: {item.title} @ {item.company}")
							current_item = None
							continue
						unconfirmed = {
							**item.to_dict(),
							"status": "unconfirmed",
							"error": "ACTION_UNCONFIRMED",
							"details": {"outcome_unknown": True, "replay_safe": False},
						}
						results.append(unconfirmed)
						stopped_reason = "ACTION_UNCONFIRMED"
						logger.warning(
							f"上一次招呼结果未确认，停止批量打招呼: {item.title} @ {item.company}"
						)
						current_item = None
						break

					for attempt in range(2):
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
										"details": cli_error_details(resp),
									}
									results.append(unconfirmed)
									stopped_reason = error_code
									logger.warning(
										f"招呼结果未确认，停止批量打招呼: {item.title} @ {item.company}"
									)
									break
								if error_code == "NETWORK_ERROR":
									# HTTP 5xx / 超时文案无法证明招呼没发出去：sleep 后
									# 重发就是在不确定时重复写操作，保留预约并停批
									# （Issue #2316，与 UNKNOWN 分支同口径）。
									unconfirmed = {
										**item.to_dict(),
										"status": "unconfirmed",
										"error": error_code,
										"details": {"outcome_unknown": True, "replay_safe": False, **cli_error_details(resp)},
									}
									results.append(unconfirmed)
									stopped_reason = error_code
									logger.warning(
										f"招呼结果未确认（网络错误响应），停止批量打招呼: {item.title} @ {item.company}"
									)
									break
								if error_code == "UNKNOWN":
									# 未识别的响应同样无法证明招呼没发出去：sleep 后重发就是
									# 在不确定的情况下重复写操作（Issue #990）。
									unconfirmed = {
										**item.to_dict(),
										"status": "unconfirmed",
										"error": error_code,
										"details": {"outcome_unknown": True, "replay_safe": False, **cli_error_details(resp)},
									}
									results.append(unconfirmed)
									stopped_reason = "UNKNOWN"
									logger.warning(
										f"招呼结果未确认（平台响应未识别），停止批量打招呼: {item.title} @ {item.company}"
									)
									break
								if error_code in {"RATE_LIMITED", "GREET_LIMIT"}:
									# 触发停批的候选人同样要进结果：既不能从 greeted/failed
									# 里消失，也不能让限流批次以 ok:true 收尾（Issue #2003）。
									results.append({
										"security_id": item.security_id,
										"job_id": item.job_id,
										"title": item.title,
										"company": item.company,
										"status": "failed",
										"error": error_code,
										"details": cli_error_details(resp),
									})
									if not _release_or_stop(item):
										# 平台已明确拒绝，请求没有产生记录；释放失败只是本地
										# 状态问题，不能按「结果未知」收尾（Issue #2964）。
										stopped_reason = "LOCAL_STATE_WRITE_FAILED"
										break
									stopped_reason = error_code
									break
								error_msg = error_code
							else:
								entry: dict[str, Any] = {
									"security_id": item.security_id,
									"job_id": item.job_id,
									"title": item.title,
									"company": item.company,
									"status": "success",
								}
								try:
									settle_candidate_action(
										cache, item.security_id, item.job_id, "greet", account=account
									)
								except Exception:
									# 平台已确认发送。记账失败交给批次收尾处理。
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
								# request_sent=False / 通道不可用 / 派发前失败可以证明未发出，撤销
								# 预约；风控与超时可能已产生写入，保留 pending 按结果未知处理。
								if candidate_action_provably_unsent(e) and not _release_or_stop(item):
									# 请求可以证明未发出，但预约没删掉：下一次执行会被
									# pending 挡住，报本地状态错误而不是原始停止原因。
									stopped_reason = "LOCAL_STATE_WRITE_FAILED"
									break
								stop_error = e
								stopped_reason = getattr(e, "code", None) or "REQUEST_BUDGET_EXHAUSTED"
								logger.warning(f"请求预算耗尽，停止批量打招呼: {item.title} @ {item.company}")
								break
							error_msg = str(e)
							if not candidate_action_provably_unsent(e):
								# 请求可能已发出。保留预约并停止，等待核对送达结果（Issue #1818）。
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
							stop_code = getattr(e, "code", None)
							if stop_code in ("RATE_LIMITED", "GREET_LIMIT"):
								results.append({
									"security_id": item.security_id,
									"job_id": item.job_id,
									"title": item.title,
									"company": item.company,
									"status": "failed",
									"error": str(stop_code),
								})
								if not _release_or_stop(item):
									stopped_reason = "LOCAL_STATE_WRITE_FAILED"
									break
								stopped_reason = str(stop_code)
								break

						# 明确失败后可重试一次。保留预约，避免另一进程在等待期间发送。
						if attempt == 0:
							logger.warning(f"打招呼失败，重试中: {item.title}")
							time.sleep(random.uniform(1.0, 2.0))
							continue
						release_error_after_retry = not _release_or_stop(item)
						results.append({
							"security_id": item.security_id,
							"job_id": item.job_id,
							"title": item.title,
							"company": item.company,
							"status": "failed",
							"error": error_msg,
						})
						if release_error_after_retry:
							stopped_reason = "LOCAL_STATE_WRITE_FAILED"
							break

					current_item = None
					if stopped_reason:
						break

					if cache_write_failed:
						break

					if success and idx < len(candidates) - 1:
						bg_delay = ctx.obj.get("config", {}).get("batch_greet_delay", [2.0, 5.0])
						time.sleep(random.uniform(bg_delay[0], bg_delay[1]))

			except KeyboardInterrupt:
				# Ctrl-C / MCP 超时 SIGINT：Click 默认直接退出且 stdout 为空，MCP 只能
				# 报「结果未知」而拿不到已完成的部分。这里把结果与在途候选人放进错误
				# 信封，并明确禁止原样重放（Issue #1491）。
				completed = list(results)
				interrupted = (
					{
						**current_item.to_dict(),
						"status": "unconfirmed",
						"error": "WORKFLOW_STOPPED",
						"details": {"outcome_unknown": True, "replay_safe": False},
					}
					if current_item is not None
					else None
				)
				data: dict[str, Any] = {
					"completed": completed,
					# 与成功信封的 greeted / failed 保持同形，Agent 不必为中断路径换解析。
					"greeted": [r for r in completed if r["status"] == "success"],
					"failed": [r for r in completed if r["status"] == "failed"],
					"processed": len(completed),
					"remaining": max(0, len(candidates) - len(completed)),
					"total_greeted": sum(1 for r in completed if r["status"] == "success"),
					"total_failed": sum(1 for r in completed if r["status"] == "failed"),
					"unconfirmed": interrupted,
				}
				chat_cmd = _hint_command(ctx, "chat")
				handle_error_output(
					ctx,
					"batch-greet",
					code="WORKFLOW_STOPPED",
					message="批量打招呼被中断：已完成的部分如下，最后一个动作的结果可能未知，不要原样重试",
					recoverable=False,
					recovery_action=(
						f"先到 BOSS 直聘官方页面核对最后一个招呼是否已送达，再用 {chat_cmd} 查看会话；"
						"确认后只对未完成的候选人重新执行，不要原样重试本次批次"
					),
					details={
						**data,
						"outcome_unknown": True,
						"replay_safe": False,
					},
					hints={
						"next_actions": [chat_cmd],
						"operator_actions": [
							"先在 BOSS 直聘官方会话列表核对最后一个招呼是否已送达；不要原样重试本次批次",
						],
					},
				)
				# 不能用 ctx.exit()：click.exceptions.Exit 是 RuntimeError 子类，会被
				# @handle_auth_errors 的兜底 except Exception 再发一份信封。
				raise SystemExit(1)

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

			if release_error is not None:
				# 平台未接受本次招呼（或请求可证明未发出），但本地预约没有释放：
				# 报不可重试的本地状态错误，而不是结果未知。否则 Agent 会按「结果
				# 可能已生效」去核对，照指引重试又会撞上残留的 pending（Issue #2964）。
				data["stopped_reason"] = "LOCAL_STATE_WRITE_FAILED"
				handle_error_output(
					ctx, "batch-greet",
					code="LOCAL_STATE_WRITE_FAILED",
					message="本次招呼未产生平台记录，但本地预约未释放；已停止剩余候选人",
					recoverable=False,
					recovery_action="先确认数据目录可写（磁盘空间、缓存目录权限），再对未完成的候选人重新执行批量打招呼命令",
					details={
						**data,
						"request_sent": False,
						"recorded": False,
						"replay_safe": False,
						"unreleased": (
							release_failed_item.to_dict() if release_failed_item is not None else None
						),
						"release_error": type(release_error).__name__,
					},
					hints={
						"operator_actions": [
							"检查磁盘空间与 ~/.boss-agent 目录权限；本次招呼未被平台接受，修好后只对未完成的候选人重新执行",
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

			if stopped_reason in ("RATE_LIMITED", "GREET_LIMIT"):
				# 限流停批必须回 ok:false：单次 greet 对同一响应就是 ok:false，
				# GREET_LIMIT 还登记为不可恢复——ok:true 会让 Agent 把额度用完
				# 当成批次成功（Issue #2003）。剩余候选人未处理。
				recoverable, recovery_action = error_contract_for_code(stopped_reason)
				handle_error_output(
					ctx, "batch-greet",
					code=stopped_reason,
					message="批量打招呼被平台限流中断；剩余候选人未处理",
					recoverable=recoverable,
					recovery_action=recovery_action,
					details=data,
					hints={
						"next_actions": [_hint_command(ctx, "chat")],
						"operator_actions": [
							"剩余候选人未发送；先在官方页面确认频率限制解除，再对未完成的候选人重新执行",
						],
					},
				)
				return

			if unconfirmed is not None:
				# 结果未知的招呼不能以 ok:true 回报：Agent 会当成普通失败重发（Issue #984）。
				# UNKNOWN / NETWORK_ERROR 与 ACTION_UNCONFIRMED 同义——响应无法证明
				# 招呼没发出去（Issue #990、#2316）。NETWORK_ERROR 登记表本身标可重试，
				# 这里必须按结果未知统一覆盖为不可重试。
				unconfirmed_code = str(unconfirmed.get("error") or "ACTION_UNCONFIRMED")
				recoverable = False
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
