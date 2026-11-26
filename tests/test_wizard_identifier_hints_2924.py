"""Issue #2924：漂移的内部标识不能变成不可执行的 hints.next_actions。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from boss_agent_cli.commands import wizard


def _ctx(tmp_path: Path) -> SimpleNamespace:
	return SimpleNamespace(obj={"platform": "zhipin", "role": "candidate", "data_dir": tmp_path})


def _stopped_run(inner: Any) -> dict[str, Any]:
	return {
		"run_id": "wrn-stopped",
		"status": "stopped",
		"steps": [{
			"step_name": "s",
			"status": "stopped",
			"result": {"inner_run_id": inner, "data": {"run_id": 123}},
			"error": None,
		}],
	}


def test_container_inner_run_id_is_not_turned_into_a_command(tmp_path: Path) -> None:
	run = _stopped_run({"nested": [1, 2]})

	assert wizard._workflow_inner_run_id(run) == ""
	hints = wizard._workflow_hints(_ctx(tmp_path), run)

	assert all("crawl resume" not in command for command in hints["next_actions"])
	assert any(command.endswith(" wizard") for command in hints["next_actions"])
	assert any(command.endswith("wizard --status wrn-stopped") for command in hints["next_actions"])


def test_string_inner_run_id_still_produces_crawl_resume(tmp_path: Path) -> None:
	run = _stopped_run("crawl-42")

	hints = wizard._workflow_hints(_ctx(tmp_path), run)

	assert any("crawl resume crawl-42" in command for command in hints["next_actions"])


def test_container_busy_run_id_does_not_produce_status_hint(tmp_path: Path) -> None:
	ctx = _ctx(tmp_path)
	error = {
		"code": "WORKFLOW_BUSY",
		"message": "busy",
		"recoverable": True,
		"recovery_action": "wait",
		"details": {"run_id": {"a": 1}},
	}

	assert wizard._busy_hints(ctx, error) is None


def test_string_busy_run_id_still_produces_status_hint(tmp_path: Path) -> None:
	ctx = _ctx(tmp_path)
	error = {
		"code": "CRAWL_BUSY",
		"message": "busy",
		"recoverable": True,
		"recovery_action": "wait",
		"details": {"run_id": "crawl-42"},
	}

	hints = wizard._busy_hints(ctx, error)

	assert hints is not None
	assert any("crawl status crawl-42" in command for command in hints["next_actions"])
