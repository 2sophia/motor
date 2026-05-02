# Copyright (c) 2026 Sophia AI
# SPDX-License-Identifier: MIT
"""Docker example — explicit workspace_root pointed at a mounted volume.

The default `~/.sophia-motor/runs/` would die with the container.
Pass an explicit `workspace_root` under a volume so audit dumps and
trace files survive container restarts.

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
from pathlib import Path

from sophia_motor import Motor, MotorConfig, RunTask


async def main() -> None:
    motor = Motor(MotorConfig(
        workspace_root=Path("/data/runs"),
    ))

    result = await motor.run(RunTask(
        prompt="In one sentence: what's the difference between a process and a container?",
        max_turns=2,
    ))

    print("─" * 60)
    print(f"output:\n{result.output_text}")
    print("─" * 60)
    print(f"runs persisted under: /data/runs/{result.metadata.run_id}/")
    print(
        f"turns={result.metadata.n_turns}  "
        f"cost=${result.metadata.total_cost_usd:.4f}  "
        f"duration={result.metadata.duration_s:.1f}s"
    )


if __name__ == "__main__":
    asyncio.run(main())
