from pathlib import Path
from typing import Any

import click

from boss_agent_cli.api.request_budget import risk_lock_status as lock_status
from boss_agent_cli.auth.health import assess_auth_health
from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.commands._platform import get_platform_instance
from boss_agent_cli.display import (
	auth_required_hints,
	boss_command_for_ctx,
	handle_auth_errors,
	handle_error_output,
	handle_output,
	handle_platform_error_output,
	login_action_for_ctx,
	render_status,
)


@click.command("status")
@click.option("--live", is_flag=True, default=False, help="执行一次只读在线验证（默认仅检查本地登录态）")
@click.pass_context
@handle_auth_errors("status")
def status_cmd(ctx: click.Context, live: bool) -> None:
	"""检查当前登录态"""
	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]
	platform_name = ctx.obj.get("platform", "zhipin")
	auth = AuthManager(data_dir, logger=logger, platform=platform_name)

	token = auth.check_status()
	auth_health = assess_auth_health(data_dir, platform=platform_name, token=token)
	if token is None:
		login_action = login_action_for_ctx(ctx)
		handle_error_output(
			ctx, "status",
			code="AUTH_REQUIRED",
			message=f"未登录，请先执行 {login_action}",
			recoverable=True, recovery_action=login_action,
			hints={"auth_health": auth_health.public_summary(), "checks": auth_health.checks_as_dicts(), **auth_required_hints(ctx)},
		)
		return

	data = {
		"logged_in": True,
		"live": live,
		"user_name": None,
		"token_expires_in": None,
		"auth_state": auth_health.auth_state,
		"auth_summary": auth_health.summary,
		"auth_health": auth_health.public_summary(),
		"checks": auth_health.checks_as_dicts(),
		# 只读本地锁文件，不连浏览器、不访问网络
		"cdp_risk_lock": lock_status(Path(data_dir)),
	}

	if not live:
		status_hints: dict[str, Any] = {
			"next_actions": _status_next_actions(auth_health.auth_state, login_action=login_action_for_ctx(ctx)),
			"live_probe": "运行 boss status --live 执行一次只读在线验证",
		}
		status_operator_actions = _status_operator_actions(auth_health.auth_state)
		if status_operator_actions:
			status_hints["operator_actions"] = status_operator_actions
		handle_output(
			ctx,
			"status",
			data,
			render=lambda payload: render_status(payload, login_action=login_action_for_ctx(ctx)),
			hints=status_hints,
		)
		return

	with get_platform_instance(ctx, auth) as platform:
		info = platform.user_info()
		if not platform.is_success(info):
			# 错误码按登记表给出 recoverable / recovery_action；风控码的停止指引
			# 由风险契约覆盖，本地健康检查随 extra_hints 一并带回。
			handle_platform_error_output(
				ctx,
				"status",
				platform,
				info,
				fallback_message="用户信息获取失败",
				extra_hints={"auth_health": auth_health.public_summary(), "checks": auth_health.checks_as_dicts()},
			)
			return
		user_info = platform.unwrap_data(info) or {}
		user_name = user_info.get("name", "未知用户")
		data["user_name"] = user_name
		# 非 live 的下一步正是 `--live`；在线验证本身成功后不能到此为止，
		# 否则 Agent 做完健康检查拿不到任何可执行的业务命令（TTY 里也只有一行
		# 「logged in」，真人同样不知道该干什么）。
		handle_output(
			ctx,
			"status",
			data,
			render=lambda payload: render_status(payload, login_action=login_action_for_ctx(ctx)),
			hints={
				"next_actions": [
					boss_command_for_ctx(ctx, "recommend"),
					boss_command_for_ctx(ctx, "search <关键词>"),
				],
				"operator_actions": ["登录态在线验证通过，可以直接开始浏览推荐职位或搜索职位"],
			},
		)


def _status_next_actions(auth_state: str, *, login_action: str) -> list[str]:
	# hints.next_actions 只放能直接执行的命令；`&&` 组合与「先启动带调试端口的
	# 浏览器」这类前置条件属于操作者指引，见 _status_operator_actions。
	if auth_state == "complete":
		return ["boss status --live"]
	if auth_state == "partial":
		return ["boss login --cdp", "boss status --live"]
	if auth_state == "broken":
		return ["boss logout", login_action]
	return [login_action]


def _status_operator_actions(auth_state: str) -> list[str]:
	if auth_state == "partial":
		return ["想走 CDP 复用浏览器时，先用远程调试端口启动 Chrome，再执行 boss login --cdp"]
	return []
