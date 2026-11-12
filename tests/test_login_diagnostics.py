import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.api.client import AccountRiskError
from boss_agent_cli.auth.browser import LOGIN_PAGE_URL, login_via_browser, login_via_cdp
from boss_agent_cli.auth.login_diagnostics import login_diagnostic
from boss_agent_cli.main import cli


def _page(count=1):
	page = MagicMock()
	page.url = "https://www.zhipin.com/web/geek/jobs?token=synthetic-sensitive#fragment"
	page.main_frame = SimpleNamespace()
	page.context.pages = [page] + [MagicMock() for _ in range(count - 1)]
	return page


@pytest.mark.parametrize("scope", ["main_frame", "child_frame", "other_page"])
def test_response_diagnostics_use_cached_metadata_without_reading_credentials(scope):
	page = _page(count=2)
	frame = page.main_frame if scope == "main_frame" else SimpleNamespace(page=page if scope == "child_frame" else object())
	response = MagicMock()
	response.status = 200
	response.frame = frame
	response.request.resource_type = "document"
	response.request.is_navigation_request.return_value = True

	diagnostic = login_diagnostic(page, stage="read_user_agent", response=response)

	assert diagnostic == {
		"origin": "response", "stage": "read_user_agent", "top_path": "/web/geek/jobs",
		"open_pages": 2, "status": 200, "resource_type": "document",
		"is_navigation_request": True, "frame_scope": scope,
	}
	assert "synthetic-sensitive" not in json.dumps(diagnostic)
	response.json.assert_not_called()
	response.text.assert_not_called()
	page.evaluate.assert_not_called()
	page.context.cookies.assert_not_called()


@pytest.mark.parametrize("url", [
	"https://www.zhipin.com/private/synthetic-sensitive?token=synthetic-sensitive",
	"https://elsewhere.example/web/geek/jobs?token=synthetic-sensitive",
	"not-a-url/synthetic-sensitive",
	None,
])
def test_unknown_urls_never_appear_in_diagnostics(url):
	page = _page()
	page.url = url
	diagnostic = login_diagnostic(page, stage="open_login")
	assert diagnostic["origin"] == "page_check"
	assert "synthetic-sensitive" not in json.dumps(diagnostic)
	assert "elsewhere.example" not in json.dumps(diagnostic)


def test_unavailable_metadata_does_not_mask_original_risk():
	class Unavailable:
		def __getattr__(self, name):
			raise RuntimeError("synthetic-sensitive")

	diagnostic = login_diagnostic(Unavailable(), stage="synthetic-sensitive", response=Unavailable())
	assert diagnostic == {
		"origin": "response", "stage": "unknown", "top_path": "<unavailable>",
		"resource_type": "unknown", "frame_scope": "unknown",
	}
	assert "synthetic-sensitive" not in json.dumps(diagnostic)


def test_diagnostic_never_reads_response_urls_headers_or_bodies():
	reads = []
	class GuardedRequest:
		resource_type = "fetch"
		def is_navigation_request(self):
			return False
		@property
		def headers(self):
			reads.append("request.headers")
			raise AssertionError("forbidden")
	class GuardedResponse:
		status = 200
		frame = None
		request = GuardedRequest()
		def __getattr__(self, name):
			reads.append(name)
			raise AssertionError("forbidden")
	login_diagnostic(_page(), stage="read_user_agent", response=GuardedResponse())
	assert reads == []


def test_partial_metadata_failure_keeps_independent_fields():
	class PartialResponse:
		status = 403
		request = SimpleNamespace(resource_type="xhr", is_navigation_request=lambda: False)
		@property
		def frame(self):
			raise RuntimeError("synthetic-sensitive")
	diagnostic = login_diagnostic(_page(), stage="wait_for_scan", response=PartialResponse())
	assert diagnostic["status"] == 403
	assert diagnostic["resource_type"] == "xhr"
	assert diagnostic["is_navigation_request"] is False
	assert diagnostic["frame_scope"] == "unknown"
	assert "synthetic-sensitive" not in json.dumps(diagnostic)


def test_invalid_metadata_values_are_omitted_or_marked_unknown():
	response = SimpleNamespace(status=True, frame=None, request=SimpleNamespace(
		resource_type={"token": "synthetic-sensitive"}, is_navigation_request=lambda: "synthetic-sensitive",
	))
	diagnostic = login_diagnostic(_page(), stage="wait_for_scan", response=response)
	assert "status" not in diagnostic
	assert "is_navigation_request" not in diagnostic
	assert diagnostic["resource_type"] == "unknown"
	assert diagnostic["frame_scope"] == "unknown"
	assert "synthetic-sensitive" not in json.dumps(diagnostic)


