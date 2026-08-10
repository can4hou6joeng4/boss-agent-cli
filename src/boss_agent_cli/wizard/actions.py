"""Reusable candidate, recruiter and local workflow actions."""

from __future__ import annotations

import socket
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Mapping

from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.api.endpoints import CITY_CODES
from boss_agent_cli.ai.config import AIConfigStore
from boss_agent_cli.ai.service import AIService, AIServiceError
from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.commands._platform import build_platform_instance
from boss_agent_cli.commands._recruiter_platform import build_recruiter_platform_instance
from boss_agent_cli.crawler.operations import crawl_status
from boss_agent_cli.crawler.service import CrawlService, CrawlSettings
from boss_agent_cli.crawler.transport import DrissionCrawlerSession
from boss_agent_cli.digest import build_digest
from boss_agent_cli.output import Logger
from boss_agent_cli.pipeline_state import build_pipeline_items, select_follow_up_candidates
from boss_agent_cli.resume.models import resume_to_text
from boss_agent_cli.resume.store import ResumeStore
from boss_agent_cli.search_filters import SearchFilterCriteria, resolve_welfare_keywords, run_search_pipeline
from boss_agent_cli.wizard.models import StepResult, WorkflowStatus
from boss_agent_cli.wizard.runner import Action, WorkflowActionError, WorkflowControl

PlatformFactory = Callable[[str, AuthManager, tuple[float, float], str | None], Any]


@dataclass(frozen=True)
class ActionContext:
	data_dir: Path
	platform: str
	role: str
	logger: Logger
	delay: tuple[float, float] = (1.5, 3.0)
	cdp_url: str | None = None
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
			return self.candidate_factory(self.platform, self.auth(), self.delay, self.cdp_url)
		return build_platform_instance(
			self.platform,
			self.auth(),
			delay=self.delay,
			cdp_url=self.cdp_url,
		)

	def recruiter_platform(self) -> Any:
		if self.recruiter_factory:
			return self.recruiter_factory(self.platform, self.auth(), self.delay, self.cdp_url)
		return build_recruiter_platform_instance(
			self.platform,
			self.auth(),
			delay=self.delay,
			cdp_url=self.cdp_url,
		)


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
		raw_params=dict(inputs.get("raw_params") or {}),
	)
	return pipeline(
		platform,
		cache,
		logger,
		criteria=criteria,
		start_page=int(inputs.get("page") or 1),
		max_pages=int(inputs.get("max_pages") or (5 if welfare_conditions else 1)),
		welfare_conditions=welfare_conditions,
	)


