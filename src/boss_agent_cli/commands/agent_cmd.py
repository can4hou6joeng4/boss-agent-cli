"""Agent 命令 — 配置和运行自主决策 Agent。"""

from __future__ import annotations

import click

from boss_agent_cli.agent.config import AgentConfig
from boss_agent_cli.agent.runner import AgentRunner
from boss_agent_cli.display import handle_auth_errors, handle_error_output, handle_output


@click.group("agent")
@click.pass_context
def agent_cmd(ctx: click.Context) -> None:
	"""自主决策 Agent（DeepSeek + 工具调用）。"""


@agent_cmd.command("config")
@click.option("--api-key", default=None, help="DeepSeek API 密钥（加密存储）")
@click.option("--model", default="deepseek-chat", help="模型名称")
@click.option("--resume-path", default=None, help="本地简历 PDF/文件路径")
@click.option("--salary-proof-path", default=None, help="期望薪资截图路径")
@click.option("--education-proof-path", default=None, help="学历/学位截图路径")
@click.pass_context
@handle_auth_errors("agent")
def agent_config_cmd(
	ctx: click.Context,
	api_key: str | None,
	model: str,
	resume_path: str | None,
	salary_proof_path: str | None,
	education_proof_path: str | None,
) -> None:
	"""配置 DeepSeek API 与上传素材路径。"""
	data_dir = ctx.obj["data_dir"]
	agent_config = AgentConfig(data_dir)

	has_ai = api_key is not None
	has_assets = any([resume_path, salary_proof_path, education_proof_path])

	if not has_ai and not has_assets:
		cfg = agent_config.get_ai_config()
		cfg["api_key_set"] = agent_config.get_api_key() is not None
		handle_output(ctx, "agent-config", {
			"ai": cfg,
			"assets": agent_config.get_assets(),
		})
		return

	if api_key:
		agent_config.configure_deepseek(api_key, model)
	if has_assets:
		agent_config.save_assets(
			resume_path=resume_path,
			salary_proof_path=salary_proof_path,
			education_proof_path=education_proof_path,
		)

	handle_output(ctx, "agent-config", {
		"message": "Agent 配置已更新",
		"provider": "deepseek",
		"model": model,
		"assets": agent_config.get_assets(),
	})


@agent_cmd.command("test")
@click.pass_context
@handle_auth_errors("agent")
def agent_test_cmd(ctx: click.Context) -> None:
	"""测试 Agent / AI 配置。"""
	agent_config = AgentConfig(ctx.obj["data_dir"])
	if not agent_config.is_configured():
		handle_error_output(
			ctx, "agent",
			code="AI_NOT_CONFIGURED",
			message="AI 未配置",
			recoverable=True,
			recovery_action="python run.py agent config --api-key <key>",
		)
		return
	cfg = agent_config.get_ai_config()
	handle_output(ctx, "agent-test", {
		"configured": True,
		"provider": cfg.get("ai_provider"),
		"model": cfg.get("ai_model"),
		"assets": agent_config.get_assets(),
	})


@agent_cmd.command("search")
@click.argument("query")
@click.option("--min-salary", default=15, help="最低薪资（K）")
@click.option("--exclude-outsourcing/--no-exclude-outsourcing", default=True)
@click.option("--exclude-dispatch/--no-exclude-dispatch", default=True)
@click.option("--exclude-remote/--no-exclude-remote", default=True)
@click.option("--use-llm/--no-llm", default=False, help="对通过规则的岗位做 LLM 分析")
@click.pass_context
@handle_auth_errors("agent")
def agent_search_cmd(
	ctx: click.Context,
	query: str,
	min_salary: int,
	exclude_outsourcing: bool,
	exclude_dispatch: bool,
	exclude_remote: bool,
	use_llm: bool,
) -> None:
	"""规则 + 可选 LLM 过滤职位（不消耗多轮 tool calling）。"""
	try:
		with AgentRunner(ctx.obj["data_dir"], platform_name=ctx.obj.get("platform", "zhipin")) as runner:
			runner.filter_tools.min_salary = min_salary
			runner.filter_tools.exclude_outsourcing = exclude_outsourcing
			runner.filter_tools.exclude_dispatch = exclude_dispatch
			runner.filter_tools.exclude_remote = exclude_remote
			if use_llm:
				jobs = runner.run_job_search_and_filter(query)
			else:
				resp = runner.toolkit.execute("search_jobs", {"query": query})
				if not resp.get("ok"):
					handle_error_output(ctx, "agent", code="SEARCH_FAILED", message=resp.get("error", "搜索失败"))
					return
				filtered = runner.toolkit.execute("filter_jobs", {"jobs": resp["jobs"], "use_llm": False})
				jobs = filtered.get("kept") or []
			handle_output(ctx, "agent-search", {"count": len(jobs), "jobs": jobs})
	except RuntimeError as exc:
		handle_error_output(
			ctx, "agent",
			code="AI_NOT_CONFIGURED",
			message=str(exc),
			recoverable=True,
			recovery_action="python run.py agent config --api-key <key>",
		)


