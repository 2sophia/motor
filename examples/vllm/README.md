# vllm

Run sophia-motor against a self-hosted **vLLM** server (Qwen3.5,
Llama, Mistral, …) instead of api.anthropic.com. Same code, same
`RunResult`, same audit dump — only the proxy → upstream hop changes,
encapsulated in an `UpstreamAdapter` selected via `MotorConfig`.

## The pattern: env-first

The motor reads its upstream from env vars. Write the motor once,
switch provider/model without touching code:

```bash
export SOPHIA_MOTOR_BASE_URL=http://localhost:8001
export SOPHIA_MOTOR_ADAPTER=vllm
export SOPHIA_MOTOR_MODEL=Qwen/Qwen3.5-27B
export ANTHROPIC_API_KEY=local           # any non-empty string for unauth'd vLLM
```

```python
import asyncio
from sophia_motor import Motor, MotorConfig, RunTask

async def main():
    motor = Motor(MotorConfig())          # all four read from env
    result = await motor.run(RunTask(
        prompt="Why do open-weight LLMs matter? Two sentences.",
    ))
    print(result.output_text)
    await motor.stop()

asyncio.run(main())
```

To swap to Anthropic for a side-by-side comparison:

```bash
unset SOPHIA_MOTOR_BASE_URL SOPHIA_MOTOR_ADAPTER
export SOPHIA_MOTOR_MODEL=claude-opus-4-6
export ANTHROPIC_API_KEY=sk-ant-...
python main.py
```

Same code, different provider.

## vLLM server (Qwen3.5-27B, prod config)

The vLLM command Sophia AI runs in production:

```bash
vllm serve Qwen/Qwen3.5-27B \
    --language-model-only \
    --enable-auto-tool-choice \
    --reasoning-parser qwen3 \
    --tool-call-parser qwen3_coder \
    --gpu-memory-utilization 0.95 \
    --enable-prefix-caching \
    --max-model-len 98304 \
    --host 0.0.0.0 \
    --port 8001 \
    --max-num-seqs 5
```

Key flags:

| Flag | Why |
|---|---|
| `--enable-auto-tool-choice` + `--tool-call-parser qwen3_coder` | Tool calls land as proper Anthropic `tool_use` blocks instead of inline XML strings |
| `--reasoning-parser qwen3` | Qwen3.5 thinking content goes into `thinking` blocks, not interleaved text |
| `--enable-prefix-caching` | Per-turn agent runs share a long system+history prefix; massive throughput win |
| `--max-model-len 98304` | Comfortable context for multi-turn agent traces |
| `--max-num-seqs 5` | Bound concurrent decoders so a single greedy run doesn't starve others |
| `--gpu-memory-utilization 0.95` | Squeeze the last 5% out of the card on a single-tenant host |

## Full env reference

| Env | Default | Purpose |
|---|---|---|
| `SOPHIA_MOTOR_BASE_URL` | `https://api.anthropic.com` | Proxy → upstream URL |
| `SOPHIA_MOTOR_ADAPTER` | `anthropic` | Preset (`anthropic` / `vllm`) — selects auth scheme + body transforms |
| `SOPHIA_MOTOR_MODEL` | `claude-opus-4-6` | Model identifier the SDK sends to the upstream |
| `ANTHROPIC_API_KEY` | unset → motor refuses to start | Bearer token for vLLM (any non-empty string for unauth'd self-hosted), real API key for Anthropic |

The cascade is **explicit param > env var > `./.env` file > hardcoded
default**, applied per-field. So you can env-default everything and
still override a single field inline:

```python
motor = Motor(MotorConfig(model="Qwen/Qwen3.5-72B"))   # env wins for url/adapter/key
```

## When the preset isn't enough

`SOPHIA_MOTOR_ADAPTER=vllm` instantiates `VLLMAdapter()` with neutral
defaults — Bearer auth + Anthropic-compatible passthrough. Good for
a smoke test against vanilla Qwen3.5.

For sampling tuning, `max_tokens` clamping, or Qwen XML scrubbing,
build the adapter explicitly and pass the instance:

```python
from sophia_motor._adapters import VLLMAdapter

motor = Motor(MotorConfig(
    upstream_adapter=VLLMAdapter(
        sampling={"temperature": 0.6, "top_p": 0.9, "top_k": 20,
                  "min_p": 0.0, "presence_penalty": 0.5,
                  "repetition_penalty": 1.0},
        max_model_len=98304,           # clamp max_tokens to len - 1024
        strip_qwen_xml=True,           # scrub Qwen </tool_call> SSE artifacts
        verify_ssl=False,              # self-signed dev / RunPod tunnel
    ),
))
```

The instance still composes with env vars: `SOPHIA_MOTOR_BASE_URL`
and `SOPHIA_MOTOR_MODEL` keep working.

## What `VLLMAdapter` does on the hop

| Concern | What it does |
|---|---|
| Auth scheme | `Authorization: Bearer <api_key>` instead of `x-api-key` |
| TLS | `verify=False` toggle for self-signed dev / RunPod tunnels |
| Sampling | Inject `temperature` / `top_p` / `top_k` / `min_p` / `presence_penalty` / `repetition_penalty` if SDK didn't already set them — Qwen quality is mediocre at vLLM defaults |
| `max_tokens` | Clamp to `max_model_len - 1024` (leaves 1024 for prompt) so vLLM doesn't reject token-overflow requests |
| SSE artifacts | Strip `</tool_call>`, `</function>`, `</parameter>` fragments from text/thinking deltas (Qwen3.5 hallucinates them; they break the SDK parser) |

## Limitations vs Anthropic upstream

These are **upstream/model quirks**, not motor regressions:

- **`ToolUseDeltaChunk` doesn't fire**: vLLM with Qwen typically
  doesn't emit `input_json_delta` chunks during tool_use streaming.
  `ToolUseStartChunk`, `ToolUseFinalizedChunk`, `ToolUseCompleteChunk`,
  `ToolResultChunk`, and `TextDeltaChunk` all work normally.
- **No native cost accounting**: `result.metadata.total_cost_usd` will
  be `0.0` — vLLM doesn't bill, you pay for the GPU instead.
- **Tool-calling reliability** depends on the model. Qwen3.5 with
  `--tool-call-parser qwen3_coder` is solid; smaller / older Qwens may
  need different prompts. Test before shipping.

## Writing your own adapter

The `UpstreamAdapter` base class has 5 hooks; override what differs:

```python
from sophia_motor._adapters import UpstreamAdapter

class MyAdapter(UpstreamAdapter):
    name = "my-provider"

    def forward_url(self, base_url):
        return f"{base_url}/api/v2/chat"     # different path

    def forward_headers(self, sdk_headers, api_key):
        return {"x-my-token": api_key,
                "content-type": "application/json"}

    def transform_request(self, body):
        # body re-mapping if the provider doesn't speak Anthropic
        return body

motor = Motor(MotorConfig(
    upstream_base_url="https://my-provider.example",
    upstream_adapter=MyAdapter(),
))
```

OpenAI / Google adapters aren't shipped yet — they need full body
re-mapping (Anthropic Messages → Chat Completions / Gemini), not just
header switching. The plumbing is in place; the format translation is
the work.
