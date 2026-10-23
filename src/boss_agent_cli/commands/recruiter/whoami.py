"""招聘者 — 当前登录态是否在招聘者侧。"""
from typing import Any

import click

from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.commands._platform import get_platform_instance
from boss_agent_cli.display import (
	boss_command_for_ctx,
	handle_auth_errors,
	handle_output,
	handle_platform_error_output,
)
from boss_agent_cli.services.recruiter_identity import interpret_recruiter_identity


@click.command("whoami")
@click.pass_context
@handle_auth_errors("recruiter-whoami")
def whoami_cmd(ctx: click.Context) -> None:
	"""查看当前登录态是否处于招聘者侧，不切换身份"""
	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]

	auth = AuthManager(data_dir, logger=logger, platform=ctx.obj.get("platform", "zhipin"))
	with get_platform_instance(ctx, auth) as platform:
		result = platform.user_info()
		if not platform.is_success(result):
			handle_platform_error_output(
				ctx,
				"recruiter-whoami",
				platform,
				result,
				fallback_message="招聘者身份获取失败",
			)
			return
		record = interpret_recruiter_identity(platform.unwrap_data(result))
		handle_output(ctx, "recruiter-whoami", record, hints=_hints(ctx, record))


def _hints(ctx: click.Context, record: dict[str, Any]) -> dict[str, list[str]]:
	if record["ready"] is True:
		return {
			"next_actions": [
				boss_command_for_ctx(ctx, "hr chat"),
				boss_command_for_ctx(ctx, "hr jobs list"),
			],
		}
	retry = boss_command_for_ctx(ctx, "hr whoami")
	if record["side"] == "candidate" and record["double_identity"] is True:
		return {
			"next_actions": [retry],
			"operator_actions": [
				f"当前登录态在求职者侧。到 BOSS 直聘官方页面切换到「我要招聘」，再执行 {retry}",
			],
		}
	if record["side"] == "candidate":
		return {
			"operator_actions": [
				"当前登录态在求职者侧。先在官方页面确认这个账号能否进入招聘者工作台，再执行 hr 命令",
			],
		}
	return {
		"operator_actions": [
			f"getUserInfo 的 identity 不是 0 或 1。先打开招聘者工作台，再执行 {retry}",
		],
	}
