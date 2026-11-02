"""错误消息语言契约：会进入信封 error.message 的默认文案必须是中文。

`display.handle_auth_errors` 对 `RequestBudgetExceeded` 用 `str(e)` 填
`error.message`，紧邻的 `recovery_action` 却来自错误码登记表（中文）。内部抛错点
残留英文默认文案时，Agent 与真人拿到的是中英混排的错误信封。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

_SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"
_CJK = re.compile(r"[一-鿿]")


def _budget_raise_sites() -> list[tuple[Path, int, ast.expr]]:
	"""收集源码里 `raise RequestBudgetExceeded(<首参>, ...)` 的抛错点。"""
	sites: list[tuple[Path, int, ast.expr]] = []
	for path in sorted(_SOURCE_ROOT.rglob("*.py")):
		tree = ast.parse(path.read_text(encoding="utf-8"))
		for node in ast.walk(tree):
			if not (isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call)):
				continue
			func = node.exc.func
			name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
			if name != "RequestBudgetExceeded" or not node.exc.args:
				continue
			sites.append((path, node.lineno, node.exc.args[0]))
	return sites


def test_chat_action_messages_are_chinese():
	"""`_chat_action_failure_response(message=...)` 的文案会原样进入信封 error.message。

	招聘者聊天页写操作（发消息、交换联系方式）失败时直接用这句当 message；
	残留英文函数名（如 send_message_by_friend unconfirmed）会和中文的
	recovery_action / hints 混排。
	"""
	path = _SOURCE_ROOT / "boss_agent_cli" / "api" / "recruiter_client.py"
	tree = ast.parse(path.read_text(encoding="utf-8"))
	sites: list[tuple[int, str]] = []
	for node in ast.walk(tree):
		if not isinstance(node, ast.Call):
			continue
		func = node.func
		name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
		if name != "_chat_action_failure_response":
			continue
		for keyword in node.keywords:
			if keyword.arg != "message":
				continue
			value = keyword.value
			if isinstance(value, ast.Constant) and isinstance(value.value, str):
				text = value.value
			elif isinstance(value, ast.JoinedStr):
				text = "".join(
					part.value for part in value.values
					if isinstance(part, ast.Constant) and isinstance(part.value, str)
				)
			else:
				continue  # 由寄存器变量拼出来的文案（如 _EXCHANGE_NOT_AVAILABLE_MESSAGE）由各自的用例覆盖
			sites.append((node.lineno, text))
	assert len(sites) >= 3  # 扫描器有效性：当前至少发消息 / 交换联系方式两条失败路径
	offenders = [f"recruiter_client.py:{lineno}" for lineno, text in sites if not _CJK.search(text)]
	assert not offenders, f"以下聊天页失败文案会进入信封 error.message，必须中文: {offenders}"


def test_request_budget_messages_are_chinese():
	"""`RequestBudgetExceeded` 的默认文案会原样进入信封，必须是中文。

	扫描源码而不是维护手工清单：新增抛错点漏翻译时这里会失败。
	动态拼出来的文案（如熔断原因）跳过，由各自的用例覆盖。
	"""
	sites = _budget_raise_sites()
	assert len(sites) >= 5  # 扫描器有效性：抛错点只会增加，不会消失到 0
	offenders = [
		f"{path.relative_to(_SOURCE_ROOT.parent)}:{lineno}"
		for path, lineno, message in sites
		if isinstance(message, ast.Constant)
		and isinstance(message.value, str)
		and not _CJK.search(message.value)
	]
	assert not offenders, f"以下停作文案会进入信封 error.message，必须中文: {offenders}"
