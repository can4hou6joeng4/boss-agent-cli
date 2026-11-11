"""向导渲染标签与动作 / 错误码注册表的一致性守卫。

renderer 的标签表是 TTY 路径的唯一文案来源：新增 wizard 动作或错误码却漏配
标签时，界面会静默退化到「处理任务步骤」「请返回主菜单后重试」，JSON 与
Agent 路径完全看不出问题。这里把三张表的覆盖关系钉在注册表上。
"""

from __future__ import annotations

import ast
from pathlib import Path

from boss_agent_cli.schema.error_codes import ERROR_CODES
from boss_agent_cli.wizard.actions import DEFAULT_ACTIONS
from boss_agent_cli.wizard.renderer import (
	ERROR_LABELS,
	RECOVERY_LABELS,
	STEP_LABELS,
	recovery_message,
)

ROOT = Path(__file__).resolve().parents[1]
WIZARD_SOURCE_ROOT = ROOT / "src" / "boss_agent_cli" / "wizard"


def _wizard_error_code_literals() -> set[str]:
	"""收集 wizard 包内 AST 字符串常量里出现的已登记错误码。"""
	codes: set[str] = set()
	for path in sorted(WIZARD_SOURCE_ROOT.rglob("*.py")):
		tree = ast.parse(path.read_text(encoding="utf-8"))
		for node in ast.walk(tree):
			if isinstance(node, ast.Constant) and isinstance(node.value, str):
				if node.value in ERROR_CODES:
					codes.add(node.value)
	return codes


def test_step_labels_cover_every_registered_wizard_action():
	missing = set(DEFAULT_ACTIONS) - set(STEP_LABELS)
	orphan = set(STEP_LABELS) - set(DEFAULT_ACTIONS)
	assert missing == set(), f"动作没有步骤标签，TTY 会显示「处理任务步骤」：{sorted(missing)}"
	assert orphan == set(), f"步骤标签没有对应动作：{sorted(orphan)}"


def test_error_and_recovery_labels_are_registered_and_symmetric():
	label_codes = set(ERROR_LABELS)
	assert label_codes == set(RECOVERY_LABELS), (
		f"有错误文案但缺恢复文案：{sorted(label_codes - set(RECOVERY_LABELS))}；"
		f"有恢复文案但缺错误文案：{sorted(set(RECOVERY_LABELS) - label_codes)}"
	)
	assert label_codes <= set(ERROR_CODES), (
		f"标签引用了未登记的错误码：{sorted(label_codes - set(ERROR_CODES))}"
	)


def test_every_error_code_reachable_from_the_wizard_has_a_tty_label():
	referenced = _wizard_error_code_literals()
	assert len(referenced) >= 30, f"只收集到 {len(referenced)} 个错误码，AST 扫描可能失效"
	assert referenced - set(ERROR_LABELS) == set(), (
		f"wizard 可产生的错误码缺少 TTY 文案：{sorted(referenced - set(ERROR_LABELS))}"
	)
	assert referenced - set(RECOVERY_LABELS) == set(), (
		f"wizard 可产生的错误码缺少恢复文案：{sorted(referenced - set(RECOVERY_LABELS))}"
	)


def test_expired_auth_and_stopped_workflow_do_not_fall_back_to_generic_retry():
	"""这两类状态此前会落到「请返回主菜单后重试」，真人照做仍会失败。"""
	auth_recovery = recovery_message("AUTH_EXPIRED") or ""
	assert "重新登录" in auth_recovery
	assert auth_recovery != "请返回主菜单后重试。"

	stopped_recovery = recovery_message("WORKFLOW_STOPPED") or ""
	assert "恢复已有任务" in stopped_recovery
	assert stopped_recovery != "请返回主菜单后重试。"
