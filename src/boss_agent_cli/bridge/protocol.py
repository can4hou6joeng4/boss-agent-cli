"""Browser Bridge 协议定义 — daemon / 扩展 / CLI 三方共享。"""

from dataclasses import dataclass, field
from typing import Any
import os
import time

# ── 端口配置 ──────────────────────────────────────────────────────────

DEFAULT_PORT = 19826
BRIDGE_PORT = int(os.getenv("BOSS_BRIDGE_PORT", str(DEFAULT_PORT)))
BRIDGE_HOST = "127.0.0.1"
DAEMON_WS_PATH = "/ext"
DAEMON_PING_PATH = "/ping"
DAEMON_STATUS_PATH = "/status"
DAEMON_COMMAND_PATH = "/command"

# daemon 空闲超时：4 小时
DAEMON_IDLE_TIMEOUT = int(os.getenv("BOSS_BRIDGE_TIMEOUT", str(4 * 3600)))


# ── 命令类型 ──────────────────────────────────────────────────────────

@dataclass
class BridgeCommand:
	"""CLI → daemon → 扩展 的命令。"""
	id: str
	action: str  # exec | navigate | cookies | close-window
	code: str = ""
	url: str = ""
	domain: str = ""
	workspace: str = "boss"
	tab_id: int | None = None

	def to_dict(self) -> dict[str, Any]:
		d: dict[str, Any] = {"id": self.id, "action": self.action}
		if self.code:
			d["code"] = self.code
		if self.url:
			d["url"] = self.url
		if self.domain:
			d["domain"] = self.domain
		if self.workspace:
			d["workspace"] = self.workspace
		if self.tab_id is not None:
			d["tabId"] = self.tab_id
		return d


@dataclass
class BridgeResult:
	"""扩展 → daemon → CLI 的结果。"""
	id: str
	ok: bool
	data: Any = None
	error: str = ""

	@classmethod
	def from_dict(cls, d: dict) -> "BridgeResult":
		return cls(
			id=d.get("id", ""),
			ok=d.get("ok", False),
			data=d.get("data"),
			error=d.get("error", ""),
		)


def make_command_id() -> str:
	"""生成唯一命令 ID（uuid 防碰撞）。"""
	import uuid
	return f"cmd_{uuid.uuid4().hex[:12]}"
