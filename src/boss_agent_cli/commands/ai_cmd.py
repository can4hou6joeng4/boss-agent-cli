"""AI 简历优化命令组。

子命令：config / analyze-jd / polish / optimize / suggest
"""

import json
from pathlib import Path
from typing import Any, cast

import click

from boss_agent_cli.ai.config import (
	AIConfigStore,
	AIConfigValueError,
	PROVIDER_BASE_URLS,
	ai_float_setting,
	ai_int_setting,
	ai_text_setting,
	normalize_base_url,
	provider_endpoint,
)
from boss_agent_cli.ai.prompts import (
	CHAT_COACH_PROMPT,
	CHAT_REPLY_PROMPT,
	COVER_LETTER_PROMPT,
	INTERVIEW_PREP_PROMPT,
	JD_ANALYSIS_PROMPT,
	RESUME_OPTIMIZE_FOR_JD_PROMPT,
	RESUME_OPTIMIZE_SIMPLE_PROMPT,
	RESUME_POLISH_PROMPT,
	RESUME_SUGGEST_PROMPT,
	SUGGEST_KEYWORDS_PROMPT,
)
from boss_agent_cli.ai.service import AIService, AIServiceError, recovery_action_for
from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.display import (
	boss_command_for_ctx,
	handle_error_output,
	handle_output,
	render_action_result,
	render_ai_result,
	shell_argument,
)
from boss_agent_cli.resume.models import resume_to_text
from boss_agent_cli.resume.store import ResumeStore


def _get_ai_config_store(ctx: click.Context) -> AIConfigStore:
	return AIConfigStore(ctx.obj["data_dir"])


def _get_resume_store(ctx: click.Context) -> ResumeStore:
	return ResumeStore(ctx.obj["data_dir"] / "resumes")


def _hint_command(ctx: click.Context, command: str) -> str:
	"""AI 命令的后继与恢复提示沿用本地简历、缓存与 AI 配置所在的数据目录。"""
	return boss_command_for_ctx(ctx, command, include_data_dir=True)


def _create_ai_service(ctx: click.Context) -> AIService | None:
	"""从配置创建 AIService 实例，未配置时返回 None。

	数值项在构造前统一校验：``ai_temperature`` / ``ai_max_tokens`` 被写成
	字符串或数组时，坏值会被原样塞进请求 payload、由服务商返回 400，调用方
	只看到泛化的平台错误（Issue #1209）。``ValueError`` 由调用方映射成
	``INVALID_PARAM`` 与修复命令，不能兜成可重试的网络错误。
	"""
	store = _get_ai_config_store(ctx)
	if not store.is_configured():
		return None
	config = store.load_config()
	api_key = store.get_api_key()
	# 字符串项同样要在构造前校验（Issue #1428、#1431）：``ai_model`` 漂移成数组 /
	# 数字会让服务层 ``rsplit`` 崩溃，``ai_base_url`` 写成空白串或非 URL 会让
	# httpx 抛 UnsupportedProtocol，两者都被兜成与真实原因无关的错误。
	model = ai_text_setting(config, "ai_model") or ""
	if not model.strip():
		raise AIConfigValueError(
			"AI 配置项 ai_model 未设置或为空",
			recovery_hint="ai config --model <model>",
		)
	provider = ai_text_setting(config, "ai_provider")
	raw_base_url = ai_text_setting(config, "ai_base_url")
	base_url = normalize_base_url(raw_base_url) if raw_base_url is not None else None
	base_url = provider_endpoint(provider, base_url)
	if not api_key or not base_url:
		return None
	temperature = ai_float_setting(config, "ai_temperature", 0.7)
	max_tokens = ai_int_setting(config, "ai_max_tokens", 4096)
	return AIService(
		base_url=base_url,
		api_key=api_key,
		model=model,
		temperature=temperature,
		max_tokens=max_tokens,
	)


