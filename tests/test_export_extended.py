"""Tests for boss export 命令 - 扩展覆盖。

补齐 export.py 的三种格式、文件输出、公式注入防护、分页循环、空数据等分支。
"""

import csv
import json
import shlex
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner
from boss_agent_cli.main import cli

_DEFAULT_DATA_DIR = str((Path.home() / ".boss-agent").resolve())


def _ctx_mock(mock_cls):
	instance = mock_cls.return_value
	instance.__enter__ = lambda self: self
	instance.__exit__ = lambda self, *a: None
	instance.name = "zhipin"
	instance.unwrap_data.side_effect = lambda response: (
		response.get("zpData") if "zpData" in response else response.get("data")
	)
	instance.is_success.side_effect = lambda response: response.get("code", 0) in (0, 200)
	return instance


def _make_raw_job(
	name: str = "Go 开发", skills: list | None = None, welfare: list | None = None, security_id: str = "sec_x"
) -> dict:
	return {
		"encryptJobId": f"j_{security_id}",
		"jobName": name,
		"brandName": "TestCo",
		"salaryDesc": "20K",
		"cityName": "广州",
		"areaDistrict": "天河区",
		"jobExperience": "3-5年",
		"jobDegree": "本科",
		"skills": skills if skills is not None else ["Golang"],
		"welfareList": welfare if welfare is not None else ["五险一金"],
		"brandIndustry": "互联网",
		"brandScaleName": "100-499人",
		"brandStageName": "A轮",
		"bossName": "李",
		"bossTitle": "HR",
		"bossOnline": True,
		"securityId": security_id,
	}


def _api_response(jobs: list[dict], has_more: bool = False) -> dict:
	return {"zpData": {"hasMore": has_more, "jobList": jobs}}


# ── 文件输出 · CSV ────────────────────────────────────────────────────


@patch("boss_agent_cli.commands.export.get_platform_instance")
@patch("boss_agent_cli.commands.export.AuthManager")
def test_export_output_path_expands_tilde_and_creates_parent(mock_auth_cls, mock_client_cls, tmp_path: Path, monkeypatch):
	"""MCP 子进程调用不经 shell，`~` 不会被展开；-o 必须自己展开并建父目录。"""
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.search_jobs.return_value = _api_response([_make_raw_job("Go")])
	monkeypatch.setenv("HOME", str(tmp_path))
	monkeypatch.setenv("USERPROFILE", str(tmp_path))

	result = CliRunner().invoke(cli, ["export", "golang", "--count", "1", "-o", "~/nested/jobs.csv"])

	assert result.exit_code == 0
	payload = json.loads(result.output)
	expected = tmp_path / "nested" / "jobs.csv"
	assert payload["data"]["path"] == str(expected)
	assert expected.exists()


@patch("boss_agent_cli.commands.export.get_platform_instance")
@patch("boss_agent_cli.commands.export.AuthManager")
def test_export_csv_to_file(mock_auth_cls, mock_client_cls, tmp_path: Path):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.search_jobs.return_value = _api_response([_make_raw_job("Go")])

	out_path = tmp_path / "jobs.csv"
	runner = CliRunner()
	result = runner.invoke(cli, ["export", "golang", "--count", "1", "-o", str(out_path)])

	assert result.exit_code == 0
	assert out_path.exists()
	content = out_path.read_text(encoding="utf-8")
	assert "title,company,salary" in content
	assert "Go" in content
	# list 字段应被转成逗号分隔
	assert "Golang" in content
	# JSON 信封里应该说明已导出
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["data"]["count"] == 1
	assert parsed["data"]["format"] == "csv"
	assert parsed["data"]["path"] == str(out_path)


