"""doctor.py 扩展测试 — 覆盖各检查项的正常/异常路径。"""

import json
import sys
from unittest.mock import patch, MagicMock

from click.testing import CliRunner
from boss_agent_cli.main import cli


# ── 辅助 ─────────────────────────────────────────────────────────────


def _base_patches():
	"""返回 doctor 命令核心 mock 的 patch 路径字典。"""
	return {
		"auth": "boss_agent_cli.commands.doctor.AuthManager",
		"cdp": "boss_agent_cli.commands.doctor.probe_cdp",
		"httpx": "boss_agent_cli.commands.doctor.httpx.get",
		"cookie": "boss_agent_cli.commands.doctor.extract_cookies",
	}


def _invoke_doctor(tmp_path=None, platform="zhipin", **overrides):
	"""执行 doctor 命令并返回 (exit_code, parsed_json)。

	overrides 可以覆盖 token / cdp_ws / cookie / http_status / http_exc。
	传入 tmp_path 以使用隔离的 data-dir，避免读取本机真实文件。
	"""
	runner = CliRunner()
	paths = _base_patches()
	with (
		patch(paths["auth"]) as mock_auth,
		patch(paths["cdp"]) as mock_cdp,
		patch(paths["httpx"]) as mock_httpx,
		patch(paths["cookie"]) as mock_cookie,
		patch("boss_agent_cli.bridge.client.BridgeClient") as mock_bridge,
	):
		# BridgeClient 构造不抛异常，但 is_running 返回 False
		mock_bridge.return_value.diagnose.return_value = [
			{
				"name": "bridge_daemon",
				"status": "warn",
				"detail": "Bridge daemon 未运行或无法访问 http://127.0.0.1:19826",
				"recovery_action": "运行 Bridge daemon 或检查端口占用",
			}
		]
		# 默认值
		mock_auth.return_value.check_status.return_value = overrides.get("token", None)
		mock_cdp.return_value = overrides.get("cdp_ws", None)
		mock_cookie.return_value = overrides.get("cookie", None)

		if "http_exc" in overrides:
			mock_httpx.side_effect = overrides["http_exc"]
		else:
			mock_httpx.return_value = MagicMock(status_code=overrides.get("http_status", 200))

		cli_args = []
		if tmp_path is not None:
			cli_args.extend(["--data-dir", str(tmp_path)])
		if platform != "zhipin":
			cli_args.extend(["--platform", platform])
		cli_args.append("doctor")
		result = runner.invoke(cli, cli_args)
	parsed = json.loads(result.output)
	return result.exit_code, parsed


def _find_check(checks, name):
	return next((c for c in checks if c["name"] == name), None)


# ── 1. 所有检查项正常通过 ────────────────────────────────────────────


def test_all_checks_pass(tmp_path):
	"""所有检查项均为 ok 时 summary 应为 healthy（环境依赖项除外）。"""
	token = {"cookies": {"wt2": "tok", "wbg": "1", "zp_at": "a"}, "stoken": "st_ok", "user_agent": "ua"}
	code, parsed = _invoke_doctor(
		tmp_path,
		token=token,
		cdp_ws="ws://127.0.0.1:9222/devtools/browser/abc",
		cookie={"cookies": {"wt2": "tok"}},
		http_status=200,
	)
	assert code == 0
	assert parsed["ok"] is True
	# 核心逻辑检查项（不依赖本机工具链）应全部为 ok
	env_dependent = {"patchright", "patchright_chromium", "browser", "auth_salt", "bridge_daemon"}
	for check in parsed["data"]["checks"]:
		if check["name"] in env_dependent:
			continue
		assert check["status"] == "ok", f"{check['name']} 状态应为 ok，实际为 {check['status']}"


# ── 2. Python 版本不满足 ─────────────────────────────────────────────


