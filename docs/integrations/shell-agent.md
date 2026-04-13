# Shell Agent Integration Example

适用版本：`boss-agent-cli` 当前 CLI 契约（2026-04-13）

## 适用场景

- 你的 Agent 框架只需要一个 shell/subprocess tool
- 你希望把 `boss` 作为可复用外部命令挂到已有编排器
- 你需要最通用、最少依赖的接入方式

## 最小接入流程

核心原则：让宿主只负责“运行命令 + 解析 JSON”，不要自己重写 BOSS 直聘协议。

推荐包装流程：

```text
1. 运行 boss schema
2. 运行 boss status
3. 未登录时运行 boss login
4. 运行 boss search
5. 运行 boss detail
6. 满足条件时运行 boss greet
```

最小命令链路：

```bash
boss schema
boss status
boss search "Golang" --city 广州 --welfare "双休,五险一金"
boss detail <security_id>
boss greet <security_id> <job_id>
```

如果宿主支持单函数包装，建议包装成：

```python
def run_boss(*args: str) -> dict:
	result = subprocess.run(["boss", *args], check=False, capture_output=True, text=True)
	return json.loads(result.stdout)
```

然后让上层只依赖返回的 JSON 信封字段。

## 失败恢复

标准恢复顺序：

```bash
boss doctor
boss status
boss login
boss search "Golang" --city 广州
```

通用处理建议：

- `ok=false` 时不要继续后续动作
- `AUTH_REQUIRED` 时先恢复登录，再重试 `boss search`
- `RATE_LIMITED` 时暂停，不要继续 `boss greet`