def _require_ai_service(ctx: click.Context) -> AIService | None:
	"""获取 AIService，未配置时输出错误信封并返回 None。"""
	try:
		svc = _create_ai_service(ctx)
	except ValueError as exc:
		# 本地配置坏值：重试同一条命令必然复现，给 INVALID_PARAM 与修复命令，
		# 不能落成可重试的 NETWORK_ERROR（Issue #1209）。字符串项自带更贴题的
		# 修复子命令（Issue #1428、#1431），数值项沿用冒烟提示。
		hint = getattr(exc, "recovery_hint", None) or "ai config --max-tokens 4096 --temperature 0.7"
		handle_error_output(
			ctx, "ai",
			code="INVALID_PARAM",
			message=f"本地 AI 配置损坏：{exc}",
			recoverable=True,
			recovery_action=_hint_command(ctx, hint),
		)
		ctx.exit(1)
		return None
	if svc is None:
		# provider / model / api_key 都在、只缺端点时，笼统的「AI 服务未配置」会把人
		# 引去重填 key；真正要改的是 provider 名或补 base_url。
		provider = _get_ai_config_store(ctx).unresolved_provider()
		if provider is not None:
			handle_error_output(
				ctx, "ai",
				code="AI_NOT_CONFIGURED",
				message=f"AI provider {provider!r} 没有可用端点（provider 未登记或未设置 --base-url）",
				recoverable=True,
				recovery_action=_hint_command(ctx, "ai config --provider <已登记的 provider>")
				+ "，或使用 --base-url 指向自建 OpenAI 兼容端点",
			)
		else:
			handle_error_output(
				ctx, "ai",
				code="AI_NOT_CONFIGURED",
				message="AI 服务未配置",
				recoverable=True,
				recovery_action=_hint_command(ctx, "ai config --provider <provider> --model <model> --api-key <key>"),
			)
		ctx.exit(1)
		return None
	return svc


def _load_resume_text(ctx: click.Context, resume_name: str) -> str | None:
	"""加载简历纯文本；不存在、文件损坏或名称为空时输出对应错误。

	损坏与缺失必须分开报：坏文件不报 ``RESUME_NOT_FOUND``，否则 Agent 照
	``resume list`` 只看得到空列表、``resume init`` 又被同名文件挡住
	（``RESUME_ALREADY_EXISTS``），恢复链断在这里（Issue #1369）。
	"""
	if not resume_name.strip():
		list_cmd = _hint_command(ctx, "resume list")
		handle_error_output(
			ctx,
			"ai",
			code="INVALID_PARAM",
			message="简历名称不能为空",
			recovery_action=f"补上简历名称后重试；用 {list_cmd} 查看已有简历",
			hints={"next_actions": [list_cmd]},
		)
		ctx.exit(1)
		return None
	store = _get_resume_store(ctx)
	resume, load_error = store.get_with_error(resume_name)
	if resume is not None:
		return resume_to_text(resume)
	if load_error is not None:
		delete_cmd = _hint_command(ctx, f"resume delete {shell_argument(resume_name)}")
		handle_error_output(
			ctx,
			"ai",
			code="RESUME_FILE_INVALID",
			message=f"简历文件 '{resume_name}.json' 损坏：{load_error}",
			recovery_action=(
				f"修正 resumes/{resume_name}.json 的 JSON 内容后重试，"
				f"或执行 {delete_cmd} 后重新创建；该文件只存在本机"
			),
			hints={"next_actions": [delete_cmd, _hint_command(ctx, "resume list")]},
		)
		ctx.exit(1)
		return None
	handle_error_output(
		ctx, "ai",
		code="RESUME_NOT_FOUND",
		message=f"简历 '{resume_name}' 不存在",
	)
	ctx.exit(1)
	return None


def _read_text_argument(ctx: click.Context, value: str) -> str | None:
	"""展开 `@文件路径` 参数；读不出来时输出信封并返回 None。

	`read_text` 对 GBK 保存的 JD、误选的 PDF/图片会抛 `UnicodeDecodeError`，
	传目录会抛 `IsADirectoryError`，权限不足会抛 `PermissionError`；
	这些都不该变成 traceback。
	"""
	if not value.startswith("@"):
		return value
	file_path = Path(value[1:])
	if not file_path.exists():
		handle_error_output(
			ctx,
			"ai",
			code="INVALID_PARAM",
			message=f"文件 '{file_path}' 不存在",
			recovery_action="检查 @ 后面的文件路径是否存在后重试",
		)
		ctx.exit(1)
		return None
	if file_path.is_dir():
		handle_error_output(
			ctx,
			"ai",
			code="INVALID_PARAM",
			message=f"'{file_path}' 是目录，需要传入文件路径",
			recovery_action="把 @ 后面的目录换成具体的 UTF-8 文本文件路径后重试",
		)
		ctx.exit(1)
		return None
	try:
		return file_path.read_text(encoding="utf-8")
	except UnicodeDecodeError:
		handle_error_output(
			ctx,
			"ai",
			code="INVALID_PARAM",
			message=f"文件 '{file_path}' 不是 UTF-8 编码的文本；请先转存为 UTF-8 后重试",
			recovery_action="先把文件转存为 UTF-8 文本，再重新传入 @<文件> 路径",
		)
		ctx.exit(1)
		return None
	except OSError as exc:
		handle_error_output(
			ctx, "ai",
			code="LOCAL_IO_ERROR",
			message=f"读取 {file_path} 失败: {exc.strerror or exc}",
		)
		ctx.exit(1)
		return None