def test_python_version_below_310(tmp_path):
	"""Python 版本低于 3.10 时 python 检查应为 error。"""

	class FakeVersionInfo(tuple):
		"""模拟 sys.version_info：同时支持元组比较和属性访问。"""

		major = 3
		minor = 9
		micro = 1

		def __new__(cls):
			return tuple.__new__(cls, (3, 9, 1))

	original = sys.version_info
	sys.version_info = FakeVersionInfo()
	try:
		code, parsed = _invoke_doctor(tmp_path)
	finally:
		sys.version_info = original
	python_check = _find_check(parsed["data"]["checks"], "python")
	assert python_check is not None
	assert python_check["status"] == "error"
	assert "3.10" in python_check["detail"]


def test_python_version_satisfies(tmp_path):
	"""当前测试运行的 Python 应满足 >=3.10。"""
	code, parsed = _invoke_doctor(tmp_path)
	python_check = _find_check(parsed["data"]["checks"], "python")
	assert python_check["status"] == "ok"


# ── 3. 依赖缺失 ─────────────────────────────────────────────────────


@patch("boss_agent_cli.commands.doctor.shutil.which", return_value=None)
def test_patchright_missing(mock_which, tmp_path):
	"""patchright 可执行文件不在 PATH 中时应为 warn。"""
	code, parsed = _invoke_doctor(tmp_path)
	pr_check = _find_check(parsed["data"]["checks"], "patchright")
	assert pr_check["status"] == "warn"
	assert "未找到" in pr_check["detail"]


# ── 4. 网络不通 ──────────────────────────────────────────────────────


def test_network_failure(tmp_path):
	"""访问 zhipin.com 抛异常时 network 应为 warn。"""
	code, parsed = _invoke_doctor(tmp_path, http_exc=ConnectionError("network down"))
	network_check = _find_check(parsed["data"]["checks"], "network")
	assert network_check is not None
	assert network_check["status"] == "warn"
	assert "失败" in network_check["detail"]


def test_network_http_error_status(tmp_path):
	"""zhipin.com 返回 HTTP 5xx 时 network 应为 warn。"""
	code, parsed = _invoke_doctor(tmp_path, http_status=503)
	network_check = _find_check(parsed["data"]["checks"], "network")
	assert network_check["status"] == "warn"


def test_network_ok(tmp_path):
	"""zhipin.com 返回 200 时 network 应为 ok。"""
	code, parsed = _invoke_doctor(tmp_path, http_status=200)
	network_check = _find_check(parsed["data"]["checks"], "network")
	assert network_check["status"] == "ok"


# ── 5. 登录态检查 ────────────────────────────────────────────────────


def test_auth_logged_in_full(tmp_path):
	"""wt2 + stoken 均在时 auth_token_quality 应为 ok。"""
	token = {"cookies": {"wt2": "tok"}, "stoken": "stoken_val"}
	code, parsed = _invoke_doctor(tmp_path, token=token)
	quality = _find_check(parsed["data"]["checks"], "auth_token_quality")
	assert quality["status"] == "ok"
	assert "完整" in quality["detail"]
	stoken_presence = _find_check(parsed["data"]["checks"], "stoken_presence")
	assert stoken_presence["status"] == "ok"


def test_auth_logged_in_no_stoken(tmp_path):
	"""wt2 存在但 stoken 缺失时 quality 应为 warn。"""
	token = {"cookies": {"wt2": "tok"}, "stoken": ""}
	code, parsed = _invoke_doctor(tmp_path, token=token)
	quality = _find_check(parsed["data"]["checks"], "auth_token_quality")
	assert quality["status"] == "warn"
	assert "stoken 缺失" in quality["detail"]
	stoken_presence = _find_check(parsed["data"]["checks"], "stoken_presence")
	assert stoken_presence["status"] == "warn"
	assert "CDP" in stoken_presence["recovery_action"]
	assert parsed["data"]["auth_state"] == "partial"


