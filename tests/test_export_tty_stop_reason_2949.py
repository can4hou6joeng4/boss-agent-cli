"""终端导出只显示后继指引里的真实终止原因（Issue #2949）。"""

import io
from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner
import pytest
from rich.console import Console

from boss_agent_cli import display
from boss_agent_cli.main import cli
from boss_agent_cli.search_filters import SearchPipelineResult, SearchPipelineStats


@pytest.mark.parametrize(
	("stop_reason", "count", "expected"),
	[
		("active_target_reached", 50, "活跃度筛选可判定的范围内提前停止"),
		("page_budget_exhausted", 50, "扫描到内部页数上限"),
		("limit_reached", 1, ""),
	],
)
def test_export_file_tty_uses_single_accurate_stop_hint(
	tmp_path: Path, stop_reason: str, count: int, expected: str,
) -> None:
	stream = io.StringIO()
	output = tmp_path / "demo-jobs.csv"
	platform = MagicMock()
	platform.__enter__.return_value = platform
	platform.name = "zhipin"
	pipeline = SearchPipelineResult(
		items=[{"job_id": "demo-job", "title": "demo", "company": "demo-company"}],
		stop_reason=stop_reason,
		stats=SearchPipelineStats(),
	)
	with (
		patch("boss_agent_cli.commands.export.AuthManager"),
		patch("boss_agent_cli.commands.export.account_scope", return_value="demo-account"),
		patch("boss_agent_cli.commands.export.get_platform_instance", return_value=platform),
		patch("boss_agent_cli.commands.export.run_search_pipeline", return_value=pipeline),
		patch.object(display, "is_json_mode", return_value=False),
		patch.object(display, "console", Console(file=stream, width=300, color_system=None)),
	):
		result = CliRunner().invoke(
			cli,
			["--data-dir", str(tmp_path), "export", "demo", "--active", "online", "--count", str(count), "-o", str(output)],
		)

	assert result.exit_code == 0, result.output
	assert output.is_file()
	text = stream.getvalue()
	assert "exported" in text
	if expected:
		assert expected in text
		assert text.count("结果可能不完整") == 1
	else:
		assert "结果可能不完整" not in text
	if stop_reason == "active_target_reached":
		assert "页数上限" not in text
