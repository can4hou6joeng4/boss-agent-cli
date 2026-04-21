"""Tests for boss export 命令 - 扩展覆盖。

补齐 export.py 的三种格式、文件输出、公式注入防护、分页循环、空数据等分支。
"""

import csv
import json
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner
from boss_agent_cli.main import cli


def _ctx_mock(mock_cls):
	instance = mock_cls.return_value
	instance.__enter__ = lambda self: self
	instance.__exit__ = lambda self, *a: None
	return instance


def _make_raw_job(name: str = "Go 开发", skills: list | None = None, welfare: list | None = None, security_id: str = "sec_x") -> dict:
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
	evil_job = _make_raw_job(name="=HYPERLINK(\"https://evil\")")
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
	mock_client.search_jobs.return_value = _api_response([
		_make_raw_job("Go 1", security_id="s1"),
		_make_raw_job("Go 2", security_id="s2"),
	])

	out_path = tmp_path / "jobs.json"
	runner = CliRunner()
	result = runner.invoke(cli, ["export", "golang", "--count", "2", "--format", "json", "-o", str(out_path)])

	assert result.exit_code == 0
	assert out_path.exists()
	data = json.loads(out_path.read_text(encoding="utf-8"))
	assert len(data) == 2
	assert data[0]["title"] == "Go 1"
	assert data[1]["security_id"] == "s2"


# ── 文件输出 · HTML ──────────────────────────────────────────────────


@patch("boss_agent_cli.commands.export.get_platform_instance")
@patch("boss_agent_cli.commands.export.AuthManager")
def test_export_html_to_file(mock_auth_cls, mock_client_cls, tmp_path: Path):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.search_jobs.return_value = _api_response([
		_make_raw_job("Full-Stack", skills=["Python", "React"], welfare=["双休", "五险"]),
	])

	out_path = tmp_path / "jobs.html"
	runner = CliRunner()
	result = runner.invoke(cli, ["export", "fullstack", "--count", "1", "--format", "html", "-o", str(out_path)])

	assert result.exit_code == 0
	assert out_path.exists()
	content = out_path.read_text(encoding="utf-8")
	assert "<!DOCTYPE html>" in content
	assert "Full-Stack" in content
	assert "TestCo" in content
	# 技能和福利应各自带标签样式
	assert 'class="tag sk"' in content
	assert 'class="tag wf"' in content
	assert "Python" in content
	assert "双休" in content
	# 共 1 条应显示
	assert "共 1 条" in content


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
	from boss_agent_cli.commands.export import _sanitize_csv_cell

	assert _sanitize_csv_cell("normal value") == "normal value"
	assert _sanitize_csv_cell("123") == "123"
	assert _sanitize_csv_cell("") == ""


def test_sanitize_csv_cell_escapes_formula_prefixes():
	from boss_agent_cli.commands.export import _sanitize_csv_cell

	assert _sanitize_csv_cell("=SUM(A1)") == "'=SUM(A1)"
	assert _sanitize_csv_cell("+1234") == "'+1234"
	assert _sanitize_csv_cell("-5") == "'-5"
	assert _sanitize_csv_cell("@cmd") == "'@cmd"


def test_sanitize_csv_cell_passes_other_specials():
	from boss_agent_cli.commands.export import _sanitize_csv_cell

	# # 和 " 不是公式前缀，不应加单引号
	assert _sanitize_csv_cell("#test") == "#test"
	assert _sanitize_csv_cell('"quoted"') == '"quoted"'
