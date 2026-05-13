"""Click command registration helpers."""

import click

from boss_agent_cli.commands import (
	ai_cmd,
	apply,
	chat,
	chat_summary,
	chatmsg,
	cities,
	clean,
	config_cmd,
	detail,
	digest,
	doctor,
	exchange,
	export,
	greet,
	history,
	interviews,
	login,
	logout,
	mark,
	me,
	pipeline,
	preset,
	recommend,
	resume_cmd,
	schema,
	search,
	shortlist,
	show,
	stats,
	status,
	watch,
)
from boss_agent_cli.commands.recruiter import applications as recruiter_applications
from boss_agent_cli.commands.recruiter import candidates as recruiter_candidates
from boss_agent_cli.commands.recruiter import chat as recruiter_chat
from boss_agent_cli.commands.recruiter import jobs as recruiter_jobs
from boss_agent_cli.commands.recruiter import reply as recruiter_reply
from boss_agent_cli.commands.recruiter import request_resume as recruiter_request_resume
from boss_agent_cli.commands.recruiter import resume as recruiter_resume
from boss_agent_cli.display import handle_error_output
from boss_agent_cli.platforms import list_recruiter_platforms


def register_candidate_commands(cli: click.Group) -> None:
	"""Register candidate and shared top-level commands."""
	cli.add_command(schema.schema_cmd, "schema")
	cli.add_command(login.login_cmd, "login")
	cli.add_command(logout.logout_cmd, "logout")
	cli.add_command(status.status_cmd, "status")
	cli.add_command(doctor.doctor_cmd, "doctor")
	cli.add_command(search.search_cmd, "search")
	cli.add_command(detail.detail_cmd, "detail")
	cli.add_command(greet.greet_cmd, "greet")
	cli.add_command(greet.batch_greet_cmd, "batch-greet")
	cli.add_command(recommend.recommend_cmd, "recommend")
	cli.add_command(export.export_cmd, "export")
	cli.add_command(cities.cities_cmd, "cities")
	cli.add_command(me.me_cmd, "me")
	cli.add_command(chat.chat_cmd, "chat")
	cli.add_command(chatmsg.chatmsg_cmd, "chatmsg")
	cli.add_command(chat_summary.chat_summary_cmd, "chat-summary")
	cli.add_command(mark.mark_cmd, "mark")
	cli.add_command(exchange.exchange_cmd, "exchange")
	cli.add_command(interviews.interviews_cmd, "interviews")
	cli.add_command(show.show_cmd, "show")
	cli.add_command(history.history_cmd, "history")
	cli.add_command(watch.watch_group, "watch")
	cli.add_command(pipeline.pipeline_cmd, "pipeline")
	cli.add_command(pipeline.follow_up_cmd, "follow-up")
	cli.add_command(apply.apply_cmd, "apply")
	cli.add_command(shortlist.shortlist_group, "shortlist")
	cli.add_command(preset.preset_group, "preset")
	cli.add_command(digest.digest_cmd, "digest")
	cli.add_command(config_cmd.config_group, "config")
	cli.add_command(clean.clean_cmd, "clean")
	cli.add_command(resume_cmd.resume_group, "resume")
	cli.add_command(ai_cmd.ai_group, "ai")
	cli.add_command(stats.stats_cmd, "stats")


@click.group("hr", help="招聘者模式快捷命令")
@click.pass_context
def hr_group(ctx: click.Context) -> None:
	ctx.obj["role"] = "recruiter"
	platform_name = ctx.obj.get("platform") or "zhipin"
	recruiter_name = f"{platform_name}-recruiter"
	supported = list_recruiter_platforms()
	if recruiter_name not in supported:
		handle_error_output(
			ctx,
			"hr",
			code="PLATFORM_NOT_SUPPORTED",
			message=(
				f"招聘者模式暂不支持平台 {platform_name!r}；"
				f"当前仅支持: {', '.join(supported)}"
			),
			recoverable=True,
			recovery_action="boss --platform zhipin hr ...",
		)
		raise SystemExit(1)


def register_recruiter_commands(cli: click.Group) -> None:
	"""Register recruiter shortcut commands."""
	cli.add_command(hr_group, "hr")
	hr_group.add_command(recruiter_applications.applications_cmd, "applications")
	hr_group.add_command(recruiter_resume.resume_cmd, "resume")
	hr_group.add_command(recruiter_chat.recruiter_chat_cmd, "chat")
	hr_group.add_command(recruiter_chat.recruiter_chatmsg_cmd, "chatmsg")
	hr_group.add_command(recruiter_chat.recruiter_last_messages_cmd, "last-messages")
	hr_group.add_command(recruiter_jobs.jobs_group, "jobs")
	hr_group.add_command(recruiter_candidates.candidates_cmd, "candidates")
	hr_group.add_command(recruiter_reply.reply_cmd, "reply")
	hr_group.add_command(recruiter_request_resume.request_resume_cmd, "request-resume")
