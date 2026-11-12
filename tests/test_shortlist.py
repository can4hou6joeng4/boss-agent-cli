import json
import shlex
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.main import cli


def _hinted_args(data_dir, *parts):
	return ["boss", "--data-dir", str(data_dir.resolve()), *parts]


def test_shortlist_add_list_remove(tmp_path):
	runner = CliRunner()
	with patch("boss_agent_cli.commands._platform.get_platform_instance") as platform:
		result = runner.invoke(
			cli,
			[
				"--data-dir", str(tmp_path),
				"--json",
				"shortlist", "add", "sec_001", "job_001",
				"--title", "Go 开发",
				"--company", "TestCo",
				"--city", "广州",
				"--salary", "20-30K",
				"--source", "search",
				"--tags", "后端, 双休,后端",
				"--note", "优先沟通",
			],
		)
		platform.assert_not_called()
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["data"]["security_id"] == "sec_001"
	assert parsed["data"]["tags"] == ["后端", "双休"]
	assert parsed["data"]["note"] == "优先沟通"

	list_result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "shortlist", "list"])
	assert list_result.exit_code == 0
	list_parsed = json.loads(list_result.output)
	assert len(list_parsed["data"]) == 1
	assert list_parsed["data"][0]["company"] == "TestCo"
	assert list_parsed["data"][0]["tags"] == ["后端", "双休"]
	assert list_parsed["data"][0]["note"] == "优先沟通"

	remove_result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "shortlist", "remove", "sec_001", "job_001"])
	assert remove_result.exit_code == 0
	remove_parsed = json.loads(remove_result.output)
	assert remove_parsed["data"]["removed"] is True


def test_shortlist_annotate_updates_tags_and_note(tmp_path):
	runner = CliRunner()
	runner.invoke(
		cli,
		[
			"--data-dir", str(tmp_path),
			"--json",
			"shortlist", "add", "sec_001", "job_001",
			"--title", "Go 开发",
			"--company", "TestCo",
			"--tags", "后端,双休",
		],
	)

	with patch("boss_agent_cli.commands._platform.get_platform_instance") as platform:
		result = runner.invoke(
			cli,
			[
				"--data-dir", str(tmp_path),
				"--json",
				"shortlist", "annotate", "sec_001", "job_001",
				"--add-tag", "远程",
				"--add-tag", "双休",
				"--remove-tag", "双休",
				"--note", "等年终奖确认",
			],
		)
		platform.assert_not_called()

	assert result.exit_code == 0, result.output
	parsed = json.loads(result.output)
	assert parsed["data"]["updated"] is True
	assert parsed["data"]["item"]["tags"] == ["后端", "远程"]
	assert parsed["data"]["item"]["note"] == "等年终奖确认"


def test_shortlist_compare_filters_by_tag_locally(tmp_path):
	runner = CliRunner()
	for args in (
		[
			"shortlist", "add", "sec_001", "job_001",
			"--title", "Go 开发", "--company", "TestCo",
			"--city", "广州", "--salary", "20-30K",
			"--tags", "后端,远程", "--note", "优先",
		],
		[
			"shortlist", "add", "sec_002", "job_002",
			"--title", "Python 开发", "--company", "OtherCo",
			"--city", "深圳", "--salary", "25-35K",
			"--tags", "后端",
		],
	):
		result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", *args])
		assert result.exit_code == 0, result.output

	with patch("boss_agent_cli.commands._platform.get_platform_instance") as platform:
		result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "shortlist", "compare", "--tag", "远程"])
		platform.assert_not_called()

	assert result.exit_code == 0, result.output
	parsed = json.loads(result.output)
	assert parsed["data"]["tag"] == "远程"
	assert parsed["data"]["count"] == 1
	assert parsed["data"]["items"] == [
		{
			"security_id": "sec_001",
			"job_id": "job_001",
			"lid": "",
			"title": "Go 开发",
			"company": "TestCo",
			"city": "广州",
			"salary": "20-30K",
			"tags": ["后端", "远程"],
			"note": "优先",
		}
	]


