"""chat_export.py 扩展测试 — CSV/MD/HTML/JSON 渲染、注入防护、边界场景。"""

import json

from boss_agent_cli.commands.chat_export import (
	render_export,
	prepare_render_data,
)
from boss_agent_cli.commands.chat_utils import sanitize_csv_cell, escape_md_cell


# ── 测试数据工厂 ────────────────────────────────────────────────────


def _make_friend(
	name="张HR",
	brand_name="阿里",
	initiated_by="对方主动",
	last_msg="你好",
	security_id="sec_001",
	unread=0,
	msg_status="已读",
	**overrides,
):
	base = {
		"name": name,
		"title": "招聘经理",
		"brand_name": brand_name,
		"initiated_by": initiated_by,
		"last_msg": last_msg,
		"last_time": "今天 10:00",
		"last_ts": 1700000000000,
		"msg_status": msg_status,
		"security_id": security_id,
		"encrypt_job_id": "job_001",
		"unread": unread,
	}
	base.update(overrides)
	return base


_NO_DIFF = {"is_first": True, "added": [], "removed": [], "new_unread": []}


# ── CSV 公式注入防护 ────────────────────────────────────────────────


def test_sanitize_csv_cell_equal_sign():
	"""以 = 开头的值应被前置单引号"""
	assert sanitize_csv_cell("=cmd|' /C calc'!A0") == "'=cmd|' /C calc'!A0"


def test_sanitize_csv_cell_plus_sign():
	"""以 + 开头的值应被前置单引号"""
	assert sanitize_csv_cell("+SUM(A1:A2)") == "'+SUM(A1:A2)"


def test_sanitize_csv_cell_minus_sign():
	"""以 - 开头的值应被前置单引号"""
	assert sanitize_csv_cell("-1+2") == "'-1+2"


def test_sanitize_csv_cell_at_sign():
	"""以 @ 开头的值应被前置单引号"""
	assert sanitize_csv_cell("@risk") == "'@risk"


def test_sanitize_csv_cell_normal_text():
	"""正常文本不应被修改"""
	assert sanitize_csv_cell("正常文本") == "正常文本"
	assert sanitize_csv_cell("") == ""
	assert sanitize_csv_cell("hello world") == "hello world"


def test_sanitize_csv_cell_tab_and_cr_stripped():
	"""Tab 和回车应被替换/移除"""
	assert "\t" not in sanitize_csv_cell("有\tTab")
	assert "\r" not in sanitize_csv_cell("有\r回车")


def test_sanitize_csv_cell_non_string_input():
	"""非字符串输入应被转为字符串"""
	assert sanitize_csv_cell(123) == "123"
	assert sanitize_csv_cell(None) == "None"


def test_csv_export_formula_injection_in_render():
	"""CSV 渲染结果中以危险字符开头的字段应被转义"""
	friends = [_make_friend(
		name="=cmd|' /C calc'!A0",
		last_msg="+SUM(A1)",
		brand_name="@evil_corp",
	)]
	csv_text = render_export(friends, "csv", None, None, _NO_DIFF)
	# 所有危险开头都应被转义
	assert "'=cmd" in csv_text
	assert "'+SUM" in csv_text
	assert "'@evil" in csv_text


# ── MD 表格注入防护 ─────────────────────────────────────────────────


def test_escape_md_cell_pipe():
	"""管道符应被转义为 \\|"""
	assert escape_md_cell("消息|含管道符") == "消息\\|含管道符"


def test_escape_md_cell_newline():
	"""换行应被替换为空格"""
	assert escape_md_cell("多行\n消息") == "多行 消息"


def test_escape_md_cell_carriage_return():
	"""回车应被移除"""
	assert escape_md_cell("带\r回车") == "带回车"


def test_escape_md_cell_normal():
	"""正常文本不应被修改"""
	assert escape_md_cell("正常") == "正常"


def test_escape_md_cell_non_string():
	"""非字符串输入应被转为字符串"""
	assert escape_md_cell(42) == "42"


def test_md_export_pipe_injection_in_render():
	"""MD 渲染结果中管道符和换行应被正确转义"""
	friends = [_make_friend(
		name="张|HR",
		last_msg="你好\n请看简历",
		brand_name="测试公司",
	)]
	md_text = render_export(friends, "md", None, None, _NO_DIFF)
	# 管道符应被转义，换行应被替换
	assert "张\\|HR" in md_text
	assert "你好 请看简历" in md_text
	# 主数据表格行中（排除 id_map 映射表）应有转义后的管道符
	for line in md_text.split("\n"):
		# 主表行以 "| S" 或 "| NEW S" 开头，且包含联系人名
		if line.startswith("|") and "测试公司" in line and "张" in line:
			assert "张\\|HR" in line
			break