def test_auth_logged_in_no_wt2(tmp_path):
	"""stoken 存在但 wt2 缺失时 quality 应为 error。"""
	token = {"cookies": {}, "stoken": "stoken_val"}
	code, parsed = _invoke_doctor(tmp_path, token=token)
	quality = _find_check(parsed["data"]["checks"], "auth_token_quality")
	assert quality["status"] == "error"
	assert "wt2 缺失" in quality["detail"]


def test_auth_logged_in_both_missing(tmp_path):
	"""wt2 和 stoken 均缺失时 quality 应为 error。"""
	token = {"cookies": {}, "stoken": ""}
	code, parsed = _invoke_doctor(tmp_path, token=token)
	quality = _find_check(parsed["data"]["checks"], "auth_token_quality")
	assert quality["status"] == "error"
	assert "均缺失" in quality["detail"]


def test_auth_not_logged_in(tmp_path):
	"""未登录时（无 session 文件）auth_session 应为 warn，quality 也应为 warn。"""
	code, parsed = _invoke_doctor(tmp_path, token=None)
	session = _find_check(parsed["data"]["checks"], "auth_session")
	assert session["status"] == "warn"
	quality = _find_check(parsed["data"]["checks"], "auth_token_quality")
	assert quality["status"] == "warn"
	# next_actions 应包含 login 提示
	assert any("boss login" in a for a in parsed["hints"]["next_actions"])


def test_auth_session_file_exists_but_undecryptable(tmp_path):
	"""session.enc 存在但无法解密时 auth_session 应为 error。"""
	auth_dir = tmp_path / "auth"
	auth_dir.mkdir(parents=True)
	(auth_dir / "session.enc").write_text("corrupted")
	code, parsed = _invoke_doctor(tmp_path, token=None)
	session = _find_check(parsed["data"]["checks"], "auth_session")
	assert session["status"] == "error"
	assert "损坏" in session["detail"]


# ── 6. CDP 可用/不可用 ───────────────────────────────────────────────


def test_cdp_available(tmp_path):
	"""CDP 探测返回 ws url 时 cdp 检查应为 ok。"""
	code, parsed = _invoke_doctor(tmp_path, cdp_ws="ws://127.0.0.1:9222/devtools/browser/abc")
	cdp_check = _find_check(parsed["data"]["checks"], "cdp")
	assert cdp_check["status"] == "ok"


def test_cdp_unavailable(tmp_path):
	"""CDP 探测返回 None 时 cdp 检查应为 warn。"""
	code, parsed = _invoke_doctor(tmp_path, cdp_ws=None)
	cdp_check = _find_check(parsed["data"]["checks"], "cdp")
	assert cdp_check["status"] == "warn"


def test_cdp_probe_exception(tmp_path):
	"""CDP 探测抛异常时 cdp 检查应为 error。"""
	paths = _base_patches()
	runner = CliRunner()
	with (
		patch(paths["auth"]) as mock_auth,
		patch(paths["cdp"]) as mock_cdp,
		patch(paths["httpx"]) as mock_httpx,
		patch(paths["cookie"]) as mock_cookie,
		patch("boss_agent_cli.bridge.client.BridgeClient") as mock_bridge,
	):
		mock_bridge.return_value.is_running.return_value = False
		mock_auth.return_value.check_status.return_value = None
		mock_cdp.side_effect = RuntimeError("probe boom")
		mock_cookie.return_value = None
		mock_httpx.return_value = MagicMock(status_code=200)

		result = runner.invoke(cli, ["--data-dir", str(tmp_path), "doctor"])
	parsed = json.loads(result.output)
	cdp_check = _find_check(parsed["data"]["checks"], "cdp")
	assert cdp_check["status"] == "error"
	assert "probe boom" in cdp_check["detail"]