def test_shortlist_repeat_add_keeps_annotation_and_reports_stored_row(tmp_path):
	"""重复 add 同一职位时保留本地标签/备注，响应回显真实存储值。

	响应原先直接回显入参：再次 add 只给城市时，标签/备注字段是空字符串，Agent
	会以为本地标注已被清空（旧实现确实会在库里清掉）。
	"""
	runner = CliRunner()
	base = ["--data-dir", str(tmp_path), "--json"]
	runner.invoke(cli, base + ["shortlist", "add", "sec_001", "job_001", "--title", "Go 开发"])
	runner.invoke(cli, base + ["shortlist", "annotate", "sec_001", "job_001", "--add-tag", "重点", "--note", "等回复"])

	result = runner.invoke(cli, base + ["shortlist", "add", "sec_001", "job_001", "--city", "广州"])
	assert result.exit_code == 0, result.output
	data = json.loads(result.output)["data"]
	assert data["tags"] == ["重点"]
	assert data["note"] == "等回复"
	assert data["title"] == "Go 开发"
	assert data["city"] == "广州"


def test_shortlist_local_flows_do_not_construct_platform_clients(tmp_path):
	runner = CliRunner()
	with (
		patch("boss_agent_cli.auth.manager.AuthManager") as auth_manager,
		patch("boss_agent_cli.api.client.BossClient") as boss_client,
		patch("boss_agent_cli.commands._platform.get_platform_instance") as platform,
	):
		commands = [
			[
				"--data-dir", str(tmp_path), "--json",
				"shortlist", "add", "sec_001", "job_001", "--tags", "后端", "--note", "本地备注",
			],
			["--data-dir", str(tmp_path), "--json", "shortlist", "list"],
			[
				"--data-dir", str(tmp_path), "--json",
				"shortlist", "annotate", "sec_001", "job_001", "--add-tag", "远程",
			],
			["--data-dir", str(tmp_path), "--json", "shortlist", "compare", "--tag", "远程"],
			["--data-dir", str(tmp_path), "--json", "shortlist", "remove", "sec_001", "job_001"],
		]
		for args in commands:
			result = runner.invoke(cli, args)
			assert result.exit_code == 0, result.output

		auth_manager.assert_not_called()
		boss_client.assert_not_called()
		platform.assert_not_called()


def test_shortlist_add_rejects_blank_identifiers(tmp_path):
	"""空 security_id / job_id 会存下一条全空候选，remove 提示也没有可执行参数。"""
	runner = CliRunner()
	for security_id, job_id in (("", ""), ("  ", "job_001"), ("sec_001", "")):
		result = runner.invoke(
			cli,
			["--data-dir", str(tmp_path), "--json", "shortlist", "add", security_id, job_id],
		)
		assert result.exit_code == 1, result.output
		parsed = json.loads(result.output)
		assert parsed["error"]["code"] == "INVALID_PARAM"
		# 空编号的恢复指引要能直接执行：编号从搜索结果里取，而不是让 Agent 猜。
		assert [shlex.split(action) for action in parsed["hints"]["next_actions"]] == [
			_hinted_args(tmp_path, "search", "<关键词>"),
		]
		assert parsed["hints"]["next_actions"][0] in parsed["error"]["recovery_action"]

	listed = json.loads(runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "shortlist", "list"]).output)
	assert listed["data"] == []


