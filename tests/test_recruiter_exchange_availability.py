"""换电话/换微信按钮未解锁时的预检：不调用 handleExChange，返回 EXCHANGE_NOT_AVAILABLE。"""
import json
import shutil
import subprocess
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.api import recruiter_client as rc
from boss_agent_cli.api.recruiter_client import BossRecruiterClient
from boss_agent_cli.api.recruiter_resume import history_self_message_state
from boss_agent_cli.main import cli
from boss_agent_cli.platforms.zhipin_recruiter import BossRecruiterPlatform
from boss_agent_cli.schema.error_codes import ERROR_CODES
from boss_agent_cli.wizard.actions import _classify_action_error

FRIEND_ID = 1
SELF_UID = 66826625
FRIEND_DETAIL = {
	"code": 0,
	"zpData": {"friendList": [{"uid": FRIEND_ID, "encryptUid": "u", "encryptJobId": "j", "securityId": "s", "name": "Tester", "friendSource": 0}]},
}


def _msg(mid, sender):
	return {"mid": mid, "time": mid, "from": {"uid": sender, "source": 0}, "body": {"type": 1, "text": "你好"}}


def _history(*messages, **extra):
	return {"code": 0, "zpData": {"messages": list(messages), **extra}}


CANDIDATE_ONLY = _history(_msg(100, FRIEND_ID))
WITH_SELF = _history(_msg(100, FRIEND_ID), _msg(101, SELF_UID))


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
	monkeypatch.setattr(rc.time, "sleep", lambda _s: None)


def _client(page, history=CANDIDATE_ONLY, events=()):
	client = BossRecruiterClient(MagicMock())
	client.friend_detail = MagicMock(return_value=FRIEND_DETAIL)
	client.chat_history = MagicMock(return_value=history)
	browser = MagicMock()
	browser.evaluate_js_with_chat_events.return_value = {"value": page, "events": list(events)}
	client._get_browser = MagicMock(return_value=browser)
	return client, browser


UNAVAILABLE_PAGE = {
	"ok": False,
	"unavailable": True,
	"error": "exchange button not available",
	"availability": "disabled",
	"availability_signals": ["root.class=disabled"],
	"componentName": "ExchangeWx",
	"log": ["geekClick called", "found ExchangeWx type=2", "availability=disabled [root.class=disabled]"],
}


# ── history_self_message_state ──────────────────────────────


def test_self_message_state_true_when_any_self_message():
	assert history_self_message_state(WITH_SELF["zpData"], FRIEND_ID, page_size=20) is True


def test_self_message_state_false_only_when_conversation_is_complete():
	assert history_self_message_state(CANDIDATE_ONLY["zpData"], FRIEND_ID, page_size=20) is False
	full_page = {"messages": [_msg(i, FRIEND_ID) for i in range(20)]}
	assert history_self_message_state(full_page, FRIEND_ID, page_size=20) is None
	assert history_self_message_state({**full_page, "hasMore": False}, FRIEND_ID, page_size=20) is False
	assert history_self_message_state({**CANDIDATE_ONLY["zpData"], "hasMore": True}, FRIEND_ID, page_size=20) is None


def test_self_message_state_ignores_system_and_unknown_senders():
	system = {"mid": 1, "from": {"uid": 0}, "body": {}}
	no_sender = {"mid": 2, "body": {}}
	assert history_self_message_state({"messages": [system, no_sender, _msg(3, FRIEND_ID)]}, FRIEND_ID, page_size=20) is False
	assert history_self_message_state({"messages": []}, FRIEND_ID, page_size=20) is None
	assert history_self_message_state(None, FRIEND_ID, page_size=20) is None


# ── client ──────────────────────────────────────────────────


@pytest.mark.parametrize(("exchange_type", "label"), [(2, "换微信"), (1, "换电话")])
def test_unavailable_page_returns_exchange_not_available(exchange_type, label):
	page = {**UNAVAILABLE_PAGE, "componentName": rc._EXCHANGE_COMPONENT_NAMES[exchange_type]}
	client, _ = _client(page)
	result = client.exchange_request_by_friend(FRIEND_ID, exchange_type=exchange_type)
	assert result["code"] == -1
	assert result["__cli_error_code__"] == "EXCHANGE_NOT_AVAILABLE"
	assert label in result["message"]
	assert f"boss hr request-resume {FRIEND_ID}" in result["message"]
	details = result["__cli_error_details__"]
	assert details["availability"] == "disabled"
	assert details["availability_signals"] == ["root.class=disabled"]
	assert details["history_self_message"] is False
	# 预检拦下后不再回读聊天记录确认（只有动作前那一次快照）
	assert client.chat_history.call_count == 1
	client.close()


