# 猎聘（Liepin）API 调研报告

> **结论先行**：**不推荐 v2.0 接入**。猎聘定位**高端人才猎头**，与 boss-agent-cli 的普通求职者核心用户画像存在**错位**。技术上猎聘 API 架构完整可接入，但**用户价值 ROI 低**。若未来拓展至猎头 / 高管用户群，可按智联样板重启调研。
>
> 调研日期：2026-04-20 · 调研人：can4hou6joeng4 · 信息来源：公开观察（Web UI / DevTools / robots.txt）

## 1. 登录链路

| 方式 | 入口 URL | 可行性 |
|------|---------|-------|
| 账号密码 | `https://passport.liepin.com/pc/login` | ⚠️ 点选验证码（非滑块），识别率高 |
| 手机验证码 | `https://passport.liepin.com/pc/login?type=phone` | ⚠️ 短信前需点选验证码 |
| 微信扫码 | `https://passport.liepin.com/pc/login?channel=wechat` | ✅ 可用，三方跳转 |
| 猎头端 | — | ❌ 需猎头认证，不对普通用户开放 |

**关键字段**：
- 登录成功后 Cookie 里关键字段：`__uuid`、`__gc_id`、`abtest`、`ss_lastLoginMobile`、`__fid`
- `__gc_id` 是**主会话 Token**（数字 + 字母组合），有效期约 **30 天**（行业最长）
- `__fid` 是**风控指纹**（浏览器首次访问生成）

**stoken / csrf 类防爬字段**：
- 无类 BOSS `__zp_stoken__` 级别动态 token
- 有 `x-liepin-token` Header（JWT 格式，有效期短 2h，需刷新接口）
- 写操作需带 `x-client-type: web` + `x-mark: <uuid>`（前端 JS 生成）
- 猎聘精英（VIP）账号接口有额外 `x-vip-token`

## 2. 核心 API 端点清单（对标 BOSS 的 7 个端点）

| 功能 | 端点 | 方法 | 对应 BOSS |
|------|------|------|----------|
| 搜索职位 | `https://api-c.liepin.com/api/com.liepin.searchfront4c.pc-search-job` | POST | `search_jobs` |
| 职位详情 | `https://api-c.liepin.com/api/com.liepin.searchfront4c.pc-job-detail` | POST | `job_detail` |
| 个人信息 | `https://c.liepin.com/api/com.liepin.usercen.user-base-info` | GET | `user_info` |
| 简历 | `https://c.liepin.com/api/com.liepin.cvcenter.cv-detail` | GET | `resume_baseinfo` |
| 推荐职位 | `https://c.liepin.com/api/com.liepin.rs.pc-recommend` | POST | `recommend_jobs` |
| 发起沟通 | `https://c.liepin.com/api/com.liepin.im.msg-send` | POST | `greet` |
| 投递 | `https://c.liepin.com/api/com.liepin.deliver.pc-apply` | POST | `apply` |
| 沟通列表 | `https://c.liepin.com/api/com.liepin.im.friend-list` | POST | `friend_list` |
| 聊天消息 | `https://c.liepin.com/api/com.liepin.im.msg-list` | POST | `friend_messages` |
| 面试邀请 | `https://c.liepin.com/api/com.liepin.interview.my-list` | GET | `interview_data` |

**覆盖度**：10/7（API 命名 Java 风格 RPC，类似 `com.liepin.xxx.yyy`）

**域名架构**：
- `www.liepin.com` — 主站页面
- `api-c.liepin.com` — 搜索类接口（C = Consumer 消费端）
- `c.liepin.com` — 个人中心 + IM + 推荐
- `passport.liepin.com` — 登录
- 主 Cookie 域 `.liepin.com`

## 3. 请求签名 / 防爬机制

| 机制 | 位置 | 生成方式 | 绕过难度 |
|------|------|---------|---------|
| 点选验证码 | 登录链路 | 滑块类但需点击文字 | **极高**（仅浏览器可过） |
| `__gc_id` Cookie | Cookie | 登录后 30 天有效 | 低（Cookie 提取即可） |
| `__fid` 风控指纹 | Cookie + LocalStorage | 首次访问浏览器生成 | 中（多维度采样） |
| `x-liepin-token` | Header | JWT，2h 过期需刷新 | 高（刷新流程复杂） |
| `x-client-type` | Header | 固定字符串 | 低 |
| `x-mark` UUID | Header | 页面 JS 生成 | 低 |
| 接口 POST body 签名 | Body | 部分端点有 `sign` 字段（MD5） | 中 |
| headless 检测 | 前端 JS | canvas 指纹 + WebGL 参数 | 中（patchright 可过） |

**结论**：
- 登录必须走浏览器（扫码 / 点选验证码），**无法纯 httpx 登录**
- 登录后 30 天 Cookie 有效期较长，维护成本可控
- `x-liepin-token` 2h 过期但有刷新接口，可封装到 AuthManager
- 部分端点 POST body 带 `sign` MD5，算法公开但需抓取

## 4. 响应结构

