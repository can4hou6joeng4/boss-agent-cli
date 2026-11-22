"""审计批次 121：向导输入的对象键只接受字符串（Issue #2485）。

`_ensure_storable_inputs` 原来允许数字 / 布尔 / null 键（#2420 的理由是
`json.dumps` 会自动转成字符串），但混合类型键在 `sort_keys=True` 排序时抛
`TypeError`：`WizardInput.from_mapping` 通过校验后，`WorkflowStore.create`
落库才失败，命令层只能兜成 `INTERNAL_ERROR`。

契约：入口一次拒绝所有非字符串键，给出确定、可复现的 `INVALID_PARAM` 诊断；
纯字符串键（含嵌套对象）维持通过。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from boss_agent_cli.wizard.catalog import build_plan
from boss_agent_cli.wizard.models import WizardInput, WorkflowInputError, _ensure_storable_inputs
from boss_agent_cli.wizard.store import WorkflowStore

_MAPPING = {"role": "candidate", "platform": "zhipin", "goal": "job_search"}


@pytest.mark.parametrize("key", [1, True, 1.5, None], ids=["int", "bool", "float", "null"])
def test_non_string_keys_are_rejected(key):
	with pytest.raises(WorkflowInputError, match="对象键只支持字符串"):
		_ensure_storable_inputs({"inputs": {key: "x"}})


def test_mixed_keys_are_rejected_before_sort_keys_runs():
	# 混合键正是 json.dumps(sort_keys=True) 抛 TypeError 的输入。
	with pytest.raises(WorkflowInputError, match="对象键只支持字符串"):
		_ensure_storable_inputs({"query": "x", 1: "y"})


def test_from_mapping_rejects_non_string_keys_with_entry_error():
	with pytest.raises(WorkflowInputError, match="对象键只支持字符串"):
		WizardInput.from_mapping({**_MAPPING, "inputs": {"query": "x", 1: "y"}})


def test_string_keys_and_nested_containers_still_pass(tmp_path: Path):
	inputs = {
		"query": "Python",
		"filters": {"city": ["北京", "上海"], "pages": 2, "fresh": True, "note": None},
	}
	plan_input = WizardInput.from_mapping({**_MAPPING, "inputs": inputs})
	assert dict(plan_input.inputs) == inputs

	# 通过校验的输入必须能真正落库（不触发 json.dumps 的键排序错误）。
	with WorkflowStore(tmp_path) as store:
		store.create("run-keys", build_plan(plan_input))
		assert store.get_checkpoint("run-keys") is not None
