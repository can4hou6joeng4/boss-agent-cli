from typing import TextIO

import click

from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.commands._login_errors import classify_login_error
from boss_agent_cli.display import boss_command_for_ctx
from boss_agent_cli.output import emit_error, emit_success


@click.command("login")
@click.option("--timeout", default=120, help="扫码登录超时时间（秒）")
@click.option("--cookie-source", default=None, help="指定浏览器提取 Cookie（如 chrome/firefox/edge），不指定则自动检测")
@click.option("--cdp", is_flag=True, default=False, help="强制 CDP 模式（跳过 Cookie 提取，CDP 不可用直接报错）")
@click.option("--force", is_flag=True, default=False, help="不复用任何既有登录态：跳过本地 Cookie 提取，CDP 下不复用已登录 context 并清掉目标平台 cookie 后重新登录")
@click.option("--curl-file", type=click.File("r", encoding="utf-8"), default=None, help="从 Copy as cURL (bash) 文件导入 BOSS 登录态；- 表示标准输入")
@click.pass_context
def login_cmd(ctx: click.Context, timeout: int, cookie_source: str | None, cdp: bool, force: bool, curl_file: TextIO | None) -> None:
	"""登录当前招聘平台（按平台走对应的 Cookie / CDP / 浏览器降级链路）"""
	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]
	cdp_url = ctx.obj.get("cdp_url")
	platform_name = ctx.obj.get("platform") or "zhipin"

	auth = AuthManager(data_dir, logger=logger, platform=platform_name)
	try:
		if curl_file is not None:
			if cookie_source is not None or cdp or force:
				raise ValueError("--curl-file 不能与 --cookie-source、--cdp 或 --force 同时使用")
			token = auth.import_curl(curl_file.read())
		else:
			if force and cookie_source is not None:
				raise ValueError("--force 会跳过本地 Cookie 提取，不能与 --cookie-source 同时使用")
			token = auth.login(
				timeout=timeout,
				cookie_source=cookie_source,
				cdp_url=cdp_url,
				force_cdp=cdp,
				force_relogin=force,
			)
		method = token.pop("_method", "未知")
		status_cmd = boss_command_for_ctx(ctx, "status")
		search_cmd = boss_command_for_ctx(ctx, "search <query>")
		recommend_cmd = boss_command_for_ctx(ctx, "recommend")
		emit_success(
			"login",
			{"message": f"登录成功（{method}）"},
			hints={
				"next_actions": [
					f"{status_cmd} — 验证登录态",
					f"{search_cmd} — 搜索职位",
					f"{recommend_cmd} — 获取个性化推荐",
				],
			},
		)
	except Exception as e:
		if curl_file is not None:
			# 文件解码、解析器或网络库的异常可能包含凭据，禁止透传原异常文本。
			emit_error(
				"login", code="INVALID_PARAM" if isinstance(e, ValueError) else "LOGIN_CREDENTIAL_EXTRACTION_FAILED",
				message="cURL 登录态导入失败，未完成会话更新", recoverable=True,
				recovery_action="核对 BOSS cURL 原始文本、选项及平台登录状态后重新导入",
				hints={"operator_actions": [
					"仅支持 BOSS 直聘 Copy as cURL (bash) 原始文本，不能与 --cdp 或 --cookie-source 混用",
					"检查文件格式、网络和网页登录状态；导入失败不会自动打开浏览器或重放请求",
				]},
			)
		else:
			emit_error("login", **classify_login_error(e, ctx))