# ── HTML 导出格式正确性 ─────────────────────────────────────────────


def test_html_export_basic_structure():
	"""HTML 导出应包含基本 HTML 结构"""
	friends = [_make_friend()]
	html = render_export(friends, "html", None, None, _NO_DIFF)
	assert "<!DOCTYPE html>" in html
	assert "<html" in html
	assert "BOSS 直聘沟通列表" in html
	assert "<table>" in html
	assert "</html>" in html


def test_html_export_contains_data():
	"""HTML 导出应包含实际数据"""
	friends = [_make_friend(name="李HR", brand_name="腾讯")]
	html = render_export(friends, "html", None, None, _NO_DIFF)
	assert "李HR" in html
	assert "腾讯" in html
	assert "S1" in html  # 编号


def test_html_export_xss_prevention():
	"""HTML 导出应转义危险字符，防止 XSS"""
	friends = [_make_friend(
		name="<script>alert(1)</script>",
		brand_name="公司&名",
		last_msg='<img onerror="alert(1)">',
	)]
	html = render_export(friends, "html", None, None, _NO_DIFF)
	assert "<script>" not in html
	assert '<img onerror' not in html
	assert "&lt;script&gt;" in html
	assert "&amp;名" in html


def test_html_export_with_diff():
	"""HTML 导出应包含 diff 摘要"""
	diff_result = {
		"is_first": False,
		"prev_date": "2026-04-12",
		"added": [_make_friend(security_id="sec_new")],
		"removed": [],
		"new_unread": [],
	}
	friends = [_make_friend(), _make_friend(security_id="sec_new", name="新HR")]
	html = render_export(friends, "html", None, None, diff_result)
	assert "新增 1 条" in html
	assert "2026-04-12" in html


def test_html_export_with_removed():
	"""HTML 导出包含消失条目时应渲染消失表格"""
	diff_result = {
		"is_first": False,
		"prev_date": "2026-04-12",
		"added": [],
		"removed": [{"brand_name": "旧公司", "name": "旧HR", "last_time": "昨天"}],
		"new_unread": [],
	}
	friends = [_make_friend()]
	html = render_export(friends, "html", None, None, diff_result)
	assert "已消失" in html
	assert "旧公司" in html
	assert "旧HR" in html


def test_html_export_unread_badge():
	"""有未读消息时应渲染 unread badge"""
	friends = [_make_friend(unread=3)]
	html = render_export(friends, "html", None, None, _NO_DIFF)
	assert 'class="unread"' in html
	assert ">3<" in html


def test_html_export_new_badge():
	"""新增条目应渲染 NEW badge"""
	new_friend = _make_friend(security_id="sec_new", name="新HR")
	diff_result = {
		"is_first": False,
		"prev_date": "2026-04-12",
		"added": [new_friend],
		"removed": [],
		"new_unread": [],
	}
	friends = [new_friend]
	html = render_export(friends, "html", None, None, diff_result)
	assert "badge-new" in html
	assert "NEW" in html


# ── JSON 导出格式正确性 ─────────────────────────────────────────────


def test_json_export_basic():
	"""JSON 导出应返回有效的 JSON 数组"""
	friends = [_make_friend(), _make_friend(name="李HR", security_id="sec_002")]
	json_text = render_export(friends, "json", None, None, _NO_DIFF)
	parsed = json.loads(json_text)
	assert isinstance(parsed, list)
	assert len(parsed) == 2
	assert parsed[0]["name"] == "张HR"
	assert parsed[1]["name"] == "李HR"


def test_json_export_preserves_all_fields():
	"""JSON 导出应保留所有字段"""
	friends = [_make_friend()]
	json_text = render_export(friends, "json", None, None, _NO_DIFF)
	parsed = json.loads(json_text)
	item = parsed[0]
	assert "name" in item
	assert "title" in item
	assert "brand_name" in item
	assert "security_id" in item
	assert "encrypt_job_id" in item
	assert "unread" in item
	assert "msg_status" in item


