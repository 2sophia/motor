# sophia-motor

Programmable, instanceable agent motor. Wraps the Claude Agent SDK with:

- **Class-based API**: `Motor(config)` → `motor.run(task)` — instanceable from any program
- **Event bus**: `motor.on_event(fn)` / `motor.on_log(fn)` — live observability
- **Proxy gateway** (in-process FastAPI): audit dump per run, strip SDK noise, console log per turn
- **Workspace per run**: isolated cwd under `<workspace_root>/<run_id>/`
- **Provider-agnostic**: default `claude-opus-4-6` via Anthropic API; pluggable

Built for Sophia and RGCI to run reasoning agents under controlled rails — compliance defense, traceability, reproducibility.

## Quick start

```python
import asyncio
from sophia_motor import Motor, MotorConfig, RunTask

async def main():
    config = MotorConfig(api_key="...")  # or read ANTHROPIC_API_KEY from env
    async with Motor(config) as motor:
        @motor.on_event
        async def on_event(event):
            print(f"[event] {event.type}: {event.payload}")

        result = await motor.run(RunTask(
            prompt="Read tests/data/sample.txt and summarize.",
            allowed_tools=["Read"],
        ))
        print(result.output_text)
        print(f"audit dir: {result.audit_dir}")

asyncio.run(main())
```

## Status

Pre-alpha. PoC verticale per validare il pattern motor + proxy + audit. Vedi `docs/design_doc_phase0.md`.
