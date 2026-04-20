# 拉勾网（Lagou）API 调研报告

> **结论先行**：**不建议近期接入**。主站 2023 年全面切 Next.js SSR + App Router 架构，**公开 JSON API 表面大幅收缩**，原有 `passport.lagou.com` 登录链路仍可用但 Cookie 有效期短（约 24h），**性价比不如把主精力放在智联 / 猎聘**。本报告按 Issue #90 的 7 项调研清单体例给出，同时作为其他平台调研的**样板**。
>
> 调研日期：2026-04-20 · 调研人：can4hou6joeng4 · 信息来源：公开观察（Web UI / DevTools / robots.txt）

## 1. 登录链路

| 方式 | 入口 URL | 可行性 |
|------|---------|-------|
| 账号密码 | `https://passport.lagou.com/login/login.html` | ✅ 仍可用，但密码需 RSA 前端加密（`rsa-password` 字段） |
| 手机验证码 | `https://passport.lagou.com/login/quickLogin.html` | ✅ 可用，4G 号段有滑块验证概率高 |
| 微信扫码 | `https://passport.lagou.com/login/login.html?service=...` | ✅ 可用，但微信开放平台回调链路涉及三方跳转 |
| 企业 SSO | — | ❌ 不对个人开放 |

**关键字段**：
- 登录成功后 Cookie 里关键字段：`user_trace_token`、`LGUID`、`LGSID`、`X_HTTP_TOKEN`
- `X_HTTP_TOKEN` 是**请求头级 CSRF**，每次登录重新生成，需从 HTML 头部 meta 抓取
- 没有 BOSS 直聘 `__zp_stoken__` 级别的**动态防爬 token**，但有请求级签名（见 §3）

**stoken / csrf 类防爬字段**：
- 无全局 stoken 机制
- 每请求需带 `X-Anit-Forge-Token`（浏览器运行时生成，内存 Map 存储）+ `X-Anit-Forge-Code`（时间戳哈希）
- **这个机制 2024 年后升级**：部分接口改用 `X-Lagou-Token`（JWT 格式，含 uid + exp）

## 2. 核心 API 端点清单（对标 BOSS 的 7 个端点）

| 功能 | 端点 | 方法 | 对应 BOSS |
|------|------|------|----------|
| 搜索职位 | `https://www.lagou.com/jobs/positionAjax.json` | POST form | `search_jobs` |
| 职位详情 | `https://www.lagou.com/jobs/<jobId>.html`（HTML） | GET | `job_detail` |
| 个人信息 | `https://gate.lagou.com/v1/neirong/account/users/0` | GET | `user_info` |
| 简历 | `https://gate.lagou.com/v1/neirong/resume/mine` | GET | `resume_baseinfo` |
| 发起沟通 | `https://easy.lagou.com/im/mobile/sendMessage.json` | POST | `greet` |
| 投递 | `https://www.lagou.com/deliver/sendResume.json` | POST | `apply` |
| 沟通列表 | `https://easy.lagou.com/im/mobile/friendList.json` | GET | `friend_list` |
| 聊天消息 | `https://easy.lagou.com/im/mobile/getMessageList.json` | GET | `friend_messages` |
| 面试邀请 | `https://easy.lagou.com/interview/myInterview.json` | GET | `interview_data` |

**覆盖度**：9/7（比 BOSS 多了独立的面试和聊天域名 `easy.lagou.com`）

**域名架构**：
- `www.lagou.com` — 主站页面 + 搜索/投递
- `gate.lagou.com` — 新版 API 网关（2022 年后增量端点）
- `easy.lagou.com` — 沟通类接口（原 IM 模块）
- `passport.lagou.com` — 登录
- 三方域名共享 Cookie 域 `.lagou.com`

## 3. 请求签名 / 防爬机制

| 机制 | 位置 | 生成方式 | 绕过难度 |
|------|------|---------|---------|
| Cookie 域名校验 | `wt2`-类似的 `LGSID` | 登录后 24h 有效 | 低（Cookie 提取即可） |
| `X-Anit-Forge-Token` | Request Header | 页面 JS 运行时 Map | 中（需浏览器环境） |
| `X-Anit-Forge-Code` | Request Header | SHA1(timestamp + fixedSalt) | 低（算法公开） |
| `X-Lagou-Token` | Request Header（新 API） | JWT，签名密钥前端静态 | **高**（密钥随构建版本变） |
| 搜索分页签名 | POST body 的 `pn` + `kd` 前 hash | 服务端校验 | 中 |
| UA 指纹 | `sec-ch-ua` 全系列 | 浏览器原生 | 低 |
| CDP / headless 检测 | webdriver 属性 + Chrome runtime | `navigator.webdriver`、`window.chrome.runtime` 缺失 | 中（patchright 已处理） |

**结论**：
- 老 `positionAjax` 端点仍可用 httpx + Cookie 提取走通
- 新 `gate.lagou.com` 端点全量切 `X-Lagou-Token`，需**浏览器执行 JS** 才能拿 token，必须走 BrowserSession 通道

## 4. 响应结构

**统一包络**：
```json
{
  "code": 0,
  "message": "success",
  "content": { ... },
  "success": true,
  "resubmitToken": "..."
}
```