def _call_ai(ctx: click.Context, svc: AIService, prompt: str) -> dict[str, Any] | None:
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
			recovery_action=recovery_action_for(
				exc,
				"检查网络连接和密钥配置，重试",
				config_command=_hint_command(ctx, "ai config"),
			),
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
		parsed = json.loads(text)
	# 超长整数（3.11+）与深嵌套同样是模型输出不可解析，走 AI_PARSE_ERROR 信封。
	except (ValueError, RecursionError):
		handle_error_output(
			ctx, "ai",
			code="AI_PARSE_ERROR",
			message="AI 返回结果解析失败",
			recoverable=True,
			recovery_action="重试（模型输出不稳定时可能发生）",
		)
		ctx.exit(1)
		return None
	if not isinstance(parsed, dict):
		# 顶层是数组 / 字符串 / 数字同样是解析失败：调用方会在
		# `result.setdefault(...)` 处抛 AttributeError，把信封变成 traceback。
		handle_error_output(
			ctx, "ai",
			code="AI_PARSE_ERROR",
			message=f"AI 返回结果不是 JSON 对象（收到 {type(parsed).__name__}）",
			recoverable=True,
			recovery_action="重试（模型输出不稳定时可能发生）",
		)
		ctx.exit(1)
		return None
	return cast("dict[str, Any]", parsed)


def _fit_missing_detail_item(ctx: click.Context, item: dict[str, Any]) -> dict[str, Any]:
	security_id = str(item.get("security_id") or "")
	hint = (
		f"先 {_hint_command(ctx, f'detail {shell_argument(security_id)}')}"
		if security_id
		else f"先 {_hint_command(ctx, 'detail <security_id>')}"
	)
	return {
		"job_id": item.get("job_id", ""),
		"security_id": security_id,
		"title": item.get("title", ""),
		"company": item.get("company", ""),
		"status": "缺详情",
		"hint": hint,
	}


def _build_fit_prompt(resume_text: str, jobs: list[dict[str, Any]]) -> str:
	payload = {
		"resume": resume_text,
		"jobs": jobs,
		"output_schema": {
			"results": [
				{
					"selector": "string",
					"title": "string",
					"match_score": "0-100 integer",
					"gaps": ["string"],
					"keyword_hits": ["string"],
					"recommendation": "string",
				},
			],
		},
	}
	return (
		"请基于本地简历和已缓存职位详情，逐岗评估匹配度。"
		"只返回 JSON，不要包含 markdown。字段必须符合 output_schema。\n\n"
		f"{json.dumps(payload, ensure_ascii=False)}"
	)


@click.group("ai")
def ai_group() -> None:
	"""AI 简历优化。"""