def test_json_export_ensure_ascii_false():
	"""JSON 导出应保留中文字符（不转为 unicode escape）"""
	friends = [_make_friend(name="中文名")]
	json_text = render_export(friends, "json", None, None, _NO_DIFF)
	assert "中文名" in json_text
	assert "\\u" not in json_text


def test_json_export_empty():
	"""空列表 JSON 导出应返回空数组"""
	json_text = render_export([], "json", None, None, _NO_DIFF)
	parsed = json.loads(json_text)
	assert parsed == []


# ── 空数据导出边界 ──────────────────────────────────────────────────


def test_csv_export_empty():
	"""空列表 CSV 导出应返回空字符串"""
	csv_text = render_export([], "csv", None, None, _NO_DIFF)
	assert csv_text == ""


def test_md_export_empty():
	"""空列表 MD 导出应包含标题但无数据行"""
	md_text = render_export([], "md", None, None, _NO_DIFF)
	assert "BOSS 直聘沟通列表" in md_text
	assert "总计：0 条" in md_text
	# 不应有数据编号
	assert "S1" not in md_text


def test_html_export_empty():
	"""空列表 HTML 导出应包含基本结构但无数据行"""
	html = render_export([], "html", None, None, _NO_DIFF)
	assert "<!DOCTYPE html>" in html
	assert "总计：0 条" in html
	assert "S1" not in html


# ── None 字段兜底 ───────────────────────────────────────────────────


def test_none_fields_md_no_crash():
	"""字段为 None 时 MD 渲染不应崩溃"""
	friends = [{
		"name": None,
		"title": None,
		"brand_name": None,
		"initiated_by": "对方主动",
		"last_msg": None,
		"last_time": None,
		"last_ts": 0,
		"msg_status": None,
		"security_id": "sec_null",
		"encrypt_job_id": None,
		"unread": None,
	}]
	md_text = render_export(friends, "md", None, None, _NO_DIFF)
	assert "sec_null" in md_text
	# None 应被兜底为 "-"
	assert "S1" in md_text


def test_none_fields_html_no_crash():
	"""字段为 None 时 HTML 渲染不应崩溃"""
	friends = [{
		"name": None,
		"title": None,
		"brand_name": None,
		"initiated_by": "我主动",
		"last_msg": None,
		"last_time": None,
		"last_ts": 0,
		"msg_status": None,
		"security_id": "sec_null",
		"encrypt_job_id": None,
		"unread": None,
	}]
	html = render_export(friends, "html", None, None, _NO_DIFF)
	assert "<!DOCTYPE html>" in html
	assert "sec_null" in html


def test_none_fields_csv_no_crash():
	"""字段为 None 时 CSV 渲染不应崩溃"""
	friends = [{
		"name": None,
		"title": None,
		"brand_name": None,
		"initiated_by": "对方主动",
		"last_msg": None,
		"last_time": None,
		"msg_status": None,
		"security_id": "sec_null",
		"encrypt_job_id": None,
		"unread": None,
	}]
	csv_text = render_export(friends, "csv", None, None, _NO_DIFF)
	assert "name" in csv_text  # 表头
	assert "None" in csv_text or "sec_null" in csv_text


# ── prepare_render_data 逻辑 ────────────────────────────────────────


def test_prepare_render_data_grouping():
	"""数据应按 initiated_by 分组"""
	friends = [
		_make_friend(initiated_by="对方主动", security_id="sec_1"),
		_make_friend(initiated_by="我主动", security_id="sec_2"),
		_make_friend(initiated_by="对方主动", security_id="sec_3"),
	]
	rd = prepare_render_data(friends, None, _NO_DIFF)
	assert rd["total"] == 3
	# 应有两个分组
	assert len(rd["sections"]) == 2
	# 对方主动应有 2 条
	boss_section = [s for s in rd["sections"] if "对方主动" in s["subtitle"]][0]
	assert len(boss_section["rows"]) == 2


def test_prepare_render_data_filter_boss():
	"""from_who='boss' 只应返回对方主动分组"""
	friends = [
		_make_friend(initiated_by="对方主动", security_id="sec_1"),
		_make_friend(initiated_by="我主动", security_id="sec_2"),
	]
	rd = prepare_render_data(friends, "boss", _NO_DIFF)
	assert len(rd["sections"]) == 1
	assert "对方主动" in rd["sections"][0]["subtitle"]


