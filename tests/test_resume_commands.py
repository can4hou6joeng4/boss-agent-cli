import json
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from boss_agent_cli.main import cli


def _invoke(runner, tmp_path, args):
	return runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "resume"] + args)


# ── init ──────────────────────────────────────────────────────


def test_init_with_template(tmp_path):
	runner = CliRunner()
	result = _invoke(runner, tmp_path, ["init", "--name", "myresume", "--template", "default"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["data"]["name"] == "myresume"
	assert parsed["data"]["action"] == "init"


def test_init_default_name(tmp_path):
	"""不传 --name 时应使用默认名称"""
	runner = CliRunner()
	result = _invoke(runner, tmp_path, ["init", "--template", "default"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["data"]["name"] != ""


def test_init_already_exists(tmp_path):
	runner = CliRunner()
	_invoke(runner, tmp_path, ["init", "--name", "dup", "--template", "default"])
	result = _invoke(runner, tmp_path, ["init", "--name", "dup", "--template", "default"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["ok"] is False
	assert parsed["error"]["code"] == "RESUME_ALREADY_EXISTS"


# ── list ──────────────────────────────────────────────────────


def test_list_empty(tmp_path):
	runner = CliRunner()
	result = _invoke(runner, tmp_path, ["list"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["data"] == []


def test_list_with_items(tmp_path):
	runner = CliRunner()
	_invoke(runner, tmp_path, ["init", "--name", "r1", "--template", "default"])
	_invoke(runner, tmp_path, ["init", "--name", "r2", "--template", "default"])
	result = _invoke(runner, tmp_path, ["list"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert len(parsed["data"]) == 2


# ── show ──────────────────────────────────────────────────────


def test_show_existing(tmp_path):
	runner = CliRunner()
	_invoke(runner, tmp_path, ["init", "--name", "showme", "--template", "default"])
	result = _invoke(runner, tmp_path, ["show", "showme"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["data"]["name"] == "showme"
	assert "title" in parsed["data"]
	assert "personal_info" in parsed["data"]


def test_show_not_found(tmp_path):
	runner = CliRunner()
	result = _invoke(runner, tmp_path, ["show", "nonexist"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["ok"] is False
	assert parsed["error"]["code"] == "RESUME_NOT_FOUND"


# ── edit ──────────────────────────────────────────────────────


def test_edit_existing_field(tmp_path):
	runner = CliRunner()
	_invoke(runner, tmp_path, ["init", "--name", "editable", "--template", "default"])
	result = _invoke(runner, tmp_path, ["edit", "editable", "--field", "title", "--value", "Senior Dev"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["data"]["field"] == "title"
	assert parsed["data"]["value"] == "Senior Dev"

	# 验证确实修改了
	show_result = _invoke(runner, tmp_path, ["show", "editable"])
	show_parsed = json.loads(show_result.output)
	assert show_parsed["data"]["title"] == "Senior Dev"


def test_edit_nested_field(tmp_path):
	runner = CliRunner()
	_invoke(runner, tmp_path, ["init", "--name", "nested", "--template", "default"])
	result = _invoke(runner, tmp_path, ["edit", "nested", "--field", "personal_info.layout", "--value", "block"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True


def test_edit_not_found(tmp_path):
	runner = CliRunner()
	result = _invoke(runner, tmp_path, ["edit", "ghost", "--field", "title", "--value", "X"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "RESUME_NOT_FOUND"


# ── delete ────────────────────────────────────────────────────


def test_delete_existing(tmp_path):
	runner = CliRunner()
	_invoke(runner, tmp_path, ["init", "--name", "todel", "--template", "default"])
	result = _invoke(runner, tmp_path, ["delete", "todel"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["data"]["deleted"] is True

	# 验证已删除
	show_result = _invoke(runner, tmp_path, ["show", "todel"])
	assert show_result.exit_code == 1


def test_delete_not_found(tmp_path):
	runner = CliRunner()
	result = _invoke(runner, tmp_path, ["delete", "nope"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "RESUME_NOT_FOUND"


# ── export ────────────────────────────────────────────────────


def test_export_json(tmp_path):
	runner = CliRunner()
	_invoke(runner, tmp_path, ["init", "--name", "exportme", "--template", "default"])
	result = _invoke(runner, tmp_path, ["export", "exportme", "--format", "json"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert "version" in parsed["data"]
	assert parsed["data"]["data"]["name"] == "exportme"


def test_export_pdf_success(tmp_path):
	"""PDF 导出使用 mock patchright 验证成功路径"""
	runner = CliRunner()
	_invoke(runner, tmp_path, ["init", "--name", "pdftest", "--template", "default"])

	mock_page = MagicMock()
	mock_browser = MagicMock()
	mock_browser.new_page.return_value = mock_page
	mock_chromium = MagicMock()
	mock_chromium.launch.return_value = mock_browser
	mock_pw = MagicMock()
	mock_pw.chromium = mock_chromium
	mock_cm = MagicMock()
	mock_cm.__enter__ = MagicMock(return_value=mock_pw)
	mock_cm.__exit__ = MagicMock(return_value=False)

	out_file = tmp_path / "test_output.pdf"
	with patch("patchright.sync_api.sync_playwright", return_value=mock_cm):
		result = _invoke(runner, tmp_path, ["export", "pdftest", "--format", "pdf", "-o", str(out_file)])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["data"]["format"] == "pdf"


def test_export_not_found(tmp_path):
	runner = CliRunner()
	result = _invoke(runner, tmp_path, ["export", "missing", "--format", "json"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "RESUME_NOT_FOUND"


# ── import ────────────────────────────────────────────────────


def test_import_valid(tmp_path):
	runner = CliRunner()
	# 创建一个合法的 JSON 简历文件
	resume_file = tmp_path / "import_resume.json"
	resume_data = {
		"name": "imported",
		"title": "Imported Resume",
		"center_title": False,
		"personal_info": {"items": [], "layout": "inline"},
		"modules": [],
		"avatar": "",
	}
	resume_file.write_text(json.dumps(resume_data), encoding="utf-8")
	result = _invoke(runner, tmp_path, ["import", str(resume_file)])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["data"]["name"] == "imported"


def test_import_invalid_file(tmp_path):
	runner = CliRunner()
	bad_file = tmp_path / "bad.json"
	bad_file.write_text("not json at all", encoding="utf-8")
	result = _invoke(runner, tmp_path, ["import", str(bad_file)])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["ok"] is False


def test_import_file_not_found(tmp_path):
	runner = CliRunner()
	result = _invoke(runner, tmp_path, ["import", str(tmp_path / "no_such_file.json")])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["ok"] is False


# ── clone ─────────────────────────────────────────────────────


def test_clone_existing(tmp_path):
	runner = CliRunner()
	_invoke(runner, tmp_path, ["init", "--name", "original", "--template", "default"])
	result = _invoke(runner, tmp_path, ["clone", "original", "copy1"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["data"]["name"] == "copy1"


def test_clone_source_not_found(tmp_path):
	runner = CliRunner()
	result = _invoke(runner, tmp_path, ["clone", "nosrc", "dst"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "RESUME_NOT_FOUND"


def test_clone_target_already_exists(tmp_path):
	runner = CliRunner()
	_invoke(runner, tmp_path, ["init", "--name", "src", "--template", "default"])
	_invoke(runner, tmp_path, ["init", "--name", "dst", "--template", "default"])
	result = _invoke(runner, tmp_path, ["clone", "src", "dst"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "RESUME_ALREADY_EXISTS"


# ── diff ──────────────────────────────────────────────────────


def test_diff_two_resumes(tmp_path):
	runner = CliRunner()
	_invoke(runner, tmp_path, ["init", "--name", "a", "--template", "default"])
	_invoke(runner, tmp_path, ["init", "--name", "b", "--template", "default"])
	# 修改 b 的 title
	_invoke(runner, tmp_path, ["edit", "b", "--field", "title", "--value", "Modified Title"])
	result = _invoke(runner, tmp_path, ["diff", "a", "b"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	diffs = parsed["data"]["diffs"]
	assert isinstance(diffs, list)
	# 应有 title 和 updated_at 差异
	fields = [d["field"] for d in diffs]
	assert "title" in fields


def test_diff_one_not_found(tmp_path):
	runner = CliRunner()
	_invoke(runner, tmp_path, ["init", "--name", "exists", "--template", "default"])
	result = _invoke(runner, tmp_path, ["diff", "exists", "missing"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "RESUME_NOT_FOUND"


# ── schema 集成 ───────────────────────────────────────────────


def test_schema_contains_resume():
	runner = CliRunner()
	result = runner.invoke(cli, ["schema"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert "resume" in parsed["data"]["commands"]
	cmd = parsed["data"]["commands"]["resume"]
	assert "subcommands" in cmd
	assert "init" in cmd["subcommands"]
	assert "diff" in cmd["subcommands"]


def test_schema_contains_resume_error_codes():
	runner = CliRunner()
	result = runner.invoke(cli, ["schema"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	codes = parsed["data"]["error_codes"]
	assert "RESUME_NOT_FOUND" in codes
	assert "RESUME_ALREADY_EXISTS" in codes
	assert "EXPORT_FAILED" in codes
