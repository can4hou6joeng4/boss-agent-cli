from __future__ import annotations

import shutil
from pathlib import Path

import click
import httpx

from boss_agent_cli.auth.browser import probe_cdp
from boss_agent_cli.auth.cookie_extract import extract_cookies
from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.display import handle_output, render_simple_list


@click.command("doctor")
@click.pass_context
def doctor_cmd(ctx):
	"""诊断本地运行环境、依赖和登录条件。"""
	data_dir = ctx.obj["data_dir"]
	auth = AuthManager(data_dir)
	cdp_url = ctx.obj.get("cdp_url")

	checks: list[dict] = []

	def add_check(name: str, status: str, detail: str, hint: str = ""):
		checks.append({
			"name": name,
			"status": status,
			"detail": detail,
			"hint": hint,
		})

	# 1) CLI dependencies
	python_ok = True
	add_check("python", "ok" if python_ok else "error", "Python 运行环境可用")

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
	chromium_candidates = []
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
	session_path = auth_dir / "session.enc"
	salt_path = auth_dir / "salt"
	token = auth.check_status()
	has_token = token is not None
	if token:
		cookie_count = len(token.get("cookies", {}))
		stoken_state = "存在" if token.get("stoken") else "缺失"
		add_check("auth_session", "ok", f"检测到登录态文件: {session_path}（cookies={cookie_count}, stoken={stoken_state}）")
	else:
		status = "warn"
		detail = "未检测到本地登录态"
		if session_path.exists():
			status = "error"
			detail = f"检测到 session 文件但无法解密/已损坏: {session_path}"
		add_check("auth_session", status, detail, "运行 boss login 完成登录；如为旧密钥残留，可先 boss logout")

	add_check(
		"auth_salt",
		"ok" if salt_path.exists() else "warn",
		f"salt 文件{'存在' if salt_path.exists() else '不存在'}: {salt_path}",
		"首次保存登录态后会自动生成，可忽略",
	)

	if token:
		cookies = token.get("cookies", {}) or {}
		has_wt2 = bool(cookies.get("wt2"))
		has_stoken = bool(token.get("stoken"))
		if has_wt2 and has_stoken:
			quality_status = "ok"
			quality_detail = "登录态完整：wt2/stoken 均存在"
			quality_hint = "可直接运行 boss status / boss search 验证实际可用性"
		elif has_wt2 and not has_stoken:
			quality_status = "warn"
			quality_detail = "登录态部分可用：wt2 存在，但 stoken 缺失"
			quality_hint = "通常仍可读信息；若请求失败可先运行 boss status，必要时 boss login 触发重建/刷新"
		elif not has_wt2 and has_stoken:
			quality_status = "error"
			quality_detail = "登录态异常：stoken 存在，但关键 Cookie wt2 缺失"
			quality_hint = "执行 boss logout && boss login 重建登录态"
		else:
			quality_status = "error"
			quality_detail = "登录态无效：wt2/stoken 均缺失"
			quality_hint = "执行 boss login 建立有效登录态"
		add_check("auth_token_quality", quality_status, quality_detail, quality_hint)
	else:
		add_check("auth_token_quality", "warn", "未检测到可评估的登录态", "先运行 boss login，再用 boss doctor / boss status 复查")

	# 3) Cookie extraction support
	cookie_probe = extract_cookies(None)
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
			"未从本地浏览器提取到 zhipin Cookie",
			"请先在本机浏览器登录 zhipin.com，或使用 boss login 走 CDP/扫码",
		)

	# 4) CDP availability
	try:
		ws_url = probe_cdp(cdp_url)
		cdp_detail = ws_url or f"CDP 不可用（目标: {cdp_url or 'http://localhost:9222'}）"
		if ws_url:
			try:
				resp = httpx.get(f"{cdp_url or 'http://localhost:9222'}/json/version", timeout=3)
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
		resp = httpx.get("https://www.zhipin.com/", timeout=5, follow_redirects=True)
		status = "ok" if resp.status_code < 400 else "warn"
		add_check("network", status, f"访问 zhipin.com 返回 HTTP {resp.status_code}")
	except Exception as e:
		add_check("network", "warn", f"访问 zhipin.com 失败: {e}", "检查网络、代理或风控拦截")

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
		next_actions.append("boss login — 建立登录态")
	else:
		auth_quality = next((item for item in checks if item["name"] == "auth_token_quality"), None)
		if auth_quality and auth_quality["status"] == "warn":
			next_actions.append("boss status — 验证缺失 stoken 的登录态是否仍可用")
			next_actions.append("如状态异常，执行 boss login — 重建或刷新登录态")
		elif auth_quality and auth_quality["status"] == "error":
			next_actions.append("boss logout && boss login — 重建损坏的登录态")
		else:
			next_actions.append("boss status — 验证当前账号是否可用")
	if not any(item["name"] == "cdp" and item["status"] == "ok" for item in checks):
		next_actions.append("boss --cdp-url http://localhost:9222 doctor — 检查指定 CDP 地址")
	if not any(item["name"] == "cookie_extract" and item["status"] == "ok" for item in checks):
		next_actions.append("先在本机浏览器登录 zhipin.com，再重试 boss login")

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
