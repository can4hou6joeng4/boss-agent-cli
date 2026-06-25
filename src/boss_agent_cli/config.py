import json
from pathlib import Path
from typing import Any

DEFAULTS: dict[str, Any] = {
	"default_city": None,
	"default_salary": None,
	"request_delay": [1.5, 3.0],
	"batch_greet_delay": [2.0, 5.0],
	"batch_greet_max": 10,
	"log_level": "error",
	"login_timeout": 120,
	"cdp_url": None,
	"export_dir": None,
	"resume_default_template": "default",
	"resume_export_format": "pdf",
	"platform": "zhipin",
	"role": "candidate",
	"low_risk_mode": True,
	"automation": {
		"mode": "autonomous",
		"platforms": ["zhilian", "zhipin"],
		"allowed_actions": [
			"scan_conversations",
			"read_candidate_profile",
			"send_questionnaire",
			"send_follow_up",
			"exchange_contact",
			"create_interview_lead",
		],
		"human_review_threshold": 0.65,
		"auto_execute_threshold": 0.82,
	},
}


def load_config(config_path: Path | None) -> dict[str, Any]:
	cfg = dict(DEFAULTS)
	if config_path and config_path.exists():
		with open(config_path, encoding="utf-8") as f:
			user_cfg = json.load(f)
		cfg.update(user_cfg)
	return cfg
