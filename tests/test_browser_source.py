"""浏览器来源 seam 的门禁。

这些测试守的不是某个功能，而是**结构**：通道选择必须只有一个表达式、
只有一个分发点、fail-closed 必须是来源的结构性属性而不是某处 if 的副作用。

背景：PR #404 与 #388 曾各自在 ``_ensure_started`` 相隔三行的两处缝隙里插了一个
模式短路，git 三方合并干净通过、不留冲突标记，合并后类上有两个互不感知的模式
布尔，谁生效取决于插入位置，且失效方向是 fail-open。下面每条测试都对应那次
事故的一个侧面。
"""

import ast
import inspect
import re
import textwrap
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from boss_agent_cli.api import browser_client
from boss_agent_cli.api.browser_client import BrowserSession
from boss_agent_cli.api.browser_source import (
	CHANNEL_BRIDGE,
	CHANNEL_CDP,
	CHANNEL_HEADLESS,
	DEFAULT_BROWSER_SOURCE,
	KNOWN_CHANNELS,
	POLICIES,
	BrowserSourceUnavailable,
	resolve_policy,
)
from boss_agent_cli.commands.schema import SCHEMA_DATA

_CHANNEL_ENTRYPOINTS = {"_try_bridge", "_try_cdp", "_start_headless"}


# ── T1：通道方法只能从分发器调用 ────────────────────────────────────────


def test_channel_entrypoints_are_only_called_from_the_dispatcher():
	"""``_try_bridge`` / ``_try_cdp`` / ``_start_headless`` 只能由 ``_ensure_started`` 调用。

	一旦有人在别处直接调用其中之一（#404 的 ``_start_cdp_required`` 就是这么做的），
	就等于开了第二个分发点，本 seam 的全部保证立刻失效。
	"""
	source = Path(browser_client.__file__).read_text(encoding="utf-8")
	tree = ast.parse(source)

	offenders: list[str] = []
	for func in ast.walk(tree):
		if not isinstance(func, ast.FunctionDef):
			continue
		for node in ast.walk(func):
			if not isinstance(node, ast.Call):
				continue
			callee = node.func
			if (
				isinstance(callee, ast.Attribute)
				and isinstance(callee.value, ast.Name)
				and callee.value.id == "self"
				and callee.attr in _CHANNEL_ENTRYPOINTS
				and func.name != "_ensure_started"
			):
				offenders.append(f"{func.name}() 调用了 self.{callee.attr}()（第 {node.lineno} 行）")

	assert not offenders, (
		"通道方法只能从 _ensure_started 这个唯一分发点调用，检测到额外调用点：\n  "
		+ "\n  ".join(offenders)
		+ "\n请在 browser_source.POLICIES 里加一行，而不是新开一条分发路径。"
	)


def _executable_source(func) -> str:
	"""返回函数体的可执行部分（去掉 docstring 与注释）。

	必须去掉 docstring：``_ensure_started`` 的 docstring 里就写着
	「不许写 ``if self._policy.name == X``」这句反例，对原始源码做文本匹配会自伤。
	"""
	tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
	fn = tree.body[0]
	assert isinstance(fn, ast.FunctionDef)
	body = fn.body
	if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
		body = body[1:]
	return "\n".join(ast.unparse(node) for node in body)


def test_dispatcher_has_no_policy_name_branch():
	"""分发器里不许出现按来源名分支——顺序与准入只能来自 policy 字段。"""
	src = _executable_source(BrowserSession._ensure_started)
	assert "_policy.name ==" not in src
	assert "_browser_source" not in src
	# 允许 `channel == CHANNEL_X`（那是通道分派），不允许按来源名硬编码
	for name in POLICIES:
		assert f"'{name}'" not in src, f"分发器里不该出现来源名字面量 {name!r}"


def test_every_declared_channel_is_dispatchable():
	"""策略表里出现的每个通道，分发器都必须认识。"""
	declared = {ch for p in POLICIES.values() for ch in p.channels}
	assert declared <= set(KNOWN_CHANNELS)
	src = inspect.getsource(BrowserSession._ensure_started)
	for channel_const in ("CHANNEL_BRIDGE", "CHANNEL_CDP", "CHANNEL_HEADLESS"):
		assert channel_const in src, f"分发器缺少 {channel_const} 分支"


# ── T2：只允许一个来源选择器属性 ────────────────────────────────────────


