# Capability Matrix

用于统一 CLI / Skill / MCP 的能力对照，方便 Agent 在不同接入面保持同一语义。

默认低风险辅助模式：本地辅助、只读优先、用户主动触发、不规避风控、不批量触达、不抓取平台数据。标记为“受限”的能力会返回 `COMPLIANCE_BLOCKED`，应回到平台官网手动完成。

## 认证与环境

| 能力 | CLI 命令 | 需要登录 | 通道 |
|---|---|---|---|
| 协议发现 | `boss schema` | 否 | 本地 |
| 登录 | `boss login` | 否 | 用户主动登录 |
| 退出登录 | `boss logout` | 否 | 本地 |
| 登录态检查 | `boss status` | 是 | httpx |
| 环境诊断 | `boss doctor` | 否 | 混合 |
| 配置管理 | `boss config` | 否 | 本地 |
| 缓存清理 | `boss clean` | 否 | 本地 |

## 职位发现

| 能力 | CLI 命令 | 需要登录 | 通道 |
|---|---|---|---|
| 职位搜索 | `boss search` | 是 | 浏览器；支持 `--url` 复用网页筛选和逗号多选 |
| 个性化推荐 | `boss recommend` | 是 | 受限（默认阻断） |
| 职位详情 | `boss detail` | 是 | httpx 优先，降级浏览器 |
| 按编号查看 | `boss show` | 否 | 本地缓存 |
| 城市列表 | `boss cities` | 否 | httpx |
| 浏览历史 | `boss history` | 是 | httpx |

## 求职动作

| 能力 | CLI 命令 | 需要登录 | 通道 |
|---|---|---|---|
| 打招呼 | `boss greet` | 是 | 受限（默认阻断） |
| 批量打招呼 | `boss batch-greet` | 是 | 受限（默认阻断） |
| 投递沟通 | `boss apply` | 是 | 受限（默认阻断） |
| 导出结果 | `boss export` | 是 | 浏览器；支持 `--url` 复用网页筛选 |

## 沟通管理

| 能力 | CLI 命令 | 需要登录 | 通道 |
|---|---|---|---|
| 沟通列表 | `boss chat` | 是 | 受限（默认阻断） |
| 聊天消息 | `boss chatmsg [--raw]` | 是 | 受限（默认阻断）；`--raw` 仅在合规放行后保留结构化 body/链接/职位卡片字段 |
| 聊天摘要 | `boss chat-summary` | 是 | 受限（默认阻断） |
| 联系人标签 | `boss mark` | 是 | 受限（默认阻断） |
| 交换联系方式 | `boss exchange` | 是 | 受限（默认阻断） |
| 面试邀请 | `boss interviews` | 是 | httpx |

## 流程管理

| 能力 | CLI 命令 | 需要登录 | 通道 |
|---|---|---|---|
| 候选进度 | `boss pipeline` | 是 | 受限（默认阻断） |
| 跟进筛选 | `boss follow-up` | 是 | 受限（默认阻断） |
| 日报汇总 | `boss digest` | 是 | 受限（默认阻断） |
| 增量监控 | `boss watch run` | 是 | 受限（默认阻断）；add/list/remove 为本地 |
| 搜索预设 | `boss preset` | 否 | 本地 |
| 候选池 | `boss shortlist` | 否 | 本地 |

## 用户信息

| 能力 | CLI 命令 | 需要登录 | 通道 |
|---|---|---|---|
| 我的信息 | `boss me` | 是 | httpx |

## 简历管理

| 能力 | CLI 命令 | 需要登录 | 通道 |
|---|---|---|---|
| 本地简历管理 | `boss resume` | 视情况 | 本地（init 支持在线拉取） |

## 智能能力

| 能力 | CLI 命令 | 需要登录 | 通道 |
|---|---|---|---|
| AI 配置 | `boss ai config` | 否 | 本地 |
| JD 匹配分析 | `boss ai analyze-jd` | 否 | AI 服务 |
| 简历润色 | `boss ai polish` | 否 | AI 服务 |
| 简历定向优化 | `boss ai optimize` | 否 | AI 服务 |
| 简历改进建议 | `boss ai suggest` | 否 | AI 服务 |
| 聊天回复草稿 | `boss ai reply` | 否 | AI 服务 |
| 模拟面试题 | `boss ai interview-prep` | 否 | AI 服务 |
| 沟通教练 | `boss ai chat-coach` | 否 | AI 服务 |

## 数据洞察

| 能力 | CLI 命令 | 需要登录 | 通道 |
|---|---|---|---|
| 投递转化漏斗 | `boss stats` | 否 | 本地 |

## 招聘者工作流

| 能力 | CLI 命令 | 需要登录 | 通道 |
|---|---|---|---|
| 投递申请列表 | `boss hr applications` | 是 | 受限（默认阻断） |
| 候选人搜索 | `boss hr candidates` | 是 | 受限（默认阻断） |
| 沟通列表 | `boss hr chat` | 是 | 受限（默认阻断） |
| 聊天消息历史 | `boss hr chatmsg <friend_id>` | 是 | 受限（默认阻断） |
| 最近消息摘要 | `boss hr last-messages [--friend-id <id>]` | 是 | 受限（默认阻断） |
| 在线简历查看 | `boss hr resume <geek_id> --job-id <id> --security-id <id>` | 是 | 受限（默认阻断） |
| 联系方式交换 | `boss hr resume --exchange --friend-id <friend_id> [--type wechat]` | 是 | 受限（默认阻断） |
| 消息回复 | `boss hr reply <friend_id> <message>` | 是 | 受限（默认阻断） |
| 附件简历请求 | `boss hr request-resume <friend_id>` | 是 | 受限（默认阻断） |
| 职位列表与上下线 | `boss hr jobs` | 是 | httpx |

说明：
- **通道**：httpx 为直接 API 请求，浏览器通道仅作兼容保留；命中风控时不应切换自动化通道继续重试。AI 服务为第三方大模型 API，不应输入平台聊天记录、候选人简历或联系方式等未获授权的个人信息。
- 若以 CLI 直连为主，优先通过 `boss schema` 进行能力发现与参数校验；当前 schema 会同时暴露 `supported_platforms` 与 `supported_recruiter_platforms`。
- 当前多平台状态：`boss platforms` 返回本地平台注册与能力状态，也可通过 `boss platforms --platform qiancheng` / `--platform 51job` 只查看单个平台或别名；`zhipin` 已覆盖求职者与招聘者实现，但敏感链路默认受低风险模式阻断；`zhilian` 已接通候选者侧链路，招聘者侧自动化通过 `agent` browser/CDP adapter V1 接入；`qiancheng` / 51job 仅为已注册占位适配器，真实工作流统一返回 `NOT_SUPPORTED`。
- 当前登录状态：`zhipin` / `zhilian` 保留用户主动登录兼容链路，但不得用于规避平台风控。
- 以 `boss schema` 为准：当前暴露 36 个顶层命令；其中 `hr` 下还有 9 个一级招聘者子命令，`ai` / `resume` 为命令组入口。
