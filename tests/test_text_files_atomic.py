"""导出文本统一走原子写（Issue #1171）。

`Path.write_text` / `open("w")` 会先截断目标文件：写入中途失败（Ctrl-C、
进程被杀、磁盘写满）会留下半截报表并毁掉上一次的完整导出。职位导出已在
#1137 改为原子替换，本文件锁住其余三处人工/Agent 直接打开的导出：

- `digest --format md -o`；
- `stats --format html -o`；
- `chat --export -o`。

服务层测试覆盖 `write_text_atomic` 本身：成功写、失败保留旧文件、临时文件
不残留；命令层测试覆盖写入失败时返回 `LOCAL_IO_ERROR` 信封且旧文件不变。
"""

import errno
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.main import cli
from boss_agent_cli.output import is_local_io_error
from boss_agent_cli.services.json_files import write_json_atomic
from boss_agent_cli.services.private_files import write_private_bytes
from boss_agent_cli.services.text_files import write_text_atomic


def _write_error(path: Path) -> OSError:
	"""带 filename 的本地 IO 错误：main 才会归类为 LOCAL_IO_ERROR。"""
	return OSError(errno.ENOSPC, "No space left on device", str(path))


def _fd_level_write_error() -> OSError:
	"""fd 级写失败的真实形态：os.fsync / handle.write 抛出的 OSError 没有 filename。"""
	return OSError(errno.ENOSPC, "No space left on device")


# ── 服务层 ──────────────────────────────────────────────────────────


def test_write_text_atomic_writes_content_and_leaves_no_temp_file(tmp_path: Path):
	target = tmp_path / "report.md"
	result = write_text_atomic(target, "完整内容\n第二行\n")

	assert result == target
	assert target.read_text(encoding="utf-8") == "完整内容\n第二行\n"
	assert [p.name for p in tmp_path.iterdir()] == ["report.md"]


def test_write_text_atomic_creates_parent_directories(tmp_path: Path):
	target = tmp_path / "nested" / "deeper" / "report.md"
	write_text_atomic(target, "body")
	assert target.read_text(encoding="utf-8") == "body"


def test_write_text_atomic_preserves_existing_file_on_write_failure(tmp_path: Path):
	"""写入中途失败不能截断已有文件（旧实现正是先截断再写）。"""
	target = tmp_path / "report.md"
	target.write_text("上一次的完整报表", encoding="utf-8")

	with patch("os.fsync", side_effect=_write_error(target)):
		with pytest.raises(OSError):
			write_text_atomic(target, "本次的新内容")

	assert target.read_text(encoding="utf-8") == "上一次的完整报表"
	assert [p.name for p in tmp_path.iterdir()] == ["report.md"]


def test_write_text_atomic_newline_passthrough(tmp_path: Path):
	"""聊天 CSV 导出依赖 newline="" 保留原换行。"""
	target = tmp_path / "chat.csv"
	write_text_atomic(target, "a,b\r\n1,2\r\n", newline="")
	assert target.read_bytes() == b"a,b\r\n1,2\r\n"


# ── fd 级写入失败：异常补上目标路径（Issue #1400） ───────────────────


@pytest.mark.parametrize(
	"write",
	[
		pytest.param(lambda path: write_text_atomic(path, "new content"), id="text"),
		pytest.param(lambda path: write_json_atomic(path, {"new": True}), id="json"),
		pytest.param(lambda path: write_private_bytes(path, b"new content"), id="private"),
	],
)
def test_fd_level_write_failure_gets_target_filename(write, tmp_path: Path):
	"""磁盘满（ENOSPC）在 fd 层抛出时 filename 为 None：写入入口要补上目标路径，
	分类才会落成本地 IO 错误；原文件与目录内容保持不变。"""
	target = tmp_path / "state.bin"
	target.write_bytes(b"previous content")

	with patch("os.fsync", side_effect=_fd_level_write_error()):
		with pytest.raises(OSError) as raised:
			write(target)

	assert raised.value.errno == errno.ENOSPC
	assert raised.value.filename == str(target)
	assert is_local_io_error(raised.value) is True
	assert target.read_bytes() == b"previous content"
	assert [item.name for item in tmp_path.iterdir()] == ["state.bin"]


# ── 命令层：写入失败给 LOCAL_IO_ERROR，旧文件保留 ────────────────────


def _assert_local_io_error(result, target: Path, previous: str) -> None:
	assert result.exit_code == 1, result.output
	envelope = json.loads(result.output)
	assert envelope["ok"] is False
	assert envelope["error"]["code"] == "LOCAL_IO_ERROR", envelope["error"]
	assert target.read_text(encoding="utf-8") == previous
	# 写入失败后不留下半截临时文件（命令可能同时创建快照目录，只看目标名）。
	assert not list(target.parent.glob(f".{target.name}.*"))


