"""简历命令的后继与恢复提示在产生它的数据目录中直接重放。"""

import io
import json
import shlex
from pathlib import Path

import pytest
from click.testing import CliRunner

from boss_agent_cli.main import cli

_RESUME_JSON = {
	"name": "imported",
	"title": "Imported Resume",
	"center_title": False,
	"personal_info": {"items": [], "layout": "inline"},
	"modules": [],
	"avatar": "",
}


@pytest.fixture
def resume_dir(tmp_path, monkeypatch):
	"""数据目录含空格、单引号与 Rich 标签；默认目录指向一次性 HOME。"""
	monkeypatch.setenv("HOME", str(tmp_path / "home"))
	return tmp_path / "jobs [bold] team's"


def _run(data_dir, *args, json_mode=True):
	prefix = ["--data-dir", str(data_dir), "--json"] if json_mode else ["--data-dir", str(data_dir)]
	return CliRunner().invoke(cli, [*prefix, *args])


def _hint_argv(action, data_dir):
	"""把提示还原成 argv：前缀必须是原目录。"""
	parts = shlex.split(action)
	assert parts[:3] == ["boss", "--data-dir", str(data_dir.resolve())], action
	return parts[1:]


def _replay(action, data_dir, **placeholders):
	# 只使用提示里的参数，调用方不补回 --data-dir。
	values = {
		"<path>": "",
		"<file>": "",
		"<name>": "default",
		"<新标题>": "新标题",
		"<新名>": "renamed",
		"<security_id>": "sid_replay",
		"<job_id>": "jid_replay",
		**placeholders,
	}
	argv = [values.get(part, part) for part in _hint_argv(action, data_dir)]
	result = CliRunner().invoke(cli, ["--json", *argv])
	assert result.exit_code == 0, result.output
	return json.loads(result.stdout)


def _default_resumes():
	return Path.home() / ".boss-agent" / "resumes"


def test_resume_init_followups_replay_in_original_directory(resume_dir):
	result = _run(resume_dir, "resume", "init", "--name", "后端")
	assert result.exit_code == 0, result.output
	actions = json.loads(result.stdout)["hints"]["next_actions"]
	# show / edit / me / import 四条后继都属于同一份本地简历。
	assert [shlex.split(action)[3] for action in actions] == ["resume", "resume", "me", "resume"]
	for action in actions:
		_hint_argv(action, resume_dir)

	shown = _replay(actions[0], resume_dir)
	assert shown["data"]["name"] == "后端"
	edited = _replay(actions[1], resume_dir)
	assert edited["data"]["field"] == "title"
	assert edited["data"]["value"] == "新标题"
	# 默认目录没有被提示写出第二份简历。
	assert not _default_resumes().exists()


def test_resume_list_empty_points_at_init_in_same_directory(resume_dir):
	result = _run(resume_dir, "resume", "list")
	assert result.exit_code == 0, result.output
	(action,) = json.loads(result.stdout)["hints"]["next_actions"]
	payload = _replay(action, resume_dir)
	assert payload["data"]["name"] == "default"
	assert (resume_dir / "resumes" / "default.json").exists()
	assert not _default_resumes().exists()


def test_resume_show_export_hint_replays_in_original_directory(resume_dir):
	_run(resume_dir, "resume", "init", "--name", "我的 简历")
	result = _run(resume_dir, "resume", "show", "我的 简历")
	assert result.exit_code == 0, result.output
	actions = json.loads(result.stdout)["hints"]["next_actions"]
	export_action = next(action for action in actions if "resume export" in action)
	assert shlex.split(export_action)[3:] == ["resume", "export", "我的 简历", "--format", "json"]
	exported = _replay(export_action, resume_dir)
	assert exported["data"]["data"]["name"] == "我的 简历"


def test_resume_edit_name_field_clone_hint_replays_in_original_directory(resume_dir):
	_run(resume_dir, "resume", "init", "--name", "oldname")
	result = _run(resume_dir, "resume", "edit", "oldname", "--field", "name", "--value", "newname")
	assert result.exit_code == 1, result.output
	parsed = json.loads(result.stdout)
	assert parsed["error"]["code"] == "INVALID_PARAM"
	# 目录含单引号，正文里是 shell 转义后的写法：逐段还原后必须指回原目录。
	clone_segment = parsed["error"]["recovery_action"].split("先用 ", 1)[1].split(" 复制出", 1)[0]
	_hint_argv(clone_segment, resume_dir)
	(action,) = parsed["hints"]["next_actions"]
	cloned = _replay(action, resume_dir)
	assert cloned["data"]["name"] == "renamed"


