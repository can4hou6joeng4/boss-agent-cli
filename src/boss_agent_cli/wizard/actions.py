"""Reusable candidate, recruiter and local workflow actions."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Mapping, NoReturn, cast

from boss_agent_cli.api._base_client import parameter_status_code
from boss_agent_cli.api.cdp_risk_lock import unlock_operator_actions
from boss_agent_cli.api.client import PlatformRateLimitError, PlatformRiskError
from boss_agent_cli.api.models import (
	dict_items,
	field_dict,
	field_list,
	field_text,
	platform_data_dict,
	scalar_or_none,
)
from boss_agent_cli.config import DEFAULT_REQUEST_DELAY
from boss_agent_cli.automation.storage import AutomationStore
from boss_agent_cli.auth.identity import account_scope
from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.api.endpoints import CITY_CODES
from boss_agent_cli.ai.config import AIConfigStore, ai_float_setting, ai_int_setting
from boss_agent_cli.ai.service import AIService, AIServiceError, recovery_action_for
from boss_agent_cli.cache.store import (
	CacheRecordInvalid,
	CacheStore,
	SavedSearchParamsError,
	saved_search_name_error,
)
from boss_agent_cli.platforms.factory import build_platform_instance, build_recruiter_platform_instance
from boss_agent_cli.services.candidate_actions import (
	CandidateActionBlocked,
	CandidateActionNotRecorded,
	CandidateActionReleaseFailed,
	candidate_action_provably_unsent,
	candidate_release_recovery_action,
	exception_reports_request_unsent,
	run_candidate_action,
)
from boss_agent_cli.services.chat_utils import (
	FROM_FILTER,
	RELATION_LABELS,
	chat_cutoff_ts,
	matches_chat_filters,
	message_list,
	normalize_chat_message,
	relation_text,
)
from boss_agent_cli.services.contact_lookup import (
	FriendListShapeError,
	FriendLookupLimitExceeded,
	find_friend,
	friend_display_name,
	friend_security_id,
	friend_uid,
)
from boss_agent_cli.services.friend_list_pages import collect_friend_list_page
from boss_agent_cli.services.output_path import OutputPathProblem, inspect_output_path, prepare_output_path
from boss_agent_cli.services.pagination import (
	DEFAULT_EXPORT_COUNT,
	MAX_TRUSTED_COUNT,
	MAX_TRUSTED_PAGE,
	export_page_budget,
	list_pagination,
)
from boss_agent_cli.crawler.operations import crawl_status
from boss_agent_cli.crawler.service import (
	CrawlBudget,
	CrawlBusyError,
	CrawlService,
	CrawlSettings,
	crawl_cdp_port,
	crawl_chrome_path,
	crawl_execution_lock,
	crawl_int_setting,
	crawl_run_awaiting_worker,
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
from boss_agent_cli.services.labels import LABEL_MAP, brief_label, parse_label_id
from boss_agent_cli.services.recommend_items import build_recommend_items
from boss_agent_cli.services.recruiter_chat_summary import enrich_friend_summaries, normalize_last_message
from boss_agent_cli.services.recruiter_last_messages import (
	DEFAULT_SUMMARY_LIMIT,
	MAX_SUMMARY_LIMIT,
	fetch_last_messages,
	friend_id_for,
	friend_sources,
)
from boss_agent_cli.services.saved_searches import SEARCH_PARAM_KEYS, validate_stored_search_params
from boss_agent_cli.services.search_order import SEARCH_SORT_MODES, sort_search_items
from boss_agent_cli.services.text_files import is_utf8_encodable
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
from boss_agent_cli.match_score import score_job_dict
from boss_agent_cli.output import Logger
from boss_agent_cli.pipeline_state import build_pipeline_items, select_follow_up_candidates
from boss_agent_cli.resume.models import resume_to_text
from boss_agent_cli.resume.store import ResumeStore, resume_name_input_error
from boss_agent_cli.schema.error_codes import ERROR_CODES
from boss_agent_cli.search_filters import (
	SearchFilterCriteria,
	SearchPipelinePlatformError,
	SearchUrlParseError,
	active_filter_hint,
	client_is_browser_only,
	detail_filter_max_pages,
	ensure_supported_city,
	ensure_text_encodable,
	parse_boss_search_url,
	resolve_active_level,
	resolve_search_code_params,
	resolve_welfare_keywords,
	run_search_pipeline,
)
from boss_agent_cli.wizard.models import StepResult, WorkflowInputError, WorkflowStatus
from boss_agent_cli.wizard.runner import Action, WorkflowActionError, WorkflowControl

PlatformFactory = Callable[[str, AuthManager, tuple[float, float], str | None, str | None], Any]


def _hint_command(context: ActionContext, command: str, *, role: str | None = None) -> str:
	"""动作层恢复命令必须定位触发它的数据目录与平台。

	wizard 的 headless / MCP 调用可以带 ``--data-dir``：恢复指引写成裸 ``boss ...``
	时，Agent 照抄会把新 AI 密钥、``config reset``、watch 删除重建或 crawl 重建
	落到默认目录，原目录的问题原样保留（与 #913 / #919 的命令层迁移同一接缝）。

	``role`` 只给不在所属组内的命令（如 agent 组）：招聘者照抄不带角色的
	``agent run --resume`` 会在默认候选人角色下读取另一套本地状态（Issue #3061）。
	"""
	return boss_command(command, platform_name=context.platform, data_dir=context.data_dir, role=role)


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
		f"或 [[标签, [关键词...]], ...] 形状；收到 {_brief_value(raw)}",
		recoverable=recoverable,
		recovery_action=recovery,
	)


def _parse_welfare_input(raw: Any) -> list[tuple[str, list[str]]]:
	"""把 wizard 的 ``welfare`` 输入归一成 pipeline 的 ``(label, keywords)`` 形状。

	数组元素必须是字符串：``str(item)`` 会把 123 / 嵌套数组变成 ``"123"`` /
	``"['双休']"`` 这类永不命中的标签，搜索照常报成功（#2421 给 watch add 修过
	同一模式；搜索 / 导出入口此前仍在静默转换，Issue #2654）。非字符串、非
	数组的取值（false / 0 / 对象）也不能被入口的真值判断吞成「不筛选」。
	"""
	if isinstance(raw, str):
		labels = [item.strip() for item in raw.split(",")]
	elif isinstance(raw, (list, tuple)):
		for index, item in enumerate(raw):
			if not isinstance(item, str):
				raise WorkflowActionError(
					"INVALID_PARAM",
					f"welfare 的第 {index} 个元素必须是字符串（收到 {type(item).__name__}）",
					recoverable=True,
					recovery_action='把 welfare 写成字符串数组（如 ["双休", "五险一金"]）后重试',
				)
		labels = [item.strip() for item in raw]
	else:
		raise WorkflowActionError(
			"INVALID_PARAM",
			f"welfare 必须是字符串或字符串数组（收到 {type(raw).__name__}）",
			recoverable=True,
			recovery_action="把 welfare 写成逗号分隔字符串或字符串数组后重试；不按福利筛选时省略该字段",
		)
	return [(label, resolve_welfare_keywords(label)) for label in labels if label]


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


def _detail_filter_inputs(
	inputs: Mapping[str, Any],
) -> tuple[list[tuple[str, list[str]]] | None, str | None]:
	"""把 wizard 的 ``welfare`` / ``welfare_conditions`` / ``active`` 归一。

	search / watch / export 共用同一口径（Issue #2706）；export 还需要在调用
	管线前按归一后的条件决定默认 count 与页数上限（Issue #2716）。
	"""
	welfare_conditions = inputs.get("welfare_conditions")
	raw_welfare = inputs.get("welfare")
	if welfare_conditions is None and raw_welfare is not None and raw_welfare != "":
		welfare_conditions = _parse_welfare_input(raw_welfare)
	else:
		welfare_conditions = _normalize_welfare_conditions(welfare_conditions)
	try:
		active = resolve_active_level(_optional_str(inputs.get("active")))
	except ValueError as exc:
		raise WorkflowActionError(
			"INVALID_PARAM", str(exc), recoverable=True, recovery_action="修正 active 取值后重试"
		) from exc
	return welfare_conditions, active


def execute_candidate_search(
	platform: Any,
	cache: CacheStore,
	logger: Logger,
	inputs: Mapping[str, Any],
	*,
	pipeline: Callable[..., Any] = run_search_pipeline,
	limit: int | None = None,
	continue_when_has_more_unknown: bool = False,
	with_score: bool = False,
) -> Any:
	"""Shared platform search call used by the command and workflow action.

	``with_score`` 与顶层 `boss search --with-score` 同口径：用同一次搜索的
	criteria 重算分数并附加匹配原因（Issue #2665）。缺省保持管线原始 items，
	命令层与向导都在此之后按自己的顺序排序 / 缓存。
	"""
	# query 类型错误不能经 str() 静默变成空关键词或容器的 repr 文本：
	# false / 0 会变成无关键词搜索，对象会按字面去搜（Issue #2009）。
	query = _optional_str_strict(inputs, "query") or ""
	# 与 CLI `search` / `export` / `watch add` 同一入口判定（Issue #2528）：
	# 孤立代理项进入 httpx 的 URL 参数时抛 UnicodeEncodeError，会被兜成带
	# 磁盘指引的 LOCAL_IO_ERROR；同一条输入重试多少次都是同一个结果。
	try:
		ensure_text_encodable("搜索关键词", query)
	except ValueError as exc:
		raise WorkflowActionError(
			"INVALID_PARAM",
			str(exc),
			recoverable=True,
			recovery_action="换成只含普通 Unicode 文本的搜索关键词后重试",
		) from exc
	welfare_conditions, active = _detail_filter_inputs(inputs)
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
		query=query,
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
	if continue_when_has_more_unknown:
		# 导出场景：平台缺失 hasMore 时按 count / max_pages 预算继续扫描，
		# 与顶层 `boss export` 同口径（Issue #2657）。搜索类调用保持默认的
		# 保守停止，不向测试替身传入陌生关键字。
		extra["continue_when_has_more_unknown"] = True
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
	result = pipeline(
		platform,
		cache,
		logger,
		criteria=criteria,
		start_page=_int_input(inputs, "page", 1, minimum=1, maximum=MAX_TRUSTED_PAGE),
		max_pages=_int_input(inputs, "max_pages", detail_filter_max_pages(welfare_conditions, active), minimum=1),
		welfare_conditions=welfare_conditions,
		before_list_request=inputs.get("before_list_request"),
		limit=limit,
		**extra,
	)
	if with_score:
		# 与命令层同一份评分实现（`score_job_dict`）：管线内嵌的 match_score
		# 不带原因，命令层与向导都需要额外的 match_reasons / mismatch_reasons。
		# 结果对象是本次调用新建的，就地替换 items 不会与调用方共享旧列表。
		result.items = [score_job_dict(item, criteria=criteria, expect_data=None) for item in result.items]
	return result


def execute_recruiter_candidate_search(platform: Any, inputs: Mapping[str, Any]) -> dict[str, Any]:
	"""Shared recruiter search call used by the command and workflow action."""
	# platform 按鸭子类型接收真实适配器与测试替身，返回的平台响应字典由调用方
	# 按 API 响应契约读取；这里显式收敛 Any，避免整条调用链丢失类型。
	# 字符串筛选项与求职者侧同口径走 strict：false / 0 / 容器不能经 str()
	# 静默变成空关键词或 repr 文本后真实发往平台（Issue #2009、#2347）。
	return cast(
		"dict[str, Any]",
		platform.search_geeks(
			_optional_str_strict(inputs, "query") or "",
			city=_optional_str_strict(inputs, "city") or None,
			page=_int_input(inputs, "page", 1, minimum=1),
			job_id=_optional_str_strict(inputs, "job_id") or None,
			experience=_optional_str_strict(inputs, "experience") or None,
			degree=_optional_str_strict(inputs, "degree") or None,
			age=_optional_str_strict(inputs, "age") or None,
			school_level=_optional_str_strict(inputs, "school_level") or None,
			activeness=_optional_str_strict(inputs, "activeness") or None,
			source=_optional_str_strict(inputs, "source") or None,
			select=_bool_input(inputs, "select"),
			salary=_optional_str_strict(inputs, "salary") or None,
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


def _pagination_evidence(data: Any, page: int, items: list[dict[str, Any]]) -> dict[str, Any]:
	"""步骤 data 的分页证据：与 CLI 信封同词汇、同实现（Issue #2670 / #2900）。

	直接复用 ``services.pagination.list_pagination``：平台返回有效 ``page`` 时
	以平台为准（并钳制到可信上限），否则回显钳制后的请求页；``has_more`` 只认
	真正的布尔 ``hasMore``，缺失或类型漂移（字符串 ``"true"``、数字 ``1``）都是
	``null``，不能据此停止翻页；``total`` 是本次返回条数。

	此前这里是固定回显请求页的本地副本，平台纠正页码后向导仍报请求页，与 CLI
	信封产生两套证据（Issue #2900）。
	"""
	return list_pagination(data, page=page, rows=items)


def _optional_str(value: Any) -> str | None:
	return str(value) if value is not None and value != "" else None


def _brief_value(value: Any, limit: int = 60) -> str:
	"""错误消息里回显的输入值：repr 后截断，信封与断点不随输入规模膨胀（Issue #1909）。"""
	text = repr(value)
	if len(text) <= limit:
		return text
	return f"{text[:limit]}…（共 {len(text)} 字符）"


