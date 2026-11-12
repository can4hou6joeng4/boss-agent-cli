from boss_agent_cli.digest import build_digest, render_digest_markdown


def test_build_digest_groups_inputs_into_sections():
	result = build_digest(
		new_matches=[{"security_id": "sec_1"}],
		follow_ups=[{"security_id": "sec_2"}],
		interviews=[{"jobName": "Go 开发"}],
	)
	assert result["new_match_count"] == 1
	assert result["follow_up_count"] == 1
	assert result["interview_count"] == 1
	assert result["new_matches"][0]["security_id"] == "sec_1"


def test_build_digest_handles_empty_sections():
	result = build_digest(new_matches=[], follow_ups=[], interviews=[])
	assert result["new_match_count"] == 0
	assert result["follow_up_count"] == 0
	assert result["interview_count"] == 0
	assert result["summary"] == "0 new matches, 0 follow-ups, 0 interviews"


def test_render_digest_markdown_warns_when_truncated():
	"""Markdown 日报常被直接转发，截断时必须写明计数只覆盖前 N 页（Issue #885）。"""
	data = build_digest(new_matches=[], follow_ups=[], interviews=[])
	data["truncated"] = True
	data["chat_pages_scanned"] = 50

	text = render_digest_markdown(data, generated_at="2026-11-12 10:00:00")

	assert "50 页" in text
	assert "仅覆盖已扫描的前 50 页" in text


def test_render_digest_markdown_has_no_warning_when_complete():
	data = build_digest(new_matches=[], follow_ups=[], interviews=[])
	data["truncated"] = False
	data["chat_pages_scanned"] = 2

	text = render_digest_markdown(data, generated_at="2026-11-12 10:00:00")

	assert "页上限" not in text


def test_render_digest_markdown_breaks_link_and_html_injection():
	"""日报常被转发到邮件 / 飞书，公司名与职位名里的链接语法必须被转义。"""
	payload = "[点我领奖](https://example.invalid/phish)"
	data = build_digest(
		new_matches=[{"company": payload, "title": "<https://example.invalid/auto>", "last_msg": "hi"}],
		follow_ups=[],
		interviews=[],
	)

	text = render_digest_markdown(data, generated_at="2026-11-13 10:00:00")

	assert "\\[点我领奖\\](https://example.invalid/phish)" in text
	assert "\\<https://example.invalid/auto>" in text
	assert "[点我领奖](https://example.invalid/phish)" not in text
