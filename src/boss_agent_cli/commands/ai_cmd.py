"""AI 简历优化命令组。

子命令：config / analyze-jd / polish / optimize / suggest
"""

import json
from pathlib import Path

import click

from boss_agent_cli.ai.config import AIConfigStore
from boss_agent_cli.ai.prompts import (
	CHAT_COACH_PROMPT,
	CHAT_REPLY_PROMPT,
	INTERVIEW_PREP_PROMPT,
	JD_ANALYSIS_PROMPT,
	RESUME_OPTIMIZE_FOR_JD_PROMPT,
	RESUME_POLISH_PROMPT,
	RESUME_SUGGEST_PROMPT,
)
from boss_agent_cli.ai.service import AIService, AIServiceError
from boss_agent_cli.display import handle_error_output, handle_output
from boss_agent_cli.resume.models import resume_to_text
from boss_agent_cli.resume.store import ResumeStore


def _get_ai_config_store(ctx) -> AIConfigStore:
	return AIConfigStore(ctx.obj["data_dir"])


def _get_resume_store(ctx) -> ResumeStore:
	return ResumeStore(ctx.obj["data_dir"] / "resumes")


def _create_ai_service(ctx) -> AIService | None:
	"""从配置创建 AIService 实例，未配置时返回 None。"""
	store = _get_ai_config_store(ctx)
	if not store.is_configured():
		return None
	config = store.load_config()
	api_key = store.get_api_key()
	base_url = store.get_base_url()
	if not api_key or not base_url:
		return None
	return AIService(
		base_url=base_url,
		api_key=api_key,
		model=config["ai_model"],
		temperature=config.get("ai_temperature", 0.7),
		max_tokens=config.get("ai_max_tokens", 4096),
	)


def _require_ai_service(ctx) -> AIService | None:
	"""获取 AIService，未配置时输出错误信封并返回 None。"""
	svc = _create_ai_service(ctx)
	if svc is None:
		handle_error_output(
			ctx, "ai",
			code="AI_NOT_CONFIGURED",
			message="AI 服务未配置",
			recoverable=True,
			recovery_action="boss ai config --provider <provider> --model <model> --api-key <key>",
		)
		ctx.exit(1)
		return None
	return svc


def _load_resume_text(ctx, resume_name: str) -> str | None:
	"""加载简历纯文本，不存在时输出错误。"""
	store = _get_resume_store(ctx)
	resume = store.get(resume_name)
	if resume is None:
		handle_error_output(
			ctx, "ai",
			code="RESUME_NOT_FOUND",
			message=f"简历 '{resume_name}' 不存在",
		)
		ctx.exit(1)
		return None
	return resume_to_text(resume)


def _call_ai(ctx, svc: AIService, prompt: str) -> dict | None:
	"""调用 AI 并解析 JSON 结果，失败时输出错误信封。"""
	try:
		raw = svc.chat([
			{"role": "system", "content": "你是求职顾问。所有输出使用 JSON 格式。"},
			{"role": "user", "content": prompt},
		])
	except AIServiceError as exc:
		handle_error_output(
			ctx, "ai",
			code="AI_API_ERROR",
			message=f"AI 服务调用失败: {exc}",
			recoverable=True,
			recovery_action="检查网络连接和密钥配置，重试",
		)
		ctx.exit(1)
		return None

	# 尝试提取 JSON（兼容 markdown 代码块包裹）
	text = raw.strip()
	if text.startswith("```"):
		lines = text.split("\n")
		lines = [ln for ln in lines if not ln.startswith("```")]
		text = "\n".join(lines).strip()

	try:
		return json.loads(text)
	except json.JSONDecodeError:
		handle_error_output(
			ctx, "ai",
			code="AI_PARSE_ERROR",
			message="AI 返回结果解析失败",
			recoverable=True,
			recovery_action="重试（模型输出不稳定时可能发生）",
		)
		ctx.exit(1)
		return None


@click.group("ai")
def ai_group():
	"""AI 简历优化。"""


@ai_group.command("config")
@click.option("--provider", default=None, help="AI 提供商（openai/deepseek/moonshot/openrouter/qwen/zhipu/siliconflow/custom）")
@click.option("--model", default=None, help="模型名称（如 gpt-4o / claude-sonnet-4.5 / deepseek-chat）")
@click.option("--api-key", default=None, help="API 密钥（将加密存储）")
@click.option("--base-url", default=None, help="自定义 API 基础地址（provider=custom 或自建代理时使用）")
@click.option("--temperature", default=None, type=float, help="生成温度")
@click.option("--max-tokens", default=None, type=int, help="最大令牌数")
@click.pass_context
def ai_config_cmd(ctx, provider, model, api_key, base_url, temperature, max_tokens):
	"""查看或设置 AI 服务配置"""
	store = _get_ai_config_store(ctx)

	# 无参数时显示当前配置
	has_updates = any([provider, model, api_key, base_url, temperature is not None, max_tokens is not None])
	if not has_updates:
		config = store.load_config()
		config["api_key_set"] = store.get_api_key() is not None
		config.pop("ai_api_key", None)
		handle_output(
			ctx, "ai-config", config,
			hints={"next_actions": [
				"boss ai config --provider openai --model gpt-4o --api-key <key>",
			]},
		)
		return

	# 有参数时更新配置
	updates = {}
	if provider is not None:
		updates["ai_provider"] = provider
	if model is not None:
		updates["ai_model"] = model
	if base_url is not None:
		updates["ai_base_url"] = base_url
	if temperature is not None:
		updates["ai_temperature"] = temperature
	if max_tokens is not None:
		updates["ai_max_tokens"] = max_tokens
	if updates:
		store.save_config(**updates)
	if api_key is not None:
		store.save_api_key(api_key)

	handle_output(
		ctx, "ai-config",
		{"action": "update", "updated_fields": list(updates.keys()) + (["api_key"] if api_key else [])},
		hints={"next_actions": ["boss ai config", "boss ai analyze-jd <security_id> --resume <name>"]},
	)


