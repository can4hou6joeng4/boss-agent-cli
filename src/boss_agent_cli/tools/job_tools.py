"""职位操作工具 - 封装职位搜索、详情获取等操作"""

from typing import Any

from boss_agent_cli.api.client import BossClient


class JobTools:
    """职位相关操作工具类"""

    def __init__(self, client: BossClient):
        self.client = client

    def search_jobs(self, query: str, **filters: Any) -> dict[str, Any]:
        """搜索职位

        Args:
            query: 搜索关键词
            **filters: 筛选条件（city, salary, experience等）

        Returns:
            搜索结果字典
        """
        return self.client.search_jobs(query, **filters)

    def get_job_detail(self, job_id: str) -> dict[str, Any]:
        """获取职位详情

        Args:
            job_id: 职位ID

        Returns:
            职位详情字典
        """
        return self.client.job_detail(job_id)

    def get_job_card(self, security_id: str, lid: str = "") -> dict[str, Any]:
        """获取职位卡片信息

        Args:
            security_id: 安全ID
            lid: 职位ID（可选）

        Returns:
            职位卡片信息
        """
        return self.client.job_card(security_id, lid)

    def greet_recruiter(self, security_id: str, job_id: str, message: str = "") -> dict[str, Any]:
        """向招聘者打招呼

        Args:
            security_id: 安全ID
            job_id: 职位ID
            message: 打招呼消息

        Returns:
            操作结果
        """
        return self.client.greet(security_id, job_id, message)

    def apply_job(self, security_id: str, job_id: str, lid: str = "") -> dict[str, Any]:
        """投递职位

        Args:
            security_id: 安全ID
            job_id: 职位ID
            lid: 简历ID（可选）

        Returns:
            操作结果
        """
        return self.client.apply(security_id, job_id, lid)