def _ctx_mock(mock_cls):
	instance = mock_cls.return_value
	instance.__enter__ = lambda self: self
	instance.__exit__ = lambda self, *a: None
	instance.unwrap_data.side_effect = lambda response: response.get("zpData") if "zpData" in response else response.get("data")
	instance.is_success.side_effect = lambda response: response.get("code", 0) in (0, 200)
	return instance


def _friend_item() -> dict:
	return {
		"uid": 10001,
		"securityId": "sid_current",
		"name": "联系人甲",
		"brandName": "示例公司",
		"title": "招聘专员",
		"friendSource": 0,
		"encryptJobId": "job_001",
		"lastMsg": "你好",
		"lastTS": 1700000000000,
		"unreadMsgCount": 0,
		"relationType": 2,
		"lastMessageInfo": {"status": 2},
	}


def _setup_digest_mocks(mock_client_cls) -> None:
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.return_value = {"zpData": {"result": [_friend_item()]}}
	mock_client.interview_data.return_value = {"zpData": {"interviewList": []}}


@patch("boss_agent_cli.commands.digest.get_platform_instance")
@patch("boss_agent_cli.commands.digest.AuthManager")
def test_digest_write_failure_returns_local_io_error(mock_auth_cls, mock_client_cls, tmp_path: Path):
	"""digest -o 写入失败时旧报表保留，信封为 LOCAL_IO_ERROR。"""
	_setup_digest_mocks(mock_client_cls)
	target = tmp_path / "digest.md"
	target.write_text("上一次的完整日报", encoding="utf-8")

	with patch("boss_agent_cli.commands.digest.write_text_atomic", side_effect=_write_error(target)):
		result = CliRunner().invoke(
			cli,
			["--json", "--data-dir", str(tmp_path), "digest", "--format", "md", "-o", str(target)],
		)

	_assert_local_io_error(result, target, "上一次的完整日报")


def test_stats_write_failure_returns_local_io_error(tmp_path: Path):
	"""stats --format html -o 写入失败时旧报表保留，信封为 LOCAL_IO_ERROR。"""
	target = tmp_path / "stats.html"
	target.write_text("上一次的完整报表", encoding="utf-8")

	with patch("boss_agent_cli.commands.stats.write_text_atomic", side_effect=_write_error(target)):
		result = CliRunner().invoke(
			cli,
			["--json", "--data-dir", str(tmp_path), "stats", "--format", "html", "-o", str(target)],
		)

	_assert_local_io_error(result, target, "上一次的完整报表")


def test_stats_fd_level_write_failure_returns_local_io_error(tmp_path: Path):
	"""os.fsync 在 fd 层抛 ENOSPC（没有 filename）时，信封必须仍不可重试。

	以前只有带 filename 的 OSError 才被归成本地错误，磁盘满会兜成可重试的
	NETWORK_ERROR，Agent 会无限重试同一条写不进去的命令（Issue #1400）。
	"""
	target = tmp_path / "stats.html"
	target.write_text("上一次的完整报表", encoding="utf-8")

	with patch("os.fsync", side_effect=_fd_level_write_error()):
		result = CliRunner().invoke(
			cli,
			["--json", "--data-dir", str(tmp_path), "stats", "--format", "html", "-o", str(target)],
		)

	_assert_local_io_error(result, target, "上一次的完整报表")


@patch("boss_agent_cli.commands.chat.get_platform_instance")
@patch("boss_agent_cli.commands.chat.AuthManager")
def test_chat_export_write_failure_returns_local_io_error(mock_auth_cls, mock_client_cls, tmp_path: Path):
	"""chat --export -o 写入失败时旧导出保留，信封为 LOCAL_IO_ERROR。"""
	client = _ctx_mock(mock_client_cls)
	client.friend_list.return_value = {"zpData": {"result": [_friend_item()], "friendList": [_friend_item()]}}
	target = tmp_path / "chat.md"
	target.write_text("上一次的完整导出", encoding="utf-8")

	with patch("boss_agent_cli.commands.chat.write_text_atomic", side_effect=_write_error(target)):
		result = CliRunner().invoke(
			cli,
			["--json", "--data-dir", str(tmp_path), "chat", "--export", "md", "-o", str(target)],
		)

	_assert_local_io_error(result, target, "上一次的完整导出")


def test_resume_export_json_write_failure_returns_local_io_error(tmp_path: Path):
	"""resume export --format json -o 写入失败时旧导出保留，信封为 LOCAL_IO_ERROR。"""
	runner = CliRunner()
	runner.invoke(
		cli,
		["--data-dir", str(tmp_path), "--json", "resume", "init", "--name", "atomic", "--template", "default"],
	)
	target = tmp_path / "resume.json"
	target.write_text("上一次的完整导出", encoding="utf-8")

	with patch("boss_agent_cli.commands.resume_cmd.write_text_atomic", side_effect=_write_error(target)):
		result = runner.invoke(
			cli,
			["--data-dir", str(tmp_path), "--json", "resume", "export", "atomic", "--format", "json", "-o", str(target)],
		)

	_assert_local_io_error(result, target, "上一次的完整导出")
