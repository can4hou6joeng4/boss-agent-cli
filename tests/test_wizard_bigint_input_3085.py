"""Issue #3085：超长整数必须在输入边界拒绝，不能落到 json.dumps 抛裸 ValueError。"""

from __future__ import annotations

import sys

import pytest

from boss_agent_cli.wizard.models import WizardInput, WorkflowInputError, ensure_storable_inputs


def _digits_limit() -> int:
	limit = sys.get_int_max_str_digits()
	if limit == 0:
		pytest.skip("解释器关闭了 int 字符串位数上限")
	return limit


def test_oversized_integer_rejected_with_path() -> None:
	limit = _digits_limit()
	big = 10 ** (limit + 1)

	with pytest.raises(WorkflowInputError) as exc:
		ensure_storable_inputs({"limit": big})

	message = str(exc.value)
	assert "inputs.limit" in message
	assert len(message) < 200  # 不把超长数字字面量回显到信封


def test_oversized_negative_integer_rejected() -> None:
	limit = _digits_limit()

	with pytest.raises(WorkflowInputError):
		ensure_storable_inputs({"delta": -(10 ** (limit + 1))})


def test_oversized_integer_in_nested_list_rejected() -> None:
	limit = _digits_limit()

	with pytest.raises(WorkflowInputError) as exc:
		ensure_storable_inputs({"items": [1, {"n": 10 ** (limit + 1)}]})

	assert "inputs.items[1].n" in str(exc.value)


def test_in_limit_integer_still_accepted() -> None:
	limit = _digits_limit()

	ensure_storable_inputs({"limit": 10 ** (limit - 1), "flag": True, "small": -1})


def test_wizard_input_from_mapping_rejects_oversized_integer() -> None:
	limit = _digits_limit()

	with pytest.raises(WorkflowInputError) as exc:
		WizardInput.from_mapping({
			"role": "candidate",
			"platform": "zhipin",
			"goal": "crawl_start",
			"inputs": {"limit": 10 ** (limit + 1)},
		})

	assert "inputs.limit" in str(exc.value)
