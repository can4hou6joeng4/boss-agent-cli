import json
from unittest.mock import patch, MagicMock
from types import SimpleNamespace

from click.testing import CliRunner
from boss_agent_cli.main import cli


def _ctx_mock(mock_cls):
	"""Make mock class support context manager (with ... as client:)."""
	instance = mock_cls.return_value
	instance.__enter__ = lambda self: self
	instance.__exit__ = lambda self, *a: None
	return instance


def test_schema_command():
	runner = CliRunner()
	result = runner.invoke(cli, ["schema"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["command"] == "schema"
	assert "search" in parsed["data"]["commands"]
	assert "login" in parsed["data"]["commands"]
	assert "greet" in parsed["data"]["commands"]
	assert "AUTH_EXPIRED" in parsed["data"]["error_codes"]
	assert "stdout" in parsed["data"]["conventions"]


@patch("boss_agent_cli.commands.status.AuthManager")
def test_status_not_logged_in(mock_auth_cls):
	mock_auth_cls.return_value.check_status.return_value = None
	runner = CliRunner()
	result = runner.invoke(cli, ["status"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["ok"] is False
	assert parsed["error"]["code"] == "AUTH_REQUIRED"


@patch("boss_agent_cli.commands.search.CacheStore")
@patch("boss_agent_cli.commands.search.AuthManager")
@patch("boss_agent_cli.commands.search.BossClient")
def test_search_invalid_city(mock_client_cls, mock_auth_cls, mock_cache_cls):
	runner = CliRunner()
	result = runner.invoke(cli, ["search", "golang", "--city", "火星"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "INVALID_PARAM"


@patch("boss_agent_cli.commands.greet.BossClient")
@patch("boss_agent_cli.commands.greet.AuthManager")
@patch("boss_agent_cli.commands.greet.CacheStore")
def test_greet_already_greeted(mock_cache_cls, mock_auth_cls, mock_client_cls):
	_ctx_mock(mock_cache_cls)
	mock_cache_cls.return_value.is_greeted.return_value = True
	runner = CliRunner()
	result = runner.invoke(cli, ["greet", "sec_001", "job_001"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "ALREADY_GREETED"


@patch("boss_agent_cli.commands.greet.time")
@patch("boss_agent_cli.commands.greet.BossClient")
@patch("boss_agent_cli.commands.greet.AuthManager")
@patch("boss_agent_cli.commands.greet.CacheStore")
def test_batch_greet_dry_run(mock_cache_cls, mock_auth_cls, mock_client_cls, mock_time):
	mock_cache = _ctx_mock(mock_cache_cls)
	mock_cache.is_greeted.return_value = False
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.search_jobs.return_value = {
		"zpData": {
			"jobList": [
				{
					"encryptJobId": "j1",
					"jobName": "Golang",
					"brandName": "ByteDance",
					"salaryDesc": "30K",
					"cityName": "北京",
					"jobExperience": "3-5年",
					"jobDegree": "本科",
					"bossName": "张",
					"bossTitle": "CTO",
					"bossOnline": True,
					"securityId": "sec_1",
				},
			],
		},
	}
	runner = CliRunner()
	result = runner.invoke(cli, ["batch-greet", "golang", "--dry-run"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["data"]["dry_run"] is True
	assert parsed["data"]["count"] == 1


def test_cities_command():
	runner = CliRunner()
	result = runner.invoke(cli, ["cities"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["command"] == "cities"
	assert parsed["data"]["count"] == 40
	assert "广州" in parsed["data"]["cities"]
	assert "北京" in parsed["data"]["cities"]
	assert parsed["hints"] is not None


def test_schema_includes_new_commands():
	runner = CliRunner()
	result = runner.invoke(cli, ["schema"])
	parsed = json.loads(result.output)
	commands = parsed["data"]["commands"]
	assert "recommend" in commands
	assert "export" in commands
	assert "cities" in commands
	assert "me" in commands
	assert "show" in commands
	assert "history" in commands
	assert "chat" in commands
	assert "interviews" in commands
	assert "logout" in commands
	assert "doctor" in commands
	assert "watch" in commands
	assert "pipeline" in commands
	assert "follow-up" in commands
	assert "apply" in commands
	assert len(commands) >= 23


@patch("boss_agent_cli.commands.doctor.extract_cookies")
@patch("boss_agent_cli.commands.doctor.httpx.get")
@patch("boss_agent_cli.commands.doctor.probe_cdp")
@patch("boss_agent_cli.commands.doctor.AuthManager")
def test_doctor_command(mock_auth_cls, mock_probe_cdp, mock_httpx_get, mock_extract_cookies):
	mock_auth_cls.return_value.check_status.return_value = None
	mock_probe_cdp.return_value = None
	mock_extract_cookies.return_value = None
	mock_httpx_get.return_value = MagicMock(status_code=200)

	runner = CliRunner()
	result = runner.invoke(cli, ["doctor"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["command"] == "doctor"
	assert parsed["data"]["check_count"] >= 8
	assert "checks" in parsed["data"]
	assert any(item["name"] == "network" for item in parsed["data"]["checks"])
	assert any(item["name"] == "cookie_extract" for item in parsed["data"]["checks"])
	assert any(item["name"] == "auth_token_quality" for item in parsed["data"]["checks"])
	assert parsed["hints"]["next_actions"]


@patch("boss_agent_cli.commands.doctor.extract_cookies")
@patch("boss_agent_cli.commands.doctor.httpx.get")
@patch("boss_agent_cli.commands.doctor.probe_cdp")
@patch("boss_agent_cli.commands.doctor.AuthManager")
def test_doctor_with_partial_token_quality_warn(mock_auth_cls, mock_probe_cdp, mock_httpx_get, mock_extract_cookies):
	mock_auth_cls.return_value.check_status.return_value = {"cookies": {"wt2": "ok"}, "stoken": ""}
	mock_probe_cdp.return_value = None
	mock_extract_cookies.return_value = {"cookies": {"wt2": "ok"}}
	mock_httpx_get.return_value = MagicMock(status_code=200)

	runner = CliRunner()
	result = runner.invoke(cli, ["doctor"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	quality = next(item for item in parsed["data"]["checks"] if item["name"] == "auth_token_quality")
	assert quality["status"] == "warn"
	assert "stoken 缺失" in quality["detail"]
	assert any("boss status" in action for action in parsed["hints"]["next_actions"])


@patch("boss_agent_cli.commands.recommend.CacheStore")
@patch("boss_agent_cli.commands.recommend.BossClient")
@patch("boss_agent_cli.commands.recommend.AuthManager")
def test_recommend_success(mock_auth_cls, mock_client_cls, mock_cache_cls):
	mock_cache = _ctx_mock(mock_cache_cls)
	mock_cache.is_greeted.return_value = False
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.recommend_jobs.return_value = {
		"zpData": {
			"hasMore": True,
			"jobList": [
				{
					"encryptJobId": "j1",
					"jobName": "Go 开发",
					"brandName": "TestCo",
					"salaryDesc": "20K",
					"cityName": "广州",
					"areaDistrict": "天河区",
					"jobExperience": "3-5年",
					"jobDegree": "本科",
					"skills": ["Golang"],
					"welfareList": ["五险一金"],
					"brandIndustry": "互联网",
					"brandScaleName": "100-499人",
					"brandStageName": "A轮",
					"bossName": "李",
					"bossTitle": "HR",
					"bossOnline": True,
					"securityId": "sec_r1",
				},
			],
		},
	}
	runner = CliRunner()
	result = runner.invoke(cli, ["recommend"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert len(parsed["data"]) == 1
	assert parsed["data"][0]["company"] == "TestCo"
	assert parsed["pagination"]["has_more"] is True
	assert parsed["hints"] is not None


@patch("boss_agent_cli.index_cache.save_index", side_effect=PermissionError("readonly"))
@patch("boss_agent_cli.commands.recommend.CacheStore")
@patch("boss_agent_cli.commands.recommend.BossClient")
@patch("boss_agent_cli.commands.recommend.AuthManager")
def test_recommend_ignores_index_cache_write_failure(mock_auth_cls, mock_client_cls, mock_cache_cls, mock_save_index):
	mock_cache = _ctx_mock(mock_cache_cls)
	mock_cache.is_greeted.return_value = False
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.recommend_jobs.return_value = {
		"zpData": {
			"hasMore": False,
			"jobList": [
				{
					"encryptJobId": "j1",
					"jobName": "Go 开发",
					"brandName": "TestCo",
					"salaryDesc": "20K",
					"cityName": "广州",
					"areaDistrict": "天河区",
					"jobExperience": "3-5年",
					"jobDegree": "本科",
					"skills": ["Golang"],
					"welfareList": ["五险一金"],
					"brandIndustry": "互联网",
					"brandScaleName": "100-499人",
					"brandStageName": "A轮",
					"bossName": "李",
					"bossTitle": "HR",
					"bossOnline": True,
					"securityId": "sec_r1",
				},
			],
		},
	}
	runner = CliRunner()
	result = runner.invoke(cli, ["recommend"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	mock_save_index.assert_called_once()


@patch("boss_agent_cli.index_cache.save_index", side_effect=PermissionError("readonly"))
@patch("boss_agent_cli.commands.search.run_search_pipeline")
@patch("boss_agent_cli.commands.search.CacheStore")
@patch("boss_agent_cli.commands.search.AuthManager")
@patch("boss_agent_cli.commands.search.BossClient")
def test_search_ignores_index_cache_write_failure(mock_client_cls, mock_auth_cls, mock_cache_cls, mock_pipeline, mock_save_index):
	mock_cache = _ctx_mock(mock_cache_cls)
	mock_cache.get_search.return_value = None
	_ctx_mock(mock_client_cls)
	mock_pipeline.return_value = SimpleNamespace(
		items=[{
			"job_id": "j1",
			"title": "Go 开发",
			"company": "TestCo",
			"salary": "20K",
			"city": "广州",
			"experience": "3-5年",
			"education": "本科",
			"security_id": "sec_001",
			"greeted": False,
		}],
		has_more=False,
		total=1,
		stats=SimpleNamespace(
			pages_scanned=1,
			jobs_seen=1,
			jobs_prefiltered=0,
			detail_checks=0,
		),
	)
	runner = CliRunner()
	result = runner.invoke(cli, ["search", "golang"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	mock_save_index.assert_called_once()


@patch("boss_agent_cli.commands.export.BossClient")
@patch("boss_agent_cli.commands.export.AuthManager")
def test_export_to_stdout(mock_auth_cls, mock_client_cls):
	mock_client =	_ctx_mock(mock_client_cls)
	mock_client.search_jobs.return_value = {
		"zpData": {
			"hasMore": False,
			"jobList": [
				{
					"encryptJobId": "j1",
					"jobName": "Go 开发",
					"brandName": "TestCo",
					"salaryDesc": "20K",
					"cityName": "广州",
					"areaDistrict": "天河区",
					"jobExperience": "3-5年",
					"jobDegree": "本科",
					"skills": ["Golang"],
					"welfareList": ["五险一金"],
					"brandIndustry": "互联网",
					"brandScaleName": "100-499人",
					"brandStageName": "A轮",
					"bossName": "李",
					"bossTitle": "HR",
					"bossOnline": True,
					"securityId": "sec_e1",
				},
			],
		},
	}
	runner = CliRunner()
	result = runner.invoke(cli, ["export", "golang", "--count", "1"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["data"]["count"] == 1
	assert parsed["data"]["format"] == "csv"
	assert len(parsed["data"]["jobs"]) == 1


def _make_friend_item(name, brand, relation_type, last_ts):
	"""构造 friend_list API 返回的单条记录"""
	return {
		"name": name,
		"title": "HR",
		"brandName": brand,
		"lastMsg": "你好",
		"lastTime": "今天 10:00",
		"lastTS": last_ts,
		"securityId": f"sec_{name}",
		"encryptJobId": f"job_{name}",
		"unreadMsgCount": 0,
		"relationType": relation_type,
		"friendSource": 0,
		"sourceType": 0,
		"lastMessageInfo": {"status": 2},
	}


@patch("boss_agent_cli.commands.chat.BossClient")
@patch("boss_agent_cli.commands.chat.AuthManager")
def test_chat_from_boss_filter(mock_auth_cls, mock_client_cls):
	"""--from boss 只返回 relationType=1 的记录"""
	import time
	now_ms = int(time.time() * 1000)
	mock_auth_cls.return_value.check_status.return_value = {"cookies": {}}
	_ctx_mock(mock_client_cls)
	mock_client_cls.return_value.friend_list.return_value = {
		"zpData": {
			"result": [
				_make_friend_item("张HR", "阿里", 1, now_ms),
				_make_friend_item("我自己", "腾讯", 2, now_ms),
				_make_friend_item("李HR", "字节", 1, now_ms),
			],
		},
	}
	runner = CliRunner()
	result = runner.invoke(cli, ["chat", "--from", "boss"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert len(parsed["data"]) == 2
	assert all(f["initiated_by"] == "对方主动" for f in parsed["data"])


@patch("boss_agent_cli.commands.chat.BossClient")
@patch("boss_agent_cli.commands.chat.AuthManager")
def test_chat_days_filter(mock_auth_cls, mock_client_cls):
	"""--days 3 只返回最近 3 天的记录"""
	import time
	now_ms = int(time.time() * 1000)
	old_ms = now_ms - 5 * 86400 * 1000  # 5 天前
	mock_auth_cls.return_value.check_status.return_value = {"cookies": {}}
	_ctx_mock(mock_client_cls)
	mock_client_cls.return_value.friend_list.return_value = {
		"zpData": {
			"result": [
				_make_friend_item("新HR", "阿里", 1, now_ms),
				_make_friend_item("旧HR", "腾讯", 1, old_ms),
			],
		},
	}
	runner = CliRunner()
	result = runner.invoke(cli, ["chat", "--days", "3"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert len(parsed["data"]) == 1
	assert parsed["data"][0]["brand_name"] == "阿里"


@patch("boss_agent_cli.commands.chat.BossClient")
@patch("boss_agent_cli.commands.chat.AuthManager")
def test_chat_combined_filter(mock_auth_cls, mock_client_cls):
	"""--from boss --days 3 组合筛选"""
	import time
	now_ms = int(time.time() * 1000)
	old_ms = now_ms - 5 * 86400 * 1000
	mock_auth_cls.return_value.check_status.return_value = {"cookies": {}}
	_ctx_mock(mock_client_cls)
	mock_client_cls.return_value.friend_list.return_value = {
		"zpData": {
			"result": [
				_make_friend_item("新HR", "阿里", 1, now_ms),       # 命中
				_make_friend_item("新我", "腾讯", 2, now_ms),       # from 不匹配
				_make_friend_item("旧HR", "字节", 1, old_ms),       # days 不匹配
			],
		},
	}
	runner = CliRunner()
	result = runner.invoke(cli, ["chat", "--from", "boss", "--days", "3"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert len(parsed["data"]) == 1
	assert parsed["data"][0]["brand_name"] == "阿里"
	assert parsed["data"][0]["initiated_by"] == "对方主动"


@patch("boss_agent_cli.commands.chat.BossClient")
@patch("boss_agent_cli.commands.chat.AuthManager")
def test_chat_export_md(mock_auth_cls, mock_client_cls, tmp_path):
	"""--export md 导出包含 security_id 和 diff 摘要"""
	import time
	now_ms = int(time.time() * 1000)
	mock_auth_cls.return_value.check_status.return_value = {"cookies": {}}
	_ctx_mock(mock_client_cls)
	mock_client_cls.return_value.friend_list.return_value = {
		"zpData": {
			"result": [
				_make_friend_item("张HR", "阿里", 1, now_ms),
				_make_friend_item("我自己", "腾讯", 2, now_ms),
			],
		},
	}
	out_file = str(tmp_path / "chat.md")
	runner = CliRunner()
	result = runner.invoke(cli, [
		"--data-dir", str(tmp_path),
		"chat", "--export", "md", "-o", out_file,
	])
	assert result.exit_code == 0
	with open(out_file, encoding="utf-8") as f:
		content = f.read()
	assert "security_id" in content  # 折叠映射表中包含
	assert "sec_张HR" in content    # 完整 sid 在映射表中
	assert "S1" in content          # 主表用短编号
	assert "BOSS 直聘沟通列表" in content
	assert "对方主动" in content
	# 快照应已保存
	snapshot_dir = tmp_path / "chat-history"
	assert snapshot_dir.exists()
	json_files = list(snapshot_dir.glob("*.json"))
	assert len(json_files) == 1


@patch("boss_agent_cli.commands.chat.BossClient")
@patch("boss_agent_cli.commands.chat.AuthManager")
def test_chat_export_csv(mock_auth_cls, mock_client_cls, tmp_path):
	"""--export csv 导出 CSV 格式"""
	import time
	now_ms = int(time.time() * 1000)
	mock_auth_cls.return_value.check_status.return_value = {"cookies": {}}
	_ctx_mock(mock_client_cls)
	mock_client_cls.return_value.friend_list.return_value = {
		"zpData": {
			"result": [
				_make_friend_item("张HR", "阿里", 1, now_ms),
			],
		},
	}
	out_file = str(tmp_path / "chat.csv")
	runner = CliRunner()
	result = runner.invoke(cli, [
		"--data-dir", str(tmp_path),
		"chat", "--export", "csv", "-o", out_file,
	])
	assert result.exit_code == 0
	with open(out_file, encoding="utf-8") as f:
		content = f.read()
	assert "name,title,brand_name" in content
	assert "张HR" in content


@patch("boss_agent_cli.commands.chat.BossClient")
@patch("boss_agent_cli.commands.chat.AuthManager")
def test_chat_export_json_default_path(mock_auth_cls, mock_client_cls, tmp_path):
	"""--export json 不指定 -o 时自动保存到 export_dir"""
	import time
	now_ms = int(time.time() * 1000)
	mock_auth_cls.return_value.check_status.return_value = {"cookies": {}}
	_ctx_mock(mock_client_cls)
	mock_client_cls.return_value.friend_list.return_value = {
		"zpData": {
			"result": [
				_make_friend_item("张HR", "阿里", 1, now_ms),
			],
		},
	}
	# 写 config 让 export_dir 指向 tmp_path 下
	export_dir = tmp_path / "exports"
	config_path = tmp_path / "config.json"
	config_path.write_text(json.dumps({"export_dir": str(export_dir)}))

	runner = CliRunner()
	result = runner.invoke(cli, [
		"--data-dir", str(tmp_path),
		"chat", "--export", "json",
	])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["data"]["format"] == "json"
	# 文件应已写入 export_dir
	import datetime
	today = datetime.date.today().isoformat()
	expected = export_dir / f"沟通列表-{today}.json"
	assert expected.exists()
	with open(expected, encoding="utf-8") as f:
		items = json.load(f)
	assert items[0]["security_id"] == "sec_张HR"


@patch("boss_agent_cli.commands.chat.BossClient")
@patch("boss_agent_cli.commands.chat.AuthManager")
def test_chat_snapshot_diff(mock_auth_cls, mock_client_cls, tmp_path):
	"""第二次导出时 diff 能检测新增条目"""
	import time
	now_ms = int(time.time() * 1000)

	# 先手动写入一份"昨天"的快照
	import datetime
	snapshot_dir = tmp_path / "chat-history"
	snapshot_dir.mkdir(parents=True)
	yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
	prev_snapshot = [
		{"name": "张HR", "brand_name": "阿里", "security_id": "sec_张HR",
		 "unread": 0, "last_time": "昨天"},
	]
	with open(snapshot_dir / f"{yesterday}.json", "w") as f:
		json.dump(prev_snapshot, f)

	# 现在 API 返回多了一条
	mock_auth_cls.return_value.check_status.return_value = {"cookies": {}}
	_ctx_mock(mock_client_cls)
	mock_client_cls.return_value.friend_list.return_value = {
		"zpData": {
			"result": [
				_make_friend_item("张HR", "阿里", 1, now_ms),
				_make_friend_item("新HR", "字节", 1, now_ms),
			],
		},
	}
	out_file = str(tmp_path / "chat.md")
	runner = CliRunner()
	result = runner.invoke(cli, [
		"--data-dir", str(tmp_path),
		"chat", "--export", "md", "-o", out_file,
	])
	assert result.exit_code == 0
	with open(out_file, encoding="utf-8") as f:
		content = f.read()
	assert "新增 1 条" in content
	assert "NEW" in content


# ── 负面路径 / 安全测试 ──────────────────────────────────────────


def test_csv_formula_injection_sanitized(tmp_path):
	"""CSV 导出时以 =+@- 开头的值应被前置单引号"""
	from boss_agent_cli.commands.chat import _sanitize_csv_cell
	assert _sanitize_csv_cell("=cmd|' /C calc'!A0") == "'=cmd|' /C calc'!A0"
	assert _sanitize_csv_cell("+SUM(A1:A2)") == "'+SUM(A1:A2)"
	assert _sanitize_csv_cell("-1+2") == "'-1+2"
	assert _sanitize_csv_cell("@risk") == "'@risk"
	assert _sanitize_csv_cell("正常文本") == "正常文本"
	assert _sanitize_csv_cell("") == ""


def test_md_escape_pipe_and_newline():
	"""Markdown 表格中管道符和换行应被转义"""
	from boss_agent_cli.commands.chat import _escape_md_cell
	assert _escape_md_cell("消息|含管道符") == "消息\\|含管道符"
	assert _escape_md_cell("多行\n消息") == "多行 消息"
	assert _escape_md_cell("正常") == "正常"


@patch("boss_agent_cli.commands.chat.BossClient")
@patch("boss_agent_cli.commands.chat.AuthManager")
def test_chat_export_none_fields(mock_auth_cls, mock_client_cls, tmp_path):
	"""API 返回 None 字段时不应 crash"""
	import time
	now_ms = int(time.time() * 1000)
	mock_auth_cls.return_value.check_status.return_value = {"cookies": {}}
	_ctx_mock(mock_client_cls)
	mock_client_cls.return_value.friend_list.return_value = {
		"zpData": {
			"result": [{
				"name": None,
				"title": None,
				"brandName": None,
				"lastMsg": None,
				"lastTime": "今天",
				"lastTS": now_ms,
				"securityId": "sec_null",
				"encryptJobId": None,
				"unreadMsgCount": None,
				"relationType": 1,
				"lastMessageInfo": {"status": None},
			}],
		},
	}
	out_file = str(tmp_path / "chat.md")
	runner = CliRunner()
	result = runner.invoke(cli, [
		"--data-dir", str(tmp_path),
		"chat", "--export", "md", "-o", out_file,
	])
	assert result.exit_code == 0


@patch("boss_agent_cli.commands.chat.BossClient")
@patch("boss_agent_cli.commands.chat.AuthManager")
def test_chat_export_unknown_relation_type(mock_auth_cls, mock_client_cls, tmp_path):
	"""未知 relationType 应渲染到「未知」分组，不被丢弃"""
	import time
	now_ms = int(time.time() * 1000)
	mock_auth_cls.return_value.check_status.return_value = {"cookies": {}}
	_ctx_mock(mock_client_cls)
	mock_client_cls.return_value.friend_list.return_value = {
		"zpData": {
			"result": [
				{**_make_friend_item("张HR", "阿里", 1, now_ms), "relationType": 99},
			],
		},
	}
	out_file = str(tmp_path / "chat.md")
	runner = CliRunner()
	result = runner.invoke(cli, [
		"--data-dir", str(tmp_path),
		"chat", "--export", "md", "-o", out_file,
	])
	assert result.exit_code == 0
	with open(out_file, encoding="utf-8") as f:
		content = f.read()
	assert "未知" in content
	assert "S1" in content  # 应该被渲染到


def test_snapshot_corrupted_structure(tmp_path):
	"""快照文件结构损坏时应安全降级，不 crash"""
	from boss_agent_cli.commands.chat import _load_snapshot
	from boss_agent_cli.output import Logger
	logger = Logger("error")

	# 非数组
	bad_file = tmp_path / "bad.json"
	bad_file.write_text('{"not": "a list"}')
	assert _load_snapshot(str(bad_file), logger) is None

	# 数组含非 dict
	bad_file2 = tmp_path / "bad2.json"
	bad_file2.write_text('[1, "string", {"security_id": "ok"}]')
	result = _load_snapshot(str(bad_file2), logger)
	assert result == [{"security_id": "ok"}]


@patch("boss_agent_cli.commands.chat.BossClient")
@patch("boss_agent_cli.commands.chat.AuthManager")
def test_chat_snapshot_page_merge(mock_auth_cls, mock_client_cls, tmp_path):
	"""同天不同页的快照应合并，而非覆盖"""
	import datetime
	import time
	now_ms = int(time.time() * 1000)

	# 模拟第一次 page=1 的快照
	snapshot_dir = tmp_path / "chat-history"
	snapshot_dir.mkdir(parents=True)
	today = datetime.date.today().isoformat()
	page1_data = [
		{"name": "P1_HR", "brand_name": "公司A", "security_id": "sid_a", "unread": 0},
	]
	with open(snapshot_dir / f"{today}.json", "w") as f:
		json.dump(page1_data, f)

	# 现在 API 返回 page=2 的不同记录
	mock_auth_cls.return_value.check_status.return_value = {"cookies": {}}
	_ctx_mock(mock_client_cls)
	mock_client_cls.return_value.friend_list.return_value = {
		"zpData": {
			"result": [
				_make_friend_item("P2_HR", "公司B", 1, now_ms),
			],
		},
	}
	runner = CliRunner()
	result = runner.invoke(cli, [
		"--data-dir", str(tmp_path),
		"chat", "--export", "json", "-o", str(tmp_path / "out.json"),
	])
	assert result.exit_code == 0

	# 验证快照包含两页的数据
	with open(snapshot_dir / f"{today}.json") as f:
		merged = json.load(f)
	sids = {item["security_id"] for item in merged}
	assert "sid_a" in sids      # page 1 保留
	assert "sec_P2_HR" in sids  # page 2 新增


@patch("boss_agent_cli.commands.chat.BossClient")
@patch("boss_agent_cli.commands.chat.AuthManager")
def test_chat_export_html(mock_auth_cls, mock_client_cls, tmp_path):
	"""--export html 导出 HTML 格式，包含表格、分组和映射表"""
	import time
	now_ms = int(time.time() * 1000)
	mock_auth_cls.return_value.check_status.return_value = {"cookies": {}}
	_ctx_mock(mock_client_cls)
	mock_client_cls.return_value.friend_list.return_value = {
		"zpData": {
			"result": [
				_make_friend_item("张HR", "阿里", 1, now_ms),
				_make_friend_item("我自己", "腾讯", 2, now_ms),
			],
		},
	}
	out_file = str(tmp_path / "chat.html")
	runner = CliRunner()
	result = runner.invoke(cli, [
		"--data-dir", str(tmp_path),
		"chat", "--export", "html", "-o", out_file,
	])
	assert result.exit_code == 0
	with open(out_file, encoding="utf-8") as f:
		content = f.read()
	assert "<!DOCTYPE html>" in content
	assert "BOSS 直聘沟通列表" in content
	assert "对方主动" in content
	assert "S1" in content  # 编号
	assert "sec_张HR" in content  # 映射表中的 security_id
	assert "阿里" in content
	assert "<table>" in content


@patch("boss_agent_cli.commands.chat.BossClient")
@patch("boss_agent_cli.commands.chat.AuthManager")
def test_chat_export_html_xss_prevention(mock_auth_cls, mock_client_cls, tmp_path):
	"""HTML 导出应转义特殊字符，防止 XSS"""
	import time
	now_ms = int(time.time() * 1000)
	mock_auth_cls.return_value.check_status.return_value = {"cookies": {}}
	xss_item = _make_friend_item("<script>alert(1)</script>", "公司&名", 1, now_ms)
	xss_item["lastMsg"] = '<img onerror="alert(1)">'
	_ctx_mock(mock_client_cls)
	mock_client_cls.return_value.friend_list.return_value = {
		"zpData": {"result": [xss_item]},
	}
	out_file = str(tmp_path / "chat_xss.html")
	runner = CliRunner()
	result = runner.invoke(cli, [
		"--data-dir", str(tmp_path),
		"chat", "--export", "html", "-o", out_file,
	])
	assert result.exit_code == 0
	with open(out_file, encoding="utf-8") as f:
		content = f.read()
	# 原始 HTML 标签不能出现（已被转义为实体）
	assert "<script>" not in content
	assert '<img onerror' not in content
	# 转义后的实体应该存在
	assert "&lt;script&gt;" in content
	assert "&amp;名" in content
