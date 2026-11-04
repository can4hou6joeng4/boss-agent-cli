import json
import math
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

#: 请求间隔默认值。来源：实测评测显示 15 秒间隔连续 3 次 search 会在第 3 次命中
#: code 37（「您的环境存在异常」），90 秒间隔 3/3 通过
#: （docs/research/zhipin-cdp-free-read-path-2026-10-29.md 第七节）。
#: 单条只读命令只发 1 次请求，因此不受影响；受影响的是分页、批量详情这类多请求流程。
DEFAULT_REQUEST_DELAY: tuple[float, float] = (30.0, 60.0)

#: 角色取值域：``--role`` 的 ``Choice``、启动期校验与 ``config set role`` 共用。
#: 三处必须一致，否则 ``config set`` 能写进一个让之后每条命令都启动失败的值。
ROLES: tuple[str, str] = ("candidate", "recruiter")

DEFAULTS: dict[str, Any] = {
	"request_delay": list(DEFAULT_REQUEST_DELAY),
	"request_budget_dir": None,
	"request_max_per_minute": 20,
	"request_wait_timeout": 30.0,
	"batch_greet_delay": [2.0, 5.0],
	"log_level": "error",
	"cdp_url": None,
	"browser_source": "auto",
	"export_dir": None,
	"platform": "zhipin",
	"role": "candidate",
	"operating_mode": "assisted",
	"low_risk_mode": True,
	"automation": {
		"mode": "autonomous",
		"platforms": ["zhipin"],
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
	"crawl": {
		"chrome_path": None,
		"cdp_port": None,
		"hook_profile": "none",
		"hook_dir": None,
		"max_requests": 20,
		"max_details": 50,
		"max_seconds": 600,
		"max_retries": 1,
	},
}


#: ``--cdp-url`` / ``config set cdp_url`` 的取值要求，启动期校验与写配置共用同一句文案。
CDP_URL_REQUIREMENT = "必须是形如 http://127.0.0.1:9222 的 http(s) 调试地址"


def is_valid_cdp_url(value: str) -> bool:
	"""``cdp_url`` 必须是带主机名的 http(s) 地址。

	``probe_cdp`` 只是把值拼成 ``{base}/json/version`` 交给 httpx：``:::``、
	``http://``、``localhost:9222`` 这类值会让探测稳定失败，doctor 却仍把它原样
	写进 next_actions，Agent 照着再执行一次还是同一个错误。
	"""
	try:
		parsed = urlsplit(value)
		_ = parsed.port  # 端口非数字 / 超出 1-65535 时抛 ValueError
	except ValueError:
		return False
	return parsed.scheme in {"http", "https"} and bool(parsed.hostname)


def expand_budget_directory(value: str) -> Path:
	"""把 ``request_budget_dir`` 展开成绝对路径，未知用户的 ``~user`` 按配置错误处理。

	``Path.expanduser`` 在系统里查不到该用户时抛 ``RuntimeError``：启动期它会让整条
	命令冲出 traceback（连信封都没有），运行期则会把配置问题报成可重试的网络错误。
	"""
	try:
		path = Path(value).expanduser()
	except RuntimeError as exc:
		raise ValueError(
			f"request_budget_dir 的家目录写法无法展开（{exc}）：{value!r}；请改用绝对路径或 ~/ 开头的当前用户路径"
		) from exc
	if not path.is_absolute():
		raise ValueError("request_budget_dir 必须是非空绝对路径或 null")
	return path


def validate_request_config(cfg: dict[str, Any]) -> None:
	"""Validate local pacing limits before they reach a transport or persistence."""
	for key in ("request_delay", "batch_greet_delay"):
		value = cfg.get(key, DEFAULTS[key])
		if (
			not isinstance(value, (list, tuple))
			or len(value) != 2
			or not all(
				isinstance(item, (int, float)) and not isinstance(item, bool) and math.isfinite(item) for item in value
			)
			or not 0 <= value[0] <= value[1]
		):
			raise ValueError(f"{key} 必须包含两个有限数值，且满足 0 <= 下限 <= 上限")
	limit = cfg.get("request_max_per_minute", DEFAULTS["request_max_per_minute"])
	if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
		raise ValueError("request_max_per_minute 必须是正整数")
	seconds = cfg.get("request_wait_timeout", DEFAULTS["request_wait_timeout"])
	if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or seconds <= 0:
		raise ValueError("request_wait_timeout 必须是有限正数")
	directory = cfg.get("request_budget_dir")
	if directory is not None:
		if not isinstance(directory, str) or not directory.strip():
			raise ValueError("request_budget_dir 必须是非空绝对路径或 null")
		expand_budget_directory(directory)


def load_config(config_path: Path | None) -> dict[str, Any]:
	cfg = dict(DEFAULTS)
	user_cfg: dict[str, Any] = {}
	if config_path and config_path.exists():
		with open(config_path, encoding="utf-8") as f:
			try:
				user_cfg = json.load(f)
			except json.JSONDecodeError as exc:
				# 这份文件坏掉时所有命令都在启动阶段退出，连 config reset 也跑不了：
				# 消息必须给出文件路径和行列，让用户知道要改哪份文件、怎么恢复。
				raise ValueError(
					f"{config_path} 不是合法 JSON（第 {exc.lineno} 行第 {exc.colno} 列：{exc.msg}）："
					"修正该文件或删除它后重试；删除后所有配置回到默认值"
				) from exc
		if not isinstance(user_cfg, dict):
			raise ValueError(f"{config_path} 必须是 JSON 对象")
		cfg.update(user_cfg)
	mode = user_cfg.get("operating_mode")
	if mode not in {"assisted", "research"}:
		if "low_risk_mode" in user_cfg:
			mode = "research" if user_cfg["low_risk_mode"] is False else "assisted"
		else:
			mode = DEFAULTS.get("operating_mode", "assisted")
	if mode not in {"assisted", "research"}:
		mode = "assisted"
	cfg["operating_mode"] = mode
	return cfg
