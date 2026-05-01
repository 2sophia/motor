# Copyright (c) 2026 Sophia AI
# SPDX-License-Identifier: MIT
"""Demo runner used by assets/demo/demo.tape (vhs) to record the README GIF.

Pre-configured motor with sample data, opens the interactive console.
Uses claude-haiku-4-5 so the GIF stays under 15s per turn.
"""
import asyncio
from sophia_motor import Motor, MotorConfig

SAMPLE = {
    "sales-q1.json": '{"product":"alpha","revenue":102000,"units":1234}',
    "sales-q2.json": '{"product":"alpha","revenue":118500,"units":1410}',
    "sales-q3.json": '{"product":"alpha","revenue":134200,"units":1602}',
}

async def main():
    motor = Motor(MotorConfig(
        model="claude-haiku-4-5",
        default_system="You are a concise data analyst. Use tools to answer.",
        default_tools=["Read", "Glob", "Write"],
        default_attachments=SAMPLE,
        default_max_turns=6,
    ))
    await motor.console()

if __name__ == "__main__":
    asyncio.run(main())
