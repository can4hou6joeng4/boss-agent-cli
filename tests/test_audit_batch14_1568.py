"""Issue #1568：agent train 的 --dry-run / --live 不得同时生效；Issue #1570：schema 描述与实现对齐。"""

import json
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.main import cli
from boss_agent_cli.schema.data import SCHEMA_DATA


def _train(tmp_path, *flags):
	return CliRunner().invoke(
		cli,
		["--data-dir", str(tmp_path), "--json", "agent", "train", *flags],
	)


@pytest.mark.parametrize(
	"flags",
	[("--dry-run", "--live"), ("--live", "--dry-run")],
	ids=["dry-run-then-live", "live-then-dry-run"],
)
def test_agent_train_rejects_conflicting_flags_before_running(tmp_path, flags):
	with patch("boss_agent_cli.commands.agent._run_agent") as run:
		result = _train(tmp_path, *flags)
		run.assert_not_called()

	assert result.exit_code == 1, result.output
	payload = json.loads(result.output)
	assert payload["ok"] is False
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert "--dry-run" in payload["error"]["message"]
	assert "--live" in payload["error"]["message"]
	assert payload["hints"]["next_actions"]
	assert payload["hints"]["operator_actions"]


@pytest.mark.parametrize(
	("flags", "expected_dry_run"),
	[((), True), (("--dry-run",), True), (("--live",), False)],
	ids=["default", "dry-run", "live"],
)
def test_agent_train_effective_dry_run_flag(tmp_path, flags, expected_dry_run):
	with (
		patch("boss_agent_cli.commands.agent._run_agent") as run,
		patch("boss_agent_cli.commands.agent._report_payload", return_value={}),
	):
		result = _train(tmp_path, *flags)

	assert result.exit_code == 0, result.output
	assert run.call_args.kwargs["dry_run"] is expected_dry_run


# ── Issue #1570：schema 描述与实现对齐 ──────────────────────────────


def test_clean_schema_lists_crawl_targets():
	description = SCHEMA_DATA["commands"]["clean"]["options"]["--privacy"]["description"]
	assert "crawl 任务" in description


def test_export_output_schema_distinguishes_html_stdout():
	description = SCHEMA_DATA["commands"]["export"]["options"]["-o, --output"]["description"]
	assert "HTML 不指定时直出 stdout" in description
	assert "data.content" in description


def test_search_welfare_schema_documents_five_page_cap():
	description = SCHEMA_DATA["commands"]["search"]["options"]["--welfare"]["description"]
	assert "最多自动翻页 5 页" in description
	assert "直到找到匹配结果" not in description


def test_hr_resume_schema_documents_required_fields():
	options = SCHEMA_DATA["commands"]["hr"]["options"]["resume"]
	geek_id = options["args"][0]["description"]
	assert "查看简历时必填" in geek_id
	assert "同时提供" in geek_id
	assert "查看简历时必填" in options["--job-id"]["description"]
	assert "查看简历时必填" in options["--security-id"]["description"]
	assert "--friend-id" in options["--exchange"]["description"]


def test_resume_init_schema_does_not_claim_platform_import():
	description = SCHEMA_DATA["commands"]["resume"]["options"]["init"]
	assert "BOSS 直聘简历" not in json.dumps(description, ensure_ascii=False)
	assert "默认模板" in json.dumps(description, ensure_ascii=False)


def test_crawl_configure_schema_does_not_claim_hook_support():
	description = SCHEMA_DATA["commands"]["crawl"]["options"]["configure"]["description"]
	assert "Hook" not in description
	assert "Chrome" in description
	assert "预算" in description
