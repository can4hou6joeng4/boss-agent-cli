"""batch-greet / greet / detail 的回归（Issue #1815 #1816 #1817）。"""

from __future__ import annotations

import json
import shlex
import sqlite3
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.main import cli


def _ctx_mock(mock_cls):
	instance = mock_cls.return_value
	instance.__enter__ = lambda self: self
	instance.__exit__ = lambda self, *a: None
	instance.unwrap_data.side_effect = lambda response: response.get("zpData") if "zpData" in response else response.get("data")
	instance.is_success.side_effect = lambda response: response.get("code", 0) in (0, 200)
	return instance


def _job(n, **overrides):
	job = {"encryptJobId": f"J{n}", "securityId": f"S{n}", "jobName": f"job{n}", "brandName": "示例公司"}
	job.update(overrides)
	return job


def _batch(jobs, *argv):
	with patch("boss_agent_cli.commands.greet.time"), patch("boss_agent_cli.commands.greet.AuthManager"), patch(
		"boss_agent_cli.commands.greet.get_platform_instance"
	) as mock_cls:
		client = _ctx_mock(mock_cls)
		client.name = "zhipin"
		client.search_jobs.return_value = {"zpData": {"jobList": jobs}}
		client.greet.return_value = {"code": 0}
		out = CliRunner().invoke(cli, ["--json", *argv])
	return out, client


def test_dry_run_rerun_protects_leading_dash_query(tmp_path):
	data_dir = tmp_path / "data dir"
	out, preview_client = _batch(
		[_job(1)], "--data-dir", str(data_dir), "batch-greet", "--dry-run", "--city", "北京", "--", "-python"
	)
	assert out.exit_code == 0, out.output
	preview_client.greet.assert_not_called()
	hint = json.loads(out.output)["hints"]["next_actions"][0]
	assert hint.endswith("--city 北京 --count 10 -- -python")
	replayed, client = _batch([_job(1)], *shlex.split(hint)[1:])
	assert replayed.exit_code == 0, replayed.output
	assert client.search_jobs.call_args.args == ("-python",)
	assert client.search_jobs.call_args.kwargs["city"] == "北京"
	client.greet.assert_called_once_with("S1", "J1")


def test_dry_run_rerun_keeps_plain_query_first(tmp_path):
	out, _ = _batch([_job(1)], "--data-dir", str(tmp_path), "batch-greet", "golang", "--dry-run")
	hint = json.loads(out.output)["hints"]["next_actions"][0]
	assert " batch-greet golang " in hint and " -- " not in hint


@pytest.mark.parametrize("bad", [
	{"securityId": {"a": 1}},
	{"encryptJobId": ["x"]},
	{"securityId": None},
	{"encryptJobId": "  "},
	{"securityId": 5},
])
def test_batch_skips_candidates_without_string_ids_and_continues(tmp_path, bad):
	out, client = _batch([_job(2, **bad), _job(2)], "--data-dir", str(tmp_path), "batch-greet", "py", "--count", "5")
	assert out.exit_code == 0, out.output
	payload = json.loads(out.output)
	assert [g["job_id"] for g in payload["data"]["greeted"]] == ["J2"]
	assert client.greet.call_count == 1
	assert client.greet.call_args.args[:2] == ("S2", "J2")


def test_dry_run_omits_invalid_candidates(tmp_path):
	out, _ = _batch([{"jobName": "noid"}, _job(2)], "--data-dir", str(tmp_path), "batch-greet", "py", "--dry-run")
	payload = json.loads(out.output)
	assert payload["data"]["count"] == 1


def test_greet_rejects_whitespace_only_message(tmp_path):
	with patch("boss_agent_cli.commands.greet.AuthManager"), patch(
		"boss_agent_cli.commands.greet.get_platform_instance"
	) as mock_cls:
		client = _ctx_mock(mock_cls)
		out = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "greet", "S1", "J1", "--message", "   "])
	assert out.exit_code == 1
	assert json.loads(out.output)["error"]["code"] == "INVALID_PARAM"
	client.greet.assert_not_called()


@pytest.mark.parametrize("cached_id", [None, b"\x00\xff", "J1"])
def test_detail_treats_blank_job_id_as_missing(tmp_path, cached_id):
	db_path = tmp_path / "cache" / "boss_agent.db"
	with CacheStore(db_path):
		pass
	if cached_id is not None:
		with sqlite3.connect(db_path) as conn:
			conn.execute(
				"INSERT INTO greet_records (account, job_id, security_id, greeted_at) VALUES (?, ?, ?, ?)",
				("", cached_id, "S1", 1),
			)
	with patch("boss_agent_cli.commands.detail.AuthManager"), patch(
		"boss_agent_cli.commands.detail.get_platform_instance"
	) as mock_cls, patch("boss_agent_cli.commands.detail._detail_via_browser") as browser:
		client = _ctx_mock(mock_cls)
		client.job_detail.return_value = {"zpData": {"jobInfo": {"jobName": "示例职位"}}}
		browser.return_value = ({"title": "示例职位"}, None)
		out = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "detail", "S1", "--job-id", "   "])
	assert out.exit_code == 0, out.output
	if isinstance(cached_id, str):
		client.job_detail.assert_called_once_with(cached_id)
		browser.assert_not_called()
	else:
		client.job_detail.assert_not_called()
		assert browser.call_args.kwargs["job_id"] == ""
