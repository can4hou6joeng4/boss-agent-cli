"""hr greet 的 lid 允许空串（Issue #2789）。"""

from pathlib import Path

from click.testing import CliRunner

from boss_agent_cli.main import cli


def _invoke(data_dir: Path, *args: str):
	return CliRunner().invoke(
		cli,
		[
			"--role", "recruiter",
			"--data-dir", str(data_dir),
			"hr", "greet",
			"--geek-id", "g",
			"--job-id", "j",
			"--expect-id", "e",
			"--security-id", "s",
			"--message", "hi",
			*args,
		],
	)


def test_hr_greet_allows_empty_lid_dry_run(tmp_path: Path) -> None:
	result = _invoke(tmp_path, "--lid", "", "--dry-run")
	assert result.exit_code == 0, result.output or repr(result.exception)


def test_hr_greet_still_rejects_blank_target_ids(tmp_path: Path) -> None:
	for option, value in (
		("--geek-id", ""),
		("--job-id", " "),
		("--expect-id", ""),
		("--security-id", " "),
	):
		args = {"--geek-id": "g", "--job-id": "j", "--expect-id": "e", "--security-id": "s"}
		args[option] = value
		result = CliRunner().invoke(
			cli,
			[
				"--role", "recruiter",
				"--data-dir", str(tmp_path),
				"hr", "greet",
				"--geek-id", args["--geek-id"],
				"--job-id", args["--job-id"],
				"--expect-id", args["--expect-id"],
				"--lid", "",
				"--security-id", args["--security-id"],
				"--message", "hi",
				"--dry-run",
			],
		)
		assert result.exit_code == 1
		assert "不能为空" in result.output
