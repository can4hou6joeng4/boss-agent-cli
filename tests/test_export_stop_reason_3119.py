"""向导 export 取满 count 时的停止原因必须与 CLI 一致（Issue #3119）。"""

from unittest.mock import patch

from boss_agent_cli.output import Logger
from boss_agent_cli.wizard.actions import ActionContext, DEFAULT_ACTIONS


class _Platform:
	def __enter__(self):
		return self

	def __exit__(self, *args):
		return None


class _Result:
	items = [{"job_id": "j1", "title": "Go", "company": "TestCo", "security_id": "sec_001"}]
	has_more = False
	stop_reason = "has_more_false"


def test_wizard_export_count_reached_overrides_platform_false(tmp_path):
	ctx = ActionContext(
		data_dir=tmp_path, platform="zhipin", role="candidate", logger=Logger(),
		candidate_factory=lambda *a, **k: _Platform(),
	)
	with patch("boss_agent_cli.wizard.actions.execute_candidate_search", return_value=_Result()):
		step = DEFAULT_ACTIONS["candidate_export"](
			ctx, {"query": "Go", "count": 1, "output": str(tmp_path / "out.json")}, {}
		)
	assert step.data["count"] == 1
	assert step.data["truncated"] is False
	assert step.data["stop_reason"] == "count_reached"
