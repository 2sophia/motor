# interrupt

Cancel an in-flight run mid-execution — the "user clicks stop" pattern.

## Minimal example

```python
async def consume():
    async for chunk in motor.stream(task):
        if isinstance(chunk, DoneChunk):
            print("interrupted:", chunk.result.metadata.was_interrupted)

consumer = asyncio.create_task(consume())
await asyncio.sleep(2)
await motor.interrupt()   # cancels the in-flight run cleanly
await consumer
```

## Run

```bash
pip install sophia-motor
export ANTHROPIC_API_KEY=sk-ant-...
cd examples/interrupt
python main.py
```

## What it does

1. Launches a deliberately long task (read 3 files, then write a multi-
   paragraph analysis).
2. Runs `motor.stream(task)` on one `asyncio` task and a "stop button"
   (`motor.interrupt()`) on another, after a 2.5s delay.
3. Shows the stream finishing cleanly with a terminal `DoneChunk` whose
   `result.metadata.was_interrupted=True` and `is_error=False`.
4. Confirms the audit dump under `<run>/audit/` is preserved — every
   API request/response up to the moment of interruption is on disk.

## `interrupt()` vs `stop()` — they are different

| Method | Scope | Effect |
|---|---|---|
| `await motor.stop()` | **lifecycle** | Shuts the motor down: kills the proxy, releases the port. No more runs. Use on app shutdown. |
| `await motor.interrupt(run_id=None)` | **the run currently in flight** | Aborts the active turn. The motor stays alive and ready for the next `motor.run(...)` / `motor.stream(...)`. |

## The `run_id` argument

`motor.interrupt()` (no argument) means "interrupt whatever is current".

`motor.interrupt(run_id="run-…")` is a **safety check** for race
conditions: in a chat UI the user might click "stop" exactly as their
old run finished and a new one started. Passing the `run_id` they saw
on screen ensures we don't interrupt the wrong run.

```python
# user_message_id corresponds to a specific run we showed in the UI
ok = await motor.interrupt(run_id=expected_run_id)
if not ok:
    # Either no run is active OR the active run isn't the one we expected
    # (already finished, or replaced by a newer one). Idempotent — never
    # raises.
    pass
```

## How it works under the hood

1. `motor.interrupt()` calls `client.interrupt()` on the active SDK client.
2. The Claude CLI subprocess receives the control signal and exits the
   current turn.
3. The `async with ClaudeSDKClient` context exits → `disconnect()` →
   subprocess SIGTERM → TCP close to the local proxy → upstream API
   abort.
4. The motor's stream loop sees the run end, persists `trace.json`,
   yields the terminal `DoneChunk` with `was_interrupted=True`.

The pattern is identical to how `sophia-agent` cancels a chat from the
frontend — `sophia-motor` just exposes it as a clean public method
instead of "close the SSE and trust the cleanup chain".