def test_precheck_args_passed_to_page():
	client, browser = _client({"ok": True, "componentName": "ExchangeWx", "confirmed": "not_required", "log": []},
		history=WITH_SELF, events=[{"kind": "ws_send", "bytes": 194, "utf8_bits": ["请求交换联系方式"]}])
	assert client.exchange_request_by_friend(FRIEND_ID, exchange_type=2)["code"] == 0
	args = browser.evaluate_js_with_chat_events.call_args[0][1]
	assert args["checkAvailability"] is True
	assert args["selfMessageInHistory"] is True
	client.close()


def test_resume_request_skips_precheck():
	client, browser = _client({"ok": True, "componentName": "ExchangeResume", "confirmed": True, "log": []},
		events=[{"kind": "ws_send", "bytes": 194, "utf8_bits": ["想要一份您的附件简历"]}])
	assert client.exchange_request_by_friend(FRIEND_ID, exchange_type=4)["code"] == 0
	assert browser.evaluate_js_with_chat_events.call_args[0][1]["checkAvailability"] is False
	client.close()


def test_unconfirmed_mentions_locked_button_when_no_self_message():
	# 按钮看起来可用（不拦截），但聊天记录里没有自己的消息：仍走 ACTION_UNCONFIRMED，提示里点出原因。
	client, _ = _client({"ok": True, "componentName": "ExchangeWx", "confirmed": "not_required", "log": []})
	result = client.exchange_request_by_friend(FRIEND_ID, exchange_type=2)
	assert result["__cli_error_code__"] == "ACTION_UNCONFIRMED"
	assert "按钮可能尚未解锁" in result["message"]
	assert result["__cli_error_details__"]["history_self_message"] is False
	client.close()

	client, _ = _client({"ok": True, "componentName": "ExchangeWx", "confirmed": "not_required", "log": []}, history=WITH_SELF)
	result = client.exchange_request_by_friend(FRIEND_ID, exchange_type=2)
	assert "按钮可能尚未解锁" not in result["message"]
	client.close()


# ── 错误码契约 / CLI 信封 / 向导 ─────────────────────────────


def test_exchange_not_available_is_registered_and_recoverable():
	spec = ERROR_CODES["EXCHANGE_NOT_AVAILABLE"]
	assert spec["recoverable"] is True
	assert "boss hr reply" in spec["recovery_action"]
	assert "boss hr request-resume" in spec["recovery_action"]
	code, recoverable, recovery = _classify_action_error("EXCHANGE_NOT_AVAILABLE", "x")
	assert (code, recoverable, recovery) == ("EXCHANGE_NOT_AVAILABLE", True, spec["recovery_action"])


def test_cli_envelope_for_exchange_not_available():
	client, _ = _client(UNAVAILABLE_PAGE)
	platform = BossRecruiterPlatform(client)
	context = MagicMock()
	context.__enter__ = MagicMock(return_value=platform)
	context.__exit__ = MagicMock(return_value=None)
	with patch("boss_agent_cli.commands.recruiter.resume.get_recruiter_platform_instance", return_value=context), \
		patch("boss_agent_cli.commands.recruiter.resume.AuthManager"):
		result = CliRunner().invoke(cli, ["--role", "recruiter", "--json", "hr", "resume", "--exchange", "--type", "wechat",
			"--friend-id", str(FRIEND_ID)])
	assert result.exit_code == 1
	error = json.loads(result.output)["error"]
	assert error["code"] == "EXCHANGE_NOT_AVAILABLE"
	assert error["recoverable"] is True
	assert "换微信按钮未解锁" in error["message"]
	assert error["details"]["availability"] == "disabled"
	client.close()


# ── 页面脚本（node 假 DOM）────────────────────────────────────