def _required_str(inputs: Mapping[str, Any], name: str) -> str:
	"""读取必填字符串输入：容器 / 数字 / 布尔不能经 ``str()`` 变成 repr 发给真人。"""
	raw = _required(inputs, name)
	if not isinstance(raw, str):
		raise WorkflowInputError(f"wizard 输入 {name} 必须是字符串，收到 {_brief_value(raw)}")
	return raw


def _required_nonblank_value(inputs: Mapping[str, Any], name: str) -> str:
	"""必填且非空白的原始字符串：只校验、不 strip。

	CLI 的位置参数只判断 ``.strip()``、原样透传（`detail` / `hr jobs` /
	`hr reply`），向导必须同口径：空白值在入口报 ``INVALID_PARAM``，非空白值
	不被悄悄改写（Issue #2516）。
	"""
	value = _required_str(inputs, name)
	if not value.strip():
		raise WorkflowActionError(
			"INVALID_PARAM",
			f"wizard 输入 {name} 不能只有空白字符",
			recoverable=True,
			recovery_action=f"在 inputs 中提供有效的 {name} 后重试",
		)
	return value


def _reject_unencodable_inputs(**fields: str | None) -> None:
	"""标识 / 自由文本必须能按严格 UTF-8 编码（Issue #3017）。

	客户端在发出第一个字节之前就会因孤立代理项抛 ``RequestTextEncodingError``，
	但写动作的「结果未知」标记已经落盘，runner 的通用异常分支会先把确定未发出的
	请求报成 ``NETWORK_ERROR`` 结果未知并禁止 ``--resume`` 重放。与 CLI 命令入口
	同口径，在构造平台与 ``begin_write`` 之前按参数错误拒绝。
	"""
	bad = [
		name
		for name, value in fields.items()
		if value is not None and not is_utf8_encodable(value)
	]
	if not bad:
		return
	raise WorkflowActionError(
		"INVALID_PARAM",
		f"{' / '.join(bad)} 含有无法按 UTF-8 编码的字符（如孤立代理项），请求未发出",
		recoverable=True,
		recovery_action=(
			"去掉无效字符后新建 workflow（同一 run 的输入已绑定，不能原地改写）；"
			"编号建议重新从 HR 沟通列表输出中取"
		),
		replay_safe=True,
	)


def _required_nonblank_str(inputs: Mapping[str, Any], name: str) -> str:
	"""读取必填的非空白字符串。

	``CacheStore.claim_candidate_action`` 对纯空白 job_id 返回 ``"pending"``
	（Issue #1645 的空 ID 保护），预约层会把它当成「上一次动作仍在」并给出
	结果未知指引。wizard 必须在预约前把空白参数报成 INVALID_PARAM，让 Agent
	修正输入而不是去平台核对一个不存在的动作（Issue #2320）。
	"""
	return _required_nonblank_value(inputs, name).strip()


def _optional_str_strict(inputs: Mapping[str, Any], name: str) -> str | None:
	"""读取可选字符串输入：缺省 / null 为空，其余非字符串点名报错。"""
	if name not in inputs:
		return None
	raw = inputs[name]
	if raw is None:
		return None
	if not isinstance(raw, str):
		raise WorkflowInputError(f"wizard 输入 {name} 必须是字符串，收到 {_brief_value(raw)}")
	return raw


def _choice_input(inputs: Mapping[str, Any], name: str, default: str) -> str:
	"""读取枚举型字符串输入：缺省 / null / 空串用默认值，其余非字符串点名报错。

	``str(inputs.get(name) or default)`` 会让 ``false`` / ``0`` / ``[]`` 静默落到
	默认值（``{"action": 0}`` 变成 list），其他类型则经 ``str()`` 变成 repr 再报
	「未知取值」，看不出是类型问题（Issue #2701）。取值域仍由调用方校验。
	"""
	return _optional_str_strict(inputs, name) or default


def _optional_nonblank_str(
	inputs: Mapping[str, Any],
	name: str,
	*,
	recovery_action: str | None = None,
) -> str | None:
	"""读取可选的非空白字符串：缺省 / null 为空，显式空白按参数错误拒绝。

	顶层 `hr candidates` / `hr applications` / `hr chat` / `hr last-messages`
	已在平台调用前拒绝显式空筛选项（Issue #2600）；向导入口若把空串归一成
	``None``，同一条件会静默按「不筛选」执行，Agent 会以为过滤已生效
	（Issue #2618）。同样的口径适用于自由文本字段：`boss greet --message ""`
	会报参数错误，向导 / MCP 不能静默退回默认招呼语（Issue #2623）。
	非空白值原样透传，不 strip。
	"""
	raw = _optional_str_strict(inputs, name)
	if raw is None:
		return None
	if not raw.strip():
		raise WorkflowActionError(
			"INVALID_PARAM",
			f"wizard 输入 {name} 不能只有空白字符",
			recoverable=True,
			recovery_action=recovery_action
			or f"在 inputs 中补上有效的 {name} 后重试；不按该条件筛选时省略该字段",
		)
	return raw


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


def _preset_invalid_error(context: ActionContext, name: str, detail: str) -> WorkflowActionError:
	"""预设记录损坏 / 失效：与 `watch run` 的 SAVED_SEARCH_INVALID 同口径，
	删除重建命令指向 preset（而不是 watch）。"""
	return WorkflowActionError(
		"SAVED_SEARCH_INVALID",
		detail,
		recoverable=True,
		recovery_action=(
			f"{_hint_command(context, f'preset remove {positional_hint(name)}')}；"
			f"再用 {_hint_command(context, f'preset add {positional_hint(name)} <关键词>')} 重建"
		),
	)


def _expand_search_sources(
	context: ActionContext,
	cache: CacheStore,
	inputs: Mapping[str, Any],
) -> dict[str, Any]:
	"""展开 `search_url` / `preset`，与顶层 `search` / `export` 同口径（Issue #2685）。

	CLI 允许 `search --url` / `search --preset` / `export --url` 不给关键词；
	向导 / MCP 此前只认 `query`，把这两条入口挡在 action 之外。这里把来源
	展开成 `execute_candidate_search` 认识的输入：

	- preset 只补未提供的键（显式空串也算提供，保持「不筛选」语义）；
	- search_url 的关键词只在没有关键词时补上，白名单 code 参数并入
	  `raw_params`（显式筛选的 code 参数随后覆盖），page 只在未显式给 page
	  时采用；
	- 空白 query 与 CLI 一样视为未提供，由最后的关键词校验决定是否拒绝。
	"""
	merged = dict(inputs)
	preset = _optional_nonblank_str(merged, "preset")
	if preset is not None:
		if problem := saved_search_name_error(preset):
			raise WorkflowActionError(
				"INVALID_PARAM",
				f"预设{problem}",
				recoverable=True,
				recovery_action="换一个只含普通 Unicode 文本的预设名后重试；用 preset list 查看已有预设",
			)
		try:
			record = cache.get_saved_search(preset)
		except (SavedSearchParamsError, CacheRecordInvalid) as exc:
			raise _preset_invalid_error(context, preset, str(exc)) from exc
		if record is None:
			raise WorkflowActionError(
				"SAVED_SEARCH_NOT_FOUND",
				f"未找到 preset: {preset}",
				recoverable=True,
				recovery_action="用 preset list 查看已保存的预设后重试，或省略 preset 直接传入筛选条件",
			)
		params = record["params"]
		# 与 `watch run` 共用读取侧校验：旧版本 / 手工编辑写入的空白关键词、
		# 类型漂移与未知标签不能以空泛搜索报成功（Issue #1338、#1798、#2529）。
		try:
			validate_stored_search_params(params)
		except ValueError as exc:
			raise _preset_invalid_error(context, preset, f"预设 {preset!r} 的参数已失效：{exc}") from exc
		for key in SEARCH_PARAM_KEYS:
			if merged.get(key) is None:
				merged[key] = params.get(key)
	search_url = _optional_nonblank_str(merged, "search_url")
	if search_url is not None:
		try:
			parsed = parse_boss_search_url(search_url)
		except SearchUrlParseError as exc:
			raise WorkflowActionError(
				"INVALID_PARAM",
				str(exc),
				recoverable=True,
				recovery_action="换成完整的 BOSS 直聘搜索页链接，或改为提供搜索关键词 / preset 后重试",
			) from exc
		raw_query = _optional_str_strict(merged, "query")
		if parsed.query and (raw_query is None or not raw_query.strip()):
			merged["query"] = parsed.query
		if parsed.page is not None and merged.get("page") is None:
			merged["page"] = parsed.page
		raw_params: dict[str, Any] = dict(_mapping_input(merged, "raw_params"))
		# 链接参数先并入、显式 raw_params 后覆盖：CLI `search --url` 先
		# `raw_params.update(parsed_url.params)`，再用显式选项转换结果 update，
		# 显式筛选优先（Issue #2690）。反过来会让 Agent 显式指定的筛选被链接
		# 悄悄改写，结果集合与请求不符。
		merged["raw_params"] = {**parsed.params, **raw_params}
	return merged


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
	raise WorkflowInputError(f"wizard 输入 {name} 必须是布尔值，收到 {_brief_value(raw)}")


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
		raise WorkflowInputError(f"wizard 输入 {name} 必须是 JSON 对象，收到 {_brief_value(raw)}")
	return dict(raw)


def _exchange_type(inputs: Mapping[str, Any]) -> int:
	"""解析交换联系方式类型：只有精确的 ``phone`` / ``wechat`` 才合法。

	wizard inputs 来自 ``--input-json`` / MCP，不经过 Click 的 ``Choice``，catalog
	也只检查必填项。原先除 ``wechat`` 外的任何值都会静默按手机号处理（`"wecaht"`、
	`"PHONE"`、`"phone "`），发出与用户意图不同的隐私授权请求（Issue #905）。
	未提供类型时按 schema 默认 ``phone``；显式空串 / ``null`` 与 CLI 的
	``--type ""`` 同口径，必须报参数错误，绝不能静默按默认发出授权请求（Issue #2943）。
	"""
	if "type" not in inputs:
		return 1
	raw = inputs["type"]
	if raw == "wechat":
		return 2
	if raw == "phone":
		return 1
	raise WorkflowActionError("INVALID_PARAM", f"未知的交换类型: {_brief_value(raw)}（可选: phone、wechat）")