@ai_group.command("config")
@click.option("--provider", default=None, help="AI 提供商（openai/deepseek/moonshot/openrouter/qwen/zhipu/siliconflow/ollama/vllm/custom）")
@click.option("--model", default=None, help="模型名称（如 gpt-4o / claude-sonnet-4.5 / deepseek-chat）")
@click.option("--api-key", default=None, help="API 密钥（将加密存储；空值会被拒绝，清除密钥请用 --clear-api-key）")
@click.option("--clear-api-key", is_flag=True, default=False, help="删除已存储的 API 密钥（与 --api-key 互斥；没有密钥时也安全）")
@click.option("--base-url", default=None, help="自定义 API 基础地址（provider=custom 或自建代理时使用）")
@click.option("--temperature", default=None, type=click.FloatRange(min=0), help="生成温度")
@click.option("--max-tokens", default=None, type=click.IntRange(min=1), help="最大令牌数")
@click.pass_context
def ai_config_cmd(ctx: click.Context, provider: str | None, model: str | None, api_key: str | None, clear_api_key: bool, base_url: str | None, temperature: float | None, max_tokens: int | None) -> None:
	"""查看或设置 AI 服务配置"""
	store = _get_ai_config_store(ctx)

	# 无参数时显示当前配置。字符串项必须用 `is not None` 判断：`--model ""`
	# 是显式传入的空值，按真值判断会退化成查看配置，悄悄吞掉调用方的意图
	# （Issue #1499）。
	has_updates = (
		provider is not None
		or model is not None
		or api_key is not None
		or clear_api_key
		or base_url is not None
		or temperature is not None
		or max_tokens is not None
	)
	if not has_updates:
		config = store.load_config()
		config["api_key_set"] = store.api_key_is_set()
		config.pop("ai_api_key", None)
		# 回显解析后的实际端点：只设 --provider 时 ai_base_url 是空的，
		# 真正生效的地址来自 PROVIDER_BASE_URLS 查表，用户此前无从确认。
		# provider 名相近时（如 openrouter / orcarouter）记混会把 API key 与
		# 简历全文发到另一家，而界面上看不出任何差别。
		config["resolved_base_url"] = store.get_base_url()
		handle_output(
			ctx, "ai-config", config,
			render=lambda d: render_action_result(
				d, title="ai config", next_steps=[
					_hint_command(ctx, "ai local status"),
					_hint_command(ctx, "ai analyze-jd <security_id>"),
				]
			),
			hints={"next_actions": [
				_hint_command(ctx, "ai config --provider openai --model gpt-4o --api-key <key>"),
				_hint_command(ctx, "ai local configure --runtime ollama --model qwen3:14b"),
			]},
		)
		return

	# 所有校验都在任何写盘之前完成：显式空值绝不能落成空配置、更不能把已存
	# 密钥覆盖成空串（Issue #1499）。
	if api_key is not None and clear_api_key:
		handle_error_output(
			ctx, "ai-config",
			code="INVALID_PARAM",
			message="--api-key 与 --clear-api-key 不能同时使用",
			recovery_action="设置新密钥用 --api-key <key>，清除已存密钥用 --clear-api-key，二选一",
		)
		ctx.exit(1)
		return
	if provider is not None and not provider.strip():
		handle_error_output(
			ctx, "ai-config",
			code="INVALID_PARAM",
			message="--provider 不能为空或只包含空白字符",
			recovery_action="按 next_actions 传入已登记的 provider 后重试",
			hints={"next_actions": [
				_hint_command(ctx, "ai config --provider openai --model gpt-4o --api-key <key>"),
				_hint_command(ctx, "ai config"),
			]},
		)
		ctx.exit(1)
		return
	if api_key is not None and not api_key.strip():
		handle_error_output(
			ctx, "ai-config",
			code="INVALID_PARAM",
			message="--api-key 不能为空或只包含空白字符；如需清除已存密钥请使用 --clear-api-key",
			recovery_action="传入真实密钥，或用 --clear-api-key 显式清除已存凭据",
			hints={"next_actions": [
				_hint_command(ctx, "ai config --api-key <key>"),
				_hint_command(ctx, "ai config --clear-api-key"),
			]},
		)
		ctx.exit(1)
		return

	# provider 写错（如 `openai-cn`）或 provider=custom 没给 base_url 时，端点解析为
	# None：写进配置后每条 AI 命令都只会回「AI 服务未配置」，用户看不出是 provider
	# 名的问题，而配置和 api_key 已经落盘。写入前先拒绝，并列出登记表里的取值。
	if provider is not None:
		stored_base_url = store.load_config().get("ai_base_url")
		effective_base_url = base_url if base_url is not None else stored_base_url
		if provider_endpoint(provider, effective_base_url) is None:
			if provider == "custom":
				message = "provider 'custom' 需要同时传 --base-url（自建 OpenAI 兼容端点）"
			else:
				message = f"未知 AI provider: {provider!r}，可选: {', '.join(sorted(PROVIDER_BASE_URLS))}"
			handle_error_output(
				ctx, "ai-config",
				code="INVALID_PARAM",
				message=message,
				recovery_action="按 next_actions 选择可用的 provider 参数后重试",
				hints={"next_actions": [
					_hint_command(ctx, "ai config --provider custom --base-url <url> --model <model> --api-key <key>"),
					_hint_command(ctx, "ai config"),
				]},
			)
			ctx.exit(1)
			return

	# 有参数时更新配置
	updates: dict[str, Any] = {}
	if provider is not None:
		updates["ai_provider"] = provider
	if model is not None:
		if not model.strip():
			# 空白模型名会原样发进请求，由服务商返回 400，之后每条 AI 命令都
			# 报可重试的 AI_API_ERROR；写入前拒绝（Issue #1428）。
			handle_error_output(
				ctx, "ai-config",
				code="INVALID_PARAM",
				message="--model 不能为空或只包含空白字符",
				recovery_action="按 next_actions 传入真实模型名后重试",
				hints={"next_actions": [_hint_command(ctx, "ai config --model <model>")]},
			)
			ctx.exit(1)
			return
		updates["ai_model"] = model
	if base_url is not None:
		try:
			# 空白串 / 缺 scheme / 非 http(s) 地址会让之后每条 AI 命令抛
			# UnsupportedProtocol 并被兜成可重试的网络错误；写入前拒绝（Issue #1431）。
			updates["ai_base_url"] = normalize_base_url(base_url)
		except AIConfigValueError as exc:
			handle_error_output(
				ctx, "ai-config",
				code="INVALID_PARAM",
				message=str(exc),
				recovery_action="按 next_actions 传入 http(s) 绝对地址后重试",
				hints={"next_actions": [_hint_command(ctx, "ai config --base-url https://api.example.com/v1")]},
			)
			ctx.exit(1)
			return
	if temperature is not None:
		updates["ai_temperature"] = temperature
	if max_tokens is not None:
		updates["ai_max_tokens"] = max_tokens
	if updates:
		store.save_config(**updates)
	if api_key is not None:
		store.save_api_key(api_key)
	elif clear_api_key:
		store.clear_api_key()

	updated_fields = list(updates.keys())
	if api_key is not None or clear_api_key:
		updated_fields.append("api_key")
	handle_output(
		ctx, "ai-config",
		{
			"action": "update",
			"updated_fields": updated_fields,
			"api_key_set": store.api_key_is_set(),
			# 写入后立刻回显生效的端点，让「刚配的这个 provider 会把数据发去哪」
			# 在同一屏内可确认，而不是要再跑一次 boss ai config 才看得到。
			"resolved_base_url": store.get_base_url(),
		},
		render=lambda d: render_action_result(d, title="ai config", next_steps=[_hint_command(ctx, "ai config")]),
		hints={"next_actions": [
			_hint_command(ctx, "ai config"),
			_hint_command(ctx, "ai local status"),
			_hint_command(ctx, "ai analyze-jd <security_id> --resume <name>"),
		]},
	)


