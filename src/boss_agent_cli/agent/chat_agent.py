"""聊天智能 Agent — 基于 LLM 的聊天分析与自动交互决策。"""

from typing import Any

from boss_agent_cli.agent.utils import parse_llm_json
from boss_agent_cli.ai.service import AIService


class ChatAgent:
	"""聊天智能决策 Agent。"""

	def __init__(self, ai_service: AIService):
		self.ai_service = ai_service

	def analyze_chat_context(
		self,
		chat_history: list[dict[str, Any]],
		job_info: dict[str, Any] | None = None,
	) -> dict[str, Any]:
		chat_text = self._format_chat_history(chat_history)
		job_text = self._format_job_info(job_info) if job_info else "无职位信息"

		prompt = f"""你是一位智能求职助手。请分析以下聊天记录，判断是否需要上传文件。

职位信息：
{job_text}

聊天记录：
{chat_text}

请以 JSON 格式返回分析结果：
{{
  "should_upload_resume": false,
  "should_upload_salary_proof": false,
  "should_upload_education": false,
  "should_reply": false,
  "reply_message": "建议的回复消息（30-80字）",
  "reason": "决策原因",
  "urgency": "high"
}}

判断标准：
- 招聘者询问简历、要求发简历 → should_upload_resume: true
- 招聘者询问薪资、期望薪资 → should_upload_salary_proof: true
- 招聘者询问学历、学位 → should_upload_education: true
- 招聘者提出问题需要回复 → should_reply: true
- 若岗位/聊天涉及外包、劳务派遣，在 reason 中注明

只返回 JSON，不要包含其他内容。"""

		try:
			response = self.ai_service.chat([{"role": "user", "content": prompt}])
			return parse_llm_json(response)
		except Exception as exc:
			return {
				"should_upload_resume": False,
				"should_upload_salary_proof": False,
				"should_upload_education": False,
				"should_reply": False,
				"reason": f"LLM 分析失败: {exc}",
				"urgency": "low",
			}

	def _format_chat_history(self, chat_history: list[dict[str, Any]]) -> str:
		if not chat_history:
			return "无聊天记录"
		lines = []
		for msg in chat_history:
			role = msg.get("from", "unknown")
			if role in ("boss", "招聘者"):
				role_name = "招聘者"
			elif role in ("me", "我"):
				role_name = "我"
			else:
				role_name = str(role)
			content = msg.get("text") or msg.get("msg", "")
			lines.append(f"{role_name}: {content}")
		return "\n".join(lines)

	def _format_job_info(self, job_info: dict[str, Any]) -> str:
		return f"""
职位名称: {job_info.get('title', '')}
公司名称: {job_info.get('brandName') or job_info.get('company', '')}
薪资: {job_info.get('salary', '')}
"""

	def generate_reply(self, chat_history: list[dict[str, Any]], context: str = "") -> str:
		chat_text = self._format_chat_history(chat_history)
		prompt = f"""你是一位专业的求职者。请根据以下聊天记录生成一条回复。

聊天记录：
{chat_text}

上下文：
{context}

请生成一条 30-80 字的回复，专业、礼貌。只返回回复内容。"""

		try:
			response = self.ai_service.chat([{"role": "user", "content": prompt}])
			return response.strip()
		except Exception:
			return "您好，我对该岗位很感兴趣，希望能进一步了解。"
