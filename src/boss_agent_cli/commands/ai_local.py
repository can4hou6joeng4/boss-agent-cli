"""Local AI model management commands."""

from __future__ import annotations

import subprocess
from dataclasses import asdict
from pathlib import Path

import click

from boss_agent_cli.ai.config import AIConfigStore
from boss_agent_cli.ai.local_models import (
	RUNTIME_BASE_URLS,
	LocalModelManifestError,
	import_local_model,
	read_imported_models,
	recommended_model_rows,
)
from boss_agent_cli.ai.service import AIService, AIServiceError
from boss_agent_cli.display import handle_error_output, handle_output


@click.group("local")
def ai_local_group() -> None:
	"""本地模型管理。"""


@ai_local_group.command("status")
@click.pass_context
def local_status_cmd(ctx: click.Context) -> None:
	"""查看本地模型配置与推荐清单。"""
	store = AIConfigStore(ctx.obj["data_dir"])
	config = store.load_config()
	handle_output(
		ctx,
		"ai.local.status",
		{
			"configured": store.is_configured(),
			"provider": config.get("ai_provider"),
			"model": config.get("ai_model"),
			"base_url": store.get_base_url(),
			"api_key_set": store.get_api_key() is not None,
			"recommended_models": recommended_model_rows(),
			"imported_models": [asdict(item) for item in read_imported_models(ctx.obj["data_dir"])],
		},
		hints={"next_actions": ["boss ai local configure --runtime ollama --model qwen3:14b"]},
	)


@ai_local_group.command("configure")
@click.option("--runtime", required=True, type=click.Choice(["ollama", "vllm"]))
@click.option("--model", required=True)
@click.option("--base-url", default=None)
@click.pass_context
def local_configure_cmd(ctx: click.Context, runtime: str, model: str, base_url: str | None) -> None:
	"""配置本地 OpenAI-compatible 模型服务。"""
	store = AIConfigStore(ctx.obj["data_dir"])
	store.save_config(
		ai_provider=runtime,
		ai_model=model,
		ai_base_url=base_url or RUNTIME_BASE_URLS[runtime],
		ai_temperature=0.3,
		ai_max_tokens=512,
	)
	store.save_api_key("local")
	handle_output(
		ctx,
		"ai.local.configure",
		{"runtime": runtime, "model": model, "base_url": store.get_base_url()},
		hints={"next_actions": ["boss ai local smoke", "boss ai local status"]},
	)


@ai_local_group.command("pull")
@click.option("--model", required=True)
@click.option("--confirm-download", is_flag=True, default=False)
@click.pass_context
def local_pull_cmd(ctx: click.Context, model: str, confirm_download: bool) -> None:
	"""通过 Ollama 下载模型权重。"""
	if not confirm_download:
		handle_error_output(
			ctx,
			"ai.local.pull",
			code="CONFIRM_DOWNLOAD_REQUIRED",
			message="下载模型权重需要显式传 --confirm-download",
			recoverable=True,
			recovery_action=f"boss ai local pull --model {model} --confirm-download",
		)
	result = subprocess.run(
		["ollama", "pull", model],
		check=False,
		capture_output=True,
		text=True,
	)
	if result.returncode != 0:
		handle_error_output(
			ctx,
			"ai.local.pull",
			code="LOCAL_MODEL_PULL_FAILED",
			message=result.stderr.strip() or "ollama pull failed",
			recoverable=True,
			recovery_action="确认 Ollama 已安装并可访问网络后重试",
		)
	handle_output(ctx, "ai.local.pull", {"status": "installed", "model": model})


@ai_local_group.command("import")
@click.option("--path", "source_path", required=True, type=click.Path(exists=True, path_type=Path))
@click.option("--model", required=True)
@click.pass_context
def local_import_cmd(ctx: click.Context, source_path: Path, model: str) -> None:
	"""导入离线模型文件或目录到本地数据目录。"""
	try:
		imported = import_local_model(ctx.obj["data_dir"], source_path, model)
	except LocalModelManifestError as exc:
		handle_error_output(
			ctx,
			"ai.local.import",
			code=exc.code,
			message=exc.message,
			recoverable=True,
			recovery_action="检查模型路径和 manifest 后重试",
		)
	store = AIConfigStore(ctx.obj["data_dir"])
	store.save_config(ai_provider="custom", ai_model=model, ai_base_url=None)
	store.save_api_key("local")
	handle_output(ctx, "ai.local.import", asdict(imported))


@ai_local_group.command("smoke")
@click.pass_context
def local_smoke_cmd(ctx: click.Context) -> None:
	"""调用一次本地 OpenAI-compatible 服务做健康检查。"""
	store = AIConfigStore(ctx.obj["data_dir"])
	config = store.load_config()
	api_key = store.get_api_key()
	base_url = store.get_base_url()
	model = config.get("ai_model")
	if not api_key or not base_url or not model:
		handle_error_output(
			ctx,
			"ai.local.smoke",
			code="AI_NOT_CONFIGURED",
			message="本地 AI 服务未配置",
			recoverable=True,
			recovery_action="boss ai local configure --runtime ollama --model qwen3:14b",
		)
		return
	service = AIService(
		base_url=base_url,
		api_key=api_key,
		model=str(model),
		temperature=0.1,
		max_tokens=32,
	)
	try:
		reply = service.chat([
			{"role": "system", "content": "Return a short plain-text health response."},
			{"role": "user", "content": "ping"},
		])
	except AIServiceError as exc:
		handle_error_output(
			ctx,
			"ai.local.smoke",
			code="AI_API_ERROR",
			message=f"本地 AI 服务调用失败: {exc}",
			recoverable=True,
			recovery_action="确认本地模型服务已启动后重试",
		)
	handle_output(ctx, "ai.local.smoke", {"status": "ok", "model": str(model), "reply": reply[:200]})