@ai_group.command("analyze-jd")
@click.argument("jd_text")
@click.option("--resume", "resume_name", required=True, help="对比的本地简历名称")
@click.pass_context
def ai_analyze_jd_cmd(ctx: click.Context, jd_text: str, resume_name: str) -> None:
	"""分析职位描述并评估简历匹配度

	JD_TEXT 可以是职位描述文本，也可以是 @文件路径 读取文件内容。
	"""
	svc = _require_ai_service(ctx)
	if svc is None:
		return

	# 支持 @file 语法读取文件
	resolved = _read_text_argument(ctx, jd_text)
	if resolved is None:
		return
	jd_text = resolved

	resume_text = _load_resume_text(ctx, resume_name)
	if resume_text is None:
		return

	prompt = JD_ANALYSIS_PROMPT.format(jd_text=jd_text, resume_text=resume_text)
	result = _call_ai(ctx, svc, prompt)
	if result is None:
		return

	handle_output(
		ctx, "ai-analyze-jd", result,
		render=lambda d: render_ai_result(d, title="ai-analyze-jd"),
		hints={"next_actions": [
			_hint_command(ctx, f"ai optimize {shell_argument(resume_name)} --jd <jd_text>"),
			_hint_command(ctx, f"ai suggest {shell_argument(resume_name)} --jd <jd_text>"),
		]},
	)


@ai_group.command("polish")
@click.argument("resume_name")
@click.pass_context
def ai_polish_cmd(ctx: click.Context, resume_name: str) -> None:
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
		render=lambda d: render_ai_result(d, title="ai-polish"),
		hints={"next_actions": [_hint_command(ctx, f"resume show {shell_argument(resume_name)}")]},
	)


@ai_group.command("optimize")
@click.argument("resume_name")
@click.option("--jd", "jd_text", required=True, help="目标职位描述文本或 @文件路径")
@click.pass_context
def ai_optimize_cmd(ctx: click.Context, resume_name: str, jd_text: str) -> None:
	"""基于目标职位描述优化简历"""
	svc = _require_ai_service(ctx)
	if svc is None:
		return

	resolved = _read_text_argument(ctx, jd_text)
	if resolved is None:
		return
	jd_text = resolved

	resume_text = _load_resume_text(ctx, resume_name)
	if resume_text is None:
		return

	prompt = RESUME_OPTIMIZE_FOR_JD_PROMPT.format(jd_text=jd_text, resume_text=resume_text)
	result = _call_ai(ctx, svc, prompt)
	if result is None:
		return

	handle_output(
		ctx, "ai-optimize", result,
		render=lambda d: render_ai_result(d, title="ai-optimize"),
		hints={"next_actions": [
			_hint_command(ctx, f"resume show {shell_argument(resume_name)}"),
			_hint_command(ctx, f"resume export {shell_argument(resume_name)} --format pdf"),
		]},
	)


