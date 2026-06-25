"""Action execution helpers for Zhilian browser automation."""

from __future__ import annotations

from typing import Any

from boss_agent_cli.automation.models import ActionResult, PlatformAction
from boss_agent_cli.automation.zhilian_browser import ZhilianBrowserRecruiterSession


def execute_browser_action(
	session: ZhilianBrowserRecruiterSession,
	action: PlatformAction,
	message: str,
	ref_id: str,
) -> ActionResult:
	match action:
		case PlatformAction.SEND_QUESTIONNAIRE | PlatformAction.SEND_FOLLOW_UP:
			return _action_result(session.send_recruiter_message(ref_id, message))
		case PlatformAction.EXCHANGE_CONTACT:
			return _action_result(session.exchange_recruiter_contact(ref_id))
		case PlatformAction.CREATE_INTERVIEW_LEAD:
			return ActionResult("executed", {"local_only": True})
		case _:
			return ActionResult("blocked", {"reason": f"unsupported action {action}"})


def _action_result(response: dict[str, Any]) -> ActionResult:
	code = response.get("code")
	if code in {0, 200} or response.get("ok") is True:
		return ActionResult("executed", {"response": response})
	return ActionResult("blocked", {"reason": str(response.get("message", code)), "response": response})
