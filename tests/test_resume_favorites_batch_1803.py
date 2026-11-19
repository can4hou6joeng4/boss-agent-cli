"""resume / favorites 批次的回归（Issue #1803–#1808 的已实现部分）。"""

from __future__ import annotations

import json
import pytest
from click.testing import CliRunner

from boss_agent_cli.commands.favorites import _card_to_shortlist_item
from boss_agent_cli.main import cli
from boss_agent_cli.services.resume_parser import parse_resume


# ── #1803：后继命令对 - 开头的名字前置 -- ────────────────────


def test_resume_init_hint_runs_leading_dash_name(tmp_path):
	runner = CliRunner()
	out = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "resume", "init", "--name=-draft"])
	assert out.exit_code == 0, out.output
	hint = json.loads(out.output)["hints"]["next_actions"][0]
	assert hint.endswith("resume show -- -draft")
	replayed = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", *hint.removeprefix("boss ").split()])
	payload = json.loads(replayed.output)
	# 未登录无关：名字被 `--` 保护后 show 正常取到简历。
	assert payload["ok"] is True
	assert payload["data"]["name"] == "-draft"


def test_resume_edit_hint_puts_options_before_the_separator(tmp_path):
	runner = CliRunner()
	runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "resume", "init", "--name=-draft"])
	out = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "resume", "show", "--", "-draft"])
	hints = json.loads(out.output)["hints"]["next_actions"]
	assert any(h.endswith("resume edit --field title --value <新标题> -- -draft") for h in hints)


def test_resume_link_hint_protects_both_ids(tmp_path):
	runner = CliRunner()
	runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "resume", "init", "--name=d1"])
	out = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "resume", "link", "--", "d1", "-S1", "-J1"])
	assert out.exit_code == 0, out.output
	hints = json.loads(out.output)["hints"]["next_actions"]
	assert any(h.endswith("apply -- -S1 -J1") for h in hints)


# ── #1806：托管字段 ─────────────────────────────────────────


@pytest.mark.parametrize("field", ["name", "created_at", "updated_at"])
def test_resume_edit_rejects_managed_fields(tmp_path, field):
	runner = CliRunner()
	runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "resume", "init", "--name=d1"])
	out = runner.invoke(
		cli, ["--data-dir", str(tmp_path), "--json", "resume", "edit", "--field", field, "--value", "HELLO", "--", "d1"]
	)
	assert out.exit_code == 1, out.output
	payload = json.loads(out.output)
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert "由系统托管" in payload["error"]["message"]
	stored = json.loads((tmp_path / "resumes" / "d1.json").read_text(encoding="utf-8"))
	assert stored.get(field) != "HELLO"


# ── #1807：favorites 的类型漂移 ─────────────────────────────


@pytest.mark.parametrize("sid", [{"a": 1}, 5, True, None, ""])
def test_drifted_ids_become_empty_not_stringified(sid):
	item = _card_to_shortlist_item({"securityId": sid, "encryptJobId": "J1", "jobName": "t", "jobValidStatus": 1})
	# 空主键由 add_shortlist_batch 计入 skipped_count，不会落库。
	assert item["security_id"] == ""


def test_drifted_text_fields_degrade_to_empty():
	item = _card_to_shortlist_item(
		{"securityId": "S", "encryptJobId": "J", "jobName": {"k": 1}, "brandName": None, "cityName": 7,
		 "jobSalary": "20k", "jobLabels": [{"a": 1}, "tag", None, ""], "lid": 3, "jobValidStatus": 1}
	)
	assert item is not None
	assert item["title"] == "" and item["company"] == "" and item["city"] == ""
	assert item["tags"] == ["tag"]
	assert item["lid"] == ""


def test_valid_card_still_maps():
	item = _card_to_shortlist_item({"securityId": "S", "encryptJobId": "J", "jobName": "t", "jobValidStatus": 1, "lid": "L"})
	assert item["security_id"] == "S" and item["job_id"] == "J" and item["lid"] == "L"


# ── #1808：resume_parser 的类型归一 ─────────────────────────


def test_parser_degrades_drifted_text_fields():
	raw = {"zpData": {"geekDetailInfo": {
		"geekBaseInfo": {"name": {"x": 1}, "ageDesc": None, "degreeCategory": 3},
		"geekWorkExpList": [{"workEmphasis": ["a", "b"], "company": None}, {"workEmphasis": "a#&#b"}],
		"geekCertificationList": [{"certName": {"n": 1}}, {"certName": "PMP"}],
		"geekEduExpList": [{"degreeDesc": 0, "degreeName": 3}],
	}}}
	parsed = parse_resume(raw)
	assert parsed["basic"]["name"] == ""
	assert parsed["basic"]["degree"] == ""
	assert parsed["work_experience"][0]["company"] == ""
	assert parsed["work_experience"][0]["keywords"] == []
	assert parsed["work_experience"][1]["keywords"] == ["a", "b"]
	assert parsed["certifications"] == ["PMP"]
	assert parsed["education"][0]["degree"] == ""


def test_parser_falls_back_to_data_when_zpdata_is_null():
	parsed = parse_resume({"zpData": None, "data": {"geekDetailInfo": {"geekBaseInfo": {"name": "real"}}}})
	assert parsed["basic"]["name"] == "real"


@pytest.mark.parametrize("raw", [None, [], "x", 5])
def test_parser_returns_empty_structure_for_non_object_input(raw):
	parsed = parse_resume(raw)
	assert parsed["basic"]["name"] == ""


def test_parser_keeps_real_payload_unchanged():
	raw = {"zpData": {"geekDetailInfo": {"geekBaseInfo": {"name": "张三", "degreeCategory": "本科"}}}}
	assert parse_resume(raw)["basic"]["name"] == "张三"


def test_resume_hints_keep_positional_first_for_plain_names(tmp_path):
	runner = CliRunner()
	runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "resume", "init", "--name=plain"])
	out = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "resume", "show", "plain"])
	hints = json.loads(out.output)["hints"]["next_actions"]
	assert any(h.endswith("resume edit plain --field title --value <新标题>") for h in hints)