def test_browser_session_has_exactly_one_source_selector_attribute():
	"""通道/来源选择必须收敛到 ``_policy`` 这一个属性。

	回归防护：#404 加了 ``_browser_mode``、#388 在相隔三行的位置加了
	``_existing_browser_only``，两者互不感知，谁先命中纯属插入位置的偶然。
	"""
	session = BrowserSession(cookies={}, user_agent="")
	selector_like = {name for name in vars(session) if re.search(r"mode|only|required|strict|source|policy", name)}
	assert selector_like == {"_policy"}, (
		f"检测到未收敛的通道开关：{sorted(selector_like - {'_policy'})}。"
		"请在 browser_source.POLICIES 里加一行，不要在 BrowserSession.__init__ 上加第二个布尔。"
	)


def test_default_source_is_auto():
	session = BrowserSession(cookies={}, user_agent="")
	assert session._policy.name == DEFAULT_BROWSER_SOURCE
	assert resolve_policy(None) is POLICIES["auto"]
	assert resolve_policy("  AUTO  ") is POLICIES["auto"]


def test_unknown_source_raises_instead_of_silently_falling_back():
	"""静默回落到 auto 正是本 seam 要消灭的东西。"""
	with pytest.raises(KeyError):
		resolve_policy("no-such-source")


# ── T3：枚举对齐 + 安全属性作为可执行不变量 ─────────────────────────────


def test_auto_keeps_the_full_fallback_chain_and_stays_fail_open():
	"""默认路径行为不变：三级降级链 + 失败仍走既有 NETWORK_ERROR 兜底。

	改这条断言等于对所有存量用户和所有无浏览器 CI 环境做破坏性变更。
	"""
	auto = POLICIES["auto"]
	assert auto.channels == (CHANNEL_BRIDGE, CHANNEL_CDP, CHANNEL_HEADLESS)
	assert auto.allow_browser_launch is True
	assert auto.may_create_context is True
	assert auto.auto_probe_cdp is True
	assert auto.failure_code is None
	assert auto.fail_closed is False


def test_existing_browser_policy_is_fail_closed_and_credential_free():
	policy = POLICIES["existing-browser"]
	assert policy.channels == (CHANNEL_BRIDGE, CHANNEL_CDP)
	assert policy.use_stored_credentials is False
	assert policy.may_create_context is False
	assert policy.allow_browser_launch is False
	assert policy.auto_probe_cdp is True
	assert policy.failure_code == "BROWSER_SESSION_NOT_FOUND"
	assert policy.operator_actions
	assert policy.next_actions == ("boss doctor",)


def test_only_auto_is_fail_open():
	"""fail-closed 是「显式命名来源」的结构性属性，不是某处 if 的副作用。"""
	for name, policy in POLICIES.items():
		assert policy.fail_closed is (name != "auto"), f"{name} 的 fail_closed 与「非 auto 即 fail-closed」不符"


def test_fail_closed_policies_declare_a_registered_error_code_and_human_guidance():
	"""每个 fail-closed 来源的错误码必须已在 schema 枚举里，且必须给真人指引。

	浏览器/会话类失败是教科书级的 operator 场景——TTY 下 display 只渲染
	``operator_actions``，只填 ``next_actions`` 等于让真正需要指引的人看不到。
	"""
	declared = {p.failure_code for p in POLICIES.values() if p.failure_code}
	missing = declared - set(SCHEMA_DATA["error_codes"])
	assert not missing, f"策略表声明了未登记进 SCHEMA_DATA['error_codes'] 的错误码：{sorted(missing)}"

	for name, policy in POLICIES.items():
		if not policy.fail_closed:
			continue
		assert policy.failure_message, f"{name} 缺 failure_message"
		assert policy.recovery_action, f"{name} 缺 recovery_action"
		assert policy.operator_actions, f"{name} 缺 operator_actions（TTY 下唯一会被渲染的一路）"


def test_fail_closed_policies_never_allow_browser_launch_or_credential_injection():
	"""显式来源一律不得 launch 浏览器、不得在空浏览器里新建 context 注入本地 Cookie。

	后者是 Issue #387 点名的反面方案（把 Cookie 复制到另一个 Profile）。
	"""
	for name, policy in POLICIES.items():
		if not policy.fail_closed:
			continue
		assert policy.allow_browser_launch is False, f"{name} 不该允许本进程 launch 浏览器"
		assert policy.may_create_context is False, f"{name} 不该在空浏览器里新建 context 注入凭据"


def test_policies_are_frozen():
	policy = POLICIES["auto"]
	with pytest.raises(Exception):
		policy.channels = ()  # type: ignore[misc]


# ── T4：行为级 —— 尝试顺序 == 声明顺序，fail-closed 绝不启动浏览器 ──────


