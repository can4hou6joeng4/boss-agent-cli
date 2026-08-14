"""JobItem 公开 lid 字段的测试。

lid 是 BOSS 直聘职位卡片的列表标识，取 JD 全文需要 securityId + lid 两个参数。
此前 JobItem 不保留 lid，导致走 CLI 的调用方拿不到取 JD 所需的完整参数。
"""

from boss_agent_cli.api.models import JobItem


def test_job_item_from_api_extracts_lid():
	raw = {
		"encryptJobId": "enc1",
		"jobName": "大模型实习",
		"securityId": "sec1",
		"lid": "lid123",
	}
	item = JobItem.from_api(raw)
	assert item.lid == "lid123"


def test_job_item_lid_defaults_to_empty_when_missing():
	raw = {
		"encryptJobId": "enc1",
		"jobName": "大模型实习",
		"securityId": "sec1",
	}
	item = JobItem.from_api(raw)
	assert item.lid == ""


def test_job_item_to_dict_includes_lid():
	raw = {
		"encryptJobId": "enc1",
		"jobName": "大模型实习",
		"securityId": "sec1",
		"lid": "lid123",
	}
	item = JobItem.from_api(raw)
	assert item.to_dict()["lid"] == "lid123"