@patch("boss_agent_cli.commands.export.get_platform_instance")
@patch("boss_agent_cli.commands.export.AuthManager")
def test_export_csv_formula_injection_sanitized(mock_auth_cls, mock_client_cls, tmp_path: Path):
	"""CSV 公式注入防护：以 =+@- 开头的值应前置单引号。"""
	mock_client = _ctx_mock(mock_client_cls)
	evil_job = _make_raw_job(name='=HYPERLINK("https://evil")')
	evil_job["brandName"] = "+7060035"
	mock_client.search_jobs.return_value = _api_response([evil_job])

	out_path = tmp_path / "evil.csv"
	runner = CliRunner()
	result = runner.invoke(cli, ["export", "test", "--count", "1", "-o", str(out_path)])
	assert result.exit_code == 0
	# 读取 CSV 检查首字符被加单引号
	with open(out_path, encoding="utf-8") as f:
		reader = csv.DictReader(f)
		rows = list(reader)
	assert rows[0]["title"].startswith("'=")
	assert rows[0]["company"].startswith("'+")


@patch("boss_agent_cli.commands.export.get_platform_instance")
@patch("boss_agent_cli.commands.export.AuthManager")
def test_export_csv_empty_result_writes_empty_file(mock_auth_cls, mock_client_cls, tmp_path: Path):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.search_jobs.return_value = _api_response([])

	out_path = tmp_path / "empty.csv"
	runner = CliRunner()
	result = runner.invoke(cli, ["export", "ghost-query", "-o", str(out_path)])
	assert result.exit_code == 0
	assert out_path.exists()
	assert out_path.read_text(encoding="utf-8") == ""


# ── 文件输出 · JSON ──────────────────────────────────────────────────


@patch("boss_agent_cli.commands.export.get_platform_instance")
@patch("boss_agent_cli.commands.export.AuthManager")
def test_export_json_to_file(mock_auth_cls, mock_client_cls, tmp_path: Path):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.search_jobs.return_value = _api_response(
		[
			_make_raw_job("Go 1", security_id="s1"),
			_make_raw_job("Go 2", security_id="s2"),
		]
	)

	out_path = tmp_path / "jobs.json"
	runner = CliRunner()
	result = runner.invoke(cli, ["export", "golang", "--count", "2", "--format", "json", "-o", str(out_path)])

	assert result.exit_code == 0
	assert out_path.exists()
	data = json.loads(out_path.read_text(encoding="utf-8"))
	assert len(data) == 2
	assert data[0]["title"] == "Go 1"
	assert data[1]["security_id"] == "[REDACTED]"
	assert data[1]["boss_name"] == "[REDACTED]"


@patch("boss_agent_cli.commands.export.get_platform_instance")
@patch("boss_agent_cli.commands.export.AuthManager")
def test_export_json_include_private_keeps_routing_fields(mock_auth_cls, mock_client_cls, tmp_path: Path):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.search_jobs.return_value = _api_response([_make_raw_job("Go", security_id="s1")])

	out_path = tmp_path / "jobs-private.json"
	runner = CliRunner()
	result = runner.invoke(
		cli,
		["export", "golang", "--count", "1", "--format", "json", "--include-private", "-o", str(out_path)],
	)

	assert result.exit_code == 0
	data = json.loads(out_path.read_text(encoding="utf-8"))
	assert data[0]["security_id"] == "s1"
	assert data[0]["boss_name"] == "李"


@patch("boss_agent_cli.commands.export.get_platform_instance")
@patch("boss_agent_cli.commands.export.AuthManager")
def test_export_internship_filter_keeps_only_verified_response_type(mock_auth_cls, mock_client_cls, tmp_path: Path):
	mock_client = _ctx_mock(mock_client_cls)
	intern = _make_raw_job("产品实习生", security_id="intern")
	intern.update({
		"jobType": 4,
		"daysPerWeekDesc": "4天/周",
		"leastMonthDesc": "3个月",
	})
	non_intern = _make_raw_job("接受实习生", security_id="non_intern")
	non_intern["jobType"] = 5
	mock_client.search_jobs.return_value = _api_response([non_intern, intern])

	out_path = tmp_path / "internships.json"
	result = CliRunner().invoke(
		cli,
		[
			"export", "产品", "--job-type", "实习", "--count", "2",
			"--format", "json", "--include-private", "-o", str(out_path),
		],
	)

	assert result.exit_code == 0
	data = json.loads(out_path.read_text(encoding="utf-8"))
	assert [job["security_id"] for job in data] == ["intern"]
	assert data[0]["raw_job_type"] == 4
	assert data[0]["employment_type"] == "实习"
	assert data[0]["days_per_week"] == "4天/周"
	assert data[0]["least_month"] == "3个月"


