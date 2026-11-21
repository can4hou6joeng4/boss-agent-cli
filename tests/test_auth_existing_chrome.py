import json
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

from click.testing import CliRunner
from patchright.sync_api import Error as PlaywrightError
import pytest

from boss_agent_cli.api import cdp_risk_lock, endpoints
from boss_agent_cli.api.chrome_devtools import read_chrome_devtools_endpoint
from boss_agent_cli.auth import browser as auth_browser
from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.main import cli


_BOSS_URL = "https://www.zhipin.com/web/geek/jobs"
_ENDPOINT = "ws://127.0.0.1:9222/devtools/browser/approved-id"
_RISK_URL = "https://www.zhipin.com/web/passport/zp/security.html"


@pytest.mark.parametrize(("lines", "expected"), [
	("9312\r\n/devtools/browser/example-id\r\n", "ws://127.0.0.1:9312/devtools/browser/example-id"),
	("9222\n/devtools/browser\n", "ws://127.0.0.1:9222/devtools/browser"),
	("65536\n/devtools/browser/x\n", None),
	("9222@elsewhere\n/devtools/browser/x\n", None),
	("9222\n//elsewhere/devtools/browser/x\n", None),
	("9222\n/devtools/browser/x?url=secret\n", None),
	("9222\n", None),
])
def test_chrome_endpoint_uses_only_the_local_discovery_file(tmp_path, lines, expected):
	(tmp_path / "DevToolsActivePort").write_bytes(lines.encode("ascii"))
	assert read_chrome_devtools_endpoint(tmp_path) == expected


@pytest.fixture
def chrome(monkeypatch):
	page = Mock()
	page.url = _BOSS_URL
	page.is_closed.return_value = False
	page.evaluate.return_value = "test UA"
	context = Mock()
	context.pages = [page]
	context.cookies.return_value = [
		{"domain": ".zhipin.com", "name": "wt2", "value": "fake-wt2"},
		{"domain": "www.zhipin.com", "name": "__zp_stoken__", "value": "fake-stoken"},
		{"domain": "unrelated.example", "name": "unrelated", "value": "never-import"},
	]
	page.context = context
	browser = Mock()
	browser.contexts = [context]
	playwright = Mock()
	playwright.chromium.connect_over_cdp.return_value = browser

	driver = MagicMock()
	driver.__enter__.return_value = playwright
	monkeypatch.setattr(auth_browser, "sync_playwright", Mock(return_value=driver))
	monkeypatch.setattr(auth_browser, "read_chrome_devtools_endpoint", Mock(return_value=_ENDPOINT))
	monkeypatch.setattr("boss_agent_cli.auth.manager.login_via_cdp", Mock(side_effect=AssertionError("CDP fallback")))
	monkeypatch.setattr("boss_agent_cli.auth.manager.login_via_browser", Mock(side_effect=AssertionError("QR fallback")))
	monkeypatch.setattr("boss_agent_cli.auth.manager.extract_cookies", Mock(side_effect=AssertionError("disk fallback")))

	def respond(*, url=endpoints.USER_INFO_URL, status=200, payload=None, method="GET"):
		response = Mock()
		response.url = url
		response.status = status
		response.headers = {"content-type": "application/json"}
		response.frame = page.main_frame
		response.request.method = method
		response.json.return_value = {"code": 0, "zpData": {"name": "fixture"}} if payload is None else payload
		callback = next(call.args[1] for call in page.on.call_args_list if call.args[0] == "response")
		callback(response)
		return response

	page.wait_for_timeout.side_effect = lambda _: respond()
	return SimpleNamespace(page=page, context=context, browser=browser, playwright=playwright, driver=driver, respond=respond)


def run_login(tmp_path, *options):
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "login", "--existing-chrome", *options])
	return result, json.loads(result.stdout)


def test_login_imports_verified_full_session_and_keeps_the_browser_open(tmp_path, chrome):
	result, body = run_login(tmp_path, "--timeout", "17")
	assert result.exit_code == 0, result.output
	assert body["ok"] and "常用 Chrome" in body["data"]["message"]
	assert "fake-wt2" not in result.output and "fake-stoken" not in result.output
	chrome.playwright.chromium.connect_over_cdp.assert_called_once_with(_ENDPOINT, no_defaults=True, timeout=17000)
	chrome.page.evaluate.assert_called_once_with("navigator.userAgent")
	chrome.context.cookies.assert_called_once_with([_BOSS_URL, endpoints.USER_INFO_URL])
	chrome.browser.close.assert_not_called()
	chrome.browser.new_context.assert_not_called()
	chrome.context.new_page.assert_not_called()
	chrome.context.clear_cookies.assert_not_called()
	chrome.page.close.assert_not_called()
	chrome.page.goto.assert_not_called()
	chrome.driver.__exit__.assert_called_once()
	token = AuthManager(tmp_path).get_token()
	assert token["cookies"] == {"wt2": "fake-wt2", "__zp_stoken__": "fake-stoken"}
	assert token["stoken"] == "fake-stoken"


def test_login_connector_denial_does_not_leak_endpoint_or_fall_back(tmp_path, chrome):
	chrome.playwright.chromium.connect_over_cdp.side_effect = PlaywrightError(f"Access denied: {_ENDPOINT}")
	result, body = run_login(tmp_path)
	assert result.exit_code == 1
	assert body["error"]["code"] == "CDP_UNAVAILABLE"
	assert _ENDPOINT not in result.output
	chrome.context.cookies.assert_not_called()
	chrome.driver.__exit__.assert_called_once()


