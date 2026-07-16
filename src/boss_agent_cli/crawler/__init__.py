"""Explicit Research Mode crawl support."""

from boss_agent_cli.crawler.hooks import HOOK_SCRIPT_NAMES, inject_hook_profile
from boss_agent_cli.crawler.service import CrawlOutcome, CrawlService
from boss_agent_cli.crawler.transport import CrawlRiskError, DrissionCrawlerSession

__all__ = [
	"CrawlOutcome",
	"CrawlRiskError",
	"CrawlService",
	"DrissionCrawlerSession",
	"HOOK_SCRIPT_NAMES",
	"inject_hook_profile",
]
