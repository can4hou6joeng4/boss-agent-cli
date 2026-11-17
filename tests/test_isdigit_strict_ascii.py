"""非 ASCII 数字串（'①' / '²' / '١'）不能当数字解析（Issue #1306）。

``str.isdigit()`` 对圆圈数字、上标、全角数字都为真，但 ``int()`` 不认这些
字符，转换时抛 ValueError；``isdigit()`` 为假的全角数字串又会绕过数字分支。
原先这些输入进入参数校验或平台响应解析后，会在 ``int()`` 处崩溃，落成
可重试的网络错误、叉口内部错误或直接丢掉整个信封。现在一律按 ASCII 严格
判定：非 ASCII 输入走参数校验的确定性错误，平台字段漂移则视为非游标。
"""

from types import SimpleNamespace

import click
import pytest

from boss_agent_cli.commands.crawl import _city_code
from boss_agent_cli.commands.mark import _resolve_label
from boss_agent_cli.commands.recruiter.chat import _chat_history_pagination, _message_cursor_id
from boss_agent_cli.output import Logger
from boss_agent_cli.search_filters import resolve_lookup_codes
from boss_agent_cli.wizard.actions import ActionContext, DEFAULT_ACTIONS
from boss_agent_cli.wizard.prompts import ClickMenu
from boss_agent_cli.wizard.runner import WorkflowActionError

_NON_ASCII_DIGITS = ["①", "²", "٣"]


@pytest.mark.parametrize("label", _NON_ASCII_DIGITS)
def test_mark_rejects_non_ascii_digits_as_unknown_label(label):
	with pytest.raises(click.BadParameter, match="未知标签"):
		_resolve_label(label)
	assert _resolve_label("7") == 7


@pytest.mark.parametrize("city", _NON_ASCII_DIGITS)
def test_crawl_city_code_rejects_non_ascii_digits(city):
	with pytest.raises(ValueError, match="城市"):
		_city_code(city)
	assert _city_code("101010100") == "101010100"


@pytest.mark.parametrize("label", _NON_ASCII_DIGITS)
def test_wizard_mark_reports_invalid_param_for_non_ascii_digits(tmp_path, label):
	context = ActionContext(data_dir=tmp_path, platform="zhipin", role="candidate", logger=Logger())

	with pytest.raises(WorkflowActionError) as excinfo:
		DEFAULT_ACTIONS["candidate_mark"](context, {"security_id": "sid", "label": label}, {})

	# 未知标签是参数错误，不是可重试的网络错误。
	assert excinfo.value.code == "INVALID_PARAM"


def test_wizard_crawl_rejects_non_ascii_digit_city(tmp_path):
	context = ActionContext(data_dir=tmp_path, platform="zhipin", role="candidate", logger=Logger())

	with pytest.raises(WorkflowActionError) as excinfo:
		DEFAULT_ACTIONS["crawl_start"](context, {"city": "①"}, {})

	assert excinfo.value.code == "INVALID_PARAM"
	assert "未知城市" in str(excinfo.value)


def test_menu_select_reprompts_on_non_ascii_digit(monkeypatch):
	answers = iter(["①", "1"])
	monkeypatch.setattr(click, "prompt", lambda *args, **kwargs: next(answers))

	options = [SimpleNamespace(value="one", label="一", description="")]
	assert ClickMenu().select("选择", options) == "one"


@pytest.mark.parametrize("part", _NON_ASCII_DIGITS)
def test_search_filter_rejects_non_ascii_digit_code(part):
	lookup = {"1-3年": "101"}
	with pytest.raises(ValueError, match="未知"):
		resolve_lookup_codes(part, lookup, "经验要求")
	assert resolve_lookup_codes("101", lookup, "经验要求") == "101"


@pytest.mark.parametrize("mid", _NON_ASCII_DIGITS)
def test_chat_cursor_ignores_non_ascii_digits(mid):
	assert _message_cursor_id(mid) is None
	assert _message_cursor_id(f" {mid} ") is None
	assert _message_cursor_id("42") == 42

	pagination = _chat_history_pagination({"messages": [{"mid": mid}, {"mid": "42"}, {"mid": 43}]})
	assert pagination["next_max_msg_id"] == 42


# ── 超长数字串（int_max_str_digits）不能兜成网络错误（Issue #1437 / #1442 / #1438） ──

_LONG_DIGITS = "9" * 4301


def test_mark_rejects_overlong_numeric_label_with_bounded_message():
	"""4301 位数字串让 int() 抛裸 ValueError；必须按参数错误上报且回显截断。"""
	with pytest.raises(click.BadParameter) as excinfo:
		_resolve_label(_LONG_DIGITS)

	message = str(excinfo.value)
	assert "标签 ID 无效" in message
	assert len(message) < 200  # 信封不能随输入规模膨胀


def test_wizard_mark_reports_invalid_param_for_overlong_numeric_label(tmp_path):
	context = ActionContext(data_dir=tmp_path, platform="zhipin", role="candidate", logger=Logger())

	with pytest.raises(WorkflowActionError) as excinfo:
		DEFAULT_ACTIONS["candidate_mark"](context, {"security_id": "sid", "label": _LONG_DIGITS}, {})

	assert excinfo.value.code == "INVALID_PARAM"
	assert len(str(excinfo.value)) < 250


def test_menu_select_reprompts_on_overlong_digit(monkeypatch):
	"""回退菜单遇到超长数字不能 traceback，必须重新提示序号。"""
	answers = iter([_LONG_DIGITS, "1"])
	monkeypatch.setattr(click, "prompt", lambda *args, **kwargs: next(answers))

	options = [SimpleNamespace(value="one", label="一", description="")]
	assert ClickMenu().select("选择", options) == "one"
