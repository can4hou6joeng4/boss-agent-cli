"""审计批次 126：简历职位关联的不可编码文本（Issue #2503）。

`resume_job_links` 直接绑定原始文本：argv 的孤立代理项、文件系统来源的简历名
（非 UTF-8 字节经 surrogateescape）都会在 sqlite3 绑定时抛
``UnicodeEncodeError``，被全局兜成带磁盘指引的 ``LOCAL_IO_ERROR``。argv 职位
字段在入口报 ``INVALID_PARAM``。含代理项的简历名以可逆方式绑定，保留文件名身份。
"""

from __future__ import annotations

import errno
import json
import os
from pathlib import Path

import pytest
from click.testing import CliRunner

from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.main import cli

_BAD = "\ud800"
_BAD_LITERAL = "\\ud800"


def _invoke(tmp_path: Path, *args: str):
	return CliRunner().invoke(cli, ["--json", "--data-dir", str(tmp_path), "resume", *args])


def _error(result) -> dict:
	assert result.exit_code == 1, result.output
	payload = json.loads(result.output)
	assert payload["ok"] is False
	return payload["error"]


def _db(tmp_path: Path) -> Path:
	return tmp_path / "cache" / "boss_agent.db"


@pytest.mark.parametrize(
	("label", "args"),
	[
		("security_id", ("link", "cv", _BAD, "job-1")),
		("job_id", ("link", "cv", "sec-1", _BAD)),
		("--title", ("link", "cv", "sec-1", "job-1", "--title", _BAD)),
		("--company", ("link", "cv", "sec-1", "job-1", "--company", _BAD)),
	],
	ids=["security_id", "job_id", "title", "company"],
)
def test_link_rejects_unencodable_job_fields(tmp_path: Path, label: str, args: tuple[str, ...]) -> None:
	created = _invoke(tmp_path, "init", "--name", "cv")
	assert created.exit_code == 0, created.output

	error = _error(_invoke(tmp_path, *args))

	assert error["code"] == "INVALID_PARAM"
	assert label in error["message"]
	assert "UTF-8" in error["message"]
	assert "磁盘" not in error["recovery_action"]
	with CacheStore(_db(tmp_path)) as cache:
		assert cache.get_resume_applications("cv") == []


def test_resume_names_with_surrogates_keep_separate_links(tmp_path: Path) -> None:
	filesystem_name = f"cv{_BAD}"
	literal_name = f"cv{_BAD_LITERAL}"
	with CacheStore(_db(tmp_path)) as cache:
		cache.link_resume_to_job(filesystem_name, "sec-1", "job-1", "标题", "")
		cache.link_resume_to_job(literal_name, "sec-1", "job-1", "另一份简历", "")
		assert {item["resume_name"] for item in cache.get_job_resumes("sec-1", "job-1")} == {
			filesystem_name,
			literal_name,
		}
		assert cache.update_job_link_status(filesystem_name, "sec-1", "job-1", "applied", "已投递")
		cache.link_resume_to_job(filesystem_name, "sec-2", "job-1", "", "")
		items = cache.get_resume_applications(filesystem_name)
		assert len(items) == 1
		assert items[0]["security_id"] == "sec-2"
		assert items[0]["job_title"] == "标题"
		assert items[0]["notes"] == "已投递"
		assert cache.remove_job_link(filesystem_name, "sec-2", "job-1")
		cache.link_resume_to_job(filesystem_name, "sec-3", "job-2", "", "")
		assert cache.remove_resume_links(filesystem_name) == 1
		assert cache.get_resume_applications(filesystem_name) == []
		assert cache.get_resume_applications(literal_name)[0]["job_title"] == "另一份简历"


@pytest.mark.skipif(os.name != "posix", reason="需要支持原始文件名字节的 POSIX 文件系统")
def test_cli_links_lists_and_deletes_resume_with_non_utf8_filename(tmp_path: Path) -> None:
	assert _invoke(tmp_path, "init", "--name", "cv").exit_code == 0
	name = os.fsdecode(b"cv\xff")
	path = tmp_path / "resumes" / f"{name}.json"
	try:
		(tmp_path / "resumes" / "cv.json").rename(path)
	except OSError as exc:
		if exc.errno in {errno.EILSEQ, errno.EINVAL, errno.ENOTSUP}:
			pytest.skip("当前文件系统拒绝非 UTF-8 文件名")
		raise

	linked = _invoke(tmp_path, "link", name, "sec-1", "job-1")
	assert linked.exit_code == 0, linked.output
	applications = _invoke(tmp_path, "applications", name)
	assert applications.exit_code == 0, applications.output
	assert json.loads(applications.output)["data"][0]["resume_name"] == name
	deleted = _invoke(tmp_path, "delete", name)
	assert deleted.exit_code == 0, deleted.output
	assert json.loads(deleted.output)["data"]["removed_links"] == 1
	assert not path.exists()


def test_normal_resume_link_flow_is_unchanged(tmp_path: Path) -> None:
	assert _invoke(tmp_path, "init", "--name", "cv").exit_code == 0

	linked = _invoke(tmp_path, "link", "cv", "sec-1", "job-1", "--title", "示例岗位")
	assert linked.exit_code == 0, linked.output
	assert json.loads(linked.output)["data"]["action"] == "link"

	applications = _invoke(tmp_path, "applications", "cv")
	assert applications.exit_code == 0, applications.output
	items = json.loads(applications.output)["data"]
	assert items[0]["job_title"] == "示例岗位"
	assert items[0]["security_id"] == "sec-1"
