# Boss Resume Downloader Reference

## Known recruiter state

Verified recruiter login in this workspace:

```json
{
  "logged_in": true,
  "user_name": "<recruiter_name>"
}
```

Known online job from prior verification:

```json
{
  "jobName": "<job_name>",
  "jobId": "<jobId>",
  "encryptJobId": "<encryptJobId>",
  "salaryDesc": "<salary>",
  "address": "<city>",
  "jobOnlineStatus": 1
}
```

## Critical implementation notes

- Correct platform argument is `--platform zhipin`.
- `--platform zhipin-recruiter` fails with unknown platform.
- Use `--role recruiter` for recruiter commands.
- Prefer `--cdp-url http://localhost:9222` to reuse the logged-in Chrome session.
- Resume view requires both `--job-id` and `--security-id`.
- `encryptFriendId` was tested and is not necessarily equivalent to `securityId`.
- 2026-05-14 实测：`friend_detail([friendId])` 内部接口可返回 `securityId`、候选人姓名、`encryptUid`、`jobId`、`encryptJobId`，可用于补齐 `hr applications` 缺失的 `securityId`。
- If candidate security ID cannot be resolved, keep candidate in the index as pending rather than failing permanently.

## CLI commands of interest

```bash
boss --role recruiter --platform zhipin --cdp-url http://localhost:9222 status
boss --role recruiter --platform zhipin --cdp-url http://localhost:9222 login --cdp
boss --role recruiter --platform zhipin --cdp-url http://localhost:9222 hr jobs list
boss --role recruiter --platform zhipin --cdp-url http://localhost:9222 hr applications --job-id <encryptJobId> --page <page>
boss --role recruiter --platform zhipin --cdp-url http://localhost:9222 hr candidates <keyword>
boss --role recruiter --platform zhipin --cdp-url http://localhost:9222 hr chat
boss --role recruiter --platform zhipin --cdp-url http://localhost:9222 hr resume <encryptGeekId> --job-id <encryptJobId> --security-id <securityId>
```
