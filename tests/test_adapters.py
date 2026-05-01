# Copyright (c) 2026 Sophia AI
# SPDX-License-Identifier: MIT
"""Deterministic tests for upstream adapters."""
from __future__ import annotations

import pytest

from sophia_motor._adapters import (
    AnthropicAdapter,
    UpstreamAdapter,
    VLLMAdapter,
    resolve_adapter,
)


# ─── resolve_adapter ─────────────────────────────────────────────────────

def test_resolve_anthropic_preset_string():
    a = resolve_adapter("anthropic")
    assert isinstance(a, AnthropicAdapter)


def test_resolve_vllm_preset_string():
    a = resolve_adapter("vllm")
    assert isinstance(a, VLLMAdapter)


def test_resolve_passes_instance_through():
    custom = VLLMAdapter(sampling={"temperature": 0.5}, max_model_len=8192)
    out = resolve_adapter(custom)
    assert out is custom  # exact same instance


def test_resolve_unknown_preset_raises():
    with pytest.raises(ValueError, match="unknown upstream_adapter preset"):
        resolve_adapter("not-a-real-preset")


def test_resolve_wrong_type_raises():
    with pytest.raises(TypeError):
        resolve_adapter(42)  # type: ignore[arg-type]


# ─── AnthropicAdapter ────────────────────────────────────────────────────

def test_anthropic_forward_url_default():
    a = AnthropicAdapter()
    assert a.forward_url("https://api.anthropic.com") == "https://api.anthropic.com/v1/messages"


def test_anthropic_headers_inject_x_api_key():
    a = AnthropicAdapter()
    headers = a.forward_headers({"content-type": "application/json"}, "sk-ant-test")
    assert headers["x-api-key"] == "sk-ant-test"
    assert "authorization" not in headers
    assert headers["content-type"] == "application/json"


def test_anthropic_headers_no_key_no_x_api_key():
    a = AnthropicAdapter()
    headers = a.forward_headers({}, None)
    assert "x-api-key" not in headers


def test_anthropic_headers_forwards_anthropic_beta():
    a = AnthropicAdapter()
    headers = a.forward_headers(
        {"anthropic-beta": "tools-2024-04-04", "anthropic-version": "2023-06-01"},
        "k",
    )
    assert headers["anthropic-beta"] == "tools-2024-04-04"
    assert headers["anthropic-version"] == "2023-06-01"


def test_anthropic_request_passthrough():
    a = AnthropicAdapter()
    body = {"model": "claude-opus-4-6", "messages": [], "max_tokens": 1024}
    out = a.transform_request(body)
    assert out is body  # same dict, no mutation


def test_anthropic_sse_passthrough():
    a = AnthropicAdapter()
    chunk = b'data: {"type":"content_block_delta","delta":{"text":"hi"}}\n\n'
    assert a.transform_sse_chunk(chunk) == chunk


def test_anthropic_verify_ssl_true_by_default():
    assert AnthropicAdapter().verify_ssl() is True


# ─── VLLMAdapter ─────────────────────────────────────────────────────────

def test_vllm_uses_bearer_auth():
    v = VLLMAdapter()
    headers = v.forward_headers({"content-type": "application/json"}, "vllm-token-xyz")
    assert headers["authorization"] == "Bearer vllm-token-xyz"
    assert "x-api-key" not in headers


def test_vllm_no_key_no_authorization():
    v = VLLMAdapter()
    headers = v.forward_headers({}, None)
    assert "authorization" not in headers


def test_vllm_verify_ssl_toggle():
    assert VLLMAdapter(verify_ssl=True).verify_ssl() is True
    assert VLLMAdapter(verify_ssl=False).verify_ssl() is False


def test_vllm_injects_sampling_when_missing():
    v = VLLMAdapter(sampling={"temperature": 0.7, "top_p": 0.9, "top_k": 20})
    body = {"model": "Qwen/Qwen3", "messages": []}
    out = v.transform_request(body)
    assert out["temperature"] == 0.7
    assert out["top_p"] == 0.9
    assert out["top_k"] == 20


def test_vllm_does_not_override_sdk_sampling():
    v = VLLMAdapter(sampling={"temperature": 0.7})
    body = {"model": "Qwen", "messages": [], "temperature": 0.0}
    out = v.transform_request(body)
    assert out["temperature"] == 0.0  # SDK value wins


def test_vllm_clamps_max_tokens_above_ceiling():
    v = VLLMAdapter(max_model_len=8192)
    body = {"max_tokens": 100_000}
    out = v.transform_request(body)
    # ceiling = max(256, 8192 - 1024) = 7168
    assert out["max_tokens"] == 7168


def test_vllm_does_not_clamp_when_under_ceiling():
    v = VLLMAdapter(max_model_len=65536)
    body = {"max_tokens": 4096}
    out = v.transform_request(body)
    assert out["max_tokens"] == 4096  # untouched


def test_vllm_clamp_floor_is_256():
    v = VLLMAdapter(max_model_len=512)  # tiny model, ceiling would go below floor
    body = {"max_tokens": 9999}
    out = v.transform_request(body)
    assert out["max_tokens"] == 256


def test_vllm_no_clamp_when_max_model_len_unset():
    v = VLLMAdapter()
    body = {"max_tokens": 1_000_000}
    out = v.transform_request(body)
    assert out["max_tokens"] == 1_000_000  # passthrough


def test_vllm_sse_strips_qwen_xml_when_enabled():
    v = VLLMAdapter(strip_qwen_xml=True)
    chunk = b'data: {"delta":{"text":"hi </tool_call> there"}}\n\n'
    cleaned = v.transform_sse_chunk(chunk)
    assert b"</tool_call>" not in cleaned
    assert b'"hi  there"' in cleaned


def test_vllm_sse_passthrough_when_strip_disabled():
    v = VLLMAdapter(strip_qwen_xml=False)
    chunk = b'data: {"delta":{"text":"hi </tool_call> there"}}\n\n'
    assert v.transform_sse_chunk(chunk) == chunk  # exact passthrough


def test_vllm_sse_fast_path_no_artifacts():
    v = VLLMAdapter(strip_qwen_xml=True)
    chunk = b'data: {"delta":{"text":"hello world"}}\n\n'
    assert v.transform_sse_chunk(chunk) == chunk  # untouched, no regex applied


# ─── custom subclass extension ───────────────────────────────────────────

def test_custom_subclass_can_override_minimally():
    """A third-party adapter only needs to override what differs.

    This is the public extension contract: subclass UpstreamAdapter,
    override the methods that differ for your provider, leave the rest
    at their passthrough defaults.
    """
    class _MyAdapter(UpstreamAdapter):
        name = "custom"

        def forward_url(self, base_url: str) -> str:
            return f"{base_url}/api/v2/chat"  # different path

    a = _MyAdapter()
    assert a.forward_url("https://my-provider.example") == "https://my-provider.example/api/v2/chat"
    # defaults still in place
    assert a.verify_ssl() is True
    assert a.transform_request({"x": 1}) == {"x": 1}
