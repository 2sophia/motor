# concurrency

Fan out N independent runs across N motor instances, all in parallel.

## Minimal example

```python
async def classify(review: str) -> ToneVerdict:
    motor = Motor(MotorConfig(console_log_enabled=False))
    result = await motor.run(RunTask(
        prompt=f"Classify the tone of: {review}",
        output_schema=ToneVerdict,
    ))
    return result.output_data

verdicts = await asyncio.gather(*(classify(r) for r in reviews))
```

## Why N motors instead of one?

A single Motor handles one run at a time — its proxy is bound to the
active run for audit-dump tagging. Trying to drive parallel runs
through a shared instance would scramble the audit trail. The clean
pattern is one motor per concurrent task, each with its own
kernel-assigned proxy port. No shared state, no port collisions.

## What this example shows

- 5 product reviews classified in parallel via `asyncio.gather`.
- One Motor per task, each with `console_log_enabled=False` so the
  combined stdout stays readable.
- `output_schema` set so each result comes back as a typed
  `ToneVerdict` instance.
- An explicit `motor.stop()` for each instance at the end (optional —
  process exit would tear down the proxies anyway).

## Run

```bash
pip install sophia-motor
export ANTHROPIC_API_KEY=sk-ant-...
python main.py
```

## What you should see

Five reviews classified with their tone and a one-line rationale, plus
a wall-clock duration. The wall-clock should be roughly the time of a
single run, not 5×, because all runs execute concurrently.
