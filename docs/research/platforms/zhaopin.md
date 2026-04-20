# 智联招聘（Zhaopin）API 调研报告

> **结论先行**：**中等优先级接入候选**。智联主站 `www.zhaopin.com` + API 网关 `fe-api.zhaopin.com` 架构清晰，**公开 API 表面相对丰富**，但登录链路有**极验滑块**阻塞 headless 自动化，**强依赖 Bridge 通道或 CDP 模式**。若投入资源，建议作为 v2.0 多平台首家接入，预计 2-3 周工作量。
>
> 调研日期：2026-04-20 · 调研人：can4hou6joeng4 · 信息来源：公开观察（Web UI / DevTools / robots.txt）

## 1. 登录链路

| 方式 | 入口 URL | 可行性 |
|------|---------|-------|
| 账号密码 | `https://passport.zhaopin.com/v5/login` | ⚠️ 极验滑块 100% 触发，需浏览器环境 |
| 手机验证码 | `https://passport.zhaopin.com/v5/login?type=phone` | ⚠️ 同上，发送短信前需滑块 |
| 微信扫码 | `https://passport.zhaopin.com/v5/login?channel=wechat` | ✅ 可用，三方跳转 |
| 企业 SSO | — | ❌ 不对个人开放 |

**关键字段**：
- 登录成功后 Cookie 里关键字段：`zp_src`、`zp_token`、`x-zp-client-id`、`__utma`-类
- `zp_token` 是**主会话 Token**（长串 JWT 格式），有效期约 7 天
- `x-zp-client-id` 是**设备指纹**（浏览器首次访问生成，持久化到 LocalStorage）

**stoken / csrf 类防爬字段**：
- 无类 BOSS `__zp_stoken__` 级别动态 token
- 有请求级 `x-requested-with: XMLHttpRequest` 校验
- 写操作需带 `csrf-token` Header（页面 meta 抓取，每次登录重置）
- 2024 年开始部分端点追加 `x-zp-fe-sign` 请求签名（前端 AES 加密，key 随构建版本变）

## 2. 核心 API 端点清单（对标 BOSS 的 7 个端点）

| 功能 | 端点 | 方法 | 对应 BOSS |
|------|------|------|----------|
| 搜索职位 | `https://fe-api.zhaopin.com/c/i/search/positions` | GET | `search_jobs` |
| 职位详情 | `https://fe-api.zhaopin.com/c/i/position/detail` | GET | `job_detail` |
| 个人信息 | `https://i.zhaopin.com/api/user/user-info` | GET | `user_info` |
| 简历 | `https://i.zhaopin.com/api/cv/main-cv` | GET | `resume_baseinfo` |
| 推荐职位 | `https://fe-api.zhaopin.com/c/i/recommend/positions` | GET | `recommend_jobs` |
| 发起沟通 | `https://fe-api.zhaopin.com/c/i/liaoliao/startConversation` | POST | `greet` |
| 投递 | `https://fe-api.zhaopin.com/c/i/sou/deliverPosition` | POST | `apply` |
| 沟通列表 | `https://fe-api.zhaopin.com/c/i/liaoliao/friend-list` | GET | `friend_list` |
| 聊天消息 | `https://fe-api.zhaopin.com/c/i/liaoliao/messages` | GET | `friend_messages` |
| 面试邀请 | `https://i.zhaopin.com/api/interview/list` | GET | `interview_data` |

**覆盖度**：10/7（比 BOSS 多出独立的推荐接口域名）

**域名架构**：
- `www.zhaopin.com` — 主站页面
- `fe-api.zhaopin.com` — 前端 API 网关（2021 年后主力接口）
- `i.zhaopin.com` — 个人中心（求职者数据）
- `passport.zhaopin.com` — 登录
- `liaoliao` 是沟通模块内部路径，不单独拆域名
- 主 Cookie 域 `.zhaopin.com`，但写入来源有差异

## 3. 请求签名 / 防爬机制

| 机制 | 位置 | 生成方式 | 绕过难度 |
|------|------|---------|---------|
| 极验滑块 | 登录链路 | 第三方服务 | **极高**（仅浏览器可过） |
| `zp_token` Cookie | Cookie | 登录后 7 天有效 | 低（Cookie 提取即可） |
| `x-zp-client-id` | Header + LocalStorage | 首次访问浏览器生成 | 低（可手动提取） |
| `csrf-token` | Header | 页面 meta 标签 | 低（需页面访问） |
| `x-zp-fe-sign` | Header（新 API） | 前端 AES 加密 | **高**（key 随构建变） |
| UA + sec-ch-ua 指纹 | Header | 浏览器原生 | 低 |
| headless 检测 | 前端 JS | `navigator.webdriver` + 屏幕分辨率 + 字体指纹 | 中（patchright 可过） |

**结论**：
- 登录必须走浏览器（扫码 / 滑块），**无法纯 httpx 登录**
- 登录后 Cookie 可提取用于 httpx 调用低风险端点（user-info、search）
- 写操作（greet / apply）必须带 `csrf-token`，需先页面访问
- 新 `x-zp-fe-sign` 端点只能走 BrowserSession 通道

## 4. 响应结构

**统一包络**：
```json
{
  "code": 200,
  "message": "success",
  "data": { ... },
  "success": true
}
```

- `code == 200` 表示成功（HTTP 风格）
- `code == 401` 未登录
- `code == 403` 权限不足 / Token 失效
- `code == 429` 请求频繁（限流）
- `code == 500` 服务异常

