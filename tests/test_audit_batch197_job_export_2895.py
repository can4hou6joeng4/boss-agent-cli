"""Issue #2895：HTML 导出两条映射路径的文本字段口径一致。"""

from boss_agent_cli.api.models import JobItem
from boss_agent_cli.services.job_export import (
	public_html_export_item_from_api,
	public_html_export_item_from_job,
	render_html_export,
)


def _row(item):
	rows = [line for line in render_html_export([item]).splitlines() if "<tr>" in line]
	return rows[-1]


def test_api_and_pipeline_paths_agree_on_drifted_text_fields():
	raw = {
		"encryptJobId": "j1",
		"jobName": ["Go 开发"],
		"brandName": {"name": "TestCo"},
		"cityName": "上海",
		"jobExperience": 3,
		"jobDegree": None,
	}

	api = public_html_export_item_from_api(raw)
	pipeline = public_html_export_item_from_job(JobItem.from_api(raw).to_dict())

	assert api == pipeline
	assert _row(api) == _row(pipeline)
	assert "Go 开发" not in _row(api)
	assert "TestCo" not in _row(api)


def test_api_path_keeps_legitimate_text_fields():
	raw = {
		"encryptJobId": "j1",
		"jobName": "Go 开发",
		"brandName": "TestCo",
		"cityName": "上海",
		"jobExperience": "3 年",
		"jobDegree": "本科",
		"skills": ["Go"],
		"welfareList": ["双休"],
	}

	api = public_html_export_item_from_api(raw)
	pipeline = public_html_export_item_from_job(JobItem.from_api(raw).to_dict())

	assert api == pipeline
	assert "Go 开发" in _row(api)
	assert "TestCo" in _row(api)
