"""本地导出失败必须保留可诊断的 errno 与原因（Issue #3156）。"""

import errno
import json
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from boss_agent_cli.main import cli


def _invoke(tmp_path: Path, args: list[str]):
	return CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "resume", *args])


def test_export_local_io_error_keeps_errno_and_cause(tmp_path: Path) -> None:
	_invoke(tmp_path, ["init", "--name", "cv"])
	target = tmp_path / "out.json"
	target.write_text("old", encoding="utf-8")
	reason = OSError(errno.EPERM, "Operation not permitted")

	with patch("boss_agent_cli.services.json_files.os.replace", side_effect=reason):
		result = _invoke(tmp_path, ["export", "cv", "--format", "json", "-o", str(target)])

	assert result.exit_code == 1, result.output
	payload = json.loads(result.output)
	assert payload["error"]["code"] == "LOCAL_IO_ERROR"
	details = payload["error"]["details"]
	assert details["errno"] == errno.EPERM
	assert details["strerror"] == "Operation not permitted"
	assert "Operation not permitted" in details["cause"]
