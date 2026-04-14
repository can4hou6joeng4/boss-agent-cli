from pathlib import Path

import click

from boss_agent_cli import __version__
from boss_agent_cli.commands import schema, login, logout, status, doctor, search, detail, greet, recommend, export, cities, me, chat, chatmsg, chat_summary, mark, exchange, interviews, show, history, watch, pipeline, apply, shortlist, preset, digest, config_cmd, clean, resume_cmd, ai_cmd
from boss_agent_cli.config import load_config
from boss_agent_cli.hooks import create_hook_bus
from boss_agent_cli.output import Logger


@click.group(context_settings={"allow_interspersed_args": False})
@click.version_option(version=__version__, prog_name="boss")
@click.option("--data-dir", default="~/.boss-agent", help="数据存储目录")
@click.option("--delay", default=None, help="请求间隔范围（秒），如 1.5-3.0")
@click.option("--cdp-url", default=None, help="Chrome CDP 调试地址（如 http://localhost:9222），启用则优先用用户 Chrome")
@click.option("--log-level", default=None, type=click.Choice(["error", "warning", "info", "debug"]))
@click.option("--json/--no-json", "json_output", default=False, help="强制 JSON 输出（即使在终端中）")
@click.pass_context
def cli(ctx, data_dir, delay, cdp_url, log_level, json_output):
	ctx.ensure_object(dict)
	data_dir = Path(data_dir).expanduser()
	data_dir.mkdir(parents=True, exist_ok=True)
	ctx.obj["data_dir"] = data_dir
	ctx.obj["json_output"] = json_output

	cfg = load_config(data_dir / "config.json")

	if delay:
		low, high = delay.split("-")
		ctx.obj["delay"] = (float(low), float(high))
	else:
		ctx.obj["delay"] = tuple(cfg["request_delay"])

	level = log_level or cfg["log_level"]
	ctx.obj["log_level"] = level
	ctx.obj["logger"] = Logger(level)
	ctx.obj["cdp_url"] = cdp_url or cfg.get("cdp_url")
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
