# Capability Matrix

用于统一 CLI / Skill / MCP 的能力对照，方便 Agent 在不同接入面保持同一语义。

| 能力 | CLI 命令 | Skill 入口 | MCP 工具 |
|---|---|---|---|
| 协议发现 | `boss schema` | `schema` | `boss_schema` |
| 登录态检查 | `boss status` | `status` | `boss_status` |
| 环境诊断 | `boss doctor` | `doctor` | `boss_doctor` |
| 职位搜索 | `boss search` | `search` | `boss_search` |
| 个性化推荐 | `boss recommend` | `recommend` | `boss_recommend` |
| 职位详情 | `boss detail` | `detail` | `boss_detail` |
| 打招呼 | `boss greet` | `greet` | `boss_greet` |
| 沟通列表 | `boss chat` | `chat` | `boss_chat` |
| 我的信息 | `boss me` | `me` | `boss_me` |
| 城市列表 | `boss cities` | `cities` | `boss_cities` |
| 面试邀请 | `boss interviews` | `interviews` | `boss_interviews` |
| 浏览历史 | `boss history` | `history` | `boss_history` |

说明：
- Skill 入口列表示 Agent 在 skill 语境下的动作名。
- MCP 工具列对应 `mcp-server/server.py` 的工具名。
- 若以 CLI 直连为主，优先通过 `boss schema` 进行能力发现与参数校验。
