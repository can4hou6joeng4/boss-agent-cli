from boss_agent_cli.pipeline_state import build_pipeline_items, select_follow_up_candidates


def _chat_item(
	*,
	security_id="sec_001",
	job_id="job_001",
	relation_type=1,
	unread=0,
	last_ts=1700000000000,
	company="TestCo",
	title="HR",
	last_msg="你好",
):
	return {
		"securityId": security_id,
		"encryptJobId": job_id,
		"relationType": relation_type,
		"unreadMsgCount": unread,
		"lastTS": last_ts,
		"brandName": company,
		"title": title,
		"lastMsg": last_msg,
	}


def _interview_item(*, company="TestCo", job_name="Go 开发", status="待面试", interview_time="2026-04-12 10:00"):
	return {
		"brandName": company,
		"jobName": job_name,
		"statusDesc": status,
		"interviewTime": interview_time,
		"address": "线上",
	}


def test_build_pipeline_items_marks_reply_needed_for_unread():
	items = build_pipeline_items(
		chat_items=[_chat_item(unread=2)],
		interview_items=[],
		now_ts_ms=1700000000000,
		stale_days=3,
	)
	assert items[0]["stage"] == "reply_needed"


def test_build_pipeline_items_marks_follow_up_for_stale_chat():
	items = build_pipeline_items(
		chat_items=[_chat_item(last_ts=1700000000000)],
		interview_items=[],
		now_ts_ms=1700000000000 + 5 * 24 * 3600 * 1000,
		stale_days=3,
	)
	assert items[0]["stage"] == "follow_up"


def test_build_pipeline_items_marks_applied_for_relation_type_3():
	items = build_pipeline_items(
		chat_items=[_chat_item(relation_type=3)],
		interview_items=[],
		now_ts_ms=1700000000000,
		stale_days=3,
	)
	assert items[0]["stage"] == "applied"


def test_build_pipeline_items_adds_interview_candidates():
	items = build_pipeline_items(
		chat_items=[],
		interview_items=[_interview_item()],
		now_ts_ms=1700000000000,
		stale_days=3,
	)
	assert items[0]["stage"] == "interview"
	assert items[0]["source"] == "interview"


def test_select_follow_up_candidates_filters_only_actionable_states():
	items = [
		{"stage": "reply_needed", "source": "chat"},
		{"stage": "follow_up", "source": "chat"},
		{"stage": "interview", "source": "interview"},
		{"stage": "chatting", "source": "chat"},
	]
	selected = select_follow_up_candidates(items)
	assert [item["stage"] for item in selected] == ["reply_needed", "follow_up", "interview"]
