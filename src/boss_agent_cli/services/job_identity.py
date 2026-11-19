"""同一次候选人结果中的职位去重；不建立跨账号或历史记录的身份映射。"""

from typing import Any


def deduplicate_job_result(
	items: list[dict[str, Any]], *, seen_job_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
	"""在单次结果/分页 workflow 中按非空职位 ID 保留首条完整列表项。

	job_id/encryptJobId 是职位标识，security_id/securityId 与 lid 是访问上下文。
	去重不合并字段，不从缺失 ID、招聘者或标题推断职位；无 ID 条目原样保留。
	seen_job_ids 只能在同一次 workflow 内共享，不能由历史缓存或其他账号填充。
	"""
	seen = seen_job_ids if seen_job_ids is not None else set()
	unique = []
	for item in items:
		if not isinstance(item, dict):
			continue
		# 真值但不可用的 job_id（int / dict / list）不能遮蔽合法的 encryptJobId，
		# 否则同一职位不再去重（Issue #1879）。
		job_id = item.get("job_id")
		if not (isinstance(job_id, str) and job_id):
			job_id = item.get("encryptJobId")
		if isinstance(job_id, str) and job_id:
			if job_id in seen:
				continue
			seen.add(job_id)
		unique.append(item)
	return unique
