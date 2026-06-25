"""boss-agent-cli — BOSS 直聘求职 CLI 工具，专为 AI Agent 设计。

Public API 使用示例:

    from boss_agent_cli import AuthManager, BossClient, CacheStore
    from boss_agent_cli import AuthRequired, TokenRefreshFailed

    auth = AuthManager(data_dir)
    with BossClient(auth) as client:
        result = client.search_jobs("Golang", city="广州")
"""

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
	from boss_agent_cli.ai.service import AIService, AIServiceError
	from boss_agent_cli.api.client import AccountRiskError, AuthError, BossClient
	from boss_agent_cli.api.models import JobDetail, JobItem
	from boss_agent_cli.auth.manager import AuthManager, AuthRequired, TokenRefreshFailed
	from boss_agent_cli.cache.store import CacheStore
	from boss_agent_cli.platforms import BossPlatform, Platform, ZhilianPlatform, get_platform, list_platforms
	from boss_agent_cli.resume.models import ResumeData, ResumeFile

__version__ = "1.14.0"

_LAZY_EXPORT_MODULES = {
	"AuthManager": "boss_agent_cli.auth.manager",
	"AuthRequired": "boss_agent_cli.auth.manager",
	"TokenRefreshFailed": "boss_agent_cli.auth.manager",
	"BossClient": "boss_agent_cli.api.client",
	"AuthError": "boss_agent_cli.api.client",
	"AccountRiskError": "boss_agent_cli.api.client",
	"JobItem": "boss_agent_cli.api.models",
	"JobDetail": "boss_agent_cli.api.models",
	"CacheStore": "boss_agent_cli.cache.store",
	"AIService": "boss_agent_cli.ai.service",
	"AIServiceError": "boss_agent_cli.ai.service",
	"ResumeData": "boss_agent_cli.resume.models",
	"ResumeFile": "boss_agent_cli.resume.models",
	"Platform": "boss_agent_cli.platforms",
	"BossPlatform": "boss_agent_cli.platforms",
	"QianchengPlatform": "boss_agent_cli.platforms",
	"ZhilianPlatform": "boss_agent_cli.platforms",
	"get_platform": "boss_agent_cli.platforms",
	"list_platforms": "boss_agent_cli.platforms",
}

__all__ = [
	# 版本
	"__version__",
	# 认证
	"AuthManager",
	"AuthRequired",
	"TokenRefreshFailed",
	# API 客户端
	"BossClient",
	"AuthError",
	"AccountRiskError",
	# 数据模型
	"JobItem",
	"JobDetail",
	# 缓存
	"CacheStore",
	# AI 服务
	"AIService",
	"AIServiceError",
	# 简历模型
	"ResumeData",
	"ResumeFile",
	# 平台抽象（Week 1 ABC，详见 Issue #129）
	"Platform",
	"BossPlatform",
	"QianchengPlatform",
	"ZhilianPlatform",
	"get_platform",
	"list_platforms",
]


def __getattr__(name: str) -> object:
	"""Resolve package-level public API exports on first access."""
	try:
		module_name = _LAZY_EXPORT_MODULES[name]
	except KeyError:
		raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None

	value = getattr(import_module(module_name), name)
	globals()[name] = value
	return value
