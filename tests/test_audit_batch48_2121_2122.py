"""审计批次：resume 关联别名口径与导入路径 ~ 展开（Issue #2121、#2122）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from boss_agent_cli.main import cli


def _invoke(tmp_path: Path, *args: str):
	runner = CliRunner()
	return runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "resume", *args])


def _payload(result) -> dict:
	assert result.exit_code in {0, 1}, result.output
	return json.loads(result.output)


# ── #2121：关联落库 / 查询 / 删除统一到文件真实名称 ──────────────────


def test_alias_link_is_visible_from_canonical_name(tmp_path: Path) -> None:
	assert _payload(_invoke(tmp_path, "init", "--name", "a_b"))["ok"] is True
	linked = _payload(_invoke(tmp_path, "link", "a/b", "SID", "JID"))
	assert linked["ok"] is True

	canonical = _payload(_invoke(tmp_path, "applications", "a_b"))
	alias = _payload(_invoke(tmp_path, "applications", "a/b"))

	assert [item["job_id"] for item in canonical["data"]] == ["JID"]
	assert [item["job_id"] for item in alias["data"]] == ["JID"]


def test_canonical_link_is_visible_from_alias(tmp_path: Path) -> None:
	assert _payload(_invoke(tmp_path, "init", "--name", "a_b"))["ok"] is True
	assert _payload(_invoke(tmp_path, "link", "a_b", "SID", "JID"))["ok"] is True

	alias = _payload(_invoke(tmp_path, "applications", "a/b"))

	assert [item["job_id"] for item in alias["data"]] == ["JID"]


def test_delete_canonical_name_removes_alias_links(tmp_path: Path) -> None:
	assert _payload(_invoke(tmp_path, "init", "--name", "a_b"))["ok"] is True
	assert _payload(_invoke(tmp_path, "link", "a/b", "SID", "JID"))["ok"] is True

	deleted = _payload(_invoke(tmp_path, "delete", "a_b"))
	assert deleted["ok"] is True
	assert deleted["data"]["removed_links"] == 1

	assert _payload(_invoke(tmp_path, "init", "--name", "a_b"))["ok"] is True
	after = _payload(_invoke(tmp_path, "applications", "a/b"))

	assert after["ok"] is True
	assert after["data"] == []


def test_applications_without_links_stays_empty(tmp_path: Path) -> None:
	assert _payload(_invoke(tmp_path, "init", "--name", "demo"))["ok"] is True

	result = _payload(_invoke(tmp_path, "applications", "demo"))

	assert result["ok"] is True
	assert result["data"] == []


# ── #2122：import 输入路径与 -o 同口径展开 ~ ────────────────────────


def test_import_expands_tilde_against_home(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
	home = tmp_path / "home"
	home.mkdir()
	(home / "cv.json").write_text(json.dumps({"name": "demo"}), encoding="utf-8")
	monkeypatch.setenv("HOME", str(home))

	result = _payload(_invoke(tmp_path, "import", "~/cv.json"))

	assert result["ok"] is True, result
	assert result["data"]["name"] == "demo"


def test_import_unknown_user_tilde_is_reported_as_expansion_failure(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
	monkeypatch.setenv("HOME", str(tmp_path))
	monkeypatch.chdir(tmp_path)

	result = _payload(_invoke(tmp_path, "import", "~no-such-user/cv.json"))

	assert result["ok"] is False
	assert result["error"]["code"] == "INVALID_PARAM"
	# ~某用户 无法展开时说清是家目录写法的问题，别伪装成文件缺失（Issue #2289）
	assert "家目录写法无法展开" in result["error"]["message"]
	assert not (tmp_path / "~no-such-user").exists()


def test_import_absolute_path_still_works(tmp_path: Path) -> None:
	path = tmp_path / "resume.json"
	path.write_text(json.dumps({"name": "demo"}), encoding="utf-8")

	result = _payload(_invoke(tmp_path, "import", str(path)))

	assert result["ok"] is True
	assert result["data"]["name"] == "demo"
