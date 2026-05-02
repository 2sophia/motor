# Copyright (c) 2026 Sophia AI
# SPDX-License-Identifier: MIT
"""vLLM upstream — same motor, different provider.

Run sophia-motor against a self-hosted vLLM server (Qwen3.5 in this
example) instead of api.anthropic.com. Same `motor.run(...)`, same
`RunResult`, same audit dump — only the proxy → upstream hop changes.

Configured via env vars: write the motor once, switch provider/model
without touching code.

    export SOPHIA_MOTOR_BASE_URL=http://localhost:8001
    export SOPHIA_MOTOR_ADAPTER=vllm
    export SOPHIA_MOTOR_MODEL=Qwen/Qwen3.5-27B
    export ANTHROPIC_API_KEY=local       # any non-empty string for unauth'd vLLM
    python main.py

See README.md for the matching vLLM server command. For sampling /
max_tokens / Qwen XML scrubbing knobs, instantiate `VLLMAdapter(...)`
directly and pass it as `upstream_adapter=` instead of the preset.
"""
from __future__ import annotations

import asyncio

from sophia_motor import Motor, RunTask


async def main() -> None:
    # No args — every MotorConfig field reads from env via the
    # SOPHIA_MOTOR_* cascade. Pass an explicit MotorConfig(...) only
    # when you want to override a field inline.
    motor = Motor()

    # ── Advanced: explicit VLLMAdapter ────────────────────────────────
    # When the `vllm` preset isn't enough (sampling tuning, max_tokens
    # clamping, Qwen XML scrubbing, self-signed TLS), instantiate
    # `VLLMAdapter` directly and pass it as `upstream_adapter`. URL and
    # model still come from env via the cascade — adapter instance just
    # composes on top:
    #
    #     from sophia_motor._adapters import VLLMAdapter
    #     motor = Motor(MotorConfig(
    #         upstream_adapter=VLLMAdapter(
    #             sampling={"temperature": 0.6, "top_p": 0.9, "top_k": 20,
    #                       "min_p": 0.0, "presence_penalty": 0.5,
    #                       "repetition_penalty": 1.0},
    #             max_model_len=98304,
    #             strip_qwen_xml=True,
    #             verify_ssl=False,
    #         ),
    #     ))

    result = await motor.run(RunTask(
        prompt="Reply with exactly two short sentences about why open-weight LLMs matter.",
        max_turns=2,
    ))

    print("─" * 60)
    print(f"output:\n{result.output_text}")
    print("─" * 60)
    print(
        f"upstream={motor.config.upstream_base_url}  "
        f"model={motor.config.model}  "
        f"turns={result.metadata.n_turns}  "
        f"tokens=in:{result.metadata.input_tokens}/out:{result.metadata.output_tokens}  "
        f"duration={result.metadata.duration_s:.1f}s"
    )
    print(f"audit dump: {result.audit_dir}")

    await motor.stop()


if __name__ == "__main__":
    asyncio.run(main())