def execute_recruiter_candidate_search(platform: Any, inputs: Mapping[str, Any]) -> dict[str, Any]:
	"""Shared recruiter search call used by the command and workflow action."""
	return platform.search_geeks(
		str(inputs.get("query") or ""),
		city=_optional_str(inputs.get("city")),
		page=int(inputs.get("page") or 1),
		job_id=_optional_str(inputs.get("job_id")),
		experience=_optional_str(inputs.get("experience")),
		degree=_optional_str(inputs.get("degree")),
		age=_optional_str(inputs.get("age")),
		school_level=_optional_str(inputs.get("school_level")),
		activeness=_optional_str(inputs.get("activeness")),
		source=_optional_str(inputs.get("source")),
		select=bool(inputs.get("select", False)),
		salary=_optional_str(inputs.get("salary")),
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


def _required(inputs: Mapping[str, Any], name: str) -> Any:
	value = inputs.get(name)
	if value is None or value == "":
		raise WorkflowActionError(
			"WIZARD_INPUT_REQUIRED",
			f"workflow input {name!r} is required",
			recoverable=True,
			recovery_action=f"在 inputs 中提供 {name} 后用同一 run_id 重试",
		)
	return value


def _classify_action_error(code: str, message: str) -> tuple[str, bool, str]:
	"""Map platform error codes into wizard-facing code/recoverable/recovery."""
	text = message or ""
	if code == "UNKNOWN":
		if any(token in text for token in ("缺少必要参数", "参数错误", "非法参数", "invalid param")):
			code = "INVALID_PARAM"
		else:
			code = "NETWORK_ERROR"
	recoverable = code in {
		"AUTH_EXPIRED",
		"RATE_LIMITED",
		"NETWORK_ERROR",
		"TOKEN_REFRESH_FAILED",
		"NOT_SUPPORTED",
		"INVALID_PARAM",
	}
	if code in {"AUTH_EXPIRED", "TOKEN_REFRESH_FAILED"}:
		recovery = "boss login"
	elif code == "NOT_SUPPORTED":
		recovery = "切换平台或 workflow goal 后重试"
	elif code == "INVALID_PARAM":
		recovery = "换一条职位重试，或返回结果列表重新选择"
	else:
		recovery = "稍后重试，或返回主菜单选择其他事项"
	return code, recoverable, recovery


def _unwrap(platform: Any, response: dict[str, Any], fallback: str) -> Any:
	if platform.is_success(response):
		return platform.unwrap_data(response) or {}
	code, message = platform.parse_error(response)
	text = message or fallback
	code, recoverable, recovery = _classify_action_error(code, text)
	raise WorkflowActionError(
		code,
		text,
		recoverable=recoverable,
		recovery_action=recovery,
	)


def execute_candidate_detail(
	platform: Any,
	*,
	security_id: str,
	job_id: str | None = None,
	lid: str = "",
	data_dir: Path | None = None,
) -> dict[str, Any]:
	"""Fetch job detail with the same httpx → job_card fallback as `boss detail`."""
	from boss_agent_cli.api.models import employment_type_from_raw
	from boss_agent_cli.commands.detail import build_job_from_card

	greeted = False
	if data_dir is not None:
		with CacheStore(data_dir / "cache" / "boss_agent.db") as cache:
			greeted = cache.is_greeted(security_id)

	last_error: WorkflowActionError | None = None
	if job_id:
		raw = platform.job_detail(job_id)
		if platform.is_success(raw):
			platform_data = platform.unwrap_data(raw) or {}
			job_info = platform_data.get("jobInfo") or {}
			if job_info:
				boss_info = platform_data.get("bossInfo") or {}
				brand_info = platform_data.get("brandComInfo") or {}
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
					"skills": job_info.get("jobLabels", []) or job_info.get("skills", []),
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
			code, recoverable, recovery = _classify_action_error(code, text)
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
		platform_data = platform.unwrap_data(raw_card) or {}
		card = platform_data.get("jobCard") or {}
		if card:
			job = build_job_from_card(card, security_id=security_id, greeted=greeted)
			job["lid"] = lid
			job["channel"] = "job_card"
			if job_id and not job.get("job_id"):
				job["job_id"] = job_id
			return job

	if last_error is not None:
		raise last_error
	code, message = platform.parse_error(raw_card)
	text = message or "职位详情获取失败"
	code, recoverable, recovery = _classify_action_error(code, text)
	raise WorkflowActionError(code, text, recoverable=recoverable, recovery_action=recovery)


def _auth_status(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	if context.role == "candidate" and context.platform in {"qiancheng", "51job"}:
		with context.candidate_platform() as platform:
			_unwrap(platform, platform.user_info(), "当前平台不支持登录态检查")
	token = context.auth().check_status()
	if token is None:
		raise WorkflowActionError(
			"AUTH_REQUIRED",
			"未登录",
			recoverable=True,
			recovery_action=f"boss --platform {context.platform} login",
		)
	return StepResult({"authenticated": True, "platform": context.platform, "role": context.role})


def _candidate_search(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	_required(inputs, "query")
	with CacheStore(context.data_dir / "cache" / "boss_agent.db") as cache:
		with context.candidate_platform() as platform:
			result = execute_candidate_search(platform, cache, context.logger, inputs)
	return StepResult(
		{
			"items": result.items,
			"pagination": {
				"page": int(inputs.get("page") or 1),
				"has_more": result.has_more,
				"total": result.total or len(result.items),
			},
		}
	)


def _candidate_recommend(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	with context.candidate_platform() as platform:
		data = _unwrap(platform, platform.recommend_jobs(page=int(inputs.get("page") or 1)), "推荐职位获取失败")
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
			)
	return StepResult({"job": data, "result": data})


def _candidate_apply(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	security_id = str(_required(inputs, "security_id"))
	job_id = str(_required(inputs, "job_id"))
	with CacheStore(context.data_dir / "cache" / "boss_agent.db") as cache:
		if cache.is_applied(security_id, job_id):
			raise WorkflowActionError("ALREADY_APPLIED", "已对该职位发起过投递/立即沟通")
		with context.candidate_platform() as platform:
			data = _unwrap(platform, platform.apply(security_id, job_id, lid=str(inputs.get("lid") or "")), "投递失败")
		cache.record_apply(security_id, job_id)
	return StepResult({"security_id": security_id, "job_id": job_id, "result": data})


def _candidate_greet(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	security_id = str(_required(inputs, "security_id"))
	job_id = str(_required(inputs, "job_id"))
	with CacheStore(context.data_dir / "cache" / "boss_agent.db") as cache:
		if cache.is_greeted(security_id):
			raise WorkflowActionError("ALREADY_GREETED", "已向该招聘者打过招呼")
		with context.candidate_platform() as platform:
			data = _unwrap(
				platform, platform.greet(security_id, job_id, str(inputs.get("message") or "")), "打招呼失败"
			)
		cache.record_greet(security_id, job_id)
	return StepResult({"security_id": security_id, "job_id": job_id, "result": data})


def _candidate_friend(platform: Any, security_id: str) -> dict[str, Any]:
	data = _unwrap(platform, platform.friend_list(page=1), "沟通列表获取失败")
	items = data.get("result") or data.get("friendList") or []
	for item in items:
		if str(item.get("securityId") or item.get("security_id") or "") == security_id:
			return item
	raise WorkflowActionError("JOB_NOT_FOUND", f"沟通列表中未找到联系人: {security_id}")


def _candidate_exchange(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	security_id = str(_required(inputs, "security_id"))
	exchange_type = 2 if str(inputs.get("type") or "phone") == "wechat" else 1
	with context.candidate_platform() as platform:
		friend = _candidate_friend(platform, security_id)
		data = _unwrap(
			platform,
			platform.exchange_contact(
				security_id,
				str(friend.get("uid") or ""),
				str(friend.get("name") or "-"),
				exchange_type=exchange_type,
			),
			"联系方式交换失败",
		)
	return StepResult({"security_id": security_id, "result": data})


_LABEL_IDS = {"新招呼": 1, "沟通中": 2, "已约面": 3, "已获取简历": 4, "不合适": 7, "收藏": 11}


def _candidate_mark(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	security_id = str(_required(inputs, "security_id"))
	label = str(_required(inputs, "label"))
	label_id = int(label) if label.isdigit() else _LABEL_IDS.get(label)
	if label_id is None:
		raise WorkflowActionError("INVALID_PARAM", f"unknown label: {label}")
	with context.candidate_platform() as platform:
		friend = _candidate_friend(platform, security_id)
		data = _unwrap(
			platform,
			platform.friend_label(
				str(friend.get("uid") or ""),
				label_id,
				int(friend.get("friendSource") or 0),
				remove=bool(inputs.get("remove", False)),
			),
			"联系人标签更新失败",
		)
	return StepResult({"security_id": security_id, "label": label, "result": data})


def _candidate_chat(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	with context.candidate_platform() as platform:
		data = _unwrap(platform, platform.friend_list(page=int(inputs.get("page") or 1)), "沟通列表获取失败")
	items = _extract_list_items(data, ("result", "friendList", "list", "items"))
	return StepResult(_with_items({"result": data}, items))


def _candidate_chat_history(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	with context.candidate_platform() as platform:
		response = platform.chat_history(
			str(_required(inputs, "gid")),
			str(_required(inputs, "security_id")),
			page=int(inputs.get("page") or 1),
			count=int(inputs.get("count") or 20),
		)
		data = _unwrap(platform, response, "聊天记录获取失败")
	items = _extract_list_items(data, ("messageList", "messages", "list", "items", "result"))
	return StepResult(
		_with_items(
			{
				"result": data,
				"gid": str(inputs.get("gid") or ""),
				"security_id": str(inputs.get("security_id") or ""),
			},
			items,
		)
	)


def _candidate_pipeline_data(context: ActionContext, inputs: Mapping[str, Any]) -> list[dict[str, Any]]:
	with context.candidate_platform() as platform:
		chat_data = _unwrap(platform, platform.friend_list(page=1), "沟通列表获取失败")
		interview_data = _unwrap(platform, platform.interview_data(), "面试列表获取失败")
	chat_items = chat_data.get("result") or chat_data.get("friendList") or []
	interview_items = interview_data.get("interviewList") or []
	return build_pipeline_items(
		chat_items=chat_items,
		interview_items=interview_items,
		now_ts_ms=int(inputs.get("now_ts_ms") or time.time() * 1000),
		stale_days=int(inputs.get("days_stale") or 3),
	)


def _candidate_pipeline(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	items = _candidate_pipeline_data(context, inputs)
	return StepResult({"items": items, "total": len(items)})


def _candidate_digest(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	items = _candidate_pipeline_data(context, inputs)
	follow_ups = select_follow_up_candidates(items)
	data = build_digest(
		new_matches=[item for item in items if item.get("source") == "chat" and item.get("stage") == "reply_needed"],
		follow_ups=follow_ups,
		interviews=[item for item in items if item.get("source") == "interview"],
	)
	return StepResult(data)


def _local_shortlist(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	with CacheStore(context.data_dir / "cache" / "boss_agent.db") as cache:
		items = cache.list_shortlist()
	return StepResult({"items": items, "total": len(items)})


def _local_resumes(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	items = ResumeStore(context.data_dir / "resumes").list_all()
	return StepResult({"items": items, "total": len(items)})


def _ai_assist(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	resume_name = str(_required(inputs, "resume"))
	resume = ResumeStore(context.data_dir / "resumes").get(resume_name)
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
			recovery_action="boss ai config --provider <provider> --model <model> --api-key <key>",
		)
	service = AIService(str(base_url), api_key, str(model))
	try:
		result = service.chat(
			[
				{"role": "system", "content": "你是求职顾问。"},
				{"role": "user", "content": f"{inputs['prompt']}\n\n简历:\n{resume_to_text(resume)}"},
			]
		)
	except AIServiceError as exc:
		raise WorkflowActionError(
			"AI_API_ERROR", str(exc), recoverable=True, recovery_action="检查 AI 配置后重试"
		) from exc
	return StepResult({"resume": resume_name, "result": result})


def _candidate_export(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	from boss_agent_cli.commands.export import _prepare_export_items, _write_to_file

	with CacheStore(context.data_dir / "cache" / "boss_agent.db") as cache:
		with context.candidate_platform() as platform:
			result = execute_candidate_search(platform, cache, context.logger, inputs)
	fmt = str(inputs.get("format") or "json")
	if fmt not in {"json", "csv"}:
		raise WorkflowActionError("INVALID_PARAM", "wizard export format 仅支持 json/csv")
	output = Path(str(inputs.get("output") or context.data_dir / "exports" / f"wizard-jobs.{fmt}"))
	output.parent.mkdir(parents=True, exist_ok=True)
	items = _prepare_export_items(result.items, include_private=bool(inputs.get("include_private", False)))
	_write_to_file(items, fmt, str(output))
	return StepResult({"path": str(output), "format": fmt, "count": len(items)}, artifacts=(str(output),))


def _candidate_watch(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	action = str(inputs.get("action") or "list")
	with CacheStore(context.data_dir / "cache" / "boss_agent.db") as cache:
		if action == "list":
			items = cache.list_saved_searches()
			return StepResult({"items": items, "total": len(items)})
		name = str(_required(inputs, "name"))
		if action == "add":
			params = {
				key: inputs.get(key)
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
				)
			}
			_required(inputs, "query")
			cache.save_saved_search(name, params)
			return StepResult({"action": "add", "name": name, "params": params})
		if action == "remove":
			return StepResult({"action": "remove", "name": name, "removed": cache.delete_saved_search(name)})
		if action != "run":
			raise WorkflowActionError("INVALID_PARAM", f"unknown watch action: {action}")
		record = cache.get_saved_search(name)
		if record is None:
			raise WorkflowActionError("JOB_NOT_FOUND", f"未找到 watch: {name}")
		with context.candidate_platform() as platform:
			result = execute_candidate_search(platform, cache, context.logger, record["params"])
		watch_result = cache.record_watch_results(name, result.items)
		return StepResult({"name": name, **watch_result})


def _unused_port() -> int:
	with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
		connection.bind(("127.0.0.1", 0))
		return int(connection.getsockname()[1])


def _crawl_seed_session(context: ActionContext) -> tuple[dict[str, str], str]:
	"""Load boss-login cookies for the isolated crawl browser profile."""
	from boss_agent_cli.auth.manager import AuthRequired

	try:
		token = context.auth().get_token()
	except AuthRequired as exc:
		raise WorkflowActionError(
			"AUTH_REQUIRED",
			str(exc),
			recoverable=True,
			recovery_action=f"boss --platform {context.platform} login",
		) from exc
	raw_cookies = token.get("cookies") if isinstance(token, dict) else None
	if not isinstance(raw_cookies, dict) or not raw_cookies:
		raise WorkflowActionError(
			"AUTH_REQUIRED",
			"本地登录态缺少 Cookie，采集浏览器无法自动登录",
			recoverable=True,
			recovery_action=f"boss --platform {context.platform} login",
		)
	cookies = {str(name): str(value) for name, value in raw_cookies.items() if name and value is not None}
	if context.platform in {"zhipin", ""} and "wt2" not in cookies:
		raise WorkflowActionError(
			"AUTH_REQUIRED",
			"登录态缺少 wt2，采集浏览器会被当成未登录并立即退出",
			recoverable=True,
			recovery_action="boss login",
		)
	user_agent = str(token.get("user_agent") or "")
	return cookies, user_agent


def _crawl_service(context: ActionContext, cache: CacheStore) -> CrawlService:
	control = context.workflow_control
	seed_cookies, user_agent = _crawl_seed_session(context)
	return CrawlService(
		cache,
		data_dir=context.data_dir,
		transport_factory=lambda settings: DrissionCrawlerSession(
			profile_path=settings.profile_path,
			chrome_path=settings.chrome_path,
			cdp_port=settings.cdp_port,
			hook_profile=settings.hook_profile,
			hook_dir=settings.hook_dir,
			seed_cookies=seed_cookies,
			user_agent=user_agent,
		),
		control_probe=control.poll if control is not None else None,
		on_run_created=control.bind_inner_run if control is not None else None,
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


def _crawl_outcome_result(outcome: Any, cache: CacheStore | None = None) -> StepResult:
	data = outcome.as_dict()
	if cache is not None:
		data = _attach_wizard_jobs(data if isinstance(data, dict) else {}, cache, str(outcome.run_id))
	if outcome.status == "timeout_stopped":
		raise WorkflowActionError(
			"WORKFLOW_TIMEOUT",
			outcome.error or "workflow exceeded its timeout",
			recoverable=True,
			recovery_action=f"boss crawl resume {outcome.run_id}",
		)
	if outcome.status in {"workflow_stopped", "stopped"} and "stop requested" in str(outcome.error or ""):
		raise WorkflowActionError(
			"WORKFLOW_STOPPED",
			outcome.error or "crawl stopped",
			recoverable=True,
			recovery_action=f"boss crawl resume {outcome.run_id}",
		)
	if outcome.status in {"risk_stopped", "budget_stopped"}:
		# 风控停止时 service 会 keep Chrome；把标记写入结果供向导文案使用。
		if isinstance(data, dict):
			browser = data.get("browser") if isinstance(data.get("browser"), dict) else {}
			data = {
				**data,
				"browser_kept_open": outcome.status == "risk_stopped",
				"cdp_port": browser.get("cdp_port") or data.get("cdp_port"),
			}
		return StepResult(
			data,
			status=WorkflowStatus.WAITING_INPUT,
			next_action=f"boss crawl resume {outcome.run_id}",
			artifacts=tuple(outcome.output_paths.values()),
		)
	if outcome.status != "completed":
		raise WorkflowActionError(
			"NETWORK_ERROR",
			outcome.error or f"crawl stopped with status {outcome.status}",
			recoverable=True,
			recovery_action=f"boss crawl resume {outcome.run_id}",
		)
	return StepResult(data, artifacts=tuple(outcome.output_paths.values()))


def _crawl_start(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	# 恢复 waiting_input 时若已有内部 crawl run_id，必须续跑而不是新建采集。
	existing_run_id = _optional_str(inputs.get("run_id") or inputs.get("crawl_run_id"))
	if existing_run_id is None:
		prior_step = prior.get("crawl_start")
		if isinstance(prior_step, Mapping):
			data = prior_step.get("data") if isinstance(prior_step.get("data"), Mapping) else prior_step
			if isinstance(data, Mapping):
				existing_run_id = _optional_str(data.get("run_id"))
	if existing_run_id:
		return _crawl_resume(context, {"run_id": existing_run_id, **dict(inputs)}, prior)

	crawl_config = dict((context.config or {}).get("crawl") or {})
	city = str(_required(inputs, "city"))
	city_code = CITY_CODES.get(city, city if city.isdigit() else None)
	if city_code is None:
		raise WorkflowActionError("INVALID_PARAM", f"unknown city: {city}")
	settings = CrawlSettings(
		query=str(_required(inputs, "query")),
		city_code=city_code,
		pages=int(inputs.get("pages") or 5),
		with_detail=bool(inputs.get("with_detail", False)),
		profile_path=context.data_dir / "crawl" / "chrome-profile",
		chrome_path=crawl_config.get("chrome_path"),
		cdp_port=int(crawl_config.get("cdp_port") or _unused_port()),
		hook_profile=str(inputs.get("hook_profile") or "none"),
		max_requests=int(crawl_config.get("max_requests") or 20),
		max_details=int(crawl_config.get("max_details") or 50),
		max_seconds=int(crawl_config.get("max_seconds") or 600),
		max_retries=int(crawl_config.get("max_retries") or 1),
	)
	with CacheStore(context.data_dir / "cache" / "boss_agent.db") as cache:
		outcome = _crawl_service(context, cache).create_and_run(settings)
		return _crawl_outcome_result(outcome, cache)


def _crawl_status(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	with CacheStore(context.data_dir / "cache" / "boss_agent.db") as cache:
		try:
			run_id = str(_required(inputs, "run_id"))
			data = crawl_status(cache, run_id)
			data = _attach_wizard_jobs(data, cache, run_id)
		except KeyError as exc:
			raise WorkflowActionError("JOB_NOT_FOUND", str(exc)) from exc
	return StepResult(data)


def _crawl_resume(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	run_id = str(_required(inputs, "run_id"))
	with CacheStore(context.data_dir / "cache" / "boss_agent.db") as cache:
		try:
			outcome = _crawl_service(context, cache).resume(
				run_id,
				pages=int(inputs["pages"]) if inputs.get("pages") else None,
				with_detail=bool(inputs.get("with_detail", False)),
				clear_stop=True,
			)
		except KeyError as exc:
			raise WorkflowActionError("JOB_NOT_FOUND", f"未找到 crawl run: {run_id}") from exc
		return _crawl_outcome_result(outcome, cache)


def _crawl_stop(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	run_id = str(_required(inputs, "run_id"))
	with CacheStore(context.data_dir / "cache" / "boss_agent.db") as cache:
		if not cache.request_crawl_stop(run_id):
			raise WorkflowActionError("JOB_NOT_FOUND", f"未找到 crawl run: {run_id}")
	return StepResult({"run_id": run_id, "status": "stop_requested"})


def _recruiter_call(
	context: ActionContext,
	call: Callable[[Any], dict[str, Any]],
	fallback: str,
	*,
	list_keys: tuple[str, ...] = (),
) -> StepResult:
	with context.recruiter_platform() as platform:
		data = _unwrap(platform, call(platform), fallback)
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
			page=int(inputs.get("page") or 1),
			label_id=int(inputs.get("label_id") or 0),
			job_id=_optional_str(inputs.get("job_id")),
		),
		"投递申请获取失败",
		list_keys=("friendList", "result", "list", "items"),
	)


def _recruiter_resume(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	return _recruiter_call(
		context,
		lambda platform: platform.view_geek(
			str(_required(inputs, "geek_id")),
			str(_required(inputs, "job_id")),
			_optional_str(inputs.get("security_id")),
		),
		"候选人简历获取失败",
	)


def _recruiter_chat(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	return _recruiter_applications(context, inputs, prior)


def _recruiter_last_messages(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	with context.recruiter_platform() as platform:
		friend_data = _unwrap(
			platform,
			platform.friend_list(
				page=int(inputs.get("page") or 1),
				label_id=int(inputs.get("label_id") or 0),
				job_id=_optional_str(inputs.get("job_id")),
			),
			"沟通列表获取失败",
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
		data = _unwrap(platform, platform.last_messages(friend_ids), "最近消息获取失败")
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
	friend_id = int(_required(inputs, "friend_id"))
	message = str(_required(inputs, "message"))
	return _recruiter_call(
		context, lambda platform: platform.send_message_by_friend(friend_id, message), "消息发送失败"
	)


def _recruiter_chat_history(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	friend_id = int(_required(inputs, "friend_id"))
	return _recruiter_call(
		context,
		lambda platform: platform.chat_history(
			friend_id,
			count=int(inputs.get("count") or 20),
			max_msg_id=int(inputs["max_msg_id"]) if inputs.get("max_msg_id") else None,
		),
		"聊天记录获取失败",
		list_keys=("messageList", "messages", "list", "items", "result"),
	)


def _recruiter_request_resume(
	context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]
) -> StepResult:
	friend_id = int(_required(inputs, "friend_id"))
	return _recruiter_call(
		context,
		lambda platform: platform.exchange_request_by_friend(friend_id, exchange_type=4),
		"附件简历请求失败",
	)


def _recruiter_exchange_contact(
	context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]
) -> StepResult:
	friend_id = int(_required(inputs, "friend_id"))
	exchange_type = 2 if str(inputs.get("type") or "phone") == "wechat" else 1
	return _recruiter_call(
		context,
		lambda platform: platform.exchange_request_by_friend(friend_id, exchange_type=exchange_type),
		"联系方式交换请求失败",
	)


def _recruiter_jobs_list(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	return _recruiter_call(
		context,
		lambda platform: platform.list_jobs(),
		"职位列表获取失败",
		list_keys=("jobs", "jobList", "items", "list", "result"),
	)


def _recruiter_jobs_detail(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	job_id = str(_required(inputs, "job_id"))
	return _recruiter_call(context, lambda platform: platform.job_detail(job_id), "职位详情获取失败")


def _recruiter_jobs_online(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	job_id = str(_required(inputs, "job_id"))
	return _recruiter_call(context, lambda platform: platform.job_online(job_id), "职位上线失败")


def _recruiter_jobs_offline(context: ActionContext, inputs: Mapping[str, Any], prior: Mapping[str, Any]) -> StepResult:
	job_id = str(_required(inputs, "job_id"))
	return _recruiter_call(context, lambda platform: platform.job_offline(job_id), "职位下线失败")


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
