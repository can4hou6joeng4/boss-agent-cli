"""简历别名与删除顺序的回归（Issue #1804 #1805）。"""

from __future__ import annotations

import json

from click.testing import CliRunner

from boss_agent_cli.main import cli


def _run(tmp_path, *args):
	out = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "resume", *args])
	return out.exit_code, json.loads(out.output)


def test_edit_through_alias_keeps_the_stored_name(tmp_path):
	assert _run(tmp_path, "init", "--name", "a/b")[0] == 0
	code, payload = _run(tmp_path, "edit", "a_b", "--field", "title", "--value", "X")
	assert code == 0, payload
	stored = json.loads((tmp_path / "resumes" / "a_b.json").read_text(encoding="utf-8"))
	assert stored["name"] == "a/b"
	assert stored["title"] == "X"


def test_delete_through_alias_clears_links_of_the_real_name(tmp_path):
	_run(tmp_path, "init", "--name", "a/b")
	assert _run(tmp_path, "link", "a/b", "sid-1", "jid-1")[0] == 0
	code, payload = _run(tmp_path, "delete", "a_b")
	assert code == 0, payload
	assert payload["data"]["removed_links"] == 1
	_run(tmp_path, "init", "--name", "a/b")
	code, payload = _run(tmp_path, "applications", "a/b")
	assert code == 0 and payload["data"] == []


def test_copied_file_with_foreign_name_is_not_trusted(tmp_path):
	_run(tmp_path, "init", "--name", "foo")
	resumes = tmp_path / "resumes"
	(resumes / "bar.json").write_text((resumes / "foo.json").read_text(encoding="utf-8"), encoding="utf-8")
	code, payload = _run(tmp_path, "edit", "bar", "--field", "title", "--value", "COPY")
	assert code == 0, payload
	assert json.loads((resumes / "bar.json").read_text(encoding="utf-8"))["title"] == "COPY"
	assert json.loads((resumes / "foo.json").read_text(encoding="utf-8"))["title"] != "COPY"


def test_delete_directory_path_fails_without_touching_links(tmp_path):
	_run(tmp_path, "init", "--name", "d1")
	_run(tmp_path, "link", "d1", "sid-1", "jid-1")
	path = tmp_path / "resumes" / "d1.json"
	path.unlink()
	path.mkdir()
	code, payload = _run(tmp_path, "delete", "d1")
	assert code == 1
	assert payload["error"]["code"] == "LOCAL_IO_ERROR"
	assert "关联未改动" in payload["error"]["message"]
	path.rmdir()
	_run(tmp_path, "init", "--name", "d1")
	code, payload = _run(tmp_path, "applications", "d1")
	assert len(payload["data"]) == 1


def test_plain_delete_still_removes_file_and_links(tmp_path):
	_run(tmp_path, "init", "--name", "d1")
	_run(tmp_path, "link", "d1", "sid-1", "jid-1")
	code, payload = _run(tmp_path, "delete", "d1")
	assert code == 0 and payload["data"]["removed_links"] == 1
	assert not (tmp_path / "resumes" / "d1.json").exists()
