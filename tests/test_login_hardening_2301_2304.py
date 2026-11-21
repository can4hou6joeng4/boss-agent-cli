"""登录收尾复查、探针页清理与超时上界的离线回归（Issue #2301–#2304）。

两条登录路径在最后一次风控检查之后还会 evaluate / 等待页面事件；这期间页面可能
被重定向到安全验证页，监听器也可能刚锁存风控响应。返回成功凭证前必须复查，
否则 ``AuthManager`` 会把被风控的会话当「登录成功」落盘。其余三条分别覆盖
探针页误关用户页签、--timeout 无上界与 stoken 提取失败不回退 cookie jar。
全部离线：Playwright、CDP 与页面都是 MagicMock。
"""

import json
from unittest.mock import MagicMock, PropertyMock, patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.api.client import AccountRiskError
from boss_agent_cli.auth import browser as browser_auth
from boss_agent_cli.auth.browser import HOME_URL, LOGIN_PAGE_URL, _NAV_TIMEOUT_MS, login_via_browser, login_via_cdp
from boss_agent_cli.auth.manager import MAX_LOGIN_TIMEOUT_SECONDS, AuthManager
from boss_agent_cli.main import cli

_SECURITY_URL = "https://www.zhipin.com/web/passport/zp/security.html"
_PROFILE_URL = "https://www.zhipin.com/web/geek/job"


def _mock_playwright_context(browser: MagicMock) -> MagicMock:
	chromium = MagicMock()
	chromium.launch.return_value = browser
	playwright = MagicMock()
	playwright.chromium = chromium
	manager = MagicMock()
	manager.__enter__ = MagicMock(return_value=playwright)
	manager.__exit__ = MagicMock(return_value=False)
	return manager


def _mock_cdp_playwright(context: MagicMock) -> tuple[MagicMock, MagicMock, MagicMock]:
	page = MagicMock()
	context.new_page.return_value = page
	browser = MagicMock()
	browser.contexts = [context]
	playwright = MagicMock()
	playwright.chromium.connect_over_cdp.return_value = browser
	launcher = MagicMock()
	launcher.start.return_value = playwright
	return launcher, playwright, page


# ── #2301：迟到风控 / 安全页 ───────────────────────────────────────


@patch("boss_agent_cli.auth.browser.time.sleep", return_value=None)
def test_login_via_browser_rechecks_late_risk_after_stoken_extraction(mock_sleep):
	"""stoken 的 evaluate 驱动事件循环后锁存的风控响应不能被返回成功凭证跳过。"""

	class _RiskResponse:
		url = "https://www.zhipin.com/wapi/zpgeek/job/list.json"
		status = 200
		headers = {"content-type": "application/json"}

		def json(self):
			return {"code": 36, "message": "检测到异常行为"}

	page = MagicMock()
	page.goto.return_value = None
	page.wait_for_load_state.return_value = None
	page.evaluate.return_value = "UA"

	context = MagicMock()
	context.new_page.return_value = page
	context.cookies.return_value = [
		{"name": "wt2", "value": "token", "domain": ".zhipin.com"},
		{"name": "__zp_stoken__", "value": "jar-stoken", "domain": ".zhipin.com"},
	]
	browser = MagicMock()
	browser.new_context.return_value = context

	def _extract_with_late_risk(_page):
		# 监听器在 evaluate 期间收到一条 403 以外的 /wapi/ 风控响应（code 36）。
		handler = next(call.args[1] for call in page.on.call_args_list if call.args and call.args[0] == "response")
		handler(_RiskResponse())
		return "page-stoken"

	with (
		patch("boss_agent_cli.auth.browser.sync_playwright", return_value=_mock_playwright_context(browser)),
		patch("boss_agent_cli.auth.browser._extract_stoken", side_effect=_extract_with_late_risk) as extract,
	):
		with pytest.raises(AccountRiskError):
			login_via_browser(timeout=2, platform="zhipin")

	extract.assert_called_once_with(page)
	browser.close.assert_called_once()


@patch("boss_agent_cli.auth.browser.probe_cdp", return_value="ws://localhost/devtools/browser")
@patch("boss_agent_cli.auth.browser.time.sleep", return_value=None)
def test_login_via_cdp_rechecks_security_page_before_returning_credentials(mock_sleep, mock_probe_cdp):
	"""身份头采集期间主页面被重定向到安全页：返回凭证前的复查必须拦住。"""
	context = MagicMock()
	existing = MagicMock()
	context.pages = [existing]
	context.cookies.return_value = [
		{"name": "wt2", "value": "token", "domain": ".zhipin.com"},
		{"name": "__zp_stoken__", "value": "jar-stoken", "domain": ".zhipin.com"},
	]
	browser = MagicMock()
	browser.contexts = [context]
	launcher = MagicMock()
	launcher.start.return_value.chromium.connect_over_cdp.return_value = browser

	state = {"flipped": False}
	type(existing).url = PropertyMock(side_effect=lambda: _SECURITY_URL if state["flipped"] else _PROFILE_URL)

	def _evaluate(script, arg=None):
		return {"code": 0, "zpData": {}} if "fetch(" in script else "UA"

	existing.evaluate.side_effect = _evaluate

	def _capture_flips_url(ctx, home_url, *, existing=None):
		state["flipped"] = True
		return {}

	with (
		patch("boss_agent_cli.auth.browser.sync_playwright", return_value=launcher),
		patch("boss_agent_cli.auth.browser._capture_identity_from_probe_page", side_effect=_capture_flips_url),
	):
		with pytest.raises(AccountRiskError) as caught:
			login_via_cdp(timeout=1)

	assert caught.value.details.get("risk_source") == "security_page"
	launcher.start.return_value.stop.assert_called_once()


