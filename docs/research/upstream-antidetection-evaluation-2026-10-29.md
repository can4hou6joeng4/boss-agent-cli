# 上游反检测仓库评估：ruyijs 与 Safarier（2026-10-29）

结论：**采纳设计逻辑，不集成代码，不引入依赖。** 本文记录评估依据与重启条件，
避免后来者重复论证。

## 背景

Issue #485 报告 CDP 注入请求后账号被封，换号后未发请求也被封。讨论中出现两个第三方仓库，
都声称能让自动化流量"看起来像真实浏览器"。本文只回答：它们能否、以及以什么形式进入本项目。

## ruyijs

| 项 | 事实 |
|---|---|
| 形态 | Windows x64 原生运行时（SpiderMonkey + Necko + NSS），Python 绑定 |
| 能力 | 真实 Firefox TLS（可切 Firefox 100–159）、真实 DOM/Canvas、可跑页面签名器 |
| 自述覆盖 | Akamai Bot Manager 3.x、DataDome、CreepJS；站点页另把 BOSS 直聘列为「签名 token」 |
| 平台 | 仅 Windows x64；macOS/Linux「not yet」 |
| 分发 | PyPI 仅 `win_amd64` wheel（约 43 MB）；仓库 2026-10-28 建，单作者 |
| 许可 | Source Available v1.0（非 OSI）：商用需书面授权，分发修改必须开源 |

不集成的原因（按约束力排序）：

1. **无法验证**。本项目 CI 为 `ubuntu-latest`，容器镜像为 Linux，开发机为 macOS。
   该依赖在任何门禁环境里都装不上，接入后得到的是无法执行、无法回归的代码。
2. **结构上覆盖不了登录**。它无窗口、无渲染，`headless=False` 的扫码登录与 PDF 导出都用不了。
3. **许可与 MIT 冲突**。非商用条款会让下游商用用户失去授权基础。
4. **不在本轮假设的收益路径上**。它改的是请求形状；而 #485 里最有诊断价值的一条
   （换号后零请求即封）指向账号/设备关联，客户端换引擎无解。

## Safarier

| 项 | 事实 |
|---|---|
| 形态 | macOS 原生 Swift 应用（约 1.4 万行，`Sources/` 可读），Releases 分发 `.app` |
| 手法 | 真 WebKit + **私有 SPI** 覆盖引擎层（`NSScreen`、`devicePixelRatio`、WebRTC、高级隐私保护），符号名 XOR 混淆后运行时解析 |
| 隔离 | 每 profile 独立进程与 `WKWebsiteDataStore`，配 sing-box 出站，断线不回落真实 IP |
| 代理联动 | `ProxyGeoResolver`：按出口 IP 国家/城市匹配 language 与 timezone |
| 许可 | **仓库无 LICENSE 文件**（默认保留所有权利） |
| 其他 | README 要求 `xattr -dr com.apple.quarantine` 后再运行 |

不集成的原因：

1. **无许可**：无法把代码并入 MIT 仓库。
2. **不可放入凭据路径**：需要绕过 Gatekeeper、用混淆符号 patch 系统框架、且无许可，
   却要持有用户的登录态与简历。
3. **平台限制**：macOS 专用 GUI 应用，`ubuntu-latest` 与容器都进不去。
4. **技术不可移植**：可复制的部分是 `import WebKit` 的 Swift；对 Chromium 没有接收者。

## 实际采纳的设计逻辑

以下四项与引擎无关，全部可在 Chromium 公开 API 上实现，已或将要落地：

| 逻辑 | 来源机制 | 本项目落地 |
|---|---|---|
| 出口一致：登录、刷新、只读共用同一个出站 | Safarier 每环境独立代理 + 零泄漏 | `api/proxy.py` 的 `resolve_proxy()`，同时供 httpx 与 Chromium 使用 |
| 禁非代理 UDP 泄漏 | Safarier 用 SPI 关 WebRTC | `WEBRTC_LEAK_GUARD_ARGS`（Chromium 公开启动参数） |
| 会话内客户端身份唯一 | 两者的「真引擎、零注入」 | `auth/page_identity.py` 采集并回放页面自己的 `token` / `zp_token`，见 [请求身份一致性](zhipin-request-identity-2026-10-29.md) |
| 能力如实上报 | Safarier 的 `PrivacyCapabilityReport` | 沿用既有 `NOT_SUPPORTED` 与 health 语义 |

## 重启条件

满足任一条时可重新评估集成，而不是重新论证：

- ruyijs 发布 macOS/Linux wheel，且许可证允许 MIT 项目间接分发与商业使用；
- Safarier 补上明确许可证，且提供不依赖私有 SPI 的公开构建；
- 出现证据表明平台风控的决定性信号确实落在 TLS/引擎指纹层（而非账号、设备或出口关联）。

## 边界

本项目不提供反检测能力，也不承诺任何"规避封号"的效果。上述改动只做一件事：
让同一个会话内不同通道对外呈现的身份彼此一致。
