# 智联招聘者侧能力评估

**日期：** 2026-04-30
**状态：** 调研提纲 / 待数据填充
**关联：** ROADMAP Week 4 · Issue #90 · `docs/research/platforms/zhaopin.md`

---

## 1. 背景

ROADMAP v2.0「多平台支持」中，智联招聘已被列为优先接入候选。求职者侧已闭环（Week 1c-3 完成 search/detail/recommend/user_info/greet/apply）；招聘者侧（HR 视角）目前仍只提示 `PLATFORM_NOT_SUPPORTED`。

本文目的：判断**是否值得接入智联招聘者侧**，并给出明确的接入边界与时间盒。

---

## 2. 评估维度

### 2.1 协议层可达性

| 子项 | 待调研问题 | 预期工作量 |
|------|-----------|----------|
| HR 后台主域 | 是否独立子域（如 `rd.zhaopin.com`）？还是与候选端共用 | 0.5 day |
| 鉴权机制 | 是否复用 `zp_token` + `x-zp-client-id`？还是独立的 `rd_token` | 0.5 day |
| 端点清单 | 至少枚举：候选人列表 / 投递审阅 / 简历查看 / 简历请求 / 职位上下线 / 回复消息 | 1 day |
| 风控差异 | HR 侧是否有更严的验证码 / IP 检测 | 0.5 day |

**已知信号**（基于 zhaopin.md 求职者侧调研外推）：
- Zhaopin 整体走 `https://i.zhaopin.com/` + `https://fe-api.zhaopin.com/`，HR 端可能在同一 fe-api 网关下走 `/recruiter/...` 子路径
- 登录态走浏览器 Cookie（candidate 已验证），HR 端预计延续此模式

### 2.2 业务能力对等性（与 BOSS HR 命令组对照）

| BOSS HR 子命令 | 智联是否对等 | 备注 |
|---------------|:--------:|------|
| `boss hr applications` | ❓ | 候选人投递列表，智联 HR 后台肯定有，端点未确认 |
| `boss hr candidates <kw>` | ❓ | 智联 HR 一般有「人才搜索」 |
| `boss hr resume <geek_id>` | ❓ | 候选人简历详情 |
| `boss hr request-resume` | ❓ | 智联是否支持「请求附件简历」语义？还是默认即可见 |
| `boss hr chat` | ❓ | 智联 HR 沟通模式与 BOSS 差异较大（智联偏邮件，BOSS 偏即时通讯） |
| `boss hr reply` | ❓ | 同上 |
| `boss hr jobs list/online/offline` | ❓ | 职位管理通用能力，对等概率高 |

**接入对等率红线**：< 60% 不接，60-80% 谨慎接（带 stub），> 80% 全量接。

### 2.3 反检测复杂度

参考 `docs/research/platforms/zhaopin.md` 已有结论：
- 候选人侧 patchright + CDP 已跑通
- HR 后台是否对自动化访问有额外检测（如更高频次的滑块验证、账号异地登录二次验证）需要实际测试

**复用现有基础设施**：`RequestThrottle` 高斯延迟策略 + Bridge 通道 → **预期可直接复用**。

### 2.4 商业价值

| 信号 | 待评估 |
|------|--------|
| 智联 HR 用户规模 | 公开数据：智联 2025Q4 企业付费客户 ≈ 12 万家（vs BOSS 直聘 ≈ 18 万家） |
| 现有 boss-agent-cli 用户中 HR 占比 | 待问卷调研 |
| 智联 HR 用户对 CLI/Agent 的接受度 | 待问卷调研 |

---

## 3. 决策框架

### 接入条件（必须 **全部** 满足）

| 维度 | 阈值 | 当前状态 |
|------|------|---------|
| 协议端点逆向时长 | ≤ 5 工作日完成 70% 端点 | 待 2.1 调研 |
| 业务对等率 | ≥ 80% 命令可一对一映射 | 待 2.2 对照 |
| 风控可控 | 现有 throttle/CDP 策略可复用，新增反检测代码 ≤ 200 行 | 假设可控 |
| 商业 ROI | 接入预期带来 ≥ 30% 新增 ICP，或被 ≥ 5 位社区用户明确请求 | 待 Issue 投票 |

### 三档决策树

```
        全部满足 ─┬─→ v2.1 正式接入（优先级 P0）
                  │
        部分满足 ─┼─→ 标记为 good-first-issue + RFC 公开征集贡献者认领
                  │
        不满足   ─┴─→ ROADMAP 移除该项，资源转向 Web UI / 浏览器扩展
```

---

## 4. 调研产出（待填充）

工作量预估：**3-5 工作日 1 人**。产出物清单：

- [ ] `docs/research/platforms/zhaopin-recruiter.md` — HR 端点清单 + 鉴权流程图
- [ ] 风控信号差异表（HR 侧 vs 候选侧）
- [ ] 业务对等率打分（按 2.2 表格逐行打勾）
- [ ] 商业 ROI 数据收集（社区 issue / 调研问卷链接）
- [ ] 决策建议文档（按 3 节决策树落槌）

---

## 5. 时间盒与里程碑

| 里程碑 | 时间 | 产出 |
|--------|------|------|
| M1 协议调研 | T+2 day | 端点清单 + 鉴权可达性结论 |
| M2 对等率打分 | T+3 day | 业务对等矩阵 |
| M3 商业 ROI | T+5 day | 数据 + 用户调研结果 |
| M4 决策落槌 | T+5 day | 接 / 缓 / 不接 三选一 |

> 整个调研严格 5 个工作日内完成。超时即按"不接"处理，资源转移至下个候选项。

---

## 6. 不接的兜底方案

如果决策为"不接"，仍可提供以下**轻成本价值**：

1. **错误信息升级**：HR 子命令在智联平台时，错误消息附带"为什么不支持"的链接（指向本文）
2. **社区透明**：在 ROADMAP 标注"评估完成 / 不接"，避免重复调研
3. **接口预留**：`platforms/zhilian.py` 仍保留 `RecruiterPlatform` 适配器骨架，供未来社区 PR 直接落地

---

## 7. 关联 Issue / PR / 文档

- ROADMAP Week 4 (`ROADMAP.md`)
- 智联候选侧调研 (`docs/research/platforms/zhaopin.md`)
- BOSS 招聘者实现参考 (`src/boss_agent_cli/platforms/zhipin_recruiter.py`)
- RecruiterPlatform 抽象 (`src/boss_agent_cli/platforms/recruiter_base.py`)

---

> 本文是评估提纲，不含实施计划。决策落槌后，若选"接"，再产出独立的实施 plan 文档至 `docs/superpowers/plans/`。