@ai_group.command("suggest")
@click.argument("resume_name")
@click.option("--jd", "jd_text", required=True, help="目标职位描述文本或 @文件路径")
@click.pass_context
def ai_suggest_cmd(ctx: click.Context, resume_name: str, jd_text: str) -> None:
	"""基于目标职位描述给出改进建议（不修改简历）"""
	svc = _require_ai_service(ctx)
	if svc is None:
		return

	resolved = _read_text_argument(ctx, jd_text)
	if resolved is None:
		return
	jd_text = resolved

	resume_text = _load_resume_text(ctx, resume_name)
	if resume_text is None:
		return

	prompt = RESUME_SUGGEST_PROMPT.format(jd_text=jd_text, resume_text=resume_text)
	result = _call_ai(ctx, svc, prompt)
	if result is None:
		return

	handle_output(
		ctx, "ai-suggest", result,
		render=lambda d: render_ai_result(d, title="ai-suggest"),
		hints={"next_actions": [
			_hint_command(ctx, f"ai optimize {shell_argument(resume_name)} --jd <jd_text>"),
			_hint_command(ctx, f"resume show {shell_argument(resume_name)}"),
		]},
	)


@ai_group.command("fit")
@click.option("--resume", "resume_name", required=True, help="本地简历名称")
@click.option("--limit", default=20, type=click.IntRange(min=1), help="最多分析的候选池职位数")
@click.pass_context
def ai_fit_cmd(ctx: click.Context, resume_name: str, limit: int) -> None:
	"""基于本地简历和候选池缓存详情生成逐岗匹配报告。"""
	svc = _require_ai_service(ctx)
	if svc is None:
		return

	resume_text = _load_resume_text(ctx, resume_name)
	if resume_text is None:
		return

	with CacheStore(ctx.obj["data_dir"] / "cache" / "boss_agent.db") as cache:
		shortlist = cache.list_shortlist()[:limit]
		jobs: list[dict[str, Any]] = []
		missing: list[dict[str, Any]] = []
		for item in shortlist:
			job_id = str(item.get("job_id") or "")
			description = cache.get_job_desc(job_id)
			if description:
				jobs.append({
					"job_id": job_id,
					"security_id": item.get("security_id", ""),
					"title": item.get("title", ""),
					"company": item.get("company", ""),
					"city": item.get("city", ""),
					"salary": item.get("salary", ""),
					"description": description,
				})
			else:
				missing.append(_fit_missing_detail_item(ctx, item))

	if not shortlist:
		handle_output(
			ctx,
			"ai-fit",
			{"results": [], "missing": [], "summary": {"analyzed": 0, "missing_details": 0}},
			render=lambda d: render_ai_result(d, title="ai-fit"),
			hints={"next_actions": [_hint_command(ctx, "shortlist add <security_id> <job_id>")]},
		)
		return

	if not jobs:
		handle_output(
			ctx,
			"ai-fit",
			{"results": [], "missing": missing, "summary": {"analyzed": 0, "missing_details": len(missing)}},
			render=lambda d: render_ai_result(d, title="ai-fit"),
			hints={"next_actions": [_hint_command(ctx, "detail <security_id>")],
				"operator_actions": [
					f"候选池职位缺少详情缓存，先逐个 {_hint_command(ctx, 'detail <security_id>')} 后再重试 "
					f"{_hint_command(ctx, f'ai fit --resume {shell_argument(resume_name)}')}"
				]},
		)
		return

	result = _call_ai(ctx, svc, _build_fit_prompt(resume_text, jobs))
	if result is None:
		return

	result.setdefault("results", [])
	result["missing"] = missing
	result["summary"] = {
		"analyzed": len(jobs),
		"missing_details": len(missing),
	}
	handle_output(
		ctx,
		"ai-fit",
		result,
		render=lambda d: render_ai_result(d, title="ai-fit"),
		hints={"next_actions": [_hint_command(ctx, f"ai optimize {shell_argument(resume_name)} --jd <jd_text>")]},
	)


@ai_group.command("reply")
@click.argument("recruiter_message")
@click.option("--context", default="", help="会话上下文（可选，或 @文件路径）")
@click.option("--resume", "resume_name", default=None, help="参考简历名称（可选）")
@click.option("--tone", default="简洁专业", type=click.Choice(["简洁专业", "热情积极", "谨慎确认"]), help="语气偏好")
@click.pass_context
def ai_reply_cmd(ctx: click.Context, recruiter_message: str, context: str, resume_name: str | None, tone: str) -> None:
	"""基于招聘者消息生成回复草稿（2-3 条候选）

	RECRUITER_MESSAGE 为招聘者发来的消息文本，或 @文件路径 读取文件内容。
	"""
	svc = _require_ai_service(ctx)
	if svc is None:
		return

	def _resolve_text(value: str) -> str:
		resolved = _read_text_argument(ctx, value)
		if resolved is None:
			ctx.exit(1)
		return resolved

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
		render=lambda d: render_ai_result(d, title="ai-reply"),
		hints={"next_actions": [
			_hint_command(ctx, "chatmsg <uid>"),
		], "operator_actions": [
			"复制草稿到 BOSS 聊天框手动发送",
			f"uid 见 {_hint_command(ctx, 'chat')} 输出",
		]},
	)