def test_bridge_diagnostics_are_included(tmp_path):
	"""doctor 应输出拆分后的 Bridge daemon/extension/protocol/workspace 检查。"""
	bridge_checks = [
		{"name": "bridge_daemon", "status": "ok", "detail": "Bridge daemon 运行中 pid=123 uptime=7s"},
		{"name": "bridge_extension", "status": "ok", "detail": "Chrome 扩展已连接 version=1.0.0"},
		{"name": "bridge_protocol", "status": "ok", "detail": "扩展协议版本 1.0.0；CLI 期望 major=1"},
		{
			"name": "bridge_workspace",
			"status": "ok",
			"detail": "Bridge workspace/tab 可用: https://www.zhipin.com/web/geek/job",
		},
		{"name": "bridge_exec", "status": "ok", "detail": "Bridge exec 基础能力可用"},
		{"name": "bridge_fetch", "status": "ok", "detail": "Bridge fetch 基础能力可用"},
		{"name": "bridge_navigate", "status": "ok", "detail": "Bridge navigate 基础能力可用"},
	]
	paths = _base_patches()
	runner = CliRunner()
	with (
		patch(paths["auth"]) as mock_auth,
		patch(paths["cdp"]) as mock_cdp,
		patch(paths["httpx"]) as mock_httpx,
		patch(paths["cookie"]) as mock_cookie,
		patch("boss_agent_cli.bridge.client.BridgeClient") as mock_bridge,
	):
		mock_auth.return_value.check_status.return_value = None
		mock_cdp.return_value = None
		mock_cookie.return_value = None
		mock_httpx.return_value = MagicMock(status_code=200)
		mock_bridge.return_value.diagnose.return_value = bridge_checks

		result = runner.invoke(cli, ["--data-dir", str(tmp_path), "doctor"])

	parsed = json.loads(result.output)
	checks = parsed["data"]["checks"]
	for name in (
		"bridge_daemon",
		"bridge_extension",
		"bridge_protocol",
		"bridge_workspace",
		"bridge_exec",
		"bridge_fetch",
		"bridge_navigate",
	):
		assert _find_check(checks, name) is not None
	browser_channel = _find_check(checks, "browser_channel")
	assert browser_channel["status"] == "ok"
	assert "Bridge" in browser_channel["detail"]


def test_bridge_diagnostics_do_not_expose_secrets(tmp_path):
	"""doctor 输出不应泄漏 Bridge 诊断中的 cookie/header/token 字样值。"""
	bridge_checks = [
		{
			"name": "bridge_workspace",
			"status": "ok",
			"detail": "Bridge workspace/tab 可用: https://www.zhipin.com/web/geek/job",
			"tab_url": "https://www.zhipin.com/web/geek/job",
		}
	]
	paths = _base_patches()
	runner = CliRunner()
	with (
		patch(paths["auth"]) as mock_auth,
		patch(paths["cdp"]) as mock_cdp,
		patch(paths["httpx"]) as mock_httpx,
		patch(paths["cookie"]) as mock_cookie,
		patch("boss_agent_cli.bridge.client.BridgeClient") as mock_bridge,
	):
		mock_auth.return_value.check_status.return_value = None
		mock_cdp.return_value = None
		mock_cookie.return_value = None
		mock_httpx.return_value = MagicMock(status_code=200)
		mock_bridge.return_value.diagnose.return_value = bridge_checks

		result = runner.invoke(cli, ["--data-dir", str(tmp_path), "doctor"])

	raw = result.output.lower()
	assert "cookie_value" not in raw
	assert "authorization" not in raw
	assert "secret_token" not in raw


# ── 7. 输出 JSON 格式正确性 ─────────────────────────────────────────


def test_json_envelope_structure(tmp_path):
	"""验证 doctor 输出的 JSON 信封结构完整。"""
	code, parsed = _invoke_doctor(tmp_path)
	assert code == 0
	# 顶层字段
	assert "ok" in parsed
	assert "command" in parsed
	assert "data" in parsed
	assert "hints" in parsed
	# data 子字段
	data = parsed["data"]
	assert "summary" in data
	assert "auth_state" in data
	assert "data_dir" in data
	assert "live_probe" in data
	assert "check_count" in data
	assert "checks" in data
	assert isinstance(data["checks"], list)
	assert data["check_count"] == len(data["checks"])
	# 每条 check 的字段
	for check in data["checks"]:
		assert "name" in check
		assert "status" in check
		assert "detail" in check
		assert check["status"] in ("ok", "warn", "error")