@ai_group.command("analyze-jd")
@click.argument("jd_text")
@click.option("--resume", "resume_name", required=True, help="对比的本地简历名称")
@click.pass_context
def ai_analyze_jd_cmd(ctx, jd_text, resume_name):
	"""分析职位描述并评估简历匹配度

	JD_TEXT 可以是职位描述文本，也可以是 @文件路径 读取文件内容。
	"""
	svc = _require_ai_service(ctx)
	if svc is None:
		return

	# 支持 @file 语法读取文件
	if jd_text.startswith("@"):
		file_path = Path(jd_text[1:])
		if not file_path.exists():
			handle_error_output(ctx, "ai", code="INVALID_PARAM", message=f"文件 '{file_path}' 不存在")
			ctx.exit(1)
			return
		jd_text = file_path.read_text(encoding="utf-8")

	resume_text = _load_resume_text(ctx, resume_name)
	if resume_text is None:
		return

	prompt = JD_ANALYSIS_PROMPT.format(jd_text=jd_text, resume_text=resume_text)
	result = _call_ai(ctx, svc, prompt)
	if result is None:
		return

	handle_output(
		ctx, "ai-analyze-jd", result,
		hints={"next_actions": [
			f"boss ai optimize {resume_name} --jd <jd_text>",
			f"boss ai suggest {resume_name} --jd <jd_text>",
		]},
	)


@ai_group.command("polish")
@click.argument("resume_name")
@click.pass_context
def ai_polish_cmd(ctx, resume_name):
	"""通用简历润色优化"""
	svc = _require_ai_service(ctx)
	if svc is None:
		return

	resume_text = _load_resume_text(ctx, resume_name)
	if resume_text is None:
		return

	prompt = RESUME_POLISH_PROMPT.format(resume_text=resume_text)
	result = _call_ai(ctx, svc, prompt)
	if result is None:
		return

	handle_output(
		ctx, "ai-polish", result,
		hints={"next_actions": [f"boss resume show {resume_name}"]},
	)


@ai_group.command("optimize")
@click.argument("resume_name")
@click.option("--jd", "jd_text", required=True, help="目标职位描述文本或 @文件路径")
@click.pass_context
def ai_optimize_cmd(ctx, resume_name, jd_text):
	"""基于目标职位描述优化简历"""
	svc = _require_ai_service(ctx)
	if svc is None:
		return

	if jd_text.startswith("@"):
		file_path = Path(jd_text[1:])
		if not file_path.exists():
			handle_error_output(ctx, "ai", code="INVALID_PARAM", message=f"文件 '{file_path}' 不存在")
			ctx.exit(1)
			return
		jd_text = file_path.read_text(encoding="utf-8")

	resume_text = _load_resume_text(ctx, resume_name)
	if resume_text is None:
		return

	prompt = RESUME_OPTIMIZE_FOR_JD_PROMPT.format(jd_text=jd_text, resume_text=resume_text)
	result = _call_ai(ctx, svc, prompt)
	if result is None:
		return

	handle_output(
		ctx, "ai-optimize", result,
		hints={"next_actions": [
			f"boss resume show {resume_name}",
			f"boss resume export {resume_name} --format pdf",
		]},
	)


@ai_group.command("suggest")
@click.argument("resume_name")
@click.option("--jd", "jd_text", required=True, help="目标职位描述文本或 @文件路径")
@click.pass_context
def ai_suggest_cmd(ctx, resume_name, jd_text):
	"""基于目标职位描述给出改进建议（不修改简历）"""
	svc = _require_ai_service(ctx)
	if svc is None:
		return

	if jd_text.startswith("@"):
		file_path = Path(jd_text[1:])
		if not file_path.exists():
			handle_error_output(ctx, "ai", code="INVALID_PARAM", message=f"文件 '{file_path}' 不存在")
			ctx.exit(1)
			return
		jd_text = file_path.read_text(encoding="utf-8")

	resume_text = _load_resume_text(ctx, resume_name)
	if resume_text is None:
		return

	prompt = RESUME_SUGGEST_PROMPT.format(jd_text=jd_text, resume_text=resume_text)
	result = _call_ai(ctx, svc, prompt)
	if result is None:
		return

	handle_output(
		ctx, "ai-suggest", result,
		hints={"next_actions": [
			f"boss ai optimize {resume_name} --jd <jd_text>",
			f"boss resume show {resume_name}",
		]},
	)