@patch("boss_agent_cli.commands.export.get_platform_instance")
@patch("boss_agent_cli.commands.export.AuthManager")
def test_export_internship_filter_continues_after_rejected_page(mock_auth_cls, mock_client_cls, tmp_path: Path):
	mock_client = _ctx_mock(mock_client_cls)
	page1 = [_make_raw_job(f"非实习 {index}", security_id=f"other_{index}") for index in range(15)]
	for job in page1:
		job["jobType"] = 5
	page2 = [
		{**_make_raw_job("产品实习生", security_id="intern_1"), "jobType": 4},
		{**_make_raw_job("运营实习生", security_id="intern_2"), "jobType": 4},
	]

	def search_side_effect(query, **filters):
		if filters.get("page") == 1:
			return _api_response(page1, has_more=True)
		return _api_response(page2, has_more=False)

	mock_client.search_jobs.side_effect = search_side_effect
	out_path = tmp_path / "internships-paginated.json"

	result = CliRunner().invoke(
		cli,
		[
			"export", "产品", "--job-type", "实习", "--count", "2",
			"--format", "json", "--include-private", "-o", str(out_path),
		],
	)

	assert result.exit_code == 0
	data = json.loads(out_path.read_text(encoding="utf-8"))
	assert [job["security_id"] for job in data] == ["intern_1", "intern_2"]
	assert mock_client.search_jobs.call_count == 2


# ── 文件输出 · HTML ──────────────────────────────────────────────────


@patch("boss_agent_cli.commands.export.get_platform_instance")
@patch("boss_agent_cli.commands.export.AuthManager")
def test_export_html_to_file(mock_auth_cls, mock_client_cls, tmp_path: Path):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.search_jobs.return_value = _api_response(
		[
			_make_raw_job("Full-Stack", skills=["Python", "React"], welfare=["双休", "五险"]),
		]
	)

	out_path = tmp_path / "jobs.html"
	runner = CliRunner()
	result = runner.invoke(cli, ["export", "fullstack", "--count", "1", "--format", "html", "-o", str(out_path)])

	assert result.exit_code == 0
	assert out_path.exists()
	content = out_path.read_text(encoding="utf-8")
	assert "<!DOCTYPE html>" in content
	assert "Full-Stack" in content
	assert "TestCo" in content
	assert "20K" not in content
	assert "薪资" not in content
	assert "李" not in content
	assert "招聘者" not in content
	# 技能和福利应各自带标签样式
	assert 'class="tag sk"' in content
	assert 'class="tag wf"' in content
	assert "Python" in content
	assert "双休" in content
	# 共 1 条应显示
	assert "共 1 条" in content


@patch("boss_agent_cli.commands.export.get_platform_instance")
@patch("boss_agent_cli.commands.export.AuthManager")
def test_export_html_without_output_writes_html_to_stdout(mock_auth_cls, mock_client_cls):
	"""`--format html` 不指定 -o 时应直出 HTML，而不是退化成职位记录数组。"""
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.search_jobs.return_value = _api_response([_make_raw_job("Full-Stack", skills=["Python"])])

	runner = CliRunner()
	result = runner.invoke(cli, ["export", "fullstack", "--count", "1", "--format", "html"])

	assert result.exit_code == 0
	assert result.output.lstrip().startswith("<!DOCTYPE html>")
	assert "Full-Stack" in result.output
	assert "TestCo" in result.output
	assert "20K" not in result.output
	# 直出路径下 stdout 是报表本身，不是 JSON 信封。
	with pytest.raises(json.JSONDecodeError):
		json.loads(result.output)


