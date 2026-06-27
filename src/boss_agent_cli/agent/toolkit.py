"""Agent 原生工具集 — 直接调用 Platform / BossClient，供 LLM tool calling 使用。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from boss_agent_cli.agent.config import AgentConfig
from boss_agent_cli.agent.chat_agent import ChatAgent
from boss_agent_cli.agent.job_agent import JobAgent
from boss_agent_cli.agent.utils import parse_llm_json
from boss_agent_cli.api.models import JobItem
from boss_agent_cli.commands.contact_lookup import FriendLookupLimitExceeded, find_friend_by_security_id
from boss_agent_cli.platforms.base import Platform
from boss_agent_cli.tools.filter_tools import FilterTools
from boss_agent_cli.tools.job_tools import JobTools
from boss_agent_cli.tools.chat_tools import ChatTools

_LABEL_MAP = {
	"新招呼": 1, "沟通中": 2, "已约面": 3, "已获取简历": 4,
	"已交换电话": 5, "已交换微信": 6, "不合适": 7, "牛人发起": 8, "收藏": 11,
}


def _openai_tool(name: str, description: str, parameters: dict[str, Any]) -> dict[str, Any]:
	return {
		"type": "function",
		"function": {"name": name, "description": description, "parameters": parameters},
	}


class AgentToolkit:
	"""封装 Agent 可调用的底层操作。"""

	def __init__(
		self,
		platform: Platform,
		job_tools: JobTools,
		chat_tools: ChatTools,
		filter_tools: FilterTools,
		job_agent: JobAgent,
		chat_agent: ChatAgent,
		agent_config: AgentConfig,
	) -> None:
		self.platform = platform
		self.job_tools = job_tools
		self.chat_tools = chat_tools
		self.filter_tools = filter_tools
		self.job_agent = job_agent
		self.chat_agent = chat_agent
		self.agent_config = agent_config

	def openai_tools(self) -> list[dict[str, Any]]:
		return [
			_openai_tool(
				"search_jobs",
				"按关键词搜索 BOSS 直聘职位，返回列表页摘要（不含完整 JD）。",
				{
					"type": "object",
					"properties": {
						"query": {"type": "string", "description": "搜索关键词"},
						"city": {"type": "string", "description": "城市，如 北京、上海"},
						"salary": {"type": "string", "description": "薪资区间，如 15-25K"},
						"page": {"type": "integer", "description": "页码，默认 1"},
					},
					"required": ["query"],
				},
			),
			_openai_tool(
				"get_job_detail",
				"获取职位详情（含完整描述），用于判断外包/劳务派遣/异地/低薪。",
				{
					"type": "object",
					"properties": {
						"job_id": {"type": "string", "description": "encryptJobId"},
						"security_id": {"type": "string", "description": "securityId（可选，用于关联卡片）"},
					},
					"required": ["job_id"],
				},
			),
			_openai_tool(
				"filter_jobs",
				"对职位列表做规则过滤（外包/劳务派遣/异地/低薪），可选 LLM 二次分析。",
				{
					"type": "object",
					"properties": {
						"jobs": {
							"type": "array",
							"items": {"type": "object"},
							"description": "search_jobs 返回的 jobs 数组",
						},
						"use_llm": {"type": "boolean", "description": "是否对通过规则的岗位做 LLM 分析，默认 false"},
					},
					"required": ["jobs"],
				},
			),
			_openai_tool(
				"list_chats",
				"获取沟通列表（招聘者会话摘要）。",
				{
					"type": "object",
					"properties": {"page": {"type": "integer", "description": "页码，默认 1"}},
				},
			),
			_openai_tool(
				"get_chat_messages",
				"获取与指定联系人的聊天记录。",
				{
					"type": "object",
					"properties": {
						"security_id": {"type": "string", "description": "联系人 securityId"},
						"page": {"type": "integer"},
						"count": {"type": "integer", "description": "条数，默认 30"},
					},
					"required": ["security_id"],
				},
			),
			_openai_tool(
				"analyze_chat",
				"分析聊天记录，判断是否需要上传简历/薪资证明/学历截图，并生成回复建议。",
				{
					"type": "object",
					"properties": {
						"security_id": {"type": "string"},
						"job_title": {"type": "string"},
						"company": {"type": "string"},
					},
					"required": ["security_id"],
				},
			),
			_openai_tool(
				"mark_contact",
				"给联系人打标签，如 不合适、沟通中、已获取简历。",
				{
					"type": "object",
					"properties": {
						"security_id": {"type": "string"},
						"label": {"type": "string", "description": "标签名：不合适/沟通中/已获取简历 等"},
						"remove": {"type": "boolean", "description": "是否移除标签"},
					},
					"required": ["security_id", "label"],
				},
			),
			_openai_tool(
				"get_upload_assets",
				"读取本地配置的简历/薪资截图/学历截图路径，供上传决策使用。",
				{"type": "object", "properties": {}},
			),
			_openai_tool(
				"plan_upload",
				"根据分析结果返回应上传的文件路径（实际上传需用户在 BOSS 网页或后续版本完成）。",
				{
					"type": "object",
					"properties": {
						"upload_resume": {"type": "boolean"},
						"upload_salary_proof": {"type": "boolean"},
						"upload_education": {"type": "boolean"},
					},
				},
			),
		]

	def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
		handlers = {
			"search_jobs": self._search_jobs,
			"get_job_detail": self._get_job_detail,
			"filter_jobs": self._filter_jobs,
			"list_chats": self._list_chats,
			"get_chat_messages": self._get_chat_messages,
			"analyze_chat": self._analyze_chat,
			"mark_contact": self._mark_contact,
			"get_upload_assets": self._get_upload_assets,
			"plan_upload": self._plan_upload,
		}
		handler = handlers.get(name)
		if handler is None:
			return {"ok": False, "error": f"未知工具: {name}"}
		try:
			return handler(arguments)
		except Exception as exc:
			return {"ok": False, "error": str(exc)}

	def _search_jobs(self, args: dict[str, Any]) -> dict[str, Any]:
		query = args["query"]
		filters: dict[str, Any] = {}
		if city := args.get("city"):
			filters["city"] = city
		if salary := args.get("salary"):
			filters["salary"] = salary
		resp = self.job_tools.search_jobs(query, **filters)
		if resp.get("code") != 0:
			return {"ok": False, "error": resp.get("message", "搜索失败"), "raw": resp}
		zp = resp.get("zpData") or {}
		raw_jobs = zp.get("jobList") or []
		jobs = [JobItem.from_api(item).to_dict() for item in raw_jobs]
		return {"ok": True, "count": len(jobs), "jobs": jobs}

	def _get_job_detail(self, args: dict[str, Any]) -> dict[str, Any]:
		resp = self.job_tools.get_job_detail(args["job_id"])
		if resp.get("code") != 0:
			return {"ok": False, "error": resp.get("message", "详情获取失败")}
		zp = resp.get("zpData") or {}
		job_info = zp.get("jobInfo") or zp
		brand = zp.get("brandComInfo") or {}
		merged = {
			"job_id": args["job_id"],
			"security_id": args.get("security_id", ""),
			"title": job_info.get("jobName") or job_info.get("positionName", ""),
			"company": brand.get("brandName") or job_info.get("brandName", ""),
			"salary": job_info.get("salaryDesc", ""),
			"city": job_info.get("locationName", ""),
			"description": job_info.get("postDescription") or job_info.get("jobDesc", ""),
			"experience": job_info.get("experienceName", ""),
			"education": job_info.get("degreeName", ""),
		}
		should_filter, reason = self.filter_tools.should_filter_job(merged)
		merged["rule_filter"] = {"filtered": should_filter, "reason": reason}
		return {"ok": True, "job": merged}

	def _filter_jobs(self, args: dict[str, Any]) -> dict[str, Any]:
		jobs = args.get("jobs") or []
		use_llm = bool(args.get("use_llm"))
		kept: list[dict[str, Any]] = []
		rejected: list[dict[str, Any]] = []
		for job in jobs:
			should_filter, reason = self.filter_tools.should_filter_job(job)
			if should_filter:
				rejected.append({**job, "_reject_reason": reason})
				continue
			if use_llm:
				analysis = self.job_agent.analyze_job(job)
				job = {**job, "_analysis": analysis}
				if not analysis.get("recommend"):
					rejected.append({**job, "_reject_reason": analysis.get("reason", "LLM 不推荐")})
					continue
			kept.append(job)
		return {"ok": True, "kept": kept, "rejected": rejected, "kept_count": len(kept)}

	def _list_chats(self, args: dict[str, Any]) -> dict[str, Any]:
		page = int(args.get("page") or 1)
		resp = self.chat_tools.get_friend_list(page=page)
		if not self.platform.is_success(resp):
			code, message = self.platform.parse_error(resp)
			return {"ok": False, "error": message, "code": code}
		data = self.platform.unwrap_data(resp) or {}
		items = data.get("result") or data.get("friendList") or []
		friends = []
		for item in items:
			friends.append({
				"name": item.get("name", ""),
				"title": item.get("title", ""),
				"company": item.get("brandName", ""),
				"last_msg": item.get("lastMsg", ""),
				"security_id": item.get("securityId", ""),
				"job_id": item.get("encryptJobId", ""),
				"uid": str(item.get("uid", "")),
				"unread": item.get("unreadMsgCount", 0),
			})
		return {"ok": True, "friends": friends, "count": len(friends)}

	def _get_chat_messages(self, args: dict[str, Any]) -> dict[str, Any]:
		security_id = args["security_id"]
		try:
			friend, err = find_friend_by_security_id(self.platform, security_id)
		except FriendLookupLimitExceeded as exc:
			return {"ok": False, "error": str(exc)}
		if err is not None:
			_, message = self.platform.parse_error(err)
			return {"ok": False, "error": message or "沟通列表获取失败"}
		if friend is None:
			return {"ok": False, "error": f"未找到 security_id={security_id}"}
		gid = str(friend.get("uid", ""))
		resp = self.chat_tools.get_chat_history(
			gid, security_id,
			page=int(args.get("page") or 1),
			count=int(args.get("count") or 30),
		)
		if not self.platform.is_success(resp):
			_, message = self.platform.parse_error(resp)
			return {"ok": False, "error": message or "聊天记录获取失败"}
		msg_data = self.platform.unwrap_data(resp) or {}
		raw = msg_data.get("messages") or msg_data.get("historyMsgList") or []
		messages = []
		for msg in raw:
			from_obj = msg.get("from") or {}
			is_self = isinstance(from_obj, dict) and str(from_obj.get("uid", "")) != gid
			messages.append({
				"from": "我" if is_self else "招聘者",
				"text": msg.get("text") or msg.get("body", {}).get("text", ""),
				"type": msg.get("type"),
			})
		return {
			"ok": True,
			"security_id": security_id,
			"friend_name": friend.get("name", ""),
			"gid": gid,
			"messages": messages,
		}

	def _analyze_chat(self, args: dict[str, Any]) -> dict[str, Any]:
		msg_result = self._get_chat_messages(args)
		if not msg_result.get("ok"):
			return msg_result
		job_info = None
		if args.get("job_title") or args.get("company"):
			job_info = {"title": args.get("job_title", ""), "brandName": args.get("company", "")}
		analysis = self.chat_agent.analyze_chat_context(msg_result["messages"], job_info)
		assets = self.agent_config.get_assets()
		upload_plan = self._plan_upload({
			"upload_resume": analysis.get("should_upload_resume"),
			"upload_salary_proof": analysis.get("should_upload_salary_proof"),
			"upload_education": analysis.get("should_upload_education"),
		})
		return {
			"ok": True,
			"security_id": args["security_id"],
			"friend_name": msg_result.get("friend_name"),
			"analysis": analysis,
			"upload_plan": upload_plan,
			"message_count": len(msg_result["messages"]),
		}

	def _mark_contact(self, args: dict[str, Any]) -> dict[str, Any]:
		security_id = args["security_id"]
		label = args["label"]
		label_id = _LABEL_MAP.get(label)
		if label_id is None and label.isdigit():
			label_id = int(label)
		if label_id is None:
			return {"ok": False, "error": f"未知标签: {label}"}
		try:
			friend, err = find_friend_by_security_id(self.platform, security_id)
		except FriendLookupLimitExceeded as exc:
			return {"ok": False, "error": str(exc)}
		if err is not None:
			_, message = self.platform.parse_error(err)
			return {"ok": False, "error": message}
		if friend is None:
			return {"ok": False, "error": f"未找到联系人 {security_id}"}
		friend_id = str(friend.get("uid", ""))
		resp = self.platform.friend_label(
			friend_id, label_id,
			friend_source=int(friend.get("friendSource") or 0),
			remove=bool(args.get("remove")),
		)
		if not self.platform.is_success(resp):
			_, message = self.platform.parse_error(resp)
			return {"ok": False, "error": message}
		return {"ok": True, "security_id": security_id, "label": label, "removed": bool(args.get("remove"))}

	def _get_upload_assets(self, _args: dict[str, Any]) -> dict[str, Any]:
		assets = self.agent_config.get_assets()
		resolved = {}
		for key, path in assets.items():
			if not path:
				resolved[key] = None
				continue
			p = Path(path).expanduser()
			resolved[key] = {"path": str(p), "exists": p.is_file()}
		return {"ok": True, "assets": resolved}

	def _plan_upload(self, args: dict[str, Any]) -> dict[str, Any]:
		assets = self.agent_config.get_assets()
		plan: list[dict[str, Any]] = []
		mapping = [
			("upload_resume", "resume_path", "在线简历/附件简历"),
			("upload_salary_proof", "salary_proof_path", "期望薪资/薪资证明截图"),
			("upload_education", "education_proof_path", "学历/学位截图"),
		]
		for arg_key, asset_key, label in mapping:
			if not args.get(arg_key):
				continue
			path = assets.get(asset_key)
			if not path:
				plan.append({"type": label, "status": "not_configured", "hint": f"agent config --{asset_key.replace('_', '-')} <路径>"})
				continue
			p = Path(path).expanduser()
			plan.append({
				"type": label,
				"path": str(p),
				"exists": p.is_file(),
				"status": "ready" if p.is_file() else "missing_file",
				"note": "当前版本需用户在 BOSS 聊天页手动上传；Agent 已识别上传意图并给出文件路径",
			})
		return {"ok": True, "plan": plan}