def test_summary_degraded_when_warn_only(tmp_path):
	"""只有 warn（无 error）时 summary 应为 degraded。"""
	code, parsed = _invoke_doctor(tmp_path, token=None, cdp_ws=None, cookie=None)
	summary = parsed["data"]["summary"]
	has_error = any(c["status"] == "error" for c in parsed["data"]["checks"])
	has_warn = any(c["status"] == "warn" for c in parsed["data"]["checks"])
	if has_error:
		assert summary == "broken"
	elif has_warn:
		assert summary == "degraded"


def test_summary_broken_when_error_exists(tmp_path):
	"""存在 error 检查项时 summary 应为 broken。"""
	token = {"cookies": {}, "stoken": "stoken_val"}  # no wt2 => error
	code, parsed = _invoke_doctor(tmp_path, token=token)
	quality = _find_check(parsed["data"]["checks"], "auth_token_quality")
	assert quality["status"] == "error"
	assert parsed["data"]["summary"] == "broken"


# ── 8. browser_channel 检查 ──────────────────────────────────────────


def test_browser_channel_ok_with_cdp(tmp_path):
	"""CDP 可用时 browser_channel 应为 ok 且提示 CDP 模式。"""
	code, parsed = _invoke_doctor(tmp_path, cdp_ws="ws://127.0.0.1:9222/devtools/browser/abc")
	ch = _find_check(parsed["data"]["checks"], "browser_channel")
	assert ch is not None
	assert ch["status"] == "ok"
	assert "CDP" in ch["detail"]


def test_browser_channel_warn_without_cdp_or_bridge(tmp_path):
	"""CDP 和 Bridge 均不可用时 browser_channel 应为 warn。"""
	code, parsed = _invoke_doctor(tmp_path, cdp_ws=None)
	ch = _find_check(parsed["data"]["checks"], "browser_channel")
	assert ch is not None
	assert ch["status"] == "warn"
	assert "默认低风险模式" in ch["detail"]


# ── 9. cookie 提取检查 ───────────────────────────────────────────────


def test_cookie_extract_ok(tmp_path):
	"""本地浏览器可提取 Cookie 时应为 ok。"""
	code, parsed = _invoke_doctor(tmp_path, cookie={"cookies": {"wt2": "v", "token": "t"}})
	ce = _find_check(parsed["data"]["checks"], "cookie_extract")
	assert ce["status"] == "ok"
	assert "提取" in ce["detail"]


def test_cookie_extract_not_available(tmp_path):
	"""无法从本地浏览器提取 Cookie 时应为 warn。"""
	code, parsed = _invoke_doctor(tmp_path, cookie=None)
	ce = _find_check(parsed["data"]["checks"], "cookie_extract")
	assert ce["status"] == "warn"


# ── 10. next_actions 提示逻辑 ────────────────────────────────────────


def test_next_actions_suggests_login_when_not_logged_in(tmp_path):
	code, parsed = _invoke_doctor(tmp_path, token=None)
	actions = parsed["hints"]["next_actions"]
	assert any("boss login" in a for a in actions)


def test_next_actions_suggests_cdp_when_cdp_unavailable(tmp_path):
	code, parsed = _invoke_doctor(tmp_path, cdp_ws=None)
	actions = parsed["hints"]["next_actions"]
	assert any("cdp" in a.lower() for a in actions)


def test_next_actions_suggests_status_when_logged_in(tmp_path):
	token = {"cookies": {"wt2": "tok"}, "stoken": "st"}
	code, parsed = _invoke_doctor(tmp_path, token=token, cdp_ws="ws://x")
	actions = parsed["hints"]["next_actions"]
	assert any("boss status --live" in a for a in actions)


