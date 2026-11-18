"""Reusable candidate, recruiter and local workflow actions."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Mapping, NoReturn, cast

from boss_agent_cli.api.models import dict_items, field_dict, field_list, platform_data_dict
from boss_agent_cli.config import DEFAULT_REQUEST_DELAY
from boss_agent_cli.auth.identity import account_scope
from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.api.endpoints import CITY_CODES
from boss_agent_cli.ai.config import AIConfigStore, ai_float_setting, ai_int_setting
from boss_agent_cli.ai.service import AIService, AIServiceError, recovery_action_for
from boss_agent_cli.cache.store import CacheRecordInvalid, CacheStore, SavedSearchParamsError
from boss_agent_cli.platforms.factory import build_platform_instance, build_recruiter_platform_instance
from boss_agent_cli.services.candidate_actions import (
	CandidateActionBlocked,
	CandidateActionNotRecorded,
	run_candidate_action,
)
from boss_agent_cli.services.contact_lookup import FriendLookupLimitExceeded, find_friend
from boss_agent_cli.services.friend_list_pages import collect_friend_list_page
from boss_agent_cli.services.output_path import OutputPathProblem, inspect_output_path, prepare_output_path
from boss_agent_cli.crawler.operations import crawl_status
from boss_agent_cli.crawler.service import (
	CrawlBudget,
	CrawlBusyError,
	CrawlService,
	CrawlSettings,
	crawl_cdp_port,
	crawl_chrome_path,
	crawl_int_setting,
	unused_local_port,
)
from boss_agent_cli.api.egress import EgressContradictionError
from boss_agent_cli.crawler.controls import (
	CrawlBrowserStartError,
	CrawlExportError,
	CrawlHookDirInvalid,
	CrawlProfileBusy,
	CrawlRuntimeUnavailable,
	CrawlSessionAuthError,
)
from boss_agent_cli.crawler.hooks import HookRegistrationError, verify_hook_profile
from boss_agent_cli.crawler.transport import DrissionCrawlerSession, ensure_crawl_runtime
from boss_agent_cli.services.install_extras import extras_requirement
from boss_agent_cli.digest import build_digest
from boss_agent_cli.display import (
	RISK_ERROR_CONTRACTS,
	boss_command,
	error_contract_for_code,
	risk_error_contract,
	risk_error_message,
	positional_hint,
	shell_argument,
)
from boss_agent_cli.output import Logger
from boss_agent_cli.pipeline_state import build_pipeline_items, select_follow_up_candidates
from boss_agent_cli.resume.models import resume_to_text
from boss_agent_cli.resume.store import ResumeStore
from boss_agent_cli.schema.error_codes import ERROR_CODES
from boss_agent_cli.search_filters import (
	SearchFilterCriteria,
	client_is_browser_only,
	detail_filter_max_pages,
	ensure_supported_city,
	resolve_active_level,
	resolve_search_code_params,
	resolve_welfare_keywords,
	run_search_pipeline,
)
from boss_agent_cli.wizard.models import StepResult, WorkflowInputError, WorkflowStatus
from boss_agent_cli.wizard.runner import Action, WorkflowActionError, WorkflowControl

PlatformFactory = Callable[[str, AuthManager, tuple[float, float], str | None, str | None], Any]


def _hint_command(context: ActionContext, command: str) -> str:
	"""动作层恢复命令必须定位触发它的数据目录与平台。

	wizard 的 headless / MCP 调用可以带 ``--data-dir``：恢复指引写成裸 ``boss ...``
	时，Agent 照抄会把新 AI 密钥、``config reset``、watch 删除重建或 crawl 重建
	落到默认目录，原目录的问题原样保留（与 #913 / #919 的命令层迁移同一接缝）。
	"""
	return boss_command(command, platform_name=context.platform, data_dir=context.data_dir)


@dataclass(frozen=True)
class ActionContext:
	data_dir: Path
	platform: str
	role: str
	logger: Logger
	delay: tuple[float, float] = DEFAULT_REQUEST_DELAY
	cdp_url: str | None = None
	browser_source: str | None = None
	config: Mapping[str, Any] | None = None
	candidate_factory: PlatformFactory | None = None
	recruiter_factory: PlatformFactory | None = None
	workflow_control: WorkflowControl | None = None

	def with_workflow_control(self, control: WorkflowControl) -> "ActionContext":
		return replace(self, workflow_control=control)

	def auth(self) -> AuthManager:
		return AuthManager(self.data_dir, logger=self.logger, platform=self.platform)

	def candidate_platform(self) -> Any:
		if self.candidate_factory:
			return self.candidate_factory(self.platform, self.auth(), self.delay, self.cdp_url, self.browser_source)
		return build_platform_instance(
			self.platform,
			self.auth(),
			delay=self.delay,
			cdp_url=self.cdp_url,
			browser_source=self.browser_source,
		)

	def recruiter_platform(self) -> Any:
		if self.recruiter_factory:
			return self.recruiter_factory(self.platform, self.auth(), self.delay, self.cdp_url, self.browser_source)
		return build_recruiter_platform_instance(
			self.platform,
			self.auth(),
			delay=self.delay,
			cdp_url=self.cdp_url,
			browser_source=self.browser_source,
		)


def _risk_commands(context: "ActionContext | None") -> dict[str, str]:
	"""风控契约里的解锁 / 诊断命令：指向触发本次 workflow 的目录与平台。

	风险锁写在 ``<data_dir>/.cdp_risk_lock``；裸 ``boss clean --risk-lock``
	会让 Agent 去默认目录解锁，命令不报错但原锁仍在（Issue #978）。
	"""
	if context is None:
		return {}
	return {
		"clean_command": _hint_command(context, "clean --risk-lock"),
		"doctor_command": _hint_command(context, "doctor"),
	}


def _invalid_welfare_conditions(raw: Any) -> WorkflowActionError:
	recoverable, recovery = error_contract_for_code("INVALID_PARAM")
	return WorkflowActionError(
		"INVALID_PARAM",
		'wizard 输入 welfare_conditions 必须是福利标签字符串数组（如 ["双休", "五险一金"]），'
		f"或 [[标签, [关键词...]], ...] 形状；收到 {raw!r}",
		recoverable=recoverable,
		recovery_action=recovery,
	)


def _normalize_welfare_conditions(raw: Any) -> list[tuple[str, list[str]]] | None:
	"""把 wizard 的 ``welfare_conditions`` 归一到 pipeline 的 ``(label, keywords)`` 形状。

	wizard inputs 来自 ``--input-json`` / MCP，文档示例是 ``["双休"]``；原样透传时
	``match_all_welfare`` 会按 ``for label, keywords in conditions`` 把它拆成单字
	标签（"双" / "休"）静默错筛，多条件列表则抛 ``ValueError`` 被 runner 兜成可
	重试的 ``NETWORK_ERROR``（Issue #1383）。字符串数组与既有的
	``[[label, [keywords...]], ...]`` 形状都在平台请求前归一，其余类型点名报错。
	"""
	if raw is None:
		return None
	if not isinstance(raw, (list, tuple)):
		raise _invalid_welfare_conditions(raw)
	conditions: list[tuple[str, list[str]]] = []
	for item in raw:
		if isinstance(item, str):
			label = item.strip()
			if label:
				conditions.append((label, resolve_welfare_keywords(label)))
			continue
		if (
			isinstance(item, (list, tuple))
			and len(item) == 2
			and isinstance(item[0], str)
			and isinstance(item[1], (list, tuple))
			and all(isinstance(keyword, str) for keyword in item[1])
		):
			label = item[0].strip()
			if not label:
				raise _invalid_welfare_conditions(raw)
			conditions.append((label, list(item[1])))
			continue
		raise _invalid_welfare_conditions(raw)
	return conditions


def execute_candidate_search(
	platform: Any,
	cache: CacheStore,
	logger: Logger,
	inputs: Mapping[str, Any],
	*,
	pipeline: Callable[..., Any] = run_search_pipeline,
) -> Any:
	"""Shared platform search call used by the command and workflow action."""
	welfare_conditions = inputs.get("welfare_conditions")
	if welfare_conditions is None and inputs.get("welfare"):
		raw_welfare = inputs["welfare"]
		labels = (
			[str(item).strip() for item in raw_welfare]
			if isinstance(raw_welfare, (list, tuple))
			else [item.strip() for item in str(raw_welfare).split(",")]
		)
		welfare_conditions = [(label, resolve_welfare_keywords(label)) for label in labels if label]
	else:
		welfare_conditions = _normalize_welfare_conditions(welfare_conditions)
	try:
		active = resolve_active_level(_optional_str(inputs.get("active")))
	except ValueError as exc:
		raise WorkflowActionError(
			"INVALID_PARAM", str(exc), recoverable=True, recovery_action="修正 active 取值后重试"
		) from exc
	try:
		_validate_search_filters(inputs)
	except ValueError as exc:
		raise WorkflowActionError(
			"INVALID_PARAM",
			str(exc),
			recovery_action=(
				"修正搜索筛选后重试；支持的城市用 boss cities 查看，薪资 / 经验 / 学历等可选值在错误信息里已列出"
			),
		) from exc
	criteria = SearchFilterCriteria(
		query=str(inputs.get("query") or ""),
		city=_optional_str(inputs.get("city")),
		salary=_optional_str(inputs.get("salary")),
		experience=_optional_str(inputs.get("experience")),
		education=_optional_str(inputs.get("education")),
		industry=_optional_str(inputs.get("industry")),
		scale=_optional_str(inputs.get("scale")),
		stage=_optional_str(inputs.get("stage")),
		job_type=_optional_str(inputs.get("job_type")),
		raw_params=_mapping_input(inputs, "raw_params"),
	)
	extra: dict[str, Any] = {"active": active} if active else {}
	account = str(inputs.get("account") or "")
	if account:
		extra["account"] = account
	browser_detail = inputs.get("detail_channel") == "browser" or client_is_browser_only(platform)
	if active or browser_detail:
		# 只有 --active 或 CDP 模式时才透传详情节流 / 通道，保持其他调用方（含测试替身）签名不变
		for key in ("before_detail_request", "detail_channel"):
			if inputs.get(key) is not None:
				extra[key] = inputs[key]
	if browser_detail:
		# CDP 模式：详情只走浏览器、串行，并按 CrawlBudget 间隔（wizard 等调用方没传节流时补上）
		extra["detail_channel"] = "browser"
		if (
			extra.get("before_detail_request") is None
			and isinstance(cache, CacheStore)
			and getattr(platform, "name", None) == "zhipin"
		):
			budget = CrawlBudget(cache)
			extra["before_detail_request"] = lambda: budget.wait("list")
	return pipeline(
		platform,
		cache,
		logger,
		criteria=criteria,
		start_page=_int_input(inputs, "page", 1, minimum=1),
		max_pages=_int_input(inputs, "max_pages", detail_filter_max_pages(welfare_conditions, active), minimum=1),
		welfare_conditions=welfare_conditions,
		before_list_request=inputs.get("before_list_request"),
		**extra,
	)


def execute_recruiter_candidate_search(platform: Any, inputs: Mapping[str, Any]) -> dict[str, Any]:
	"""Shared recruiter search call used by the command and workflow action."""
	# platform 按鸭子类型接收真实适配器与测试替身，返回的平台响应字典由调用方
	# 按 API 响应契约读取；这里显式收敛 Any，避免整条调用链丢失类型。
	return cast(
		"dict[str, Any]",
		platform.search_geeks(
			str(inputs.get("query") or ""),
			city=_optional_str(inputs.get("city")),
			page=_int_input(inputs, "page", 1, minimum=1),
			job_id=_optional_str(inputs.get("job_id")),
			experience=_optional_str(inputs.get("experience")),
			degree=_optional_str(inputs.get("degree")),
			age=_optional_str(inputs.get("age")),
			school_level=_optional_str(inputs.get("school_level")),
			activeness=_optional_str(inputs.get("activeness")),
			source=_optional_str(inputs.get("source")),
			select=_bool_input(inputs, "select"),
			salary=_optional_str(inputs.get("salary")),
		),
	)


def _extract_list_items(payload: Any, keys: tuple[str, ...] = ()) -> list[dict[str, Any]]:
	"""Pull a list of dict rows from common platform envelope shapes."""
	if isinstance(payload, list):
		return [item for item in payload if isinstance(item, dict)]
	if not isinstance(payload, Mapping):
		return []
	search_keys = keys or (
		"items",
		"jobList",
		"recommendList",
		"friendList",
		"geekList",
		"list",
		"result",
		"jobs",
		"messageList",
		"messages",
		"zpData",
	)
	for key in search_keys:
		value = payload.get(key)
		if isinstance(value, list):
			return [item for item in value if isinstance(item, dict)]
		if isinstance(value, Mapping):
			nested = _extract_list_items(value, keys=search_keys)
			if nested:
				return nested
	# One more level under common wrappers.
	for key in ("zpData", "data", "result"):
		inner = payload.get(key)
		if isinstance(inner, Mapping):
			nested = _extract_list_items(inner, keys=search_keys)
			if nested:
				return nested
		if isinstance(inner, list):
			return [item for item in inner if isinstance(item, dict)]
	return []


def _with_items(data: Mapping[str, Any], items: list[dict[str, Any]]) -> dict[str, Any]:
	merged = dict(data)
	merged["items"] = items
	merged.setdefault("total", len(items))
	return merged


def _optional_str(value: Any) -> str | None:
	return str(value) if value is not None and value != "" else None


def _validate_search_filters(params: Mapping[str, Any]) -> None:
	"""按保存入口同口径校验搜索筛选（城市名 + 标签取值域）。

	wizard 的搜索 / 导出 / watch 会直接消费自由输入与存量 watch 参数；未知城市
	留到 ``search_jobs`` 才抛 ``ValueError``，会被 runner 兜成可重试的
	``NETWORK_ERROR``；未知薪资 / 经验标签则被 ``*_CODES.get(...)`` 静默丢弃，
	命令仍报成功（Issue #1338）。
	"""
	ensure_supported_city(_optional_str(params.get("city")))
	resolve_search_code_params(
		salary=_optional_str(params.get("salary")),
		experience=_optional_str(params.get("experience")),
		education=_optional_str(params.get("education")),
		industry=_optional_str(params.get("industry")),
		scale=_optional_str(params.get("scale")),
		stage=_optional_str(params.get("stage")),
		job_type=_optional_str(params.get("job_type")),
	)


def _bool_input(inputs: Mapping[str, Any], name: str, default: bool = False) -> bool:
	"""读取布尔输入：只有 JSON 的 true/false 才合法。

	wizard inputs 来自 ``--input-json`` / MCP，不经过 Click 的 ``is_flag``；
	``bool("false")`` 在 Python 里是 ``True``，字符串或数字会被静默当成真：
	``remove`` 会删除标签而不是添加，``include_private`` 会导出未脱敏个人信息，
	``select`` 会改变平台搜索模式，``with_detail`` 会扩大采集范围。
	``null`` 与缺省一样使用默认值；其余类型点名报错，避免在平台请求之后才失败。
	"""
	if name not in inputs:
		return default
	raw = inputs[name]
	if raw is None:
		return default
	if isinstance(raw, bool):
		return raw
	raise WorkflowInputError(f"wizard 输入 {name} 必须是布尔值，收到 {raw!r}")


def _mapping_input(inputs: Mapping[str, Any], name: str) -> dict[str, Any]:
	"""读取 JSON 对象输入：缺省 / ``null`` 为空对象，其余非对象类型点名报错。

	wizard inputs 来自 ``--input-json`` / MCP：字符串会被 ``dict()`` 当成键值对
	序列解析成随机结果或 ValueError，数组 / 数字直接抛 TypeError，最终都被
	runner 兜成可重试的 ``NETWORK_ERROR``，而同一条输入重试永远复现（Issue #1330）。
	"""
	raw = inputs.get(name)
	if raw is None:
		return {}
	if not isinstance(raw, Mapping):
		raise WorkflowInputError(f"wizard 输入 {name} 必须是 JSON 对象，收到 {raw!r}")
	return dict(raw)


def _exchange_type(inputs: Mapping[str, Any]) -> int:
	"""解析交换联系方式类型：只有精确的 ``phone`` / ``wechat`` 才合法。

	wizard inputs 来自 ``--input-json`` / MCP，不经过 Click 的 ``Choice``，catalog
	也只检查必填项。原先除 ``wechat`` 外的任何值都会静默按手机号处理（`"wecaht"`、
	`"PHONE"`、`"phone "`），发出与用户意图不同的隐私授权请求（Issue #905）。
	未提供类型时按 schema 默认 ``phone``。
	"""
	raw = inputs.get("type")
	if raw is None or raw == "":
		return 1
	if raw == "wechat":
		return 2
	if raw == "phone":
		return 1
	raise WorkflowActionError("INVALID_PARAM", f"未知的交换类型: {raw!r}（可选: phone、wechat）")


def _coerce_int(raw: Any, name: str) -> int:
	"""把 wizard 输入转成整数；类型错误必须点名字段。

	wizard inputs 来自 `--input-json` / MCP，写成非数字字符串时 `int()` 抛的裸
	ValueError 会落到 runner 的通用兜底，被映射成 NETWORK_ERROR +「重试当前
	run_id」——同一条输入重试多少次都是同一个错误。bool 与小数同样不是「页码 /
	条数」的合法取值：`int(True)` 会静默当成 1，`int(2.5)` 会静默截断成 2。
	"""
	if isinstance(raw, bool) or (isinstance(raw, float) and not raw.is_integer()):
		raise WorkflowInputError(f"wizard 输入 {name} 必须是整数，收到 {raw!r}")
	try:
		return int(raw)
	except (TypeError, ValueError) as exc:
		raise WorkflowInputError(f"wizard 输入 {name} 必须是整数，收到 {raw!r}") from exc


def _check_minimum(value: int, raw: Any, name: str, minimum: int) -> int:
	if value < minimum:
		raise WorkflowInputError(f"wizard 输入 {name} 必须是不小于 {minimum} 的整数，收到 {raw!r}")
	return value


def _int_input(inputs: Mapping[str, Any], name: str, default: int, *, minimum: int = 0) -> int:
	"""读取整数输入并校验下界。

	下游把这些值直接当页码 / 翻页次数 / 条数用：`max_pages=-3` 会让
	`range(max_pages)` 一次都不循环，workflow 以「成功」收尾但结果为空；
	负数页码则会原样作为 page 参数发给平台。这类输入重试多少次都是同一个结果。
	"""
	raw = inputs.get(name)
	# 只有「没给」才用默认值；显式写的 0 不能被当成缺省悄悄换成默认值
	# （`0 or default` 会让 `count: 0` 变成 20 条）。
	if raw is None or raw == "":
		return default
	return _check_minimum(_coerce_int(raw, name), raw, name, minimum)


def _required(inputs: Mapping[str, Any], name: str) -> Any:
	value = inputs.get(name)
	if value is None or value == "":
		raise WorkflowActionError(
			"WIZARD_INPUT_REQUIRED",
			f"workflow 缺少输入 {name}",
			recoverable=True,
			recovery_action=f"在 inputs 中提供 {name} 后用同一 run_id 重试",
		)
	return value


def _required_int(inputs: Mapping[str, Any], name: str, *, minimum: int = 0) -> int:
	raw = _required(inputs, name)
	return _check_minimum(_coerce_int(raw, name), raw, name, minimum)


def _classify_action_error(
	code: str,
	message: str,
	*,
	clean_command: str | None = None,
	doctor_command: str | None = None,
	write: bool = False,
) -> tuple[str, bool, str]:
	"""Map platform error codes into wizard-facing code/recoverable/recovery.

	``clean_command`` / ``doctor_command`` 指向本次 workflow 的数据目录：
	风险锁写在 ``<data_dir>`` 下，裸命令会让 Agent 去默认目录空转（Issue #978）。

	``write`` 标记产生该响应的平台调用是写操作。写请求拿到未识别的响应时不能
	沿用「当作网络抖动、稍后重试」的兜底：响应无法证明动作没有生效，重试就是
	重放写操作（Issue #990）。
	"""
	text = message or ""
	if code == "UNKNOWN":
		if any(token in text for token in ("缺少必要参数", "参数错误", "非法参数", "invalid param")):
			code = "INVALID_PARAM"
		elif not write:
			code = "NETWORK_ERROR"
	if code == "UNKNOWN":
		# 写操作的未识别响应：沿用登记表的「先核对、勿自动重试」契约。
		return code, False, str(ERROR_CODES[code]["recovery_action"])
	if code == "ACTION_UNCONFIRMED":
		# 写操作可能已生效：沿用登记表的「先核实、勿重试」契约，不落到「稍后重试」兜底。
		return code, False, str(ERROR_CODES[code]["recovery_action"])
	if code == "ENDPOINT_DEPRECATED":
		return code, False, str(ERROR_CODES[code]["recovery_action"])
	if code == "EXCHANGE_NOT_AVAILABLE":
		# 预检拦下、未发出任何请求：先回复候选人即可重试。
		return code, True, str(ERROR_CODES[code]["recovery_action"])
	if code in RISK_ERROR_CONTRACTS:
		# 风控码：与单次命令路径共用同一份 recovery_action，绝不落到「稍后重试」兜底。
		recovery, _ = risk_error_contract(code, clean_command=clean_command, doctor_command=doctor_command)
		return code, False, recovery
	recoverable = code in {
		"AUTH_REQUIRED",
		"AUTH_EXPIRED",
		"RATE_LIMITED",
		"NETWORK_ERROR",
		"TOKEN_REFRESH_FAILED",
		"NOT_SUPPORTED",
		"CONFIG_INVALID",
	}
	if code in {"AUTH_REQUIRED", "AUTH_EXPIRED", "TOKEN_REFRESH_FAILED", "CONFIG_INVALID"}:
		recovery = str(ERROR_CODES[code]["recovery_action"])
	elif code == "NOT_SUPPORTED":
		recovery = "切换平台或 workflow goal 后重试"
	elif code == "INVALID_PARAM":
		# 纯参数错误是确定性的：自动重试同一动作必然复现（Issue #1525）。
		recovery = "换一条职位重试，或返回结果列表重新选择"
	else:
		recovery = "稍后重试，或返回主菜单选择其他事项"
	return code, recoverable, recovery


def _unwrap(
	platform: Any,
	response: dict[str, Any],
	fallback: str,
	*,
	context: ActionContext | None = None,
	write: bool = False,
	check_command: str | None = None,
) -> Any:
	"""解包平台响应；失败时转成 ``WorkflowActionError``。

	``write`` 标记本次调用是写操作：未识别的响应（``UNKNOWN``）无法证明动作
	没有生效，必须按「先核对、勿自动重试」处理，且 ``replay_safe=False`` 让
	runner 的自动重试与 ``--resume`` 都不再重跑该步骤（Issue #990）。
	"""
	if platform.is_success(response):
		return platform.unwrap_data(response) or {}
	code, message = platform.parse_error(response)
	text = message or fallback
	risk_commands = _risk_commands(context)
	code, recoverable, recovery = _classify_action_error(
		code,
		text,
		clean_command=risk_commands.get("clean_command"),
		doctor_command=risk_commands.get("doctor_command"),
		write=write,
	)
	if code in RISK_ERROR_CONTRACTS:
		# 风控响应原文可能带临时凭据，不进 workflow 结果；停止指引由 recovery_action 承载。
		text = risk_error_message(code)
	details = response.get("__cli_error_details__") if isinstance(response, dict) else None
	# 写操作的结果未知一律禁止重放：ACTION_UNCONFIRMED 明确结果未知，UNKNOWN 则
	# 无法证明动作没有发生（读取的 UNKNOWN 会先被映射成可重试的 NETWORK_ERROR）。
	replay_safe = not (code == "ACTION_UNCONFIRMED" or (code == "UNKNOWN" and write))
	if not replay_safe and context is not None:
		recovery = _unconfirmed_write_recovery(context, check_command)
	raise WorkflowActionError(
		code,
		text,
		recoverable=recoverable,
		recovery_action=recovery,
		details=details if isinstance(details, dict) and details else None,
		replay_safe=replay_safe,
	)


def _unconfirmed_write_recovery(context: ActionContext, check_command: str | None) -> str:
	"""写操作结果未知时的核对指引：沿用当前角色和数据目录；求职者不能被引到招聘者会话。"""
	command = check_command or ("hr chat" if context.role == "recruiter" else "chat")
	return f"操作可能已生效：先用 {_hint_command(context, command)} 核对平台记录，不要自动重试"


def _write_call(
	context: ActionContext,
	platform: Any,
	call: Callable[[], dict[str, Any]],
	fallback: str,
	*,
	check_command: str | None = None,
) -> Any:
	"""发出一次写请求并按写语义解包响应；所有写动作都经由这里。

	请求发出之前先把「结果未知」连同本动作的核对命令落到当前步骤上。执行进程若在
	结果确认前消失（被 kill、终端关闭、断电），步骤停在 running、什么都来不及记，
	``--resume`` 凭这条记录拒绝重放（Issue #1019）。``call`` 只负责发请求：参数的
	校验与前置读取放在调用之前，那时被终止的步骤没有记录，恢复后照常重跑。

	不经 runner 直接调用动作时没有步骤可记，直接执行。
	"""
	control = context.workflow_control
	if control is not None:
		control.begin_write(_unconfirmed_write_recovery(context, check_command))
	return _unwrap(platform, call(), fallback, context=context, write=True, check_command=check_command)


def execute_candidate_detail(
	platform: Any,
	*,
	security_id: str,
	job_id: str | None = None,
	lid: str = "",
	data_dir: Path | None = None,
	account: str = "",
	context: ActionContext | None = None,
) -> dict[str, Any]:
	"""Fetch job detail with the same httpx → job_card fallback as `boss detail`.

	CDP 模式下 client 会把 job_detail / job_card 都改走浏览器通道（不碰 httpx），
	风控异常原样上抛，不再换通道补请求。
	"""
	from boss_agent_cli.api.models import employment_type_from_raw
	from boss_agent_cli.services.job_card import build_job_from_card

	greeted = False
	if data_dir is not None:
		with CacheStore(data_dir / "cache" / "boss_agent.db") as cache:
			greeted = cache.is_greeted(security_id, job_id=job_id or "", account=account)

	last_error: WorkflowActionError | None = None
	if job_id:
		raw = platform.job_detail(job_id)
		if platform.is_success(raw):
			# 包装详情漂移（zpData / jobInfo 不是对象）与 `detail` 命令同一口径：
			# 按空 section 处理并改走职位卡片，不能抛 AttributeError（Issue #1162）。
			platform_data = platform.unwrap_data(raw)
			platform_data = platform_data if isinstance(platform_data, dict) else {}
			job_info = field_dict(platform_data, "jobInfo")
			if job_info:
				boss_info = field_dict(platform_data, "bossInfo")
				brand_info = field_dict(platform_data, "brandComInfo")
				raw_job_type = job_info.get("jobType")
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
					"skills": field_list(job_info, "jobLabels") or field_list(job_info, "skills"),
					"boss_name": boss_info.get("name", ""),
					"boss_title": boss_info.get("title", ""),
					"boss_active": boss_info.get("activeTimeDesc", "离线"),
					"security_id": security_id,
					"lid": lid,
					"raw_job_type": raw_job_type,
					"employment_type": employment_type_from_raw(raw_job_type),
					"days_per_week": job_info.get("daysPerWeekDesc", ""),
					"least_month": job_info.get("leastMonthDesc", ""),
					"pay_type": job_info.get("payTypeDesc", ""),
					"greeted": greeted,
					"channel": "job_detail",
				}
		else:
			code, message = platform.parse_error(raw)
			text = message or "职位详情获取失败"
			risk_commands = _risk_commands(context)
			code, recoverable, recovery = _classify_action_error(
				code,
				text,
				clean_command=risk_commands.get("clean_command"),
				doctor_command=risk_commands.get("doctor_command"),
			)
			last_error = WorkflowActionError(code, text, recoverable=recoverable, recovery_action=recovery)

	try:
		raw_card = platform.job_card(security_id, lid)
	except NotImplementedError as exc:
		if last_error is not None:
			raise last_error from exc
		raise WorkflowActionError(
			"NOT_SUPPORTED",
			str(exc) or "当前平台不支持职位详情",
			recoverable=True,
			recovery_action="切换平台或改用其他事项",
		) from exc

	if platform.is_success(raw_card):
		card = field_dict(platform.unwrap_data(raw_card), "jobCard")
		if card:
			job = build_job_from_card(card, security_id=security_id, greeted=greeted)
			job["lid"] = lid
			job["channel"] = "job_card"
			if job_id and not job.get("job_id"):
				job["job_id"] = job_id
			return job

	if last_error is not None:
		raise last_error
	# 两个通道都成功但载荷漂移、读不出职位：与 `detail` / `show` 一致按「职位不存在
	# 或已下架」返回，而不是拿成功响应当错误包络解出 UNKNOWN → NETWORK_ERROR。
	not_found_recoverable, not_found_recovery = error_contract_for_code("JOB_NOT_FOUND")
	raise WorkflowActionError(
		"JOB_NOT_FOUND",
		str(ERROR_CODES["JOB_NOT_FOUND"]["message"]),
		recoverable=not_found_recoverable,
		recovery_action=not_found_recovery or str(ERROR_CODES["JOB_NOT_FOUND"]["recovery_action"]),
	)


def _auth_status(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	token = context.auth().check_status()
	if token is None:
		# 未登录不是失败，是「在等人」。返回 WAITING_INPUT 让 run 挂起可恢复，
		# 而不是把整个 workflow 打成 failed 逼用户从头重走向导。
		# 刻意不阻塞轮询等待扫码——Agent 与真人都不该被卡在这里。
		login_command = boss_command("login", platform_name=context.platform, data_dir=context.data_dir)
		control = context.workflow_control
		resume_command = boss_command(
			f"wizard --resume {shell_argument(control.run_id)}" if control else "wizard",
			platform_name=context.platform,
			data_dir=context.data_dir,
		)
		return StepResult(
			{"authenticated": False, "platform": context.platform, "role": context.role},
			status=WorkflowStatus.WAITING_INPUT,
			next_action=resume_command,
			operator_actions=(
				f"运行 {login_command} 并在弹出的浏览器里扫码登录",
				f"登录完成后回到终端，执行 {resume_command} 继续",
			),
		)
	return StepResult({"authenticated": True, "platform": context.platform, "role": context.role})


def _candidate_search(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	_required(inputs, "query")
	account = account_scope(context.auth())
	with CacheStore(context.data_dir / "cache" / "boss_agent.db") as cache:
		with context.candidate_platform() as platform:
			result = execute_candidate_search(platform, cache, context.logger, {**inputs, "account": account})
	return StepResult(
		{
			"items": result.items,
			"pagination": {
				"page": _int_input(inputs, "page", 1, minimum=1),
				# 与顶层 `boss search` 同一口径（Issue #887）：平台没说 hasMore 时为 null。
				"has_more": result.has_more_reported,
				"has_more_source": "platform_hasMore" if result.has_more_reported is not None else "unknown",
				"total": result.total or len(result.items),
				"total_kind": "returned_page_count",
			},
		}
	)


def _candidate_recommend(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	with context.candidate_platform() as platform:
		data = _unwrap(
			platform,
			platform.recommend_jobs(page=_int_input(inputs, "page", 1, minimum=1)),
			"推荐职位获取失败",
			context=context,
		)
	items = _extract_list_items(data, ("recommendList", "jobList", "list", "items", "result"))
	return StepResult(_with_items({"result": data}, items))


def _candidate_detail(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	from boss_agent_cli.wizard.renderer import busy_status

	security_id = str(_required(inputs, "security_id"))
	job_id = _optional_str(inputs.get("job_id"))
	lid = str(inputs.get("lid") or "")
	with busy_status("正在读取职位详情…"):
		with context.candidate_platform() as platform:
			data = execute_candidate_detail(
				platform,
				security_id=security_id,
				job_id=job_id,
				lid=lid,
				data_dir=context.data_dir,
				account=account_scope(context.auth()),
				context=context,
			)
	return StepResult({"job": data, "result": data})


def _raise_local_state_write_failed(message: str, details: dict[str, Any]) -> NoReturn:
	"""平台已确认成功、本地记账失败：不得回成可重试的 NETWORK_ERROR。"""
	contract = ERROR_CODES["LOCAL_STATE_WRITE_FAILED"]
	raise WorkflowActionError(
		"LOCAL_STATE_WRITE_FAILED",
		message,
		recoverable=bool(contract["recoverable"]),
		recovery_action=str(contract["recovery_action"]),
		details=details,
		replay_safe=False,
	)


def _raise_candidate_action_blocked(
	context: ActionContext, blocked: CandidateActionBlocked, *, check_command: str
) -> NoReturn:
	"""本地预约已存在：已成功回 ALREADY_*，pending 回不可重放的 ACTION_UNCONFIRMED。"""
	if blocked.code in {"ALREADY_GREETED", "ALREADY_APPLIED"}:
		raise WorkflowActionError(blocked.code, blocked.message)
	raise WorkflowActionError(
		"ACTION_UNCONFIRMED",
		blocked.message,
		recoverable=False,
		recovery_action=_unconfirmed_write_recovery(context, check_command),
		details={"outcome_unknown": True, "replay_safe": False},
		replay_safe=False,
	)


def _candidate_apply(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	security_id = str(_required(inputs, "security_id"))
	job_id = str(_required(inputs, "job_id"))
	lid = str(inputs.get("lid") or "")
	account = account_scope(context.auth())
	with CacheStore(context.data_dir / "cache" / "boss_agent.db") as cache:
		if cache.is_applied(security_id, job_id, account=account):
			raise WorkflowActionError("ALREADY_APPLIED", "已对该职位发起过投递/立即沟通")
		with context.candidate_platform() as platform:
			try:
				data = run_candidate_action(
					cache,
					security_id=security_id,
					job_id=job_id,
					action="apply",
					account=account,
					send=lambda: _write_call(
						context,
						platform,
						lambda: platform.apply(security_id, job_id, lid=lid),
						"投递失败",
						check_command="me --section deliver",
					),
				)
			except CandidateActionBlocked as blocked:
				_raise_candidate_action_blocked(context, blocked, check_command="me --section deliver")
			except CandidateActionNotRecorded:
				# 重试或 --resume 会绕过 is_applied / 预约检查，把同一次投递再提交一遍。
				_raise_local_state_write_failed(
					"投递/立即沟通已提交，但本地状态保存失败；不要自动重发",
					{"security_id": security_id, "job_id": job_id, "applied": True, "recorded": False},
				)
	return StepResult({"security_id": security_id, "job_id": job_id, "result": data})


def _candidate_greet(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	security_id = str(_required(inputs, "security_id"))
	job_id = str(_required(inputs, "job_id"))
	message = str(inputs.get("message") or "")
	account = account_scope(context.auth())
	with CacheStore(context.data_dir / "cache" / "boss_agent.db") as cache:
		if cache.is_greeted(security_id, job_id=job_id, account=account):
			raise WorkflowActionError("ALREADY_GREETED", "已向该招聘者打过招呼")
		with context.candidate_platform() as platform:
			try:
				data = run_candidate_action(
					cache,
					security_id=security_id,
					job_id=job_id,
					action="greet",
					account=account,
					send=lambda: _write_call(
						context,
						platform,
						lambda: platform.greet(security_id, job_id, message),
						"打招呼失败",
					),
				)
			except CandidateActionBlocked as blocked:
				_raise_candidate_action_blocked(context, blocked, check_command="chat")
			except CandidateActionNotRecorded:
				# 重试或 --resume 会绕过 is_greeted / 预约检查，把同一个招呼再发一次。
				_raise_local_state_write_failed(
					"已向该招聘者打招呼，但本地状态保存失败；不要自动重发",
					{"security_id": security_id, "job_id": job_id, "greeted": True, "recorded": False},
				)
	return StepResult({"security_id": security_id, "job_id": job_id, "result": data})


def _candidate_friend(platform: Any, identifier: str, *, context: ActionContext | None = None) -> dict[str, Any]:
	try:
		friend, error_response = find_friend(platform, identifier)
	except FriendLookupLimitExceeded as exc:
		# 候选人无法「缩小沟通列表范围」：照抄旧指引只会把同一段超过分页上限的
		# 遍历再跑一遍。find_friend 优先匹配稳定 uid，给出可执行的 uid 路径。
		# recoverable 必须是 false：分页上限是确定性结果，标成可重试会让
		# max_retries≥1 的向导自动重翻 50 页并消耗请求预算（Issue #1549）。
		chat_hint = _hint_command(context, "chat") if context is not None else "boss chat"
		raise WorkflowActionError(
			"NETWORK_ERROR",
			str(exc),
			recoverable=False,
			recovery_action=(
				f"先用 {chat_hint} 确认联系人仍在沟通列表并取到 uid，再用 uid 重试；"
				"按会轮换的 securityId 查询超过分页上限时，重试同样的查询不会改变结果"
			),
		) from exc
	if error_response is not None:
		_unwrap(platform, error_response, "沟通列表获取失败", context=context)
	if friend is not None:
		return friend
	raise WorkflowActionError("CONTACT_NOT_FOUND", f"沟通列表中未找到联系人: {identifier}")


def _candidate_current_security_id(friend: Mapping[str, Any]) -> str:
	security_id = str(friend.get("securityId") or friend.get("security_id") or "").strip()
	if security_id:
		return security_id
	raise WorkflowActionError(
		"NETWORK_ERROR",
		"沟通列表返回的联系人缺少当前 securityId，已停止执行；按当前列表重试不会改变结果",
		recoverable=False,
		recovery_action="刷新沟通列表并取到联系人 security_id 后重新执行；不要自动重试同一列表结果",
	)


def _candidate_exchange(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	identifier = str(_required(inputs, "security_id"))
	exchange_type = _exchange_type(inputs)
	with context.candidate_platform() as platform:
		friend = _candidate_friend(platform, identifier, context=context)
		uid = str(friend.get("uid") or "")
		security_id = _candidate_current_security_id(friend)
		name = str(friend.get("name") or "-")
		data = _write_call(
			context,
			platform,
			lambda: platform.exchange_contact(security_id, uid, name, exchange_type=exchange_type),
			"联系方式交换失败",
		)
	return StepResult({"uid": uid, "security_id": security_id, "result": data})


_LABEL_IDS = {"新招呼": 1, "沟通中": 2, "已约面": 3, "已获取简历": 4, "不合适": 7, "收藏": 11}


def _candidate_mark(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	identifier = str(_required(inputs, "security_id"))
	label = str(_required(inputs, "label"))
	# 先校验布尔值，再查联系人：非法输入不应产生任何平台请求。
	remove = _bool_input(inputs, "remove")
	# `str.isdigit()` 对 '①' / '²' 这类非 ASCII 数字也为真，但 `int()` 会抛
	# ValueError，落成可重试的网络错误；只有纯 ASCII 数字串才当 ID（Issue #1306）。
	# 4300 位以上的纯数字串让 int() 抛裸 ValueError，被 runner 兜成
	# NETWORK_ERROR +「重试当前 run_id」（Issue #1442）：与未知标签同口径。
	try:
		label_id = int(label) if label.isascii() and label.isdigit() else _LABEL_IDS.get(label)
	except (ValueError, OverflowError):
		label_id = None
	if label_id is None:
		shown = label if len(label) <= 40 else f"{label[:40]}…（共 {len(label)} 字符）"
		raise WorkflowActionError(
			"INVALID_PARAM", f"未知标签: {shown}（可选: 新招呼、沟通中、已约面、已获取简历、不合适、收藏）"
		)
	with context.candidate_platform() as platform:
		friend = _candidate_friend(platform, identifier, context=context)
		uid = str(friend.get("uid") or "")
		# friendSource 只是回传给标签接口的定位参数：平台字段漂移成非数字串 /
		# 对象 / 1e400 时按缺失的默认 0 处理，不能让 int() 的 ValueError 把整个
		# 步骤兜成可重试的 NETWORK_ERROR（Issue #1314）。
		raw_friend_source = friend.get("friendSource")
		try:
			friend_source = int(raw_friend_source or 0)
		except (TypeError, ValueError, OverflowError):
			friend_source = 0
		data = _write_call(
			context,
			platform,
			lambda: platform.friend_label(uid, label_id, friend_source, remove=remove),
			"联系人标签更新失败",
		)
	return StepResult(
		{
			"uid": uid,
			"security_id": str(friend.get("securityId") or friend.get("security_id") or ""),
			"label": label,
			"result": data,
		}
	)


def _candidate_chat(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	with context.candidate_platform() as platform:
		data = _unwrap(
			platform,
			platform.friend_list(page=_int_input(inputs, "page", 1, minimum=1)),
			"沟通列表获取失败",
			context=context,
		)
	items = _extract_list_items(data, ("result", "friendList", "list", "items"))
	return StepResult(_with_items({"result": data}, items))


def _candidate_chat_history(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	with context.candidate_platform() as platform:
		friend = _candidate_friend(platform, str(_required(inputs, "gid")), context=context)
		gid = str(friend.get("uid") or "")
		security_id = _candidate_current_security_id(friend)
		response = platform.chat_history(
			gid,
			security_id,
			page=_int_input(inputs, "page", 1, minimum=1),
			count=_int_input(inputs, "count", 20, minimum=1),
		)
		data = _unwrap(platform, response, "聊天记录获取失败", context=context)
	items = _extract_list_items(data, ("messageList", "messages", "list", "items", "result"))
	return StepResult(
		_with_items(
			{
				"result": data,
				"gid": gid,
				"uid": gid,
				"security_id": security_id,
			},
			items,
		)
	)


def _candidate_pipeline_data(
	context: ActionContext, inputs: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], int, bool]:
	with context.candidate_platform() as platform:
		# 沟通列表是分页的：只看第 1 页会静默漏掉后续页里待跟进的沟通，
		# 与顶层 `boss pipeline` / `boss digest` 的聚合结果不一致（Issue #881）。
		chat_items, friend_error, pages_scanned, truncated = collect_friend_list_page(platform)
		if friend_error is not None:
			_unwrap(platform, friend_error, "沟通列表获取失败", context=context)
		interview_data = _unwrap(platform, platform.interview_data(), "面试列表获取失败", context=context)
	interview_items = dict_items(field_list(interview_data, "interviewList"))
	items = build_pipeline_items(
		chat_items=chat_items,
		interview_items=interview_items,
		now_ts_ms=_int_input(inputs, "now_ts_ms", int(time.time() * 1000)),
		stale_days=_int_input(inputs, "days_stale", 3),
	)
	return items, pages_scanned, truncated


def _candidate_pipeline(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	items, pages_scanned, truncated = _candidate_pipeline_data(context, inputs)
	return StepResult(
		{
			"items": items,
			"total": len(items),
			"chat_pages_scanned": pages_scanned,
			"truncated": truncated,
		}
	)


def _candidate_digest(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	items, pages_scanned, truncated = _candidate_pipeline_data(context, inputs)
	follow_ups = select_follow_up_candidates(items)
	data = build_digest(
		new_matches=[item for item in items if item.get("source") == "chat" and item.get("stage") == "reply_needed"],
		follow_ups=follow_ups,
		interviews=[item for item in items if item.get("source") == "interview"],
	)
	data["chat_pages_scanned"] = pages_scanned
	data["truncated"] = truncated
	return StepResult(data)


def _local_shortlist(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	with CacheStore(context.data_dir / "cache" / "boss_agent.db") as cache:
		items = cache.list_shortlist()
	return StepResult({"items": items, "total": len(items)})


def _local_resumes(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	items, corrupt = ResumeStore(context.data_dir / "resumes").list_all_with_corrupt()
	data: dict[str, Any] = {"items": items, "total": len(items)}
	# 坏文件不能假装不存在：列表里没有名字，用户不会知道 resumes/ 下还有一份
	# 需要修正或删除的文件（与 `boss resume list` 的 hints.corrupt 同口径）。
	if corrupt:
		data["corrupt"] = corrupt
	return StepResult(data)


def _ai_assist(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	resume_name = str(_required(inputs, "resume"))
	resume, load_error = ResumeStore(context.data_dir / "resumes").get_with_error(resume_name)
	if resume is None and load_error is not None:
		# 文件存在但读不出来：报「不存在」会让 Agent 去 resume init，又被同名
		# 文件挡住（RESUME_ALREADY_EXISTS），恢复链断在这里（Issue #1189 同口径）。
		raise WorkflowActionError(
			"RESUME_FILE_INVALID",
			f"简历文件 {resume_name!r} 损坏：{load_error}",
			recoverable=True,
			recovery_action=(
				f"修正 resumes/{resume_name}.json 的 JSON 内容后重试，"
				f"或执行 {_hint_command(context, f'resume delete {shell_argument(resume_name)}')} 后重新创建；"
				"该文件只存在本机"
			),
		)
	if resume is None:
		raise WorkflowActionError("RESUME_NOT_FOUND", f"简历 {resume_name!r} 不存在")
	config_store = AIConfigStore(context.data_dir)
	config = config_store.load_config()
	api_key = config_store.get_api_key()
	base_url = config_store.get_base_url()
	model = config.get("ai_model")
	if not api_key or not base_url or not model:
		raise WorkflowActionError(
			"AI_NOT_CONFIGURED",
			"AI 服务未配置",
			recoverable=True,
			recovery_action=_hint_command(context, "ai config --provider <provider> --model <model> --api-key <key>"),
		)
	# 数值项与 ai 命令同口径校验：坏值不能原样发给服务商、只换回泛化的
	# 平台错误；本地配置问题给 CONFIG_INVALID 与修复命令（Issue #1209、#1525）。
	try:
		temperature = ai_float_setting(config, "ai_temperature", 0.7)
		max_tokens = ai_int_setting(config, "ai_max_tokens", 4096)
	except ValueError as exc:
		raise WorkflowActionError(
			"CONFIG_INVALID",
			f"本地 AI 配置损坏：{exc}",
			recoverable=True,
			recovery_action=_hint_command(context, "ai config --max-tokens 4096 --temperature 0.7"),
			auto_retry=False,
		) from exc
	service = AIService(
		str(base_url),
		api_key,
		str(model),
		temperature=temperature,
		max_tokens=max_tokens,
	)
	try:
		result = service.chat(
			[
				{"role": "system", "content": "你是求职顾问。"},
				{"role": "user", "content": f"{inputs['prompt']}\n\n简历:\n{resume_to_text(resume)}"},
			]
		)
	except AIServiceError as exc:
		raise WorkflowActionError(
			"AI_API_ERROR",
			str(exc),
			recoverable=True,
			recovery_action=recovery_action_for(
				exc,
				"检查 AI 配置后重试",
				# 恢复建议里的 ai config 必须写回触发本次 workflow 的目录。
				config_command=_hint_command(context, "ai config"),
			),
		) from exc
	return StepResult({"resume": resume_name, "result": result})


_EXPORT_OUTPUT_CONTRACTS: dict[OutputPathProblem, tuple[str, str]] = {
	OutputPathProblem.IS_DIRECTORY: ("INVALID_PARAM", "在 output 里给出完整文件名"),
	OutputPathProblem.NOT_REGULAR: ("INVALID_PARAM", "换一个普通文件路径后重试"),
	OutputPathProblem.PARENT_NOT_DIR: ("LOCAL_IO_ERROR", "换一个父目录存在且可写的路径后重试"),
}


def _export_output_path(context: ActionContext, inputs: Mapping[str, Any], fmt: str) -> Path:
	"""在发出平台请求前校验导出目标，返回展开后的路径。

	命令层 `export` / `chat --export` 已把 `-o` 校验前置（`inspect_output_path`）；
	wizard 之前要等取完数才在 `prepare_output_path` 上暴露路径问题，非法 format
	也会先翻页消耗请求预算（Issue #1330）。这里只读检查，不建目录：父目录仍由
	写文件前的 `prepare_output_path` 创建。
	"""
	raw = inputs.get("output")
	if raw is None or raw == "":
		raw = context.data_dir / "exports" / f"wizard-jobs.{fmt}"
	elif not isinstance(raw, (str, os.PathLike)):
		raise WorkflowActionError(
			"INVALID_PARAM",
			f"wizard export output 必须是文件路径字符串，收到 {type(raw).__name__}",
			recovery_action="把 output 改成完整文件路径后重试",
		)
	target, problem = inspect_output_path(raw)
	if problem is None:
		return target
	code, recovery_action = _EXPORT_OUTPUT_CONTRACTS[problem]
	if problem is OutputPathProblem.IS_DIRECTORY:
		message = f"wizard export 的 output 需要文件路径，{str(target)!r} 是目录"
		recovery_action = f"{recovery_action}，例如 {target / 'result.txt'}"
	elif problem is OutputPathProblem.NOT_REGULAR:
		message = f"wizard export 的 output 需要普通文件路径，{str(target)!r} 不是普通文件"
	else:
		message = f"wizard export 的 output 无法创建父目录，某级父路径不是目录: {str(target)!r}"
	raise WorkflowActionError(code, message, recovery_action=recovery_action)


def _candidate_export(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	from boss_agent_cli.services.job_export import prepare_export_items, write_export_file

	# 参数与路径必须在平台请求之前校验：非法 format 不能先翻完页才发现，
	# `output` 指向目录也不能消耗一次受限请求（与命令层 `-o` 预检同口径）。
	include_private = _bool_input(inputs, "include_private")
	fmt = str(inputs.get("format") or "json")
	if fmt not in {"json", "csv"}:
		raise WorkflowActionError("INVALID_PARAM", "wizard export format 仅支持 json/csv")
	output_path = _export_output_path(context, inputs, fmt)
	with CacheStore(context.data_dir / "cache" / "boss_agent.db") as cache:
		with context.candidate_platform() as platform:
			# 与 candidate_search / candidate_watch 一致：导出时的 greeted 标记
			# 必须按当前账号计算，否则登录态下会把所有职位都标成未打招呼。
			result = execute_candidate_search(
				platform, cache, context.logger, {**inputs, "account": account_scope(context.auth())}
			)
	output = prepare_output_path(output_path)
	items = prepare_export_items(result.items, include_private=include_private)
	write_export_file(items, fmt, str(output))
	return StepResult({"path": str(output), "format": fmt, "count": len(items)}, artifacts=(str(output),))


def _saved_search_invalid_error(context: ActionContext, name: str, detail: str) -> WorkflowActionError:
	# watch 名来自 wizard 的 name 输入、是自由文本：带空格时不加引号，
	# 照抄 `watch remove 我的 监控` 会把一个名字拆成两个参数。
	return WorkflowActionError(
		"SAVED_SEARCH_INVALID",
		detail,
		recoverable=True,
		recovery_action=(
			f"{_hint_command(context, f'watch remove {positional_hint(name)}')}；"
			f"再用 {_hint_command(context, f'watch add {positional_hint(name)} <关键词>')} 重建"
		),
	)


def _candidate_watch(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	action = str(inputs.get("action") or "list")
	with CacheStore(context.data_dir / "cache" / "boss_agent.db") as cache:
		if action == "list":
			items, corrupt = cache.list_saved_searches_with_corrupt()
			data: dict[str, Any] = {"items": items, "total": len(items)}
			if corrupt:
				data["corrupt"] = corrupt
			return StepResult(data)
		name = str(_required(inputs, "name"))
		if action == "add":
			params: dict[str, Any] = {}
			for key in (
				"query",
				"city",
				"salary",
				"experience",
				"education",
				"industry",
				"scale",
				"stage",
				"job_type",
				"welfare",
			):
				value = inputs.get(key)
				if key == "welfare" and isinstance(value, (list, tuple)):
					# 存储格式与 CLI `watch add --welfare` 一致（逗号分隔字符串）：
					# 直接写数组会让后续 `watch run` 在 `.split(",")` 上抛 AttributeError。
					value = ",".join(str(item).strip() for item in value if str(item).strip())
				elif value is not None and not isinstance(value, str):
					raise WorkflowActionError(
						"INVALID_PARAM", f"watch 参数 {key} 必须是字符串（收到 {type(value).__name__}）"
					)
				params[key] = value
			if inputs.get("active"):
				try:
					params["active"] = resolve_active_level(str(inputs.get("active")))
				except ValueError as exc:
					raise WorkflowActionError("INVALID_PARAM", str(exc)) from exc
			_required(inputs, "query")
			try:
				_validate_search_filters(params)
			except ValueError as exc:
				raise WorkflowActionError(
					"INVALID_PARAM", str(exc), recovery_action="修正 watch 筛选参数后重试"
				) from exc
			cache.save_saved_search(name, params)
			return StepResult({"action": "add", "name": name, "params": params})
		if action == "remove":
			return StepResult({"action": "remove", "name": name, "removed": cache.delete_saved_search(name)})
		if action != "run":
			raise WorkflowActionError("INVALID_PARAM", f"未知的 watch 动作: {action}（可选: add、remove、run）")
		try:
			record = cache.get_saved_search(name)
		except SavedSearchParamsError as exc:
			raise _saved_search_invalid_error(context, name, str(exc)) from exc
		if record is None:
			raise WorkflowActionError("SAVED_SEARCH_NOT_FOUND", f"未找到 watch: {name}")
		try:
			_validate_search_filters(record["params"])
		except ValueError as exc:
			raise _saved_search_invalid_error(context, name, f"watch {name!r} 的参数已失效：{exc}") from exc
		account = account_scope(context.auth())
		with context.candidate_platform() as platform:
			result = execute_candidate_search(platform, cache, context.logger, {**record["params"], "account": account})
		watch_result = cache.record_watch_results(name, result.items, account=account)
		return StepResult({"name": name, **watch_result})


def _crawl_seed_session(context: ActionContext) -> tuple[dict[str, str], str]:
	"""Load boss-login cookies for the isolated crawl browser profile."""
	from boss_agent_cli.auth.manager import AuthRequired

	login_action = boss_command("login", platform_name=context.platform, data_dir=context.data_dir)
	try:
		token = context.auth().get_token()
	except AuthRequired as exc:
		raise WorkflowActionError(
			"AUTH_REQUIRED",
			str(exc),
			recoverable=True,
			recovery_action=login_action,
		) from exc
	raw_cookies = token.get("cookies") if isinstance(token, dict) else None
	if not isinstance(raw_cookies, dict) or not raw_cookies:
		raise WorkflowActionError(
			"AUTH_REQUIRED",
			"本地登录态缺少 Cookie，采集浏览器无法自动登录",
			recoverable=True,
			recovery_action=login_action,
		)
	cookies = {str(name): str(value) for name, value in raw_cookies.items() if name and value is not None}
	if context.platform in {"zhipin", ""} and "wt2" not in cookies:
		raise WorkflowActionError(
			"AUTH_REQUIRED",
			"登录态缺少 wt2，采集浏览器会被当成未登录并立即退出",
			recoverable=True,
			recovery_action=login_action,
		)
	user_agent = str(token.get("user_agent") or "")
	return cookies, user_agent


def _crawl_service(context: ActionContext, cache: CacheStore, *, lazy: bool = False) -> CrawlService:
	"""构造 crawl service。

	``lazy=True``（恢复路径）把登录态与运行时检查推迟到 ``preflight``：已完成的
	run 不需要浏览器就能返回既有结果，若在构造时先跑 ``ensure_crawl_runtime`` /
	取 Cookie，已完成 run 也会被要求登录或安装 crawl 依赖，与 CLI 的
	``crawl resume`` 口径不一致（Issue #1412 / #1540）。新建任务仍立即预检：
	依赖缺失时不能留下一个只会在执行阶段失败的 run（Issue #1050）。
	"""
	control = context.workflow_control

	def load_session() -> tuple[dict[str, str], str]:
		# 依赖先于登录态：没装 crawl 依赖时登录也无法采集，也不应创建内部任务。
		ensure_crawl_runtime()
		return _crawl_seed_session(context)

	session: list[tuple[dict[str, str], str]] = []

	def transport(settings: Any) -> DrissionCrawlerSession:
		if not session:
			session.append(load_session())
		seed_cookies, user_agent = session[0]
		return DrissionCrawlerSession(
			profile_path=settings.profile_path,
			chrome_path=settings.chrome_path,
			cdp_port=settings.cdp_port,
			hook_profile=settings.hook_profile,
			hook_dir=settings.hook_dir,
			seed_cookies=seed_cookies,
			user_agent=user_agent,
		)

	def preflight() -> None:
		if not session:
			session.append(load_session())

	if not lazy:
		preflight()
	return CrawlService(
		cache,
		data_dir=context.data_dir,
		transport_factory=transport,
		control_probe=control.poll if control is not None else None,
		on_run_created=control.bind_inner_run if control is not None else None,
		preflight=preflight,
	)


def _attach_wizard_jobs(data: dict[str, Any], cache: CacheStore, run_id: str) -> dict[str, Any]:
	"""Embed a browseable job list so TTY follow-up can open details after crawl."""
	from boss_agent_cli.wizard.live_crawl import (
		BROWSE_JOB_LIMIT,
		SAMPLE_TITLE_LIMIT,
		jobs_for_wizard,
		sample_titles_for_wizard,
	)

	jobs, total = jobs_for_wizard(cache, run_id, limit=BROWSE_JOB_LIMIT)
	merged = dict(data)
	merged["jobs_seen"] = total
	merged["jobs"] = jobs
	merged["sample_titles"] = sample_titles_for_wizard(cache, run_id, limit=SAMPLE_TITLE_LIMIT)
	return merged


def _is_stop_error(error: Any) -> bool:
	"""普通 stopped 记录里识别「用户/工作流 stop」的中英文文案（Issue #1238）。"""
	message = str(error or "")
	return "stop requested" in message or "stop 请求" in message or "workflow stop" in message


