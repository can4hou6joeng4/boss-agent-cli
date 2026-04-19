---
name: boss-agent-cli
description: >-
  BOSS Zhipin (BOSS直聘) job search CLI for AI agents — 32 commands covering search
  with welfare filtering, greet, apply, pipeline tracking, resume AI optimization.
  All output is structured JSON via stdout. 4-tier login: Cookie → CDP → QR httpx → patchright.
  Trigger: 找工作, 投简历, boss直聘, 招聘, 搜索职位, 打招呼, 福利筛选, 双休, 求职流水线,
  简历优化, job search, recruitment, greet recruiter, batch-greet, welfare filter, BOSS Zhipin.
metadata: {"openclaw":{"requires":{"bins":["boss"]},"emoji":"💼","homepage":"https://github.com/can4hou6joeng4/boss-agent-cli"}}
---

# boss-agent-cli

BOSS 直聘求职 CLI，专为 AI Agent 设计。32 个命令覆盖搜索、筛选、打招呼、沟通、流水线、简历优化全流程。通过 Bash 调用 `boss` 命令，解析 stdout JSON 完成求职操作链。

## Codex 环境默认约定

在 Codex 沙箱里，默认使用可写数据目录：

```bash
boss --data-dir /tmp/boss-agent-codex --json <command>
```

- 不要依赖默认的 `~/.boss-agent`，该目录在 Codex 沙箱里可能不可写
- 优先显式带上 `--json`，确保稳定返回 JSON 信封
- 所有命令统一复用 `/tmp/boss-agent-codex`

如果本机已有登录态，但 `/tmp/boss-agent-codex` 还是空的，先做一次迁移：

```bash
mkdir -p /tmp/boss-agent-codex
if [ ! -f /tmp/boss-agent-codex/auth/session.enc ] && [ -f ~/.boss-agent/auth/session.enc ]; then
  cp -R ~/.boss-agent/auth /tmp/boss-agent-codex/
fi
```

若命令因沙箱网络限制失败（典型表现：DNS/network error、无法访问 zhipin.com），应对同一条 `boss` 命令申请提权后重试，而不是改掉命令参数或跳过该步骤。

## 安装前置

运行此 skill 需要 `boss` 命令可用。若不可用，提示用户执行：

```bash
uv tool install boss-agent-cli
patchright install chromium    # QR httpx 模式可省略此步
```

验证安装：`boss schema`（应返回 JSON 能力描述）。

## Agent 决策树

```
用户意图 → 选择命令链
│
├─ "帮我找工作"
│   → boss status → boss search "关键词" --city X --welfare "Y"
│   → boss detail <sid> → boss greet <sid> <jid>
│
├─ "有什么新职位？"
│   → boss watch run <name>  (已有监控)
│   → boss recommend         (个性化推荐)
│
├─ "我的求职进展怎样？"
│   → boss pipeline → boss follow-up → boss digest
│
├─ "帮我优化简历"
│   → boss ai analyze-jd → boss ai polish → boss ai optimize
│
├─ "查看沟通记录"
│   → boss chat → boss chatmsg <sid> → boss chat-summary <sid>
│
├─ "登录/环境有问题"
│   → boss doctor → boss login
│
└─ "不知道能做什么"
    → boss schema  (返回全部能力 JSON)
```

## 输出协议

所有命令输出 JSON 到 stdout，格式固定：

```json
{
  "ok": true,
  "schema_version": "1.0",
  "command": "命令名",
  "data": {},
  "pagination": null,
  "error": null,
  "hints": {"next_actions": ["下一步建议"]}
}
```

- `ok=false` 时读 `error.code` 判断错误类型，`error.recovery_action` 包含修复命令
- `hints.next_actions` 包含 Agent 可直接执行的下一步命令
- stderr 为日志/进度信息，忽略即可
- exit code 0 = 成功，1 = 失败

## 调用流程

**必须按此顺序操作：**

```
1. boss --json status         → 检查登录态
2. boss --json login          → 若未登录（四级降级：Cookie → CDP → QR → patchright）
3. boss --json search <关键词>  → 搜索职位
   boss --json recommend       → 或获取推荐
4. boss --json detail <sid>    → 查看详情
5. boss --json greet <sid> <jid> → 打招呼
6. boss --json pipeline        → 追踪进度
7. boss --json digest          → 每日摘要
```

> Codex 环境中所有命令加 `--data-dir /tmp/boss-agent-codex`

## 命令速查（32 个）

运行 `boss schema` 获取完整命令定义。运行 `boss <cmd> --help` 查看单个命令帮助。

### 基础

| 命令 | 用途 | 关键参数 |
|------|------|----------|
| `boss schema` | 能力自描述（32 命令 + 17 错误码） | 无 |
| `boss login` | 四级降级登录 | `--timeout` `--cdp` |
| `boss logout` | 退出登录 | 无 |
| `boss status` | 检查登录态 | 无 |
| `boss doctor` | 诊断环境、凭据完整性、网络 | 无 |
| `boss me` | 个人信息/简历/期望/投递 | `--section` `--deliver-page` |

### 搜索

| 命令 | 用途 | 关键参数 |
|------|------|----------|
| `boss search <query>` | 搜索职位 | `--city` `--salary` `--welfare` `--experience` `--education` `--scale` `--industry` `--stage` `--preset` |
| `boss recommend` | 个性化推荐 | `--page` |
| `boss detail <sid>` | 职位详情 | `--job-id`（快速通道） |
| `boss show <#>` | 按编号查看上次结果 | 无 |
| `boss cities` | 40 个支持城市 | 无 |