# ── Cookie 完整性检查（wbg/zp_at） ─────────────────────────────────


def test_cookie_completeness_all_present(tmp_path):
	"""四个关键 Cookie 全部存在时应报 ok。"""
	token = {"cookies": {"wt2": "t", "wbg": "1", "zp_at": "a"}, "stoken": "st"}
	code, parsed = _invoke_doctor(tmp_path, token=token)
	completeness = _find_check(parsed["data"]["checks"], "cookie_completeness")
	assert completeness is not None
	assert completeness["status"] == "ok"


def test_cookie_completeness_missing_wbg(tmp_path):
	"""wbg 缺失时应报 warn。"""
	token = {"cookies": {"wt2": "t", "zp_at": "a"}, "stoken": "st"}
	code, parsed = _invoke_doctor(tmp_path, token=token)
	completeness = _find_check(parsed["data"]["checks"], "cookie_completeness")
	assert completeness is not None
	assert completeness["status"] == "warn"
	assert "wbg" in completeness["detail"]


def test_doctor_includes_layered_auth_health_checks(tmp_path):
	token = {"cookies": {"wt2": "tok"}, "stoken": "stoken_val"}
	code, parsed = _invoke_doctor(tmp_path, token=token)
	names = {item["name"] for item in parsed["data"]["checks"]}
	assert {
		"credential_file",
		"cookie_presence",
		"wt2_presence",
		"stoken_presence",
		"stoken_freshness",
		"candidate_search_health",
		"candidate_detail_health",
		"recruiter_read_health",
	}.issubset(names)


def test_doctor_redacts_sensitive_token_values(tmp_path):
	token = {
		"cookies": {"wt2": "secret-wt2", "wbg": "secret-wbg", "zp_at": "secret-zp"},
		"stoken": "secret-stoken",
		"user_agent": "ua",
	}
	code, parsed = _invoke_doctor(tmp_path, token=token, cookie={"cookies": {"wt2": "secret-wt2"}})
	output = json.dumps(parsed, ensure_ascii=False)
	assert "secret-wt2" not in output
	assert "secret-stoken" not in output
	assert "secret-zp" not in output


def test_doctor_marks_zhilian_recruiter_agent_preflight_supported(tmp_path):
	token = {"cookies": {"zp_token": "tok", "at": "a", "rt": "r"}, "x_zp_client_id": "cid"}
	code, parsed = _invoke_doctor(tmp_path, platform="zhilian", token=token)
	recruiter = _find_check(parsed["data"]["checks"], "recruiter_read_health")
	assert recruiter["status"] == "ok"
	assert "browser/CDP" in recruiter["detail"]


@patch("boss_agent_cli.commands.doctor_checks.get_recruiter_platform_instance")
@patch("boss_agent_cli.commands.doctor_checks.get_platform_instance")
def test_doctor_live_probe_adds_readonly_probe_checks(mock_platform_cls, mock_recruiter_cls, tmp_path):
	paths = _base_patches()
	runner = CliRunner()
	with (
		patch(paths["auth"]) as mock_auth,
		patch(paths["cdp"]) as mock_cdp,
		patch(paths["httpx"]) as mock_httpx,
		patch(paths["cookie"]) as mock_cookie,
		patch("boss_agent_cli.bridge.client.BridgeClient") as mock_bridge,
	):
		mock_bridge.return_value.is_running.return_value = False
		mock_auth.return_value.check_status.return_value = {"cookies": {"wt2": "tok"}, "stoken": "st"}
		mock_cdp.return_value = None
		mock_cookie.return_value = None
		mock_httpx.return_value = MagicMock(status_code=200)

		platform = mock_platform_cls.return_value
		platform.__enter__ = lambda self: self
		platform.__exit__ = lambda self, *a: None
		platform.user_info.return_value = {"zpData": {"name": "tester"}}
		platform.is_success.return_value = True

		recruiter = mock_recruiter_cls.return_value
		recruiter.__enter__ = lambda self: self
		recruiter.__exit__ = lambda self, *a: None
		recruiter.list_jobs.return_value = {"zpData": {"list": []}}
		recruiter.is_success.return_value = True

		result = runner.invoke(cli, ["--data-dir", str(tmp_path), "doctor", "--live-probe"])

	parsed = json.loads(result.output)
	assert _find_check(parsed["data"]["checks"], "candidate_live_user_info")["status"] == "ok"
	assert _find_check(parsed["data"]["checks"], "recruiter_live_read")["status"] == "ok"