def test_session_probe_origin_is_not_mislabelled_as_a_page_check():
	assert login_diagnostic(_page(), stage="probe_session")["origin"] == "session_probe"


def test_risk_response_keeps_event_time_stage_when_top_page_is_already_normal(capsys):
	page = _page()
	page.url = LOGIN_PAGE_URL
	context = page.context
	context.new_page.return_value = page
	context.cookies.return_value = [{"name": "wt2", "value": "synthetic-cookie", "domain": ".zhipin.com"}]
	browser = MagicMock()
	browser.new_context.return_value = context
	runtime = MagicMock()
	runtime.__enter__.return_value.chromium.launch.return_value = browser
	handlers = {}
	page.on.side_effect = lambda event, callback: handlers.__setitem__(event, callback)
	response = MagicMock()
	response.url = "https://www.zhipin.com/web/passport/zp/security.html?token=synthetic-sensitive"
	response.status = 200
	response.headers = {"content-type": "text/html"}
	response.frame = page.main_frame
	response.request.resource_type = "document"
	response.request.is_navigation_request.return_value = True

	def read_user_agent(_script):
		page.url = "https://www.zhipin.com/web/geek/jobs?token=synthetic-sensitive"
		handlers["response"](response)
		return "synthetic-UA"

	page.evaluate.side_effect = read_user_agent
	with patch("boss_agent_cli.auth.browser.sync_playwright", return_value=runtime), \
		patch("boss_agent_cli.auth.browser.time.sleep"):
		with pytest.raises(AccountRiskError) as caught:
			login_via_browser(timeout=1)

	assert caught.value.details["path"] == "/web/passport/zp/security.html"
	assert caught.value.details["risk_source"] == "security_page"
	assert caught.value.details["login_diagnostic"] == {
		"origin": "response", "stage": "read_user_agent", "top_path": "/web/geek/jobs",
		"open_pages": 1, "status": 200, "resource_type": "document",
		"is_navigation_request": True, "frame_scope": "main_frame",
	}
	page.goto.assert_called_once()
	context.cookies.assert_called_once()
	browser.close.assert_called_once()
	assert capsys.readouterr().out == ""
	assert "synthetic-sensitive" not in json.dumps(caught.value.details)


def test_cdp_page_check_adds_diagnostics_without_changing_cleanup():
	page = _page()
	page.url = LOGIN_PAGE_URL
	context = page.context
	context.pages = []
	context.new_page.side_effect = lambda: (context.pages.append(page), page)[1]
	context.cookies.side_effect = [[], [{"name": "wt2", "value": "synthetic-cookie", "domain": ".zhipin.com"}]]
	browser = MagicMock()
	browser.contexts = [context]
	runtime = MagicMock()
	runtime.start.return_value.chromium.connect_over_cdp.return_value = browser

	def read_user_agent(_script):
		page.url = "https://www.zhipin.com/web/passport/zp/security.html?token=synthetic-sensitive"
		return "synthetic-UA"

	page.evaluate.side_effect = read_user_agent
	with patch("boss_agent_cli.auth.browser.sync_playwright", return_value=runtime), \
		patch("boss_agent_cli.auth.browser.probe_cdp", return_value="ws://localhost/devtools/browser"), \
		patch("boss_agent_cli.auth.browser.time.sleep"):
		with pytest.raises(AccountRiskError) as caught:
			login_via_cdp(timeout=1)

	assert caught.value.is_cdp is True
	assert caught.value.details["login_diagnostic"] == {
		"origin": "page_check", "stage": "read_user_agent",
		"top_path": "/web/passport/zp/security.html", "open_pages": 1,
	}
	page.goto.assert_called_once()
	page.close.assert_called_once()
	runtime.start.return_value.stop.assert_called_once()
	browser.close.assert_not_called()


def test_login_error_envelope_preserves_diagnostic_and_risk_lock(tmp_path):
	diagnostic = login_diagnostic(_page(), stage="post_scan_wait")
	error = AccountRiskError(details={
		"risk_source": "security_page", "login_diagnostic": diagnostic,
		"risk_lock": {"locked": True, "persisted": True, "explicit_resume_required": True},
	})
	with patch("boss_agent_cli.auth.manager.AuthManager.login", side_effect=error):
		result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "login", "--force"])
	assert result.exit_code == 1
	envelope = json.loads(result.stdout)
	assert set(envelope) == {"ok", "schema_version", "command", "data", "pagination", "error", "hints"}
	assert envelope["error"]["code"] == "ACCOUNT_RISK"
	assert envelope["error"]["recoverable"] is False
	assert envelope["error"]["details"]["login_diagnostic"] == diagnostic
	assert envelope["error"]["details"]["risk_lock"]["explicit_resume_required"] is True
