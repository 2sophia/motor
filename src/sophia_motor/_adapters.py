# Copyright (c) 2026 Sophia AI
# SPDX-License-Identifier: MIT
"""Upstream adapters — make the proxy multi-provider.

The motor + proxy frontend (registry per-run, audit dump, EventBus,
universal request transforms) stay provider-agnostic. Provider-specific
quirks live in `UpstreamAdapter` subclasses: where to forward, which
auth scheme, what shape the body needs, what to strip from the SSE.

Built-in adapters:

- **AnthropicAdapter** — default, passthrough. Talks to api.anthropic.com
  with `x-api-key` and the body the SDK sent.
- **VLLMAdapter** — vLLM serving Qwen / similar, exposing the Anthropic
  Messages API. Bearer auth, sampling injection, max_tokens clamping,
  optional Qwen XML-artifact stripping in the SSE.

Adding a new provider (OpenAI, Google, …) means writing one more
subclass — no `if upstream == "X"` branching anywhere in the proxy.

Subclassing contract: override `transform_request` / `forward_headers`
/ `transform_sse_chunk` / `transform_response` as needed. Defaults are
no-op so a minimal adapter is just a few lines.
"""
from __future__ import annotations

import json
import re
from typing import Any


class UpstreamAdapter:
    """Base class — every adapter customizes the proxy → upstream hop.

    A subclass overrides whichever methods it needs; the unmodified
    defaults are passthrough so writing a minimal adapter is a few
    lines.
    """

    name: str = "abstract"

    def forward_url(self, base_url: str) -> str:
        """Return the URL the proxy POSTs to. Default: `<base>/v1/messages`."""
        return f"{base_url}/v1/messages"

    def forward_headers(self, sdk_headers: dict, api_key: str | None) -> dict:
        """Build the headers sent upstream.

        `sdk_headers` are the headers the local SDK call carried (e.g.
        `anthropic-version`, `content-type`, `anthropic-beta`). `api_key`
        is the configured key (may be empty when not authenticating
        upstream — e.g. local vLLM without auth).
        """
        out: dict[str, str] = {}
        for key in ("anthropic-version", "content-type", "anthropic-beta"):
            v = sdk_headers.get(key)
            if v:
                out[key] = v
        out.setdefault("content-type", "application/json")
        return out

    def verify_ssl(self) -> bool:
        """Whether httpx should verify the upstream TLS certificate.

        True for any production endpoint. Override to False for local
        self-signed dev clusters (vLLM behind nginx, RunPod, etc.) and
        accept that you're disabling cert pinning.
        """
        return True

    def transform_request(self, body: dict) -> dict:
        """In-place transform of the JSON body before forwarding.

        Returns the (possibly mutated) body. Default: passthrough.
        Override to inject sampling params, drop unsupported blocks,
        re-shape for non-Anthropic providers, etc.
        """
        return body

    def transform_sse_chunk(self, chunk: bytes) -> bytes:
        """Per-chunk transform on streaming SSE responses. Default: passthrough."""
        return chunk

    def transform_response(self, body: dict) -> dict:
        """Transform a fully-parsed sync (non-streaming) response body.

        Default: passthrough. Override when the upstream's wire format
        diverges from Anthropic Messages and needs to be re-mapped
        before the local SDK consumes it.
        """
        return body


# ─────────────────────────────────────────────────────────────────────────
# Built-in adapters
# ─────────────────────────────────────────────────────────────────────────

class AnthropicAdapter(UpstreamAdapter):
    """Real api.anthropic.com — `x-api-key`, no body transforms.

    This is the default and the path covered by every existing example.
    Overrides only `forward_headers` to attach `x-api-key`.
    """

    name = "anthropic"

    def forward_headers(self, sdk_headers: dict, api_key: str | None) -> dict:
        out = super().forward_headers(sdk_headers, api_key)
        if api_key:
            out["x-api-key"] = api_key
        return out


# Qwen3.5 sometimes hallucinates these XML closer fragments inside its
# `text` / `thinking` deltas — the patterns trip the Anthropic-format
# parser on the SDK side and truncate the response. Strip them before
# the SDK sees them. Pattern lifted from sophia-agent's _clean_sse_chunk.
_QWEN_XML_ARTIFACTS_RE = re.compile(
    rb"</?(?:tool_call|function|parameter)\s*[^>]*>",
)