@patch("boss_agent_cli.commands.export.get_platform_instance")
@patch("boss_agent_cli.commands.export.AuthManager")
def test_export_html_with_json_flag_returns_envelope(mock_auth_cls, mock_client_cls):
	"""--json 是硬契约：HTML 正文要进 data.content，stdout 只留信封。"""
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.search_jobs.return_value = _api_response([_make_raw_job("Full-Stack", skills=["Python"])])

	runner = CliRunner()
	result = runner.invoke(cli, ["--json", "export", "fullstack", "--count", "1", "--format", "html"])

	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["data"]["format"] == "html"
	assert parsed["data"]["count"] == 1
	assert parsed["data"]["private_fields"] == "omitted"
	content = parsed["data"]["content"]
	assert content.lstrip().startswith("<!DOCTYPE html>")
	assert "Full-Stack" in content
	assert "20K" not in content
	assert parsed["data"]["bytes"] == len(content.encode("utf-8"))


@patch("boss_agent_cli.commands.export.get_platform_instance")
@patch("boss_agent_cli.commands.export.AuthManager")
def test_export_html_include_private_still_omits_private_fields(mock_auth_cls, mock_client_cls, tmp_path: Path):
	"""HTML 文件是可分享报表，即使显式 include_private 也不写路由/招聘者私有字段。"""
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.search_jobs.return_value = _api_response(
		[
			_make_raw_job(
				"Full-Stack", skills=["Python", "React"], welfare=["双休", "五险"], security_id="sec_private"
			),
		]
	)

	out_path = tmp_path / "jobs-private.html"
	runner = CliRunner()
	result = runner.invoke(
		cli,
		["export", "fullstack", "--count", "1", "--format", "html", "--include-private", "-o", str(out_path)],
	)

	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["data"]["private_fields"] == "omitted"
	content = out_path.read_text(encoding="utf-8")
	assert "Full-Stack" in content
	assert "TestCo" in content
	assert "20K" not in content
	assert "薪资" not in content
	assert "李" not in content
	assert "sec_private" not in content
	assert "j_sec_private" not in content
	assert "招聘者" not in content


@patch("boss_agent_cli.commands.export.JobItem.from_api")
@patch("boss_agent_cli.commands.export.get_platform_instance")
@patch("boss_agent_cli.commands.export.AuthManager")
def test_export_html_to_file_uses_public_api_projection(mock_auth_cls, mock_client_cls, mock_from_api, tmp_path: Path):
	"""HTML 文件导出不经过包含薪资和路由字段的 JobItem.to_dict 路径。"""
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.search_jobs.return_value = _api_response(
		[
			_make_raw_job("Full-Stack", skills=["Python"], welfare=["双休"], security_id="sec_private"),
		]
	)
	mock_from_api.side_effect = AssertionError("HTML export should not build full JobItem")

	out_path = tmp_path / "jobs-public.html"
	runner = CliRunner()
	result = runner.invoke(cli, ["export", "fullstack", "--count", "1", "--format", "html", "-o", str(out_path)])

	assert result.exit_code == 0
	content = out_path.read_text(encoding="utf-8")
	assert "Full-Stack" in content
	assert "TestCo" in content
	assert "20K" not in content
	assert "sec_private" not in content
	assert "j_sec_private" not in content
	assert mock_from_api.call_count == 0


@patch("boss_agent_cli.commands.export.get_platform_instance")
@patch("boss_agent_cli.commands.export.AuthManager")
def test_export_html_empty_result(mock_auth_cls, mock_client_cls, tmp_path: Path):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.search_jobs.return_value = _api_response([])

	out_path = tmp_path / "empty.html"
	runner = CliRunner()
	result = runner.invoke(cli, ["export", "ghost", "--format", "html", "-o", str(out_path)])
	assert result.exit_code == 0
	content = out_path.read_text(encoding="utf-8")
	assert "无数据" in content


