"""历史列表与向导职位详情在非对象平台载荷上的读取。"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.main import cli
from boss_agent_cli.wizard.actions import ActionContext, execute_candidate_detail

_ENVELOPE_KEYS = {"ok", "schema_version", "command", "data", "pagination", "error", "hints"}
_JOB = {
	"encryptJobId": "j1",
	"jobName": "后端",
	"brandName": "示例",
	"salaryDesc": "20-30K",
	"cityName": "北京",
	"securityId": "s1",
}


def _platform(payload, **responses):
	platform = MagicMock()
	platform.name = "zhipin"
	platform.__enter__.return_value = platform
	platform.__exit__.return_value = False
	platform.is_success.return_value = True
	# 按响应包络解包，两个通道可以给出不同的载荷。
	platform.unwrap_data.side_effect = lambda response: (response or {}).get("zpData")
	platform.job_history.return_value = {"code": 0, "zpData": payload}
	platform.job_detail.return_value = {"code": 0, "zpData": payload}
	platform.job_card.return_value = {"code": 0, "zpData": payload}
	for name, value in responses.items():
		getattr(platform, name).return_value = {"code": 0, "zpData": value}
	return platform


def _invoke_history(payload):
	with patch("boss_agent_cli.commands.history.get_platform_instance", return_value=_platform(payload)), \
		patch("boss_agent_cli.commands.history.AuthManager"), \
		patch("boss_agent_cli.commands.history.CacheStore", create=True):
		return CliRunner().invoke(cli, ["--json", "--data-dir", "/tmp/boss-drift-fake", "history"])


@pytest.mark.parametrize("payload", ["oops", ["oops"], 7, True, {"jobList": "oops"}])
def test_history_non_object_payload_returns_empty_page(payload):
	result = _invoke_history(payload)

	assert result.exit_code == 0, result.output
	envelope = json.loads(result.stdout)
	assert set(envelope) == _ENVELOPE_KEYS
	assert envelope["ok"] is True
	assert envelope["data"] == []
	# 平台没有给出分页事实：不能伪造成「没有下一页」。
	assert envelope["pagination"]["has_more"] is None
	assert envelope["pagination"]["has_more_source"] == "unknown"


def test_history_still_returns_valid_jobs_and_pagination():
	result = _invoke_history({"jobList": [_JOB], "hasMore": True})

	assert result.exit_code == 0, result.output
	envelope = json.loads(result.stdout)
	assert [item["job_id"] for item in envelope["data"]] == ["j1"]
	assert envelope["pagination"] == {
		# 请求页由命令决定；history 不采信载荷里的 page 字段。
		"page": 1,
		"has_more": True,
		"has_more_source": "platform_hasMore",
		"total": 1,
		"total_kind": "returned_page_count",
	}


def _detail(payload, card_payload=None):
	platform = _platform(
		payload,
		**({} if card_payload is None else {"job_card": {"jobCard": card_payload}}),
	)
	context = ActionContext(
		data_dir=None, platform="zhipin", role="candidate", logger=MagicMock(),
	)
	return execute_candidate_detail(platform, security_id="s1", job_id="j1", lid="lid-1", context=context)


@pytest.mark.parametrize("payload", ["oops", ["oops"], 7, True, {"jobInfo": "oops"}])
def test_wizard_detail_falls_back_to_job_card_on_drifted_detail_payload(payload):
	"""包装详情漂移时与 `detail` 命令同一口径：改走职位卡片，不再抛 AttributeError。"""
	job = _detail(payload, card_payload={**_JOB, "bossName": "张"})

	assert job["channel"] == "job_card"
	assert job["title"] == "后端"
	assert job["job_id"] == "j1"


@pytest.mark.parametrize("payload", ["oops", ["oops"], 7, True])
def test_wizard_detail_raises_structured_error_when_both_channels_drift(payload):
	from boss_agent_cli.wizard.runner import WorkflowActionError

	with pytest.raises(WorkflowActionError) as caught:
		_detail(payload, card_payload=payload)

	assert caught.value.code == "JOB_NOT_FOUND"
	# 与 `detail` / `show` 同一契约：职位不存在是确定性结果，但仍给出换线索的路径。
	assert caught.value.recoverable is False
	assert "search" in caught.value.recovery_action
	assert caught.value.replay_safe is True


def test_wizard_detail_still_reads_a_valid_job_info_payload():
	job = _detail(
		{
			"jobInfo": {"jobName": "后端", "salaryDesc": "20-30K", "cityName": "北京"},
			"bossInfo": {"name": "张"},
			"brandComInfo": {"brandName": "示例"},
			"jobDetail": "描述",
		}
	)

	assert job["channel"] == "job_detail"
	assert job["title"] == "后端"
	assert job["boss_name"] == "张"
	assert job["description"] == "描述"