def test_probe_reused_session_rechecks_security_page_when_evaluate_fails():
	"""evaluate 失败也可能是页面刚跳到安全页：先复查，再按 unverified 收口。"""
	page = MagicMock()
	page.url = _SECURITY_URL
	page.evaluate.side_effect = RuntimeError("Execution context was destroyed")

	with pytest.raises(AccountRiskError):
		browser_auth._probe_reused_session(page, platform="zhipin")


def test_probe_reused_session_keeps_unverified_for_plain_evaluate_failure():
	"""非风控的 evaluate 失败仍按 unverified 处理，不误报风控。"""
	page = MagicMock()
	page.url = _PROFILE_URL
	page.evaluate.side_effect = RuntimeError("target closed")

	assert browser_auth._probe_reused_session(page, platform="zhipin") == "unverified"


# ── #2302：探针页防御分支不得关闭用户页签 ──────────────────────────


def test_identity_probe_does_not_close_existing_page():
	page = MagicMock()
	context = MagicMock()
	context.new_page.return_value = page

	assert browser_auth._capture_identity_from_probe_page(context, HOME_URL, existing=page) == {}
	page.close.assert_not_called()


# ── #2303：--timeout 上界 ─────────────────────────────────────────


@pytest.mark.parametrize(
	"value",
	[str(MAX_LOGIN_TIMEOUT_SECONDS + 1), "9" * 400],
	ids=["over-limit", "arbitrary-precision"],
)
def test_login_timeout_over_limit_is_rejected_by_cli(value):
	runner = CliRunner()
	result = runner.invoke(cli, ["login", "--timeout", value])

	assert result.exit_code == 1, result.output
	body = json.loads(result.stdout)
	assert body["ok"] is False
	assert body["error"]["code"] == "INVALID_PARAM"
	assert body["error"]["recoverable"] is False


@patch("boss_agent_cli.commands.login.AuthManager")
def test_login_timeout_at_upper_bound_is_accepted(mock_auth_cls):
	mock_auth = MagicMock()
	mock_auth.login.return_value = {"cookies": {"wt2": "x"}, "stoken": "s", "_method": "Cookie 提取"}
	mock_auth_cls.return_value = mock_auth

	result = CliRunner().invoke(cli, ["login", "--timeout", str(MAX_LOGIN_TIMEOUT_SECONDS)])

	assert result.exit_code == 0, result.output
	assert mock_auth.login.call_args.kwargs["timeout"] == MAX_LOGIN_TIMEOUT_SECONDS


@pytest.mark.parametrize("timeout", [0, -1, MAX_LOGIN_TIMEOUT_SECONDS + 1, 10**400, True], ids=["zero", "negative", "over-limit", "huge", "bool"])
def test_auth_manager_rejects_out_of_range_timeout_before_login(tmp_path, timeout):
	"""Python API 入口同样拒绝：不能等 time.time() + timeout 抛 OverflowError 再报网络错误。"""
	with (
		patch("boss_agent_cli.auth.manager.login_via_browser") as via_browser,
		patch("boss_agent_cli.auth.manager.extract_cookies") as extract,
		patch("boss_agent_cli.auth.manager.probe_cdp") as probe,
	):
		with pytest.raises(ValueError, match="timeout"):
			AuthManager(tmp_path).login(timeout=timeout)

	via_browser.assert_not_called()
	extract.assert_not_called()
	probe.assert_not_called()


# ── #2304：stoken 提取失败回退 cookie jar ─────────────────────────


@patch("boss_agent_cli.auth.browser.time.sleep", return_value=None)
def test_login_via_browser_falls_back_to_cookie_jar_when_stoken_extract_fails(mock_sleep):
	"""首页已加载但 evaluate 失败（_extract_stoken 返回空串）时不能保存空 stoken。"""
	page = MagicMock()
	page.goto.return_value = None
	page.wait_for_load_state.return_value = None
	page.evaluate.return_value = "UA"

	context = MagicMock()
	context.new_page.return_value = page
	context.cookies.return_value = [
		{"name": "wt2", "value": "token", "domain": ".zhipin.com"},
		{"name": "__zp_stoken__", "value": "jar-stoken", "domain": ".zhipin.com"},
	]
	browser = MagicMock()
	browser.new_context.return_value = context

	with (
		patch("boss_agent_cli.auth.browser.sync_playwright", return_value=_mock_playwright_context(browser)),
		patch("boss_agent_cli.auth.browser._extract_stoken", return_value=""),
	):
		result = login_via_browser(timeout=2, platform="zhipin")

	assert result["stoken"] == "jar-stoken"
	assert result["cookies"]["wt2"] == "token"
	# 登录页 + 首页各一次 goto；提取失败不额外导航
	assert page.goto.call_count == 2
	assert page.goto.call_args_list[1].args[0] == HOME_URL
	assert page.goto.call_args_list[1].kwargs["timeout"] == _NAV_TIMEOUT_MS
	assert page.goto.call_args_list[0].args[0] == LOGIN_PAGE_URL