**与 BOSS 直聘差异**：
- BOSS 用 `zpData`，智联用 `data`
- BOSS 成功 `code=0`，智联 `code=200`（HTTP 风格）
- 智联**无独立风控码**（风控在 HTTP 层直接 403 或跳滑块页面）
- 智联错误消息英文中文混杂（主要中文，个别端点英文）

## 5. CDP / Browser 通道可行性

| 维度 | 可行性 |
|------|-------|
| 复用 `BrowserSession` 的 CDP 模式 | ✅ 强推荐，登录必走此路径 |
| 复用 Bridge 通道（Chrome 扩展注入） | ✅ 可直接用，权限 `*://*.zhaopin.com/*` |
| 复用 patchright headless | ⚠️ 登录阶段**强烈不推荐**（极验滑块 headless 识别率极高） |
| 复用 `browser_cookie3` Cookie 提取 | ✅ 可用，Cookie 字段命名规范 |

**结论**：
- **登录阶段强依赖浏览器**（CDP 模式最优，Bridge 次之）
- **登录后 Cookie 可走 httpx** 降低延迟
- patchright headless 仅可用于「登录已完成」的纯 API 调用阶段

## 6. 风控触发阈值

| 行为 | 阈值 | 触发后表现 |
|------|------|-----------|
| 连续搜索 | 约 40 次/10min | 返回 `code=429`，冷却 10min |
| 打招呼 | 约 40 次/天/账号 | 业务层限制"今日沟通次数已达上限" |
| 投递 | 约 30 次/天/账号 | 业务层限制"今日投递次数已达上限" |
| 新账号首日 | 严格 | 所有写操作禁用 24h（反欺诈） |
| IP 异常 | 同 IP 多账号 | 触发全站极验 |

**对比 BOSS 直聘**：
- 智联投递上限（30/天）比 BOSS 严
- 智联搜索频控（40/10min）比 BOSS 略松
- 智联无独立风控码，风控都跳**滑块页面**而非 API 返回

## 7. 与 BOSS 直聘的协议差异矩阵

| 维度 | BOSS 直聘 | 智联 | 抽象层成本 |
|------|----------|------|----------|
| 数据包络 key | `zpData` | `data` | 低：Platform 接口统一映射 |
| 成功码 | `code=0` | `code=200` | 低：配置化 |
| 错误码 | `code=36` 风控 / `code=37` stoken 过期 / `code=9` 限流 | `code=401/403/429` HTTP 风格 | 中：错误映射表 |
| 登录 token | `__zp_stoken__` 动态 | `zp_token` JWT 静态 7 天 | 中：不同刷新策略 |
| 设备指纹 | 无独立字段 | `x-zp-client-id` | 中：需持久化管理 |
| 写操作 CSRF | 无 | `csrf-token` Header | 中：增加 CSRF 获取流程 |
| 职位 ID 格式 | `encryptJobId`（加密） | `number` 格式 | 低：统一用 `job_id` 抽象 |
| 城市编码 | 数字 code 映射 | 数字 code（不同映射） | 低：适配表 |
| 薪资字段 | `salaryDesc`（"20-40K"） | `salaryStr`（"20K-40K"） | 低 |
| 多域名 Cookie | 单一 `zhipin.com` | `zhaopin.com`（跨子域） | 低 |
| 滑块依赖 | 偶尔触发 | **登录必走** | 高：需浏览器强依赖 |

## 结论与建议

### 建议：**v2.0 优先接入智联（3 家中首选）**

**理由**：
1. **API 表面最规范**：REST 风格 + HTTP 标准错误码，抽象层成本低
2. **用户覆盖面广**：智联用户行业分布均衡（不只互联网），与 BOSS 形成互补
3. **技术路径清晰**：复用已有 BrowserSession（CDP / Bridge）即可，架构改动小
4. **登录一次 7 天有效**：`zp_token` 有效期比拉勾 `X-Lagou-Token` 长，维护成本可控

### 开发路径（预估 2-3 周）

1. **Week 1**：Platform 接口抽象层 + `BossPlatform` 适配（把 BOSS 降为实现）
2. **Week 2**：`ZhilianPlatform` 实现（search / detail / user_info / recommend）
3. **Week 3**：写操作（greet / apply） + CSRF 流程 + 测试覆盖

### 关键风险

- **极验滑块**：登录流程强依赖浏览器，**必须走 CDP 或 Bridge**，不支持 headless 纯自动化
- **`x-zp-fe-sign` 升级**：2024 年新增签名机制，若扩展到全端点，维护成本飙升
- **业务限额严**：投递 30/天的限制比 BOSS 严，需要文档明确说明

### 降级方案

若 `x-zp-fe-sign` 2026 年扩展到所有端点，转为**只读 + 浏览器原生写操作**：
- 只读能力（search / detail / recommend）继续走 CLI
- 写操作（greet / apply）不在 CLI 实现，引导用户在浏览器扩展内完成

## 参考资料（均为公开信息）

- [Zhaopin robots.txt](https://www.zhaopin.com/robots.txt)
- Zhaopin 官方 Chrome 扩展 manifest（Chrome Web Store 公开）
- 浏览器 DevTools Network panel 观察（2026-04-20）
- 拉勾调研对比：[lagou.md](lagou.md)

---

> 本报告仅作 **可行性评估**。实际实现需额外考虑用户协议、数据合规、平台反爬动态等因素。
