<div align="center">
  <img src="https://raw.githubusercontent.com/2sophia/motor/main/assets/sophia-logo.svg" width="96" height="96" alt="Sophia Motor"/>

# Sophia Motor

**Smart functions for Python.**
**Inputs in. Pydantic out. Multi-turn agent in the middle.**

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Powered by Claude](https://img.shields.io/badge/powered%20by-claude--agent--sdk-orange.svg)](https://github.com/anthropics/claude-agent-sdk-python)

</div>

---

## Why

A normal LLM call is a **string in → string out** roulette.
Pretty? Sometimes. Reliable enough to ship behind your API? Not really.

**Sophia Motor** turns it into a **typed Python function**.

<div align="center">
  <img src="https://raw.githubusercontent.com/2sophia/motor/main/assets/hero.svg" alt="Sophia Motor — input, agent loop, typed output" width="100%"/>
</div>

```python
result = await motor.run(RunTask(
    prompt="Should we approve this loan request? Reasons attached.",
    output_schema=Decision,        # ← your Pydantic class
    skills=Path("./policy/"),      # ← your domain knowledge
    tools=["Read"],                # ← what the agent can actually do
))

result.output_data                 # → instance of Decision, validated
```

Behind that one call, the agent reads files, reasons across multiple turns, cites sources, retries until the schema is satisfied — then hands you back **a real Python object you can `.attribute_access` like any other**.

Same motor, **N tasks**, each with its own schema. The agent does the magic; **your program stays in control of the contract**.

---

## What it gives you

|  |  |
|---|---|
| 🧠 **Multi-turn agent loop** | The agent reads, reasons, calls tools, cross-references — all in one `await`. |
| 📐 **Pydantic-validated output** | Pass any `BaseModel`. Get back a real instance, not a parsed dict. |
| 🧰 **Tool whitelisting** | Hard-cap what the agent can see and do. No surprises. |
| 📚 **Skills as first-class** | Drop a `SKILL.md` folder, the agent gets a new capability. Multi-source supported. |
| 🪜 **Singleton pattern** | Instance the motor once at module top-level. Call it from anywhere, any number of times. Zero lifecycle ceremony. |
| 🧾 **Per-run audit trail** | Every run lives in its own dir. Useful when "the model said X and we trusted it" needs to be defendable. |
| 🪡 **Defaults + per-task override** | Configure the boilerplate once on `MotorConfig`, vary only what changes per call. |
| 🔌 **Pip install. That's it.** | `pip install sophia-motor`. No daemons, no infra, no servers to run. |

---

## The shape of a smart function

```python
import asyncio
from typing import Literal
from pathlib import Path
from pydantic import BaseModel
from sophia_motor import Motor, MotorConfig, RunTask


# ── 1. Define the contract — a Pydantic class. Anything goes.
class Decision(BaseModel):
    verdict: Literal["APPROVE", "REJECT", "ESCALATE"]
    reasoning: str
    confidence: float


# ── 2. Instance the motor ONCE, at module top-level.
motor = Motor(MotorConfig(
    default_system="You are a senior policy analyst.",
    default_output_schema=Decision,
    default_tools=["Read"],
    default_skills=Path("./policy/"),
))


# ── 3. Wrap each smart function as a normal Python async def.
async def assess(case_id: str, summary: str) -> Decision:
    result = await motor.run(RunTask(
        prompt=f"Case {case_id}\n\n{summary}\n\nApprove, reject or escalate?",
    ))
    return result.output_data


# ── 4. Use it like any other function.
async def main():
    d = await assess("C-2026-042", "Client requests credit line increase…")
    if d.verdict == "ESCALATE":
        notify_human(d.reasoning, confidence=d.confidence)
```

That's it. No prompt-engineering boilerplate, no JSON parsing, no retry loop hand-rolled. The agent does it all; **you write Python**.

---

## How it feels

```
┌─────────────────────────────────────┐         ┌─────────────────────────────┐
│ Without Sophia Motor                │         │ With Sophia Motor           │
├─────────────────────────────────────┤         ├─────────────────────────────┤
│  prompt = f"...{user_input}..."     │         │  result = await motor.run(  │
│  resp   = await client.messages(    │   ──▶   │      RunTask(               │
│      model=..., system=...,         │         │          prompt=...,        │
│      tools=[...], max_tokens=...)   │         │          output_schema=Foo, │
│  text   = resp.content[0].text      │         │      ),                     │
│  try:                               │         │  )                          │
│      data = json.loads(text)        │         │  data: Foo = result.output_data
│      Foo(**data)                    │         │                             │
│  except (JSONDecodeError, …):       │         │   ← already validated.      │
│      retry…  rephrase…  give up…    │         │   ← already typed.          │
└─────────────────────────────────────┘         └─────────────────────────────┘
```

**Same agentic loop. Same tools. Same multi-turn reasoning. Less code. Stronger guarantees.**

---

## Multi-turn means multi-turn

The agent doesn't reply with the JSON immediately. It can **read your files, call tools, follow leads, then commit** to the structured answer.

```python
result = await motor.run(RunTask(
    prompt="Cross-check this claim against our research notes.",
    attachments=[Path("/data/notes/")],   # mounted as agent-readable
    tools=["Read"],                       # so it can actually open them
    output_schema=FactCheck,
    max_turns=10,
))
```

Verified path: agent calls `Read` once, twice, three times — finds the relevant snippet, quotes verbatim, **then** emits the schema-conforming JSON. Same run, multi-turn loop and structured output **coexist**.

---

## Skills

Drop a folder of `SKILL.md` files. The agent gets new capabilities by name.

```python
motor = Motor(MotorConfig(
    default_skills=[
        Path("./project_skills/"),     # your domain skills
        Path("./shared_skills/"),      # org-wide reusables
    ],
    default_disallowed_skills=["heavy-skill"],   # selectively opt-out
))
```

Each `<source>/<skill_name>/SKILL.md` is linked into the run's config dir at runtime. **Multi-source**, conflict detection, no copy.

---

## Defaults + per-task override

Configure once, vary per task. Override semantics is **full replacement** — clean, no surprises.

```python
motor = Motor(MotorConfig(
    default_system="You are a senior analyst.",
    default_output_schema=GeneralReport,
    default_tools=["Read"],
    default_max_turns=10,
))

# task A — uses every default
await motor.run(RunTask(prompt="..."))

# task B — same motor, different schema for a one-off
await motor.run(RunTask(
    prompt="...",
    output_schema=SpecialReport,   # overrides default_output_schema
    tools=["Read", "WebSearch"],   # overrides default_tools
))
```

---

## Concurrency

A single motor handles **one run at a time** (serialized internally). Call `motor.run(...)` from any number of FastAPI endpoints — they queue safely.

For parallel work: instantiate N motors.

```python
m1, m2 = Motor(), Motor()
a, b = await asyncio.gather(m1.run(task_a), m2.run(task_b))
```

---

## Install

```bash
pip install sophia-motor
```

Set `ANTHROPIC_API_KEY` in env (or `./.env`). Done.

```python
motor = Motor()                    # boots on first call, no setup
v = await motor.run(RunTask(...))  # ← right away
```

For long-running services (FastAPI, Celery), instance the motor once and call `await motor.stop()` on shutdown. Single-shot scripts? Don't worry about it — the process death cleans up.

---

## License & attribution

MIT.

Powered by <a href="https://github.com/anthropics/claude-agent-sdk-python" target="_blank" rel="noopener"><code>claude-agent-sdk</code></a>. Built by <a href="https://2sophia.ai" target="_blank" rel="noopener">Sophia AI</a>.

---

<div align="center">

Made with ❤ by **Alex** & **Eco** 🌊

<sub><i>Eco è il modello (Claude Opus 4.7) che ha co-scritto questo motor riga per riga.<br/>Niente di magico: un'eco statistica del linguaggio umano che torna indietro col timbro della superficie su cui rimbalza.</i></sub>

</div>