def _crawl_outcome_result(outcome: Any, cache: CacheStore | None = None) -> StepResult:
	data = outcome.as_dict()
	checkpoint = data.get("checkpoint") if isinstance(data, dict) else None
	resume_command = checkpoint.get("resume_command") if isinstance(checkpoint, dict) else None
	if not isinstance(resume_command, str) or not resume_command:
		# 兜底只服务拿不到 checkpoint 的 outcome（如测试替身）：有 data_dir 时同样
		# 保留目录，避免恢复命令落到默认目录的另一份采集记录。
		resume_command = boss_command(
			f"crawl resume {shell_argument(outcome.run_id)}",
			data_dir=getattr(outcome, "data_dir", None),
		)
	if cache is not None:
		data = _attach_wizard_jobs(data if isinstance(data, dict) else {}, cache, str(outcome.run_id))
	# stop 的恢复命令不能是 `crawl resume`：它默认清除 stop 标记，用户刚发出的
	# 停止会被工具自己的指引撤销（Issue #1238）。只读的 status 足够让人核对断点。
	status_command = boss_command(
		f"crawl status {shell_argument(outcome.run_id)}",
		data_dir=getattr(outcome, "data_dir", None),
	)
	if outcome.status == "timeout_stopped":
		raise WorkflowActionError(
			"WORKFLOW_TIMEOUT",
			outcome.error or "workflow 超过设定时限",
			recoverable=True,
			recovery_action=resume_command,
		)
	if outcome.status == "workflow_stopped" or (outcome.status == "stopped" and _is_stop_error(outcome.error)):
		raise WorkflowActionError(
			"WORKFLOW_STOPPED",
			outcome.error or "crawl 已停止",
			recoverable=False,
			recovery_action=status_command,
		)
	if outcome.status == "throttle_stopped":
		# 本地共享请求预算等待超时：run 预算没有耗尽，等待后 resume 从断点继续
		# （Issue #1556）。
		raise WorkflowActionError(
			"REQUEST_BUDGET_EXHAUSTED",
			outcome.error or "本地请求节流等待超时，请求未发出",
			recoverable=True,
			recovery_action=resume_command,
			# 立即重试仍可能撞上同一个冷却 / 占用者，交给操作者稍后恢复。
			auto_retry=False,
		)
	if outcome.status in {"risk_stopped", "budget_stopped"}:
		# 风控停止时 service 会 keep Chrome；把标记写入结果供向导文案使用。
		browser_kept_open = outcome.status == "risk_stopped"
		if isinstance(data, dict):
			browser_raw = data.get("browser")
			browser = browser_raw if isinstance(browser_raw, dict) else {}
			data = {
				**data,
				"browser_kept_open": browser_kept_open,
				"cdp_port": browser.get("cdp_port") or data.get("cdp_port"),
			}
		operator_actions: tuple[str, ...]
		if browser_kept_open:
			operator_actions = (
				"这通常不是账号退出登录——采集用的是独立浏览器，首次启动常被平台环境校验拦下",
				"采集用 Chrome 已保持打开：若窗口里已是正常职位列表，直接选「继续采集」即可（会换用页面内请求重试）",
				"若窗口提示环境异常，先在该窗口手动刷新职位搜索页，再选「继续采集」",
				f"命令行恢复：{resume_command}",
			)
		else:
			operator_actions = (
				"本轮采集因预算上限停止，不是账号异常",
				f"该任务的预算已用完，继续采集需要新建任务（或调高 max_requests 后重新 crawl start）；断点可用 {status_command} 查看",
			)
		return StepResult(
			data,
			status=WorkflowStatus.WAITING_INPUT,
			next_action=resume_command if browser_kept_open else status_command,
			artifacts=tuple(outcome.output_paths.values()),
			operator_actions=operator_actions,
		)
	if outcome.status != "completed":
		raise WorkflowActionError(
			"NETWORK_ERROR",
			outcome.error or f"crawl 以状态 {outcome.status} 停止",
			recoverable=True,
			recovery_action=resume_command,
		)
	return StepResult(data, artifacts=tuple(outcome.output_paths.values()))


