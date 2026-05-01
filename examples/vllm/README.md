# vllm

Run sophia-motor against a self-hosted **vLLM** server (Qwen3.5,
Llama, Mistral, …) instead of api.anthropic.com. Same code, same
`RunResult`, same audit dump — only the proxy → upstream hop changes,
encapsulated in a `VLLMAdapter` instance on `MotorConfig`.

## Minimal example

```python
import asyncio
from sophia_motor import Motor, MotorConfig, RunTask
from sophia_motor._adapters import VLLMAdapter

motor = Motor(MotorConfig(
    upstream_base_url="http://localhost:8001",
    model="Qwen/Qwen3.5-27B",
    api_key="local-vllm-no-auth",       # any non-empty string for unauth'd servers
    upstream_adapter=VLLMAdapter(
        sampling={"temperature": 0.6, "top_p": 0.9, "top_k": 20,
                  "min_p": 0.0, "presence_penalty": 0.5,
                  "repetition_penalty": 1.0},
        max_model_len=98304,
        strip_qwen_xml=True,            # scrub Qwen </tool_call> artifacts
        verify_ssl=True,
    ),
))

result = await motor.run(RunTask(
    prompt="Why do open-weight LLMs matter? Two sentences.",
))
print(result.output_text)
```

## Run

```bash
# 1. Start vLLM exposing the Anthropic Messages API at /v1/messages.
docker run --gpus all -p 8001:8000 vllm/vllm-openai:latest \
    --model Qwen/Qwen2.5-Coder-7B-Instruct \
    --enable-anthropic-compat

# 2. Run the example.
pip install sophia-motor
cd examples/vllm
python main.py
```

## How it works

`MotorConfig.upstream_adapter` is the new extension point: it routes
the proxy → upstream hop through a provider-specific class. Built-in
presets:

- `"anthropic"` — `AnthropicAdapter`, the default. Talks to
  api.anthropic.com with `x-api-key`, no body transforms.
- `"vllm"` — `VLLMAdapter`, the focus of this example.

Or pass an instance for full control of the knobs:

```python
upstream_adapter=VLLMAdapter(
    sampling={...},                   # injected when SDK doesn't supply
    max_model_len=98304,              # clamp max_tokens to len - 1024
    strip_qwen_xml=True,              # SSE chunk scrubbing
    verify_ssl=False,                 # self-signed dev clusters
),
```

## What `VLLMAdapter` actually does

| Concern | What we do |
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
- **Tool-calling reliability** depends on the model. Qwen3.5 is solid;
  smaller / older Qwens may need different prompts. Test before
  shipping.

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
