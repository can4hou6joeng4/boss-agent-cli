"""Agent配置管理"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from boss_agent_cli.ai.config import AIConfigStore

_DEFAULT_ASSETS: dict[str, str | None] = {
	"resume_path": None,
	"salary_proof_path": None,
	"education_proof_path": None,
}


class AgentConfig:
	"""Agent 配置：AI 密钥 + 本地上传素材路径。"""

	def __init__(self, data_dir: Path):
		self.data_dir = data_dir
		self.ai_config = AIConfigStore(data_dir)
		self._assets_path = data_dir / "ai" / "agent_assets.json"

	def configure_deepseek(self, api_key: str, model: str = "deepseek-chat") -> None:
		self.ai_config.save_api_key(api_key)
		self.ai_config.save_config(
			ai_provider="deepseek",
			ai_model=model,
			ai_base_url="https://api.deepseek.com/v1",
			ai_temperature=0.7,
			ai_max_tokens=4096,
		)

	def get_ai_config(self) -> dict[str, Any]:
		return self.ai_config.load_config()

	def get_api_key(self) -> str | None:
		return self.ai_config.get_api_key()

	def get_base_url(self) -> str | None:
		return self.ai_config.get_base_url()

	def is_configured(self) -> bool:
		return self.ai_config.is_configured()

	def save_assets(
		self,
		*,
		resume_path: str | None = None,
		salary_proof_path: str | None = None,
		education_proof_path: str | None = None,
	) -> None:
		current = self.get_assets()
		if resume_path is not None:
			current["resume_path"] = resume_path
		if salary_proof_path is not None:
			current["salary_proof_path"] = salary_proof_path
		if education_proof_path is not None:
			current["education_proof_path"] = education_proof_path
		self._assets_path.parent.mkdir(parents=True, exist_ok=True)
		self._assets_path.write_text(
			json.dumps(current, ensure_ascii=False, indent=2),
			encoding="utf-8",
		)

	def get_assets(self) -> dict[str, str | None]:
		assets = dict(_DEFAULT_ASSETS)
		if self._assets_path.exists():
			try:
				saved = json.loads(self._assets_path.read_text(encoding="utf-8"))
				assets.update(saved)
			except (json.JSONDecodeError, OSError):
				pass
		return assets