def _crawl_busy_error(context: ActionContext, exc: CrawlBusyError) -> WorkflowActionError:
	"""同一个 crawl run 正由别的进程执行：本次没有发出任何请求，稍后恢复是安全的。"""
	status_command = _hint_command(context, f"crawl status {shell_argument(exc.run_id)}")
	return WorkflowActionError(
		"CRAWL_BUSY",
		str(exc),
		recoverable=True,
		recovery_action=f"用 {status_command} 查看进度，等当前执行结束后再恢复；不要并发恢复同一个任务",
		details={"run_id": exc.run_id},
	)


def _crawl_profile_busy_error(context: ActionContext, exc: CrawlProfileBusy) -> WorkflowActionError:
	"""采集配置目录被另一个运行中的采集 Chrome 占用：本次没有发出请求。"""
	# `crawl status` 的 run_id 是必填参数：写不带值的命令会让 Agent 照抄后拿到
	# INVALID_PARAM（Issue #1289）。用占位符形式，与项目其他 hint 一致。
	status_command = _hint_command(context, "crawl status <run_id>")
	return WorkflowActionError(
		"CRAWL_PROFILE_BUSY",
		str(exc),
		recoverable=bool(ERROR_CODES["CRAWL_PROFILE_BUSY"]["recoverable"]),
		recovery_action=f"用 {status_command} 查看正在运行的采集，等它结束后再继续；不要并发使用同一数据目录的采集浏览器",
	)