def test_cookie_completeness_missing_zp_at(tmp_path):
	"""zp_at 缺失时应报 warn。"""
	token = {"cookies": {"wt2": "t", "wbg": "1"}, "stoken": "st"}
	code, parsed = _invoke_doctor(tmp_path, token=token)
	completeness = _find_check(parsed["data"]["checks"], "cookie_completeness")
	assert completeness is not None
	assert completeness["status"] == "warn"
	assert "zp_at" in completeness["detail"]


def test_cookie_completeness_both_missing(tmp_path):
	"""wbg 和 zp_at 均缺失时应报 warn 并列出所有缺失项。"""
	token = {"cookies": {"wt2": "t"}, "stoken": "st"}
	code, parsed = _invoke_doctor(tmp_path, token=token)
	completeness = _find_check(parsed["data"]["checks"], "cookie_completeness")
	assert completeness is not None
	assert completeness["status"] == "warn"
	assert "wbg" in completeness["detail"]
	assert "zp_at" in completeness["detail"]


def test_zhilian_doctor_uses_platform_specific_auth_quality(tmp_path):
	token = {"cookies": {"zp_token": "tok"}, "x_zp_client_id": "cid"}
	code, parsed = _invoke_doctor(tmp_path, platform="zhilian", token=token, cookie={"cookies": {"zp_token": "tok"}})
	quality = _find_check(parsed["data"]["checks"], "auth_token_quality")
	assert quality is not None
	assert quality["status"] == "ok"
	assert "at/x-zp-client-id" in quality["detail"]


def test_zhilian_doctor_uses_platform_specific_cookie_and_network_messages(tmp_path):
	code, parsed = _invoke_doctor(tmp_path, platform="zhilian", token=None, cookie=None, http_status=200)
	ce = _find_check(parsed["data"]["checks"], "cookie_extract")
	assert ce is not None
	assert "zhaopin" in ce["detail"]
	assert "boss --platform zhilian login" in ce["hint"]
	network = _find_check(parsed["data"]["checks"], "network")
	assert network is not None
	assert network["detail"] == "访问 zhaopin.com 返回 HTTP 200"


def test_doctor_login_preflight_blocks_missing_auth_before_platform_requests(tmp_path):
	code, parsed = _invoke_doctor(tmp_path, token=None)
	preflight = _find_check(parsed["data"]["checks"], "login_preflight")
	assert preflight is not None
	assert preflight["status"] == "error"
	assert "禁止发起需要认证的平台请求" in preflight["detail"]
	assert "boss login" in preflight["recovery_action"]


def test_doctor_login_preflight_warns_on_partial_auth(tmp_path):
	token = {"cookies": {"wt2": "tok"}}
	code, parsed = _invoke_doctor(tmp_path, token=token)
	preflight = _find_check(parsed["data"]["checks"], "login_preflight")
	assert preflight is not None
	assert preflight["status"] == "warn"
	assert "只读请求可能失败" in preflight["detail"]
	assert "刷新登录态" in preflight["recovery_action"]


def test_doctor_login_preflight_passes_complete_auth(tmp_path):
	token = {"cookies": {"wt2": "tok"}, "stoken": "st"}
	code, parsed = _invoke_doctor(tmp_path, token=token)
	preflight = _find_check(parsed["data"]["checks"], "login_preflight")
	assert preflight is not None
	assert preflight["status"] == "ok"
	assert "登录前置通过" in preflight["detail"]