def _coerce_int(raw: Any, name: str) -> int:
	"""把 wizard 输入转成整数；类型错误必须点名字段。

	wizard inputs 来自 `--input-json` / MCP，写成非数字字符串时 `int()` 抛的裸
	ValueError 会落到 runner 的通用兜底，被映射成 NETWORK_ERROR +「重试当前
	run_id」——同一条输入重试多少次都是同一个错误。bool 与小数同样不是「页码 /
	条数」的合法取值：`int(True)` 会静默当成 1，`int(2.5)` 会静默截断成 2。
	"""
	if isinstance(raw, bool) or (isinstance(raw, float) and not raw.is_integer()):
		raise WorkflowInputError(f"wizard 输入 {name} 必须是整数，收到 {_brief_value(raw)}")
	try:
		return int(raw)
	except (TypeError, ValueError) as exc:
		raise WorkflowInputError(f"wizard 输入 {name} 必须是整数，收到 {_brief_value(raw)}") from exc


def _check_minimum(value: int, raw: Any, name: str, minimum: int) -> int:
	if value < minimum:
		raise WorkflowInputError(f"wizard 输入 {name} 必须是不小于 {minimum} 的整数，收到 {_brief_value(raw)}")
	return value


def _check_maximum(value: int, raw: Any, name: str, maximum: int) -> int:
	if value > maximum:
		raise WorkflowInputError(f"wizard 输入 {name} 必须是不大于 {maximum} 的整数，收到 {_brief_value(raw)}")
	return value


def _int_input(
	inputs: Mapping[str, Any],
	name: str,
	default: int,
	*,
	minimum: int = 0,
	maximum: int | None = None,
) -> int:
	"""读取整数输入并校验下界（可选上界）。

	下游把这些值直接当页码 / 翻页次数 / 条数用：`max_pages=-3` 会让
	`range(max_pages)` 一次都不循环，workflow 以「成功」收尾但结果为空；
	负数页码则会原样作为 page 参数发给平台。这类输入重试多少次都是同一个结果。

	`maximum` 只在调用方对应的 CLI 选项本身有上限时传（如求职者分页命令的
	`IntRange(max=MAX_TRUSTED_PAGE)`）：向导 / MCP 绕过 Click，不能把越界页码
	原样发往平台（Issue #2625）。
	"""
	raw = inputs.get(name)
	# 只有「没给」才用默认值；显式写的 0 不能被当成缺省悄悄换成默认值
	# （`0 or default` 会让 `count: 0` 变成 20 条）。
	if raw is None or raw == "":
		return default
	value = _check_minimum(_coerce_int(raw, name), raw, name, minimum)
	if maximum is not None:
		value = _check_maximum(value, raw, name, maximum)
	return value


def _optional_int_input(
	inputs: Mapping[str, Any], name: str, *, minimum: int = 0, maximum: int | None = None
) -> int | None:
	"""读取可选整数游标：缺省 / null / 空串视为未提供，显式值仍按取值范围校验。

	可选游标若用 `inputs.get(name)` 真值判断决定是否下发，显式 `0` 会被当成
	「没给」静默丢弃（Issue #2621）：向导 / MCP 的 `recruiter_chat_history`
	因此把 `max_msg_id: 0` 悄悄换成最新一页，而顶层 `hr chatmsg --max-msg-id 0`
	由 Click `IntRange(min=1)` 报 `INVALID_PARAM`。两份实现必须同口径。

	`maximum` 只在调用方对应的 CLI 选项本身有上限时传（如 `chat --days` 的
	`IntRange(0, 36500)`），越界值不能原样进入筛选（Issue #2667）。
	"""
	raw = inputs.get(name)
	if raw is None or raw == "":
		return None
	value = _check_minimum(_coerce_int(raw, name), raw, name, minimum)
	if maximum is not None:
		value = _check_maximum(value, raw, name, maximum)
	return value


def _optional_int_list(inputs: Mapping[str, Any], name: str, *, minimum: int = 1) -> list[int]:
	"""读取可选整数列表：缺省 / null / 空串视为未提供，单个整数按一项处理。

	与 CLI 可重复的 `--friend-id`（`IntRange(min=1)`）同口径：元素必须是正整数，
	去重保序；非列表形状 / bool / 小数 / 越界元素都在平台请求前拒绝（向导 / MCP
	绕过 Click，Issue #2675）。
	"""
	raw = inputs.get(name)
	if raw is None or raw == "":
		return []
	values = raw if isinstance(raw, list) else [raw]
	result: list[int] = []
	for item in values:
		value = _check_minimum(_coerce_int(item, name), item, name, minimum)
		if value not in result:
			result.append(value)
	return result


def _require_run_id(inputs: Mapping[str, Any]) -> str:
	"""wizard 的 run_id 只接受非空字符串（Issue #1836）。

	``str(_required(...))`` 会把 123 / ``['r1']`` 拼成 "123" / "['r1']" 去查
	cache，再报成「任务不存在」并给出新建任务的误导指引；与 #1812 的命令层
	口径一致，非字符串或纯空白一律 INVALID_PARAM。
	"""
	raw = _required(inputs, "run_id")
	if not isinstance(raw, str) or not raw.strip():
		raise WorkflowActionError(
			"INVALID_PARAM",
			f"run_id 必须是非空字符串，收到 {type(raw).__name__}",
			recoverable=True,
			recovery_action="在 inputs 中提供 crawl run_id 字符串后用同一 run_id 重试；可用 crawl status <run_id> 核对",
		)
	return raw.strip()


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
	if code == "NETWORK_ERROR" and write:
		# HTTP 5xx / 超时文案的写响应无法证明动作没有生效：按结果未知处理，
		# 与候选人预约保留 pending 的口径一致（Issue #2316）。
		code = "ACTION_UNCONFIRMED"
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
		platform_rejected=write and replay_safe and code not in RISK_ERROR_CONTRACTS,
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
	provably_unsent: Callable[[BaseException], bool] | None = None,
) -> Any:
	"""发出一次写请求并按写语义解包响应；所有写动作都经由这里。

	请求发出之前先把「结果未知」连同本动作的核对命令落到当前步骤上。执行进程若在
	结果确认前消失（被 kill、终端关闭、断电），步骤停在 running、什么都来不及记，
	``--resume`` 凭这条记录拒绝重放（Issue #1019）。``call`` 只负责发请求：参数的
	校验与前置读取放在调用之前，那时被终止的步骤没有记录，恢复后照常重跑。

	``provably_unsent`` 命中时撤销预落的「结果未知」标记，让 ``--resume`` 能重试
	一次从未发出的写请求（与 greet / apply 的 #2298 同口径）。单请求写可以直接
	传 ``candidate_action_provably_unsent``；复合动作（写后还有读取，例如
	``view_geek`` 或聊天页发送）只能传 ``exception_reports_request_unsent``
	这类**只认底层显式证据**的判定——同一个异常类型可能发生在写请求之后
	（Issue #2898）。

	不经 runner 直接调用动作时没有步骤可记，直接执行。
	"""
	stop = AutomationStore.stop_reason_for(context.data_dir)
	if stop is not None:
		# 顶层 hr 写命令在派发前检查停止闩锁；wizard / MCP 走同一动作时不能例外
		# （Issue #2612）。请求未发出，不写「结果未知」标记，也不自动重试。
		resume_command = _hint_command(
			context,
			"agent run --resume",
			role="recruiter" if context.role == "recruiter" else None,
		)
		raise WorkflowActionError(
			"CIRCUIT_BREAKER_OPEN",
			f"停止开关已打开（{stop}），本次不会发出写请求",
			recoverable=True,
			recovery_action=(
				f"人工核对未确认动作后执行 {resume_command} 关闭熔断，"
				"再由操作者显式重试；不要自动重发"
			),
			auto_retry=False,
		)
	control = context.workflow_control
	if control is not None:
		control.begin_write(_unconfirmed_write_recovery(context, check_command))
	try:
		return _unwrap(platform, call(), fallback, context=context, write=True, check_command=check_command)
	except BaseException as exc:
		if provably_unsent is not None and provably_unsent(exc) and control is not None:
			control.clear_write()
		raise


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
	from boss_agent_cli.services.job_card import build_job_from_card, text_items

	greeted = False
	if data_dir is not None:
		with CacheStore(data_dir / "cache" / "boss_agent.db") as cache:
			if not job_id:
				# 与顶层 `detail` 同一优先级：显式 job_id > 缓存 job_id > 职位卡片。
				# 缓存里的非字符串 / 空白 / 孤立代理项按未命中处理（Issue #1817 /
				# #2549），不把坏值当参数发给平台。
				cached = cache.get_job_id(security_id)
				if (
					isinstance(cached, str)
					and cached.strip()
					and is_utf8_encodable(cached)
				):
					job_id = cached
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
				raw_job_type = scalar_or_none(job_info.get("jobType"))
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
					"lid": lid,
					"raw_job_type": raw_job_type,
					"employment_type": employment_type_from_raw(raw_job_type),
					"days_per_week": field_text(job_info, "daysPerWeekDesc"),
					"least_month": field_text(job_info, "leastMonthDesc"),
					"pay_type": field_text(job_info, "payTypeDesc"),
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
	except Exception as exc:
		# 与单次命令同口径：参数类 4xx 与限流是确定性 / 停止信号，换通道或
		# 自动重试都不会改变结果（Issue #2548）。
		status = parameter_status_code(exc)
		if status is not None:
			text = f"平台拒绝了请求参数（HTTP {status}）"
			code, recoverable, recovery = _classify_action_error("INVALID_PARAM", text)
			raise WorkflowActionError(
				code,
				text,
				recoverable=recoverable,
				recovery_action=recovery,
				auto_retry=False,
			) from exc
		if isinstance(exc, PlatformRateLimitError):
			text = str(exc) or "平台限流，请稍后重试"
			code, recoverable, recovery = _classify_action_error("RATE_LIMITED", text)
			raise WorkflowActionError(code, text, recoverable=recoverable, recovery_action=recovery) from exc
		raise

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
	with CacheStore(context.data_dir / "cache" / "boss_agent.db") as cache:
		# search_url / preset 与顶层 `search` 同口径展开（Issue #2685）：有
		# --url 时关键词可省略，平台按链接里的筛选条件返回。
		inputs = _expand_search_sources(context, cache, inputs)
		if inputs.get("search_url") is not None:
			query = _optional_str_strict(inputs, "query")
			if query is None or not query.strip():
				# CLI `search --url` 同口径：空白 / 缺失关键词按未提供处理，
				# 由链接里的 query（若有）或纯筛选搜索决定实际请求。
				inputs["query"] = ""
		else:
			# 纯空白关键词与 CLI `search " "` / 向导 watch 同口径拒绝（Issue #1798、
			# #2511、#2514）：`execute_candidate_search` 里 `_optional_str_strict(...) or ""`
			# 会原样接受它，平台按「无关键词」返回推荐并向动作报成功。
			_required_nonblank_str(inputs, "query")
		# sort 与顶层 `search --sort` 同一枚举：缺省 / null 保持平台顺序；显式空串
		# 或其他取值在平台请求前拒绝，不能静默按 relevance 返回（Issue #2660）。
		raw_sort = _optional_str_strict(inputs, "sort")
		sort_mode = "relevance" if raw_sort is None else raw_sort
		if sort_mode not in SEARCH_SORT_MODES:
			raise WorkflowActionError(
				"INVALID_PARAM",
				f"wizard 输入 sort 仅支持 {' / '.join(SEARCH_SORT_MODES)}，收到 {sort_mode!r}",
				recoverable=True,
				recovery_action="将 sort 改为 relevance 或 score 后重试；省略表示保持平台顺序",
			)
		# with_score 与顶层 `search --with-score` 同口径：向导此前完全不读该键，
		# 传 true 仍只返回管线内嵌的 match_score、没有匹配原因（Issue #2665）。
		# 类型错误同样在平台请求前拒绝。
		with_score = _bool_input(inputs, "with_score")
		# 页码 / 页数预算在构造平台实例前校验（Issue #2692，与 `_candidate_recommend`
		# 的 #2625 口径一致）：`execute_candidate_search` 内部也会读这两个键，但
		# 那时平台实例已构造，CDP 模式会先启动浏览器或做登录探测。
		page = _int_input(inputs, "page", 1, minimum=1, maximum=MAX_TRUSTED_PAGE)
		_int_input(inputs, "max_pages", 1, minimum=1)
		# 福利 / 活跃度在构造平台实例前校验（与 #2692 口径一致），并保留
		# 活跃度档位用于返回筛选统计（Issue #2720）。
		_, active_level = _detail_filter_inputs(inputs)
		account = account_scope(context.auth())
		search_kwargs: dict[str, Any] = {}
		if with_score:
			# 只在需要时传入，保持既有测试替身 / Python API 的调用签名（同 #2657）。
			search_kwargs["with_score"] = True
		with context.candidate_platform() as platform:
			result = execute_candidate_search(
				platform, cache, context.logger, {**inputs, "account": account}, **search_kwargs
			)
	items = sort_search_items(list(result.items), sort_mode)
	# 与顶层 `boss search` 同源：平台纠正页码时步骤数据也必须报平台页
	# （Issue #3118）；旧 test double 没有新字段时回退到最后扫描页 / 请求页。
	reported_page = getattr(result, "last_page_reported", None)
	last_scanned = getattr(result, "last_page", 0) or page
	data: dict[str, Any] = {
		# 先由 `execute_candidate_search` 评分、再排序：与命令层「先评分再
		# sort」一致（匹配分类型漂移按 0 处理）。
		"items": items,
		"pagination": _pagination_evidence(
			{"page": reported_page, "hasMore": result.has_more_reported},
			page=last_scanned,
			items=items,
		),
	}
	if active_level:
		# CLI search 把同一份统计写进 hints.active_filter；向导步骤没有 hints
		# 通道，放进 data 供 Agent 判断未知活跃度被排除了多少（Issue #2720）。
		data["active_filter"] = active_filter_hint(active_level, result)
	return StepResult(data)