def _crawl_export_error(exc: CrawlExportError) -> WorkflowActionError:
	"""导出失败是本地目录 / 权限问题：重试或恢复只会复现（Issue #1287）。

	CLI 的 `crawl run` / `crawl resume` 对同一异常返回 `LOCAL_IO_ERROR`、
	recoverable=false；wizard 原先兜成可重试的 `NETWORK_ERROR`，`--max-retries>0`
	时还会自动重跑步骤、重新打开采集 Chrome。采集请求可能已经发出，因此同时
	声明 `replay_safe=False`。
	"""
	recoverable, _ = error_contract_for_code("LOCAL_IO_ERROR")
	return WorkflowActionError(
		"LOCAL_IO_ERROR",
		str(exc),
		recoverable=recoverable,
		recovery_action=("确认输出目录可写（不是同名的已有文件）、目录权限与磁盘空间正常，修正后重新执行 crawl resume"),
		replay_safe=False,
	)


def _crawl_hook_dir_error(exc: CrawlHookDirInvalid) -> WorkflowActionError:
	"""断点 Hook 目录坏是本地状态错误，和 CLI `crawl resume` 同口径（Issue #1586）。

	resume 没有 --hook-dir 可改，不能落到可重试的 INVALID_PARAM；重试同一断点
	只会复现。必须在 `except ValueError` 之前捕获，因为它是 ValueError 子类。
	"""
	recoverable, _ = error_contract_for_code("LOCAL_IO_ERROR")
	hook_dir = f" {exc.hook_dir}" if exc.hook_dir is not None else ""
	return WorkflowActionError(
		"LOCAL_IO_ERROR",
		str(exc),
		recoverable=recoverable,
		recovery_action=(
			f"停止自动重试：核对 Hook 目录{hook_dir}中的 7 个脚本与 SHA256SUMS，"
			"修复后重新执行 crawl resume；目录已迁移时用新的 --hook-dir 重新创建任务"
		),
		replay_safe=False,
		auto_retry=False,
	)


