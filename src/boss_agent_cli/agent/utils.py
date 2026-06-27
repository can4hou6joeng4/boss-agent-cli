"""Agent 辅助函数。"""

from __future__ import annotations

import json
from typing import Any


def parse_llm_json(text: str) -> dict[str, Any]:
	"""解析 LLM 返回的 JSON（兼容 markdown 代码块）。"""
	raw = text.strip()
	if raw.startswith("```"):
		lines = [ln for ln in raw.split("\n") if not ln.strip().startswith("```")]
		raw = "\n".join(lines).strip()
	return json.loads(raw)
