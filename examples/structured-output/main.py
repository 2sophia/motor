# Copyright (c) 2026 Sophia AI
# SPDX-License-Identifier: MIT
"""Structured output — Pydantic in, Pydantic out.

Define a schema (any Pydantic BaseModel works), pass it on the RunTask,
and read `result.output_data` as a typed object. The schema is forwarded
to the model server-side via `--json-schema`, so enums, ranges, regex
patterns, nested objects, and `additionalProperties: false` are all
honored before the response ever reaches your code.

Run:
    pip install sophia-motor
    ANTHROPIC_API_KEY=sk-ant-... python main.py
"""
from __future__ import annotations

import asyncio
from typing import Literal

from pydantic import BaseModel, Field

from sophia_motor import Motor, RunTask


class TicketTriage(BaseModel):
    category: Literal["bug", "feature_request", "billing", "other"]
    priority: Literal["low", "medium", "high", "urgent"]
    sentiment_score: float = Field(ge=-1.0, le=1.0)
    suggested_response_language: Literal["en", "it", "es", "fr", "de"]
    one_line_summary: str = Field(min_length=10, max_length=140)


SUPPORT_TICKET = """
Subject: Charged twice for the same subscription!

Hi, I just noticed two charges of $49 on my card from your service for
this month. I only have one account. This is the second time it happens
and I'm seriously considering cancelling. Please refund the duplicate
charge ASAP.
"""


async def main() -> None:
    motor = Motor()

    result = await motor.run(RunTask(
        system="You are a support ticket triage agent.",
        prompt=f"Triage this ticket and produce structured metadata.\n\n{SUPPORT_TICKET}",
        output_schema=TicketTriage,
        max_turns=3,
    ))

    if result.metadata.is_error:
        raise SystemExit(f"run failed: {result.metadata.error_reason}")

    triage: TicketTriage = result.output_data  # type: ignore[assignment]

    print("─" * 60)
    print(f"category    : {triage.category}")
    print(f"priority    : {triage.priority}")
    print(f"sentiment   : {triage.sentiment_score:+.2f}")
    print(f"language    : {triage.suggested_response_language}")
    print(f"summary     : {triage.one_line_summary}")
    print("─" * 60)
    print(
        f"turns={result.metadata.n_turns}  "
        f"cost=${result.metadata.total_cost_usd:.4f}  "
        f"duration={result.metadata.duration_s:.1f}s"
    )


if __name__ == "__main__":
    asyncio.run(main())
