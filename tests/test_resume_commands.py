import json
import shlex
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.main import cli


def _hint(command: str, data_dir: Path) -> str:
	"""后继提示保留本次调用的数据目录（绝对解析路径）。"""
	return f"boss --data-dir {Path(data_dir).resolve()} {command}"


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
		_hint("resume show myresume", tmp_path),
		_hint("resume edit myresume --field title --value <新标题>", tmp_path),
		_hint("me", tmp_path),
		_hint("resume import <path>", tmp_path),
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
	# 空列表里没有简历名可 show：照抄只会换回 RESUME_NOT_FOUND。
	assert parsed["hints"]["next_actions"] == [_hint("resume init --template default", tmp_path)]


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
		_hint("resume edit showme --field title --value <新标题>", tmp_path),
		_hint("resume export showme --format json", tmp_path),
	]


def test_show_not_found(tmp_path):
	runner = CliRunner()
	result = _invoke(runner, tmp_path, ["show", "nonexist"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	_assert_error_envelope(parsed, "RESUME_NOT_FOUND")


@pytest.mark.parametrize(
	"args",
	[
		["show", "ghost"],
		["edit", "ghost", "--field", "title", "--value", "X"],
		["delete", "ghost"],
		["diff", "ghost", "other"],
		["applications", "ghost"],
		["clone", "ghost", "new"],
		["link", "ghost", "sid1", "job1"],
	],
)
def test_not_found_points_at_resume_list(tmp_path, args):
	"""简历不存在时 ``recovery_action`` 已指向 resume list，``hints`` 却是 null。

	Agent 只从自然语言的 recovery_action 里抽命令，拿不到结构化的
	``next_actions``；列出可选简历是唯一能把错误名字换成正确名字的入口。
	"""
	result = _invoke(CliRunner(), tmp_path, args)
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	_assert_error_envelope(parsed, "RESUME_NOT_FOUND")
	assert parsed["hints"]["next_actions"] == [_hint("resume list", tmp_path)]


# ── edit ──────────────────────────────────────────────────────


def test_edit_existing_field(tmp_path):
	runner = CliRunner()
	_invoke(runner, tmp_path, ["init", "--name", "editable", "--template", "default"])
	result = _invoke(runner, tmp_path, ["edit", "editable", "--field", "title", "--value", "Senior Dev"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	_assert_success_envelope(parsed)
	assert parsed["data"] == {"action": "edit", "name": "editable", "field": "title", "value": "Senior Dev"}
	assert parsed["hints"]["next_actions"] == [_hint("resume show editable", tmp_path)]

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


def test_edit_container_field_is_rejected(tmp_path):
	"""--field 点到容器时不能整体替换成字符串再炸成 INTERNAL_ERROR。"""
	runner = CliRunner()
	_invoke(runner, tmp_path, ["init", "--name", "container", "--template", "default"])
	for field in ("personal_info", "modules", "job_intention"):
		result = _invoke(runner, tmp_path, ["edit", "container", "--field", field, "--value", "x"])
		assert result.exit_code == 1, field
		parsed = json.loads(result.output)
		_assert_error_envelope(parsed, "INVALID_PARAM")
		assert "不是文本字段" in parsed["error"]["message"]
		assert parsed["hints"]["next_actions"] == [
			_hint("resume export container --format json", tmp_path),
			_hint("resume show container", tmp_path),
		]
		assert "export" in parsed["error"]["recovery_action"]

	# 简历本身没有被写坏：仍然可以正常展示与导出。
	shown = _invoke(runner, tmp_path, ["show", "container"])
	assert shown.exit_code == 0, shown.output


def test_edit_unknown_field_lists_next_steps(tmp_path):
	"""字段名拼错时给出「怎么看到完整结构」的下一步，而不是一句「不存在」。"""
	runner = CliRunner()
	_invoke(runner, tmp_path, ["init", "--name", "typo", "--template", "default"])
	result = _invoke(runner, tmp_path, ["edit", "typo", "--field", "titl", "--value", "x"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	_assert_error_envelope(parsed, "INVALID_PARAM")
	assert "不存在" in parsed["error"]["message"]
	assert parsed["hints"]["next_actions"][0] == _hint("resume export typo --format json", tmp_path)
	assert "文本子字段" in parsed["error"]["recovery_action"]


def test_edit_error_message_quotes_resume_name_with_spaces(tmp_path):
	"""message 里让人「照抄」的 export 命令同样要转义。

	hints 早已走 shell_argument，消息正文却直接拼名字：简历名带空格时
	``boss resume export 我的 简历 --format json`` 会被拆成两个参数。
	"""
	runner = CliRunner()
	_invoke(runner, tmp_path, ["init", "--name", "我的 简历", "--template", "default"])
	result = _invoke(runner, tmp_path, ["edit", "我的 简历", "--field", "titl", "--value", "x"])

	assert result.exit_code == 1
	message = json.loads(result.output)["error"]["message"]
	assert "resume export '我的 简历' --format json" in message
	assert shlex.split(message.split("完整结构见 ", 1)[1]) == [
		"boss",
		"--data-dir",
		str(tmp_path.resolve()),
		"resume",
		"export",
		"我的 简历",
		"--format",
		"json",
	]


def test_edit_name_field_points_to_clone(tmp_path):
	"""--field name 会被后面的 updated.name 覆盖，成功信封与实际内容不符，必须拒绝。"""
	runner = CliRunner()
	_invoke(runner, tmp_path, ["init", "--name", "oldname", "--template", "default"])
	result = _invoke(runner, tmp_path, ["edit", "oldname", "--field", "name", "--value", "newname"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	_assert_error_envelope(parsed, "INVALID_PARAM")
	assert parsed["hints"]["next_actions"] == [_hint("resume clone oldname <新名>", tmp_path)]
	assert "clone" in parsed["error"]["recovery_action"]

	shown = json.loads(_invoke(runner, tmp_path, ["show", "oldname"]).output)
	assert shown["data"]["name"] == "oldname"


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
	assert parsed["hints"]["next_actions"] == [_hint("resume show pdftest", tmp_path)]


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
	assert parsed["hints"]["next_actions"] == [_hint("resume show imported", tmp_path)]


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


def test_import_parse_failure_points_at_export_template(tmp_path):
	"""无法解析的 JSON 也要给出可执行的修复入口，而不只是「导入失败」。"""
	bad_file = tmp_path / "bad.json"
	bad_file.write_text("not json at all", encoding="utf-8")
	result = _invoke(CliRunner(), tmp_path, ["import", str(bad_file)])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	_assert_error_envelope(parsed, "INVALID_PARAM")
	assert "UTF-8" in parsed["error"]["recovery_action"]
	assert parsed["hints"]["next_actions"] == [
		_hint("resume export <简历> --format json", tmp_path),
		_hint("resume list", tmp_path),
	]


def test_import_malformed_structure_points_at_export_template(tmp_path):
	"""结构对不上模型的 JSON 要给出对照模板，而不是只报一句「解析失败」。"""
	bad_file = tmp_path / "bad_structure.json"
	bad_file.write_text(json.dumps({"name": "broken", "personal_info": "not-an-object"}), encoding="utf-8")
	result = _invoke(CliRunner(), tmp_path, ["import", str(bad_file)])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	_assert_error_envelope(parsed, "INVALID_PARAM")
	assert "解析失败" in parsed["error"]["message"]
	assert parsed["hints"]["next_actions"] == [
		_hint("resume export <简历> --format json", tmp_path),
		_hint("resume list", tmp_path),
	]
	assert "export" in parsed["error"]["recovery_action"]


def test_import_file_not_found(tmp_path):
	runner = CliRunner()
	result = _invoke(runner, tmp_path, ["import", str(tmp_path / "no_such_file.json")])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["ok"] is False


def test_import_rejects_blank_path(tmp_path):
	"""空路径会被 Path("") 解析成当前目录，原来报成 LOCAL_IO_ERROR「本地文件读写失败」。

	真正的原因是 argv 少传了一个值，却让人去查磁盘和权限。
	"""
	result = _invoke(CliRunner(), tmp_path, ["import", ""])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	_assert_error_envelope(parsed, "INVALID_PARAM")
	assert parsed["error"]["message"] == "文件路径不能为空"
	assert parsed["hints"]["next_actions"]


def test_import_rejects_directory_path(tmp_path):
	"""目录同样能通过 exists() 检查，随后在打开文件时抛 IsADirectoryError。"""
	result = _invoke(CliRunner(), tmp_path, ["import", str(tmp_path)])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	_assert_error_envelope(parsed, "INVALID_PARAM")
	assert "是目录" in parsed["error"]["message"]


def test_import_missing_file_points_at_import_flow(tmp_path):
	"""不存在的文件要和空路径、目录两条分支一样给出可执行的下一步。

	原来这条分支只回「文件 '…' 不存在」，``recovery_action`` 是笼统的「修正参数」、
	``hints`` 为 null，Agent 不知道是该重新导出还是换个路径。
	"""
	result = _invoke(CliRunner(), tmp_path, ["import", str(tmp_path / "missing.json")])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	_assert_error_envelope(parsed, "INVALID_PARAM")
	assert "不存在" in parsed["error"]["message"]
	assert parsed["error"]["recovery_action"] == _hint("resume import <简历JSON路径>", tmp_path)
	assert parsed["hints"]["next_actions"] == [_hint("resume import <file>", tmp_path), _hint("resume list", tmp_path)]


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
		assert parsed["hints"]["next_actions"] == [_hint("resume list", tmp_path)]

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
	assert parsed["hints"]["next_actions"] == [
		_hint("resume applications linktest", tmp_path),
		_hint("apply sid-001 jid-001", tmp_path),
	]


def test_link_hints_quote_identifiers_with_shell_metacharacters(tmp_path):
	"""security_id / job_id 是自由文本，拼进 ``boss apply`` 提示前必须转义。"""
	runner = CliRunner()
	_invoke(runner, tmp_path, ["init", "--name", "quotetest", "--template", "default"])
	result = _invoke(runner, tmp_path, ["link", "quotetest", "sid; echo pwn", "jid $(id)"])
	assert result.exit_code == 0, result.output
	parsed = json.loads(result.output)
	assert parsed["hints"]["next_actions"] == [
		_hint("resume applications quotetest", tmp_path),
		_hint("apply 'sid; echo pwn' 'jid $(id)'", tmp_path),
	]


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
	assert parsed["hints"]["next_actions"] == [_hint("resume show apptest", tmp_path)]


def test_applications_empty_hints_point_to_link(tmp_path):
	"""没有关联职位时下一步是关联一个职位，而不是再 show 一次简历。"""
	runner = CliRunner()
	_invoke(runner, tmp_path, ["init", "--name", "apptest", "--template", "default"])
	result = _invoke(runner, tmp_path, ["applications", "apptest"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["data"] == []
	assert parsed["hints"]["next_actions"] == [_hint("resume link apptest <security_id> <job_id>", tmp_path)]


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


# ── 空简历名 / 错误恢复动作 ─────────────────────────────────────


@pytest.mark.parametrize(
	"args",
	[
		["show", ""],
		["edit", "", "--field", "title", "--value", "x"],
		["delete", ""],
		["export", ""],
		["clone", "", "dst"],
		["diff", "", "other"],
		["diff", "other", ""],
		["link", "", "sid", "jid"],
		["applications", ""],
	],
)
def test_name_commands_reject_blank_resume_name(tmp_path, args):
	"""空简历名是漏填参数，不是「简历不存在」。

	以前这些命令都报 RESUME_NOT_FOUND「简历 '' 不存在」：错误码不可恢复，
	也看不出要补 NAME；同一类输入在 `resume init --name ""` 里是 INVALID_PARAM。
	"""
	result = _invoke(CliRunner(), tmp_path, args)

	assert result.exit_code == 1, result.output
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "INVALID_PARAM"
	assert "名称不能为空" in parsed["error"]["message"]
	# 空名字的下一步是列出已有简历，而不是笼统的「修正参数」。
	assert parsed["hints"]["next_actions"] == [_hint("resume list", tmp_path)]


def test_blank_name_rejected_for_whitespace_only(tmp_path):
	"""纯空格同样没有可点名的简历。"""
	result = _invoke(CliRunner(), tmp_path, ["show", "   "])

	assert result.exit_code == 1
	assert json.loads(result.output)["error"]["code"] == "INVALID_PARAM"


def test_resume_not_found_gives_executable_recovery(tmp_path):
	"""RESUME_NOT_FOUND 的 recovery_action 以前是 null，Agent 读到错误却无动作可执行。"""
	result = _invoke(CliRunner(), tmp_path, ["show", "missing"])

	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "RESUME_NOT_FOUND"
	assert parsed["error"]["recovery_action"] == _hint("resume list", tmp_path)


# ── 损坏的简历文件 ────────────────────────────────────────────


def _write_corrupt_resume(tmp_path, name: str = "broken") -> None:
	resumes = tmp_path / "resumes"
	resumes.mkdir(parents=True, exist_ok=True)
	(resumes / f"{name}.json").write_text("{bad json", encoding="utf-8")


def test_list_reports_corrupt_resume_files(tmp_path):
	"""读不出来的简历文件不能被列表隐藏：init 会被同名文件挡住。"""
	_write_corrupt_resume(tmp_path)
	result = _invoke(CliRunner(), tmp_path, ["list"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["data"] == []
	assert parsed["hints"]["corrupt"] == ["broken"]
	assert any("resume delete" in text and "--data-dir" in text for text in parsed["hints"]["operator_actions"])


def test_show_corrupt_resume_reports_file_invalid(tmp_path):
	"""文件存在但读不出来不是「简历不存在」：LIST 里也没有它，照提示会空转。"""
	_write_corrupt_resume(tmp_path)
	result = _invoke(CliRunner(), tmp_path, ["show", "broken"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	_assert_error_envelope(parsed, "RESUME_FILE_INVALID")
	assert parsed["error"]["recoverable"] is True
	assert "resume delete" in parsed["error"]["recovery_action"]
	assert "--data-dir" in parsed["error"]["recovery_action"]
	assert parsed["hints"]["next_actions"] == [_hint("resume delete broken", tmp_path), _hint("resume list", tmp_path)]


@pytest.mark.parametrize("name", ["我的 简历", "it's fine", "$HOME*"])
def test_show_corrupt_resume_quotes_delete_hint(tmp_path, name):
	"""简历名是自由文本：``resume delete`` 提示不转义时会被拆成多个参数。

	``boss resume delete 我的 简历`` 按 shell 解析是两个参数；``it's fine``
	会截断成非法引号；``$HOME*`` 会被展开。统一走 ``shell_argument`` 后，
	按 shell 解析要还原成同一个名字。
	"""
	_write_corrupt_resume(tmp_path, name=name)
	result = _invoke(CliRunner(), tmp_path, ["show", name])

	assert result.exit_code == 1
	parsed = json.loads(result.output)
	_assert_error_envelope(parsed, "RESUME_FILE_INVALID")
	hint = parsed["hints"]["next_actions"][0]
	assert shlex.split(hint) == ["boss", "--data-dir", str(tmp_path.resolve()), "resume", "delete", name]


def test_clone_corrupt_resume_not_local_io_error(tmp_path):
	"""clone 以前把 FileNotFoundError 漏到顶层，报成「本地文件读写失败」+ 查磁盘。"""
	_write_corrupt_resume(tmp_path)
	result = _invoke(CliRunner(), tmp_path, ["clone", "broken", "newone"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	_assert_error_envelope(parsed, "RESUME_FILE_INVALID")
	assert not (tmp_path / "resumes" / "newone.json").exists()


def test_delete_corrupt_resume_still_removes_file(tmp_path):
	"""损坏文件必须能被删除，否则它会一直挡着同名 init。"""
	_write_corrupt_resume(tmp_path)
	runner = CliRunner()
	deleted = _invoke(runner, tmp_path, ["delete", "broken"])
	assert deleted.exit_code == 0
	assert json.loads(deleted.output)["data"]["deleted"] is True
	assert not (tmp_path / "resumes" / "broken.json").exists()
	listed = json.loads(_invoke(runner, tmp_path, ["list"]).output)
	assert "corrupt" not in listed["hints"]
