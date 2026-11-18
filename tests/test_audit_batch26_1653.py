"""批次二十六回归测试：Issue #1653–#1659。

全部测试离线运行；不调用平台 API、浏览器或真实网络。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.api.browser_client import BrowserSession, _cdp_http_endpoint
from boss_agent_cli.api.zhipin_errors import is_security_verification_response
from boss_agent_cli.automation.config import AutomationConfig
from boss_agent_cli.automation.models import (
	ActionResult,
	Conversation,
	ConversationFingerprint,
	ConversationRef,
	EventStatus,
)
from boss_agent_cli.automation.runner import run_automation_cycle
from boss_agent_cli.automation.storage import AutomationStore
from boss_agent_cli.crawler.exporter import write_run_outputs
from boss_agent_cli.main import cli
from boss_agent_cli.resume.export import export_pdf
from boss_agent_cli.resume.models import PersonalInfoItem, PersonalInfoSection, ResumeData
from boss_agent_cli.services.job_export import write_export_file
from boss_agent_cli.services.json_files import strict_json_text

CANDIDATE = "候选人A"
INCOMING = "你好，我在上海做过3年销售，擅长客户沟通，大专，期望10-12k，想看机会"


def test_strict_json_text_normalizes_non_finite_numbers() -> None:
	text = strict_json_text({"ok": float("inf"), "nested": [float("nan"), -float("inf"), 1.5]})
	parsed = json.loads(text)
	assert parsed == {"ok": None, "nested": [None, None, 1.5]}


def test_export_file_json_rejects_non_standard_constants(tmp_path: Path) -> None:
	path = tmp_path / "jobs.json"
	write_export_file([{"salary": float("inf"), "title": "工程师"}], "json", str(path))
	assert json.loads(path.read_text(encoding="utf-8")) == [{"salary": None, "title": "工程师"}]


def test_crawl_outputs_survive_surrogate_and_non_finite_fields(tmp_path: Path) -> None:
	row = {"title": "bad\ud800", "salary": float("inf"), "company": "ok￿"}
	write_run_outputs(tmp_path, [row])
	parsed = json.loads((tmp_path / "jobs.json").read_text(encoding="utf-8"))
	assert "bad" in parsed[0]["title"]
	assert parsed[0]["salary"] is None
	assert "ok" in parsed[0]["company"]
	assert (tmp_path / "jobs.csv").exists()
	assert (tmp_path / "jobs.xlsx").exists()


@patch("boss_agent_cli.commands.export.get_platform_instance")
@patch("boss_agent_cli.commands.export.AuthManager")
def test_export_deduplicates_jobs_across_pages(mock_auth_cls, mock_client_cls) -> None:
	instance = mock_client_cls.return_value
	instance.__enter__ = lambda self: self
	instance.__exit__ = lambda self, *a: None
	instance.is_success.side_effect = lambda response: response.get("code", 0) == 0
	instance.unwrap_data.side_effect = lambda response: response.get("zpData") or {}

	def search_jobs(query, **filters):
		page = filters["page"]

		def job(job_id):
			return {"encryptJobId": job_id, "jobName": f"职位 {job_id}", "securityId": f"sec-{job_id}"}

		jobs = [job("j1"), job("j2")] if page == 1 else [job("j2"), job("j3")]
		return {"code": 0, "zpData": {"jobList": jobs, "hasMore": page < 2}}

	instance.search_jobs.side_effect = search_jobs
	result = CliRunner().invoke(
		cli, ["--data-dir", str(Path("/tmp/boss-batch26-audit")), "--json", "export", "golang", "--count", "3"]
	)
	assert result.exit_code == 0, result.output
	data = json.loads(result.output)["data"]
	assert data["count"] == 3
	assert [item["title"] for item in data["jobs"]] == ["职位 j1", "职位 j2", "职位 j3"]


def test_security_page_requires_platform_host() -> None:
	url = "https://example.com/web/passport/zp/verify.html"
	assert is_security_verification_response(url=url) is False
	assert is_security_verification_response(url="https://zhipin.com/web/passport/zp/verify.html") is True
	assert is_security_verification_response(url="https://sub.zhipin.com/web/passport/zp/security.html") is True
	assert is_security_verification_response(url="/web/passport/zp/verify.html") is True


def test_stoken_cookie_rejects_foreign_domain() -> None:
	import boss_agent_cli.auth.browser as auth_browser

	ctx = MagicMock()
	ctx.cookies.return_value = [
		{"name": "__zp_stoken__", "value": "foreign", "domain": ".example.com"},
		{"name": "__zp_stoken__", "value": "real", "domain": ".zhipin.com"},
	]
	ctx.pages = []
	browser = MagicMock()
	browser.contexts = [ctx]
	playwright = MagicMock()
	playwright.chromium.connect_over_cdp.return_value = browser
	launcher = MagicMock()
	launcher.start.return_value = playwright
	with (
		patch.object(auth_browser, "probe_cdp", return_value="ws://localhost/cdp"),
		patch.object(auth_browser, "sync_playwright", return_value=launcher),
		patch.object(auth_browser, "_warm_home_for_runtime", return_value=False),
	):
		assert auth_browser.refresh_stoken_via_cdp() == "real"


@pytest.mark.parametrize(
	("endpoint", "expected"),
	[
		("ws://127.0.0.1:9333/devtools/browser/abc", "http://127.0.0.1:9333"),
		("wss://remote.example/devtools/browser/abc", "https://remote.example"),
		("http://localhost:9222", "http://localhost:9222"),
		("file:///tmp/cdp", None),
	],
)
def test_cdp_http_endpoint(endpoint: str, expected: str | None) -> None:
	assert _cdp_http_endpoint(endpoint) == expected


def test_evaluate_js_reuses_discovered_cdp_endpoint() -> None:
	session = BrowserSession(cookies={"wt2": "abc"}, user_agent="UA", delay=(0, 0))
	session._connected_cdp_http_url = "http://127.0.0.1:9333"
	with patch("boss_agent_cli.api.browser_client._cdp_evaluate_in_chat_tab", return_value="ok") as evaluate:
		assert session.evaluate_js("1+1") == "ok"
	assert evaluate.call_args.args[0] == "http://127.0.0.1:9333"


class _AutomationAdapter:
	def __init__(self, *, unconfirmed: bool = False) -> None:
		self.unconfirmed = unconfirmed
		self.sent = 0
		conversation = Conversation(
			title=CANDIDATE,
			incoming_messages=(INCOMING,),
			ordered_messages=(("incoming", INCOMING),),
			all_messages=(INCOMING,),
			fingerprint=ConversationFingerprint(CANDIDATE),
		)
		self.ref = ConversationRef(id=CANDIDATE, tab="chat", conversation=conversation)

	def scan_conversations(self, tabs, max_per_tab):
		return [self.ref]

	def read_conversation(self, ref):
		return ref.conversation

	def ensure_session(self):
		return None

	def execute_action(self, action, message, ref):
		self.sent += 1
		if self.unconfirmed:
			return ActionResult(status="unconfirmed", details={"reason": "manual verification"})
		return ActionResult(status="executed", details={})

	def detect_safety_warning(self):
		return None

	def health_check(self):
		return SimpleNamespace(status="healthy", checks=())


def _read_state(store: AutomationStore) -> dict:
	return json.loads((store.root / "state.json").read_text(encoding="utf-8"))


def test_scan_path_unconfirmed_marks_notified_on_first_run(tmp_path: Path) -> None:
	store = AutomationStore(tmp_path)
	adapter = _AutomationAdapter(unconfirmed=True)
	report = run_automation_cycle(adapter, store, AutomationConfig(), platform="zhipin", dry_run=False, limit=1)
	assert report.status == EventStatus.CIRCUIT_BREAKER_OPEN.value
	marker = _read_state(store)["conversations"][CANDIDATE]["inflight_action"]
	assert marker["status"] == "verification-required"
	assert marker["notified"] is True

	resume_store = AutomationStore(tmp_path)
	resume_adapter = _AutomationAdapter()
	resume = run_automation_cycle(
		resume_adapter, resume_store, AutomationConfig(), platform="zhipin", dry_run=False, limit=1, resume=True
	)
	assert resume_adapter.sent == 0
	assert resume.status == "OK"
	assert resume.events[-1].status == EventStatus.SKIPPED.value


def test_pdf_render_blocks_external_requests(tmp_path: Path) -> None:
	resume = ResumeData(
		name="测试",
		title="简历",
		center_title=False,
		personal_info=PersonalInfoSection(
			items=[PersonalInfoItem(label="主页", value="example", link="javascript:alert(1)")], layout="inline"
		),
		job_intention=None,
		modules=[],
		avatar="https://example.com/avatar.png",
	)
	page = MagicMock()
	browser = MagicMock()
	browser.new_page.return_value = page
	chromium = MagicMock()
	chromium.launch.return_value = browser
	playwright = MagicMock()
	playwright.chromium = chromium
	context = MagicMock()
	context.__enter__ = MagicMock(return_value=playwright)
	context.__exit__ = MagicMock(return_value=False)
	with patch("patchright.sync_api.sync_playwright", return_value=context):
		export_pdf(resume, tmp_path / "resume.pdf")
	route_callback = page.route.call_args.args[1]
	route = MagicMock()
	route.request.url = "https://example.com/avatar.png"
	route_callback(route)
	route.abort.assert_called_once()
	route.reset_mock()
	route.request.url = "data:image/png;base64,abc"
	route_callback(route)
	route.continue_.assert_called_once()
	assert "<img" not in page.set_content.call_args.args[0]
	assert "javascript:" not in page.set_content.call_args.args[0]
