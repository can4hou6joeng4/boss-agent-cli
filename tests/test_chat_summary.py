from boss_agent_cli.chat_summary import summarize_messages


def _msg(uid: int, text: str, ts: int = 1700000000000, msg_type: int = 1):
	return {
		"from": {"uid": uid, "name": "张HR" if uid == 99 else "我"},
		"type": msg_type,
		"text": text,
		"time": ts,
	}


def test_summary_marks_reply_needed_when_latest_message_is_from_recruiter():
	result = summarize_messages(
		[
			_msg(12345, "您好，我对岗位很感兴趣", 1700000000000),
			_msg(99, "方便的话发我一份简历", 1700000001000),
		],
		friend_uid="99",
	)
	assert result["stage"] == "reply_needed"
	assert result["pending_action"] == "reply"
	assert "需要回复对方最新消息" in result["risk_flags"]


def test_summary_marks_waiting_when_latest_message_is_from_me():
	result = summarize_messages(
		[
			_msg(99, "您好"),
			_msg(12345, "好的，我稍后发您简历", 1700000001000),
		],
		friend_uid="99",
	)
	assert result["stage"] == "waiting"
	assert result["pending_action"] == "wait"


def test_summary_marks_interview_when_messages_contain_interview_signal():
	result = summarize_messages(
		[
			_msg(99, "可以约个面试吗？"),
			_msg(12345, "可以，明天下午方便"),
		],
		friend_uid="99",
	)
	assert result["stage"] == "interview"
	assert "面试" in " ".join(result["key_facts"])


def test_summary_collects_contact_exchange_fact():
	result = summarize_messages(
		[
			_msg(99, "方便加个微信沟通吗？"),
		],
		friend_uid="99",
	)
	assert any("微信" in item for item in result["key_facts"])
