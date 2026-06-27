"""职位智能 Agent — 基于 LLM 的职位筛选与决策。"""

from typing import Any

from boss_agent_cli.agent.utils import parse_llm_json
from boss_agent_cli.ai.service import AIService
from boss_agent_cli.tools.filter_tools import FilterTools


class JobAgent:
	"""职位智能决策 Agent。"""

	def __init__(self, ai_service: AIService, filter_tools: FilterTools):
		self.ai_service = ai_service
		self.filter_tools = filter_tools

	def analyze_job(self, job_data: dict[str, Any]) -> dict[str, Any]:
		should_filter, filter_reason = self.filter_tools.should_filter_job(job_data)
		if should_filter:
			return {"recommend": False, "reason": filter_reason, "method": "rule_based"}

		job_text = self._format_job_text(job_data)
		prompt = f"""你是一位资深的求职顾问。请分析以下职位是否值得推荐。

职位信息：
{job_text}

请以 JSON 格式返回分析结果：
{{
  "recommend": true,
  "score": 85,
  "reason": "推荐/不推荐的原因",
  "highlights": ["亮点1"],
  "concerns": ["顾虑1"],
  "suggested_action": "建议采取的行动"
}}

只返回 JSON，不要包含其他内容。"""

		try:
			response = self.ai_service.chat([{"role": "user", "content": prompt}])
			result = parse_llm_json(response)
			result["method"] = "llm_based"
			return result
		except Exception:
			return {
				"recommend": True,
				"reason": "LLM 分析失败，通过规则过滤",
				"method": "fallback",
			}

	def _format_job_text(self, job_data: dict[str, Any]) -> str:
		return f"""
职位名称: {job_data.get('title', '')}
公司名称: {job_data.get('company') or job_data.get('brandName', '')}
薪资: {job_data.get('salary', '')}
城市: {job_data.get('city', '')}
经验要求: {job_data.get('experience', '')}
学历要求: {job_data.get('education', '')}
公司规模: {job_data.get('scale', '')}
行业: {job_data.get('industry', '')}
职位描述: {job_data.get('description', '')}
"""

	def filter_and_analyze_jobs(self, jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
		results = []
		for job in jobs:
			analysis = self.analyze_job(job)
			job = {**job, "_analysis": analysis}
			if analysis.get("recommend"):
				results.append(job)
		return results
