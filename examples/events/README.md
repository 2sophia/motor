# events

Listen to the agent in flight. Every tool call, every assistant message,
every proxy request emits a structured event. This is how you build
your own logging, metrics, telemetry, or live UI on top of the motor.

## Minimal example

```python
from sophia_motor import Motor, MotorConfig, RunTask

motor = Motor(MotorConfig(console_log_enabled=False))

@motor.on_event
async def on_event(event):
    print(f"[{event.type}] {event.payload}")

@motor.on_log
async def on_log(rec):
    print(f"[{rec.level}] {rec.message}")

await motor.run(RunTask(prompt="Pick a tool, use it, then summarize."))
```

## What this example shows

- `@motor.on_event` to subscribe to structured events.
- `@motor.on_log` to subscribe to leveled log records.
- A handler that prints a one-line summary per event and tallies an
  event-type histogram across the run.
- `console_log_enabled=False` so the user controls stdout entirely.

## Event types you'll see

| Event type        | When it fires                                    |
|-------------------|--------------------------------------------------|
| `run_started`     | Once, when `motor.run()` begins                  |
| `proxy_request`   | Each HTTP call to the upstream Anthropic API     |
| `tool_use`        | Each time the model calls a tool                 |
| `tool_result`     | Each tool's response (or error)                  |
| `assistant_text`  | Each chunk of free-form assistant text           |
| `thinking`        | Each extended-thinking block (when enabled)      |
| `proxy_response`  | After the upstream API responds                  |
| `result`          | Once, with the final aggregated metadata         |

## Run

```bash
pip install sophia-motor
export ANTHROPIC_API_KEY=sk-ant-...
python main.py
```

## What you should see

A live trace of the run with one line per event, ending with the final
answer and a histogram of event types and tools used.
