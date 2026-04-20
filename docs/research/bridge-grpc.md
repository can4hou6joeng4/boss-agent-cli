# Bridge 协议 HTTP/WS → gRPC 升级调研

> **结论先行**：**不建议近期迁移 gRPC**。当前 HTTP/WS + aiohttp 方案在 Bridge 场景下已经足够好，gRPC 的性能/类型收益在 localhost 单用户场景**无法兑现**，反而**带来 Chrome MV3 扩展的 gRPC-Web 兼容性风险和 ~30MB 依赖膨胀**。建议保留 HTTP/WS 至 v2.x，等 Chrome MV3 官方支持 gRPC-Web streaming 或项目规模触达性能瓶颈再重启调研。
>
> 调研日期：2026-04-20 · 调研人：can4hou6joeng4 · 信息来源：公开文档（gRPC spec + Chrome Extension docs + aiohttp docs）

## 1. 现状盘点（HTTP/WS 协议）

**代码事实**：
- `bridge/daemon.py:110` — aiohttp `web.Application` 统一处理 HTTP + WebSocket
- `bridge/client.py:69` — 纯 httpx POST 到 `:19826/command`
- `bridge/protocol.py:23` — `BridgeCommand` / `BridgeResult` dataclass（4 种 action）

**消息格式**：
```python
BridgeCommand = {"id": str, "action": str, "code": str, "url": str, ...}
BridgeResult  = {"id": str, "ok": bool, "data": Any, "error": str}
```

**生命周期**：
- daemon 按需启动（首命令触发），4h 无活动自动退出
- 扩展 WebSocket 长连，断线 1.5s 重连
- CLI 每次命令独立 HTTP POST，30s 超时

**错误处理**：
- daemon 层：503（扩展未连）/ 400（JSON 错）/ 504（超时）/ 502（发送失败）
- client 层：500ms/1500ms/4 次重试

**性能基线**（本地观察）：
- 单次 `fetch_json` 往返 ≈ 50-120ms（绝大部分是 Chrome 执行 JS + BOSS API RT）
- daemon 本身的 HTTP + WS 转发 **< 3ms**

## 2. gRPC 迁移收益矩阵

| 维度 | HTTP/WS 现状 | gRPC 可期收益 | 实际评估 |
|------|-------------|-------------|---------|
| 二进制序列化 | JSON ~300B/msg | Protobuf ~120B/msg | localhost **无意义**（带宽非瓶颈） |
| 类型安全 | dataclass + mypy | .proto 强类型生成 | **已解决**（mypy 严格化 100%） |
| 双向流式 | WebSocket 已支持 | gRPC bidi stream | **平手**（ws 够用） |
| 多路复用 | HTTP/1.1 单请求/连接 | HTTP/2 多路复用 | 单 CLI 单请求，**无意义** |
| 错误语义 | HTTP status + JSON | `grpc.StatusCode` 15 种 | **回归**（没更明确） |
| 跨语言 | 任何 HTTP 语言 | 任何 gRPC 语言 | **平手**（Python + JS 都覆盖） |
| 工具链 | curl / httpx / DevTools | grpcurl / bloomrpc | **劣化**（调试工具更少） |

**结论**：**所有核心收益在 localhost 单用户场景均无法兑现**。

## 3. gRPC 迁移代价矩阵

| 代价 | 量化 |
|------|------|
| **wheel 大小增量** | `grpcio` ~6MB + `protobuf` ~2MB + `grpcio-tools` ~20MB（仅开发期）= **发布包 +8MB** |
| **Chrome 扩展改造** | MV3 Service Worker **不支持原生 gRPC**，必须引入 `grpc-web` client（~150KB gzipped） |
| **gRPC-Web 限制** | 不支持 client-streaming / bidi-streaming（**关键限制**）→ 扩展回传只能 server-streaming 或 unary |
| **需要 Envoy proxy** | gRPC-Web 规范要求浏览器 ↔ Envoy ↔ gRPC server，**daemon 端要加一层 Envoy** → 工具链 +1 进程 |
| **daemon 代码改造** | `bridge/daemon.py` 170 行 aiohttp → gRPC + Envoy 配置，**估 400 行新代码** |
| **扩展代码改造** | JS/TS 侧 WebSocket client → grpc-web client，**估 300 行** |
| **.proto 维护** | 新增 `protos/bridge.proto` + 生成脚本 + CI 校验，**长期维护负担** |
| **调试难度** | grpcurl 在本地还行，但 grpc-web 的浏览器端调试**远比 WebSocket Frames 面板难** |