@ai_group.command("interview-prep")
@click.argument("jd_text")
@click.option("--resume", "resume_name", default=None, help="参考简历名称（可选，根据简历真实经历定制问题）")
@click.option("--count", default=10, type=click.IntRange(min=1), help="题量，默认 10")
@click.pass_context
def ai_interview_prep_cmd(ctx: click.Context, jd_text: str, resume_name: str | None, count: int) -> None:
	"""基于目标职位生成模拟面试题

	JD_TEXT 是目标职位描述文本，或 @文件路径 读取文件内容。
	"""
	svc = _require_ai_service(ctx)
	if svc is None:
		return

	resolved = _read_text_argument(ctx, jd_text)
	if resolved is None:
		return
	jd_text = resolved

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
		render=lambda d: render_ai_result(d, title="ai-interview-prep"),
		hints={"next_actions": [
			_hint_command(ctx, "ai chat-coach <chat_text>"),
		], "operator_actions": [
			"对照 questions 逐题准备答案",
		]},
	)


@ai_group.command("chat-coach")
@click.argument("chat_text")
@click.option("--resume", "resume_name", default=None, help="参考简历名称（可选）")
@click.option("--style", default="简洁专业", help="沟通风格偏好（如 简洁专业/积极主动/谨慎稳重）")
@click.pass_context
def ai_chat_coach_cmd(ctx: click.Context, chat_text: str, resume_name: str | None, style: str) -> None:
	"""基于聊天记录给出沟通技巧诊断与下一步建议

	CHAT_TEXT 是聊天记录文本，或 @文件路径 读取文件内容。
	"""
	svc = _require_ai_service(ctx)
	if svc is None:
		return

	resolved = _read_text_argument(ctx, chat_text)
	if resolved is None:
		return
	chat_text = resolved

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
		render=lambda d: render_ai_result(d, title="ai-chat-coach"),
		hints={"operator_actions": [
			"按 next_action_recommendation 的建议行动",
			"message_templates 可直接复制发送",
		]},
	)


@ai_group.command("suggest-keywords")
@click.option("--limit", default=20, type=click.IntRange(min=1), help="候选池职位数上限")
@click.pass_context
def ai_suggest_keywords_cmd(ctx: click.Context, limit: int) -> None:
	"""基于候选池分析推荐搜索关键词组合"""
	svc = _require_ai_service(ctx)
	if svc is None:
		return

	with CacheStore(ctx.obj["data_dir"] / "cache" / "boss_agent.db") as cache:
		shortlist = cache.list_shortlist()[:limit]

	if not shortlist:
		handle_output(
			ctx,
			"ai-suggest-keywords",
			{"keyword_groups": [], "patterns": [], "search_suggestions": []},
			render=lambda d: render_ai_result(d, title="ai-suggest-keywords"),
			hints={"next_actions": [_hint_command(ctx, "shortlist add <security_id> <job_id>")]},
		)
		return

	shortlist_data = json.dumps([
		{
			"title": item.get("title", ""),
			"company": item.get("company", ""),
			"city": item.get("city", ""),
			"salary": item.get("salary", ""),
			"tags": item.get("tags", ""),
		}
		for item in shortlist
	], ensure_ascii=False)

	prompt = SUGGEST_KEYWORDS_PROMPT.format(shortlist_data=shortlist_data)
	result = _call_ai(ctx, svc, prompt)
	if result is None:
		return

	result.setdefault("keyword_groups", [])
	result.setdefault("patterns", [])
	result.setdefault("search_suggestions", [])
	handle_output(
		ctx,
		"ai-suggest-keywords",
		result,
		render=lambda d: render_ai_result(d, title="ai-suggest-keywords"),
		hints={"next_actions": [
			_hint_command(ctx, "search <推荐关键词>"),
			_hint_command(ctx, "preset add <name> <关键词>"),
		]},
	)


