import json
from unittest.mock import patch

from click.testing import CliRunner

from boss_agent_cli.main import cli


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
		assert parsed["hints"]["next_actions"] == ["boss search <关键词>"]
		assert "boss search <关键词>" in parsed["error"]["recovery_action"]

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
		assert parsed["hints"]["next_actions"] == ["boss shortlist list"]
		assert "boss shortlist list" in parsed["error"]["recovery_action"]


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
	assert parsed["hints"]["next_actions"] == [
		"boss shortlist add sec_missing job_missing",
		"boss shortlist list",
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
	assert listed["hints"]["next_actions"] == ["boss shortlist add <security_id> <job_id>"]

	compared = json.loads(runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "shortlist", "compare"]).output)
	assert compared["data"]["items"] == []
	assert compared["hints"]["next_actions"] == ["boss shortlist add <security_id> <job_id>"]

	runner.invoke(
		cli,
		["--data-dir", str(tmp_path), "--json", "shortlist", "add", "sec_001", "job_001", "--title", "Go 开发"],
	)
	filled = json.loads(runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "shortlist", "list"]).output)
	assert filled["hints"]["next_actions"] == ["boss detail <security_id> --job-id <job_id>"]


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
	assert compared["hints"]["next_actions"] == ["boss shortlist compare", "boss shortlist list"]