- `code == 0` 表示成功
- `code == 3` 常见"请求频繁"
- `code == -1` 参数错误
- `code == 10001` 未登录
- `code == 10002` Token 失效

**与 BOSS 直聘差异**：
- BOSS 用 `zpData` 作为数据外层 key，拉勾用 `content`
- BOSS 错误用 `code=36` 表示风控，拉勾用 `code=3` 表示频控，**无独立风控码**
- 拉勾的 `resubmitToken` 用于**写操作幂等**（投递、打招呼），BOSS 无此机制

## 5. CDP / Browser 通道可行性

| 维度 | 可行性 |
|------|-------|
| 复用 `BrowserSession` 的 CDP 模式 | ✅ 可直接用，Chrome 正常打开 lagou.com 即可 |
| 复用 Bridge 通道（Chrome 扩展注入） | ✅ 可直接用，权限 `*://*.lagou.com/*` |
| 复用 patchright headless | ⚠️ 可用但 4 小时内必触发滑块（老经验） |
| 复用 `browser_cookie3` Cookie 提取 | ✅ 可用，Cookie 字段名和域都标准 |

**结论**：如果接入，**Bridge 通道应为首选**，CDP 作为降级，patchright 不推荐。

## 6. 风控触发阈值

| 行为 | 阈值 | 触发后表现 |
|------|------|-----------|
| 连续搜索 | 约 30 次/15min | 返回 `code=3`，需等 15min 冷却 |
| 打招呼 | 约 50 次/天/账号 | 返回 `code=3` + "今日沟通次数已达上限" |
| 投递 | 约 50 次/天/账号 | 同上 |
| 新账号首日 | 严格 | 所有接口限流至约 10 次/小时 |
| IP 异常 | 同 IP 多账号 | 触发全站滑块，需人工验证 |

**对比 BOSS 直聘**：拉勾的频控阈值大致**严过 BOSS 30%-50%**。

## 7. 与 BOSS 直聘的协议差异矩阵

| 维度 | BOSS 直聘 | 拉勾 | 抽象层成本 |
|------|----------|------|----------|
| 数据包络 key | `zpData` | `content` | 低：Platform 接口统一映射 |
| 错误码含义 | `code=36` 风控 / `code=37` stoken 过期 / `code=9` 限流 | `code=3` 限流 / `code=10002` token 失效 | 中：错误映射表 |
| 登录 token | `__zp_stoken__` 动态 | `X-Lagou-Token` JWT | 高：两套不同流程 |
| 写操作幂等 | 无 | `resubmitToken` | 中：需新增字段 |
| 职位 ID 格式 | `encryptJobId`（加密） | `positionId`（明文数字） | 低：统一用 `job_id` 抽象 |
| 城市编码 | 数字 code 映射 | 字符串名称直接用 | 低：统一适配 |
| 薪资字段 | `salaryDesc`（"20-40K"） | `salary`（"20k-40k"） | 低 |
| 沟通域名 | 统一 `zhipin.com` | 拆到 `easy.lagou.com` | 中：需支持多域名 Cookie |

## 结论与建议

### 建议：**v2.0 不接入拉勾**

**理由**：
1. **信噪比低**：拉勾 HR 在线率和职位更新频率均低于 BOSS 直聘，同样的打招呼动作 ROI 更低（据公开 2025 行业观察）
2. **维护成本高**：`X-Lagou-Token` JWT 密钥每次发版可能变，会成为长期维护债
3. **用户基数错位**：拉勾用户更偏互联网/开发岗，而本 CLI 的 AI Agent 应用场景覆盖更广
4. **开发成本估算**：Platform 抽象层 + 拉勾适配器实现 + 测试 ≈ **3-4 周工作量**

### 替代方案

- **优先做智联 / 猎聘调研**：用户基数更广（非互联网岗位覆盖）
- **保持 BOSS 单平台深度**：把资源投入到 BOSS 体验提升（更准推荐、更强 follow-up）
- **用户需要拉勾时走 Web UI**：做浏览器扩展深度集成（ROADMAP 另一条项）让用户在原生页面用

### 如果未来要接入（保留路径）

1. 先做 Platform 接口抽象（把 BOSS 降为 `BossPlatform` 实现）
2. 抽象层需支持：**多域名 Cookie**、**写操作幂等 token**、**JWT token 刷新**
3. 拉勾适配器**强依赖 Bridge 通道**（扩展注入），放弃 patchright 兜底
4. 首次接入时建议先支持**只读能力**（search / detail），写操作（greet / apply）留后期

## 参考资料（均为公开信息）

- [Lagou.com robots.txt](https://www.lagou.com/robots.txt)
- Lagou 官方 Chrome 扩展 manifest（Chrome Web Store 公开可下载）
- 浏览器 DevTools Network panel 观察（2026-04-20）
- BOSS 直聘对比：`src/boss_agent_cli/api/client.py` + `api/boss.yaml`

---

> 本报告仅作 **可行性评估**。实际实现需额外考虑用户协议、数据合规、平台反爬动态等因素。Platform 接口实现 PR 不在本调研范围。