@ai_group.command("resume-optimize")
@click.argument("resume_name")
@click.option("--jd", "jd_text", default=None, help="目标职位描述文本或 @文件路径")
@click.option("--job-id", default=None, help="从缓存读取职位描述的 job_id")
@click.pass_context
def ai_resume_optimize_cmd(ctx: click.Context, resume_name: str, jd_text: str | None, job_id: str | None) -> None:
	"""基于目标岗位优化简历措辞（仅建议，不修改简历）"""
	svc = _require_ai_service(ctx)
	if svc is None:
		return

	if not jd_text and not job_id:
		search_cmd = _hint_command(ctx, "search <关键词>")
		handle_error_output(
			ctx,
			"ai",
			code="INVALID_PARAM",
			message="需要指定 --jd 或 --job-id",
			recovery_action=f"补上 --jd <职位描述> 或 --job-id <编号> 后重试；编号可从 {search_cmd} 的结果中取到",
			hints={"next_actions": [search_cmd]},
		)
		ctx.exit(1)
		return

	# 从缓存加载 JD
	if job_id:
		with CacheStore(ctx.obj["data_dir"] / "cache" / "boss_agent.db") as cache:
			jd_text = cache.get_job_desc(job_id)
		if not jd_text:
			handle_error_output(
				ctx, "ai",
				code="CACHE_MISS",
				message=f"job_id '{job_id}' 的职位描述未缓存",
				recoverable=True,
				recovery_action=_hint_command(ctx, f"detail <security_id> --job-id {shell_argument(job_id)}"),
			)
			ctx.exit(1)
			return

	# 支持 @file 语法
	if jd_text:
		resolved = _read_text_argument(ctx, jd_text)
		if resolved is None:
			return
		jd_text = resolved

	resume_text = _load_resume_text(ctx, resume_name)
	if resume_text is None:
		return

	prompt = RESUME_OPTIMIZE_SIMPLE_PROMPT.format(jd_text=jd_text, resume_text=resume_text)
	result = _call_ai(ctx, svc, prompt)
	if result is None:
		return

	handle_output(
		ctx, "ai-resume-optimize", result,
		render=lambda d: render_ai_result(d, title="ai-resume-optimize"),
		hints={"next_actions": [
			_hint_command(ctx, f"resume edit {shell_argument(resume_name)}"),
			_hint_command(ctx, f"ai optimize {shell_argument(resume_name)} --jd <jd_text>"),
		]},
	)


@ai_group.command("cover-letter")
@click.argument("resume_name")
@click.option("--jd", "jd_text", default=None, help="目标职位描述文本或 @文件路径")
@click.option("--job-id", default=None, help="从缓存读取职位描述的 job_id")
@click.option(
	"--tone", default="简洁专业", type=click.Choice(["简洁专业", "热情积极", "谨慎稳重"]), help="求职信语气"
)
@click.option("--lang", default="zh", type=click.Choice(["zh", "en"]), help="输出语言（zh 中文 / en 英文）")
@click.pass_context
def ai_cover_letter_cmd(
	ctx: click.Context, resume_name: str, jd_text: str | None, job_id: str | None, tone: str, lang: str
) -> None:
	"""基于本地简历与目标岗位起草求职信/自我介绍（仅草稿，不发送）"""
	svc = _require_ai_service(ctx)
	if svc is None:
		return

	if not jd_text and not job_id:
		search_cmd = _hint_command(ctx, "search <关键词>")
		handle_error_output(
			ctx,
			"ai",
			code="INVALID_PARAM",
			message="需要指定 --jd 或 --job-id",
			recovery_action=f"补上 --jd <职位描述> 或 --job-id <编号> 后重试；编号可从 {search_cmd} 的结果中取到",
			hints={"next_actions": [search_cmd]},
		)
		ctx.exit(1)
		return

	# 从缓存加载 JD（键为 encryptJobId）
	if job_id:
		with CacheStore(ctx.obj["data_dir"] / "cache" / "boss_agent.db") as cache:
			jd_text = cache.get_job_desc(job_id)
		if not jd_text:
			handle_error_output(
				ctx, "ai",
				code="CACHE_MISS",
				message=f"job_id '{job_id}' 的职位描述未缓存",
				recoverable=True,
				recovery_action=_hint_command(ctx, f"detail <security_id> --job-id {shell_argument(job_id)}"),
			)
			ctx.exit(1)
			return

	# 支持 @file 语法
	if jd_text:
		resolved = _read_text_argument(ctx, jd_text)
		if resolved is None:
			return
		jd_text = resolved

	resume_text = _load_resume_text(ctx, resume_name)
	if resume_text is None:
		return

	lang_label = "英文（English）" if lang == "en" else "中文"
	prompt = COVER_LETTER_PROMPT.format(jd_text=jd_text, resume_text=resume_text, tone=tone, lang=lang_label)
	result = _call_ai(ctx, svc, prompt)
	if result is None:
		return

	result.setdefault("greeting_opener", "")
	result.setdefault("cover_letter", "")
	result.setdefault("highlights", [])
	handle_output(
		ctx, "ai-cover-letter", result,
		render=lambda d: render_ai_result(d, title="ai-cover-letter"),
		hints={
			"note": "仅为 AI 生成的草稿，请自行核对真实性后再使用；本命令不发送任何消息",
			"next_actions": [
				_hint_command(ctx, f"ai interview-prep <jd_text> --resume {shell_argument(resume_name)}"),
			],
		},
	)
