"""审计批次五十六：digest Markdown 出口归一孤立代理字符（Issue #2142）。

平台文本（公司 / 职位 / 姓名）可能含被截断的 emoji 等孤立代理字符。旧实现
渲染后直接 encode / write，三个 md 输出分支都会抛 UnicodeEncodeError：
``--json`` 降级成 LOCAL_IO_ERROR，``-o`` 文件已写出却报失败。与 stats #2109
同口径，在 render_digest_markdown 出口先 sanitize_surrogates。
"""

from __future__ import annotations

import json
from unittest.mock import patch

from click.testing import CliRunner

from boss_agent_cli.digest import build_digest, render_digest_markdown
from boss_agent_cli.main import cli

_SURROGATE = "A\ud800B"
_ESCAPED = "A\\ud800B"  # backslashreplace 后的可读转义


def _ctx_mock(mock_cls):
	instance = mock_cls.return_value
	instance.__enter__ = lambda self: self
	instance.__exit__ = lambda self, *a: None
	instance.unwrap_data.side_effect = lambda response: response.get("zpData") if "zpData" in response else response.get("data")
	instance.is_success.side_effect = lambda response: response.get("code", 0) in (0, 200)
	instance.claim_candidate_action.return_value = None
	return instance


def _chat_page_with_surrogate() -> dict:
	return {
		"code": 200,
		"data": {
			"result": [
				{
					"name": "张HR",
					"title": "后端工程师",
					"brandName": _SURROGATE,
					"lastMsg": "你好",
					"lastTime": "今天 10:00",
					"lastTS": 1700000000000,
					"securityId": "sec_001",
					"encryptJobId": "job_001",
					"unreadMsgCount": 1,
					"relationType": 1,
					"friendSource": 0,
				},
			],
		},
	}


def test_render_digest_markdown_normalizes_surrogates():
	data = build_digest(
		new_matches=[{"company": _SURROGATE, "title": _SURROGATE, "last_msg": _SURROGATE}],
		follow_ups=[{"company": _SURROGATE, "title": _SURROGATE}],
		interviews=[{"jobName": _SURROGATE, "brandName": _SURROGATE}],
	)

	text = render_digest_markdown(data, generated_at="2026-11-20 10:00:00")

	text.encode("utf-8")  # 旧实现在这里抛 UnicodeEncodeError
	assert _ESCAPED in text


@patch("boss_agent_cli.commands.digest.get_platform_instance")
@patch("boss_agent_cli.commands.digest.AuthManager")
def test_digest_md_json_envelope_survives_surrogate(mock_auth_cls, mock_client_cls):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.return_value = _chat_page_with_surrogate()
	mock_client.interview_data.return_value = {"code": 200, "data": {"interviewList": []}}

	result = CliRunner().invoke(
		cli, ["--json", "digest", "--format", "md", "--now-ts-ms", "1700000000000"]
	)

	assert result.exit_code == 0, result.stdout
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert _ESCAPED in parsed["data"]["content"]
	assert parsed["data"]["bytes"] == len(parsed["data"]["content"].encode("utf-8"))


@patch("boss_agent_cli.commands.digest.get_platform_instance")
@patch("boss_agent_cli.commands.digest.AuthManager")
def test_digest_md_output_file_survives_surrogate(mock_auth_cls, mock_client_cls, tmp_path):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.return_value = _chat_page_with_surrogate()
	mock_client.interview_data.return_value = {"code": 200, "data": {"interviewList": []}}
	target = tmp_path / "digest.md"

	result = CliRunner().invoke(
		cli,
		[
			"--json", "digest", "--format", "md",
			"--now-ts-ms", "1700000000000", "-o", str(target),
		],
	)

	assert result.exit_code == 0, result.stdout
	parsed = json.loads(result.output)
	assert parsed["data"]["path"] == str(target)
	written = target.read_text(encoding="utf-8")
	assert _ESCAPED in written
	assert parsed["data"]["bytes"] == len(written.encode("utf-8"))


@patch("boss_agent_cli.commands.digest.get_platform_instance")
@patch("boss_agent_cli.commands.digest.AuthManager")
def test_digest_md_stdout_survives_surrogate(mock_auth_cls, mock_client_cls):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.return_value = _chat_page_with_surrogate()
	mock_client.interview_data.return_value = {"code": 200, "data": {"interviewList": []}}

	result = CliRunner().invoke(
		cli, ["digest", "--format", "md", "--now-ts-ms", "1700000000000"]
	)

	assert result.exit_code == 0, result.output
	assert result.output.startswith("# 每日求职摘要")
	assert _ESCAPED in result.output
