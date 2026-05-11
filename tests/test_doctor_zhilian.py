"""验证 boss doctor 输出包含智联连通性检查项。"""
import json
from pathlib import Path
from unittest.mock import patch

import httpx
from click.testing import CliRunner

from boss_agent_cli.main import cli


def test_doctor_includes_zhilian_network_check(tmp_path: Path) -> None:
	"""boss doctor 应输出 network_zhilian 检查项。"""
	def fake_get(url: str, **kwargs: object) -> httpx.Response:
		return httpx.Response(200, request=httpx.Request("GET", url))

	with (
		patch("httpx.get", side_effect=fake_get),
		patch("boss_agent_cli.commands.doctor.extract_cookies", return_value=None),
	):
		runner = CliRunner()
		result = runner.invoke(cli, ["--data-dir", str(tmp_path), "doctor"])
	assert result.exit_code in (0, 1), result.output
	envelope = json.loads(result.output)
	checks = {item["name"]: item for item in envelope["data"]["checks"]}
	assert "network_zhilian" in checks
	assert checks["network_zhilian"]["status"] in {"ok", "warn", "error"}


def test_doctor_zhilian_check_handles_network_error(tmp_path: Path) -> None:
	"""智联端点不可达时应返回 error/warn 而不是抛异常。"""
	def fake_get(url: str, **kwargs: object) -> httpx.Response:
		raise httpx.ConnectError("network down")

	with (
		patch("httpx.get", side_effect=fake_get),
		patch("boss_agent_cli.commands.doctor.extract_cookies", return_value=None),
	):
		runner = CliRunner()
		result = runner.invoke(cli, ["--data-dir", str(tmp_path), "doctor"])
	assert result.exit_code in (0, 1), result.output
	envelope = json.loads(result.output)
	checks = {item["name"]: item for item in envelope["data"]["checks"]}
	assert checks["network_zhilian"]["status"] in {"warn", "error"}