def test_prepare_render_data_filter_me():
	"""from_who='me' 只应返回我主动分组"""
	friends = [
		_make_friend(initiated_by="对方主动", security_id="sec_1"),
		_make_friend(initiated_by="我主动", security_id="sec_2"),
	]
	rd = prepare_render_data(friends, "me", _NO_DIFF)
	assert len(rd["sections"]) == 1
	assert "我主动" in rd["sections"][0]["subtitle"]


def test_prepare_render_data_unknown_group():
	"""未知的 initiated_by 值应创建独立分组"""
	friends = [_make_friend(initiated_by="神秘来源", security_id="sec_1")]
	rd = prepare_render_data(friends, None, _NO_DIFF)
	assert len(rd["sections"]) == 1
	assert "神秘来源" in rd["sections"][0]["subtitle"]


def test_prepare_render_data_diff_summary():
	"""有 diff 时应生成摘要"""
	diff_result = {
		"is_first": False,
		"prev_date": "2026-04-12",
		"added": [_make_friend(security_id="sec_new")],
		"removed": [_make_friend(security_id="sec_old")],
		"new_unread": [_make_friend(security_id="sec_unread")],
	}
	friends = [_make_friend()]
	rd = prepare_render_data(friends, None, diff_result)
	assert rd["diff_summary"] is not None
	assert "新增 1 条" in rd["diff_summary"]["change"]
	assert "消失 1 条" in rd["diff_summary"]["change"]
	assert "新消息 1 条" in rd["diff_summary"]["change"]
	assert rd["diff_summary"]["prev_date"] == "2026-04-12"


def test_prepare_render_data_diff_no_change():
	"""diff 无变化时应显示'无变化'"""
	diff_result = {
		"is_first": False,
		"prev_date": "2026-04-12",
		"added": [],
		"removed": [],
		"new_unread": [],
	}
	rd = prepare_render_data([], None, diff_result)
	assert rd["diff_summary"]["change"] == "无变化"


def test_prepare_render_data_id_map():
	"""id_map 应记录编号到 security_id 的映射"""
	friends = [
		_make_friend(security_id="sec_a", name="A", brand_name="公司A"),
		_make_friend(security_id="sec_b", name="B", brand_name="公司B"),
	]
	rd = prepare_render_data(friends, None, _NO_DIFF)
	assert len(rd["id_map"]) == 2
	refs = [item[0] for item in rd["id_map"]]
	sids = [item[1] for item in rd["id_map"]]
	assert "S1" in refs
	assert "S2" in refs
	assert "sec_a" in sids
	assert "sec_b" in sids


def test_prepare_render_data_me_read_unread_stats():
	"""我主动分组应统计已读/未读数"""
	friends = [
		_make_friend(initiated_by="我主动", msg_status="已读", security_id="sec_1"),
		_make_friend(initiated_by="我主动", msg_status="已读", security_id="sec_2"),
		_make_friend(initiated_by="我主动", msg_status="未读", security_id="sec_3"),
	]
	rd = prepare_render_data(friends, None, _NO_DIFF)
	me_section = [s for s in rd["sections"] if "我主动" in s["subtitle"]][0]
	assert "已读 2" in me_section["subtitle"]
	assert "未读 1" in me_section["subtitle"]


# ── MD 长消息截断 ───────────────────────────────────────────────────


def test_md_long_message_truncated():
	"""MD 渲染时超过 40 字符的消息应被截断"""
	long_msg = "这是一条非常非常非常非常非常非常非常非常非常非常非常非常非常非常非常非常非常长的消息"
	friends = [_make_friend(last_msg=long_msg)]
	md_text = render_export(friends, "md", None, None, _NO_DIFF)
	# 截断标记
	assert "\u2026" in md_text  # "..."


# ── HTML 长消息截断 ─────────────────────────────────────────────────


def test_html_long_message_truncated():
	"""HTML 渲染时超过 60 字符的消息应被截断"""
	long_msg = "A" * 100
	friends = [_make_friend(last_msg=long_msg)]
	html = render_export(friends, "html", None, None, _NO_DIFF)
	assert "\u2026" in html
	# 原始完整消息不应出现
	assert "A" * 100 not in html


# ── MD security_id 映射表 ──────────────────────────────────────────


def test_md_export_contains_id_map():
	"""MD 导出应包含 security_id 折叠映射表"""
	friends = [_make_friend(security_id="sec_abc123")]
	md_text = render_export(friends, "md", None, None, _NO_DIFF)
	assert "<details>" in md_text
	assert "security_id 映射表" in md_text
	assert "sec_abc123" in md_text
	assert "S1" in md_text
