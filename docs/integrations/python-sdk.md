# Python 直接集成（OpenAI / Claude SDK）

面向不走 MCP 通道的场景：在自己的 Python Agent 里直接调用 OpenAI Functions API 或 Claude Tool Use API，让大模型驱动 `boss-agent-cli`。

## 适用场景

- 自建 Agent 框架（LangGraph / LlamaIndex / 手写 Loop）
- 想把 BOSS 求职能力嵌入到现有业务代码
- 测试新模型对 34 个 CLI 工具的调度能力
- CI/CD 里跑定时任务让 Agent 自动跟进投递

## 核心思路

`boss schema` 命令支持三种输出格式：

| `--format` | 输出结构 | 适用 SDK |
|-----------|---------|---------|
| `native`（默认） | 项目自定义 JSON 信封 | 手动解析 / MCP 转换 |
| `openai-tools` | 符合 OpenAI Functions / Tools API | `openai` Python SDK |
| `anthropic-tools` | 符合 Claude Tool Use API | `anthropic` Python SDK |

后两种格式可直接喂给对应 SDK 的 `tools=` 参数，无需手写转换。

## 最小 OpenAI 示例

`examples/openai_agent.py`：

```python
"""用 OpenAI GPT-4o 驱动 boss-agent-cli。

运行：
    pip install openai
    export OPENAI_API_KEY=sk-...
    boss login  # 先确保已登录
    python examples/openai_agent.py "找上海 30K 以上的 Python 后端"
"""
import json
import subprocess
import sys
from openai import OpenAI

def run_boss(*args):
    """调用 boss CLI，返回解析后的 JSON 信封。"""
    result = subprocess.run(
        ["boss", "--json", *args],
        capture_output=True, text=True, timeout=120,
    )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "error": {"code": "CLI_ERROR", "message": result.stderr}}


def load_tools():
    """拉 boss schema openai-tools 格式，直接喂给 SDK。"""
    out = subprocess.run(
        ["boss", "schema", "--format", "openai-tools"],
        capture_output=True, text=True,
    ).stdout
    return json.loads(out)["data"]["tools"]


def main(user_prompt: str):
    client = OpenAI()
    tools = load_tools()
    messages = [
        {"role": "system", "content": "你是求职助手。通过 boss_* 工具帮用户操作 BOSS 直聘。每个工具的返回值是 JSON 信封，ok=false 时要告知用户。"},
        {"role": "user", "content": user_prompt},
    ]

    for _ in range(10):  # 最多 10 轮工具调用
        resp = client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            tools=tools,
        )
        msg = resp.choices[0].message
        messages.append(msg)

        if not msg.tool_calls:
            print(msg.content)
            return

        for call in msg.tool_calls:
            # 工具名形如 "boss_search"；还原成 "search"
            cmd = call.function.name.replace("boss_", "").replace("_", "-")
            args = json.loads(call.function.arguments)

            # 把 dict 参数拆成 CLI 参数
            cli_args = [cmd]
            for key, value in args.items():
                if key == "query" or (len(args) == 1 and isinstance(value, str)):
                    cli_args.append(str(value))
                else:
                    cli_args.extend([f"--{key.replace('_', '-')}", str(value)])

            output = run_boss(*cli_args)
            messages.append({
                "role": "tool",
                "tool_call_id": call.id,
                "content": json.dumps(output, ensure_ascii=False),
            })


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "搜索北京的 Python 开发岗位")
```

运行：

```bash
export OPENAI_API_KEY=sk-...
boss login
python examples/openai_agent.py "搜上海 25K 以上的 Golang 岗位"
```

## 最小 Claude (Anthropic) 示例

`examples/anthropic_agent.py`：

```python
"""用 Claude Sonnet 4.6 驱动 boss-agent-cli。"""
import json
import subprocess
import sys
from anthropic import Anthropic

def run_boss(*args):
    result = subprocess.run(
        ["boss", "--json", *args],
        capture_output=True, text=True, timeout=120,
    )
    return json.loads(result.stdout) if result.stdout else {"ok": False, "error": {"message": result.stderr}}


def load_tools():
    out = subprocess.run(
        ["boss", "schema", "--format", "anthropic-tools"],
        capture_output=True, text=True,
    ).stdout
    return json.loads(out)["data"]["tools"]


def main(user_prompt: str):
    client = Anthropic()
    tools = load_tools()
    messages = [{"role": "user", "content": user_prompt}]

    for _ in range(10):
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            system="你是求职助手。通过 boss_* 工具帮用户操作 BOSS 直聘。",
            tools=tools,
            messages=messages,
        )

        if resp.stop_reason == "end_turn":
            for block in resp.content:
                if block.type == "text":
                    print(block.text)
            return

        if resp.stop_reason != "tool_use":
            break

        messages.append({"role": "assistant", "content": resp.content})
        tool_results = []
        for block in resp.content:
            if block.type == "tool_use":
                cmd = block.name.replace("boss_", "").replace("_", "-")
                args = block.input

                cli_args = [cmd]
                for key, value in args.items():
                    if key == "query":
                        cli_args.append(str(value))
                    else:
                        cli_args.extend([f"--{key.replace('_', '-')}", str(value)])

                output = run_boss(*cli_args)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(output, ensure_ascii=False),
                })

        messages.append({"role": "user", "content": tool_results})


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "搜索北京的 Python 开发岗位")
```

## 参数转换要点

从 LLM 的 JSON 参数到 CLI 参数的映射约定：

| LLM 参数 | CLI 形式 | 示例 |
|---------|---------|------|
| 位置参数（如 `query`） | 直接拼进 args 开头 | `boss search python` |
| 下划线字段名（如 `job_id`） | 转 dash 作为 long option | `--job-id abc123` |
| bool true | 作为 flag 加入 | `--dry-run` |
| bool false | 省略（CLI 默认就是 false） | — |

更健壮的参数转换器参考 `mcp-server/server.py` 的 `_build_args()` 函数，它已经覆盖了 34 个命令的完整转换逻辑，建议直接复用：

```python
sys.path.insert(0, "path/to/boss-agent-cli/mcp-server")
from server import _build_args

cli_args = _build_args(call.function.name, args)
```

## 错误处理建议

boss CLI 的错误信封结构：

```json
{
  "ok": false,
  "error": {
    "code": "AUTH_EXPIRED",
    "message": "登录态已过期",
    "recoverable": true,
    "recovery_action": "boss login"
  }
}
```

让你的 Agent 识别 `recovery_action` 字段自动重试：

```python
if not output["ok"]:
    err = output["error"]
    if err.get("recoverable") and err["code"] == "AUTH_EXPIRED":
        run_boss("login")  # 自动重登
        output = run_boss(*cli_args)  # 重试一次
```

## 环境依赖

- Python ≥ 3.10
- `boss-agent-cli` 已 `uv tool install` 或 `pipx install`
- 已跑过 `boss login` 确保登录态存在
- OpenAI / Anthropic API key 按 SDK 标准环境变量设置

## 参考

- [OpenAI Tools API 文档](https://platform.openai.com/docs/guides/function-calling)
- [Anthropic Tool Use 文档](https://docs.claude.com/en/docs/build-with-claude/tool-use)
- [boss schema --format 选项](../capability-matrix.md)
- [MCP 集成方式（Claude Desktop / Cursor）](../../mcp-server/README.md)