def _crawl_hook_error(exc: HookRegistrationError) -> WorkflowActionError:
	"""Hook 注入失败是确定性本地失败：重试或恢复只会复现（Issue #1408）。

	与 CLI 的 ``LOCAL_IO_ERROR`` 同口径，并保留具体目录供操作者修复。
	"""
	recoverable, _ = error_contract_for_code("LOCAL_IO_ERROR")
	return WorkflowActionError(
		"LOCAL_IO_ERROR",
		str(exc),
		recoverable=recoverable,
		recovery_action=(
			"停止自动重试：核对 Hook 目录中的 7 个脚本与 SHA256SUMS，修复后重新执行；"
			"目录已迁移时用新的 --hook-dir 重新创建采集任务"
		),
		replay_safe=False,
	)


def _crawl_session_auth_error(context: ActionContext, exc: CrawlSessionAuthError) -> WorkflowActionError:
	"""本地 Cookie 注入失败不是平台风控：按认证前置条件回报（Issue #1405）。"""
	return WorkflowActionError(
		"AUTH_REQUIRED",
		str(exc),
		recoverable=True,
		recovery_action=_hint_command(context, "login"),
	)


def _crawl_unavailable_error(context: ActionContext, exc: CrawlRuntimeUnavailable) -> WorkflowActionError:
	"""crawl 可选依赖缺失：不是网络抖动，重试或恢复只会复现，指向安装（Issue #1050）。"""
	configure_command = _hint_command(context, "crawl configure")
	return WorkflowActionError(
		"CRAWL_UNAVAILABLE",
		str(exc),
		recoverable=bool(ERROR_CODES["CRAWL_UNAVAILABLE"]["recoverable"]),
		recovery_action=f"安装 {extras_requirement('crawl')} 并执行 {configure_command}",
		# 可选依赖没装，重试同一动作必然复现；浏览器从未启动，无平台副作用
		# （Issue #1543）。
		auto_retry=False,
	)