class VLLMAdapter(UpstreamAdapter):
    """vLLM serving Qwen (or similar) over the Anthropic Messages API.

    Quirks handled:

    - **Bearer auth** instead of `x-api-key`. vLLM behind RunPod / nginx
      typically expects `Authorization: Bearer <token>`.
    - **Sampling injection** — vLLM uses provider defaults that produce
      mediocre output for Qwen. Pass `sampling={...}` (e.g. temperature,
      top_p, top_k, min_p, presence_penalty, repetition_penalty) and we
      inject any field the SDK didn't already set.
    - **`max_tokens` clamping** — vLLM hard-rejects requests where
      `input_tokens + max_tokens > model_len`. Pass `max_model_len` to
      enable a conservative clamp on every request.
    - **Qwen XML strip** — Qwen3.5 occasionally emits `</tool_call>`,
      `</function>`, `</parameter>` inside text/thinking deltas. Enable
      `strip_qwen_xml=True` to scrub those from SSE chunks.
    - **TLS toggle** — `verify_ssl=False` for self-signed dev clusters.

    Limitations vs Anthropic upstream (NOT motor regressions, upstream
    quirks): vLLM with Qwen typically does NOT emit `input_json_delta`
    chunks for tool_use streaming — `ToolUseDeltaChunk` won't fire
    during runs against a vLLM upstream. `ToolUseStart` /
    `ToolUseFinalized` / `ToolUseComplete` / `ToolResult` /
    `TextDelta` all work normally.
    """

    name = "vllm"

    def __init__(
        self,
        *,
        sampling: dict[str, Any] | None = None,
        max_model_len: int | None = None,
        strip_qwen_xml: bool = False,
        verify_ssl: bool = True,
    ) -> None:
        self.sampling = dict(sampling) if sampling else {}
        self.max_model_len = max_model_len
        self.strip_qwen_xml = strip_qwen_xml
        self._verify_ssl = verify_ssl

    def forward_headers(self, sdk_headers: dict, api_key: str | None) -> dict:
        out = super().forward_headers(sdk_headers, api_key)
        if api_key:
            out["authorization"] = f"Bearer {api_key}"
        return out

    def verify_ssl(self) -> bool:
        return self._verify_ssl

    def transform_request(self, body: dict) -> dict:
        # Inject sampling fields the caller configured, but only where
        # the SDK didn't already supply them. Per-RunTask overrides
        # always win against adapter defaults.
        for key, value in self.sampling.items():
            body.setdefault(key, value)

        # Conservative max_tokens clamp. We can't know the exact input
        # token count without tokenizing, so we leave a generous margin
        # (1024 tokens) for the prompt overhead and reject the rest.
        # Sophia-agent has a retry-on-overflow loop; we keep it simple
        # for v1 and let the dev tune `max_model_len` lower if needed.
        if self.max_model_len is not None:
            current = body.get("max_tokens", 4096)
            ceiling = max(256, self.max_model_len - 1024)
            if current > ceiling:
                body["max_tokens"] = ceiling

        return body

    def transform_sse_chunk(self, chunk: bytes) -> bytes:
        if not self.strip_qwen_xml:
            return chunk
        # Fast path: most chunks contain none of the artifact prefixes
        if (
            b"</parameter>" not in chunk
            and b"</function>" not in chunk
            and b"</tool_call>" not in chunk
            and b"<tool_call>" not in chunk
        ):
            return chunk
        # Targeted regex over the bytes — preserves SSE framing.
        return _QWEN_XML_ARTIFACTS_RE.sub(b"", chunk)


# ─────────────────────────────────────────────────────────────────────────
# resolution helper
# ─────────────────────────────────────────────────────────────────────────

_PRESETS: dict[str, type[UpstreamAdapter]] = {
    "anthropic": AnthropicAdapter,
    "vllm": VLLMAdapter,
}


def resolve_adapter(value: "str | UpstreamAdapter") -> UpstreamAdapter:
    """Coerce a MotorConfig.upstream_adapter value into an instance.

    - `UpstreamAdapter` → returned as-is.
    - `str` matching a preset name → instantiated with default kwargs.
    - anything else → TypeError with the list of supported presets.
    """
    if isinstance(value, UpstreamAdapter):
        return value
    if isinstance(value, str):
        cls = _PRESETS.get(value)
        if cls is None:
            raise ValueError(
                f"unknown upstream_adapter preset {value!r}; "
                f"valid: {sorted(_PRESETS)} or pass an UpstreamAdapter instance"
            )
        return cls()
    raise TypeError(
        f"upstream_adapter must be an UpstreamAdapter instance or one of "
        f"{sorted(_PRESETS)}, got {type(value).__name__}"
    )
