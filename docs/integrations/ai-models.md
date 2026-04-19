# 推荐 AI 模型与入口

`boss ai` 命令组基于 OpenAI 兼容协议。下表汇总主流模型的推荐入口和配置示例，供你挑选最新或最合适的模型接入。更新时间：2026-04-20。

## 支持的 Provider

| Provider | 默认 base_url | 覆盖模型 |
|----------|---------------|---------|
| `openai` | `https://api.openai.com/v1` | GPT-5、GPT-4.1、GPT-4o、o4 系列 |
| `deepseek` | `https://api.deepseek.com/v1` | DeepSeek-V3、DeepSeek-R1 |
| `moonshot` | `https://api.moonshot.cn/v1` | Kimi K2 |
| `openrouter` | `https://openrouter.ai/api/v1` | **聚合入口**，支持 Anthropic Claude、OpenAI、Google、Meta 等全家桶 |
| `qwen` | `https://dashscope.aliyuncs.com/compatible-mode/v1` | 通义千问 Qwen3 系列 |
| `zhipu` | `https://open.bigmodel.cn/api/paas/v4` | 智谱 GLM-4.6 / GLM-Z1 |
| `siliconflow` | `https://api.siliconflow.cn/v1` | 硅基流动聚合推理 |
| `custom` | 需手动指定 `--base-url` | 自建代理、LiteLLM、OneAPI 等 |

## Claude 4.7 / GPT-5 配置示例

### Claude 4.7（通过 OpenRouter）

OpenRouter 把 Anthropic Messages API 包装成 OpenAI 协议，是目前最省事的 Claude 4.7 接入方式：

```bash
boss ai config \
  --provider openrouter \
  --model anthropic/claude-opus-4.7 \
  --api-key <OPENROUTER_KEY>
```

其他 Claude 变体：`anthropic/claude-sonnet-4.6`、`anthropic/claude-haiku-4.5`。

### GPT-5（通过 OpenAI 直连）

```bash
boss ai config \
  --provider openai \
  --model gpt-5 \
  --api-key <OPENAI_KEY>
```

### DeepSeek-V3（国内直连）

```bash
boss ai config \
  --provider deepseek \
  --model deepseek-chat \
  --api-key <DEEPSEEK_KEY>
```

### Qwen3（通义千问）

```bash
boss ai config \
  --provider qwen \
  --model qwen3-max \
  --api-key <DASHSCOPE_KEY>
```

### 智谱 GLM-4.6

```bash
boss ai config \
  --provider zhipu \
  --model glm-4.6 \
  --api-key <ZHIPU_KEY>
```

### 自建代理（LiteLLM / OneAPI）

```bash
boss ai config \
  --provider custom \
  --base-url https://your-proxy.example.com/v1 \
  --model any-model-id \
  --api-key <YOUR_KEY>
```

## 如何选择

| 场景 | 建议 |
|------|------|
| 想用最强推理模型 | `openrouter` + `anthropic/claude-opus-4.7` 或 `openai` + `gpt-5` |
| 对成本敏感 | `deepseek` + `deepseek-chat`（性价比极高） |
| 国内直连不走代理 | `qwen` / `zhipu` / `deepseek` / `moonshot` |
| 需要混用多家模型 | `openrouter` 一个 key 全覆盖 |
| 已有自建代理 | `custom` + `--base-url` |

## 配置校验

```bash
# 查看当前配置
boss ai config

# 快速测试
boss ai polish my-resume
```

配置错误会返回错误码：

- `AI_NOT_CONFIGURED`：未配置 provider / model / api_key
- `AI_API_ERROR`：API 调用失败（鉴权/网络/限速等）
- `AI_PARSE_ERROR`：模型返回不符合预期 JSON 格式（重试即可）

## 相关命令

- `boss ai analyze-jd <jd>` — 职位匹配分析
- `boss ai polish <resume>` — 简历通用润色
- `boss ai optimize <resume> --jd <jd>` — 针对岗位定向优化
- `boss ai suggest <resume> --jd <jd>` — 求职改进建议
- `boss ai reply <message>` — 招聘者消息回复草稿
- `boss ai interview-prep <jd>` — 模拟面试题生成
- `boss ai chat-coach <chat>` — 沟通教练