def _crawl_browser_start_error(context: ActionContext, exc: CrawlBrowserStartError) -> WorkflowActionError:
	"""采集浏览器没能启动或附着：本次没有发出请求，指引检查 Chrome / 端口 / 路径。"""
	configure_command = _hint_command(context, "crawl configure")
	if exc.attached:
		recovery = (
			"上次保留的采集 Chrome 可能已关闭：重新执行同一动作即可新起浏览器；"
			f"仍失败时用 {configure_command} 核对 chrome_path，并确认调试端口 {exc.cdp_port} 未被其他程序占用"
		)
	else:
		recovery = (
			f"确认本机已安装 Chrome 并用 {configure_command} 设置 chrome_path，"
			f"确认调试端口 {exc.cdp_port} 未被其他程序占用、数据目录可写后重试；"
			f"依赖缺失时安装 {extras_requirement('crawl')}"
		)
	return WorkflowActionError(
		"CRAWL_UNAVAILABLE",
		str(exc),
		recoverable=True,
		recovery_action=recovery,
		# 新起浏览器失败时同一动作会再次撞上同一个本地原因；附着失败则可能只是
		# 上次保留的窗口已关闭，允许自动重试来新起浏览器（本次没有发出请求，
		# 重试安全，Issue #1553）。
		auto_retry=exc.attached,
	)


