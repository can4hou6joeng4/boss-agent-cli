from typing import Any


def summarize_messages(messages: list[dict[str, Any]], *, friend_uid: str) -> dict[str, Any]:
	stage = "new"
	pending_action = "review"
	key_facts: list[str] = []
	risk_flags: list[str] = []

	if not messages:
		return {
			"stage": stage,
			"pending_action": pending_action,
			"key_facts": key_facts,
			"risk_flags": risk_flags,
			"message_count": 0,
		}

	latest = messages[-1]
	latest_from = str(latest.get("from", {}).get("uid", ""))
	texts = " ".join((item.get("text") or item.get("body", {}).get("text", "")) for item in messages)

	if "面试" in texts or "约个面" in texts:
		stage = "interview"
		pending_action = "confirm_interview"
		key_facts.append("涉及面试安排")
	elif latest_from == friend_uid:
		stage = "reply_needed"
		pending_action = "reply"
		risk_flags.append("需要回复对方最新消息")
	else:
		stage = "waiting"
		pending_action = "wait"

	if "微信" in texts:
		key_facts.append("涉及微信交换")
	if "简历" in texts:
		key_facts.append("涉及简历投递")

	return {
		"stage": stage,
		"pending_action": pending_action,
		"key_facts": key_facts,
		"risk_flags": risk_flags,
		"message_count": len(messages),
	}
