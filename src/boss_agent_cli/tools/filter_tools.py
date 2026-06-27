"""岗位过滤工具 - 实现智能岗位筛选逻辑"""

import re
from typing import Any


class FilterTools:
    """岗位过滤工具类"""

    # 外包相关关键词
    OUTSOURCING_KEYWORDS = [
        "外包", "外派", "第三方", "外包公司", "人力外包",
        "项目外包", "外包项目", "驻场", "驻场开发"
    ]

    # 劳务派遣相关关键词
    DISPATCH_KEYWORDS = [
        "劳务派遣", "派遣", "人才派遣", "派遣公司",
        "劳务公司", "人力资源服务"
    ]

    # 异地相关关键词
    REMOTE_KEYWORDS = [
        "异地", "远程", "异地办公", "远程办公",
        "居家办公", "home office", "remote"
    ]

    def __init__(self, min_salary: int | None = None, exclude_outsourcing: bool = True,
                 exclude_dispatch: bool = True, exclude_remote: bool = True):
        """初始化过滤规则

        Args:
            min_salary: 最低薪资要求（K）
            exclude_outsourcing: 是否排除外包
            exclude_dispatch: 是否排除劳务派遣
            exclude_remote: 是否排除异地/远程
        """
        self.min_salary = min_salary
        self.exclude_outsourcing = exclude_outsourcing
        self.exclude_dispatch = exclude_dispatch
        self.exclude_remote = exclude_remote

    def should_filter_job(self, job_data: dict[str, Any]) -> tuple[bool, str]:
        """判断是否应该过滤该岗位

        Args:
            job_data: 职位数据

        Returns:
            (是否过滤, 过滤原因)
        """
        # 检查薪资
        if self.min_salary and not self._check_salary(job_data):
            return True, f"薪资低于{self.min_salary}K"

        # 检查外包
        if self.exclude_outsourcing and self._is_outsourcing(job_data):
            return True, "外包岗位"

        # 检查劳务派遣
        if self.exclude_dispatch and self._is_dispatch(job_data):
            return True, "劳务派遣岗位"

        # 检查异地
        if self.exclude_remote and self._is_remote(job_data):
            return True, "异地/远程岗位"

        return False, ""

    def _check_salary(self, job_data: dict[str, Any]) -> bool:
        """检查薪资是否符合要求"""
        salary_str = job_data.get("salary", "")
        if not salary_str:
            return False

        # 解析薪资范围，如 "15-25K", "15-25·15薪"
        match = re.search(r"(\d+)-(\d+)", salary_str)
        if match:
            min_sal = int(match.group(1))
            return min_sal >= self.min_salary

        # 单一薪资，如 "20K"
        match = re.search(r"(\d+)", salary_str)
        if match:
            sal = int(match.group(1))
            return sal >= self.min_salary

        return False

    def _is_outsourcing(self, job_data: dict[str, Any]) -> bool:
        """判断是否为外包岗位"""
        # 检查公司名称
        company_name = job_data.get("brandName", "") or job_data.get("company", "")
        if any(keyword in company_name for keyword in self.OUTSOURCING_KEYWORDS):
            return True

        # 检查职位名称
        title = job_data.get("title", "") or job_data.get("jobName", "")
        if any(keyword in title for keyword in self.OUTSOURCING_KEYWORDS):
            return True

        # 检查职位描述
        description = job_data.get("description", "") or job_data.get("jobDetail", "")
        if any(keyword in description for keyword in self.OUTSOURCING_KEYWORDS):
            return True

        return False

    def _is_dispatch(self, job_data: dict[str, Any]) -> bool:
        """判断是否为劳务派遣岗位"""
        # 检查公司名称
        company_name = job_data.get("brandName", "") or job_data.get("company", "")
        if any(keyword in company_name for keyword in self.DISPATCH_KEYWORDS):
            return True

        # 检查职位描述
        description = job_data.get("description", "") or job_data.get("jobDetail", "")
        if any(keyword in description for keyword in self.DISPATCH_KEYWORDS):
            return True

        return False

    def _is_remote(self, job_data: dict[str, Any]) -> bool:
        """判断是否为异地/远程岗位"""
        # 检查工作地点
        location = job_data.get("city", "") or job_data.get("workCity", "")
        if any(keyword in location for keyword in self.REMOTE_KEYWORDS):
            return True

        # 检查职位描述
        description = job_data.get("description", "") or job_data.get("jobDetail", "")
        if any(keyword in description for keyword in self.REMOTE_KEYWORDS):
            return True

        return False

    def filter_jobs(self, jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """批量过滤岗位

        Args:
            jobs: 职位列表

        Returns:
            过滤后的职位列表
        """
        filtered = []
        for job in jobs:
            should_filter, reason = self.should_filter_job(job)
            if not should_filter:
                filtered.append(job)
            else:
                # 可以记录过滤原因
                job["_filter_reason"] = reason

        return filtered
