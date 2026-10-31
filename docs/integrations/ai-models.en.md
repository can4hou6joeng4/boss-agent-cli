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

The o series and gpt-5 or later are reasoning models: they accept only the default `temperature` and require `max_completion_tokens` instead of `max_tokens`. The CLI switches by model name. It sends no `temperature` for these models (`--temperature` has no effect) and sends the `--max-tokens` value as `max_completion_tokens`. Reasoning tokens count toward this limit; when an error says the model used up `max_completion_tokens` before writing its answer, raise it with `boss ai config --max-tokens`. Calls through OpenRouter (such as `openai/gpt-5`) still send `max_tokens`, which OpenRouter translates.

Newer Kimi models (such as `kimi-k2.6`, `kimi-k2.7-code`, and `kimi-k3`) have a platform-fixed temperature and reject other values. The CLI sends no `temperature` for Kimi K2.5 or later.

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
| You want one key that spans many vendors | `openrouter` |
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
- `AI_API_ERROR`: the model API call failed, such as auth, network, rate-limit, unsupported-parameter, or output-limit issues. `error.message` includes the reason returned by the service (key-like strings are masked), and `recovery_action` suggests a next step for that kind of failure
- `AI_PARSE_ERROR`: the model returned JSON that does not match the expected schema, so retrying is usually enough

## Related commands

- `boss ai analyze-jd <jd>` - JD match analysis
- `boss ai polish <resume>` - general resume polishing
- `boss ai optimize <resume> --jd <jd>` - role-targeted resume optimization
- `boss ai suggest <resume> --jd <jd>` - job-search improvement suggestions
- `boss ai reply <message>` - draft replies for recruiter messages
- `boss ai interview-prep <jd>` - mock interview question generation
- `boss ai chat-coach <chat>` - chat coaching and guidance
