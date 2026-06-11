"""Demo helper (English) — compact schema summary for GIF recording."""
import json
import sys


GROUPS = {
	"Discovery":   ["search", "recommend", "detail", "show", "cities"],
	"Action":      ["greet", "batch-greet", "apply", "exchange"],
	"Conversation": ["chat", "chatmsg", "chat-summary", "mark", "interviews"],
	"Pipeline":    ["pipeline", "follow-up", "digest", "watch", "shortlist", "preset"],
	"Resume / AI": ["resume", "ai"],
	"System":      ["schema", "login", "logout", "status", "doctor", "me", "history", "export", "config", "clean"],
}


def main():
	d = json.load(sys.stdin)["data"]
	cmds = d["commands"]
	errs = d["error_codes"]

	print(f"\n  {d['name']} v{d.get('version', '?')}")
	print(f"  {len(cmds)} commands | {len(errs)} error codes | JSON stdout protocol\n")

	for group_name, cmd_names in GROUPS.items():
		matched = [n for n in cmd_names if n in cmds]
		if not matched:
			continue
		print(f"  [{group_name}]")
		for name in matched:
			desc = cmds[name].get("description_en") or cmds[name]["description"]
			if len(desc) > 42:
				desc = desc[:42] + "..."
			print(f"    boss {name:16s} {desc}")
		print()

	recoverable = [(k, v) for k, v in errs.items() if v["recoverable"]]
	print(f"  Error Recovery ({len(recoverable)} auto-recoverable):")
	for code, info in recoverable[:5]:
		print(f"    {code:25s} -> {info['recovery_action']}")
	if len(recoverable) > 5:
		print(f"    ... and {len(recoverable) - 5} more")

	print("\n  Protocol: stdout=JSON | stderr=logs | exit 0/1\n")


if __name__ == "__main__":
	main()
