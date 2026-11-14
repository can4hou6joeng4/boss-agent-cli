"""招聘者「回复」前端脚本的离线回归测试（Node 可用时运行）。

脚本跑在真实聊天页的 Vue editor 上，Python 侧只能拼字符串；这里用 Node 执行同一段
JS，覆盖发送前的就绪判定：慢页面轮询、持续 disabled、会话被切走、加载中，以及
多行消息的 ``<br>`` 渲染。
"""

import json
import shutil
import subprocess

import pytest

from boss_agent_cli.api.recruiter_client import _CHAT_FRONTEND_HELPERS_JS, _SEND_MESSAGE_ACTION_JS

_ACTION = f"const result = await (async () => {{{_SEND_MESSAGE_ACTION_JS}}})();"


def _node() -> str:
	node = shutil.which("node")
	if node is None:
		pytest.skip("Node required to execute the recruiter send script")
	return node


def _run(tmp_path, setup: str) -> dict:
	probe = f"""
{_CHAT_FRONTEND_HELPERS_JS}
const runs = {{ sends: 0 }};
const input = {{ innerHTML: '' }};
const args = {{ content: 'hello\\nworld', targetFriendId: 101 }};
const log = [];
{setup}
process.stdout.write(JSON.stringify({{ result, sends: runs.sends, html: input.innerHTML }}));
"""
	path = tmp_path / "send.mjs"
	path.write_text(probe)
	# 与其它 Node 回归同一预算：CI 冷启动卡顿可超过 15s（Issue #1029）。
	completed = subprocess.run([_node(), str(path)], capture_output=True, text=True, timeout=30, check=True)
	return json.loads(completed.stdout)


def test_send_waits_for_slow_editor_and_preserves_newlines(tmp_path):
	"""disabled 在 250ms 后才清除时仍要发送一次；多行内容渲染为 <br>。

	固定等待 150ms 的版本在这里会误判为「编辑器不可用」并放弃发送。
	"""
	data = _run(
		tmp_path,
		f"""
const editor = {{
	disabled: true, ['conversationLoading$']: false, uniqueId: 'u1', draft: {{}},
	conversation$: {{ friendId: 101 }},
	handleKeyup() {{ setTimeout(() => {{ this.disabled = false; }}, 250); }},
	sendText() {{ runs.sends += 1; return {{ ok: true }}; }},
}};
{_ACTION}
""",
	)

	assert data["result"]["ok"] is True
	assert data["sends"] == 1
	assert data["html"] == "hello<br>world"


def test_send_still_refuses_a_persistently_disabled_editor(tmp_path):
	"""轮询不能变成强制发送：3 秒内始终 disabled 时必须放弃，且不调用 sendText。"""
	data = _run(
		tmp_path,
		f"""
const editor = {{
	disabled: true, ['conversationLoading$']: false, uniqueId: 'u1', draft: {{}},
	conversation$: {{ friendId: 101 }},
	handleKeyup() {{}},
	sendText() {{ runs.sends += 1; return {{ ok: true }}; }},
}};
{_ACTION}
""",
	)

	assert data["result"]["ok"] is False
	assert "disabled" in data["result"]["error"]
	assert data["sends"] == 0


def test_send_refuses_when_conversation_switches_away(tmp_path):
	"""填内容到发送之间会话被切走时，必须放弃而不是发到错误的候选人。"""
	data = _run(
		tmp_path,
		f"""
const editor = {{
	disabled: false, ['conversationLoading$']: false, uniqueId: 'u1', draft: {{}},
	conversation$: {{ friendId: 202 }},
	handleKeyup() {{}},
	sendText() {{ runs.sends += 1; return {{ ok: true }}; }},
}};
{_ACTION}
""",
	)

	assert data["result"]["ok"] is False
	assert "switched away" in data["result"]["error"]
	assert data["sends"] == 0


def test_send_refuses_while_conversation_is_loading(tmp_path):
	data = _run(
		tmp_path,
		f"""
const editor = {{
	disabled: false, ['conversationLoading$']: true, uniqueId: 'u1', draft: {{}},
	conversation$: {{ friendId: 101 }},
	handleKeyup() {{}},
	sendText() {{ runs.sends += 1; return {{ ok: true }}; }},
}};
{_ACTION}
""",
	)

	assert data["result"]["ok"] is False
	assert data["sends"] == 0
