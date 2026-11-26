"""Issue #2941：crawl 新建任务的恢复提示必须带必填 --city。"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from click.testing import CliRunner

from boss_agent_cli.cache.store import CacheRecordInvalid
from boss_agent_cli.commands import crawl
from boss_agent_cli.main import cli


def test_run_not_found_hint_includes_required_city(tmp_path: Any) -> None:
	result = CliRunner().invoke(
		cli, ["--json", "--data-dir", str(tmp_path), "crawl", "status", "missing-run"]
	)

	assert result.exit_code == 1, result.output
	envelope = json.loads(result.output)
	assert envelope["error"]["code"] == "CRAWL_RUN_NOT_FOUND"
	assert any("crawl start <query> --city <城市>" in action for action in envelope["hints"]["next_actions"])


def test_corrupt_run_hint_includes_required_city(tmp_path: Any) -> None:
	ctx = SimpleNamespace(obj={"data_dir": tmp_path, "platform": "zhipin", "role": "candidate"})
	exc = CacheRecordInvalid("run", "run-1", "checkpoint", "bad json")

	with patch("boss_agent_cli.commands.crawl.handle_error_output") as handle:
		crawl._report_corrupt_run(ctx, "crawl status", "run-1", exc)

	hints = handle.call_args.kwargs["hints"]
	assert hints["next_actions"][0].endswith("crawl start <关键词> --city <城市>")