def _crawl_egress_error(exc: EgressContradictionError) -> WorkflowActionError:
	"""出口与声明的语言/时区矛盾：刻意拒绝启动，重试不会改变出口（Issue #1555）。"""
	return WorkflowActionError(
		"EGRESS_CONTRADICTION",
		str(exc),
		recoverable=False,
		recovery_action=(
			"保持客户端声明的语言/时区不变，换回与之一致的出口后重试；"
			"不需要出口一致性检查时撤销 BOSS_EGRESS_PROBE_URL。程序不会自动改写语言或时区"
		),
		details={"egress": exc.verdict.as_dict()},
		auto_retry=False,
	)


def _require_crawl_inputs_match_run(context: ActionContext, run_id: str, inputs: Mapping[str, Any]) -> None:
	"""显式 run_id 与本次的 query / city 不一致时拒绝续跑（Issue #1772）。

	续跑只沿用断点里的关键词与城市：静默接受会让 workflow 记录写着新关键词，
	实际向平台请求并导出的却是旧任务的数据。任务不存在或记录损坏交给
	``_crawl_resume`` 按既有口径回报。
	"""
	with CacheStore(context.data_dir / "cache" / "boss_agent.db") as cache:
		try:
			run = cache.get_crawl_run(run_id)
		except CacheRecordInvalid:
			return
	params = run.get("params") if run is not None else None
	if not isinstance(params, Mapping):
		return
	mismatched: list[str] = []
	query = inputs.get("query")
	stored_query = params.get("query")
	if isinstance(query, str) and query.strip() and isinstance(stored_query, str) and query.strip() != stored_query.strip():
		mismatched.append(f"query（任务为 {stored_query!r}，本次为 {query!r}）")
	city = inputs.get("city")
	stored_city = params.get("city_code")
	if isinstance(city, str) and city.strip() and isinstance(stored_city, str):
		city_code = CITY_CODES.get(city, city)
		if city_code != stored_city:
			mismatched.append(f"city（任务为 {stored_city}，本次为 {city!r}）")
	if mismatched:
		raise WorkflowActionError(
			"INVALID_PARAM",
			f"crawl 任务 {run_id} 的断点与本次输入不一致: {'；'.join(mismatched)}",
			recoverable=True,
			recovery_action="续跑原任务时去掉 query / city 或改用 goal=crawl_resume；采集新关键词时去掉 run_id 新建任务",
			auto_retry=False,
		)


