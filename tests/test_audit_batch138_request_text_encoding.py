"""审计批次 138：请求参数的 UTF-8 可编码性在入口与请求边界收口（Issue #2533）。

孤立代理项（如 ``"\\ud800"``）让 httpx 在构造 URL / 请求体时抛
``UnicodeError``，此前被全局兜成 ``LOCAL_IO_ERROR`` + 磁盘指引；写操作还会被
当成「结果未知」保留预约。现在：

- ``_request`` 把它翻译成 ``RequestTextEncodingError``，命令出口按
  ``INVALID_PARAM`` 收口；
- 标识 / 关键词 / 消息要进平台请求的命令在入口先拒绝，避免无意义的浏览器降级；
- 单请求写操作把它认定为「确定未发出」，释放预约。
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import click
from click.testing import CliRunner
import pytest

from boss_agent_cli.api import endpoints
from boss_agent_cli.api._base_client import RequestTextEncodingError
from boss_agent_cli.api.client import BossClient
from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.display import handle_auth_errors
from boss_agent_cli.main import BossCliGroup, cli
from boss_agent_cli.services.candidate_actions import candidate_action_provably_unsent, run_candidate_action

_BAD = "\ud800"


class _StubAuth:
	"""只返回合法 token；请求会在 httpx 编码阶段失败，不会真的联网。"""

	def get_token(self):
		return {"cookies": {}, "stoken": "s", "user_agent": "UA"}


def test_httpx_params_encoding_failure_is_translated() -> None:
	with BossClient(_StubAuth()) as client:
		with pytest.raises(RequestTextEncodingError):
			client.job_detail(_BAD)


def test_httpx_form_body_encoding_failure_is_translated() -> None:
	with BossClient(_StubAuth()) as client:
		with pytest.raises(RequestTextEncodingError):
			client._request("POST", endpoints.SEARCH_URL, data={"query": _BAD})


def test_job_card_does_not_fall_back_to_browser_on_encoding_failure() -> None:
	client = BossClient(_StubAuth())
	client.browser_enabled = MagicMock(return_value=True)  # type: ignore[method-assign]
	client._browser_request = MagicMock()  # type: ignore[method-assign]
	try:
		with pytest.raises(RequestTextEncodingError):
			client.job_card(_BAD)
		client._browser_request.assert_not_called()
	finally:
		client.close()


def test_handle_auth_errors_maps_to_invalid_param_without_disk_guidance() -> None:
	ctx = MagicMock()
	ctx.obj = {"json_output": True, "data_dir": Path("/tmp/boss-2533"), "platform": "zhipin"}

	@handle_auth_errors("detail")
	def impl(ctx):
		raise RequestTextEncodingError("surrogates not allowed")

	with patch("boss_agent_cli.display.handle_error_output") as mock_err:
		impl(ctx)

	kwargs = mock_err.call_args.kwargs
	assert kwargs["code"] == "INVALID_PARAM"
	assert "UTF-8" in kwargs["message"]
	assert "磁盘" not in kwargs["recovery_action"]
	assert "权限" not in kwargs["recovery_action"]


def test_cli_group_maps_request_encoding_error_to_invalid_param() -> None:
	@click.group(cls=BossCliGroup)
	def group() -> None:
		pass

	@group.command("boom")
	def boom() -> None:
		raise RequestTextEncodingError("surrogates not allowed")

	result = CliRunner().invoke(group, ["boom"])

	assert result.exit_code == 1, result.output
	payload = json.loads(result.stdout)
	assert payload["ok"] is False
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert "磁盘" not in payload["error"]["recovery_action"]


def test_request_encoding_error_is_provably_unsent_and_releases_reservation(tmp_path: Path) -> None:
	assert candidate_action_provably_unsent(RequestTextEncodingError("bad")) is True

	with CacheStore(tmp_path / "cache" / "boss_agent.db") as cache:
		with pytest.raises(RequestTextEncodingError):
			run_candidate_action(
				cache,
				security_id="sec",
				job_id="job",
				action="apply",
				send=lambda: (_ for _ in ()).throw(RequestTextEncodingError("bad")),
			)
		# 预约已释放：修正参数后同一职位可以重新预约。
		assert cache.claim_candidate_action("sec", "job", "apply") is None


@pytest.mark.parametrize(
	("module", "args", "label"),
	[
		("boss_agent_cli.commands.detail", ("detail", _BAD), "security_id"),
		("boss_agent_cli.commands.chatmsg", ("chatmsg", _BAD), "security_id"),
		("boss_agent_cli.commands.apply", ("apply", _BAD, "job"), "security_id"),
		("boss_agent_cli.commands.greet", ("greet", _BAD, "job"), "security_id"),
		("boss_agent_cli.commands.recruiter.candidates", ("hr", "candidates", _BAD), "query"),
		("boss_agent_cli.commands.recruiter.jobs", ("hr", "jobs", "detail", _BAD), "enc_job_id"),
		(
			"boss_agent_cli.commands.recruiter.resume",
			("hr", "resume", _BAD, "--job-id", "job", "--security-id", "sec"),
			"geek_id",
		),
	],
	ids=["detail", "chatmsg", "apply", "greet", "recruiter-candidates", "recruiter-jobs-detail", "recruiter-resume"],
)
def test_entry_guards_reject_unencodable_identifiers(
	tmp_path: Path, module: str, args: tuple[str, ...], label: str
) -> None:
	# 入口守卫必须在构造 AuthManager / 浏览器之前收口。
	with patch(f"{module}.AuthManager", side_effect=AssertionError("不应构造 AuthManager")):
		result = CliRunner().invoke(cli, ["--json", "--data-dir", str(tmp_path), *args])

	assert result.exit_code == 1, result.output
	payload = json.loads(result.stdout)
	assert payload["ok"] is False
	assert payload["error"]["code"] == "INVALID_PARAM", payload["error"]
	assert label in payload["error"]["message"]
	assert "UTF-8" in payload["error"]["message"]
	assert "磁盘" not in payload["error"]["recovery_action"]
