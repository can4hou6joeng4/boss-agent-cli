"""wizard 的 requested_steps 显式空数组边界（Issue #1236）。

空数组曾被 plan 层折叠成「执行 goal 的全部步骤」，把显式空子集解释成全量，
而 goal 里可能包含真实平台写操作（如 candidate_greet）。省略字段才是全量。
"""

import json

import pytest
from click.testing import CliRunner

from boss_agent_cli.main import cli
from boss_agent_cli.wizard.catalog import build_plan
from boss_agent_cli.wizard.models import WizardInput, WorkflowInputError


def _payload(**overrides):
	payload = {
		"role": "candidate",
		"platform": "zhipin",
		"goal": "greet",
		"inputs": {"security_id": "sec-1", "job_id": "job-1", "message": "您好"},
	}
	payload.update(overrides)
	return payload


def test_empty_requested_steps_is_rejected():
	with pytest.raises(WorkflowInputError, match="不能是空数组"):
		WizardInput.from_mapping(_payload(requested_steps=[]))


def test_omitted_requested_steps_still_means_full_goal():
	wizard_input = WizardInput.from_mapping(_payload())

	assert wizard_input.requested_steps == ()
	assert build_plan(wizard_input).requested_steps == ("auth_status", "candidate_greet")


def test_cli_rejects_empty_requested_steps_before_planning(tmp_path):
	result = CliRunner().invoke(
		cli,
		[
			"--data-dir",
			str(tmp_path),
			"--json",
			"wizard",
			"--input-json",
			json.dumps(_payload(requested_steps=[])),
		],
	)

	envelope = json.loads(result.output)
	assert result.exit_code == 1
	assert envelope["ok"] is False
	assert envelope["error"]["code"] == "INVALID_PARAM"
	assert "不能是空数组" in envelope["error"]["message"]
	assert envelope["hints"]["next_actions"] == ["boss schema"]
