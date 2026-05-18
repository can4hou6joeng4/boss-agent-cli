from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import click
import httpx

from boss_agent_cli.auth.browser import probe_cdp, _DEFAULT_CDP_URL
from boss_agent_cli.auth.cookie_extract import extract_cookies
from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.display import handle_output, render_simple_list

_PLATFORM_DIAG_CONFIG: dict[str, dict[str, Any]] = {
	"zhipin": {
		"auth_dir_suffix": (),
		"primary_cookie": "wt2",
		"secondary_token_label": "stoken",
		"secondary_token_key": "stoken",
		"aux_cookies": ["wbg", "zp_at"],
		"cookie_domain_label": "zhipin",
		"site_url": "https://www.zhipin.com/",
		"site_host": "zhipin.com",
		"login_action": "boss login",
	},
	"zhilian": {
		"auth_dir_suffix": ("zhilian",),
		"primary_cookie": "zp_token",
		"secondary_token_label": "x-zp-client-id",
		"secondary_token_key": "x_zp_client_id",
		"aux_cookies": ["at", "rt"],
		"cookie_domain_label": "zhaopin",
		"site_url": "https://www.zhaopin.com/",
		"site_host": "zhaopin.com",
		"login_action": "boss --platform zhilian login",
	},
}


@click.command("doctor")
@click.pass_context
def doctor_cmd(ctx: click.Context) -> None:
	"""诊断本地运行环境、依赖和登录条件。"""
	data_dir = ctx.obj["data_dir"]
	platform_name = ctx.obj.get("platform", "zhipin")
	config = _PLATFORM_DIAG_CONFIG.get(platform_name, _PLATFORM_DIAG_CONFIG["zhipin"])
	auth = AuthManager(data_dir, platform=platform_name)
	cdp_url = ctx.obj.get("cdp_url")

	checks: list[dict[str, Any]] = []

	def add_check(name: str, status: str, detail: str, hint: str | None = "") -> None:
		checks.append({
			"name": name,
			"status": status,
			"detail": detail,
			"hint": hint,
		})

	# 1) CLI dependencies
	import sys
	py_version = sys.version_info
	python_ok = py_version >= (3, 10)
	py_detail = f"Python {py_version.major}.{py_version.minor}.{py_version.micro}"
	add_check(
		"python",
		"ok" if python_ok else "error",
		f"{py_detail}" if python_ok else f"{py_detail}（需要 >=3.10）",
		None if python_ok else "升级 Python 到 3.10+",
	)

	patchright_bin = shutil.which("patchright")
	add_check(
		"patchright",
		"ok" if patchright_bin else "warn",
		f"{patchright_bin}" if patchright_bin else "未找到 patchright 可执行文件",
		"运行 uv run patchright install chromium 或确认已正确安装 patchright",
	)

	patchright_browser_dirs = [
		Path.home() / ".cache" / "ms-playwright",
		Path.home() / "Library" / "Caches" / "ms-playwright",
	]
	chromium_candidates: list[Path] = []
	for base in patchright_browser_dirs:
		if base.exists():
			chromium_candidates.extend(base.glob("chromium-*"))
	add_check(
		"patchright_chromium",
		"ok" if chromium_candidates else "warn",
		f"检测到 {len(chromium_candidates)} 个 Chromium 安装" if chromium_candidates else "未检测到 patchright/Playwright Chromium 缓存",
		"运行 patchright install chromium 安装浏览器内核",
	)

	chrome_bins = [
		"google-chrome",
		"google-chrome-stable",
		"chromium",
		"chromium-browser",
		"chrome",
		"msedge",
	]
	chrome_path = next((shutil.which(name) for name in chrome_bins if shutil.which(name)), None)
	add_check(
		"browser",
		"ok" if chrome_path else "warn",
		chrome_path or "未在 PATH 中发现 Chrome/Chromium/Edge",
		"如需 CDP 登录，请先启动支持远程调试端口的浏览器；如仅用 patchright，可忽略此项",
	)

	# 2) Auth storage
	auth_dir = data_dir / "auth"
	for suffix in config["auth_dir_suffix"]:
		auth_dir = auth_dir / suffix
	session_path = auth_dir / "session.enc"
	salt_path = auth_dir / "salt"
	token = auth.check_status()
	has_token = token is not None
	if token:
		cookie_count = len(token.get("cookies", {}))
		secondary_label = config["secondary_token_label"]
		secondary_key = config["secondary_token_key"]
		secondary_state = "存在" if token.get(secondary_key) or token.get("stoken") else "缺失"
		add_check("auth_session", "ok", f"检测到登录态文件: {session_path}（cookies={cookie_count}, {secondary_label}={secondary_state}）")
	else:
		status = "warn"
		detail = "未检测到本地登录态"
		if session_path.exists():
			status = "error"
			detail = f"检测到 session 文件但无法解密/已损坏: {session_path}"
		add_check("auth_session", status, detail, f"运行 {config['login_action']} 完成登录；如为旧密钥残留，可先 boss logout")

	add_check(
		"auth_salt",
		"ok" if salt_path.exists() else "warn",
		f"salt 文件{'存在' if salt_path.exists() else '不存在'}: {salt_path}",
		"首次保存登录态后会自动生成，可忽略",
	)

	if token:
		cookies = token.get("cookies", {}) or {}
		primary_cookie = config["primary_cookie"]
		secondary_label = config["secondary_token_label"]
		secondary_key = config["secondary_token_key"]
		has_primary_cookie = bool(cookies.get(primary_cookie))
		has_secondary_token = bool(token.get(secondary_key) or token.get("stoken"))
		if has_primary_cookie and has_secondary_token:
			quality_status = "ok"
			quality_detail = f"登录态完整：{primary_cookie}/{secondary_label} 均存在"
			quality_hint = "可直接运行 boss status / boss search 验证实际可用性"
		elif has_primary_cookie and not has_secondary_token:
			quality_status = "warn"
			quality_detail = f"登录态部分可用：{primary_cookie} 存在，但 {secondary_label} 缺失"
			quality_hint = f"通常仍可读信息；若请求失败可先运行 boss status，必要时 {config['login_action']} 触发重建/刷新"
		elif not has_primary_cookie and has_secondary_token:
			quality_status = "error"
			quality_detail = f"登录态异常：{secondary_label} 存在，但关键 Cookie {primary_cookie} 缺失"
			quality_hint = f"执行 boss logout && {config['login_action']} 重建登录态"
		else:
			quality_status = "error"
			quality_detail = f"登录态无效：{primary_cookie}/{secondary_label} 均缺失"
			quality_hint = f"执行 {config['login_action']} 建立有效登录态"
		add_check("auth_token_quality", quality_status, quality_detail, quality_hint)
		# Cookie 完整性检查（辅助 Cookie）
		missing_aux = [c for c in config["aux_cookies"] if not cookies.get(c)]
		if not missing_aux:
			aux_str = "/".join(config["aux_cookies"])
			add_check("cookie_completeness", "ok", f"辅助 Cookie 完整：{aux_str} 均存在")
		else:
			missing_str = "/".join(missing_aux)
			add_check(
				"cookie_completeness", "warn",
				f"辅助 Cookie 缺失：{missing_str}",
				f"部分接口可能受影响；重新登录通常可补全：boss logout && {config['login_action']}",
			)
	else:
		add_check("auth_token_quality", "warn", "未检测到可评估的登录态", f"先运行 {config['login_action']}，再用 boss doctor / boss status 复查")

	# 3) Cookie extraction support
	cookie_probe = extract_cookies(None, platform=platform_name)
	if cookie_probe:
		cookie_sources = ",".join(sorted(cookie_probe.get("cookies", {}).keys())[:5])
		add_check(
			"cookie_extract",
			"ok",
			f"可从本地浏览器提取 Cookie（示例键: {cookie_sources or 'n/a'}）",
		)
	else:
		add_check(
			"cookie_extract",
			"warn",
			f"未从本地浏览器提取到 {config['cookie_domain_label']} Cookie",
			f"请先在本机浏览器登录 {config['site_host']}，或使用 {config['login_action']}",
		)

	# 4) CDP availability
	try:
		ws_url = probe_cdp(cdp_url)
		cdp_detail = ws_url or f"CDP 不可用（目标: {cdp_url or _DEFAULT_CDP_URL}）"
		if ws_url:
			try:
				resp = httpx.get(f"{cdp_url or _DEFAULT_CDP_URL}/json/version", timeout=3)
				meta = resp.json()
				browser_name = meta.get("Browser") or "unknown-browser"
				user_agent = meta.get("User-Agent") or "unknown-ua"
				cdp_detail = f"{browser_name} | {user_agent} | {ws_url}"
			except Exception:
				pass
		add_check(
			"cdp",
			"ok" if ws_url else "warn",
			cdp_detail,
			"如需复用用户 Chrome，请以远程调试模式启动浏览器",
		)
	except Exception as e:
		add_check("cdp", "error", f"CDP 探测失败: {e}", "检查浏览器和调试端口配置")

	# 5) Network probe
	try:
		resp = httpx.get(config["site_url"], timeout=5, follow_redirects=True)
		status = "ok" if resp.status_code < 400 else "warn"
		add_check("network", status, f"访问 {config['site_host']} 返回 HTTP {resp.status_code}")
	except Exception as e:
		add_check("network", "warn", f"访问 {config['site_host']} 失败: {e}", "检查网络、代理或风控拦截")

	# 5.1) 跨平台网络探针：智联端点连通性（与当前平台无关，便于 Agent 调试 --platform zhilian）
	try:
		resp = httpx.get("https://www.zhaopin.com/", timeout=5, follow_redirects=True)
		zhilian_status = "ok" if resp.status_code < 400 else "warn"
		add_check("network_zhilian", zhilian_status, f"访问 zhaopin.com 返回 HTTP {resp.status_code}")
	except Exception as e:
		add_check("network_zhilian", "warn", f"访问 zhaopin.com 失败: {e}", "检查网络或代理")

	# 5.5) Browser channel risk assessment
	cdp_ok = any(item["name"] == "cdp" and item["status"] == "ok" for item in checks)
	bridge_ok = False
	try:
		from boss_agent_cli.bridge.client import BridgeClient
		bc = BridgeClient()
		bridge_ok = bc.is_running() and bc.is_extension_connected()
	except Exception:
		pass

	if cdp_ok or bridge_ok:
		mode = "CDP" if cdp_ok else "Bridge"
		add_check(
			"browser_channel",
			"ok",
			f"{mode} 兼容通道可用；默认低风险模式下不得用于规避平台风控",
		)
	else:
		add_check(
			"browser_channel",
			"warn",
			"CDP 和 Bridge 均不可用；默认低风险模式不依赖它们执行敏感操作",
			"如需登录，请使用 boss login；命中风控时停止自动化访问",
		)

	# 6) Data dir writable
	try:
		auth_dir.mkdir(parents=True, exist_ok=True)
		probe_file = auth_dir / ".doctor-write-test"
		probe_file.write_text("ok", encoding="utf-8")
		probe_file.unlink(missing_ok=True)
		add_check("data_dir", "ok", f"数据目录可写: {data_dir}")
	except Exception as e:
		add_check("data_dir", "error", f"数据目录不可写: {e}", "修改 --data-dir 或目录权限")

	status_rank = {"ok": 0, "warn": 1, "error": 2}
	worst = max((status_rank[item["status"]] for item in checks), default=0)
	summary = "healthy" if worst == 0 else ("degraded" if worst == 1 else "broken")

	next_actions = []
	if not has_token:
		next_actions.append(f"{config['login_action']} — 建立登录态")
	else:
		auth_quality = next((item for item in checks if item["name"] == "auth_token_quality"), None)
		if auth_quality and auth_quality["status"] == "warn":
			next_actions.append(f"boss status — 验证缺失 {config['secondary_token_label']} 的登录态是否仍可用")
			next_actions.append(f"如状态异常，执行 {config['login_action']} — 重建或刷新登录态")
		elif auth_quality and auth_quality["status"] == "error":
			next_actions.append(f"boss logout && {config['login_action']} — 重建损坏的登录态")
		else:
			next_actions.append("boss status — 验证当前账号是否可用")
	if not any(item["name"] == "cdp" and item["status"] == "ok" for item in checks):
		next_actions.append("boss --cdp-url http://localhost:9222 doctor — 检查指定 CDP 地址")
	if not any(item["name"] == "cookie_extract" and item["status"] == "ok" for item in checks):
		next_actions.append(f"先在本机浏览器登录 {config['site_host']}，再重试 {config['login_action']}")

	data = {
		"summary": summary,
		"data_dir": str(data_dir),
		"check_count": len(checks),
		"checks": checks,
	}
	hints = {
		"next_actions": next_actions,
	}
	handle_output(
		ctx,
		"doctor",
		data,
		render=lambda d: render_simple_list(
			d["checks"],
			f"doctor: {d['summary']}",
			[
				("name", "name", "cyan"),
				("status", "status", "green"),
				("detail", "detail", "white"),
			],
		),
		hints=hints,
	)