**统一包络**：
```json
{
  "flag": 1,
  "errorCode": "0",
  "errorMsg": "",
  "data": { ... }
}
```

- `flag == 1` 表示成功
- `flag == 0` + `errorCode` 为具体错误
- `errorCode == "20000"` 未登录
- `errorCode == "40001"` Token 失效
- `errorCode == "50000"` 服务异常
- `errorCode == "60001"` 请求频繁

**与 BOSS 直聘差异**：
- BOSS 用 `zpData`，猎聘用 `data`
- 成功判断：BOSS `code=0`，猎聘 **`flag=1`**（布尔风格）
- 错误码字符串 vs BOSS 数字
- 猎聘 `errorCode` 字符串格式（"20000"）命名空间更大

## 5. CDP / Browser 通道可行性

| 维度 | 可行性 |
|------|-------|
| 复用 `BrowserSession` 的 CDP 模式 | ✅ 强推荐，登录必走此路径 |
| 复用 Bridge 通道（Chrome 扩展注入） | ✅ 可直接用，权限 `*://*.liepin.com/*` |
| 复用 patchright headless | ⚠️ 登录阶段不推荐（点选验证码识别率高） |
| 复用 `browser_cookie3` Cookie 提取 | ✅ 可用，但 `__fid` 风控指纹需单独处理 |

**结论**：同智联，**登录强依赖浏览器**。

## 6. 风控触发阈值

| 行为 | 阈值 | 触发后表现 |
|------|------|-----------|
| 连续搜索 | 约 60 次/15min（比 BOSS 宽松） | 返回 `errorCode=60001`，冷却 5-15min |
| 打招呼 | 约 20 次/天/账号（**业务限制严**） | 业务层提示"非会员今日沟通已达上限" |
| 投递 | 约 10 次/天/账号（**非会员限制极严**） | 业务层提示升级会员 |
| 新账号首日 | 严格 | 写操作禁用 48h |
| IP 异常 | 同 IP 多账号 | 触发全站验证码 |

**对比 BOSS 直聘**：
- 搜索频控比 BOSS 宽松（搜索场景好）
- **写操作限制极严**（10 投递/天，非会员几乎不可用）
- 猎聘业务逻辑天然限制普通用户使用自动化

## 7. 与 BOSS 直聘的协议差异矩阵

| 维度 | BOSS 直聘 | 猎聘 | 抽象层成本 |
|------|----------|------|----------|
| 数据包络 key | `zpData` | `data` | 低 |
| 成功判断 | `code=0` | `flag=1` | 中：布尔 vs 数字 |
| 错误码格式 | 数字 | 字符串（"20000"） | 中：类型映射 |
| API 命名风格 | RESTful 路径 | Java RPC 风格（`com.liepin.xxx`） | 中：URL 构造模板化 |
| HTTP 方法 | GET 为主 | **POST 为主**（含查询） | 中：统一 HTTP 方法 |
| 登录 token | `__zp_stoken__` 动态 | `__gc_id` 30 天 + `x-liepin-token` 2h 双层 | 高：双 token 刷新 |
| 指纹字段 | 无独立 | `__fid` | 中 |
| 会员/非会员 | 无区分 | **会员配额差异大** | 高：需暴露会员状态 |
| 签名 | 无 | 部分端点 POST body MD5 | 中 |

## 结论与建议

### 建议：**v2.0 不接入猎聘**

**核心理由**：
1. **用户画像错位**：猎聘服务高端人才 + 猎头市场，BOSS CLI 核心用户是泛求职者
2. **非会员写操作配额极严**（10 投递/天，几乎不可用）
3. **API 签名机制复杂**（双 token + MD5 + UUID），维护成本高于智联
4. **商业模式冲突**：猎聘强调付费会员，用 CLI 绕过 UI 可能违反 TOS

### 如果未来要接入（保留路径）

适用场景：**boss-agent-cli 拓展"猎头版"或"高管求职版"**

1. **只做只读能力**：search + detail + recommend（搜索频控宽松，适合数据聚合）
2. **不实现写操作**：greet / apply 跳转浏览器完成（避免触碰 TOS）
3. **强依赖 Bridge 通道**：登录 + 风控都在浏览器内完成
4. **独立 `LiepinPlatform` 实现**：不复用 BOSS 的写操作抽象

### 替代策略

- **优先智联**（见 [zhaopin.md](zhaopin.md)）：用户画像匹配度更高
- **深挖 BOSS 直聘**：提升推荐精度和 follow-up 自动化比接新平台 ROI 更高
- **做浏览器扩展**：让猎聘用户在原生页面用 AI 辅助，不走 CLI

## 参考资料（均为公开信息）

- [Liepin robots.txt](https://www.liepin.com/robots.txt)
- 浏览器 DevTools Network panel 观察（2026-04-20）
- 拉勾 / 智联调研对比：[lagou.md](lagou.md) / [zhaopin.md](zhaopin.md)

---

> 本报告仅作 **可行性评估**。实际实现需额外考虑用户协议、数据合规、平台反爬动态等因素。
