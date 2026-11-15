"""推荐列表与评分期望的非对象容器不能中断整次调用。"""

import json
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.main import cli


class _FakePlatform:
	def __init__(self, *, jobs, expectation=None):
		self.jobs = jobs
		self.expectation = expectation

	def __enter__(self):
		return self

	def __exit__(self, *exc):
		return None

	def recommend_jobs(self, *, page):
		return {"code": 0, "zpData": self.jobs}

	def resume_expect(self):
		return {"code": 0, "zpData": self.expectation}

	def is_success(self, response):
		return response.get("code") == 0

	def unwrap_data(self, response):
		return response.get("zpData")


def _recommend(platform, *args):
	with patch("boss_agent_cli.commands.recommend.AuthManager"), \
		patch("boss_agent_cli.commands.recommend.get_platform_instance", return_value=platform), \
		patch("boss_agent_cli.commands.recommend.CacheStore") as cache, \
		patch("boss_agent_cli.commands.recommend.try_save_index", return_value=False):
		cache.return_value.__enter__.return_value.is_greeted.return_value = False
		return CliRunner().invoke(cli, ["--json", "recommend", *args])


@pytest.mark.parametrize("payload", [None, "oops", ["oops"], 7, True])
def test_recommend_treats_non_object_data_as_empty(payload):
	result = _recommend(_FakePlatform(jobs=payload))

	assert result.exit_code == 0, result.output
	envelope = json.loads(result.stdout)
	assert envelope["ok"] is True
	assert envelope["data"] == []
	assert envelope["pagination"]["has_more"] is None
	assert envelope["pagination"]["has_more_source"] == "unknown"


@pytest.mark.parametrize("payload", [None, "oops", ["oops"], 7, True])
def test_recommend_scores_jobs_with_non_object_expectation(payload):
	result = _recommend(_FakePlatform(
		jobs={"jobList": [{"encryptJobId": "job-test", "jobName": "测试工程师"}], "hasMore": False},
		expectation=payload,
	), "--with-score")

	assert result.exit_code == 0, result.output
	envelope = json.loads(result.stdout)
	assert envelope["ok"] is True
	assert len(envelope["data"]) == 1
	assert envelope["data"][0]["title"] == "测试工程师"
	assert "match_score" in envelope["data"][0]
	assert envelope["pagination"]["has_more"] is False
