from pathlib import Path

import click

from boss_agent_cli import __version__
from boss_agent_cli.commands import schema, login, logout, status, doctor, search, detail, greet, recommend, export, cities, me, chat, chatmsg, chat_summary, mark, exchange, interviews, show, history, watch, pipeline, apply, shortlist, preset, digest, config_cmd, clean, resume_cmd, ai_cmd, stats
from boss_agent_cli.commands.recruiter import applications as recruiter_applications
from boss_agent_cli.commands.recruiter import resume as recruiter_resume
from boss_agent_cli.commands.recruiter import chat as recruiter_chat
from boss_agent_cli.commands.recruiter import jobs as recruiter_jobs
from boss_agent_cli.commands.recruiter import candidates as recruiter_candidates
from boss_agent_cli.commands.recruiter import reply as recruiter_reply
from boss_agent_cli.commands.recruiter import request_resume as recruiter_request_resume
from boss_agent_cli.config import load_config
from boss_agent_cli.hooks import create_hook_bus
from boss_agent_cli.output import Logger
from boss_agent_cli.platforms import list_platforms


@click.group(context_settings={"allow_interspersed_args": False})
@click.version_option(version=__version__, prog_name="boss")
@click.option("--data-dir", default="~/.boss-agent", help="数据存储目录")
@click.option("--delay", default=None, help="请求间隔范围（秒），如 1.5-3.0")
@click.option("--cdp-url", default=None, help="Chrome CDP 调试地址（如 http://localhost:9222），启用则优先用用户 Chrome")
@click.option("--platform", "platform_name", default=None, help="指定招聘平台适配器（默认 zhipin，即 BOSS 直聘）")
@click.option("--role", default=None, type=click.Choice(["candidate", "recruiter"]), help="角色模式：candidate（求职者，默认）/ recruiter（招聘者）")
@click.option("--log-level", default=None, type=click.Choice(["error", "warning", "info", "debug"]))
@click.option("--json/--no-json", "json_output", default=False, help="强制 JSON 输出（即使在终端中）")
@click.pass_context
def cli(ctx: click.Context, data_dir: str, delay: str | None, cdp_url: str | None, platform_name: str | None, role: str | None, log_level: str | None, json_output: bool) -> None:
	ctx.ensure_object(dict)
	resolved_dir = Path(data_dir).expanduser()
	resolved_dir.mkdir(parents=True, exist_ok=True)
	ctx.obj["data_dir"] = resolved_dir
	ctx.obj["json_output"] = json_output

	cfg = load_config(resolved_dir / "config.json")

	if delay:
		low, high = delay.split("-")
		ctx.obj["delay"] = (float(low), float(high))
	else:
		ctx.obj["delay"] = tuple(cfg["request_delay"])

	level = log_level or cfg["log_level"]
	ctx.obj["log_level"] = level
	ctx.obj["logger"] = Logger(level)
	ctx.obj["cdp_url"] = cdp_url or cfg.get("cdp_url")

	resolved_platform = platform_name or cfg.get("platform") or "zhipin"
	available = list_platforms()
	if resolved_platform not in available:
		raise click.BadParameter(
			f"unknown platform {resolved_platform!r}, supported: {', '.join(available)}",
			param_hint="--platform",
		)
	ctx.obj["platform"] = resolved_platform

	resolved_role = role or cfg.get("role") or "candidate"
	ctx.obj["role"] = resolved_role

	ctx.obj["config"] = cfg
	ctx.obj["hooks"] = create_hook_bus()


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

# Recruiter shortcut: boss hr <subcommand>
@click.group("hr", help="招聘者模式快捷命令")
@click.pass_context
def hr_group(ctx: click.Context) -> None:
	ctx.obj["role"] = "recruiter"

cli.add_command(hr_group, "hr")
hr_group.add_command(recruiter_applications.applications_cmd, "applications")
hr_group.add_command(recruiter_resume.resume_cmd, "resume")
hr_group.add_command(recruiter_chat.recruiter_chat_cmd, "chat")
hr_group.add_command(recruiter_jobs.jobs_group, "jobs")
hr_group.add_command(recruiter_candidates.candidates_cmd, "candidates")
hr_group.add_command(recruiter_reply.reply_cmd, "reply")
hr_group.add_command(recruiter_request_resume.request_resume_cmd, "request-resume")