@ai_group.command("reply")
@click.argument("recruiter_message")
@click.option("--context", default="", help="会话上下文（可选，或 @文件路径）")
@click.option("--resume", "resume_name", default=None, help="参考简历名称（可选）")
@click.option("--tone", default="简洁专业", type=click.Choice(["简洁专业", "热情积极", "谨慎确认"]), help="语气偏好")
@click.pass_context
def ai_reply_cmd(ctx, recruiter_message, context, resume_name, tone):
	"""基于招聘者消息生成回复草稿（2-3 条候选）

	RECRUITER_MESSAGE 为招聘者发来的消息文本，或 @文件路径 读取文件内容。
	"""
	svc = _require_ai_service(ctx)
	if svc is None:
		return

	def _resolve_text(value: str) -> str:
		if value.startswith("@"):
			file_path = Path(value[1:])
			if not file_path.exists():
				handle_error_output(ctx, "ai", code="INVALID_PARAM", message=f"文件 '{file_path}' 不存在")
				ctx.exit(1)
			return file_path.read_text(encoding="utf-8")
		return value

	recruiter_message = _resolve_text(recruiter_message)
	context_text = _resolve_text(context) if context else "（无）"

	resume_text = "（未指定）"
	if resume_name:
		loaded = _load_resume_text(ctx, resume_name)
		if loaded is None:
			return
		resume_text = loaded

	prompt = CHAT_REPLY_PROMPT.format(
		recruiter_message=recruiter_message,
		context=context_text,
		resume_text=resume_text,
		tone=tone,
	)
	result = _call_ai(ctx, svc, prompt)
	if result is None:
		return

	handle_output(
		ctx, "ai-reply", result,
		hints={"next_actions": [
			"复制草稿到 BOSS 聊天框发送",
			"boss chatmsg <security_id> 查看完整聊天历史",
		]},
	)


@ai_group.command("interview-prep")
@click.argument("jd_text")
@click.option("--resume", "resume_name", default=None, help="参考简历名称（可选，根据简历真实经历定制问题）")
@click.option("--count", default=10, type=int, help="题量，默认 10")
@click.pass_context
def ai_interview_prep_cmd(ctx, jd_text, resume_name, count):
	"""基于目标职位生成模拟面试题

	JD_TEXT 是目标职位描述文本，或 @文件路径 读取文件内容。
	"""
	svc = _require_ai_service(ctx)
	if svc is None:
		return

	if jd_text.startswith("@"):
		file_path = Path(jd_text[1:])
		if not file_path.exists():
			handle_error_output(ctx, "ai", code="INVALID_PARAM", message=f"文件 '{file_path}' 不存在")
			ctx.exit(1)
			return
		jd_text = file_path.read_text(encoding="utf-8")

	resume_text = "（未指定）"
	if resume_name:
		loaded = _load_resume_text(ctx, resume_name)
		if loaded is None:
			return
		resume_text = loaded

	prompt = INTERVIEW_PREP_PROMPT.format(
		jd_text=jd_text,
		resume_text=resume_text,
		count=count,
	)
	result = _call_ai(ctx, svc, prompt)
	if result is None:
		return

	handle_output(
		ctx, "ai-interview-prep", result,
		hints={"next_actions": [
			"对照 questions 逐题准备答案",
			"boss ai chat-coach <chat_text> 用于沟通准备",
		]},
	)


@ai_group.command("chat-coach")
@click.argument("chat_text")
@click.option("--resume", "resume_name", default=None, help="参考简历名称（可选）")
@click.option("--style", default="简洁专业", help="沟通风格偏好（如 简洁专业/积极主动/谨慎稳重）")
@click.pass_context
def ai_chat_coach_cmd(ctx, chat_text, resume_name, style):
	"""基于聊天记录给出沟通技巧诊断与下一步建议

	CHAT_TEXT 是聊天记录文本，或 @文件路径 读取文件内容。
	"""
	svc = _require_ai_service(ctx)
	if svc is None:
		return

	if chat_text.startswith("@"):
		file_path = Path(chat_text[1:])
		if not file_path.exists():
			handle_error_output(ctx, "ai", code="INVALID_PARAM", message=f"文件 '{file_path}' 不存在")
			ctx.exit(1)
			return
		chat_text = file_path.read_text(encoding="utf-8")

	resume_text = "（未指定）"
	if resume_name:
		loaded = _load_resume_text(ctx, resume_name)
		if loaded is None:
			return
		resume_text = loaded

	prompt = CHAT_COACH_PROMPT.format(
		chat_text=chat_text,
		resume_text=resume_text,
		style=style,
	)
	result = _call_ai(ctx, svc, prompt)
	if result is None:
		return

	handle_output(
		ctx, "ai-chat-coach", result,
		hints={"next_actions": [
			"按 next_action_recommendation 的建议行动",
			"message_templates 可直接复制发送",
		]},
	)