def test_doctor_reports_quality_baseline(tmp_path):
	code, parsed = _invoke_doctor(tmp_path)
	assert code == 0
	checks = parsed["data"]["checks"]
	baseline = _find_check(checks, "quality_baseline")
	assert baseline["status"] == "ok"
	assert "scripts/quality_baseline.py" in baseline["detail"]
	for tool in ("ruff", "pytest", "mypy"):
		assert _find_check(checks, f"quality_tool_{tool}")["status"] in {"ok", "warn"}
	assert any("quality_baseline.py" in action for action in parsed["hints"]["next_actions"])


def test_windows_playwright_cache_dir_uses_localappdata(tmp_path, monkeypatch):
	"""Windows 默认 ms-playwright 缓存应纳入 doctor 检测。"""
	from boss_agent_cli.commands.doctor import _patchright_browser_cache_dirs

	local_app_data = tmp_path / "AppData" / "Local"
	monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))

	dirs = _patchright_browser_cache_dirs()

	assert local_app_data / "ms-playwright" in dirs


def test_resolve_quality_tool_uses_uv_run_when_tool_missing(monkeypatch):
	"""全局 tool 环境 PATH 缺质量工具时，uv run 可用不应误报缺失。"""
	from boss_agent_cli.commands.doctor_checks import _resolve_quality_tool

	def fake_which(name):
		return "/usr/bin/uv" if name == "uv" else None

	monkeypatch.setattr("boss_agent_cli.commands.doctor_checks.shutil.which", fake_which)

	status, detail, hint = _resolve_quality_tool("pytest")

	assert status == "ok"
	assert "uv run pytest" in detail
	assert "uv run pytest" in hint


def test_evaluate_patchright_chromium_requires_exact_revision(tmp_path):
	"""回归：缓存里有其他修订版不代表 patchright 可用，必须精确匹配所需修订版。"""
	from boss_agent_cli.commands.doctor import _evaluate_patchright_chromium

	installed = [
		tmp_path / "chromium-1217",
		tmp_path / "chromium-1223",
		tmp_path / "chromium_headless_shell-1217",
	]
	status, detail = _evaluate_patchright_chromium("1208", installed)
	assert status == "warn"
	assert "chromium-1208" in detail
	assert "boss login" in detail

	status_ok, detail_ok = _evaluate_patchright_chromium("1217", installed)
	assert status_ok == "ok"
	assert "chromium-1217" in detail_ok


def test_evaluate_patchright_chromium_reports_missing_headless_shell(tmp_path):
	"""Windows/global tool 环境缺 headless shell 时应给出精确恢复命令。"""
	from boss_agent_cli.commands.doctor import _evaluate_patchright_chromium

	status, detail = _evaluate_patchright_chromium("1223", [tmp_path / "chromium-1223"])

	assert status == "warn"
	assert "chromium_headless_shell-1223" in detail
	assert "patchright install chromium-headless-shell" in detail


def test_evaluate_patchright_chromium_falls_back_without_manifest(tmp_path):
	"""browsers.json 不可读时退回旧的目录计数口径，不阻断 doctor。"""
	from boss_agent_cli.commands.doctor import _evaluate_patchright_chromium

	status, detail = _evaluate_patchright_chromium(None, [tmp_path / "chromium-1217"])
	assert status == "ok"
	assert "无法确认" in detail

	status_empty, _ = _evaluate_patchright_chromium(None, [])
	assert status_empty == "warn"


def test_patchright_chromium_revision_reads_packaged_manifest():
	"""patchright 是项目硬依赖，应能从其 browsers.json 读出数字修订版。"""
	from boss_agent_cli.commands.doctor import _patchright_chromium_revision

	revision = _patchright_chromium_revision()
	assert revision is not None
	assert revision.isdigit()
