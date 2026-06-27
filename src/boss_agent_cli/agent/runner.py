"""Agent主循环 - 自主决策与执行"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from boss_agent_cli.agent.chat_agent import ChatAgent
from boss_agent_cli.agent.config import AgentConfig
from boss_agent_cli.agent.job_agent import JobAgent
from boss_agent_cli.agent.orchestrator import AgentOrchestrator
from boss_agent_cli.agent.toolkit import AgentToolkit
from boss_agent_cli.ai.service import AIService, AIServiceError
from boss_agent_cli.api.client import BossClient
from boss_agent_cli.api.models import JobItem
from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.platforms import get_platform
from boss_agent_cli.tools.chat_tools import ChatTools
from boss_agent_cli.tools.filter_tools import FilterTools
from boss_agent_cli.tools.job_tools import JobTools

logger = logging.getLogger(__name__)


class AgentRunner:
	"""Agent 主运行器。"""

	def __init__(self, data_dir: Path, platform_name: str = "zhipin", cdp_url: str | None = None):
		self.data_dir = data_dir
		self.platform_name = platform_name
		self.agent_config = AgentConfig(data_dir)

		if not self.agent_config.is_configured():
			raise RuntimeError("AI 服务未配置，请先执行: python run.py agent config --api-key <key>")

		ai_cfg = self.agent_config.get_ai_config()
		api_key = self.agent_config.get_api_key()
		base_url = self.agent_config.get_base_url()
		if not api_key or not base_url:
			raise RuntimeError("AI 配置不完整，请重新执行 agent config")

		self.ai_service = AIService(
			base_url=base_url,
			api_key=api_key,
			model=str(ai_cfg["ai_model"]),
			temperature=float(ai_cfg.get("ai_temperature", 0.7)),
			max_tokens=int(ai_cfg.get("ai_max_tokens", 4096)),
		)

		self.auth = AuthManager(data_dir, platform=platform_name)
		self.client = BossClient(self.auth, cdp_url=cdp_url)
		self.platform = get_platform(platform_name)(self.client)

		self.filter_tools = FilterTools(
			min_salary=15,
			exclude_outsourcing=True,
			exclude_dispatch=True,
			exclude_remote=True,
		)
		self.job_tools = JobTools(self.client)
		self.chat_tools = ChatTools(self.client)
		self.job_agent = JobAgent(self.ai_service, self.filter_tools)
		self.chat_agent = ChatAgent(self.ai_service)
		self.toolkit = AgentToolkit(
			self.platform,
			self.job_tools,
			self.chat_tools,
			self.filter_tools,
			self.job_agent,
			self.chat_agent,
			self.agent_config,
		)
		self.orchestrator = AgentOrchestrator(self.ai_service, self.toolkit)

	def run_job_search_and_filter(self, query: str, **filters: Any) -> list[dict[str, Any]]:
		logger.info("搜索职位: %s", query)
		resp = self.job_tools.search_jobs(query, **filters)
		if resp.get("code") != 0:
			logger.error("搜索失败: %s", resp.get("message"))
			return []
		raw_jobs = (resp.get("zpData") or {}).get("jobList") or []
		jobs = [JobItem.from_api(item).to_dict() for item in raw_jobs]
		logger.info("搜索到 %d 个职位", len(jobs))
		filtered = self.job_agent.filter_and_analyze_jobs(jobs)
		logger.info("过滤后剩余 %d 个", len(filtered))
		return filtered

	def run_chat_analysis(self, security_id: str, job_info: dict[str, Any] | None = None) -> dict[str, Any]:
		result = self.toolkit.execute("analyze_chat", {
			"security_id": security_id,
			"job_title": (job_info or {}).get("title", ""),
			"company": (job_info or {}).get("company", ""),
		})
		return result

	def run_autonomous(self, goal: str, *, extra_rules: str = "") -> dict[str, Any]:
		try:
			return self.orchestrator.run(goal, extra_system=extra_rules)
		except AIServiceError as exc:
			return {"ok": False, "summary": f"AI 调用失败: {exc}", "tool_calls": []}

	def process_all_chats(self, *, auto_mark_unsuitable: bool = True) -> dict[str, Any]:
		"""批量分析沟通列表中的会话。"""
		listing = self.toolkit.execute("list_chats", {"page": 1})
		if not listing.get("ok"):
			return listing
		sessions: list[dict[str, Any]] = []
		for friend in listing.get("friends") or []:
			sid = friend.get("security_id")
			if not sid:
				continue
			analysis_result = self.toolkit.execute("analyze_chat", {
				"security_id": sid,
				"job_title": friend.get("title", ""),
				"company": friend.get("company", ""),
			})
			entry: dict[str, Any] = {
				"friend": friend,
				"analysis": analysis_result.get("analysis") if analysis_result.get("ok") else None,
				"upload_plan": analysis_result.get("upload_plan") if analysis_result.get("ok") else None,
				"error": analysis_result.get("error") if not analysis_result.get("ok") else None,
			}
			if auto_mark_unsuitable and analysis_result.get("ok"):
				analysis = analysis_result.get("analysis") or {}
				reason = str(analysis.get("reason", ""))
				if any(kw in reason for kw in ("外包", "劳务派遣", "外派")):
					mark = self.toolkit.execute("mark_contact", {"security_id": sid, "label": "不合适"})
					entry["marked_unsuitable"] = mark.get("ok", False)
			sessions.append(entry)
		return {"ok": True, "sessions": sessions, "count": len(sessions)}

	def close(self) -> None:
		self.client.close()

	def __enter__(self) -> "AgentRunner":
		return self

	def __exit__(self, exc_type, exc_val, exc_tb) -> None:
		self.close()
