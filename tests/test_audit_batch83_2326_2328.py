"""审计批次六十：职位字段归一进入信封与索引缓存（Issue #2326、#2327、#2328）。

- #2326：``JobItem.from_api`` 的 ``job_url`` 与 ``job_id`` 同口径，容器 /
  非有限数不能拼出带 Python repr 的伪链接；
- #2327：``JobItem`` / ``JobDetail`` 的 ``raw_job_type`` 只保留标量，容器与
  非有限数按缺失处理，与 ``job_card`` 通道一致；
- #2328：wizard 的 ``job_detail`` 成功分支逐字段归一，字段级漂移不再把
  嵌套对象 / null / 布尔写进断点与信封。

全部离线：纯函数与 MagicMock 平台，不发平台请求。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from boss_agent_cli.api.models import JobDetail, JobItem
from boss_agent_cli.wizard.actions import ActionContext, execute_candidate_detail

# ── #2326：job_url 与 job_id 同口径 ──────────────────────────────


@pytest.mark.parametrize("raw_id", [{"a": 1}, [1, 2], float("inf"), float("nan")])
def test_job_url_ignores_non_text_encrypt_job_id(raw_id):
	item = JobItem.from_api({"encryptJobId": raw_id})

	assert item.job_id == ""
	assert item.job_url == ""


def test_job_url_still_built_for_valid_job_id():
	item = JobItem.from_api({"encryptJobId": "abc123"})

	assert item.job_id == "abc123"
	assert item.job_url == "https://www.zhipin.com/job_detail/abc123.html"


# ── #2327：raw_job_type 只保留标量 ───────────────────────────────


@pytest.mark.parametrize("raw_type", [{"a": 1}, [1], float("inf"), float("nan")])
def test_job_item_raw_job_type_drops_non_scalars(raw_type):
	item = JobItem.from_api({"jobType": raw_type})

	assert item.raw_job_type is None
	assert item.employment_type == ""


@pytest.mark.parametrize("raw_type", [{"a": 1}, [1], float("inf")])
def test_job_detail_raw_job_type_drops_non_scalars(raw_type):
	detail = JobDetail.from_api({"jobInfo": {"jobType": raw_type}})

	assert detail.raw_job_type is None
	assert detail.employment_type == ""


def test_raw_job_type_keeps_scalar_and_employment_type():
	item = JobItem.from_api({"jobType": 4})
	detail = JobDetail.from_api({"jobInfo": {"jobType": 4}})

	assert item.raw_job_type == 4
	assert item.employment_type == "实习"
	assert detail.raw_job_type == 4
	assert detail.employment_type == "实习"
	assert JobItem.from_api({"jobType": "7"}).raw_job_type == "7"


# ── #2328：wizard 的 job_detail 分支逐字段归一 ───────────────────

_DRIFTED_DETAIL = {
	"jobInfo": {
		"encryptJobId": "j1",
		"jobName": {"a": 1},
		"salaryDesc": [1, 2],
		"cityName": None,
		"experienceName": {"e": 1},
		"degreeName": ["x"],
		"jobLabels": [{"x": 1}, "py", None],
		"jobType": {"k": 1},
		"daysPerWeekDesc": {"d": 1},
		"leastMonthDesc": [3],
		"payTypeDesc": {"p": 1},
	},
	"bossInfo": {"name": {"n": 1}, "title": ["t"], "activeTimeDesc": {"at": 1}},
	"brandComInfo": {"brandName": ["b"]},
	"jobDetail": {"d": 1},
}


def _platform(payload):
	platform = MagicMock()
	platform.name = "zhipin"
	platform.__enter__.return_value = platform
	platform.__exit__.return_value = False
	platform.is_success.return_value = True
	platform.unwrap_data.side_effect = lambda response: (response or {}).get("zpData")
	platform.job_detail.return_value = {"code": 0, "zpData": payload}
	platform.job_card.return_value = {"code": 0, "zpData": {}}
	return platform


def _detail(payload):
	context = ActionContext(
		data_dir=None, platform="zhipin", role="candidate", logger=MagicMock(),
	)
	return execute_candidate_detail(
		_platform(payload), security_id="s1", job_id="j1", lid="lid-1", context=context,
	)


def test_wizard_detail_normalizes_drifted_field_values():
	job = _detail(_DRIFTED_DETAIL)

	assert job["channel"] == "job_detail"
	for key in (
		"title", "salary", "city", "experience", "education", "description",
		"address", "boss_name", "boss_title", "boss_active", "days_per_week",
		"least_month", "pay_type", "company",
	):
		assert isinstance(job[key], str), (key, job[key])
	assert job["title"] == ""
	assert job["salary"] == ""
	assert job["city"] == ""
	assert job["description"] == ""
	assert job["raw_job_type"] is None
	assert job["employment_type"] == ""
	# 容器元素按 job_card 同口径转 JSON 文本，null 丢弃。
	assert job["skills"] == ['{"x": 1}', "py"]


def test_wizard_detail_normal_path_is_unchanged():
	payload = {
		"jobInfo": {
			"encryptJobId": "j1",
			"jobName": "后端",
			"salaryDesc": "20-30K",
			"cityName": "北京",
			"experienceName": "3-5年",
			"degreeName": "本科",
			"jobLabels": ["Python", "SQL"],
			"jobType": 4,
			"daysPerWeekDesc": "5天",
			"leastMonthDesc": "12个月",
			"payTypeDesc": "月薪",
			"address": "望京",
		},
		"bossInfo": {"name": "张", "title": "HR", "activeTimeDesc": "刚刚活跃"},
		"brandComInfo": {"brandName": "示例"},
		"jobDetail": "职位描述",
	}
	job = _detail(payload)

	assert job["title"] == "后端"
	assert job["salary"] == "20-30K"
	assert job["skills"] == ["Python", "SQL"]
	assert job["raw_job_type"] == 4
	assert job["employment_type"] == "实习"
	assert job["boss_name"] == "张"
	assert job["description"] == "职位描述"