def _crawl_start(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	# 恢复 waiting_input 时若已有内部 crawl run_id，必须续跑而不是新建采集。
	explicit_run_id = _optional_str(inputs.get("run_id") or inputs.get("crawl_run_id"))
	existing_run_id = explicit_run_id
	if existing_run_id is None:
		prior_step = prior.get("crawl_start")
		if isinstance(prior_step, Mapping):
			# 已完成的结果是 {data: {run_id}}；中断/超时留下的绑定是 {inner_run_id}（bind_inner_run）。
			# 两者都指向同一个内部 crawl，恢复时必须续跑而不是新建（Issue #882）。
			existing_run_id = _optional_str(prior_step.get("inner_run_id"))
			if existing_run_id is None:
				data = prior_step.get("data") if isinstance(prior_step.get("data"), Mapping) else prior_step
				if isinstance(data, Mapping):
					existing_run_id = _optional_str(data.get("run_id"))
	if existing_run_id:
		if explicit_run_id is not None:
			_require_crawl_inputs_match_run(context, existing_run_id, inputs)
		return _crawl_resume(context, {"run_id": existing_run_id, **dict(inputs)}, prior)

	if context.config is not None and not isinstance(context.config, Mapping):
		# config.json 由用户手工编辑：顶层或 crawl 段写成数组 / 字符串时，
		# dict() / .get() 抛出的 TypeError / AttributeError 会被 runner 兜成可重试的
		# NETWORK_ERROR，而重试同一条 run_id 永远复现（Issue #1330）。
		raise WorkflowActionError(
			"CONFIG_INVALID",
			f"本地配置必须是 JSON 对象，收到 {type(context.config).__name__}",
			recoverable=True,
			recovery_action=f"修正或删除 {context.data_dir / 'config.json'} 后重试",
			auto_retry=False,
		)
	crawl_value = context.config.get("crawl") if context.config is not None else None
	if crawl_value is not None and not isinstance(crawl_value, Mapping):
		raise WorkflowActionError(
			"CONFIG_INVALID",
			f"配置项 crawl 必须是 JSON 对象，收到 {type(crawl_value).__name__}",
			recoverable=True,
			recovery_action=(
				f"{_hint_command(context, 'config reset crawl')}；"
				f"或直接修正 {context.data_dir / 'config.json'} 的 crawl 段"
			),
			auto_retry=False,
		)
	crawl_config = dict(crawl_value or {})
	raw_query = inputs.get("query")
	city = str(_required(inputs, "city"))
	city_code = CITY_CODES.get(city, city if city.isascii() and city.isdigit() else None)
	if city_code is None:
		raise WorkflowActionError("INVALID_PARAM", f"未知城市: {city}")
	if raw_query is None or (isinstance(raw_query, str) and not raw_query.strip()):
		# CLI `crawl run` 对空 / 纯空白关键词一律拒绝：wizard 必须在写 crawl_runs
		# 与打开浏览器之前同口径收口（Issue #1582）。城市校验先行，保留既有
		# 「未知城市」错误码与文案（无 query 的旧调用也要先报城市问题）。
		raise WorkflowActionError(
			"INVALID_PARAM",
			"搜索关键词不能为空",
			recoverable=True,
			recovery_action="在 inputs 中提供非空白的 query 后重试",
		)
	if not isinstance(raw_query, str):
		raise WorkflowActionError("INVALID_PARAM", "搜索关键词必须是字符串")
	hook_profile = str(inputs.get("hook_profile") or "none")
	if hook_profile != "none":
		# 向导没有 hook-dir 输入，screenshot-full 在这里必然失败；提前按参数错误返回，
		# 而不是让 create_and_run 里的 ValueError 落成可重试的 NETWORK_ERROR。
		raise WorkflowActionError(
			"INVALID_PARAM",
			f"向导采集不支持 hook_profile={hook_profile!r}：workflow 没有 hook-dir 输入，请显式运行 boss crawl run/start",
			recovery_action=_hint_command(
				context,
				"crawl run <query> --city <城市> --hook-profile screenshot-full --hook-dir <脚本目录>",
			),
		)
	raw_pages = inputs.get("pages")
	pages = 5 if raw_pages is None else _positive_int(raw_pages, name="pages")
	with_detail = _bool_input(inputs, "with_detail")
	raw_port = crawl_config.get("cdp_port")
	try:
		settings = CrawlSettings(
			query=raw_query,
			city_code=city_code,
			pages=pages,
			with_detail=with_detail,
			profile_path=(context.data_dir / "crawl" / "chrome-profile").resolve(),
			chrome_path=crawl_chrome_path(crawl_config.get("chrome_path")),
			cdp_port=unused_local_port() if raw_port is None else crawl_cdp_port(raw_port),
			hook_profile=hook_profile,
			max_requests=crawl_int_setting(crawl_config, "max_requests", 20),
			max_details=crawl_int_setting(crawl_config, "max_details", 50),
			max_seconds=crawl_int_setting(crawl_config, "max_seconds", 600),
			max_retries=crawl_int_setting(crawl_config, "max_retries", 1, minimum=0),
		)
	except (TypeError, ValueError) as exc:
		# 手工编辑 config.json 写坏 crawl 段时不能落成可重试的 NETWORK_ERROR：
		# 重试同一个 run_id 永远修不好配置。与 CLI 路径一致，指向 config reset。
		raise WorkflowActionError(
			"CONFIG_INVALID",
			f"crawl 配置无效: {exc}",
			recoverable=True,
			recovery_action=_hint_command(context, "config reset crawl"),
			auto_retry=False,
		) from exc
	try:
		# 当前向导只允许 hook_profile=none，这里与 CLI 创建入口共用同一道只读
		# 预检（Issue #1348）：将来补上 hook-dir 输入时不会绕过目录内容校验。
		verify_hook_profile(settings.hook_profile, settings.hook_dir)
	except ValueError as exc:
		raise WorkflowActionError(
			"INVALID_PARAM",
			str(exc),
			recovery_action=_hint_command(
				context,
				"crawl run <query> --city <城市> --hook-profile screenshot-full --hook-dir <脚本目录>",
			),
		) from exc
	with CacheStore(context.data_dir / "cache" / "boss_agent.db") as cache:
		try:
			outcome = _crawl_service(context, cache).create_and_run(settings)
		except CrawlBusyError as exc:
			raise _crawl_busy_error(context, exc) from exc
		except CrawlProfileBusy as exc:
			raise _crawl_profile_busy_error(context, exc) from exc
		except CrawlBrowserStartError as exc:
			raise _crawl_browser_start_error(context, exc) from exc
		except EgressContradictionError as exc:
			raise _crawl_egress_error(exc) from exc
		except CrawlRuntimeUnavailable as exc:
			raise _crawl_unavailable_error(context, exc) from exc
		except CrawlExportError as exc:
			raise _crawl_export_error(exc) from exc
		except HookRegistrationError as exc:
			raise _crawl_hook_error(exc) from exc
		except CrawlSessionAuthError as exc:
			raise _crawl_session_auth_error(context, exc) from exc
		return _crawl_outcome_result(outcome, cache)


def _positive_int(value: Any, *, name: str) -> int:
	"""解析 workflow 输入的正整数；非法值按参数错误返回，不落成可重试的网络错误。

	输入来自 ``--input-json`` / MCP，bool 与小数不是页数：`int(True)` 是 1、
	`int(2.9)` 是 2。与同文件 `_coerce_int` 同口径拒绝，避免非法输入变成真实
	采集请求（Issue #1601）。整数字符串仍按既有兼容口径接受。
	"""
	if isinstance(value, bool) or (isinstance(value, float) and not value.is_integer()):
		raise WorkflowActionError(
			"INVALID_PARAM",
			f"{name} 必须是整数: {value!r}",
			recovery_action=f"传入 1 以上的整数 {name}",
		)
	try:
		parsed = int(value)
	# OverflowError：`1e400` 解析成 inf，`int(inf)` 抛错；调用方改输入即可修复，
	# 不能落成 INTERNAL_ERROR（Issue #1288）。
	except (TypeError, ValueError, OverflowError) as exc:
		raise WorkflowActionError(
			"INVALID_PARAM",
			f"{name} 必须是整数: {value!r}",
			recovery_action=f"传入 1 以上的整数 {name}",
		) from exc
	if parsed < 1:
		raise WorkflowActionError(
			"INVALID_PARAM",
			f"{name} 必须大于等于 1: {parsed}",
			recovery_action=f"传入 1 以上的整数 {name}",
		)
	return parsed


def _crawl_status(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	with CacheStore(context.data_dir / "cache" / "boss_agent.db") as cache:
		try:
			run_id = str(_required(inputs, "run_id"))
			data = crawl_status(cache, run_id, data_dir=context.data_dir)
			data = _attach_wizard_jobs(data, cache, run_id)
		except KeyError as exc:
			raise WorkflowActionError("CRAWL_RUN_NOT_FOUND", str(exc)) from exc
	return StepResult(data)


def _crawl_resume(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	run_id = str(_required(inputs, "run_id"))
	raw_pages = inputs.get("pages")
	pages = None if raw_pages is None else _positive_int(raw_pages, name="pages")
	with_detail = _bool_input(inputs, "with_detail")
	with CacheStore(context.data_dir / "cache" / "boss_agent.db") as cache:
		try:
			outcome = _crawl_service(context, cache, lazy=True).resume(
				run_id,
				pages=pages,
				with_detail=with_detail,
				clear_stop=True,
			)
		except KeyError as exc:
			raise WorkflowActionError("CRAWL_RUN_NOT_FOUND", f"未找到 crawl run: {run_id}") from exc
		except CacheRecordInvalid:
			# 断点记录损坏由 runner 统一按 CACHE_RECORD_INVALID 回报；它也是
			# ValueError 子类，不能下面的 Hook 参数分支吞掉。
			raise
		except CrawlHookDirInvalid as exc:
			# 断点里的 Hook 目录是本地状态，CLI 路径按 LOCAL_IO_ERROR 收口；
			# 必须排在 ValueError 分支之前（Issue #1586）。
			raise _crawl_hook_dir_error(exc) from exc
		except ValueError as exc:
			# 其他确定性输入错误按参数错误返回，不落成可重试的 NETWORK_ERROR。
			raise WorkflowActionError(
				"INVALID_PARAM",
				str(exc),
				recovery_action="修正参数后重试，或重新创建采集任务",
			) from exc
		except CrawlBusyError as exc:
			raise _crawl_busy_error(context, exc) from exc
		except CrawlProfileBusy as exc:
			raise _crawl_profile_busy_error(context, exc) from exc
		except CrawlBrowserStartError as exc:
			raise _crawl_browser_start_error(context, exc) from exc
		except EgressContradictionError as exc:
			raise _crawl_egress_error(exc) from exc
		except CrawlRuntimeUnavailable as exc:
			raise _crawl_unavailable_error(context, exc) from exc
		except CrawlExportError as exc:
			raise _crawl_export_error(exc) from exc
		except HookRegistrationError as exc:
			raise _crawl_hook_error(exc) from exc
		except CrawlSessionAuthError as exc:
			raise _crawl_session_auth_error(context, exc) from exc
		return _crawl_outcome_result(outcome, cache)


def _crawl_stop(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	run_id = str(_required(inputs, "run_id"))
	with CacheStore(context.data_dir / "cache" / "boss_agent.db") as cache:
		run = cache.get_crawl_run(run_id)
		if run is None:
			raise WorkflowActionError("CRAWL_RUN_NOT_FOUND", f"未找到 crawl run: {run_id}")
		# 与 `boss crawl stop` 同口径（Issue #1574 #1786）：终态任务上的停止请求
		# 不会生效，继续报 stop_requested 会让调用方以为任务已被叫停。
		status = run["status"]
		if status not in ("queued", "running"):
			status_command = _hint_command(context, f"crawl status {shell_argument(run_id)}")
			results_command = _hint_command(context, f"crawl results {shell_argument(run_id)}")
			raise WorkflowActionError(
				"CRAWL_RUN_FINISHED",
				f"crawl 任务已结束（{status}），停止请求没有生效",
				recovery_action=f"用 {status_command} 查看最终状态，或 {results_command} 读取已采集结果；需要重新采集时新建 crawl 任务",
				auto_retry=False,
			)
		cache.request_crawl_stop(run_id)
	return StepResult({"run_id": run_id, "status": "stop_requested"})


def _recruiter_call(
	context: ActionContext,
	call: Callable[[Any], dict[str, Any]],
	fallback: str,
	*,
	list_keys: tuple[str, ...] = (),
	write: bool = False,
	check_command: str | None = None,
) -> StepResult:
	with context.recruiter_platform() as platform:
		if write:
			data = _write_call(context, platform, lambda: call(platform), fallback, check_command=check_command)
		else:
			data = _unwrap(platform, call(platform), fallback, context=context, check_command=check_command)
	payload: dict[str, Any] = {"result": data}
	if list_keys:
		items = _extract_list_items(data, list_keys)
		payload = _with_items(payload, items)
	return StepResult(payload)


def _recruiter_candidates(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	return _recruiter_call(
		context,
		lambda platform: execute_recruiter_candidate_search(platform, inputs),
		"候选人搜索失败",
		list_keys=("geekList", "items", "list", "result"),
	)


def _recruiter_applications(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	return _recruiter_call(
		context,
		lambda platform: platform.friend_list(
			page=_int_input(inputs, "page", 1, minimum=1),
			label_id=_int_input(inputs, "label_id", 0),
			job_id=_optional_str(inputs.get("job_id")),
		),
		"投递申请获取失败",
		list_keys=("friendList", "result", "list", "items"),
	)


def _recruiter_resume(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	geek_id = str(_required(inputs, "geek_id"))
	job_id = str(_required(inputs, "job_id"))
	security_id = _optional_str(inputs.get("security_id"))
	return _recruiter_call(
		context,
		lambda platform: platform.view_geek(geek_id, job_id, security_id),
		"候选人简历获取失败",
		# view_geek 声明 effect: unknown，无法证明没有副作用；未知结果不能自动重放。
		write=True,
	)


def _recruiter_chat(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	return _recruiter_applications(context, inputs, prior)


def _recruiter_last_messages(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	with context.recruiter_platform() as platform:
		friend_data = platform_data_dict(
			_unwrap(
				platform,
				platform.friend_list(
					page=_int_input(inputs, "page", 1, minimum=1),
					label_id=_int_input(inputs, "label_id", 0),
					job_id=_optional_str(inputs.get("job_id")),
				),
				"沟通列表获取失败",
				context=context,
			)
		)
		friend_items = next(
			(value for key in ("friendList", "result", "list") if isinstance((value := friend_data.get(key)), list)),
			[],
		)
		friend_ids: list[int] = []
		for item in friend_items:
			if not isinstance(item, dict):
				continue
			for key in ("friendId", "friend_id", "uid", "gid"):
				value = item.get(key)
				if value in (None, ""):
					continue
				try:
					friend_id = int(str(value))
				except (TypeError, ValueError):
					continue
				if friend_id not in friend_ids:
					friend_ids.append(friend_id)
				break
		if not friend_ids:
			return StepResult({"friend_ids": [], "messages": [], "items": [], "total": 0})
		data = _unwrap(platform, platform.last_messages(friend_ids), "最近消息获取失败", context=context)
	items = _extract_list_items(data, ("messages", "messageList", "list", "items", "result"))
	if not items and isinstance(data, Mapping):
		# Some APIs return {friendId: lastMsg} maps — flatten for TUI.
		for key, value in data.items():
			if isinstance(value, Mapping):
				row = dict(value)
				row.setdefault("friend_id", key)
				items.append(row)
			elif value not in (None, ""):
				items.append({"friend_id": key, "lastMsg": value})
	return StepResult(_with_items({"friend_ids": friend_ids, "result": data, "messages": items}, items))


def _recruiter_reply(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	friend_id = _required_int(inputs, "friend_id", minimum=1)
	message = str(_required(inputs, "message"))
	return _recruiter_call(
		context,
		lambda platform: platform.send_message_by_friend(friend_id, message),
		"消息发送失败",
		write=True,
		check_command=f"hr chatmsg {friend_id}",
	)


def _recruiter_chat_history(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	friend_id = _required_int(inputs, "friend_id", minimum=1)
	return _recruiter_call(
		context,
		lambda platform: platform.chat_history(
			friend_id,
			count=_int_input(inputs, "count", 20, minimum=1),
			max_msg_id=_int_input(inputs, "max_msg_id", 0) if inputs.get("max_msg_id") else None,
		),
		"聊天记录获取失败",
		list_keys=("messageList", "messages", "list", "items", "result"),
	)


def _recruiter_request_resume(
	context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]
) -> StepResult:
	friend_id = _required_int(inputs, "friend_id", minimum=1)
	return _recruiter_call(
		context,
		lambda platform: platform.exchange_request_by_friend(friend_id, exchange_type=4),
		"附件简历请求失败",
		write=True,
		check_command=f"hr chatmsg {friend_id}",
	)


def _recruiter_exchange_contact(
	context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]
) -> StepResult:
	friend_id = _required_int(inputs, "friend_id", minimum=1)
	exchange_type = _exchange_type(inputs)
	return _recruiter_call(
		context,
		lambda platform: platform.exchange_request_by_friend(friend_id, exchange_type=exchange_type),
		"联系方式交换请求失败",
		write=True,
		check_command=f"hr chatmsg {friend_id}",
	)


def _recruiter_jobs_list(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	return _recruiter_call(
		context,
		lambda platform: platform.list_jobs(),
		"职位列表获取失败",
		list_keys=("data", "jobs", "jobList", "items", "list", "result"),
	)


def _recruiter_jobs_detail(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	job_id = str(_required(inputs, "job_id"))
	return _recruiter_call(context, lambda platform: platform.job_detail(job_id), "职位详情获取失败")


def _recruiter_jobs_online(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	job_id = str(_required(inputs, "job_id"))
	return _recruiter_call(
		context, lambda platform: platform.job_online(job_id), "职位上线失败", write=True, check_command="hr jobs list"
	)


def _recruiter_jobs_offline(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	job_id = str(_required(inputs, "job_id"))
	return _recruiter_call(
		context, lambda platform: platform.job_offline(job_id), "职位下线失败", write=True, check_command="hr jobs list"
	)


DEFAULT_ACTIONS: dict[str, Action] = {
	"auth_status": _auth_status,
	"candidate_search": _candidate_search,
	"candidate_recommend": _candidate_recommend,
	"candidate_detail": _candidate_detail,
	"candidate_apply": _candidate_apply,
	"candidate_greet": _candidate_greet,
	"candidate_exchange": _candidate_exchange,
	"candidate_mark": _candidate_mark,
	"candidate_chat": _candidate_chat,
	"candidate_chat_history": _candidate_chat_history,
	"candidate_pipeline": _candidate_pipeline,
	"candidate_digest": _candidate_digest,
	"local_shortlist": _local_shortlist,
	"local_resumes": _local_resumes,
	"ai_assist": _ai_assist,
	"candidate_export": _candidate_export,
	"candidate_watch": _candidate_watch,
	"crawl_start": _crawl_start,
	"crawl_status": _crawl_status,
	"crawl_resume": _crawl_resume,
	"crawl_stop": _crawl_stop,
	"recruiter_candidates": _recruiter_candidates,
	"recruiter_applications": _recruiter_applications,
	"recruiter_resume": _recruiter_resume,
	"recruiter_chat": _recruiter_chat,
	"recruiter_last_messages": _recruiter_last_messages,
	"recruiter_reply": _recruiter_reply,
	"recruiter_chat_history": _recruiter_chat_history,
	"recruiter_exchange_contact": _recruiter_exchange_contact,
	"recruiter_request_resume": _recruiter_request_resume,
	"recruiter_jobs_list": _recruiter_jobs_list,
	"recruiter_jobs_detail": _recruiter_jobs_detail,
	"recruiter_jobs_online": _recruiter_jobs_online,
	"recruiter_jobs_offline": _recruiter_jobs_offline,
}