@agent_cmd.command("run")
@click.argument("goal")
@click.option("--min-salary", default=15, help="最低薪资（K）")
@click.option("--exclude-outsourcing/--no-exclude-outsourcing", default=True)
@click.option("--exclude-dispatch/--no-exclude-dispatch", default=True)
@click.option("--exclude-remote/--no-exclude-remote", default=True)
@click.option("--max-rounds", default=12, help="最大 tool calling 轮次")
@click.pass_context
@handle_auth_errors("agent")
def agent_run_cmd(
	ctx: click.Context,
	goal: str,
	min_salary: int,
	exclude_outsourcing: bool,
	exclude_dispatch: bool,
	exclude_remote: bool,
	max_rounds: int,
) -> None:
	"""自主决策：DeepSeek 多轮调用工具完成求职任务。"""
	rules = (
		f"最低薪资 {min_salary}K；"
		f"排除外包={'是' if exclude_outsourcing else '否'}；"
		f"排除劳务派遣={'是' if exclude_dispatch else '否'}；"
		f"排除异地远程={'是' if exclude_remote else '否'}"
	)
	try:
		with AgentRunner(ctx.obj["data_dir"], platform_name=ctx.obj.get("platform", "zhipin")) as runner:
			runner.filter_tools.min_salary = min_salary
			runner.filter_tools.exclude_outsourcing = exclude_outsourcing
			runner.filter_tools.exclude_dispatch = exclude_dispatch
			runner.filter_tools.exclude_remote = exclude_remote
			runner.orchestrator.max_rounds = max_rounds
			result = runner.run_autonomous(goal, extra_rules=rules)
			handle_output(ctx, "agent-run", result)
	except RuntimeError as exc:
		handle_error_output(
			ctx, "agent",
			code="AI_NOT_CONFIGURED",
			message=str(exc),
			recoverable=True,
			recovery_action="python run.py agent config --api-key <key>",
		)


@agent_cmd.command("chat-run")
@click.option("--auto-mark/--no-auto-mark", default=True, help="对外包/劳务派遣会话自动打「不合适」")
@click.option("--security-id", default=None, help="仅处理指定联系人")
@click.pass_context
@handle_auth_errors("agent")
def agent_chat_run_cmd(ctx: click.Context, auto_mark: bool, security_id: str | None) -> None:
	"""批量分析沟通列表：识别索要简历/薪资/学历，规划上传与回复。"""
	try:
		with AgentRunner(ctx.obj["data_dir"], platform_name=ctx.obj.get("platform", "zhipin")) as runner:
			if security_id:
				result = runner.run_chat_analysis(security_id)
				handle_output(ctx, "agent-chat-run", result)
				return
			result = runner.process_all_chats(auto_mark_unsuitable=auto_mark)
			handle_output(ctx, "agent-chat-run", result)
	except RuntimeError as exc:
		handle_error_output(
			ctx, "agent",
			code="AI_NOT_CONFIGURED",
			message=str(exc),
			recoverable=True,
			recovery_action="python run.py agent config --api-key <key>",
		)
