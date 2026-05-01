# concurrency

Run N tasks in parallel on **one Motor instance**.

## Minimal example

```python
import asyncio
from typing import Literal
from pydantic import BaseModel
from sophia_motor import Motor, MotorConfig, RunTask

class ToneVerdict(BaseModel):
    tone: Literal["positive", "neutral", "negative"]
    rationale: str

motor = Motor(MotorConfig(console_log_enabled=False))   # ← one motor

reviews = [
    "Best product I've ever bought, recommend to everyone.",
    "It works but the packaging was terrible.",
    "Defective on arrival, customer service unresponsive.",
]

async def classify(review: str) -> ToneVerdict:
    result = await motor.run(RunTask(
        prompt=f"Classify the tone of: {review}",
        output_schema=ToneVerdict,
    ))
    return result.output_data

verdicts = await asyncio.gather(*(classify(r) for r in reviews))
```

## Run

```bash
pip install sophia-motor
export ANTHROPIC_API_KEY=sk-ant-...
cd examples/concurrency
python main.py
```

## How it works

Internally the proxy keeps a `dict[run_id → audit_dir]` registry. Each
run gets its own URL under `/run/<run_id>/v1/messages` — concurrent
runs never collide on the dump path or the request counter. There is
no per-motor lock; `await motor.run(task)` is fully reentrant.

This is exactly the pattern a chat backend uses to serve multiple
users from the same process: instantiate one Motor, hand it whatever
RunTask each request brings, fan out with `asyncio.gather` (or just
let the web framework drive concurrency via its own scheduler).

## When to use multiple Motor instances anyway

The single-Motor pattern is the right default. You only want N motors
when each task needs **a radically different MotorConfig** that can't
be expressed via per-task `RunTask` overrides — e.g. different
`upstream_base_url`, different `workspace_root`, different `guardrail`
mode. In that case each motor still services as many concurrent runs
as you want; the multiplicity is on the *config* axis, not on the
*concurrency* axis.

## What you should see

Five reviews classified with their tone and a one-line rationale, plus
a wall-clock duration. Because all five run concurrently against the
same shared motor (and hence the same shared proxy), the wall-clock
should be roughly the time of a single run, not 5×.
