"""本地简历 JSON 的字段类型漂移必须在读取边界报成损坏（Issue #1189）。

同一份坏文件此前在三个命令里得到三种判定：``resume list`` 当正常条目、
``resume show`` 原样返回错误类型、``resume export --format html`` 把模板里的
``'str' object has no attribute 'get'`` 兜成 ``EXPORT_FAILED``。这里固定三条
一致性：损坏文件被列表点名、show/export 返回 ``RESUME_FILE_INVALID`` 且消息
指出字段，合法旧格式不回归。
"""

import json

from click.testing import CliRunner

from boss_agent_cli.main import cli

_VALID_RESUME = {
	"name": "ok",
	"title": "后端工程师",
	"personal_info": {"items": [{"label": "电话", "value": "000"}], "layout": "inline"},
	"modules": [{"id": "summary", "title": "概述", "rows": [{"type": "richtext", "content": ["一行"]}]}],
}


def _seed(tmp_path, name: str, payload) -> None:
	resumes = tmp_path / "resumes"
	resumes.mkdir(exist_ok=True)
	(resumes / f"{name}.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _invoke(tmp_path, *args: str):
	return CliRunner().invoke(cli, ["--json", "--data-dir", str(tmp_path), "resume", *args])


def _assert_file_invalid(result, *, field: str) -> None:
	assert result.exit_code == 1, result.output
	payload = json.loads(result.stdout)
	assert payload["ok"] is False
	assert payload["error"]["code"] == "RESUME_FILE_INVALID"
	assert field in payload["error"]["message"]


def test_rows_element_must_be_object(tmp_path):
	_seed(tmp_path, "rowsbad", {**_VALID_RESUME, "name": "rowsbad", "modules": [{"id": "m", "title": "M", "rows": ["oops"]}]})

	_assert_file_invalid(_invoke(tmp_path, "show", "rowsbad"), field="modules[0].rows[0]")
	_assert_file_invalid(
		_invoke(tmp_path, "export", "rowsbad", "--format", "html", "--output", str(tmp_path / "out.html")),
		field="modules[0].rows[0]",
	)
	list_result = _invoke(tmp_path, "list")
	assert list_result.exit_code == 0, list_result.output
	payload = json.loads(list_result.stdout)
	assert payload["data"] == []
	assert payload["hints"]["corrupt"] == ["rowsbad"]


def test_name_must_be_string(tmp_path):
	_seed(tmp_path, "namebad", {"name": {"a": 1}})

	_assert_file_invalid(_invoke(tmp_path, "show", "namebad"), field="name")
	list_result = _invoke(tmp_path, "list")
	assert list_result.exit_code == 0, list_result.output
	payload = json.loads(list_result.stdout)
	assert payload["data"] == []
	assert payload["hints"]["corrupt"] == ["namebad"]


def test_personal_info_must_be_object(tmp_path):
	_seed(tmp_path, "pibad", {"name": "pibad", "personal_info": "oops"})

	_assert_file_invalid(_invoke(tmp_path, "show", "pibad"), field="personal_info")
	list_result = _invoke(tmp_path, "list")
	assert json.loads(list_result.stdout)["hints"]["corrupt"] == ["pibad"]


def test_import_reports_field_level_error(tmp_path):
	broken = tmp_path / "broken.json"
	broken.write_text(json.dumps({"name": "broken", "modules": [{"id": "m", "title": "M", "rows": ["oops"]}]}), encoding="utf-8")

	result = _invoke(tmp_path, "import", str(broken))

	assert result.exit_code == 1, result.output
	payload = json.loads(result.stdout)
	assert payload["ok"] is False
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert "modules[0].rows[0]" in payload["error"]["message"]


def test_valid_resume_still_loads_lists_and_exports(tmp_path):
	_seed(tmp_path, "ok", _VALID_RESUME)

	show = _invoke(tmp_path, "show", "ok")
	assert show.exit_code == 0, show.output
	assert json.loads(show.stdout)["data"]["name"] == "ok"
	list_result = _invoke(tmp_path, "list")
	payload = json.loads(list_result.stdout)
	assert [item["name"] for item in payload["data"]] == ["ok"]
	assert "corrupt" not in payload["hints"]
	export = _invoke(tmp_path, "export", "ok", "--format", "html", "--output", str(tmp_path / "ok.html"))
	assert export.exit_code == 0, export.output