@patch("boss_agent_cli.commands.export.get_platform_instance")
@patch("boss_agent_cli.commands.export.AuthManager")
def test_export_html_escapes_user_content(mock_auth_cls, mock_client_cls, tmp_path: Path):
	"""HTML 输出必须转义用户数据避免 XSS。"""
	mock_client = _ctx_mock(mock_client_cls)
	xss_job = _make_raw_job(name="<script>alert(1)</script>")
	mock_client.search_jobs.return_value = _api_response([xss_job])

	out_path = tmp_path / "xss.html"
	runner = CliRunner()
	result = runner.invoke(cli, ["export", "xss", "--format", "html", "-o", str(out_path)])
	assert result.exit_code == 0
	content = out_path.read_text(encoding="utf-8")
	# 原始 <script> 不得出现在内容里（data 部分应被 escape）
	# body 里 <script>...</script> 不应作为真实标签
	assert "<script>alert(1)</script>" not in content
	assert "&lt;script&gt;" in content  # 应被转义


# ── 分页循环 ──────────────────────────────────────────────────────────


@patch("boss_agent_cli.commands.export.get_platform_instance")
@patch("boss_agent_cli.commands.export.AuthManager")
def test_export_paginates_until_count_reached(mock_auth_cls, mock_client_cls, tmp_path: Path):
	"""当 count > 15 时应翻页直到达量或无 hasMore。"""
	mock_client = _ctx_mock(mock_client_cls)

	page1_jobs = [_make_raw_job(f"Job {i}", security_id=f"p1_{i}") for i in range(15)]
	page2_jobs = [_make_raw_job(f"Job {i}", security_id=f"p2_{i}") for i in range(10)]

	def search_side_effect(query, city=None, salary=None, page=1):
		if page == 1:
			return _api_response(page1_jobs, has_more=True)
		if page == 2:
			return _api_response(page2_jobs, has_more=False)
		return _api_response([])

	mock_client.search_jobs.side_effect = search_side_effect

	out_path = tmp_path / "bulk.json"
	runner = CliRunner()
	result = runner.invoke(cli, ["export", "bulk", "--count", "20", "--format", "json", "-o", str(out_path)])
	assert result.exit_code == 0
	data = json.loads(out_path.read_text(encoding="utf-8"))
	assert len(data) == 20
	# 应调用过 2 次 API
	assert mock_client.search_jobs.call_count == 2


@patch("boss_agent_cli.commands.export.get_platform_instance")
@patch("boss_agent_cli.commands.export.AuthManager")
def test_export_supports_url_and_multiselect_filters(mock_auth_cls, mock_client_cls, tmp_path: Path):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.search_jobs.return_value = _api_response([_make_raw_job("Go")], has_more=False)

	out_path = tmp_path / "url.json"
	runner = CliRunner()
	result = runner.invoke(
		cli,
		[
			"export",
			"--url",
			"https://www.zhipin.com/web/geek/jobs?query=Go&city=101280100&degree=203",
			"--experience",
			"应届,3-5年",
			"--count",
			"1",
			"--format",
			"json",
			"-o",
			str(out_path),
		],
	)

	assert result.exit_code == 0
	_, kwargs = mock_client.search_jobs.call_args
	assert kwargs["raw_params"] == {
		"city": "101280100",
		"degree": "203",
		"experience": "102,105",
	}
	assert json.loads(out_path.read_text(encoding="utf-8"))[0]["title"] == "Go"


@patch("boss_agent_cli.commands.export.get_platform_instance")
@patch("boss_agent_cli.commands.export.AuthManager")
def test_export_stops_when_no_more_and_last_page_short(mock_auth_cls, mock_client_cls, tmp_path: Path):
	"""页数据少于 count 且 hasMore=False 时应提前终止，不造成空循环。"""
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.search_jobs.return_value = _api_response([_make_raw_job("Only One")], has_more=False)

	out_path = tmp_path / "short.json"
	runner = CliRunner()
	result = runner.invoke(cli, ["export", "rare", "--count", "50", "--format", "json", "-o", str(out_path)])
	assert result.exit_code == 0
	data = json.loads(out_path.read_text(encoding="utf-8"))
	assert len(data) == 1
	assert mock_client.search_jobs.call_count == 1