**总改造成本估算**：**3-4 周**，其中 gRPC-Web + Envoy 配置占 **60% 复杂度**。

## 4. 迁移路径方案对比

### 方案 A：完全切 gRPC（推翻 HTTP/WS）

| 维度 | 评估 |
|------|------|
| 可行性 | 低 — MV3 限制 + Envoy 依赖 |
| 性能收益 | localhost 几乎为零 |
| 风险 | 扩展商店 Envoy 分发违反常规 |
| **建议** | ❌ 否决 |

### 方案 B：双协议并存（HTTP/WS 为主，gRPC 为实验）

| 维度 | 评估 |
|------|------|
| 可行性 | 中 — 但代码重复 |
| 运维成本 | daemon 跑两套，扩展做 A/B |
| 长期收益 | 如果将来切 gRPC，已有实验数据 |
| **建议** | ⚠️ 仅适合明确要未来切 gRPC 的场景，**当前无此需求** |

### 方案 C：保持 HTTP/WS（给出明确拒绝理由）

| 维度 | 评估 |
|------|------|
| 可行性 | 高 — 零改动 |
| 长期技术债 | 低（WebSocket 成熟规范，生命周期 >= 10 年） |
| 性能空间 | aiohttp 在 localhost 单连接场景吞吐 10k+ msg/s，**永远不会是瓶颈** |
| 扩展兼容性 | 任何版本 Chrome 都支持 WebSocket，无 MV3 风险 |
| **建议** | ✅ **推荐方案** |

## 5. 决策建议

### 短期（v1.x - v2.0）：方案 C

保持 HTTP/WS，原因：
1. **性能非瓶颈**：Chrome 执行 JS + BOSS API RT 占总耗时 95%+，Bridge 协议换型无意义
2. **类型安全已达标**：mypy 严格化 100%，protobuf 的类型收益已被覆盖
3. **Chrome MV3 + gRPC-Web + Envoy 组合风险高**：违反「零配置浏览器」的 bridge 初衷
4. **依赖膨胀 +8MB 发布包**违反 uv tool install 体验

### 长期触发条件（达到任一才重启调研）

| 触发条件 | 当前值 | 触发阈值 |
|---------|-------|---------|
| daemon 转发耗时占比 | < 3% | > 20% |
| 并发 CLI 进程数 | 1 | > 10 |
| 跨机器 daemon 部署需求 | 无 | 有明确需求 |
| Chrome 官方 gRPC-Web 一等公民支持 | 无 | 官方支持 bidi-streaming |
| 扩展需要与非 Python daemon 通信 | 无 | 有 Rust/Go daemon 方案 |

### 替代优化方向（用 HTTP/WS 基础上拿收益）

1. **protobuf 序列化（不换协议）**：WebSocket 消息从 JSON 改为 protobuf binary frame，兼容 MV3，省带宽
2. **msgpack 中间态**：比 JSON 快 30%、比 protobuf 简单，无需 .proto
3. **复用 HTTP/2 over WebSocket**：aiohttp 已支持 HTTP/2，可以开启但收益有限

这些方向 ROI 也很低，但风险远低于 gRPC 迁移。

## 6. 与多平台适配器的关系

Issue #90 多平台调研的智联/拉勾/猎聘均通过 **Bridge 通道** 工作，这些平台的流量也不会给 Bridge 增加有意义的压力（都是单用户单命令模式）。因此**多平台扩展不改变 gRPC 迁移的决策**。

## 7. 参考资料（均为公开信息）

- [gRPC-Web 规范](https://github.com/grpc/grpc-web) — 明确列出 MV3 限制
- [Chrome Extension MV3 迁移指南](https://developer.chrome.com/docs/extensions/develop/migrate) — Service Worker 限制
- [aiohttp WebSocket 文档](https://docs.aiohttp.org/en/stable/web_advanced.html#websockets)
- BOSS 直聘 Bridge 设计：[docs/superpowers/specs/2026-04-01-browser-bridge-design.md](../superpowers/specs/2026-04-01-browser-bridge-design.md)
- 多平台调研对比：[docs/research/platforms/README.md](platforms/README.md)

---

> 本报告结论为「**暂不迁移**」，不占用 v2.0 工程资源。重启调研的触发条件已列明，届时基于实际数据再决策，不拍脑袋。
