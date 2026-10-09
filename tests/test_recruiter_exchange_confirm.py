"""#443：招聘者交换联系方式 / 求简历的成功判定、确认按钮作用域与诊断信息。"""
import json
import shutil
import subprocess
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.api import recruiter_client as rc
from boss_agent_cli.api.browser_client import _ws_frame_payload_bytes
from boss_agent_cli.api.recruiter_client import BossRecruiterClient
from boss_agent_cli.main import cli
from boss_agent_cli.platforms.zhipin_recruiter import BossRecruiterPlatform
from boss_agent_cli.schema.error_codes import ERROR_CODES

FRIEND_ID = 1
FRIEND_DETAIL = {
	"code": 0,
	"zpData": {"friendList": [{"uid": FRIEND_ID, "encryptUid": "u", "encryptJobId": "j", "securityId": "s", "name": "Tester", "friendSource": 0}]},
}
OLD_MESSAGE = {"mid": 100, "time": 1, "from": {"uid": 9, "source": 0}, "body": {"type": 1, "text": "你好"}}


def _history(*messages):
	return {"code": 0, "zpData": {"messages": list(messages)}}


@pytest.fixture(autouse=True)
def _no_retry_sleep(monkeypatch):
	sleeps: list[float] = []
	monkeypatch.setattr(rc.time, "sleep", sleeps.append)
	return sleeps


def _client(*, page, events=(), histories=(_history(OLD_MESSAGE), _history(OLD_MESSAGE))):
	"""friend_detail 与 chat_history（动作前快照 + 动作后回读/轮询）都走 mock；回读次数超出时重复最后一份。"""
	client = BossRecruiterClient(MagicMock())
	client.friend_detail = MagicMock(return_value=FRIEND_DETAIL)
	queue = list(histories)

	def _chat_history(*_args, **_kwargs):
		item = queue.pop(0) if len(queue) > 1 else queue[0]
		if isinstance(item, Exception):
			raise item
		return item

	client.chat_history = MagicMock(side_effect=_chat_history)
	browser = MagicMock()
	browser.evaluate_js_with_chat_events.return_value = {"value": page, "events": list(events)}
	client._get_browser = MagicMock(return_value=browser)
	return client, browser


def _page(component="ExchangeResume", confirmed=True):
	return {"ok": True, "error": None, "componentName": component, "confirmed": confirmed, "log": ["handleExChange returned", f"confirm clicked={confirmed}"]}


def _ws(*bits):
	return {"kind": "ws_send", "bytes": 194, "utf8_bits": list(bits)}


def test_page_ok_without_evidence_is_action_unconfirmed_not_unexpected_page_result():
	# 页面脚本显式返回 error: null，以前 setdefault 不生效，信封只剩 unexpected page result。
	client, _ = _client(page=_page(), events=[_ws("/message/suggest")])
	result = client.exchange_request_by_friend(FRIEND_ID, exchange_type=4)
	assert result["code"] == -1
	assert "unexpected page result" not in result["message"]
	assert result["__cli_error_code__"] == "ACTION_UNCONFIRMED"
	assert f"boss hr chatmsg {FRIEND_ID}" in result["message"]
	details = result["__cli_error_details__"]
	assert details["log"] == ["handleExChange returned", "confirm clicked=True"]
	assert details["confirmed"] is True
	assert details["componentName"] == "ExchangeResume"
	assert details["ws_evidence"] == {"event_count": 1, "ws_send_count": 1, "matched_ws_count": 0}
	assert details["history_checked"] is True
	assert details["history_matched_count"] == 0
	client.close()


def test_new_resume_request_text_counts_as_ws_evidence():
	client, _ = _client(page=_page(), events=[_ws("想要一份您的附件简历")])
	result = client.exchange_request_by_friend(FRIEND_ID, exchange_type=4)
	assert result["code"] == 0
	assert result["zpData"]["matched_ws_count"] == 1
	client.close()


