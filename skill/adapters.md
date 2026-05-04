# Adapters — multi-provider upstream

The proxy talks to "an Anthropic-compatible upstream". The default is Anthropic itself; the same proxy works against any service that speaks `/v1/messages`. Provider-specific concerns (auth header shape, body re-mapping, SSE quirks) live in `UpstreamAdapter` subclasses.

## Setting the upstream

Two complementary knobs:

```python
MotorConfig(
    upstream_base_url="https://api.anthropic.com",  # where the proxy POSTs
    upstream_adapter="anthropic",                    # which adapter handles transforms
)
```

Both have env-cascade overrides:

| Env var | Field | Default |
|---|---|---|
| `SOPHIA_MOTOR_BASE_URL` | `upstream_base_url` | `https://api.anthropic.com` |
| `SOPHIA_MOTOR_ADAPTER` | `upstream_adapter` | `"anthropic"` |

So you can switch upstream **without touching code**:

```bash
export SOPHIA_MOTOR_BASE_URL=http://my-vllm:8000
export SOPHIA_MOTOR_ADAPTER=vllm
python my_app.py
```

`upstream_adapter` accepts:
- A **preset name** (`"anthropic"` or `"vllm"`) — instantiated with default kwargs
- An `UpstreamAdapter` **instance** — for custom subclasses or non-default kwargs

## `UpstreamAdapter` base class — the 7 hooks

Subclass and override only what differs. Defaults are passthrough.

| Hook | Signature | Default | Override to |
|---|---|---|---|
| `name` (class attr) | `str` | `"abstract"` | tag for telemetry |
| `forward_url(base_url)` | `str → str` | `f"{base_url}/v1/messages"` | change the URL path |
| `forward_headers(sdk_headers, api_key)` | `(dict, str\|None) → dict` | propagates anthropic headers | switch auth scheme |
| `verify_ssl()` | `() → bool` | `True` | disable TLS for self-signed dev tunnels |
| `transform_request(body)` | `dict → dict` | passthrough | inject sampling, drop blocks, re-shape body |
| `transform_sse_chunk(chunk)` | `bytes → bytes` | passthrough | per-chunk SSE byte transforms |
| `transform_response(body)` | `dict → dict` | passthrough | re-map a fully-parsed sync response body |

## Shipped adapters

### `AnthropicAdapter` — default

```python
class AnthropicAdapter(UpstreamAdapter):
    name = "anthropic"
    def forward_headers(self, sdk_headers, api_key):
        h = super().forward_headers(sdk_headers, api_key)
        h["x-api-key"] = api_key      # Anthropic auth scheme
        return h
```

That's it — only the auth header differs from the base passthrough. Used by every default `Motor()`.

### `VLLMAdapter` — for self-hosted Qwen / vLLM

```python
from sophia_motor._adapters import VLLMAdapter

motor = Motor(MotorConfig(
    upstream_base_url="https://my-vllm.runpod.io",
    upstream_adapter=VLLMAdapter(
        sampling={"temperature": 0.6, "top_p": 0.9},
        max_model_len=32768,            # clamps body["max_tokens"] to max(256, max_model_len - 1024)
        strip_qwen_xml=True,            # scrubs </tool_call>, </function>, </parameter> SSE artifacts
        verify_ssl=False,               # for self-signed certs / dev tunnels
    ),
    model="Qwen3.5-27B-Instruct",
))
```

What it does:
- Auth: switches from `x-api-key` to `Bearer <api_key>` (vLLM convention)
- `transform_request`: injects `sampling` defaults via `body.setdefault(...)` (per-task overrides win) + clamps `max_tokens` if `max_model_len` is set
- `transform_sse_chunk`: scrubs Qwen XML tool_call closer fragments
- `verify_ssl`: surfaced via the hook

**Documented limitation** (`_adapters.py:142-148`): vLLM with Qwen typically does **not** emit `input_json_delta` chunks → `ToolUseDeltaChunk` won't fire during streams. Other chunks work normally. UI that depends on partial tool args needs a placeholder for the vLLM path.

For a quick smoke test use the preset string + env vars; for production tuning instantiate `VLLMAdapter(...)` directly.

## Cost field on non-Anthropic upstreams

`result.metadata.total_cost_usd` is `0.0` when the upstream doesn't bill (vLLM, local Ollama-style services). Don't treat `0.0` as an error — check `metadata.input_tokens` / `output_tokens` if you need usage telemetry.

## Shipping a custom adapter (OpenAI / Gemini / your private upstream)

Shipping a Bearer-authed Anthropic-compatible upstream is one subclass:

```python
from sophia_motor._adapters import UpstreamAdapter

class MyAdapter(UpstreamAdapter):
    name = "my-provider"

    def forward_headers(self, sdk_headers, api_key):
        h = super().forward_headers(sdk_headers, api_key)
        if api_key:
            h["Authorization"] = f"Bearer {api_key}"
        return h

motor = Motor(MotorConfig(
    upstream_base_url="https://my-provider/api",
    upstream_adapter=MyAdapter(),
))
```

For **OpenAI Chat Completions** or **Gemini**, the adapter must do **body re-mapping** — the `messages`/`tools` shape is different from Anthropic Messages. That's non-trivial and not shipped:

- Map `messages: [{role, content: [{type: "text", ...}, ...]}, ...]` ↔ `messages: [{role, content: [{"type": "text", ...}, {"type": "tool_use", ...}]}, ...]`
- Map `tool_use` blocks → OpenAI's `tool_calls` array (or Gemini's `function_call`)
- Map `tool_result` blocks → OpenAI's `role: "tool"` messages
- SSE chunk shapes also differ — `transform_sse_chunk` would need to re-emit Anthropic-style events from OpenAI's stream

This is real work, not a config switch. Track issues in the upstream repo before reinventing.

## Where adapter hooks fire

| Phase | Hook called |
|---|---|
| Building forward URL once per request | `forward_url(base_url)` |
| Building headers once per request | `forward_headers(sdk_headers, api_key)` |
| Pre-forward, every request body | `transform_request(body)` |
| Each SSE chunk (streaming) | `transform_sse_chunk(chunk)` |
| Post-forward, sync (non-stream) response body | `transform_response(body)` |
| Connection setup | `verify_ssl()` |

## Live verification

`examples/vllm/main.py` shows the env-driven swap (no code changes between Anthropic and vLLM runs). The `README.md` in that folder calls out the `input_json_delta` limitation.

## When uncertain

- "Does my upstream speak Anthropic Messages format?" → Most "Anthropic-compatible" services do. vLLM's Anthropic-compat endpoint, LiteLLM, OpenRouter (Anthropic mode), Bedrock all work. OpenAI's native API does NOT — needs full body remap.
- "How do I add a header on every request?" → Subclass and override `forward_headers`, call `super()` for the base, append yours.
- "Why is my custom adapter not being used?" → Did you pass an instance (`upstream_adapter=MyAdapter()`) or just the class (`upstream_adapter=MyAdapter` ❌)? Both `instance` and `string preset` are accepted; class objects are not.
- "What happens when both `upstream_adapter` is an instance AND `SOPHIA_MOTOR_ADAPTER` env is set?" → The explicit constructor argument wins (it's the resolution-cascade pattern: explicit > env > default).
