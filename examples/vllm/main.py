# Copyright (c) 2026 Sophia AI
# SPDX-License-Identifier: MIT
"""vLLM upstream — same motor, different provider.

Run a sophia-motor task against a self-hosted vLLM server (Qwen3.5 in
this example) instead of api.anthropic.com. Same `motor.run(...)`,
same `RunResult`, same audit dump — only the proxy → upstream hop
changes, encapsulated in a `VLLMAdapter` instance on `MotorConfig`.

Run:
    # 1. Start a vLLM server somewhere reachable, exposing the
    #    Anthropic Messages API at /v1/messages. Example with Qwen:
    #
    #    docker run --gpus all -p 8001:8000 vllm/vllm-openai:latest \
    #        --model Qwen/Qwen2.5-Coder-7B-Instruct \
    #        --enable-anthropic-compat
    #
    # 2. Run the example. No ANTHROPIC_API_KEY needed for local vLLM.
    python main.py
"""
from __future__ import annotations

import asyncio

from sophia_motor import Motor, MotorConfig, RunTask
from sophia_motor._adapters import VLLMAdapter


# Edit these two lines to match your vLLM deployment.
VLLM_URL = "http://localhost:8001"
VLLM_MODEL = "Qwen/Qwen3.5-27B"


async def main() -> None:
    motor = Motor(MotorConfig(
        # Point the proxy at vLLM instead of Anthropic.
        upstream_base_url=VLLM_URL,
        # Tell the SDK subprocess which model identifier to send. vLLM
        # checks this against the loaded weights and rejects mismatches,
        # so this must match the `id` in `/v1/models`.
        model=VLLM_MODEL,
        # vLLM behind RunPod / nginx / vault wants a real Bearer token;
        # a local unauth'd server accepts any non-empty string. The motor
        # requires `api_key` to be set because the Claude CLI subprocess
        # also reads it as `ANTHROPIC_API_KEY`.
        api_key="local-vllm-no-auth",
        # The adapter is what makes vLLM work. It carries the bearer auth
        # scheme, sampling injection, max_tokens clamping, optional Qwen
        # XML scrubbing, and SSL verification toggle.
        upstream_adapter=VLLMAdapter(
            sampling={
                # Qwen3.5 quality tuning — these are the values
                # sophia-agent runs in production.
                "temperature": 0.6,
                "top_p": 0.9,
                "top_k": 20,
                "min_p": 0.0,
                "presence_penalty": 0.5,
                "repetition_penalty": 1.0,
            },
            # Conservative clamp leaves 1024 tokens for the prompt; tune
            # if your prompts dwarf the response.
            max_model_len=98304,
            # Qwen3.5 occasionally emits `</tool_call>`, `</function>`,
            # `</parameter>` artifacts inside text/thinking deltas.
            # Scrub them so the SDK doesn't mis-parse the response.
            strip_qwen_xml=True,
            # Local cluster has a real cert? Leave True. Self-signed
            # dev / RunPod tunnel? False.
            verify_ssl=True,
        ),
        console_log_enabled=False,
    ))

    result = await motor.run(RunTask(
        prompt="Reply with exactly two short sentences about why open-weight LLMs matter.",
        max_turns=2,
    ))

    print("─" * 60)
    print(f"output:\n{result.output_text}")
    print("─" * 60)
    print(
        f"model={VLLM_MODEL}  "
        f"turns={result.metadata.n_turns}  "
        f"tokens=in:{result.metadata.input_tokens}/out:{result.metadata.output_tokens}  "
        f"duration={result.metadata.duration_s:.1f}s"
    )
    print(f"audit dump: {result.audit_dir}")
    print(f"(self-hosted — no per-token cost)")

    await motor.stop()


if __name__ == "__main__":
    asyncio.run(main())