def test_chat_history_confirms_when_ws_frame_has_no_text():
	new_message = {"mid": 101, "time": 2, "from": {"uid": 9, "source": 0}, "body": {"type": 7, "dialog": {"text": "想要一份您的附件简历"}}}
	client, _ = _client(
		page=_page(),
		events=[_ws("binary-only")],
		histories=[_history(OLD_MESSAGE), _history(OLD_MESSAGE, new_message)],
	)
	result = client.exchange_request_by_friend(FRIEND_ID, exchange_type=4)
	assert result["code"] == 0
	assert result["zpData"]["matched_ws_count"] == 0
	assert result["zpData"]["history_matched_count"] == 1
	assert result["zpData"]["history_attempts"] == 1
	assert client.chat_history.call_count == 2
	client.close()


def test_chat_history_ignores_messages_that_existed_before_action():
	old_request = {"mid": 100, "time": 1, "from": {"uid": 9, "source": 0}, "body": {"type": 1, "text": "想要一份您的附件简历"}}
	client, _ = _client(page=_page(), histories=[_history(old_request), _history(old_request)])
	result = client.exchange_request_by_friend(FRIEND_ID, exchange_type=4)
	assert result["__cli_error_code__"] == "ACTION_UNCONFIRMED"
	client.close()


def test_chat_history_falls_back_to_time_when_baseline_unavailable():
	old_request = {"mid": 100, "time": 1, "body": {"text": "想要一份您的附件简历"}}
	client, _ = _client(page=_page(), histories=[{"code": 5, "message": "busy"}, _history(old_request)])
	result = client.exchange_request_by_friend(FRIEND_ID, exchange_type=4)
	assert result["__cli_error_code__"] == "ACTION_UNCONFIRMED"
	client.close()


def test_history_read_failure_after_action_is_unconfirmed_with_diagnostics():
	client, _ = _client(page=_page(), histories=[_history(OLD_MESSAGE), RuntimeError("boom")])
	result = client.exchange_request_by_friend(FRIEND_ID, exchange_type=1)
	assert result["__cli_error_code__"] == "ACTION_UNCONFIRMED"
	assert result["__cli_error_details__"]["history_checked"] is False
	assert result["__cli_error_details__"]["history_error"] == "RuntimeError"
	client.close()


def test_wechat_exchange_uses_only_exchangewx_and_reports_resume_side_effect():
	client, browser = _client(page=_page("ExchangeWx", "not_required"), events=[_ws("请求交换联系方式")])
	result = client.exchange_request_by_friend(FRIEND_ID, exchange_type=2)
	assert result["code"] == 0
	assert "side_effects" not in result["zpData"]
	args = browser.evaluate_js_with_chat_events.call_args[0][1]
	assert args["componentName"] == "ExchangeWx"
	script = browser.evaluate_js_with_chat_events.call_args[0][0]
	assert "ExchangeResume" not in script
	client.close()

	client, _ = _client(page=_page("ExchangeWx"), events=[_ws("请求交换联系方式"), _ws("想要一份您的附件简历")])
	result = client.exchange_request_by_friend(FRIEND_ID, exchange_type=2)
	assert result["zpData"]["side_effects"] == ["resume_request_detected"]
	client.close()


def test_confirm_helper_is_scoped_and_visibility_checked():
	js = rc._CHAT_FRONTEND_HELPERS_JS
	start = js.index("const isVisibleElement")
	end = js.index("const chatConversationText")
	confirm_js = js[start:end]
	assert "document.body" not in js
	assert "getComputedStyle" in confirm_js
	assert "isVisibleElement(el)" in confirm_js
	assert "confirm|sure|primary" not in js
	assert "vm.$el" in confirm_js
	assert "'not_required'" in confirm_js
	assert "snapshotConfirmState(vm)" in rc._EXCHANGE_ACTION_JS
	assert "clickPrimaryConfirm(vm, beforeConfirm)" in rc._EXCHANGE_ACTION_JS