def test_shortlist_remove_and_annotate_reject_blank_identifiers(tmp_path):
	"""空标识既不会命中条目，也不该被当成「删掉了」或「条目不存在」。"""
	runner = CliRunner()
	for action in ("remove", "annotate"):
		result = runner.invoke(
			cli,
			["--data-dir", str(tmp_path), "--json", "shortlist", action, "", "  "],
		)
		assert result.exit_code == 1, result.output
		parsed = json.loads(result.output)
		assert parsed["error"]["code"] == "INVALID_PARAM", action
		# 候选池条目的编号来自本地列表：空编号时要指回 shortlist list。
		assert [shlex.split(action) for action in parsed["hints"]["next_actions"]] == [
			_hinted_args(tmp_path, "shortlist", "list"),
		]
		assert parsed["hints"]["next_actions"][0] in parsed["error"]["recovery_action"]


def test_shortlist_schema_is_exposed():
	runner = CliRunner()
	result = runner.invoke(cli, ["schema"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	shortlist = parsed["data"]["commands"]["shortlist"]
	assert "shortlist" in parsed["data"]["commands"]
	assert "annotate" in shortlist["subcommands"]
	assert "compare" in shortlist["subcommands"]
	assert "--tags" in shortlist["options"]["add"]
	assert "--note" in shortlist["options"]["add"]
	assert shortlist["options"]["add"]["--lid"]["default"] == ""
	assert "--tag" in shortlist["options"]["compare"]


def test_shortlist_annotate_missing_entry_reports_not_found(tmp_path):
	"""对不在候选池里的职位 annotate 不能返回 ok:true。

	以前是 `ok: true` + `updated: false` + `item: null`：Agent 会以为标签和备注
	已经写进去了，实际什么都没发生，提示还指向与本次操作无关的 `shortlist compare`。
	"""
	result = CliRunner().invoke(
		cli,
		[
			"--data-dir", str(tmp_path),
			"--json",
			"shortlist", "annotate", "sec_missing", "job_missing",
			"--add-tag", "重点",
			"--note", "等回复",
		],
	)

	assert result.exit_code == 1, result.output
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "SHORTLIST_ENTRY_NOT_FOUND"
	assert parsed["error"]["recovery_action"]
	assert [shlex.split(action) for action in parsed["hints"]["next_actions"]] == [
		_hinted_args(tmp_path, "shortlist", "add", "sec_missing", "job_missing"),
		_hinted_args(tmp_path, "shortlist", "list"),
	]

	listed = json.loads(
		CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "shortlist", "list"]).output
	)
	assert listed["data"] == []


def test_shortlist_list_and_compare_empty_hints_point_to_add(tmp_path):
	"""空候选池没有职位可查看 / 可标注：下一步必须是加入候选池。"""
	runner = CliRunner()

	listed = json.loads(runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "shortlist", "list"]).output)
	assert listed["data"] == []
	assert [shlex.split(action) for action in listed["hints"]["next_actions"]] == [
		_hinted_args(tmp_path, "shortlist", "add", "<security_id>", "<job_id>"),
	]

	compared = json.loads(runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "shortlist", "compare"]).output)
	assert compared["data"]["items"] == []
	assert [shlex.split(action) for action in compared["hints"]["next_actions"]] == [
		_hinted_args(tmp_path, "shortlist", "add", "<security_id>", "<job_id>"),
	]

	runner.invoke(
		cli,
		["--data-dir", str(tmp_path), "--json", "shortlist", "add", "sec_001", "job_001", "--title", "Go 开发"],
	)
	filled = json.loads(runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "shortlist", "list"]).output)
	assert [shlex.split(action) for action in filled["hints"]["next_actions"]] == [
		_hinted_args(tmp_path, "detail", "<security_id>", "--job-id", "<job_id>"),
	]


def test_shortlist_compare_tag_without_match_does_not_point_to_add(tmp_path):
	"""带 --tag 没有命中时池子里明明有条目：下一步是不带过滤的比较，不是再加一条。"""
	runner = CliRunner()
	runner.invoke(
		cli,
		["--data-dir", str(tmp_path), "--json", "shortlist", "add", "sec_001", "job_001", "--tags", "后端"],
	)

	compared = json.loads(
		runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "shortlist", "compare", "--tag", "前端"]).output
	)
	assert compared["data"]["items"] == []
	assert [shlex.split(action) for action in compared["hints"]["next_actions"]] == [
		_hinted_args(tmp_path, "shortlist", "compare"),
		_hinted_args(tmp_path, "shortlist", "list"),
	]


