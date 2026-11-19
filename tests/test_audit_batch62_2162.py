"""导出服务的函数边界兜底：非对象条目与超大整数标量（Issue #2162）。

四个渲染入口都假定列表元素是 dict、标量 ``str()`` 不会失败。CLI 调用方目前
都已过滤，但公共边界上的坏条目会抛 ``AttributeError`` / ``TypeError`` /
``ValueError``——这些类型不在 ``BossCliGroup`` 兜底内，或会被
``handle_auth_errors`` 报成可重试的 ``NETWORK_ERROR``。本文件锁定：
渲染入口跳过坏条目、标量按缺失处理，正常输入逐字节不变。
"""

from __future__ import annotations

from pathlib import Path

from boss_agent_cli.services.chat_export import _text_cell, render_export
from boss_agent_cli.services.job_card import build_job_from_card
from boss_agent_cli.services.job_export import (
	prepare_export_items,
	render_html_export,
	write_export_file,
)

_HUGE_INT = 10**5000


def _friend(**overrides):
	item = {
		"name": "候选人甲",
		"brand_name": "示例公司",
		"title": "后端工程师",
		"initiated_by": "我主动",
		"msg_status": "未读",
		"unread": 1,
		"last_msg": "你好",
		"last_time": "2026-11-20",
		"uid": "uid-1",
		"security_id": "sid-1",
	}
	item.update(overrides)
	return item


def test_text_cell_missing_and_scalar_fallbacks() -> None:
	"""null / 超长整数按缺失处理；容器仍是 JSON 文本，布尔保持 true/false。"""
	assert _text_cell(None) == ""
	assert _text_cell(_HUGE_INT) == ""
	assert _text_cell(True) == "true"
	assert _text_cell(False) == "false"
	assert _text_cell({"a": float("inf")}) == '{"a": null}'
	assert _text_cell("文本") == "文本"
	assert _text_cell(42) == "42"


def test_chat_export_huge_scalar_does_not_raise() -> None:
	"""md / csv / html 三个文本通道对超过 4300 位的整数标量不再抛 ValueError。"""
	friends = [_friend(last_msg=_HUGE_INT)]

	markdown = render_export(friends, "md", None, None, {"is_first": True})
	csv_text = render_export(friends, "csv", None, None, {"is_first": True})
	html_text = render_export(friends, "html", None, None, {"is_first": True})

	assert "候选人甲" in markdown
	assert "候选人甲" in csv_text
	assert "候选人甲" in html_text


def test_chat_export_skips_non_object_entries() -> None:
	"""friends 含 None / 字符串 / 数字时三个通道都跳过坏条目，正常条目照常渲染。"""
	friends = [None, "坏条目", 42, _friend()]

	markdown = render_export(friends, "md", None, None, {"is_first": True})
	csv_text = render_export(friends, "csv", None, None, {"is_first": True})
	html_text = render_export(friends, "html", None, None, {"is_first": True})

	for text in (markdown, csv_text, html_text):
		assert "候选人甲" in text
		assert "坏条目" not in text
	# 总数只统计可用条目，避免「共 4 条」与实际渲染 1 行自相矛盾。
	assert "总计：1 条" in markdown


def test_chat_export_diff_lists_tolerate_drift() -> None:
	"""diff_result 的列表为 null / 非列表 / 含非对象元素时不抛，按可用条目计。"""
	diff = {
		"is_first": False,
		"prev_date": "2026-11-19",
		"added": None,
		"removed": ["坏条目", _friend(name="已消失")],
		"new_unread": {"不是列表": True},
		"complete": True,
	}

	markdown = render_export([_friend()], "md", None, None, diff)

	assert "消失 1 条" in markdown
	assert "新增" not in markdown
	assert "已消失" in markdown


def test_job_export_skips_non_object_entries(tmp_path: Path) -> None:
	"""职位导出的准备、CSV 写出与 HTML 渲染都跳过非对象条目。"""
	items = [None, "坏条目", {"title": "后端工程师", "company": "示例公司", "skills": ["Python"]}]

	assert prepare_export_items(items, include_private=True) == [items[2]]

	csv_path = tmp_path / "jobs.csv"
	write_export_file(items, "csv", str(csv_path))
	csv_text = csv_path.read_text(encoding="utf-8")
	assert "后端工程师" in csv_text
	assert "坏条目" not in csv_text

	html_text = render_html_export(items)
	assert "后端工程师" in html_text
	assert "<td>1</td>" in html_text
	assert "坏条目" not in html_text


def test_job_card_non_object_input_maps_to_empty_card() -> None:
	"""非对象 job_card 入参按空卡片处理，不再抛 AttributeError。"""
	for card in (None, "坏卡片", ["列表"]):
		mapped = build_job_from_card(card, security_id="sid-1", greeted=False)  # type: ignore[arg-type]

		assert mapped["job_id"] == ""
		assert mapped["security_id"] == "sid-1"
		assert mapped["greeted"] is False


def test_normal_export_output_unchanged() -> None:
	"""正常输入的行为保持：md 表格行与 CSV 单元格与修复前一致。"""
	friends = [_friend()]

	markdown = render_export(friends, "md", None, None, {"is_first": True})
	assert "| S1 | 示例公司 | 候选人甲 | 后端工程师 | 2026-11-20 | 1 | 未读 | 你好 |" in markdown.replace("NEW ", "")

	csv_text = render_export(friends, "csv", None, None, {"is_first": True})
	assert "候选人甲,后端工程师,示例公司,我主动,未读,1,你好,2026-11-20,uid-1,sid-1," in csv_text