_FAKE_DOM_JS = r"""
class El {
	constructor(text, {cls = '', visible = true, children = []} = {}) {
		this.ownText = text; this.className = cls; this.visible = visible; this.children = children;
		this.isConnected = true; this.clicked = 0;
	}
	get innerText() { return [this.ownText, ...this.children.map((c) => c.innerText)].filter(Boolean).join(' '); }
	get textContent() { return this.innerText; }
	all() { return this.children.flatMap((c) => [c, ...c.all()]); }
	querySelectorAll() { return this.all(); }
	getBoundingClientRect() { return this.visible ? {width: 10, height: 10} : {width: 0, height: 0}; }
	getClientRects() { return this.visible ? [1] : []; }
	click() { this.clicked += 1; if (this.onClick) this.onClick(); }
}
const resumeConfirm = new El('确定', {visible: false});
const resumePopup = new El('', {cls: 'exchange-tooltip', visible: false, children: [new El('确定向牛人请求附件简历吗'), resumeConfirm]});
const unrelatedPrimary = new El('发送', {cls: 'btn-primary'});
const wxConfirm = new El('确定');
const wxPopup = new El('', {cls: 'exchange-tooltip', visible: false, children: [new El('确定与对方交换微信吗'), wxConfirm]});
const wxRoot = new El('', {children: [new El('换微信')]});
let popups = [resumePopup];
global.window = {getComputedStyle: (el) => ({display: el.visible ? 'block' : 'none', visibility: 'visible', opacity: '1'})};
global.document = {querySelectorAll: () => popups};
__HELPERS__
const vm = {$el: wxRoot, handleExChange: () => { popups = [resumePopup, wxPopup]; wxPopup.visible = true; }};
const before = snapshotConfirmState(vm);
vm.handleExChange();
const first = clickPrimaryConfirm(vm, before);
const vm2 = {$el: new El('', {children: [new El('换手机')]})};
const before2 = snapshotConfirmState(vm2);
const second = clickPrimaryConfirm(vm2, before2);
console.log(JSON.stringify({first, second, wx: wxConfirm.clicked, resume: resumeConfirm.clicked, primary: unrelatedPrimary.clicked}));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 运行页面脚本")
def test_confirm_helper_clicks_only_new_visible_popup_in_fake_dom(tmp_path):
	script = tmp_path / "confirm.js"
	script.write_text(_FAKE_DOM_JS.replace("__HELPERS__", rc._CHAT_FRONTEND_HELPERS_JS), encoding="utf-8")
	output = subprocess.run(["node", str(script)], capture_output=True, text=True, check=True, timeout=30).stdout
	result = json.loads(output.strip().splitlines()[-1])
	# 换微信只点新出现的微信确认框；隐藏的附件简历确认框和 class 带 primary 的按钮都不碰。
	assert result == {"first": True, "second": "not_required", "wx": 1, "resume": 0, "primary": 0}


def test_ws_text_frame_is_not_base64_decoded():
	text = '{"type":1,"text":"想要一份您的附件简历"}'
	assert _ws_frame_payload_bytes({"opcode": 1, "payloadData": text}).decode("utf-8") == text
	import base64
	raw = "二进制帧里的请求交换联系方式".encode("utf-8")
	assert _ws_frame_payload_bytes({"opcode": 2, "payloadData": base64.b64encode(raw).decode()}) == raw
	assert _ws_frame_payload_bytes({"opcode": 1, "payloadData": None}) == b""


def test_action_unconfirmed_is_registered_and_not_retryable():
	spec = ERROR_CODES["ACTION_UNCONFIRMED"]
	assert spec["recoverable"] is False
	assert "boss hr chatmsg" in spec["recovery_action"]
	assert "勿直接重试" in spec["recovery_action"]


def test_cli_envelope_carries_action_unconfirmed_and_details():
	client, _ = _client(page=_page(), events=[_ws("/message/suggest")])
	platform = BossRecruiterPlatform(client)
	context = MagicMock()
	context.__enter__ = MagicMock(return_value=platform)
	context.__exit__ = MagicMock(return_value=None)
	with patch("boss_agent_cli.commands.recruiter.request_resume.get_recruiter_platform_instance", return_value=context), \
		patch("boss_agent_cli.commands.recruiter.request_resume.AuthManager"):
		result = CliRunner().invoke(cli, ["--role", "recruiter", "--json", "hr", "request-resume", str(FRIEND_ID)])
	assert result.exit_code == 1
	error = json.loads(result.output)["error"]
	assert error["code"] == "ACTION_UNCONFIRMED"
	assert error["recoverable"] is False
	assert "unexpected page result" not in error["message"]
	assert error["details"]["componentName"] == "ExchangeResume"
	assert error["details"]["ws_evidence"]["matched_ws_count"] == 0
	assert "log" in error["details"]
	client.close()


def test_page_failure_still_surfaces_real_error_in_details():
	client, _ = _client(page={"ok": False, "error": "ExchangeWx Vue component not found", "log": ["geekClick called"]})
	result = client.exchange_request_by_friend(FRIEND_ID, exchange_type=2)
	assert result["message"] == "exchange_request_by_friend failed: ExchangeWx Vue component not found"
	assert "__cli_error_code__" not in result
	assert result["__cli_error_details__"]["log"] == ["geekClick called"]
	assert BossRecruiterPlatform(client).parse_error(result)[0] == "UNKNOWN"
	client.close()


RECRUITER_UID = 66826625


def _card(mid, aid, *, sender=RECRUITER_UID, time_ms=None):
	"""聊天记录里的动作卡片：没有文案，只有 body.type=4 + action.aid。"""
	return {
		"mid": mid,
		"time": time_ms if time_ms is not None else int(rc.time.time() * 1000) + 500,
		"type": 1,
		"from": {"uid": sender, "source": 0},
		"to": {"uid": FRIEND_ID, "source": 0},
		"body": {"action": {"extend": "{}", "aid": aid}, "type": 4, "templateId": 1},
	}


def test_wechat_exchange_confirmed_by_aid_32_action_card_in_history():
	# 实测：换微信实际已发出，但聊天记录里只有 aid=32 的动作卡片，WS 帧也没有可读文案。
	client, _ = _client(
		page=_page("ExchangeWx", "not_required"),
		events=[_ws("binary-only")],
		histories=[_history(OLD_MESSAGE), _history(OLD_MESSAGE, _card(101, 32))],
	)
	result = client.exchange_request_by_friend(FRIEND_ID, exchange_type=2)
	assert result["code"] == 0
	assert result["zpData"]["history_matched_count"] == 1
	assert "side_effects" not in result["zpData"]
	client.close()


def test_resume_request_confirmed_by_aid_37_action_card_in_history():
	client, _ = _client(page=_page(), histories=[_history(OLD_MESSAGE), _history(OLD_MESSAGE, _card(101, 37))])
	result = client.exchange_request_by_friend(FRIEND_ID, exchange_type=4)
	assert result["code"] == 0
	assert result["zpData"]["history_matched_count"] == 1
	client.close()


def test_history_is_polled_until_action_card_lands(_no_retry_sleep):
	# 消息落库晚于 WS 监听窗口：前两次回读还没有，第三次出现。
	client, _ = _client(
		page=_page("ExchangeWx", "not_required"),
		histories=[_history(OLD_MESSAGE), _history(OLD_MESSAGE), _history(OLD_MESSAGE),
			_history(OLD_MESSAGE, _card(101, 32))],
	)
	result = client.exchange_request_by_friend(FRIEND_ID, exchange_type=2)
	assert result["code"] == 0
	assert result["zpData"]["history_attempts"] == 3
	assert _no_retry_sleep == [1.5, 2.0]
	client.close()


def test_unconfirmed_after_all_history_retries(_no_retry_sleep):
	client, _ = _client(page=_page("ExchangeWx", "not_required"))
	result = client.exchange_request_by_friend(FRIEND_ID, exchange_type=2)
	assert result["__cli_error_code__"] == "ACTION_UNCONFIRMED"
	assert result["__cli_error_details__"]["history_attempts"] == 4
	assert client.chat_history.call_count == 5  # 1 次快照 + 4 次回读
	assert sum(_no_retry_sleep) == pytest.approx(6.0)
	client.close()


def test_no_history_retry_when_ws_already_confirms(_no_retry_sleep):
	client, _ = _client(page=_page("ExchangeWx"), events=[_ws("请求交换联系方式")])
	assert client.exchange_request_by_friend(FRIEND_ID, exchange_type=2)["code"] == 0
	assert _no_retry_sleep == []
	client.close()


@pytest.mark.parametrize(
	("card", "reason"),
	[
		(_card(101, 37), "求简历卡片不能当作换微信成功"),
		(_card(101, 32, sender=FRIEND_ID), "对方发来的卡片不算"),
		(_card(100, 32), "动作前已存在的 mid 不算"),
		(_card(101, 32, time_ms=1), "时间远早于动作开始的不算"),
		({**_card(101, 32), "from": None}, "没有发送者信息的不算"),
	],
)
def test_wechat_action_card_matching_is_strict(card, reason):
	client, _ = _client(page=_page("ExchangeWx", "not_required"), histories=[_history(OLD_MESSAGE), _history(OLD_MESSAGE, card)])
	result = client.exchange_request_by_friend(FRIEND_ID, exchange_type=2)
	assert result["__cli_error_code__"] == "ACTION_UNCONFIRMED", reason
	client.close()


def test_wechat_exchange_reports_resume_card_as_side_effect():
	client, _ = _client(
		page=_page("ExchangeWx", "not_required"),
		histories=[_history(OLD_MESSAGE), _history(OLD_MESSAGE, _card(101, 32), _card(102, 37))],
	)
	result = client.exchange_request_by_friend(FRIEND_ID, exchange_type=2)
	assert result["code"] == 0
	assert result["zpData"]["history_matched_count"] == 1
	assert result["zpData"]["side_effects"] == ["resume_request_detected"]
	client.close()


def test_phone_exchange_accepts_unknown_aid_but_not_known_other_types():
	client, _ = _client(page=_page("ExchangePhone"), histories=[_history(OLD_MESSAGE), _history(OLD_MESSAGE, _card(101, 31))])
	assert client.exchange_request_by_friend(FRIEND_ID, exchange_type=1)["code"] == 0
	client.close()
	for aid in (32, 37):
		client, _ = _client(page=_page("ExchangePhone"), histories=[_history(OLD_MESSAGE), _history(OLD_MESSAGE, _card(101, aid))])
		assert client.exchange_request_by_friend(FRIEND_ID, exchange_type=1)["__cli_error_code__"] == "ACTION_UNCONFIRMED"
		client.close()


def test_action_card_json_in_ws_frame_counts_as_evidence():
	frame = '{"action":{"extend":"{}","aid":32},"type":4,"templateId":1}'
	client, _ = _client(page=_page("ExchangeWx", "not_required"), events=[_ws(frame)])
	result = client.exchange_request_by_friend(FRIEND_ID, exchange_type=2)
	assert result["code"] == 0
	assert result["zpData"]["matched_ws_count"] == 1
	client.close()

	client, _ = _client(page=_page("ExchangeWx", "not_required"), events=[_ws(frame.replace("32", "37"))])
	assert client.exchange_request_by_friend(FRIEND_ID, exchange_type=2)["__cli_error_code__"] == "ACTION_UNCONFIRMED"
	client.close()
