"""按当前平台注册表为每个命令派生 availability（角色 / 平台可用性）。"""

from typing import Any, cast

from boss_agent_cli.platforms import list_platforms
from boss_agent_cli.schema.data import SCHEMA_DATA


_ROLE_BOTH_COMMANDS = {
	"login",
	"status",
	"doctor",
	"logout",
	"schema",
	"config",
	"clean",
	"cities",
	"platforms",
	"wizard",
}

_CANDIDATE_COMMANDS = {
	"search",
	"detail",
	"recommend",
	"greet",
	"batch-greet",
	"export",
	"me",
	"show",
	"history",
	"chat",
	"chatmsg",
	"chat-summary",
	"mark",
	"exchange",
	"interviews",
	"watch",
	"preset",
	"pipeline",
	"follow-up",
	"apply",
	"shortlist",
	"favorites",
	"digest",
	"stats",
	"resume",
	"ai",
}

def availability_note(availability: dict[str, Any]) -> str:
	roles = ", ".join(availability.get("roles", [])) or "none"
	candidate_platforms = ", ".join(availability.get("candidate_platforms", [])) or "-"
	recruiter_platforms = ", ".join(availability.get("recruiter_platforms", [])) or "-"
	return (
		f"可用性: roles={roles}; candidate_platforms={candidate_platforms}; recruiter_platforms={recruiter_platforms}"
	)


def _command_availability(
	cmd_name: str,
	*,
	candidate_platforms: list[str],
	recruiter_platforms: list[str],
) -> dict[str, Any]:
	if cmd_name == "agent":
		return {
			"roles": ["candidate", "recruiter"],
			"candidate_platforms": ["zhipin"],
			"recruiter_platforms": ["zhilian", "zhipin"],
			"note": (
				"agent run/train 等为招聘者自动化；agent crawl 为候选人本地编排，"
				"可新建 crawl 或分析已完成的 crawl run。"
			),
		}
	if cmd_name == "hr":
		commands = cast(dict[str, Any], SCHEMA_DATA.get("commands", {}))
		hr_spec = commands.get("hr", {})
		if not isinstance(hr_spec, dict):
			hr_spec = {}
		subcommands = hr_spec.get("subcommands", {})
		if not isinstance(subcommands, dict):
			subcommands = {}
		subcommand_availability = {
			sub_name: {
				"roles": ["recruiter"],
				"candidate_platforms": [],
				"recruiter_platforms": recruiter_platforms,
			}
			for sub_name in subcommands
		}
		return {
			"roles": ["recruiter"],
			"candidate_platforms": [],
			"recruiter_platforms": recruiter_platforms,
			"subcommands": subcommand_availability,
		}
	if cmd_name in _ROLE_BOTH_COMMANDS:
		return {
			"roles": ["candidate", "recruiter"],
			"candidate_platforms": candidate_platforms,
			"recruiter_platforms": recruiter_platforms,
		}
	if cmd_name in _CANDIDATE_COMMANDS:
		return {
			"roles": ["candidate"],
			"candidate_platforms": candidate_platforms,
			"recruiter_platforms": [],
		}
	return {
		"roles": ["candidate"],
		"candidate_platforms": candidate_platforms,
		"recruiter_platforms": [],
	}


def inject_availability(data: dict[str, Any]) -> dict[str, Any]:
	# supported_platforms 与 availability 均从当前注册表派生，避免 schema 漂移。
	candidate_platforms = list_platforms()
	recruiter_platforms = data.get("supported_recruiter_platforms", [])
	commands: dict[str, Any] = {}
	for cmd_name, cmd_spec in data["commands"].items():
		cmd_copy = dict(cmd_spec)
		cmd_copy["availability"] = _command_availability(
			cmd_name,
			candidate_platforms=candidate_platforms,
			recruiter_platforms=recruiter_platforms,
		)
		commands[cmd_name] = cmd_copy
	data["commands"] = commands
	return data
