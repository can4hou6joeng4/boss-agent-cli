"""Role, platform and goal catalog used by prompts, schema and headless plans."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from boss_agent_cli.platforms import list_platforms, list_recruiter_platforms
from boss_agent_cli.wizard.models import WorkflowInputError, WorkflowPlan, WizardInput


@dataclass(frozen=True)
class GoalDefinition:
	name: str
	role: str
	description: str
	steps: tuple[str, ...]
	required_inputs: tuple[str, ...] = ()


GOALS = {
	"candidate": {
		"job_search": GoalDefinition(
			"job_search", "candidate", "搜索和筛选职位", ("auth_status", "candidate_search"), ("query",)
		),
		"recommendations": GoalDefinition(
			"recommendations", "candidate", "获取个性化推荐", ("auth_status", "candidate_recommend")
		),
		"job_detail": GoalDefinition(
			"job_detail", "candidate", "查看职位详情", ("auth_status", "candidate_detail"), ("security_id",)
		),
		"apply": GoalDefinition(
			"apply", "candidate", "投递或立即沟通", ("auth_status", "candidate_apply"), ("security_id", "job_id")
		),
		"greet": GoalDefinition(
			"greet", "candidate", "向招聘者打招呼", ("auth_status", "candidate_greet"), ("security_id", "job_id")
		),
		"exchange": GoalDefinition(
			"exchange", "candidate", "交换联系方式", ("auth_status", "candidate_exchange"), ("security_id",)
		),
		"mark": GoalDefinition(
			"mark", "candidate", "标记沟通联系人", ("auth_status", "candidate_mark"), ("security_id", "label")
		),
		"communication": GoalDefinition(
			"communication", "candidate", "查看沟通列表", ("auth_status", "candidate_chat")
		),
		"chat_history": GoalDefinition(
			"chat_history",
			"candidate",
			"查看聊天记录",
			("auth_status", "candidate_chat_history"),
			("gid", "security_id"),
		),
		"pipeline": GoalDefinition("pipeline", "candidate", "查看候选进度", ("auth_status", "candidate_pipeline")),
		"digest": GoalDefinition("digest", "candidate", "生成求职日报", ("auth_status", "candidate_digest")),
		"shortlist": GoalDefinition("shortlist", "candidate", "查看本地候选池", ("local_shortlist",)),
		"resumes": GoalDefinition("resumes", "candidate", "查看本地简历", ("local_resumes",)),
		"ai_assist": GoalDefinition(
			"ai_assist", "candidate", "使用本地简历进行 AI 辅助", ("ai_assist",), ("resume", "prompt")
		),
		"export": GoalDefinition(
			"export", "candidate", "导出职位结果", ("auth_status", "candidate_export"), ("query",)
		),
		"watch": GoalDefinition("watch", "candidate", "保存、查看或运行职位监控", ("candidate_watch",)),
		"crawl_start": GoalDefinition(
			"crawl_start", "candidate", "启动可恢复采集", ("crawl_start",), ("query", "city")
		),
		"crawl_status": GoalDefinition("crawl_status", "candidate", "查看长任务状态", ("crawl_status",), ("run_id",)),
		"crawl_resume": GoalDefinition("crawl_resume", "candidate", "恢复长任务", ("crawl_resume",), ("run_id",)),
		"crawl_stop": GoalDefinition("crawl_stop", "candidate", "停止长任务", ("crawl_stop",), ("run_id",)),
	},
	"recruiter": {
		"candidates": GoalDefinition("candidates", "recruiter", "搜索候选人", ("auth_status", "recruiter_candidates")),
		"applications": GoalDefinition(
			"applications", "recruiter", "查看投递申请", ("auth_status", "recruiter_applications")
		),
		"candidate_resume": GoalDefinition(
			"candidate_resume",
			"recruiter",
			"查看候选人简历",
			("auth_status", "recruiter_resume"),
			("geek_id", "job_id"),
		),
		"communication": GoalDefinition(
			"communication", "recruiter", "查看沟通列表", ("auth_status", "recruiter_chat")
		),
		"last_messages": GoalDefinition(
			"last_messages", "recruiter", "批量查看候选人最近消息", ("auth_status", "recruiter_last_messages")
		),
		"reply": GoalDefinition(
			"reply", "recruiter", "回复候选人", ("auth_status", "recruiter_reply"), ("friend_id", "message")
		),
		"exchange_contact": GoalDefinition(
			"exchange_contact",
			"recruiter",
			"请求交换候选人联系方式",
			("auth_status", "recruiter_exchange_contact"),
			("friend_id",),
		),
		"request_resume": GoalDefinition(
			"request_resume", "recruiter", "请求附件简历", ("auth_status", "recruiter_request_resume"), ("friend_id",)
		),
		"chat_history": GoalDefinition(
			"chat_history", "recruiter", "查看候选人聊天记录", ("auth_status", "recruiter_chat_history"), ("friend_id",)
		),
		"jobs_list": GoalDefinition("jobs_list", "recruiter", "查看职位列表", ("auth_status", "recruiter_jobs_list")),
		"jobs_detail": GoalDefinition(
			"jobs_detail", "recruiter", "查看职位详情", ("auth_status", "recruiter_jobs_detail"), ("job_id",)
		),
		"jobs_online": GoalDefinition(
			"jobs_online", "recruiter", "上线职位", ("auth_status", "recruiter_jobs_online"), ("job_id",)
		),
		"jobs_offline": GoalDefinition(
			"jobs_offline", "recruiter", "下线职位", ("auth_status", "recruiter_jobs_offline"), ("job_id",)
		),
	},
}


def catalog_data() -> dict[str, Any]:
	return {
		"roles": {
			role: {
				"platforms": (
					list_platforms()
					if role == "candidate"
					else [name.removesuffix("-recruiter") for name in list_recruiter_platforms()]
				),
				"goals": {
					name: {
						"description": goal.description,
						"steps": list(goal.steps),
						"required_inputs": list(goal.required_inputs),
					}
					for name, goal in goals.items()
				},
			}
			for role, goals in GOALS.items()
		},
	}


def build_plan(wizard_input: WizardInput) -> WorkflowPlan:
	role_goals = GOALS[wizard_input.role]
	goal = role_goals.get(wizard_input.goal)
	if goal is None:
		raise WorkflowInputError(f"角色 {wizard_input.role} 不支持 goal {wizard_input.goal!r}")
	platforms = catalog_data()["roles"][wizard_input.role]["platforms"]
	if wizard_input.platform not in platforms:
		raise WorkflowInputError(f"角色 {wizard_input.role} 不支持平台 {wizard_input.platform!r}")
	missing = [
		name
		for name in goal.required_inputs
		if wizard_input.inputs.get(name) is None or wizard_input.inputs.get(name) == ""
	]
	if missing:
		raise WorkflowInputError(f"goal {goal.name!r} 缺少 inputs: {', '.join(missing)}")
	steps = wizard_input.requested_steps or goal.steps
	unknown_steps = [step for step in steps if step not in goal.steps]
	if unknown_steps:
		raise WorkflowInputError(f"requested_steps 不属于 goal {goal.name!r}: {', '.join(unknown_steps)}")
	return WorkflowPlan(
		role=wizard_input.role,
		platform=wizard_input.platform,
		goal=wizard_input.goal,
		inputs=dict(wizard_input.inputs),
		requested_steps=steps,
		mode=wizard_input.mode,
	)
