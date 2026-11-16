"""HTML 导出空值、`-o` 符号链接与聊天快照并发写的回归测试。

- Issue #1354：HTML 导出把 null 字段渲染成字面量 "None"；
- Issue #1355：`-o` 指向符号链接时原子写入替换链接本身；
- Issue #1356：并发 `chat --export` 的读-合并-写丢失另一页联系人。
"""

import datetime
import json
import threading
from pathlib import Path
from typing import Any, Callable
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.main import cli
from boss_agent_cli.services.chat_snapshot import save_snapshot_and_diff
from boss_agent_cli.services.job_export import (
	public_html_export_item_from_api,
	render_html_export,
)
from boss_agent_cli.services.json_files import write_json_atomic
from boss_agent_cli.services.output_path import OutputPathProblem, inspect_output_path
from boss_agent_cli.services.private_files import write_private_bytes
from boss_agent_cli.services.text_files import write_text_atomic


def _symlink_or_skip(link: Path, target: Path) -> None:
	try:
		link.symlink_to(target)
	except (OSError, NotImplementedError):
		pytest.skip("当前环境不支持创建符号链接")


# ── Issue #1354：null 字段不能渲染成 "None" ────────────────────────────


def test_html_export_renders_null_platform_fields_as_empty():
	"""brandName / cityName / jobExperience / jobDegree 为 null 时输出空单元格。"""
	item = public_html_export_item_from_api(
		{
			"jobName": "Python 工程师",
			"brandName": None,
			"cityName": None,
			"jobExperience": None,
			"jobDegree": None,
			"skills": None,
			"welfareList": None,
		}
	)

	html = render_html_export([item])

	assert "Python 工程师" in html
	assert "None" not in html


# ── Issue #1355：符号链接目标按不可写路径拒绝 ──────────────────────────


def test_inspect_output_path_rejects_symlink_to_file(tmp_path):
	real = tmp_path / "real.md"
	real.write_text("original", encoding="utf-8")
	link = tmp_path / "link.md"
	_symlink_or_skip(link, real)

	target, problem = inspect_output_path(link)

	assert target == link
	assert problem == OutputPathProblem.NOT_REGULAR


def test_inspect_output_path_rejects_dangling_symlink(tmp_path):
	link = tmp_path / "link.md"
	_symlink_or_skip(link, tmp_path / "missing.md")
	assert link.is_symlink()
	assert not link.exists()

	target, problem = inspect_output_path(link)

	assert target == link
	assert problem == OutputPathProblem.NOT_REGULAR


@patch("boss_agent_cli.commands.chat.get_platform_instance")
@patch("boss_agent_cli.commands.chat.AuthManager")
def test_chat_export_refuses_symlink_output(mock_auth_cls, mock_platform_cls, tmp_path):
	"""`-o` 指向符号链接：平台请求前报 INVALID_PARAM，链接与真实目标都不变。"""
	real = tmp_path / "real.md"
	real.write_text("上一次的完整导出", encoding="utf-8")
	link = tmp_path / "link.md"
	_symlink_or_skip(link, real)

	result = CliRunner().invoke(
		cli,
		[
			"--data-dir",
			str(tmp_path),
			"chat",
			"--export",
			"md",
			"-o",
			str(link),
		],
	)

	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["ok"] is False
	assert parsed["error"]["code"] == "INVALID_PARAM"
	assert real.read_text(encoding="utf-8") == "上一次的完整导出"
	assert link.is_symlink()
	mock_auth_cls.assert_not_called()
	mock_platform_cls.assert_not_called()


@pytest.mark.parametrize(
	"write",
	[
		lambda path: write_text_atomic(path, "new content"),
		lambda path: write_json_atomic(path, {"new": True}),
		lambda path: write_private_bytes(path, b"new content"),
	],
	ids=["text", "json", "private"],
)
def test_atomic_writers_refuse_symlink_target(write: Callable[[Path], Any], tmp_path):
	"""三个原子写入入口都不能把链接替换成普通文件。"""
	real = tmp_path / "real.txt"
	real.write_bytes(b"original")
	link = tmp_path / "link.txt"
	_symlink_or_skip(link, real)

	with pytest.raises(OSError):
		write(link)

	assert link.is_symlink()
	assert real.read_bytes() == b"original"


# ── Issue #1356：并发写当日快照不能丢更新 ──────────────────────────────


def test_concurrent_snapshot_saves_keep_both_pages(tmp_path):
	"""两个并发导出各写一页联系人，最终快照必须同时包含两页。"""
	snapshot_dir = tmp_path / "chat-history"
	snapshot_dir.mkdir()
	today = datetime.date.today().isoformat()
	snapshot_path = snapshot_dir / f"{today}.json"
	snapshot_path.write_text("[]", encoding="utf-8")

	barrier = threading.Barrier(2)
	original_load = json.load

	def load_then_wait(handle):
		data = original_load(handle)
		try:
			# 未加锁的实现里两个线程都会先读完旧快照再写，这里让这个交错必然
			# 发生；加锁后第二个读者要等第一个写完才进入临界区，等不到同伴的
			# barrier 超时是预期结果，不能当成失败。
			barrier.wait(timeout=1)
		except threading.BrokenBarrierError:
			pass
		return data

	errors: list[BaseException] = []

	def save(uid: int, name: str) -> None:
		try:
			save_snapshot_and_diff(
				str(snapshot_dir),
				[{"uid": uid, "name": name}],
				MagicMock(),
			)
		except BaseException as exc:
			errors.append(exc)

	with patch("boss_agent_cli.services.chat_snapshot.json.load", side_effect=load_then_wait):
		threads = [
			threading.Thread(target=save, args=(10001, "联系人甲")),
			threading.Thread(target=save, args=(10002, "联系人乙")),
		]
		for thread in threads:
			thread.start()
		for thread in threads:
			thread.join(timeout=10)
		assert all(not thread.is_alive() for thread in threads)

	assert errors == []
	saved = json.loads(snapshot_path.read_text(encoding="utf-8"))
	assert {item["uid"] for item in saved} == {10001, 10002}
