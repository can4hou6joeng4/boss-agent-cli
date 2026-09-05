"""首次招呼的私有收尾进程；超时后终止，不能发送或重试招呼。"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

from boss_agent_cli.api.httpx_helpers import remaining_timeout
from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.commands._recruiter_platform import build_recruiter_platform_instance


def run_read_receipt(
	*,
	data_dir: Path,
	platform_name: str,
	delay: tuple[float, float],
	cdp_url: str | None,
	start_result: dict[str, Any],
	geek_id: str,
	job_id: str,
	security_id: str,
	timeout: float,
	allow_mqtt_session: bool = False,
) -> dict[str, Any]:
	"""总预算包含进程启动、认证读取、DNS、HTTP 和 MQTT；超时杀死并回收子进程。"""
	deadline = time.monotonic() + timeout
	payload = {
		"data_dir": str(data_dir), "platform_name": platform_name, "delay": delay, "cdp_url": cdp_url,
		"start_result": start_result, "geek_id": geek_id, "job_id": job_id, "security_id": security_id,
		"deadline": deadline, "allow_mqtt_session": allow_mqtt_session,
	}
	try:
		result = subprocess.run(
			[sys.executable, "-m", "boss_agent_cli.commands.recruiter._read_receipt_worker"],
			input=json.dumps(payload), capture_output=True, text=True, timeout=remaining_timeout(deadline),
		)
		remaining_timeout(deadline)
	except (subprocess.TimeoutExpired, TimeoutError):
		return {"status": "timeout", "reason": "read_receipt_timeout"}
	except OSError:
		return {"status": "failed", "reason": "read_receipt_worker_failed", "error_code": "NETWORK_ERROR"}
	try:
		state = json.loads(result.stdout) if result.returncode == 0 else None
	except ValueError:
		state = None
	if not isinstance(state, dict) or state.get("status") not in ("not_needed", "published", "unknown", "deferred", "failed", "timeout"):
		return {"status": "failed", "reason": "read_receipt_worker_failed", "error_code": "NETWORK_ERROR"}
	return state


def main() -> None:
	from boss_agent_cli.commands.recruiter.recommendations import _read_state_after_greet

	payload = json.load(sys.stdin)
	auth = AuthManager(Path(payload["data_dir"]), platform=payload["platform_name"])
	with build_recruiter_platform_instance(
		payload["platform_name"], auth, delay=tuple(payload["delay"]), cdp_url=payload["cdp_url"],
	) as platform:
		state = _read_state_after_greet(
			platform, start_result=payload["start_result"], geek_id=payload["geek_id"],
			job_id=payload["job_id"], security_id=payload["security_id"], deadline=payload["deadline"],
			allow_mqtt_session=payload["allow_mqtt_session"] is True,
		)
	print(json.dumps(state))


if __name__ == "__main__":
	main()