@patch("boss_agent_cli.commands.export.get_platform_instance")
@patch("boss_agent_cli.commands.export.AuthManager")
def test_export_stops_when_job_list_empty(mock_auth_cls, mock_client_cls, tmp_path: Path):
	"""空 jobList 也应触发终止。"""
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.search_jobs.return_value = _api_response([])

	out_path = tmp_path / "zero.csv"
	runner = CliRunner()
	result = runner.invoke(cli, ["export", "ghost", "--count", "10", "-o", str(out_path)])
	assert result.exit_code == 0
	assert mock_client.search_jobs.call_count == 1


# ── sanitize_csv_cell 单元测试 ────────────────────────────────────────


def test_sanitize_csv_cell_leaves_safe_values():
	from boss_agent_cli.services.job_export import _sanitize_csv_cell

	assert _sanitize_csv_cell("normal value") == "normal value"
	assert _sanitize_csv_cell("123") == "123"
	assert _sanitize_csv_cell("") == ""


def test_sanitize_csv_cell_escapes_formula_prefixes():
	from boss_agent_cli.services.job_export import _sanitize_csv_cell

	assert _sanitize_csv_cell("=SUM(A1)") == "'=SUM(A1)"
	assert _sanitize_csv_cell("+1234") == "'+1234"
	assert _sanitize_csv_cell("-5") == "'-5"
	assert _sanitize_csv_cell("@cmd") == "'@cmd"


def test_job_export_sanitizer_is_shared_with_chat_export():
	"""两份实现曾经分叉：job_export 的副本不剥 Tab/CR，同类数据两种导出结果不同（Issue #876）。"""
	from boss_agent_cli.services.chat_utils import sanitize_csv_cell
	from boss_agent_cli.services import job_export

	assert job_export._sanitize_csv_cell is sanitize_csv_cell
	for value in ("a\tb", "a\rb", "=\tcmd"):
		assert job_export._sanitize_csv_cell(value) == sanitize_csv_cell(value)


def test_sanitize_csv_cell_passes_other_specials():
	from boss_agent_cli.services.job_export import _sanitize_csv_cell

	# # 和 " 不是公式前缀，不应加单引号
	assert _sanitize_csv_cell("#test") == "#test"
	assert _sanitize_csv_cell('"quoted"') == '"quoted"'


# ── stdout 分支脱敏 ────────────────────────────────────────────────────


@patch("boss_agent_cli.commands.export.get_platform_instance")
@patch("boss_agent_cli.commands.export.AuthManager")
def test_export_stdout_default_redacts_private_fields(mock_auth_cls, mock_client_cls):
	"""stdout 分支默认应脱敏 job_id / security_id / boss_name。"""
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.search_jobs.return_value = _api_response([_make_raw_job("Go", security_id="s1")])

	runner = CliRunner()
	result = runner.invoke(cli, ["export", "golang", "--count", "1"])

	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	jobs = parsed["data"]["jobs"]
	assert len(jobs) == 1
	assert jobs[0]["job_id"] == "[REDACTED]"
	assert jobs[0]["security_id"] == "[REDACTED]"
	assert jobs[0]["lid"] == "[REDACTED]"
	assert jobs[0]["boss_name"] == "[REDACTED]"


@patch("boss_agent_cli.commands.export.get_platform_instance")
@patch("boss_agent_cli.commands.export.AuthManager")
def test_export_stdout_include_private_keeps_raw(mock_auth_cls, mock_client_cls):
	"""stdout + --include-private 应保留原始字段值。"""
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.search_jobs.return_value = _api_response([_make_raw_job("Go", security_id="s1")])

	runner = CliRunner()
	result = runner.invoke(cli, ["export", "golang", "--count", "1", "--include-private"])

	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	jobs = parsed["data"]["jobs"]
	assert len(jobs) == 1
	assert jobs[0]["security_id"] == "s1"
	assert jobs[0]["boss_name"] == "李"
	assert jobs[0]["job_id"] == "j_s1"