def test_resume_import_retry_replays_in_original_directory(resume_dir, tmp_path):
	source = tmp_path / "resume.json"
	source.write_text(json.dumps(_RESUME_JSON), encoding="utf-8")
	result = _run(resume_dir, "resume", "import", str(tmp_path / "missing.json"))
	assert result.exit_code == 1, result.output
	parsed = json.loads(result.stdout)
	assert parsed["error"]["code"] == "INVALID_PARAM"
	actions = parsed["hints"]["next_actions"]
	# 恢复正文与 next_actions 都指回原目录；重试用真实文件后直接成功。
	assert "--data-dir" in parsed["error"]["recovery_action"]
	imported = _replay(actions[0], resume_dir, **{"<file>": str(source)})
	assert imported["data"]["name"] == "imported"
	assert (resume_dir / "resumes" / "imported.json").exists()
	assert not _default_resumes().exists()


def test_resume_link_followups_replay_in_original_directory(resume_dir):
	_run(resume_dir, "resume", "init", "--name", "linktest")
	result = _run(resume_dir, "resume", "link", "linktest", "sid-1", "jid-1")
	assert result.exit_code == 0, result.output
	actions = json.loads(result.stdout)["hints"]["next_actions"]
	assert any(shlex.split(action)[3:] == ["apply", "sid-1", "jid-1"] for action in actions)
	applications = _replay(actions[0], resume_dir)
	assert applications["data"][0]["security_id"] == "sid-1"
	assert applications["data"][0]["resume_name"] == "linktest"


def test_resume_applications_empty_link_hint_replays_in_original_directory(resume_dir):
	_run(resume_dir, "resume", "init", "--name", "apptest")
	result = _run(resume_dir, "resume", "applications", "apptest")
	assert result.exit_code == 0, result.output
	(action,) = json.loads(result.stdout)["hints"]["next_actions"]
	linked = _replay(action, resume_dir)
	assert linked["data"]["action"] == "link"
	assert linked["data"]["security_id"] == "sid_replay"

	listed = _run(resume_dir, "resume", "applications", "apptest")
	assert [item["job_id"] for item in json.loads(listed.stdout)["data"]] == ["jid_replay"]


def test_resume_corrupt_delete_hint_replays_in_original_directory(resume_dir):
	resumes = resume_dir / "resumes"
	resumes.mkdir(parents=True)
	(resumes / "broken.json").write_text("{bad json", encoding="utf-8")
	result = _run(resume_dir, "resume", "show", "broken")
	assert result.exit_code == 1, result.output
	parsed = json.loads(result.stdout)
	assert parsed["error"]["code"] == "RESUME_FILE_INVALID"
	# 目录含单引号，正文里是 shell 转义后的写法。
	assert "--data-dir" in parsed["error"]["recovery_action"]
	assert "resume delete broken" in parsed["error"]["recovery_action"]
	deleted = _replay(parsed["hints"]["next_actions"][0], resume_dir)
	assert deleted["data"]["deleted"] is True
	assert not (resumes / "broken.json").exists()


def test_resume_tty_next_steps_keep_directory_with_rich_tags(resume_dir, monkeypatch):
	stream = io.StringIO()

	from rich.console import Console

	monkeypatch.setattr("boss_agent_cli.display.console", Console(file=stream, force_terminal=False, width=200))
	monkeypatch.setattr("boss_agent_cli.display.is_json_mode", lambda ctx: False)
	result = _run(resume_dir, "resume", "init", "--name", "tty", json_mode=False)

	assert result.exit_code == 0, result.output
	text = stream.getvalue()
	assert "--data-dir" in text
	assert "resume show tty" in text
	# 目录里的 [bold] 必须按字面显示，而不是被 Rich 当成标签吞掉。
	assert "[bold]" in text
