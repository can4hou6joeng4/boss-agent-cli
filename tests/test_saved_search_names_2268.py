"""预设 / watch 名称里不可存储的字符按 INVALID_PARAM 收口（Issue #2268）。

argv 里的非法 UTF-8 字节经 surrogateescape、JSON 的 ``"\\udcff"`` 转义都会
产生孤立代理字符；此前这类名称一路走到 SQLite 绑定参数才抛
``UnicodeEncodeError``，被兜成不可恢复的 ``LOCAL_IO_ERROR``，恢复指引还指向
磁盘权限。全部离线，只用合成输入。
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from boss_agent_cli.cache.store import saved_search_name_error
from boss_agent_cli.main import cli

SURROGATE_NAME = "pre\udcffset"


def _invoke(tmp_path, *argv: str):
	return CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", *argv])


def test_helper_accepts_regular_unicode_and_rejects_lone_surrogates():
	assert saved_search_name_error("golang-上海") is None
	assert saved_search_name_error(SURROGATE_NAME) is not None


@pytest.mark.parametrize(
	"argv",
	[
		("preset", "add", SURROGATE_NAME, "python"),
		("preset", "remove", SURROGATE_NAME),
		("watch", "add", SURROGATE_NAME, "python"),
		("watch", "remove", SURROGATE_NAME),
		("watch", "run", SURROGATE_NAME),
		("search", "python", "--preset", SURROGATE_NAME),
	],
)
def test_unstorable_names_are_rejected_before_storage(tmp_path, argv):
	result = _invoke(tmp_path, *argv)

	assert result.exit_code == 1, result.output
	envelope = json.loads(result.output)
	assert envelope["ok"] is False
	assert envelope["error"]["code"] == "INVALID_PARAM"
	assert envelope["error"]["recoverable"] is False
	assert "孤立代理项" in envelope["error"]["message"]
	assert "磁盘" not in (envelope["error"]["recovery_action"] or "")


def test_regular_names_still_round_trip(tmp_path):
	added = _invoke(tmp_path, "preset", "add", "golang-上海", "python")
	listed = _invoke(tmp_path, "preset", "list")
	removed = _invoke(tmp_path, "preset", "remove", "golang-上海")

	assert added.exit_code == 0, added.output
	assert "golang-上海" in listed.output
	assert removed.exit_code == 0, removed.output
	assert json.loads(removed.output)["data"]["removed"] is True
