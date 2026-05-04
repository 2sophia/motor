# Copyright (c) 2026 Sophia AI
# SPDX-License-Identifier: MIT
"""Docker example — same code as a local run; env decides where to write.

The Dockerfile sets `SOPHIA_MOTOR_WORKSPACE_ROOT=/data/runs` so audit
dumps and trace files land on a mounted volume. Locally — without that
env — `main.py` writes to the OS tempdir (e.g. `/tmp/sophia-motor/runs/`)
which is ephemeral by design. Whether you want persistence is decided
by the env var, not the code.

Run:
    docker compose up --build
    # or
    docker build -t sophia-motor-demo . && \
        docker run --rm \
          -e ANTHROPIC_API_KEY=sk-ant-... \
          -v $(pwd)/data:/data \
          sophia-motor-demo
"""
from __future__ import annotations

import asyncio

from sophia_motor import Motor, RunTask


async def main() -> None:
    motor = Motor()  # workspace_root comes from SOPHIA_MOTOR_WORKSPACE_ROOT env

    result = await motor.run(RunTask(
        prompt="In one sentence: what's the difference between a process and a container?",
        max_turns=2,
    ))

    print("─" * 60)
    print(f"output:\n{result.output_text}")
    print("─" * 60)
    print(f"runs persisted under: {result.workspace_dir}")
    print(
        f"turns={result.metadata.n_turns}  "
        f"cost=${result.metadata.total_cost_usd:.4f}  "
        f"duration={result.metadata.duration_s:.1f}s"
    )


if __name__ == "__main__":
    asyncio.run(main())
