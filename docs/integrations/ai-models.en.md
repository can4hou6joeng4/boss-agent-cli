# Recommended AI Models and Providers

The `boss ai` command group speaks an OpenAI-compatible protocol. This guide summarizes the recommended provider entry points and configuration examples for popular model families, so you can pick the latest or most practical option for your workflow. Updated on 2026-04-20.

## Supported providers

| Provider | Default `base_url` | Model coverage |
|----------|--------------------|----------------|
| `openai` | `https://api.openai.com/v1` | GPT-5, GPT-4.1, GPT-4o, and the o4 family |
| `deepseek` | `https://api.deepseek.com/v1` | DeepSeek-V3 and DeepSeek-R1 |
| `moonshot` | `https://api.moonshot.cn/v1` | Kimi K2 |
| `openrouter` | `https://openrouter.ai/api/v1` | Aggregated access to Anthropic Claude, OpenAI, Google, Meta, and more |
| `qwen` | `https://dashscope.aliyuncs.com/compatible-mode/v1` | Tongyi Qwen3 models |
| `zhipu` | `https://open.bigmodel.cn/api/paas/v4` | GLM-4.6 and GLM-Z1 |
| `siliconflow` | `https://api.siliconflow.cn/v1` | SiliconFlow aggregated inference |
| `atlas` | `https://api.atlascloud.ai/v1` | **Full-modal aggregator** — one OpenAI-compatible API spanning DeepSeek, Qwen, GLM, Kimi, MiniMax, Claude, GPT, and more |
| `custom` | set manually with `--base-url` | LiteLLM, OneAPI, self-hosted proxies, and other compatible gateways |

## Claude 4.7 / GPT-5 configuration examples

### Claude 4.7 via OpenRouter

OpenRouter wraps the Anthropic Messages API behind an OpenAI-compatible surface, which makes it the easiest way to plug Claude 4.7 into `boss ai` today:

```bash
boss ai config \
  --provider openrouter \
  --model anthropic/claude-opus-4.7 \
  --api-key <OPENROUTER_KEY>
```

Other Claude variants include `anthropic/claude-sonnet-4.6` and `anthropic/claude-haiku-4.5`.

### GPT-5 via OpenAI

```bash
boss ai config \
  --provider openai \
  --model gpt-5 \
  --api-key <OPENAI_KEY>
```

### DeepSeek-V3

```bash
boss ai config \
  --provider deepseek \
  --model deepseek-chat \
  --api-key <DEEPSEEK_KEY>
```

### Qwen3

```bash
boss ai config \
  --provider qwen \
  --model qwen3-max \
  --api-key <DASHSCOPE_KEY>
```

### Zhipu GLM-4.6

```bash
boss ai config \
  --provider zhipu \
  --model glm-4.6 \
  --api-key <ZHIPU_KEY>
```

### Atlas Cloud (one key across many model families)

