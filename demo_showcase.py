"""Demo helper — compact schema summary for GIF recording."""
import json
import sys


def main():
	d = json.load(sys.stdin)["data"]
	cmds = d["commands"]
	errs = d["error_codes"]

	print(f"\n  {d['name']} — {d['description']}")
	print(f"  {len(cmds)} commands | {len(errs)} error codes | JSON protocol\n")

	for name, cmd in cmds.items():
		desc = cmd["description"]
		if len(desc) > 42:
			desc = desc[:42] + "..."
		print(f"    boss {name:14s} {desc}")

	recoverable = [(k, v) for k, v in errs.items() if v["recoverable"]]
	print(f"\n  Error Recovery ({len(recoverable)} auto-recoverable):")
	for code, info in recoverable:
		print(f"    {code:25s} -> {info['recovery_action']}")

	print("\n  Protocol: stdout=JSON | stderr=logs | exit 0/1\n")


if __name__ == "__main__":
	main()