def test_export_without_query_returns_actionable_recovery():
	"""空参导出应返回 INVALID_PARAM 且带可执行 recovery_action。"""
	runner = CliRunner()
	result = runner.invoke(cli, ["--json", "export"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["ok"] is False
	assert parsed["error"]["code"] == "INVALID_PARAM"
	# 用法错误不可恢复：原样重试只会再次失败，应按 recovery_action 修正参数
	assert parsed["error"]["recoverable"] is False
	assert "export" in parsed["error"]["recovery_action"]


def test_export_invalid_search_url_points_at_keyword_export():
	"""URL 解析失败要指出改用关键词的修复路径，而不是落到默认「修正参数」。"""
	runner = CliRunner()
	result = runner.invoke(cli, ["--json", "export", "--url", "not-a-boss-url"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "INVALID_PARAM"
	assert "完整的 BOSS 直聘搜索页链接" in parsed["error"]["recovery_action"]
	actions = parsed["hints"]["next_actions"]
	assert len(actions) == 1
	assert actions[0].endswith("export <关键词>")


# ── 平台错误契约 ─────────────────────────────────────────────────────


@patch("boss_agent_cli.commands.export.get_platform_instance")
@patch("boss_agent_cli.commands.export.AuthManager")
def test_export_search_auth_expired_is_recoverable(mock_auth_cls, mock_client_cls, tmp_path: Path):
	"""登录过期是用户可恢复的错误，不能硬编码成不可恢复。"""
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.search_jobs.return_value = {"code": 401, "message": "expired"}
	mock_client.parse_error.return_value = ("AUTH_EXPIRED", "expired")

	result = CliRunner().invoke(cli, ["export", "golang", "-o", str(tmp_path / "jobs.csv")])

	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "AUTH_EXPIRED"
	assert parsed["error"]["recoverable"] is True
	assert shlex.split(parsed["error"]["recovery_action"]) == ["boss", "--data-dir", _DEFAULT_DATA_DIR, "login"]


@patch("boss_agent_cli.commands.export.get_platform_instance")
@patch("boss_agent_cli.commands.export.AuthManager")
def test_export_search_risk_uses_stop_contract(mock_auth_cls, mock_client_cls, tmp_path: Path):
	"""搜索阶段命中风控时信封必须是停止指引，而不是可恢复的普通失败。"""
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.search_jobs.return_value = {"code": 36, "message": "risk"}
	mock_client.parse_error.return_value = ("ACCOUNT_RISK", "risk")

	result = CliRunner().invoke(cli, ["export", "golang", "-o", str(tmp_path / "jobs.csv")])

	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "ACCOUNT_RISK"
	assert parsed["error"]["recoverable"] is False
	assert "停止自动化访问" in parsed["error"]["recovery_action"]
	assert parsed["hints"]["next_actions"]


@patch("boss_agent_cli.commands.export.get_platform_instance")
@patch("boss_agent_cli.commands.export.AuthManager")
def test_export_continues_when_has_more_is_missing(mock_auth_cls, mock_client_cls, tmp_path: Path):
	"""平台省略 hasMore 不等于没有下一页，不能静默只导出第一页（Issue #903）。"""
	mock_client = _ctx_mock(mock_client_cls)
	page1_jobs = [_make_raw_job(f"Job {i}", security_id=f"p1_{i}") for i in range(15)]
	page2_jobs = [_make_raw_job(f"Job {i}", security_id=f"p2_{i}") for i in range(10)]

	def search_side_effect(query, city=None, salary=None, page=1):
		if page == 1:
			return {"zpData": {"jobList": page1_jobs}}
		if page == 2:
			return _api_response(page2_jobs, has_more=False)
		return _api_response([])

	mock_client.search_jobs.side_effect = search_side_effect

	out_path = tmp_path / "unknown-more.json"
	result = CliRunner().invoke(cli, ["export", "bulk", "--count", "20", "--format", "json", "-o", str(out_path)])
	assert result.exit_code == 0, result.output
	assert len(json.loads(out_path.read_text(encoding="utf-8"))) == 20
	assert mock_client.search_jobs.call_count == 2


# ── 截断证据（Issue #907）─────────────────────────────────────────────


def _endless_single_job_pages(mock_client) -> None:
	"""每页只返回 1 条、平台始终 hasMore=true：内部页数预算必然耗尽。"""

	def search_side_effect(query, **kwargs):
		page = kwargs.get("page", 1)
		job = _make_raw_job(f"Job {page}", security_id=f"sec-{page}")
		return _api_response([job], has_more=True)

	mock_client.search_jobs.side_effect = search_side_effect


@pytest.mark.parametrize("mode", ["stdout", "file", "html-json"])
@patch("boss_agent_cli.commands.export.get_platform_instance")
@patch("boss_agent_cli.commands.export.AuthManager")
def test_export_marks_page_budget_exhaustion_in_every_envelope(
	mock_auth_cls, mock_client_cls, tmp_path: Path, mode: str
):
	"""预算耗尽仍未取满 count 时，三种信封都要带 truncated / stop_reason（Issue #907）。"""
	mock_client = _ctx_mock(mock_client_cls)
	_endless_single_job_pages(mock_client)

	argv = ["export", "golang", "--count", "10"]
	if mode == "file":
		argv += ["-o", str(tmp_path / "jobs.csv")]
	elif mode == "html-json":
		argv = ["--json", *argv, "--format", "html"]

	result = CliRunner().invoke(cli, argv)

	assert result.exit_code == 0, result.output
	payload = json.loads(result.output)
	assert payload["data"]["count"] == 6
	assert payload["data"]["truncated"] is True
	assert payload["data"]["stop_reason"] == "page_budget_exhausted"
	# estimated_pages=1 + allowance=5，恰好 6 次列表请求
	assert mock_client.search_jobs.call_count == 6
	assert payload["hints"]["operator_actions"]


@pytest.mark.parametrize(
	("has_more", "job_count", "count", "expected", "requests"),
	[
		(False, 1, 50, "platform_has_more_false", 1),
		(True, 0, 10, "empty_page", 1),
		(True, 1, 1, "count_reached", 1),
	],
	ids=["platform-false", "empty-page", "count-reached"],
)
@patch("boss_agent_cli.commands.export.get_platform_instance")
@patch("boss_agent_cli.commands.export.AuthManager")
def test_export_reports_normal_stop_reasons(
	mock_auth_cls, mock_client_cls, has_more, job_count, count, expected, requests
):
	"""取满请求量、平台明确没有下一页、空页都不算截断，但要说明终止原因。"""
	mock_client = _ctx_mock(mock_client_cls)
	jobs = [_make_raw_job(f"Job {i}", security_id=f"n-{i}") for i in range(job_count)]
	mock_client.search_jobs.return_value = _api_response(jobs, has_more=has_more)

	result = CliRunner().invoke(cli, ["export", "golang", "--count", str(count)])

	assert result.exit_code == 0, result.output
	payload = json.loads(result.output)
	assert payload["data"]["truncated"] is False
	assert payload["data"]["stop_reason"] == expected
	assert "operator_actions" not in payload["hints"]
	assert mock_client.search_jobs.call_count == requests


@patch("boss_agent_cli.commands.export.get_platform_instance")
@patch("boss_agent_cli.commands.export.AuthManager")
def test_export_html_stdout_warns_on_stderr_without_polluting_html(mock_auth_cls, mock_client_cls):
	"""HTML 直出没有信封：截断提示只能走 stderr，stdout 必须仍是完整 HTML。"""
	mock_client = _ctx_mock(mock_client_cls)
	_endless_single_job_pages(mock_client)

	result = CliRunner().invoke(cli, ["export", "golang", "--count", "10", "--format", "html"])

	assert result.exit_code == 0, result.output
	assert result.stdout.lstrip().startswith("<")
	assert "页数上限" in result.stderr