def _instrument(session: BrowserSession, monkeypatch, *, all_fail: bool = True) -> list[str]:
	"""把三个通道入口换成只记录调用名的替身，默认全部失败。"""
	calls: list[str] = []

	def fake_bridge() -> bool:
		calls.append(CHANNEL_BRIDGE)
		return False

	def fake_cdp() -> bool:
		calls.append(CHANNEL_CDP)
		return False

	def fake_headless() -> None:
		calls.append(CHANNEL_HEADLESS)
		if all_fail:
			raise RuntimeError("headless unavailable")
		session._started = True

	def fake_driver() -> None:
		session._pw = MagicMock()

	monkeypatch.setattr(session, "_try_bridge", fake_bridge)
	monkeypatch.setattr(session, "_try_cdp", fake_cdp)
	monkeypatch.setattr(session, "_start_headless", fake_headless)
	monkeypatch.setattr(session, "_ensure_playwright", fake_driver)
	return calls


@pytest.mark.parametrize("source", sorted(POLICIES))
def test_attempt_order_matches_declared_channels(source, monkeypatch):
	"""实际尝试顺序必须逐字等于 ``policy.channels``，白名单外的通道一次都不许碰。"""
	session = BrowserSession(cookies={}, user_agent="", browser_source=source)
	calls = _instrument(session, monkeypatch)
	policy = POLICIES[source]

	with pytest.raises(Exception):
		session._ensure_started()

	assert calls == list(policy.channels), f"{source} 的尝试顺序与声明不符"
	skipped = set(KNOWN_CHANNELS) - set(policy.channels)
	assert not (set(calls) & skipped), f"{source} 碰了白名单外的通道：{sorted(set(calls) & skipped)}"


@pytest.mark.parametrize("source", sorted(name for name, p in POLICIES.items() if p.fail_closed))
def test_fail_closed_source_raises_typed_error_and_leaks_no_driver(source, monkeypatch):
	"""通道耗尽时抛带 code 的 typed exception，且不留下 playwright driver 进程。"""
	session = BrowserSession(cookies={}, user_agent="", browser_source=source)
	_instrument(session, monkeypatch)

	with pytest.raises(BrowserSourceUnavailable) as excinfo:
		session._ensure_started()

	assert excinfo.value.code == POLICIES[source].failure_code
	assert excinfo.value.policy is POLICIES[source]
	assert session._pw is None, "fail-closed 路径不得泄漏 playwright driver"
	assert session._started is False


def test_auto_exhaustion_keeps_the_existing_network_error_contract(monkeypatch):
	"""auto 失败仍原样上抛 playwright 异常（→ display 兜底 NETWORK_ERROR），不发新错误码。

	改这条等于改默认路径的对外错误契约，所有存量 Agent 的重试分支都会改道。
	"""
	session = BrowserSession(cookies={}, user_agent="")
	_instrument(session, monkeypatch)

	with pytest.raises(RuntimeError) as excinfo:
		session._ensure_started()

	assert not isinstance(excinfo.value, BrowserSourceUnavailable)
	assert session._pw is None, "headless 抛错时不得泄漏 playwright driver（既有缺陷）"


def test_auto_still_reaches_headless_when_earlier_channels_fail(monkeypatch):
	session = BrowserSession(cookies={}, user_agent="")
	calls = _instrument(session, monkeypatch, all_fail=False)

	session._ensure_started()

	assert calls == [CHANNEL_BRIDGE, CHANNEL_CDP, CHANNEL_HEADLESS]
	assert session._started is True


# ── 裸 CDP 路径（evaluate_js）的来源守卫 ────────────────────────────────


def test_evaluate_js_is_blocked_when_source_forbids_cdp():
	"""``evaluate_js`` 刻意绕过分发器，必须自带等价守卫，否则是最大的 fail-open 面。"""
	session = BrowserSession(cookies={}, user_agent="")
	session._policy = POLICIES["auto"].__class__(
		name="bridge-only-probe",
		channels=(CHANNEL_BRIDGE,),
		use_stored_credentials=False,
		may_create_context=False,
		allow_browser_launch=False,
		auto_probe_cdp=False,
		failure_code="CDP_UNAVAILABLE",
		failure_message="probe",
	)

	with pytest.raises(BrowserSourceUnavailable):
		session.evaluate_js("1 + 1")
	with pytest.raises(BrowserSourceUnavailable):
		session.evaluate_js_with_chat_events("1 + 1")


def test_evaluate_js_requires_explicit_cdp_url_when_probing_is_disabled():
	"""不自动探测的来源没给 --cdp-url 时，不得静默回落到默认端点。"""
	session = BrowserSession(cookies={}, user_agent="", browser_source="stored-cookie")
	assert session._cdp_url is None

	with pytest.raises(BrowserSourceUnavailable) as excinfo:
		session.evaluate_js("1 + 1")
	assert excinfo.value.code == "CDP_UNAVAILABLE"


# ── may_create_context / auto_probe_cdp 的行为级验证 ────────────────────


