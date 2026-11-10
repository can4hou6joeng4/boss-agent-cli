"""Static code hygiene checks that ruff / mypy do not cover."""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

_TERMINATORS = (ast.Return, ast.Raise, ast.Continue, ast.Break)


def _unreachable_statements() -> list[str]:
	"""列出同一语句块里紧跟在 return/raise/continue/break 之后的语句。"""
	found: list[str] = []
	for path in sorted((ROOT / "src").rglob("*.py")):
		tree = ast.parse(path.read_text(encoding="utf-8"))
		for node in ast.walk(tree):
			for field in ("body", "orelse", "finalbody"):
				body = getattr(node, field, None)
				if not isinstance(body, list):
					continue
				for stmt, nxt in zip(body, body[1:]):
					if isinstance(stmt, _TERMINATORS):
						found.append(f"{path.relative_to(ROOT)}:{nxt.lineno}")
	return found


def test_no_unreachable_statements() -> None:
	"""终止语句之后不能再有语句。

	``ResumeStore.list_all_with_corrupt`` 曾在 ``return results, corrupt`` 之后
	留着一行 ``return results``：它永远执行不到，却会让函数签名在不同路径上返回
	list 或 tuple，也让读代码的人以为列表分支还有别的出口。ruff 的规则集
	（E4/E7/E9/F）与 mypy 都不报这类问题。
	"""
	assert _unreachable_statements() == []


def _stdout_echo_calls() -> list[str]:
	"""列出未声明 ``err`` 的 ``click.echo`` 调用（``output.py`` 的信封出口除外）。"""
	found: list[str] = []
	envelope_path = ROOT / "src" / "boss_agent_cli" / "output.py"
	for path in sorted((ROOT / "src").rglob("*.py")):
		if path == envelope_path:
			continue
		tree = ast.parse(path.read_text(encoding="utf-8"))
		for node in ast.walk(tree):
			if not (
				isinstance(node, ast.Call)
				and isinstance(node.func, ast.Attribute)
				and node.func.attr == "echo"
				and isinstance(node.func.value, ast.Name)
				and node.func.value.id == "click"
			):
				continue
			if not any(keyword.arg == "err" for keyword in node.keywords):
				found.append(f"{path.relative_to(ROOT)}:{node.lineno}")
	return found


def test_no_stdout_click_echo_outside_envelope_output() -> None:
	"""stdout 只能由 ``output.py`` 的七键信封写入。

	``commands/platforms.py`` 的 TTY render 曾用裸 ``click.echo`` 把表格写进
	stdout，管道场景因 ``is_json_mode`` 走 JSON 分支而侥幸正常；这里静态拦住
	所有未声明 ``err`` 的 ``click.echo``，日志 / 表格只允许走 stderr。
	"""
	assert _stdout_echo_calls() == []
