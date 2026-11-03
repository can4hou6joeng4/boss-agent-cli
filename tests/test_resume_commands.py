import json
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.main import cli


def _invoke(runner, tmp_path, args):
	return runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "resume"] + args)


def _assert_success_envelope(parsed, *, command="resume"):
	assert parsed["ok"] is True
	assert parsed["schema_version"] == "1.0"
	assert parsed["command"] == command
	assert parsed["pagination"] is None
	assert parsed["error"] is None
	assert isinstance(parsed["hints"]["next_actions"], list)
	assert parsed["hints"]["next_actions"]


def _assert_error_envelope(parsed, code: str, *, command="resume"):
	assert parsed["ok"] is False
	assert parsed["schema_version"] == "1.0"
	assert parsed["command"] == command
	assert parsed["data"] is None
	assert parsed["pagination"] is None
	assert parsed["error"]["code"] == code
	assert isinstance(parsed["error"]["message"], str)
	assert parsed["error"]["message"]


# ── init ──────────────────────────────────────────────────────


def test_init_with_template(tmp_path):
	runner = CliRunner()
	result = _invoke(runner, tmp_path, ["init", "--name", "myresume", "--template", "default"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	_assert_success_envelope(parsed)
	assert parsed["data"] == {"action": "init", "name": "myresume", "template": "default"}
	assert parsed["hints"]["next_actions"] == [
		"boss resume show myresume",
		"boss resume edit myresume --field title --value <新标题>",
		"boss me",
		"boss resume import <path>",
	]


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


def test_init_rejects_blank_name(tmp_path):
	"""空名称的简历没法点名，`resume show ` 缺参数，必须在写盘前拒绝。"""
	runner = CliRunner()
	for name in ("", "   "):
		result = _invoke(runner, tmp_path, ["init", "--name", name, "--template", "default"])
		assert result.exit_code == 1, result.output
		parsed = json.loads(result.output)
		assert parsed["error"]["code"] == "INVALID_PARAM"
		assert "名称" in parsed["error"]["message"]

	listed = json.loads(_invoke(runner, tmp_path, ["list"]).output)
	assert listed["data"] == []


# ── list ──────────────────────────────────────────────────────


def test_list_empty(tmp_path):
	runner = CliRunner()
	result = _invoke(runner, tmp_path, ["list"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	_assert_success_envelope(parsed)
	assert parsed["data"] == []
	assert parsed["hints"]["next_actions"] == ["boss resume show <name>", "boss resume init --template default"]


def test_list_with_items(tmp_path):
	runner = CliRunner()
	_invoke(runner, tmp_path, ["init", "--name", "r1", "--template", "default"])
	_invoke(runner, tmp_path, ["init", "--name", "r2", "--template", "default"])
	result = _invoke(runner, tmp_path, ["list"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	_assert_success_envelope(parsed)
	assert [item["name"] for item in parsed["data"]] == ["r1", "r2"]
	assert all({"name", "title", "updated_at"}.issubset(item) for item in parsed["data"])


# ── show ──────────────────────────────────────────────────────


def test_show_existing(tmp_path):
	runner = CliRunner()
	_invoke(runner, tmp_path, ["init", "--name", "showme", "--template", "default"])
	result = _invoke(runner, tmp_path, ["show", "showme"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	_assert_success_envelope(parsed)
	assert parsed["data"]["name"] == "showme"
	assert parsed["data"]["title"] == "我的简历"
	assert parsed["data"]["personal_info"] == {"items": [], "layout": "inline"}
	assert parsed["data"]["modules"] == []
	assert parsed["hints"]["next_actions"] == [
		"boss resume edit showme --field title --value <新标题>",
		"boss resume export showme --format json",
	]


def test_show_not_found(tmp_path):
	runner = CliRunner()
	result = _invoke(runner, tmp_path, ["show", "nonexist"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	_assert_error_envelope(parsed, "RESUME_NOT_FOUND")


# ── edit ──────────────────────────────────────────────────────


def test_edit_existing_field(tmp_path):
	runner = CliRunner()
	_invoke(runner, tmp_path, ["init", "--name", "editable", "--template", "default"])
	result = _invoke(runner, tmp_path, ["edit", "editable", "--field", "title", "--value", "Senior Dev"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	_assert_success_envelope(parsed)
	assert parsed["data"] == {"action": "edit", "name": "editable", "field": "title", "value": "Senior Dev"}
	assert parsed["hints"]["next_actions"] == ["boss resume show editable"]

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
	_assert_error_envelope(parsed, "RESUME_NOT_FOUND")


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


def test_delete_resume_clears_job_links(tmp_path):
	"""删除简历时一并清理职位关联，同名重建的简历不继承旧投递状态。

	关联表没有外键约束；不同步清理的话，`resume delete default` 再 `resume init`
	（默认名就是 default）之后，`resume applications default` 会把旧简历投过的职位
	连同 `applied` 状态一起报出来。
	"""
	runner = CliRunner()
	_invoke(runner, tmp_path, ["init", "--name", "default", "--template", "default"])
	_invoke(runner, tmp_path, ["link", "default", "sid-001", "jid-001", "--title", "后端", "--company", "公司甲"])

	from boss_agent_cli.cache.store import CacheStore

	with CacheStore(tmp_path / "cache" / "boss_agent.db") as cache:
		assert cache.update_job_link_status("default", "sid-001", "jid-001", "applied", "已投递") is True

	delete_result = _invoke(runner, tmp_path, ["delete", "default"])
	assert delete_result.exit_code == 0, delete_result.output

	_invoke(runner, tmp_path, ["init", "--template", "default"])
	result = _invoke(runner, tmp_path, ["applications", "default"])
	assert json.loads(result.output)["data"] == []
	assert json.loads(delete_result.output)["data"]["removed_links"] == 1


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


def test_export_json_creates_parent_directories(tmp_path):
	"""`-o` 指向尚不存在的目录时自动建父目录（与 digest / stats 一致）。"""
	runner = CliRunner()
	_invoke(runner, tmp_path, ["init", "--name", "exportme", "--template", "default"])
	out = tmp_path / "deep" / "nested" / "export.json"

	result = _invoke(runner, tmp_path, ["export", "exportme", "--format", "json", "-o", str(out)])

	assert result.exit_code == 0
	assert out.exists()
	assert json.loads(out.read_text(encoding="utf-8"))["data"]["name"] == "exportme"


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
	_assert_success_envelope(parsed)
	assert parsed["data"]["format"] == "pdf"
	assert parsed["data"]["path"] == str(out_file)
	assert parsed["hints"]["next_actions"] == ["boss resume show pdftest"]


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
	_assert_success_envelope(parsed)
	assert parsed["data"] == {"action": "import", "name": "imported", "title": "Imported Resume"}
	assert parsed["hints"]["next_actions"] == ["boss resume show imported"]


def test_import_refuses_to_overwrite_existing_resume(tmp_path):
	"""文件里的 name 与已有简历同名时默认拒绝，不静默覆盖本地编辑。

	`import_file` 最终走整份 `save()`：导入一份旧备份会把用户后来的编辑全部丢掉，
	而 `resume init` / `resume clone` 遇到同名都是 `RESUME_ALREADY_EXISTS`。
	"""
	runner = CliRunner()
	_invoke(runner, tmp_path, ["init", "--name", "mine", "--template", "default"])
	_invoke(runner, tmp_path, ["edit", "mine", "--field", "title", "--value", "本地改过的标题"])

	src = tmp_path / "backup.json"
	src.write_text('{"name": "mine", "title": "备份里的旧标题"}', encoding="utf-8")
	result = _invoke(runner, tmp_path, ["import", str(src)])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	_assert_error_envelope(parsed, "RESUME_ALREADY_EXISTS")
	assert "--force" in parsed["error"]["recovery_action"]

	show = json.loads(_invoke(runner, tmp_path, ["show", "mine"]).output)
	assert show["data"]["title"] == "本地改过的标题"


def test_import_force_overwrites_existing_resume(tmp_path):
	"""显式 --force 时才覆盖同名简历。"""
	runner = CliRunner()
	_invoke(runner, tmp_path, ["init", "--name", "mine", "--template", "default"])

	src = tmp_path / "backup.json"
	src.write_text('{"name": "mine", "title": "备份里的旧标题"}', encoding="utf-8")
	result = _invoke(runner, tmp_path, ["import", str(src), "--force"])
	assert result.exit_code == 0, result.output

	show = json.loads(_invoke(runner, tmp_path, ["show", "mine"]).output)
	assert show["data"]["title"] == "备份里的旧标题"


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


def test_import_non_utf8_file_returns_invalid_param(tmp_path):
	"""GBK 保存的 JSON、误选的 PDF/图片都会读成非 UTF-8：要给信封和转码指引，而不是 traceback。"""
	bad_file = tmp_path / "gbk.json"
	bad_file.write_bytes('{"name": "张三"}'.encode("gbk"))
	result = _invoke(CliRunner(), tmp_path, ["import", str(bad_file)])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	_assert_error_envelope(parsed, "INVALID_PARAM")
	assert str(bad_file) in parsed["error"]["message"]
	assert "UTF-8" in parsed["error"]["message"]


@pytest.mark.parametrize("content", ["[]", '"just a string"', "42", "null"])
def test_import_non_object_json_returns_invalid_param(tmp_path, content):
	"""顶层不是 JSON 对象时不能到模型解析层才抛 AttributeError。"""
	bad_file = tmp_path / "not_object.json"
	bad_file.write_text(content, encoding="utf-8")
	result = _invoke(CliRunner(), tmp_path, ["import", str(bad_file)])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	_assert_error_envelope(parsed, "INVALID_PARAM")
	assert "顶层必须是 JSON 对象" in parsed["error"]["message"]


@pytest.mark.parametrize("content", ["{}", '{"name": 123}', '{"name": "  "}'])
def test_import_missing_name_returns_invalid_param(tmp_path, content):
	"""name 缺失或不是字符串时，save() 里的 _safe_filename 会崩在 int.replace 上。"""
	bad_file = tmp_path / "no_name.json"
	bad_file.write_text(content, encoding="utf-8")
	result = _invoke(CliRunner(), tmp_path, ["import", str(bad_file)])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	_assert_error_envelope(parsed, "INVALID_PARAM")
	assert "name" in parsed["error"]["message"]


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


def test_clone_rejects_blank_new_name(tmp_path):
	"""空新名称会复制出一份无法点名的简历，必须在写盘前拒绝。"""
	runner = CliRunner()
	_invoke(runner, tmp_path, ["init", "--name", "original", "--template", "default"])
	for new_name in ("", "   "):
		result = _invoke(runner, tmp_path, ["clone", "original", new_name])
		assert result.exit_code == 1, result.output
		parsed = json.loads(result.output)
		assert parsed["error"]["code"] == "INVALID_PARAM"
		assert "名称" in parsed["error"]["message"]

	listed = json.loads(_invoke(runner, tmp_path, ["list"]).output)
	assert [item["name"] for item in listed["data"]] == ["original"]


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


# ── link / applications ─────────────────────────────────────


def test_link_resume_to_job(tmp_path):
	runner = CliRunner()
	_invoke(runner, tmp_path, ["init", "--name", "linktest", "--template", "default"])
	result = _invoke(runner, tmp_path, ["link", "linktest", "sid-001", "jid-001", "--title", "后端工程师", "--company", "测试公司"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	_assert_success_envelope(parsed)
	assert parsed["data"] == {
		"action": "link",
		"name": "linktest",
		"security_id": "sid-001",
		"job_id": "jid-001",
	}
	assert parsed["hints"]["next_actions"] == ["boss resume applications linktest", "boss apply sid-001 jid-001"]


def test_link_resume_not_found(tmp_path):
	runner = CliRunner()
	result = _invoke(runner, tmp_path, ["link", "ghost", "sid", "jid"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "RESUME_NOT_FOUND"


def test_link_rejects_blank_identifiers(tmp_path):
	"""空 security_id / job_id 会存下一条无法点名的空关联，必须在写盘前拒绝。"""
	runner = CliRunner()
	_invoke(runner, tmp_path, ["init", "--name", "blanktest", "--template", "default"])
	result = _invoke(runner, tmp_path, ["link", "blanktest", "", "  "])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "INVALID_PARAM"
	assert "security_id" in parsed["error"]["message"]

	applications = _invoke(runner, tmp_path, ["applications", "blanktest"])
	assert json.loads(applications.output)["data"] == []


def test_applications_list(tmp_path):
	runner = CliRunner()
	_invoke(runner, tmp_path, ["init", "--name", "apptest", "--template", "default"])
	_invoke(runner, tmp_path, ["link", "apptest", "sid-a", "jid-a", "--title", "前端", "--company", "公司甲"])
	_invoke(runner, tmp_path, ["link", "apptest", "sid-b", "jid-b", "--title", "后端", "--company", "公司乙"])
	result = _invoke(runner, tmp_path, ["applications", "apptest"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	_assert_success_envelope(parsed)
	assert [(item["security_id"], item["job_id"], item["job_title"], item["company"]) for item in parsed["data"]] == [
		("sid-b", "jid-b", "后端", "公司乙"),
		("sid-a", "jid-a", "前端", "公司甲"),
	]
	assert all(item["resume_name"] == "apptest" for item in parsed["data"])
	assert all(item["status"] == "prepared" for item in parsed["data"])
	assert parsed["hints"]["next_actions"] == ["boss resume show apptest"]


def test_relink_resume_keeps_title_company_and_tracking(tmp_path):
	"""重复 `resume link` 同一职位不抹掉已有标题、公司和投递状态。

	第二次关联只给必要参数（不带 `--title` / `--company`）时，旧实现会用空串覆盖
	职位名与公司名，并把状态打回 `prepared`。
	"""
	runner = CliRunner()
	_invoke(runner, tmp_path, ["init", "--name", "relink", "--template", "default"])
	_invoke(runner, tmp_path, ["link", "relink", "sid-001", "jid-001", "--title", "后端工程师", "--company", "测试公司"])

	from boss_agent_cli.cache.store import CacheStore

	with CacheStore(tmp_path / "cache" / "boss_agent.db") as cache:
		assert cache.update_job_link_status("relink", "sid-001", "jid-001", "applied", "已投递") is True

	result = _invoke(runner, tmp_path, ["link", "relink", "sid-001", "jid-001"])
	assert result.exit_code == 0, result.output

	result = _invoke(runner, tmp_path, ["applications", "relink"])
	items = json.loads(result.output)["data"]
	assert items[0]["job_title"] == "后端工程师"
	assert items[0]["company"] == "测试公司"
	assert items[0]["status"] == "applied"
	assert items[0]["notes"] == "已投递"


def test_applications_resume_not_found(tmp_path):
	runner = CliRunner()
	result = _invoke(runner, tmp_path, ["applications", "ghost"])
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
	assert cmd["options"]["import"]["--force"]["default"] is False


def test_schema_contains_resume_error_codes():
	runner = CliRunner()
	result = runner.invoke(cli, ["schema"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	codes = parsed["data"]["error_codes"]
	assert "RESUME_NOT_FOUND" in codes
	assert "RESUME_ALREADY_EXISTS" in codes
	assert "EXPORT_FAILED" in codes