def _candidate_recommend(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	# 页码在打开平台前校验：越界值不应先构造平台实例（Issue #2625）。
	page = _int_input(inputs, "page", 1, minimum=1, maximum=MAX_TRUSTED_PAGE)
	# with_score 与顶层 `recommend --with-score` 同口径：向导此前完全不读该键，
	# 传 true 仍返回平台原始列表且报成功（Issue #2662）。类型错误同样在平台
	# 请求前拒绝，避免先发一次注定无法用于评分的请求。
	with_score = _bool_input(inputs, "with_score")
	with context.candidate_platform() as platform:
		expect_data: dict[str, Any] | None = None
		if with_score:
			try:
				expect_resp = platform.resume_expect()
			except NotImplementedError as exc:
				recoverable, recovery = error_contract_for_code("NOT_SUPPORTED")
				raise WorkflowActionError(
					"NOT_SUPPORTED",
					str(exc) or "当前平台不支持求职期望能力",
					recoverable=recoverable,
					recovery_action=recovery,
				) from exc
			unwrapped = _unwrap(platform, expect_resp, "求职期望获取失败", context=context)
			# 与 CLI 同口径：求职期望不是对象时按空期望评分，不让结构漂移中断推荐。
			expect_data = unwrapped if isinstance(unwrapped, dict) else {}
		data = _unwrap(
			platform,
			platform.recommend_jobs(page=page),
			"推荐职位获取失败",
			context=context,
		)
	items = _extract_list_items(data, ("recommendList", "jobList", "list", "items", "result"))
	if with_score:
		# 归一、问候态与匹配分与 CLI `recommend` 共用一份实现（Issue #2662）。
		with CacheStore(context.data_dir / "cache" / "boss_agent.db") as cache:
			items = build_recommend_items(
				items,
				cache=cache,
				account=account_scope(context.auth()),
				with_score=True,
				expect_data=expect_data,
			)
	# 分页证据与顶层 `recommend` 同词汇：平台只给真正的布尔 hasMore 才认定
	# 有 / 没有下一页，缺失或类型漂移为 null（Issue #2670）。
	return StepResult(
		_with_items(
			{"result": data, "pagination": _pagination_evidence(data, page, items)},
			items,
		)
	)


def _candidate_detail(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	from boss_agent_cli.wizard.renderer import busy_status

	# 与 CLI `detail` 同口径：空 security_id 不是「查不到」，而是参数构造错了；
	# 照常发请求只会换来一次注定失败的平台往返（Issue #2516）。
	security_id = _required_nonblank_value(inputs, "security_id")
	# job_id / lid 也是直接进平台参数与本地缓存的自由输入：非字符串不能经 str()
	# 变成 repr，纯空白 job_id 按未提供处理（与顶层 --job-id 一致，Issue #1817）。
	raw_job_id = _optional_str_strict(inputs, "job_id")
	job_id = raw_job_id if raw_job_id is not None and raw_job_id.strip() else None
	if job_id is not None and not is_utf8_encodable(job_id):
		raise WorkflowInputError("wizard 输入 job_id 不能包含无法按 UTF-8 编码的字符")
	lid = _optional_str_strict(inputs, "lid") or ""
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


def _raise_candidate_release_failed(
	context: ActionContext, label: str, details: dict[str, Any], reference: str | None
) -> NoReturn:
	"""请求可证明未发出或平台明确拒绝，但预约没释放：不可重试，避免下一次撞 ACTION_UNCONFIRMED。"""
	if context.workflow_control is not None:
		# 请求没有被平台接受，撤销预落的「结果未知」标记（与派发前失败同口径）。
		context.workflow_control.clear_write()
	raise WorkflowActionError(
		"LOCAL_STATE_WRITE_FAILED",
		f"本次{label}未产生平台记录，但本地预约未释放；清理本地预约后再重试",
		recoverable=False,
		recovery_action=candidate_release_recovery_action(reference, lambda command: _hint_command(context, command)),
		details={"request_sent": False, "recorded": False, "claim_reference": reference, **details},
		replay_safe=False,
		auto_retry=False,
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
	security_id = _required_nonblank_str(inputs, "security_id")
	job_id = _required_nonblank_str(inputs, "job_id")
	# lid 直接进入投递写请求：非字符串不能经 str() 变成 repr（与 candidate_detail 同口径）。
	lid = _optional_str_strict(inputs, "lid") or ""
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
			except CandidateActionReleaseFailed as release_failed:
				# 请求可证明未发出或平台明确拒绝，但 pending 没释放；照原提示
				# 重试会撞结果未知（Issues #2944 #2971）。
				_raise_candidate_release_failed(
					context,
					"投递/立即沟通",
					{"security_id": security_id, "job_id": job_id, "lid": lid},
					release_failed.claim_reference,
				)
			except BaseException as exc:
				# 派发前失败：撤销 _write_call 预落的「结果未知」写标记，让 --resume
				# 能重试一次从未发出的写请求（Issue #2298）。
				if candidate_action_provably_unsent(exc) and context.workflow_control is not None:
					context.workflow_control.clear_write()
				raise
	return StepResult({"security_id": security_id, "job_id": job_id, "result": data})


def _candidate_greet(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	security_id = _required_nonblank_str(inputs, "security_id")
	job_id = _required_nonblank_str(inputs, "job_id")
	# message 可选，但类型必须严格：0 / false 经 ``or ""`` 会静默变成平台默认
	# 招呼，容器会被 str() 成 repr 发给真人（Issue #1906）。显式空白同样不能
	# 退化成默认招呼：顶层 `boss greet --message ""` 已报参数错误
	# （Issue #2603），向导 / MCP 必须同口径（Issue #2623）。
	message = (
		_optional_nonblank_str(
			inputs,
			"message",
			recovery_action="省略 message 使用默认招呼语，或传入非空内容后重试",
		)
		or ""
	)
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
			except CandidateActionReleaseFailed as release_failed:
				# 请求可证明未发出或平台明确拒绝，但 pending 没释放；照原提示
				# 重试会撞结果未知（Issues #2944 #2971）。
				_raise_candidate_release_failed(
					context, "打招呼", {"security_id": security_id, "job_id": job_id}, release_failed.claim_reference
				)
			except BaseException as exc:
				# 派发前失败：撤销 _write_call 预落的「结果未知」写标记，让 --resume
				# 能重试一次从未发出的写请求（Issue #2298）。
				if candidate_action_provably_unsent(exc) and context.workflow_control is not None:
					context.workflow_control.clear_write()
				raise
	return StepResult({"security_id": security_id, "job_id": job_id, "result": data})


def _candidate_friend(platform: Any, identifier: str, *, context: ActionContext | None = None) -> dict[str, Any]:
	try:
		friend, error_response = find_friend(platform, identifier)
	except FriendListShapeError as exc:
		# 列表容器缺失是可恢复的读取异常：刷新列表后可重试，但不能把不可
		# 判定的响应当成招聘者不存在（Issue #1978）。
		chat_hint = _hint_command(context, "chat") if context is not None else "boss chat"
		raise WorkflowActionError(
			"NETWORK_ERROR",
			str(exc),
			recoverable=True,
			recovery_action=f"先用 {chat_hint} 刷新沟通列表，确认列表可读后再重试；响应缺少列表容器时不能判定招聘者是否存在",
		) from exc
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
	# 与 CLI 侧 commands/_contact_lookup 共用同一份判定：布尔 / 容器漂移时
	# str() 出的 "True" / "{'a': 1}" 绝不能当身份发给 exchange_contact 等
	# 写请求（Issue #1812 只修了 CLI 侧，Issue #2543 统一）。
	security_id = friend_security_id(friend)
	if security_id:
		return security_id
	raise WorkflowActionError(
		"NETWORK_ERROR",
		"沟通列表返回的联系人缺少当前 securityId，已停止执行；按当前列表重试不会改变结果",
		recoverable=False,
		recovery_action="刷新沟通列表并取到联系人 security_id 后重新执行；不要自动重试同一列表结果",
	)


def _candidate_current_uid(friend: Mapping[str, Any]) -> str:
	"""读取本次列表返回的稳定 uid；缺失 / 类型漂移时安全停止（Issue #2543）。"""
	uid = friend_uid(friend)
	if uid:
		return uid
	raise WorkflowActionError(
		"NETWORK_ERROR",
		"沟通列表返回的联系人缺少稳定 uid，已停止执行；按当前列表重试不会改变结果",
		recoverable=False,
		recovery_action="刷新沟通列表并取到联系人 uid 后重新执行；不要自动重试同一列表结果",
	)


def _candidate_exchange(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	identifier = _required_nonblank_value(inputs, "security_id")
	exchange_type = _exchange_type(inputs)
	with context.candidate_platform() as platform:
		friend = _candidate_friend(platform, identifier, context=context)
		uid = _candidate_current_uid(friend)
		security_id = _candidate_current_security_id(friend)
		# name 会进写请求：与 CLI `exchange` 共用判定，非字符串 / 纯空白回落 `-`。
		# 旧实现 `str(name or "-")` 把 `{'n': 1}` / `True` / `nan` 这类 Python
		# repr 当联系人名发给平台（Issue #2748；CLI 侧为 #1812）。
		name = friend_display_name(friend)
		data = _write_call(
			context,
			platform,
			lambda: platform.exchange_contact(security_id, uid, name, exchange_type=exchange_type),
			"联系方式交换失败",
			# exchange_contact 是单次写请求：浏览器通道在 fetch 派发前失败
			# （代理 / 内核 / 导航 / 预算）时撤销写标记，--resume 可重试
			# 一次从未发出的请求（Issue #2441）。
			provably_unsent=candidate_action_provably_unsent,
		)
	return StepResult({"uid": uid, "security_id": security_id, "result": data})


def _candidate_mark(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	identifier = _required_nonblank_value(inputs, "security_id")
	label = _required_str(inputs, "label")
	# 先校验布尔值，再查联系人：非法输入不应产生任何平台请求。
	remove = _bool_input(inputs, "remove")
	# 标签名称 / 数字 ID 的映射是 services/labels.py 的单一真源（Issue #1905）：
	# 未知名称、非成员数字（999）与非 ASCII 数字都按参数错误拒绝，绝不把任意
	# label_id 发给平台去创建无名标签。
	label_id = parse_label_id(label)
	if label_id is None:
		raise WorkflowActionError(
			"INVALID_PARAM",
			f"未知标签: {brief_label(label)}（可选: {', '.join(LABEL_MAP)}；"
			f"数字 ID: {', '.join(str(value) for value in sorted(LABEL_MAP.values()))}）",
			recoverable=True,
			recovery_action="改用已知标签名称或数字 ID 后重试（见 boss mark --help）",
		)
	with context.candidate_platform() as platform:
		friend = _candidate_friend(platform, identifier, context=context)
		uid = _candidate_current_uid(friend)
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
			# friend_label 是单次写请求（Issue #2441）。
			provably_unsent=candidate_action_provably_unsent,
		)
	return StepResult(
		{
			"uid": uid,
			"security_id": friend_security_id(friend),
			"label": label,
			"result": data,
		}
	)


def _candidate_chat(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	# 页码在打开平台前校验：越界值不应先构造平台实例（Issue #2625）。
	page = _int_input(inputs, "page", 1, minimum=1, maximum=MAX_TRUSTED_PAGE)
	# from / days 与顶层 `boss chat` 同口径：向导此前只读 page，传筛选条件仍
	# 返回整页记录且报成功（Issue #2667）。取值与类型都在平台请求前拒绝。
	from_who = _optional_str_strict(inputs, "from")
	if from_who is not None and from_who not in FROM_FILTER:
		raise WorkflowActionError(
			"INVALID_PARAM",
			f"wizard 输入 from 仅支持 {' / '.join(FROM_FILTER)}，收到 {from_who!r}",
			recoverable=True,
			recovery_action="将 from 改为 boss 或 me 后重试；不筛发起方时省略该字段",
		)
	days = _optional_int_input(inputs, "days", minimum=0, maximum=36500)
	relation_filter = FROM_FILTER.get(from_who) if from_who is not None else None
	relation_label = RELATION_LABELS.get(relation_filter) if relation_filter is not None else None
	cutoff_ts = chat_cutoff_ts(days)
	with context.candidate_platform() as platform:
		data = _unwrap(
			platform,
			platform.friend_list(page=page),
			"沟通列表获取失败",
			context=context,
		)
	items = _extract_list_items(data, ("result", "friendList", "list", "items"))
	if relation_label is not None or cutoff_ts is not None:
		# 判定与 CLI `chat` 共用 `matches_chat_filters`：只筛当前页（与 CLI 相同），
		# 不额外翻页；lastTS 缺失 / 漂移与 CLI 一样按未知放行。
		items = [
			item
			for item in items
			if matches_chat_filters(
				initiated_by=relation_text(item.get("relationType")),
				last_ts=item.get("lastTS"),
				relation_label=relation_label,
				cutoff_ts=cutoff_ts,
			)
		]
	merged = _with_items({"result": data}, items)
	if from_who is not None or days is not None:
		# 回显生效的筛选，Agent 才能区分「未筛选」与「筛选项被忽略」（Issue #2667）。
		merged["filters"] = {"from": from_who, "days": days}
	# 与 CLI `chat` 同一套分页证据：`has_more` 只认平台布尔 hasMore，`total`
	# 是当前页条数（`--from` / `--days` 过滤后），Agent 不再盲翻下一页
	# （Issue #2670）。
	merged["pagination"] = _pagination_evidence(data, page, items)
	return StepResult(merged)


def _candidate_chat_history(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	# 空白 gid 不可能命中联系人：CLI chatmsg 在 resolve_friend_or_emit 里直接报
	# 参数错误，向导同样不能先翻一遍沟通列表再报 CONTACT_NOT_FOUND（Issue #2523）。
	identifier = _required_nonblank_value(inputs, "gid")
	# 页码在解析联系人之前校验：越界值不应先翻一遍沟通列表再拒绝（Issue #2625）。
	page = _int_input(inputs, "page", 1, minimum=1, maximum=MAX_TRUSTED_PAGE)
	count = _int_input(inputs, "count", 20, minimum=1, maximum=MAX_TRUSTED_COUNT)
	with context.candidate_platform() as platform:
		friend = _candidate_friend(platform, identifier, context=context)
		gid = _candidate_current_uid(friend)
		security_id = _candidate_current_security_id(friend)
		response = platform.chat_history(
			gid,
			security_id,
			page=page,
			count=count,
		)
		data = _unwrap(platform, response, "聊天记录获取失败", context=context)
	# 与 CLI `chatmsg` 共用容器的唯一真源：同一响应在两个入口必须读出同样的
	# 消息集合，不能再让一侧认 ``messageList``、另一侧只认 ``historyMsgList``（Issue #2962）。
	items = message_list(data)
	# 与 CLI `chatmsg` 同一份归一化；items 仍保留平台原值供后续步骤使用（Issue #2801）。
	messages = [normalize_chat_message(item, gid=gid, friend_name=friend_display_name(friend)) for item in items]
	return StepResult(
		_with_items(
			{
				"result": data,
				"gid": gid,
				"uid": gid,
				"security_id": security_id,
				"messages": messages,
				# 与 CLI `chatmsg` 同词汇的分页证据（Issue #2672）。
				"pagination": _pagination_evidence(data, page, items),
			},
			items,
		)
	)


def _candidate_pipeline_data(
	context: ActionContext, inputs: Mapping[str, Any], *, max_days_stale: int | None = None
) -> tuple[list[dict[str, Any]], int, bool]:
	# 参数先于平台请求校验：非法输入不应消耗受节流约束的读取配额（Issue #2631）。
	now_ts_ms = _int_input(inputs, "now_ts_ms", int(time.time() * 1000))
	stale_days = _int_input(inputs, "days_stale", 3, maximum=max_days_stale)
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
		now_ts_ms=now_ts_ms,
		stale_days=stale_days,
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
	# 上限与顶层 `boss digest --days-stale` 一致。
	items, pages_scanned, truncated = _candidate_pipeline_data(context, inputs, max_days_stale=36500)
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
	# 空白简历名在 CLI 由 `_load_resume_text` 报 INVALID_PARAM，不能落到
	# RESUME_NOT_FOUND 把参数错误伪装成「简历不存在」（Issue #2523）。
	resume_name = _required_nonblank_value(inputs, "resume")
	# 超长名同口径，且必须早于路径读取：ENAMETOOLONG 会被报成「简历损坏」，
	# 指引里的 resume delete 也无法执行（Issue #2969）。
	resume_name_error = resume_name_input_error(resume_name)
	if resume_name_error is not None:
		message, recovery = resume_name_error
		raise WorkflowActionError(
			"INVALID_PARAM",
			message,
			recoverable=True,
			recovery_action=f"{recovery}；用 {_hint_command(context, 'resume list')} 查看已有简历",
		)
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
	# 其余输入都经 _required* 校验；prompt 也必须走同口径，且先于本地 AI 配置
	# 检查：旧断点缺该键时应是参数 / 输入错误，而不是 KeyError 被归入
	# INTERNAL_ERROR 或先报 AI_NOT_CONFIGURED（Issue #2348）。
	# 空白 prompt 不能照常发起一次计费请求：CLI ai 命令族自 Issue #1975 起
	# 统一经 _ensure_text_not_blank 拒绝，向导必须在同一位置收口（Issue #2523）。
	prompt = _required_nonblank_value(inputs, "prompt")
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
				{"role": "user", "content": f"{prompt}\n\n简历:\n{resume_to_text(resume)}"},
			]
		)
	except AIServiceError as exc:
		raise WorkflowActionError(
			exc.code,
			str(exc),
			recoverable=exc.recoverable,
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
	OutputPathProblem.NOT_WRITABLE: ("LOCAL_IO_ERROR", "换一个父目录可写的路径后重试"),
	OutputPathProblem.UNEXPANDABLE_USER: ("INVALID_PARAM", "改用绝对路径或 ~/ 开头的当前用户路径"),
	OutputPathProblem.FILENAME_NOT_UTF8: ("INVALID_PARAM", "改用合法 UTF-8 的文件名后重试"),
}


def _export_output_path(context: ActionContext, inputs: Mapping[str, Any], fmt: str) -> Path:
	"""在发出平台请求前校验导出目标，返回展开后的路径。

	命令层 `export` / `chat --export` 已把 `-o` 校验前置（`inspect_output_path`）；
	wizard 之前要等取完数才在 `prepare_output_path` 上暴露路径问题，非法 format
	也会先翻页消耗请求预算（Issue #1330）。这里只读检查，不建目录：父目录仍由
	写文件前的 `prepare_output_path` 创建。
	"""
	raw = inputs.get("output")
	if raw is None:
		# 只有缺键或显式 null 才落默认路径；显式空串照命令行 `-o` 的口径
		# 拒绝（Issue #1501 / #3098），否则调用方会以为没指定输出却落了一个文件。
		raw = context.data_dir / "exports" / f"wizard-jobs.{fmt}"
	elif isinstance(raw, str) and not raw.strip():
		raise WorkflowActionError(
			"INVALID_PARAM",
			"wizard export 的 output 不能为空；省略 output 才会写到默认位置",
			recovery_action="去掉空的 output，或给出完整文件路径后重试",
		)
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
	elif problem is OutputPathProblem.UNEXPANDABLE_USER:
		message = f"wizard export 的 output 里有一个系统里查不到的用户，无法展开家目录写法: {str(target)!r}"
	elif problem is OutputPathProblem.FILENAME_NOT_UTF8:
		message = f"wizard export 的 output 文件名不是合法 UTF-8，当前文件系统无法创建: {str(target)!r}"
	elif problem is OutputPathProblem.NOT_WRITABLE:
		message = f"wizard export 的 output 父目录不可写，无法创建输出文件: {str(target)!r}"
	else:
		message = f"wizard export 的 output 无法创建父目录，某级父路径不是目录: {str(target)!r}"
	raise WorkflowActionError(code, message, recovery_action=recovery_action)


def _export_stop_reason(result: Any) -> str:
	"""把管线终止原因映射成 CLI 导出的 ``stop_reason`` 词汇。

	自定义管线（测试替身 / Python API）没有 ``stop_reason`` 时退回旧的
	``has_more`` 判定：仍有下一页就按预算耗尽处理，不能静默当完整结果。
	映射表与顶层 `boss export` 共用同一份实现（Issue #2712）。
	"""
	from boss_agent_cli.services.job_export import export_stop_reason

	raw = getattr(result, "stop_reason", "")
	if not isinstance(raw, str) or not raw:
		return "page_budget_exhausted" if getattr(result, "has_more", False) else ""
	return export_stop_reason(raw)


def _candidate_export(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	from boss_agent_cli.services.job_export import export_is_truncated, prepare_export_items, write_export_file

	# 参数与路径必须在平台请求之前校验：非法 format 不能先翻完页才发现，
	# `output` 指向目录也不能消耗一次受限请求（与命令层 `-o` 预检同口径）。
	with CacheStore(context.data_dir / "cache" / "boss_agent.db") as cache:
		# `export --url` 同口径（Issue #2685）：链接把关键词与筛选条件一起带进来，
		# query 可以省略；展开后再沿用空白关键词拒绝（Issue #2519）：
		# `execute_candidate_search` 的 ``or ""`` 会让平台按无关键词返回空泛结果，
		# 导出文件照常生成并报成功。
		inputs = _expand_search_sources(context, cache, inputs)
		if inputs.get("search_url") is not None:
			query = _optional_str_strict(inputs, "query")
			if query is None or not query.strip():
				inputs["query"] = ""
		else:
			_required_nonblank_str(inputs, "query")
		include_private = _bool_input(inputs, "include_private")
		welfare_conditions, active_level = _detail_filter_inputs(inputs)
		detail_filters = bool(welfare_conditions or active_level)
		# count 与 CLI `--count` 同口径：缺省 / null / 空串在无详情筛选时由默认
		# 页数预算决定导出范围（保持旧行为）；带福利 / 活跃度时取 CLI 默认值
		# 50，否则管线会按搜索的 10 条活跃度目标提前停止并静默导出不完整结果
		# （Issue #2716）。显式值必须是 ≥ 1 的整数，显式 0 不能被当成「没给」
		# 悄悄换成缺省（与 #2621 的游标口径一致）。
		raw_count = inputs.get("count")
		count: int | None = None
		if raw_count is not None and raw_count != "":
			count = _int_input(inputs, "count", 1, minimum=1, maximum=MAX_TRUSTED_COUNT)
		elif detail_filters:
			count = DEFAULT_EXPORT_COUNT
		fmt = _choice_input(inputs, "format", "json")
		if fmt not in {"json", "csv"}:
			raise WorkflowActionError("INVALID_PARAM", "wizard export format 仅支持 json/csv")
		output_path = _export_output_path(context, inputs, fmt)
		# 页码 / 页数预算在构造平台实例前校验（Issue #2692，与 `_candidate_chat`
		# 的 #2625 口径一致）：`execute_candidate_search` 内部也会读这两个键，但
		# 那时平台实例已构造，CDP 模式会先启动浏览器或做登录探测。这里只校验
		# 显式值；缺省预算仍由 `export_page_budget(count)` 决定。
		_int_input(inputs, "page", 1, minimum=1, maximum=MAX_TRUSTED_PAGE)
		explicit_max_pages = _int_input(inputs, "max_pages", 1, minimum=1)
		search_inputs = {**inputs, "account": account_scope(context.auth())}
		if detail_filters:
			# 福利 / 活跃度要逐个查详情：页数预算不能超过搜索的详情上限，否则
			# 导出会比 CLI `export` / `search` 多翻几倍详情页、放大风控风险
			# （Issue #2716）。显式 max_pages 也必须受这个硬边界约束。
			cap = detail_filter_max_pages(welfare_conditions, active_level)
			has_explicit_max_pages = inputs.get("max_pages") not in (None, "")
			base_pages = (
				explicit_max_pages
				if has_explicit_max_pages
				else export_page_budget(count if count is not None else DEFAULT_EXPORT_COUNT)
			)
			search_inputs["max_pages"] = min(base_pages, cap)
		elif count is not None and search_inputs.get("max_pages") in (None, ""):
			# 与顶层 `boss export` 同一页数口径：只给默认页数预算时按 count 估页，
			# 否则默认 1 页会把 count=50 的请求截在首屏。用户显式给了 max_pages
			# 时尊重其预算（结果仍按 count 截断）。
			search_inputs["max_pages"] = export_page_budget(count)
		with context.candidate_platform() as platform:
			# 与 candidate_search / candidate_watch 一致：导出时的 greeted 标记
			# 必须按当前账号计算，否则登录态下会把所有职位都标成未打招呼。
			# 平台缺失 hasMore 时按导出语义继续扫描（受 count / max_pages /
			# 空页约束），与顶层 `boss export` 的 #903 口径一致（Issue #2657）。
			# 只在需要时传新关键字，保持现有测试替身 / Python API 的调用签名。
			search_kwargs: dict[str, Any] = {"limit": count}
			if count is not None:
				search_kwargs["continue_when_has_more_unknown"] = True
			result = execute_candidate_search(
				platform,
				cache,
				context.logger,
				search_inputs,
				**search_kwargs,
			)
	output = prepare_output_path(output_path)
	items = prepare_export_items(result.items, include_private=include_private)
	write_export_file(items, fmt, str(output))
	data: dict[str, Any] = {"path": str(output), "format": fmt, "count": len(items)}
	if active_level:
		# CLI `export` 把同一份统计写进 hints.active_filter；向导步骤没有
		# hints 通道，放进 data 供 Agent 判断未知活跃度被排除了多少（Issue #2716）。
		data["active_filter"] = active_filter_hint(active_level, result)
	if count is not None:
		# 少于请求条数时区分「平台已没有更多」「空页」「取满」与「页数预算
		# 耗尽 / 活跃度提前停止」：只有后两者才是截断，调用方不能把前缀当
		# 完整结果（Issue #2657 / #2710）。判定与顶层 `boss export` 共用。
		stop_reason = _export_stop_reason(result)
		# 与顶层 `boss export` 同口径：取满请求条数时无论管线因何停止，都按
		# count_reached 报告；用管线 items 而不是导出文件行数，避免
		# include_private 过滤让口径再次漂移（Issue #3119）。
		if len(getattr(result, "items", ())) >= count:
			stop_reason = "count_reached"
		data["requested"] = count
		data["stop_reason"] = stop_reason
		data["truncated"] = export_is_truncated(stop_reason, item_count=len(items), requested=count)
	return StepResult(data, artifacts=(str(output),))


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


def _run_saved_watch(context: ActionContext, cache: CacheStore, name: str) -> dict[str, Any]:
	"""运行一条已保存的 watch；点名运行与批量运行共用。"""
	try:
		record = cache.get_saved_search(name)
	except (SavedSearchParamsError, CacheRecordInvalid) as exc:
		# 参数写坏与 name / 时间戳等元数据漂移同口径：本地记录损坏是确定性
		# 失败，必须报 SAVED_SEARCH_INVALID 并给出删除重建指引，不能落成
		# CACHE_RECORD_INVALID（Issue #2011、#2529）。
		raise _saved_search_invalid_error(context, name, str(exc)) from exc
	if record is None:
		raise WorkflowActionError("SAVED_SEARCH_NOT_FOUND", f"未找到 watch: {name}")
	# 读取侧与 CLI `watch run` 共用同一份校验：旧版本 / 手工编辑写下的
	# 空白关键词与类型漂移不能以空泛搜索返回成功（Issue #1338、#1798、#2511）。
	try:
		validate_stored_search_params(record["params"])
	except ValueError as exc:
		raise _saved_search_invalid_error(context, name, f"watch {name!r} 的参数已失效：{exc}") from exc
	account = account_scope(context.auth())
	with context.candidate_platform() as platform:
		result = execute_candidate_search(platform, cache, context.logger, {**record["params"], "account": account})
	watch_result = cache.record_watch_results(name, result.items, account=account)
	return {"name": name, **watch_result}


def _run_all_saved_watches(context: ActionContext, cache: CacheStore) -> dict[str, Any]:
	"""批量运行全部 watch，与 CLI `watch run --all` 同口径（Issue #2698）。

	单条记录损坏或被并发删除时记入 ``watches`` 的 error 条目并继续；命中
	平台风控时立即停止后续 watch，已完成的部分随 ``details`` 带回，不重试。
	"""
	records, corrupt = cache.list_saved_searches_with_corrupt()
	watches: list[dict[str, Any]] = []
	for record in records:
		name = record["name"]
		risk_code: str | None = None
		try:
			summary = _run_saved_watch(context, cache, name)
		except WorkflowActionError as exc:
			if exc.code == "SAVED_SEARCH_INVALID":
				summary = {"name": name, "error": exc.code, "message": str(exc)}
			elif exc.code == "SAVED_SEARCH_NOT_FOUND":
				# 列出之后按名称读不到：并发删除。静默丢弃会让批量结果少一条
				# 却看不出异常，与 CLI 一样显式列出。
				summary = {
					"name": name,
					"error": "SAVED_SEARCH_INVALID",
					"message": "watch 记录在运行前已不存在（可能被并发删除）；请用 watch list 确认后重新创建",
				}
			else:
				raise
		except PlatformRiskError as exc:
			risk_code = exc.code
		except SearchPipelinePlatformError as exc:
			if exc.code in RISK_ERROR_CONTRACTS:
				risk_code = exc.code
			else:
				summary = {"name": name, "error": exc.code, "message": exc.message}
		if risk_code is not None:
			# 风控是整条 workflow 的硬边界：不再向平台发起请求，也不透传平台响应原文。
			recovery_action, _ = risk_error_contract(risk_code, **_risk_commands(context))
			raise WorkflowActionError(
				risk_code,
				risk_error_message(risk_code),
				recoverable=False,
				recovery_action=recovery_action,
				details={"watches": watches, "stopped_watch": name},
			)
		watches.append(summary)
	return {
		"mode": "all",
		"watches": watches,
		"total": len(watches),
		"skipped": corrupt,
		"invalid": [item["name"] for item in watches if item.get("error") == "SAVED_SEARCH_INVALID"],
	}


def _candidate_watch(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	action = _choice_input(inputs, "action", "list")
	with CacheStore(context.data_dir / "cache" / "boss_agent.db") as cache:
		if action == "list":
			items, corrupt = cache.list_saved_searches_with_corrupt()
			data: dict[str, Any] = {"items": items, "total": len(items)}
			if corrupt:
				data["corrupt"] = corrupt
			return StepResult(data)
		# `all` 只属于 run：点名与批量是两种运行范围，同时给出时不能静默丢弃
		# 其中一个（CLI 同口径，Issue #1561）；其他动作带 all 则是误用。
		if _bool_input(inputs, "all"):
			if action != "run":
				raise WorkflowActionError(
					"INVALID_PARAM",
					f"wizard 输入 all 只适用于 action=run（收到 action={action}）",
					recoverable=True,
					recovery_action="去掉 all，或把 action 改为 run 后重试",
				)
			if inputs.get("name") is not None:
				raise WorkflowActionError(
					"INVALID_PARAM",
					"wizard 输入 all 不能与 name 同时使用",
					recoverable=True,
					recovery_action="只提供 name 运行一个 watch，或去掉 name 用 all 运行全部",
				)
			return StepResult(_run_all_saved_watches(context, cache))
		# 名称与 CLI `watch add/remove/run` 同口径：空白名不可能被命令点名，
		# 存下来后 CLI 既不能 run 也不能 remove（Issue #2511）。这里只校验、
		# 不 strip：CLI 允许名称首尾带空格，向导必须能按原值寻址。
		name = _required_str(inputs, "name")
		if not name.strip():
			raise WorkflowActionError(
				"INVALID_PARAM",
				"wizard 输入 name 不能只有空白字符",
				recoverable=True,
				recovery_action="在 inputs 中提供有效的 name 后重试",
			)
		# 名称要作为 SQLite TEXT 键绑定：孤立代理项（JSON 的 ``"\udcff"`` 转义）
		# 会在绑定参数时抛 ``UnicodeEncodeError``，被顶层兜成带磁盘指引的
		# ``LOCAL_IO_ERROR``。CLI 在入口用同一判据拒绝（Issue #2268、#2512）。
		if problem := saved_search_name_error(name):
			raise WorkflowActionError(
				"INVALID_PARAM",
				f"watch 名称无效：{problem}",
				recoverable=True,
				recovery_action="换一个只含普通 Unicode 文本的名称后重试",
			)
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
					# 元素必须是字符串：容器经 ``str()`` 会变成 repr 并持久化，后续
					# 筛选把不可识别的标签静默当成关键词（Issue #2421）。
					for index, item in enumerate(value):
						if not isinstance(item, str):
							raise WorkflowActionError(
								"INVALID_PARAM",
								f"watch 参数 welfare 的第 {index} 个元素必须是字符串（收到 {type(item).__name__}）",
							)
					value = ",".join(item.strip() for item in value if item.strip())
				elif value is not None and not isinstance(value, str):
					raise WorkflowActionError(
						"INVALID_PARAM", f"watch 参数 {key} 必须是字符串（收到 {type(value).__name__}）"
					)
				params[key] = value
			# active 与 `boss_watch_add` 的 schema 同口径（string、minLength 1）：
			# `false` / `0` / `[]` 曾被真值判断吞掉、按「不筛选」静默保存；
			# 空串与空白同样拒绝，提示省略该字段表示不筛选（Issue #2645）。
			raw_active = inputs.get("active")
			if raw_active is not None:
				if not isinstance(raw_active, str):
					raise WorkflowActionError(
						"INVALID_PARAM",
						f"watch 参数 active 必须是字符串（收到 {type(raw_active).__name__}）",
					)
				if not raw_active.strip():
					raise WorkflowActionError(
						"INVALID_PARAM",
						"watch 参数 active 不能为空白；不设筛选请省略该字段",
					)
				try:
					params["active"] = resolve_active_level(raw_active)
				except ValueError as exc:
					raise WorkflowActionError("INVALID_PARAM", str(exc)) from exc
			# 与 CLI `watch add` 同口径：纯空白关键词会让 run 发起空泛搜索并
			# 返回成功，必须在写入前拒绝（Issue #1798、#2511）。
			_required_nonblank_str(inputs, "query")
			try:
				# CLI 在写入前跑同一份存量校验，覆盖关键词 / 福利等全部参数的
				# 类型与 UTF-8 可编码性（Issue #2528、#2543）。只用
				# `_validate_search_filters` 会让向导存下永远跑不通的记录：
				# 每次 run 报 SAVED_SEARCH_INVALID，删除后用同样输入重建仍成功
				# （Issue #2651）。
				validate_stored_search_params(params)
			except ValueError as exc:
				raise WorkflowActionError(
					"INVALID_PARAM", str(exc), recoverable=True, recovery_action="修正 watch 筛选参数后重试"
				) from exc
			cache.save_saved_search(name, params)
			return StepResult({"action": "add", "name": name, "params": params})
		if action == "remove":
			return StepResult({"action": "remove", "name": name, "removed": cache.delete_saved_search(name)})
		if action != "run":
			raise WorkflowActionError("INVALID_PARAM", f"未知的 watch 动作: {action}（可选: add、remove、run）")
		return StepResult(_run_saved_watch(context, cache, name))


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


def _crawl_partial_details(data: Any) -> dict[str, Any]:
	"""采集未完成时的断点信息：进度与已落盘结果（Issue #1834）。

	限流 / 超时 / 停止分支原来只抛错误码，已经采集的页数、职位数与输出路径
	全部丢失；这里按 outcome.as_dict() 取紧凑子集，避免把 jobs 列表塞进错误。
	"""
	if not isinstance(data, Mapping):
		return {}
	details: dict[str, Any] = {}
	for key in ("run_id", "status", "next_page", "pages_completed", "jobs_seen", "detail_checks", "output_paths"):
		if key in data:
			details[key] = data[key]
	checkpoint = data.get("checkpoint")
	if isinstance(checkpoint, Mapping):
		details["checkpoint"] = dict(checkpoint)
	return details


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
			details=_crawl_partial_details(data),
		)
	if outcome.status == "workflow_stopped" or (outcome.status == "stopped" and _is_stop_error(outcome.error)):
		raise WorkflowActionError(
			"WORKFLOW_STOPPED",
			outcome.error or "crawl 已停止",
			recoverable=False,
			recovery_action=status_command,
			details=_crawl_partial_details(data),
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
			details=_crawl_partial_details(data),
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
			details=_crawl_partial_details(data),
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
	query = inputs.get("query")
	if query is not None and query != "" and not isinstance(query, str):
		raise WorkflowActionError(
			"INVALID_PARAM",
			f"query 必须是字符串，收到 {_brief_value(query)}",
			recoverable=True,
			recovery_action="去掉 query 续跑原任务，或提供字符串关键词后重试",
			auto_retry=False,
		)
	city = inputs.get("city")
	if city is not None and city != "" and not isinstance(city, str):
		raise WorkflowActionError(
			"INVALID_PARAM",
			f"city 必须是字符串，收到 {_brief_value(city)}",
			recoverable=True,
			recovery_action="去掉 city 续跑原任务，或提供城市名称后重试",
			auto_retry=False,
		)
	mismatched: list[str] = []
	stored_query = params.get("query")
	if isinstance(query, str) and query.strip() and isinstance(stored_query, str) and query.strip() != stored_query.strip():
		mismatched.append(f"query（任务为 {stored_query!r}，本次为 {query!r}）")
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


def _explicit_crawl_run_id(inputs: Mapping[str, Any]) -> str | None:
	"""读取显式传入的 run_id / crawl_run_id（Issue #1833）。

	真值 ``or`` 会吞掉 0 / false / []，把「显式传了非法值」当成「没传」，
	直接新建采集任务并向平台发出真实请求。只有缺键与 null 才视为未提供；
	显式非字符串、纯空白都按参数错误拒绝，与 CLI 的 ``--run-id`` 同口径。
	"""
	found: str | None = None
	found_key = ""
	for key in ("run_id", "crawl_run_id"):
		if key not in inputs:
			continue
		raw = inputs[key]
		if raw is None:
			continue
		if not isinstance(raw, str) or not raw.strip():
			raise WorkflowActionError(
				"INVALID_PARAM",
				f"{key} 必须是非空字符串，收到 {type(raw).__name__}",
				recoverable=True,
				recovery_action="去掉该字段新建采集，或提供字符串 run_id 续跑已有任务；可用 crawl status <run_id> 核对",
			)
		value = raw.strip()
		if found is not None and value != found:
			# 模板合并或复制粘贴残留两个指向不同任务的键：静默取第一个会续跑
			# 并导出旧任务数据（Issue #1908），必须点名冲突。
			raise WorkflowActionError(
				"INVALID_PARAM",
				f"run_id 与 crawl_run_id 同时给出且不一致（{found_key}={_brief_value(found)}，{key}={_brief_value(value)}）",
				recoverable=True,
				recovery_action="只保留一个 run_id 键后重试；两个键都指向同一个任务时才可同时提供",
			)
		found = value
		found_key = key
	return found


def _crawl_start(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	# 恢复 waiting_input 时若已有内部 crawl run_id，必须续跑而不是新建采集。
	explicit_run_id = _explicit_crawl_run_id(inputs)
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
		# plan.inputs 里的 "run_id": null（与「未提供」同义）不能覆盖刚解析出的
		# 绑定 ID：字典展开放后面会把 run_id 改回 None，恢复链立刻报缺输入
		# （Issue #1903）。显式 run_id / crawl_run_id 与断点的 query / city 一致性
		# 由 _crawl_resume 统一校验（Issue #1904）。
		return _crawl_resume(context, {**dict(inputs), "run_id": existing_run_id}, prior)

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
	city = _required_str(inputs, "city")
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
	hook_profile = _choice_input(inputs, "hook_profile", "none")
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
			f"{name} 必须是整数: {_brief_value(value)}",
			recovery_action=f"传入 1 以上的整数 {name}",
		)
	try:
		parsed = int(value)
	# OverflowError：`1e400` 解析成 inf，`int(inf)` 抛错；调用方改输入即可修复，
	# 不能落成 INTERNAL_ERROR（Issue #1288）。
	except (TypeError, ValueError, OverflowError) as exc:
		raise WorkflowActionError(
			"INVALID_PARAM",
			f"{name} 必须是整数: {_brief_value(value)}",
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
	run_id = _require_run_id(inputs)
	with CacheStore(context.data_dir / "cache" / "boss_agent.db") as cache:
		try:
			data = crawl_status(cache, run_id, data_dir=context.data_dir)
			data = _attach_wizard_jobs(data, cache, run_id)
		except KeyError as exc:
			# str(KeyError("run_id")) 会回显 "'run_id'"：任务不存在要给登记表里
			# 指向 crawl status 的可执行指引（Issue #1907）。
			raise WorkflowActionError(
				"CRAWL_RUN_NOT_FOUND",
				f"未找到 crawl run: {run_id}",
				recovery_action=(
					f"用 boss crawl status {shell_argument(run_id)} 核对 run_id，"
					"或用 boss crawl start <query> 新建任务"
				),
			) from exc
	# 风控暂停时 CLI `boss crawl status` 只给人工解锁步骤，不放 resume 到
	# next_actions：风险锁会先于浏览器构造阻断恢复，直接照 resume 重试只会
	# 再次撞锁（Issue #3053，与 #1405 同口径）。
	if data.get("status") == "risk_stopped":
		checkpoint = data.get("checkpoint")
		resume_command = checkpoint.get("resume_command") if isinstance(checkpoint, Mapping) else None
		operator_actions = list(unlock_operator_actions(_hint_command(context, "clean --risk-lock")))
		if isinstance(resume_command, str) and resume_command:
			operator_actions.append(f"完成人工恢复后，显式执行 {resume_command} 从原断点继续")
		return StepResult(data, operator_actions=tuple(operator_actions))
	# 执行器已退出 / 排队超时的任务只轮询状态永远等不到结果：与 `boss crawl status`
	# 一样把后台恢复作为下一步，数据里的 executor_active 供调用方判定（Issue #2479）。
	# 已受理的停止请求还没被消费时改为按断点结算 stop：resume --background 会先
	# 清除 stop 标记，跟随指引就撤销了用户的停止请求（Issue #2482）。
	next_action = None
	if data.get("running_stale") or data.get("queue_overdue"):
		if data.get("stop_requested"):
			next_action = _hint_command(context, f"crawl stop {shell_argument(run_id)}")
		else:
			next_action = _hint_command(context, f"crawl resume {shell_argument(run_id)} --background")
	return StepResult(data, next_action=next_action)


def _crawl_resume(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	run_id = _require_run_id(inputs)
	# 同一 run_id 配冲突的 query / city 时静默续跑旧断点，会让 workflow 记录写着
	# 新关键词、实际请求与导出的却是旧任务数据；与 _crawl_start 的显式 run_id
	# 路径同口径（Issue #1904）。
	_require_crawl_inputs_match_run(context, run_id, inputs)
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


def _crawl_run_finished_error(context: ActionContext, run_id: str, status: str | None) -> WorkflowActionError:
	status_command = _hint_command(context, f"crawl status {shell_argument(run_id)}")
	results_command = _hint_command(context, f"crawl results {shell_argument(run_id)}")
	return WorkflowActionError(
		"CRAWL_RUN_FINISHED",
		f"crawl 任务已结束（{status}），停止请求没有生效",
		recovery_action=f"用 {status_command} 查看最终状态，或 {results_command} 读取已采集结果；需要重新采集时新建 crawl 任务",
		auto_retry=False,
	)


def _crawl_stop(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	run_id = _require_run_id(inputs)
	settle_reason = None
	with CacheStore(context.data_dir / "cache" / "boss_agent.db") as cache:
		run = cache.get_crawl_run(run_id)
		if run is None:
			raise WorkflowActionError("CRAWL_RUN_NOT_FOUND", f"未找到 crawl run: {run_id}")
		# 与 `boss crawl stop` 同口径（Issue #1574 #1786 #2465）：终态任务上的
		# 停止请求不会生效；记录仍是 queued / running 但执行锁空闲说明执行器已
		# 退出，此时只写停止标记没人消费，必须按断点结算 stopped。
		status = run["status"]
		if status not in ("queued", "running"):
			raise _crawl_run_finished_error(context, run_id, status)
		accepted = False
		final_status: str | None = status
		if crawl_run_awaiting_worker(run):
			# 交接窗口内的 worker 可能正要取锁：只写停止标记，交给它消费。
			accepted, final_status = cache.stop_active_crawl_run(run_id)
		else:
			with crawl_execution_lock(context.data_dir, run_id) as acquired:
				if acquired:
					# 持锁重读：另一执行器可能在首次读取后更新断点或安排新交接。
					run = cache.get_crawl_run(run_id)
					if run is not None and not crawl_run_awaiting_worker(run):
						settle_reason = (
							"executor exited before this stop request; resume to continue from the checkpoint"
						)
				accepted, final_status = cache.stop_active_crawl_run(run_id, settle_reason=settle_reason)
	if not accepted:
		if final_status is None:
			raise WorkflowActionError("CRAWL_RUN_NOT_FOUND", f"未找到 crawl run: {run_id}")
		raise _crawl_run_finished_error(context, run_id, final_status)
	# 与 CLI `boss crawl stop` 的 next_actions 同口径：停止请求被受理或按
	# 断点结算后，都要用 crawl status 确认最终进度（Issue #3053）。
	status_command = _hint_command(context, f"crawl status {shell_argument(run_id)}")
	if settle_reason is not None:
		return StepResult(
			{"run_id": run_id, "status": "stopped", "stale_recovered": True},
			next_action=status_command,
		)
	return StepResult(
		{"run_id": run_id, "status": "stop_requested"}, next_action=status_command
	)


def _recruiter_call(
	context: ActionContext,
	call: Callable[[Any], dict[str, Any]],
	fallback: str,
	*,
	list_keys: tuple[str, ...] = (),
	list_extractor: Callable[[Any], list[dict[str, Any]]] | None = None,
	write: bool = False,
	check_command: str | None = None,
	provably_unsent: Callable[[BaseException], bool] | None = None,
	page: int | None = None,
) -> StepResult:
	with context.recruiter_platform() as platform:
		if write:
			data = _write_call(
				context,
				platform,
				lambda: call(platform),
				fallback,
				check_command=check_command,
				provably_unsent=provably_unsent,
			)
		else:
			data = _unwrap(platform, call(platform), fallback, context=context, check_command=check_command)
	payload: dict[str, Any] = {"result": data}
	items: list[dict[str, Any]] = []
	if list_extractor is not None:
		# 有共享解析器时不再走通用键猜测：聊天历史这类有明确容器集合的
		# 数据必须与 CLI 读出同一结果（Issue #2962）。
		items = list_extractor(data)
		payload = _with_items(payload, items)
	elif list_keys:
		items = _extract_list_items(data, list_keys)
		payload = _with_items(payload, items)
	if page is not None:
		# 列表类动作与 CLI 信封同词汇的分页证据：平台只给真正的布尔 hasMore
		# 才认定有 / 没有下一页（Issue #2672）。
		payload["pagination"] = _pagination_evidence(data, page, items)
	return StepResult(payload)


def _recruiter_candidates(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	# 与 `hr candidates --city ""` / `--job-id ""` 同口径：显式空白筛选在
	# 构造平台客户端之前按参数错误拒绝，不能静默按「不筛选」搜索（Issue #2618）。
	_optional_nonblank_str(inputs, "city")
	_optional_nonblank_str(inputs, "job_id")
	# 与 CLI `hr candidates --page` 同口径：页码在平台请求前校验，并回显到
	# 步骤 pagination（Issue #2672）。
	page = _int_input(inputs, "page", 1, minimum=1)
	return _recruiter_call(
		context,
		lambda platform: execute_recruiter_candidate_search(platform, inputs),
		"候选人搜索失败",
		list_keys=("geekList", "items", "list", "result"),
		page=page,
	)


def _recruiter_recommendations(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	# 与 CLI `hr recommendations --job-id` 同口径：空职位 ID 命中不了任何职位，
	# 却会带着空参数真实发一次请求（Issue #1782），向导在平台前拒绝。
	job_id = _required_nonblank_value(inputs, "job_id")
	page = _int_input(inputs, "page", 1, minimum=1)
	return _recruiter_call(
		context,
		lambda platform: platform.recommend_geeks(job_id, page=page),
		"推荐牛人获取失败",
		list_keys=("geekList", "items", "list", "result"),
		page=page,
	)


def _recruiter_applications(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	# `hr applications` / `hr chat` 对显式空 --job-id 返回 INVALID_PARAM
	# （Issue #2600），向导路径保持同一语义（Issue #2618）。
	job_id = _optional_nonblank_str(inputs, "job_id")
	page = _int_input(inputs, "page", 1, minimum=1)
	# 与 CLI `hr chat --summary-limit` 同口径（IntRange(0, MAX_SUMMARY_LIMIT)）：
	# 0 = 不请求最近消息；非整数 / 越界在平台请求前拒绝（Issue #2679）。
	summary_limit = _int_input(inputs, "summary_limit", DEFAULT_SUMMARY_LIMIT, minimum=0, maximum=MAX_SUMMARY_LIMIT)
	with context.recruiter_platform() as platform:
		data = _unwrap(
			platform,
			platform.friend_list(page=page, label_id=_int_input(inputs, "label_id", 0), job_id=job_id),
			"投递申请获取失败",
			context=context,
		)
		rows = _extract_list_items(data, ("friendList", "result", "list", "items"))
		# 向导路径没有 CDP 页面快照，全部走接口；来源统计与 CLI hints.summary_sources 同词汇。
		summary = enrich_friend_summaries(platform, rows, summary_limit=summary_limit)
	payload = _with_items({"result": data, "pagination": _pagination_evidence(data, page, rows)}, rows)
	if rows:
		payload["summary_sources"] = summary
	return StepResult(payload)


def _recruiter_resume(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	# 与 CLI `hr resume` 同口径：geek_id / job_id 去空白后必须非空。view_geek
	# 声明 effect: unknown，空白标识的请求不可能命中候选人，必须在构造平台
	# 之前按参数错误收口（Issue #2525）。
	geek_id = _required_nonblank_value(inputs, "geek_id")
	job_id = _required_nonblank_value(inputs, "job_id")
	# 非字符串不能 str() 后发给平台：{"a": 1} 会变成它的 repr 进入一次
	# effect: unknown 的请求（Issue #2633）。空串仍按未提供处理。
	security_id = _optional_str_strict(inputs, "security_id") or None
	# 编码失败发生在请求发出之前；必须在写标记落盘前拒绝（Issue #3017）。
	_reject_unencodable_inputs(geek_id=geek_id, job_id=job_id, security_id=security_id)
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
	# `hr last-messages --job-id ""` 在平台调用前拒绝（Issue #2600），向导同口径。
	job_id = _optional_nonblank_str(inputs, "job_id")
	page = _int_input(inputs, "page", 1, minimum=1)
	label_id = _int_input(inputs, "label_id", 0)
	# 与 CLI `--limit` 同口径（默认 100、上限 300）：先截断再请求，不能把整页
	# friendId 一次塞给平台（Issue #2675）。
	limit = _int_input(inputs, "limit", DEFAULT_SUMMARY_LIMIT, minimum=1, maximum=MAX_SUMMARY_LIMIT)
	# `--friend-id` 直查：跳过沟通列表，没有列表页分页证据（Issue #2675）。
	direct_ids = _optional_int_list(inputs, "friend_ids")
	with context.recruiter_platform() as platform:
		sources: dict[int, int] = {}
		pagination: dict[str, Any] | None = None
		if direct_ids:
			friend_ids = direct_ids
		else:
			friend_data = platform_data_dict(
				_unwrap(
					platform,
					platform.friend_list(page=page, label_id=label_id, job_id=job_id),
					"沟通列表获取失败",
					context=context,
				)
			)
			friend_items = next(
				(value for key in ("friendList", "result", "list") if isinstance((value := friend_data.get(key)), list)),
				[],
			)
			# total 按实际提取出的联系人行计数；非对象元素与 CLI `friend_items`
			# 一样被过滤，不能虚增本页条数（Issue #2900）。
			extracted = [item for item in friend_items if isinstance(item, dict)]
			# 与 CLI `hr last-messages` 同口径：分页证据来自沟通列表页，而不是
			# 逐个会话的最近消息响应（Issue #2672）。
			pagination = _pagination_evidence(friend_data, page, extracted)
			sources = friend_sources(extracted)
			friend_ids = []
			for item in extracted:
				fid = friend_id_for(item)
				if fid is not None and fid not in friend_ids:
					friend_ids.append(fid)
		if not friend_ids:
			empty: dict[str, Any] = {"friend_ids": [], "messages": [], "items": [], "total": 0}
			if pagination is not None:
				empty["pagination"] = pagination
			return StepResult(empty)
		skipped = max(0, len(friend_ids) - limit)
		friend_ids = friend_ids[:limit]
		# 请求按 friendSource 分组、每批 50 串行（与 CLI 共用 services 实现）；
		# 任一批失败立即停止，不在风控 / 参数错误后继续发请求。
		items, stats = fetch_last_messages(platform, friend_ids, sources=sources)
		if "error" in stats and not items:
			failed = stats["failed_response"]
			if isinstance(failed, dict):
				# 完全失败按平台错误上抛（含风控终止语义与 recovery 约定）。
				_unwrap(platform, failed, "最近消息获取失败", context=context)
			risk_commands = _risk_commands(context)
			code, recoverable, recovery = _classify_action_error(
				"UNKNOWN",
				stats["error"],
				clean_command=risk_commands.get("clean_command"),
				doctor_command=risk_commands.get("doctor_command"),
			)
			raise WorkflowActionError(code, stats["error"], recoverable=recoverable, recovery_action=recovery)
	# 与 CLI `hr last-messages` 同一份归一化：items 保留平台原值供翻页 /
	# 后续步骤使用，messages 给 Agent 可读的摘要（Issue #2801）。
	messages = [normalize_last_message(item, set(friend_ids)) for item in items]
	payload: dict[str, Any] = {
		"friend_ids": friend_ids,
		"messages": messages,
		"requests": stats["requests"],
	}
	if skipped:
		payload["skipped"] = skipped
	if "error" in stats:
		# 部分批次失败：已拿到的条目照常返回，错误回显与 CLI `partial_error` 同口径。
		payload["partial_error"] = stats["error"]
	if pagination is not None:
		payload["pagination"] = pagination
	return StepResult(_with_items(payload, items))


def _recruiter_reply(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	friend_id = _required_int(inputs, "friend_id", minimum=1)
	# message 是发给真人的自由文本：LIST / dict / 数字经 str() 会把 Python repr
	# 当成真实消息发出去（Issue #1906）；只在 --input-json / MCP 路径可达。
	# 纯空白消息与 CLI `hr reply` 同口径拒绝：它会被当成一条空消息真正发给
	# 候选人（Issue #2516）。
	message = _required_nonblank_value(inputs, "message")
	# 编码失败发生在请求发出之前；必须在写标记落盘前拒绝（Issue #3017）。
	_reject_unencodable_inputs(message=message)
	return _recruiter_call(
		context,
		lambda platform: platform.send_message_by_friend(friend_id, message),
		"消息发送失败",
		write=True,
		check_command=f"hr chatmsg {friend_id}",
		# 复合动作不按异常类型推断；只认客户端在写前读取 / 脚本发送前标注的
		# request_sent=False（Issue #2898）。
		provably_unsent=exception_reports_request_unsent,
	)


def _recruiter_chat_history(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	friend_id = _required_int(inputs, "friend_id", minimum=1)
	return _recruiter_call(
		context,
		lambda platform: platform.chat_history(
			friend_id,
			count=_int_input(inputs, "count", 20, minimum=1),
			max_msg_id=_optional_int_input(inputs, "max_msg_id", minimum=1),
		),
		"聊天记录获取失败",
		list_extractor=message_list,
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
		# 写前读取 / 脚本发送前失败带显式 request_sent=False 时才撤销写标记；
		# 写后回读失败没有该证据，继续按结果未知保护（Issue #2898）。
		provably_unsent=exception_reports_request_unsent,
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
		# 同 recruiter_request_resume：只认显式写前证据（Issue #2898）。
		provably_unsent=exception_reports_request_unsent,
	)


def _recruiter_jobs_list(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	# 与 CLI `hr jobs list --page` 同口径：向导此前完全不读 page，传第 2 页
	# 仍返回第一页且报成功（Issue #2672）。
	page = _int_input(inputs, "page", 1, minimum=1)
	return _recruiter_call(
		context,
		lambda platform: platform.list_jobs(page=page),
		"职位列表获取失败",
		list_keys=("data", "jobs", "jobList", "items", "list", "result"),
		page=page,
	)


def _recruiter_jobs_detail(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	# 与 CLI `hr jobs detail` 同口径：空 job_id 无法命中任何职位（Issue #2516）。
	job_id = _required_nonblank_value(inputs, "job_id")
	return _recruiter_call(context, lambda platform: platform.job_detail(job_id), "职位详情获取失败")


def _recruiter_jobs_online(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	# 空白 job_id 的「上线」请求只会换来一次无意义的平台状态变更（Issue #2516）。
	job_id = _required_nonblank_value(inputs, "job_id")
	return _recruiter_call(
		context,
		lambda platform: platform.job_online(job_id),
		"职位上线失败",
		write=True,
		check_command="hr jobs list",
		# 单次写请求：派发前失败不保留「结果未知」标记（Issue #2441）。
		provably_unsent=candidate_action_provably_unsent,
	)


def _recruiter_jobs_offline(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	# 与 online 同理（Issue #2516）。
	job_id = _required_nonblank_value(inputs, "job_id")
	return _recruiter_call(
		context,
		lambda platform: platform.job_offline(job_id),
		"职位下线失败",
		write=True,
		check_command="hr jobs list",
		# 单次写请求：派发前失败不保留「结果未知」标记（Issue #2441）。
		provably_unsent=candidate_action_provably_unsent,
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
	"recruiter_recommendations": _recruiter_recommendations,
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