@pytest.mark.parametrize("failure", ["missing", "multiple", "wrong_origin"])
def test_no_ambiguous_or_non_boss_tab_is_used(tmp_path, chrome, failure):
	if failure == "missing":
		chrome.context.pages = []
	elif failure == "multiple":
		chrome.context.pages.append(chrome.page)
	else:
		chrome.page.url = "https://www.zhipin.com.evil.invalid/web/geek/jobs"
	result, body = run_login(tmp_path)
	assert result.exit_code == 1
	assert body["error"]["code"] == "BROWSER_SESSION_NOT_FOUND"
	chrome.page.evaluate.assert_not_called()
	chrome.context.cookies.assert_not_called()


@pytest.mark.parametrize(("payload", "http_status", "code"), [
	({"code": 5}, 200, "AUTH_REQUIRED"),
	({"code": 0, "zpData": {"name": "fixture"}}, 500, "AUTH_REQUIRED"),
	({"code": 0, "zpData": {}}, 200, "AUTH_REQUIRED"),
	({"code": -1}, 429, "RATE_LIMITED"),
])
def test_failed_native_response_preserves_existing_session(tmp_path, chrome, payload, http_status, code):
	AuthManager(tmp_path)._store.save({"cookies": {"wt2": "old"}, "stoken": "old-stoken"})

	def observe(_):
		chrome.respond(status=http_status, payload=payload)
		time.sleep(0.01)

	chrome.page.wait_for_timeout.side_effect = observe
	result, body = run_login(tmp_path, "--timeout", "1")
	assert result.exit_code == 1
	assert body["error"]["code"] == code
	if code == "AUTH_REQUIRED":
		assert body["error"]["details"] == {"http_status": http_status, "platform_code": payload["code"]}
	assert AuthManager(tmp_path).get_token()["cookies"] == {"wt2": "old"}
	chrome.context.cookies.assert_not_called()


def test_import_does_not_replace_the_existing_session_with_a_partial_one(tmp_path, chrome):
	AuthManager(tmp_path)._store.save({"cookies": {"wt2": "old"}, "stoken": "old-stoken"})
	chrome.context.cookies.return_value = chrome.context.cookies.return_value[:1]
	chrome.page.evaluate.side_effect = ["test UA", "", ""]
	result, body = run_login(tmp_path)
	assert result.exit_code == 1
	assert body["error"]["code"] == "LOGIN_CREDENTIAL_EXTRACTION_FAILED"
	assert AuthManager(tmp_path).get_token()["cookies"] == {"wt2": "old"}



def test_navigation_away_from_boss_during_probe_stops_before_reading_cookies(tmp_path, chrome):
	def observe(_):
		chrome.page.url = "https://unrelated.example/"

	chrome.page.wait_for_timeout.side_effect = observe
	result, body = run_login(tmp_path)
	assert result.exit_code == 1
	assert body["error"]["code"] == "BROWSER_SESSION_NOT_FOUND"
	chrome.context.cookies.assert_not_called()


def test_login_security_response_stops_and_leaves_the_tab_open(tmp_path, chrome):
	chrome.page.wait_for_timeout.side_effect = lambda _: chrome.respond(url=_RISK_URL)
	result, body = run_login(tmp_path)
	assert result.exit_code == 1
	assert body["error"]["code"] == "ACCOUNT_RISK"
	assert body["error"]["details"]["login_diagnostic"]["origin"] == "response"
	assert cdp_risk_lock.read_lock(tmp_path).reason == "ACCOUNT_RISK"
	assert not (tmp_path / "auth" / "session.enc").exists()
	chrome.context.cookies.assert_not_called()
	chrome.page.close.assert_not_called()
	chrome.browser.close.assert_not_called()


def test_existing_risk_lock_stops_before_browser_discovery(tmp_path, chrome):
	cdp_risk_lock.write_lock(tmp_path, None, reason="ACCOUNT_RISK")
	result, body = run_login(tmp_path, "--ignore-risk-lock")
	assert result.exit_code == 1
	assert body["error"]["details"]["risk_lock"]["locked"] is True
	auth_browser.read_chrome_devtools_endpoint.assert_not_called()
	chrome.playwright.chromium.connect_over_cdp.assert_not_called()


def test_lock_created_during_chrome_approval_blocks_the_probe(tmp_path, chrome):
	def connect(*args, **kwargs):
		cdp_risk_lock.write_lock(tmp_path, None, reason="ACCOUNT_RISK", now=1234)
		return chrome.browser

	chrome.playwright.chromium.connect_over_cdp.side_effect = connect
	result, body = run_login(tmp_path)
	assert result.exit_code == 1
	assert body["error"]["code"] == "ACCOUNT_RISK"
	assert cdp_risk_lock.read_lock(tmp_path).locked_at == 1234
	assert "login_diagnostic" not in body["error"]["details"]
	chrome.page.evaluate.assert_not_called()
	chrome.context.cookies.assert_not_called()


@pytest.mark.parametrize("conflict", [["--force"], ["--cookie-source", "chrome"], ["--cdp"], ["--curl-file", "-"]])
def test_conflicting_login_options_are_rejected_before_connecting(tmp_path, chrome, conflict):
	result, body = run_login(tmp_path, *conflict)
	assert result.exit_code == 1
	assert body["error"]["code"] == "INVALID_PARAM"
	chrome.playwright.chromium.connect_over_cdp.assert_not_called()
