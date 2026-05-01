# console

An **interactive REPL** bound to a pre-configured motor. Type a prompt,
watch the agent stream live (thinking, tool calls, file outputs), type
the next prompt. Slash commands for everything you'd reach for after a
run.

## ⚠️ Needs the [console] extras

```bash
pip install sophia-motor[console]   # tira rich + prompt-toolkit
```

Without these, `motor.console()` raises `ImportError` with the install
hint. The base `pip install sophia-motor` deliberately stays lean (5
deps, no rich) since most callers use `motor.run()` / `motor.stream()`
programmatically.

## Minimal example

```python
import asyncio
from sophia_motor import Motor, MotorConfig

async def main():
    motor = Motor(MotorConfig(
        default_system="You are a concise data analyst.",
        default_tools=["Read", "Glob", "Write"],
        default_attachments={"data.json": "..."},
    ))
    await motor.console()    # blocks until /exit

asyncio.run(main())
```

The console **uses every `default_*`** you set on `MotorConfig` —
tools, attachments, skills, system, max_turns, output_schema — so the
user just types prompts. No per-turn boilerplate.

## Run

```bash
pip install sophia-motor[console]
export ANTHROPIC_API_KEY=sk-ant-...
cd examples/console
python main.py
```

## What you can do inside the console

| Slash command | Effect |
|---|---|
| `/help`        | Show command help |
| `/exit`, `/q`  | Quit (also `Ctrl+D`) |
| `/files`       | List `output_files` of the last run + persist hint |
| `/audit`       | Print the audit dump path of the last run |
| `/clear`       | Clear screen |

| Key | Effect |
|---|---|
| `Up` / `Down`  | Prompt history (per-session) |
| `Tab`          | Autocomplete the slash commands |
| `Ctrl+C`       | Interrupt the running task — does NOT exit the console |
| `Ctrl+D`       | Exit |
| `Esc Enter`    | Submit a multiline prompt |

## What you should see

A boxed header with the motor's config (model / upstream / adapter /
tools / skills / system), a blinking `>` prompt, and on each input:

```
> What files are in attachments/?

  thinking ▸ Let me list the files…
  [Glob] **/*
    ✓ index.txt ⏎ sales-2026-q1.json ⏎ sales-2026-q2.json …

  answer ▸ The attachments folder contains an index.txt and three
  quarterly sales JSON files (Q1, Q2, Q3) plus two notes.

  ── ok · turns=2 tools=1 cost=$0.0042 4.1s ──

>
```

A run that writes files emits live `✓ wrote outputs/<file>` lines as
each Write commits, and `/files` afterwards lists the persistable
results.

## Why this is also useful for testing

We use `motor.console()` ourselves to validate the agent loop after a
refactor: pre-configure tools/system, talk to it, watch the stream
behave. Faster than writing an ad-hoc smoke script and the live UI
makes regressions obvious (text drops, tool input doesn't stream,
chunks misordered, …).