### 动作

| 命令 | 用途 | 关键参数 |
|------|------|----------|
| `boss greet <sid> <jid>` | 打招呼 | `--message` |
| `boss batch-greet <query>` | 批量打招呼（上限 10） | `--count` `--dry-run` |
| `boss apply <sid> <jid>` | 投递/立即沟通（幂等） | 无 |
| `boss exchange <sid>` | 交换手机/微信 | 无 |

### 沟通

| 命令 | 用途 | 关键参数 |
|------|------|----------|
| `boss chat` | 沟通列表 | `--from` `--days` `--export` |
| `boss chatmsg <sid>` | 聊天消息 | `--page` |
| `boss chat-summary <sid>` | 结构化摘要 | 无 |
| `boss mark <sid>` | 标签管理 | `--label` |
| `boss interviews` | 面试邀请 | 无 |
| `boss history` | 浏览历史 | 无 |

### 流水线

| 命令 | 用途 | 关键参数 |
|------|------|----------|
| `boss pipeline` | 求职流水线 | 无 |
| `boss follow-up` | 跟进提醒 | 无 |
| `boss digest` | 每日摘要 | 无 |
| `boss watch add/list/remove/run` | 增量监控 | 搜索条件 |
| `boss shortlist add/list/remove` | 候选池 | `<sid>` |
| `boss preset add/list/remove` | 搜索预设 | 参数组合 |

### 简历与 AI

| 命令 | 用途 | 关键参数 |
|------|------|----------|
| `boss resume` | 本地简历管理 | 子命令：init/list/show/edit/delete/export/import/clone/diff |
| `boss ai config` | 配置 AI 服务 | `--provider` `--model` `--api-key` |
| `boss ai analyze-jd` | 分析岗位要求 | 无 |
| `boss ai polish` | 润色简历 | 无 |
| `boss ai optimize` | 针对岗位优化 | 无 |
| `boss ai suggest` | 求职建议 | 无 |
| `boss ai reply` | 招聘者消息回复草稿 | `--context` `--resume` `--tone` |
| `boss ai interview-prep` | 基于 JD 生成模拟面试题 | `--resume` `--count` |
| `boss ai chat-coach` | 基于聊天记录给沟通建议 | `--resume` `--style` |

### 系统

| 命令 | 用途 | 关键参数 |
|------|------|----------|
| `boss config list/set/reset` | 配置管理 | 配置键值 |
| `boss clean` | 清理缓存 | `--dry-run` `--all` `--days` |
| `boss export <query>` | 导出结果 | `--format csv/json` `--output` `--count` |

## 福利筛选（核心功能）

用户提到福利要求（如"要双休"、"五险一金"）时，使用 `--welfare` 参数：

```bash
boss --json search "golang" --city 广州 --welfare "双休,五险一金"
```

- 逗号分隔 = AND 逻辑，所有条件都必须满足
- 自动翻页逐个检查职位福利标签和描述
- 结果带 `welfare_match` 字段说明匹配来源
- 常用关键词：双休、五险一金、年终奖、餐补、住房补贴、定期体检、股票期权、加班补助

## 错误处理

| 错误码 | 含义 | 处理方式 |
|--------|------|----------|
| AUTH_REQUIRED | 未登录 | 执行 `boss login` |
| AUTH_EXPIRED | 登录过期 | 执行 `boss login` |
| TOKEN_REFRESH_FAILED | Token 刷新失败 | 执行 `boss login` |
| RATE_LIMITED | 频率过高 | 等待后重试 |
| ACCOUNT_RISK | 风控拦截 | CDP Chrome 重试 |
| NETWORK_ERROR | 网络错误 | 重试一次 |
| AI_NOT_CONFIGURED | AI 未配置 | `boss ai config` |
| AI_API_ERROR | AI 调用失败 | 重试 |
| INVALID_PARAM | 参数错误 | 修正参数重试 |
| ALREADY_GREETED | 已打过招呼 | 跳过 |
| ALREADY_APPLIED | 已投递 | 跳过 |
| GREET_LIMIT | 今日次数用完 | 告知用户明天再试 |
| JOB_NOT_FOUND | 职位不存在 | 告知用户 |
| RESUME_NOT_FOUND | 简历不存在 | 检查名称 |

## 行为规则

1. **先检查登录态**：每次操作前调用 `boss status`，失败则 `boss login`
2. **福利要求用 --welfare**：用户说"要双休"→ `--welfare "双休"`
3. **无需额外 sleep**：工具内置高斯分布请求延迟
4. **批量打招呼先 dry-run**：`boss batch-greet ... --dry-run` 让用户确认
5. **解析 JSON 不解析文本**：用 `ok` 字段判断成败
6. **未指定关键词用 recommend**：用户说"推荐职位"→ `boss recommend`
7. **导出用 export**：用户说"导出"、"下载列表"→ `boss export`
8. **展示结果含福利**：welfare 字段是列表，向用户展示时列出
9. **遇到沙箱网络错误先提权重试**：不要在 DNS/网络受限时报"工具不可用"
10. **求职进度用 pipeline**：用户问"进展怎样"→ `boss pipeline` + `boss digest`
