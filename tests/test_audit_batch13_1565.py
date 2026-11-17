"""Issue #1565：shortlist 空参数不能静默变成「没传」；Issue #1566：schema 描述与实现对齐。"""

import json
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.main import cli
from boss_agent_cli.schema.data import SCHEMA_DATA
from boss_agent_cli.schema.tool_catalog import build_tool_catalog


def _invoke(tmp_path, *args):
	return CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", *args])


def _add(tmp_path, tags="后端", note=""):
	result = _invoke(
		tmp_path,
		"shortlist", "add", "sec_1", "job_1",
		"--title", "Go 开发",
		"--tags", tags,
		"--note", note,
	)
	assert result.exit_code == 0, result.output
	return result


# ── Issue #1565：compare 的显式空 --tag ─────────────────────────────


@pytest.mark.parametrize("value", ["", "   ", "\t"])
def test_shortlist_compare_rejects_blank_tag_before_reading_cache(tmp_path, value):
	_add(tmp_path)
	with patch("boss_agent_cli.commands.shortlist.CacheStore") as cache:
		result = _invoke(tmp_path, "shortlist", "compare", "--tag", value)
		cache.assert_not_called()

	assert result.exit_code == 1, result.output
	payload = json.loads(result.output)
	assert payload["ok"] is False
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert "--tag" in payload["error"]["message"]
	assert payload["data"] is None
	assert [action for action in payload["hints"]["next_actions"] if "shortlist compare" in action]


def test_shortlist_compare_still_strips_valid_tag(tmp_path):
	_add(tmp_path)
	result = _invoke(tmp_path, "shortlist", "compare", "--tag", " 后端 ")
	assert result.exit_code == 0, result.output
	payload = json.loads(result.output)
	assert payload["data"]["tag"] == "后端"
	assert payload["data"]["count"] == 1


# ── Issue #1565：annotate 的空标签与空操作 ──────────────────────────


@pytest.mark.parametrize(
	("option", "value"),
	[
		("--add-tag", ""),
		("--add-tag", "   "),
		("--add-tag", " , "),
		("--remove-tag", ""),
		("--remove-tag", " , "),
	],
)
def test_shortlist_annotate_rejects_blank_tag_before_reading_cache(tmp_path, option, value):
	_add(tmp_path, tags="后端,双休")
	with patch("boss_agent_cli.commands.shortlist.CacheStore") as cache:
		result = _invoke(tmp_path, "shortlist", "annotate", "sec_1", "job_1", option, value)
		cache.assert_not_called()

	assert result.exit_code == 1, result.output
	payload = json.loads(result.output)
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert "标签值不能为空" in payload["error"]["message"]

	# 参数错误没有写库：原标签保持不变。
	listed = json.loads(_invoke(tmp_path, "shortlist", "list").output)
	assert listed["data"][0]["tags"] == ["后端", "双休"]


def test_shortlist_annotate_rejects_no_changes(tmp_path):
	_add(tmp_path)
	with patch("boss_agent_cli.commands.shortlist.CacheStore") as cache:
		result = _invoke(tmp_path, "shortlist", "annotate", "sec_1", "job_1")
		cache.assert_not_called()

	assert result.exit_code == 1, result.output
	payload = json.loads(result.output)
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert "至少提供" in payload["error"]["message"]


def test_shortlist_annotate_note_empty_still_clears(tmp_path):
	_add(tmp_path, note="原备注")
	result = _invoke(tmp_path, "shortlist", "annotate", "sec_1", "job_1", "--note", "")
	assert result.exit_code == 0, result.output
	payload = json.loads(result.output)
	assert payload["data"]["updated"] is True
	assert payload["data"]["item"]["note"] == ""


# ── Issue #1566：boss schema 与实现对齐 ─────────────────────────────


def test_watch_run_schema_documents_name_all_mutex():
	run = SCHEMA_DATA["commands"]["watch"]["options"]["run"]
	assert run["args"][0]["required"] is False
	assert "必须且只能" in run["args"][0]["description"]
	assert "--all" in run["args"][0]["description"]
	assert "不能与" in run["--all"]["description"]


def test_crawl_schema_declares_empty_string_and_hook_pairing():
	options = SCHEMA_DATA["commands"]["crawl"]["options"]
	for subcommand, option in (
		("configure", "--chrome-path"),
		("run", "--chrome-path"),
		("run", "--hook-dir"),
		("start", "--hook-dir"),
	):
		assert options[subcommand][option]["minLength"] == 1, f"{subcommand} {option}"
	assert "成对" in options["run"]["--hook-dir"]["description"]
	assert "成对" in options["start"]["--hook-dir"]["description"]


def test_agent_crawl_schema_documents_mutual_exclusion():
	crawl = SCHEMA_DATA["commands"]["agent"]["options"]["crawl"]
	assert "必须且只能" in crawl["--run-id"]["description"]
	assert "同时提供 --city" in crawl["--query"]["description"]
	assert "会被拒绝" in crawl["--city"]["description"]
	assert "会被拒绝" in crawl["--pages"]["description"]
	assert "会被拒绝" in crawl["--with-detail"]["description"]


def test_shortlist_schema_documents_blank_tag_guards():
	options = SCHEMA_DATA["commands"]["shortlist"]["options"]
	assert options["compare"]["--tag"]["minLength"] == 1
	assert "至少提供" in options["annotate"]["description"]
	assert options["annotate"]["--add-tag"]["minLength"] == 1
	assert options["annotate"]["--remove-tag"]["minLength"] == 1


def test_shortlist_mcp_tools_declare_nonempty_strings():
	catalog = {tool.name: tool.input_schema for tool in build_tool_catalog()}
	compare = catalog["boss_shortlist_compare"]["properties"]["tag"]
	assert compare["minLength"] == 1
	annotate = catalog["boss_shortlist_annotate"]["properties"]
	assert annotate["add_tags"]["items"]["minLength"] == 1
	assert annotate["remove_tags"]["items"]["minLength"] == 1