def test_stored_cookie_does_not_create_context_in_an_empty_browser():
	"""空浏览器不新建 context 注入本地 Cookie —— Issue #387 的反面方案。"""
	session = BrowserSession(cookies={"wt2": "abc"}, user_agent="", browser_source="stored-cookie")
	session._pw = MagicMock()
	mock_browser = MagicMock()
	mock_browser.contexts = []
	session._pw.chromium.connect_over_cdp.return_value = mock_browser

	assert session._try_connect("ws://localhost:9222/x", explicit=True) is False
	mock_browser.new_context.assert_not_called()
	assert session._started is False


def test_existing_browser_cdp_requires_an_already_open_zhipin_page():
	"""现有浏览器来源不得在用户 context 里新建页面并主动导航。"""
	session = BrowserSession(cookies={}, user_agent="", browser_source="existing-browser")
	session._pw = MagicMock()
	mock_browser = MagicMock()
	mock_context = MagicMock()
	mock_context.pages = []
	mock_browser.contexts = [mock_context]
	session._pw.chromium.connect_over_cdp.return_value = mock_browser

	assert session._try_connect("ws://localhost:9222/x", explicit=True) is False
	mock_context.new_page.assert_not_called()
	assert session._started is False


def test_existing_browser_maps_connected_but_unusable_bridge_to_source_error():
	session = BrowserSession(cookies={}, user_agent="", browser_source="existing-browser")
	session._started = True
	session._is_bridge = True
	session._bridge_client = MagicMock()
	session._bridge_client.fetch_json.side_effect = RuntimeError("workspace unavailable")
	session._throttle.wait = MagicMock()

	with pytest.raises(BrowserSourceUnavailable) as excinfo:
		session.request("GET", "https://www.zhipin.com/wapi/zpgeek/friend/getGeekFriendList.json")

	assert excinfo.value.code == "BROWSER_SESSION_NOT_FOUND"
	assert "页面会话不可用" in str(excinfo.value)


def test_auto_keeps_bridge_request_failure_as_network_error_input():
	session = BrowserSession(cookies={}, user_agent="")
	session._started = True
	session._is_bridge = True
	session._bridge_client = MagicMock()
	session._bridge_client.fetch_json.side_effect = RuntimeError("bridge fetch failed")
	session._throttle.wait = MagicMock()

	with pytest.raises(RuntimeError, match="bridge fetch failed"):
		session.request("GET", "https://www.zhipin.com/wapi/zpgeek/friend/getGeekFriendList.json")


def test_auto_still_creates_context_in_an_empty_browser():
	"""默认路径行为不变：auto 仍会新建 context 并注入 cookie。"""
	session = BrowserSession(cookies={"wt2": "abc"}, user_agent="")
	session._pw = MagicMock()
	mock_browser = MagicMock()
	mock_browser.contexts = []
	mock_context = MagicMock()
	mock_browser.new_context.return_value = mock_context
	session._pw.chromium.connect_over_cdp.return_value = mock_browser

	assert session._try_connect("ws://localhost:9222/x", explicit=True) is True
	mock_browser.new_context.assert_called_once()
	mock_context.add_cookies.assert_called_once()


def test_auto_probe_disabled_source_only_tries_the_explicit_endpoint(monkeypatch):
	"""``auto_probe_cdp=False`` 时不碰 localhost:9222 与 DevToolsActivePort。"""
	session = BrowserSession(
		cookies={}, user_agent="", cdp_url="http://127.0.0.1:9333", browser_source="stored-cookie"
	)
	tried: list[str] = []

	def fake_connect(url: str, *, explicit: bool = True) -> bool:
		tried.append(url)
		return False

	monkeypatch.setattr(session, "_try_connect", fake_connect)
	monkeypatch.setattr(session, "_fetch_ws_url", lambda *a, **k: None)
	monkeypatch.setattr(
		BrowserSession, "_read_devtools_active_port", staticmethod(lambda: "ws://127.0.0.1:9222/should-not-be-used")
	)

	assert session._try_cdp() is False
	assert tried == ["http://127.0.0.1:9333"]


def test_auto_probes_default_endpoint_and_devtools_port(monkeypatch):
	"""默认路径行为不变：auto 仍然自动探测。"""
	session = BrowserSession(cookies={}, user_agent="")
	tried: list[str] = []

	monkeypatch.setattr(session, "_try_connect", lambda url, *, explicit=True: tried.append(url) or False)
	monkeypatch.setattr(session, "_fetch_ws_url", lambda *a, **k: None)
	monkeypatch.setattr(
		BrowserSession, "_read_devtools_active_port", staticmethod(lambda: "ws://127.0.0.1:9222/devtools/browser/x")
	)

	assert session._try_cdp() is False
	assert tried == [browser_client.CDP_DEFAULT_URL, "ws://127.0.0.1:9222/devtools/browser/x"]