@pytest.mark.parametrize(
	("security_id", "job_id"),
	[("sid_fixture", "job_fixture"), ("sid fixture' [x]", 'job fixture" $value;*')],
)
def test_shortlist_add_hints_replay_list_and_remove(tmp_path, security_id, job_id):
	"""照抄后继命令要读取、删除刚加入的条目，且不拆开标识。"""
	data_dir = tmp_path / "jobs [x] team's"
	runner = CliRunner()
	added = runner.invoke(cli, ["--data-dir", str(data_dir), "--json", "shortlist", "add", security_id, job_id])
	assert added.exit_code == 0, added.output
	list_hint, remove_hint = json.loads(added.stdout)["hints"]["next_actions"]
	assert shlex.split(list_hint) == ["boss", "--data-dir", str(data_dir.resolve()), "shortlist", "list"]
	assert shlex.split(remove_hint) == [
		"boss", "--data-dir", str(data_dir.resolve()), "shortlist", "remove", security_id, job_id,
	]

	listed = runner.invoke(cli, ["--json", *shlex.split(list_hint)[1:]])
	assert listed.exit_code == 0, listed.output
	assert json.loads(listed.stdout)["data"][0]["job_id"] == job_id
	removed = runner.invoke(cli, ["--json", *shlex.split(remove_hint)[1:]])
	assert removed.exit_code == 0, removed.output
	payload = json.loads(removed.stdout)
	assert payload["data"]["removed"] is True
	assert payload["hints"]["next_actions"] == [list_hint]
	listed = runner.invoke(cli, ["--json", *shlex.split(list_hint)[1:]])
	assert listed.exit_code == 0, listed.output
	assert json.loads(listed.stdout)["data"] == []


@pytest.mark.parametrize(
	("security_id", "job_id"),
	[("sid_fixture", "job_fixture"), ("sid fixture' [x]", 'job fixture" $value;*')],
)
def test_shortlist_missing_entry_hint_creates_same_entry(tmp_path, security_id, job_id):
	"""标注失败时的 add 指引要在原目录创建同一条目，再标注才能成功。"""
	data_dir = tmp_path / "jobs [x] team's"
	runner = CliRunner()
	args = ["--data-dir", str(data_dir), "--json", "shortlist", "annotate", security_id, job_id, "--note", "优先沟通"]
	missing = runner.invoke(cli, args)
	assert missing.exit_code == 1, missing.output
	payload = json.loads(missing.stdout)
	assert payload["error"]["code"] == "SHORTLIST_ENTRY_NOT_FOUND"
	add_hint, list_hint = payload["hints"]["next_actions"]
	assert shlex.split(add_hint) == [
		"boss", "--data-dir", str(data_dir.resolve()), "shortlist", "add", security_id, job_id,
	]
	added = runner.invoke(cli, ["--json", *shlex.split(add_hint)[1:]])
	assert added.exit_code == 0, added.output
	annotated = runner.invoke(cli, args)
	assert annotated.exit_code == 0, annotated.output
	payload = json.loads(annotated.stdout)
	assert payload["data"]["item"]["note"] == "优先沟通"
	compare_hint = payload["hints"]["next_actions"][0]
	assert shlex.split(compare_hint)[:3] == ["boss", "--data-dir", str(data_dir.resolve())]
	compared = runner.invoke(cli, ["--json", *shlex.split(compare_hint)[1:]])
	assert compared.exit_code == 0, compared.output
	assert json.loads(compared.stdout)["data"]["items"][0]["job_id"] == job_id
	assert shlex.split(list_hint) == ["boss", "--data-dir", str(data_dir.resolve()), "shortlist", "list"]
