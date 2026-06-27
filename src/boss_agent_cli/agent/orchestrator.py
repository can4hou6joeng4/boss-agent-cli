"""DeepSeek / OpenAI 兼容 tool-calling Agent 循环。"""

from __future__ import annotations

import json
import logging
from typing import Any

from boss_agent_cli.agent.toolkit import AgentToolkit
from boss_agent_cli.ai.service import AIService

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是 BOSS 直聘求职自动化助手。你可以调用工具搜索职位、读聊天记录、过滤不合适岗位、打标签、规划附件上传。

## 过滤规则（必须遵守）
- 排除外包、外派、驻场、第三方人力
- 排除劳务派遣、劳务公司
- 若用户要求排除异地/远程，则过滤含「异地」「远程办公」「居家」等描述的岗位
- 排除薪资低于用户指定最低 K 数的岗位

## 聊天交互
- 读 chat 后分析招聘者是否索要简历、薪资、学历证明
- 需要上传时调用 get_upload_assets 和 plan_upload
- 对明确的外包/劳务派遣岗位，mark_contact 标签「不合适」
- 生成专业、简短的回复建议（30–80 字）；当前版本不能自动发送消息，在最终回答里列出建议回复

## 输出
- 每轮工具调用后根据结果继续决策
- 任务完成时用中文总结：推荐岗位、已过滤原因、聊天处理建议、待上传文件路径
"""


class AgentOrchestrator:
	"""LLM + tools 自主决策循环。"""

	def __init__(
		self,
		ai_service: AIService,
		toolkit: AgentToolkit,
		*,
		max_rounds: int = 12,
	) -> None:
		self.ai_service = ai_service
		self.toolkit = toolkit
		self.max_rounds = max_rounds

	def run(self, user_goal: str, *, extra_system: str = "") -> dict[str, Any]:
		system = SYSTEM_PROMPT
		if extra_system:
			system = f"{system}\n\n## 用户附加规则\n{extra_system}"

		messages: list[dict[str, Any]] = [
			{"role": "system", "content": system},
			{"role": "user", "content": user_goal},
		]
		tool_calls_log: list[dict[str, Any]] = []

		for round_idx in range(self.max_rounds):
			completion = self.ai_service.chat_completion(
				messages,
				tools=self.toolkit.openai_tools(),
			)
			assistant_msg = completion["assistant_message"]
			messages.append(assistant_msg)

			if not completion["tool_calls"]:
				return {
					"ok": True,
					"summary": completion["content"] or "",
					"rounds": round_idx + 1,
					"tool_calls": tool_calls_log,
				}

			for call in completion["tool_calls"]:
				args = json.loads(call["arguments"]) if call["arguments"] else {}
				result = self.toolkit.execute(call["name"], args)
				tool_calls_log.append({"tool": call["name"], "arguments": args, "result": result})
				logger.debug("tool %s -> %s", call["name"], result.get("ok"))
				messages.append({
					"role": "tool",
					"tool_call_id": call["id"],
					"content": json.dumps(result, ensure_ascii=False),
				})

		return {
			"ok": False,
			"summary": "已达到最大工具调用轮次，请缩小任务范围后重试。",
			"rounds": self.max_rounds,
			"tool_calls": tool_calls_log,
		}