_FAKE_PAGE_JS = r"""
class El {
	constructor({cls = [], attrs = {}, visible = true, pointerEvents = 'auto', children = []} = {}) {
		this.classList = cls; this.attrs = attrs; this.visible = visible; this.pointerEvents = pointerEvents;
		this.children = children.map((c) => (c instanceof El ? c : new El(c))); this.nodeType = 1; this.isConnected = true; this.innerText = ''; this.textContent = '';
	}
	hasAttribute(name) { return name in this.attrs; }
	getAttribute(name) { return name in this.attrs ? this.attrs[name] : null; }
	all() { return this.children.flatMap((c) => [c, ...c.all()]); }
	querySelectorAll() { return this.all(); }
	getBoundingClientRect() { return this.visible ? {width: 10, height: 10} : {width: 0, height: 0}; }
	getClientRects() { return this.visible ? [1] : []; }
}
const sleep = () => Promise.resolve();
const scenario = __SCENARIO__;
let called = 0;
const root = scenario.root === null ? null : new El(scenario.root);
const vm = {
	$options: {name: 'ExchangeWx'}, type: 2, $el: root, $props: scenario.props || {}, $data: scenario.data || {}, $children: [],
	handleExChange: () => { called += 1; },
};
const host = {__vue__: vm};
global.window = {getComputedStyle: (el) => ({display: el.visible ? 'block' : 'none', visibility: 'visible', opacity: '1', pointerEvents: el.pointerEvents})};
global.document = {querySelectorAll: (sel) => sel === '*' ? [host] : []};
__HELPERS__
(async () => {
	const args = {componentName: 'ExchangeWx', checkAvailability: true, selfMessageInHistory: scenario.self, preConfirmUiWaitMs: 0, postConfirmUiWaitMs: 0};
	const log = [];
	const result = await (async () => { __ACTION__ })();
	console.log(JSON.stringify({called, ok: result.ok, unavailable: !!result.unavailable, availability: result.availability || null, signals: result.availability_signals || []}));
})();
"""


def _run_page(tmp_path, scenario):
	script = tmp_path / "exchange.js"
	script.write_text(
		_FAKE_PAGE_JS.replace("__HELPERS__", rc._CHAT_FRONTEND_HELPERS_JS.replace("const sleep =", "const _unusedSleep ="))
		.replace("__ACTION__", rc._EXCHANGE_ACTION_JS)
		.replace("__SCENARIO__", json.dumps(scenario)),
		encoding="utf-8",
	)
	output = subprocess.run(["node", str(script)], capture_output=True, text=True, check=True, timeout=30).stdout
	return json.loads(output.strip().splitlines()[-1])


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 运行页面脚本")
@pytest.mark.parametrize(
	("scenario", "signal"),
	[
		({"root": {"cls": ["operate-btn", "disabled"]}, "self": None}, "root.class=disabled"),
		({"root": {"cls": ["operate-btn", "gray"]}, "self": None}, "root.class=gray"),
		({"root": {"pointerEvents": "none"}, "self": None}, "root.pointer-events=none"),
		({"root": {"children": [{"cls": ["btn"], "attrs": {"disabled": ""}}]}, "self": True}, "btn.disabled"),
		({"root": {"attrs": {"aria-disabled": "true"}}, "self": None}, "root.aria-disabled"),
		({"root": {}, "props": {"disabled": True}, "self": True}, "props.disabled=true"),
		({"root": {}, "data": {"canExchange": False}, "self": True}, "data.canExchange=false"),
	],
)
def test_page_blocks_disabled_button_without_calling_handle_exchange(tmp_path, scenario, signal):
	result = _run_page(tmp_path, scenario)
	assert result["called"] == 0
	assert result["unavailable"] is True
	assert result["availability"] == "disabled"
	assert signal in result["signals"]


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 运行页面脚本")
def test_page_uses_history_only_when_dom_is_ambiguous(tmp_path):
	# DOM 拿不准（根元素不可见）且聊天记录确定没有自己的消息 → 拦下
	blocked = _run_page(tmp_path, {"root": {"visible": False}, "self": False})
	assert blocked == {"called": 0, "ok": False, "unavailable": True, "availability": "unknown", "signals": []}
	# DOM 拿不准、聊天记录也拿不准 → 维持原行为，照常调用
	assert _run_page(tmp_path, {"root": None, "self": None})["called"] == 1
	# 按钮明确可用时，即使聊天记录里没有自己的消息也不拦（信号冲突不拦截）
	assert _run_page(tmp_path, {"root": {"cls": ["operate-btn"]}, "self": False})["called"] == 1
	# 不相关的 class（如 btn-gray）不算禁用
	assert _run_page(tmp_path, {"root": {"cls": ["btn-gray", "operate"]}, "self": True})["called"] == 1
