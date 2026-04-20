"""boss-agent-cli — BOSS 直聘求职 CLI 工具，专为 AI Agent 设计。

Public API 使用示例:

    from boss_agent_cli import AuthManager, BossClient, CacheStore
    from boss_agent_cli import AuthRequired, TokenRefreshFailed

    auth = AuthManager(data_dir)
    with BossClient(auth) as client:
        result = client.search_jobs("Golang", city="广州")
"""

from boss_agent_cli.ai.service import AIService, AIServiceError
from boss_agent_cli.api.client import AccountRiskError, AuthError, BossClient
from boss_agent_cli.api.models import JobDetail, JobItem
from boss_agent_cli.auth.manager import AuthManager, AuthRequired, TokenRefreshFailed
from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.resume.models import ResumeData, ResumeFile

__version__ = "1.8.14"

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
]
