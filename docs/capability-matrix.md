# Capability Matrix

用于统一 CLI / Skill / MCP 的能力对照，方便 Agent 在不同接入面保持同一语义。

## 认证与环境

| 能力 | CLI 命令 | 需要登录 | 通道 |
|---|---|---|---|
| 协议发现 | `boss schema` | 否 | 本地 |
| 登录 | `boss login` | 否 | 浏览器 |
| 退出登录 | `boss logout` | 否 | 本地 |
| 登录态检查 | `boss status` | 是 | httpx |
| 环境诊断 | `boss doctor` | 否 | 混合 |
| 配置管理 | `boss config` | 否 | 本地 |
| 缓存清理 | `boss clean` | 否 | 本地 |

## 职位发现

| 能力 | CLI 命令 | 需要登录 | 通道 |
|---|---|---|---|
| 职位搜索 | `boss search` | 是 | 浏览器 |
| 个性化推荐 | `boss recommend` | 是 | 浏览器 |
| 职位详情 | `boss detail` | 是 | httpx 优先，降级浏览器 |
| 按编号查看 | `boss show` | 否 | 本地缓存 |
| 城市列表 | `boss cities` | 否 | httpx |
| 浏览历史 | `boss history` | 是 | httpx |

## 求职动作

| 能力 | CLI 命令 | 需要登录 | 通道 |
|---|---|---|---|
| 打招呼 | `boss greet` | 是 | 浏览器 |
| 批量打招呼 | `boss batch-greet` | 是 | 浏览器 |
| 投递沟通 | `boss apply` | 是 | 浏览器 |
| 导出结果 | `boss export` | 是 | 浏览器 |

## 沟通管理

| 能力 | CLI 命令 | 需要登录 | 通道 |
|---|---|---|---|
| 沟通列表 | `boss chat` | 是 | httpx |
| 聊天消息 | `boss chatmsg` | 是 | httpx |
| 聊天摘要 | `boss chat-summary` | 是 | httpx |
| 联系人标签 | `boss mark` | 是 | httpx |
| 交换联系方式 | `boss exchange` | 是 | httpx |
| 面试邀请 | `boss interviews` | 是 | httpx |

## 流程管理

| 能力 | CLI 命令 | 需要登录 | 通道 |
|---|---|---|---|
| 候选进度 | `boss pipeline` | 是 | httpx |
| 跟进筛选 | `boss follow-up` | 是 | httpx |
| 日报汇总 | `boss digest` | 是 | httpx |
| 增量监控 | `boss watch` | 是 | 浏览器 |
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

说明：
- **通道**：httpx 为直接 API 请求（低风险），浏览器为 CDP/patchright 通道（高风险操作需要真实浏览器指纹），AI 服务为第三方大模型 API。
- 若以 CLI 直连为主，优先通过 `boss schema` 进行能力发现与参数校验。
- 以 `boss schema` 为准：当前暴露 33 个顶层命令；其中 `hr` 下还有 7 个一级招聘者子命令，`ai` / `resume` 为命令组入口。
