"""HTML 导出对平台字段类型漂移的兜底（Issue #1296）。

平台把 ``brandName`` / ``name`` / ``title`` 返回数字、或 ``skills`` 混入非字符串
元素时，``html.escape`` 直接抛 ``AttributeError``，``chat`` / ``export`` 落成
``INTERNAL_ERROR``（「升级版本 / 提 issue」）；同输入的 md / csv 早已用 ``str()``
兜底成功。HTML 渲染统一按字符串转义，行为与其它格式一致。
"""

from boss_agent_cli.services.chat_export import render_export
from boss_agent_cli.services.job_export import render_html_export


def test_chat_html_export_tolerates_non_string_fields():
	friends = [
		{
			"brand_name": 123,
			"name": 456,
			"title": 789,
			"last_time": 1_700_000_000_000,
			"msg_status": 1,
			"last_msg": 42,
			"initiated_by": "对方主动",
			"unread": 0,
			"uid": 1,
		}
	]

	html = render_export(friends, "html", None, None, {})

	assert "123" in html
	assert "456" in html
	assert "789" in html


def test_job_html_export_tolerates_non_string_fields():
	html = render_html_export(
		[
			{
				"title": 123,
				"company": 456,
				"city": None,
				"experience": 1,
				"education": 2,
				"skills": [1, None, "a"],
				"welfare": "x",
			}
		]
	)

	assert "123" in html
	assert "456" in html


def test_other_formats_keep_working_with_same_input():
	friends = [
		{
			"brand_name": 123,
			"name": 456,
			"title": 789,
			"last_time": 1_700_000_000_000,
			"msg_status": 1,
			"last_msg": 42,
			"initiated_by": "对方主动",
			"unread": 0,
			"uid": 1,
		}
	]

	assert "123" in render_export(friends, "md", None, None, {})
	assert "123" in render_export(friends, "csv", None, None, {})