[Atlas Cloud](https://www.atlascloud.ai/?utm_source=github&utm_medium=link&utm_campaign=boss-agent-cli) is a full-modal AI inference platform: a single OpenAI-compatible API gives you DeepSeek, Qwen, GLM, Kimi, MiniMax, Claude, GPT, and more — no per-vendor wiring:

```bash
boss ai config \
  --provider atlas \
  --model deepseek-ai/deepseek-v4-pro \
  --api-key <ATLASCLOUD_KEY>
```

> `deepseek-ai/deepseek-v4-pro` is a reasoning model with chain-of-thought — give it enough `max_tokens` (>= 512), otherwise tokens may be consumed by the reasoning trace and you get an empty `content` with `finish_reason=length`. The `--max-tokens` default in `boss ai config` is already 4096, so no extra tuning is needed.

<details>
<summary>Full Atlas Cloud LLM model list (59 models, matching the official <code>/zh/models/list/llm</code>)</summary>

- Anthropic (Claude): `anthropic/claude-haiku-4.5-20251001`, `anthropic/claude-opus-4.8`, `anthropic/claude-sonnet-4.6`
- OpenAI (GPT): `openai/gpt-5.4`, `openai/gpt-5.5`
- Google (Gemini): `google/gemini-3.1-flash-lite`, `google/gemini-3.1-pro-preview`, `google/gemini-3.5-flash`
- Alibaba Qwen: `qwen/qwen2.5-7b-instruct`, `Qwen/Qwen3-235B-A22B-Instruct-2507`, `qwen/qwen3-235b-a22b-thinking-2507`, `qwen/qwen3-30b-a3b`, `Qwen/Qwen3-30B-A3B-Instruct-2507`, `qwen/qwen3-30b-a3b-thinking-2507`, `qwen/qwen3-32b`, `qwen/qwen3-8b`, `Qwen/Qwen3-Coder`, `qwen/qwen3-coder-next`, `qwen/qwen3-max-2026-01-23`, `Qwen/Qwen3-Next-80B-A3B-Instruct`, `Qwen/Qwen3-Next-80B-A3B-Thinking`, `Qwen/Qwen3-VL-235B-A22B-Instruct`, `qwen/qwen3-vl-235b-a22b-thinking`, `qwen/qwen3-vl-30b-a3b-instruct`, `qwen/qwen3-vl-30b-a3b-thinking`, `qwen/qwen3-vl-8b-instruct`, `qwen/qwen3.5-122b-a10b`, `qwen/qwen3.5-27b`, `qwen/qwen3.5-35b-a3b`, `qwen/qwen3.5-397b-a17b`, `qwen/qwen3.6-35b-a3b`, `qwen/qwen3.6-plus`
- DeepSeek: `deepseek-ai/deepseek-ocr`, `deepseek-ai/deepseek-r1-0528`, `deepseek-ai/DeepSeek-V3-0324`, `deepseek-ai/DeepSeek-V3.1`, `deepseek-ai/DeepSeek-V3.1-Terminus`, `deepseek-ai/deepseek-v3.2`, `deepseek-ai/DeepSeek-V3.2-Exp`, `deepseek-ai/deepseek-v4-flash`, `deepseek-ai/deepseek-v4-pro`
- Moonshot (Kimi): `moonshotai/Kimi-K2-Instruct`, `moonshotai/Kimi-K2-Instruct-0905`, `moonshotai/Kimi-K2-Thinking`, `moonshotai/kimi-k2.5`, `moonshotai/kimi-k2.6`
- Zhipu GLM: `zai-org/GLM-4.6`, `zai-org/glm-4.7`, `zai-org/glm-5`, `zai-org/glm-5-turbo`, `zai-org/glm-5.1`, `zai-org/glm-5v-turbo`
- MiniMax: `MiniMaxAI/MiniMax-M2`, `minimaxai/minimax-m2.1`, `minimaxai/minimax-m2.5`, `minimaxai/minimax-m2.7`
- xAI: `xai/grok-4.3`
- Kuaishou KAT: `kwaipilot/kat-coder-pro-v2`
- Other: `owl`

</details>

### Self-hosted proxy via LiteLLM / OneAPI

```bash
boss ai config \
  --provider custom \
  --base-url https://your-proxy.example.com/v1 \
  --model any-model-id \
  --api-key <YOUR_KEY>
```

## How to choose

| Scenario | Recommended setup |
|----------|-------------------|
| You want the strongest reasoning models | `openrouter` + `anthropic/claude-opus-4.7`, or `openai` + `gpt-5` |
| You are cost-sensitive | `deepseek` + `deepseek-chat` |
| You want mainland-China direct access without an extra proxy | `qwen`, `zhipu`, `deepseek`, or `moonshot` |
| You want one key that spans many vendors | `openrouter` or `atlas` |
| You want a full-modal, OpenAI-compatible aggregator | `atlas` + `deepseek-ai/deepseek-v4-pro` |
| You already run your own compatible proxy | `custom` + `--base-url` |

## Validate the configuration

```bash
# Inspect the current AI configuration
boss ai config

# Run a quick smoke test
boss ai polish my-resume
```

Error codes you may see:

- `AI_NOT_CONFIGURED`: provider, model, or API key is missing
- `AI_API_ERROR`: the model API call failed, such as auth, network, or rate-limit issues
- `AI_PARSE_ERROR`: the model returned JSON that does not match the expected schema, so retrying is usually enough

## Related commands

- `boss ai analyze-jd <jd>` - JD match analysis
- `boss ai polish <resume>` - general resume polishing
- `boss ai optimize <resume> --jd <jd>` - role-targeted resume optimization
- `boss ai suggest <resume> --jd <jd>` - job-search improvement suggestions
- `boss ai reply <message>` - draft replies for recruiter messages
- `boss ai interview-prep <jd>` - mock interview question generation
- `boss ai chat-coach <chat>` - chat coaching and guidance
