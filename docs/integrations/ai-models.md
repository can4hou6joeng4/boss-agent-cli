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
| `atlas` | `https://api.atlascloud.ai/v1` | **全模态聚合入口**，一个 OpenAI 兼容 API 覆盖 DeepSeek、Qwen、GLM、Kimi、MiniMax、Claude、GPT 等 |
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

### Atlas Cloud（一个 key 覆盖多家模型）

[Atlas Cloud](https://www.atlascloud.ai/?utm_source=github&utm_medium=link&utm_campaign=boss-agent-cli) 是一个全模态 AI 推理平台，用一个 OpenAI 兼容 API 就能访问 DeepSeek、Qwen、GLM、Kimi、MiniMax、Claude、GPT 等模型，无需逐家接入：

```bash
boss ai config \
  --provider atlas \
  --model deepseek-ai/deepseek-v4-pro \
  --api-key <ATLASCLOUD_KEY>
```

> `deepseek-ai/deepseek-v4-pro` 是带思维链的推理模型，`max_tokens` 要给足（建议 ≥ 512），否则 token 可能先耗在思维链上，出现 `content` 为空且 `finish_reason=length`。`boss ai config` 的 `--max-tokens` 默认即为 4096，无需额外调整。

<details>
<summary>Atlas Cloud 全量 LLM 模型清单（59 个，与官网 <code>/zh/models/list/llm</code> 一致）</summary>

- Anthropic (Claude)：`anthropic/claude-haiku-4.5-20251001`、`anthropic/claude-opus-4.8`、`anthropic/claude-sonnet-4.6`
- OpenAI (GPT)：`openai/gpt-5.4`、`openai/gpt-5.5`
- Google (Gemini)：`google/gemini-3.1-flash-lite`、`google/gemini-3.1-pro-preview`、`google/gemini-3.5-flash`
- 阿里 Qwen：`qwen/qwen2.5-7b-instruct`、`Qwen/Qwen3-235B-A22B-Instruct-2507`、`qwen/qwen3-235b-a22b-thinking-2507`、`qwen/qwen3-30b-a3b`、`Qwen/Qwen3-30B-A3B-Instruct-2507`、`qwen/qwen3-30b-a3b-thinking-2507`、`qwen/qwen3-32b`、`qwen/qwen3-8b`、`Qwen/Qwen3-Coder`、`qwen/qwen3-coder-next`、`qwen/qwen3-max-2026-01-23`、`Qwen/Qwen3-Next-80B-A3B-Instruct`、`Qwen/Qwen3-Next-80B-A3B-Thinking`、`Qwen/Qwen3-VL-235B-A22B-Instruct`、`qwen/qwen3-vl-235b-a22b-thinking`、`qwen/qwen3-vl-30b-a3b-instruct`、`qwen/qwen3-vl-30b-a3b-thinking`、`qwen/qwen3-vl-8b-instruct`、`qwen/qwen3.5-122b-a10b`、`qwen/qwen3.5-27b`、`qwen/qwen3.5-35b-a3b`、`qwen/qwen3.5-397b-a17b`、`qwen/qwen3.6-35b-a3b`、`qwen/qwen3.6-plus`
- DeepSeek：`deepseek-ai/deepseek-ocr`、`deepseek-ai/deepseek-r1-0528`、`deepseek-ai/DeepSeek-V3-0324`、`deepseek-ai/DeepSeek-V3.1`、`deepseek-ai/DeepSeek-V3.1-Terminus`、`deepseek-ai/deepseek-v3.2`、`deepseek-ai/DeepSeek-V3.2-Exp`、`deepseek-ai/deepseek-v4-flash`、`deepseek-ai/deepseek-v4-pro`
- Moonshot (Kimi)：`moonshotai/Kimi-K2-Instruct`、`moonshotai/Kimi-K2-Instruct-0905`、`moonshotai/Kimi-K2-Thinking`、`moonshotai/kimi-k2.5`、`moonshotai/kimi-k2.6`
- 智谱 GLM：`zai-org/GLM-4.6`、`zai-org/glm-4.7`、`zai-org/glm-5`、`zai-org/glm-5-turbo`、`zai-org/glm-5.1`、`zai-org/glm-5v-turbo`
- MiniMax：`MiniMaxAI/MiniMax-M2`、`minimaxai/minimax-m2.1`、`minimaxai/minimax-m2.5`、`minimaxai/minimax-m2.7`
- xAI：`xai/grok-4.3`
- 快手 KAT：`kwaipilot/kat-coder-pro-v2`
- 其他：`owl`

</details>

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
| 需要混用多家模型 | `openrouter` 或 `atlas` 一个 key 全覆盖 |
| 想要全模态 + OpenAI 兼容聚合入口 | `atlas` + `deepseek-ai/deepseek-v4-pro` |
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
