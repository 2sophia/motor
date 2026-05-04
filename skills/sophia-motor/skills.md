# SDK Skills (`SKILL.md` mounting)

> **Don't confuse:** *this* file (the one you're reading) is a Claude Code skill that teaches developers about `sophia-motor`. The current sub-doc is about the *other* kind of skill — the SDK feature where a folder with `SKILL.md` is mounted into the agent's runtime so it can be invoked at task time.

## What an SDK skill is

A folder with a `SKILL.md` file at its root. The frontmatter declares `name` + `description`; the body tells the agent **what to do when this skill is invoked**. Optional helper scripts ride along (`scripts/discount.py`, etc.) and can be called from `Bash` inside the skill.

```
my-skill/
├── SKILL.md          # ← required. frontmatter + agent instructions
└── scripts/          # ← optional. invokable via Bash from the skill
    └── helper.py
```

`SKILL.md` frontmatter:

```yaml
---
name: skill-name-the-model-sees
description: Trigger phrase + when-to-invoke. The model uses this to decide whether to call this skill at all.
---
```

The body is plain Markdown, addressed to the model in second person.

## Mounting skills on a `Motor` / `RunTask`

```python
from pathlib import Path
from sophia_motor import Motor, MotorConfig, RunTask

motor = Motor(MotorConfig(
    default_skills=Path("./skills_local/"),    # folder containing skill subdirectories
    default_tools=["Skill", "Bash"],            # need both — Skill to invoke, Bash to run helpers
))
```

The motor walks the folder, looks at every direct subdirectory that has a `SKILL.md`, and symlinks each one into `<run>/.claude/skills/<skill_name>/`. The CLI subprocess then discovers them via `CLAUDE_CONFIG_DIR`.

Per-task override is the same pattern:

```python
RunTask(prompt="...", skills=Path("./other-skills/"))      # full replacement
```

## Multiple source folders

`skills` accepts a `list[Path | str]`:

```python
RunTask(prompt="...", skills=[
    Path("./program-specific-skills/"),
    Path("./org-shared-skills/"),
])
```

Useful for layering: program-specific first, then organisation-wide. **Name conflicts across folders raise `ValueError`** at run construction — you can't have two skills with the same `name`.

## Opt-out per task

```python
RunTask(prompt="...", disallowed_skills=["heavy-skill", "draft-only"])
```

Prevents specific skills from being mounted for this run, even if they're in the source folder.

## What the model sees

Skill catalogue is **not** in the system prompt. The CLI injects it as a `<system-reminder>` block in the user message from turn 2 onwards:

```
<system-reminder>
The following skills are available for use with the Skill tool:

- discount: Apply a tier-based customer discount...
- math: Compute numeric calculations via Python...
</system-reminder>
```

So:
- The agent only learns skills exist after the **first user→assistant→tool→user roundtrip**.
- The motor's `proxy_strip_user_system_reminders=True` (default) does **selective** stripping that **preserves** skill catalogue blocks. Don't disable this — turning off the strip is fine, but custom strippers must keep skill reminders.
- The CLI ships a set of **bundled skills** (`update-config`, `simplify`, `loop`, `claude-api`, `init`, `review`, `security-review`, …) that are always present. The motor passes `ClaudeAgentOptions.skills=[<our names>]` as a hard whitelist so the model only sees yours. Pass `default_tools=["Skill"]` with no `default_skills=...` and you'll get an empty skill catalogue (no bundled noise).

## Helper scripts inside a skill

Bundle deterministic logic inside the skill folder, invoke via Bash:

```
my-skill/
├── SKILL.md
└── scripts/
    └── compute.py
```

```markdown
<!-- in SKILL.md body -->
When the user asks for X, run:
    python ${SKILL_DIR}/scripts/compute.py <args>

Do NOT try to compute X in your head — even small chains of arithmetic drift.
Always invoke the helper.
```

`SKILL_DIR` is set by the SDK when the skill is invoked. The script is part of the skill — your domain logic lives in code, not in the prompt.

## When to choose skills vs `@tool`

| Scenario | Use this |
|---|---|
| Domain logic that benefits from being **hidden** from the model (proprietary discount table, internal scoring) | **Skill** with helper script — the percentages live in the script, not in the prompt the model can read |
| Logic where the model needs to see the **schema** to know how to invoke (typed args, structured output) | **`@tool`** — Pydantic schema is the contract |
| Procedural multi-step instructions ("when X, do A then B then check C") | **Skill** — Markdown body is great for procedure |
| Single Python function with clear input/output | **`@tool`** — less ceremony |

You can mix both freely: `default_tools=["Skill", "Bash", my_python_tool]` + `default_skills=Path("./skills_local/")`.

## Live verification

`examples/skills/` ships three skills:
- `say-hello` — minimal, instructional
- `python-math` — invokes `python -c` for arithmetic ("compute, don't guess")
- `apply-discount` — bundled helper script with proprietary discount table

Read `examples/skills/main.py` for the canonical mount pattern.

## When uncertain

- Frontmatter format? Look at `examples/skills/skills_local/*/SKILL.md` — three real working examples.
- "Why isn't the agent calling my skill?" → check the audit dump's first user message for the `<system-reminder>` block; if your skill name isn't listed, the mount didn't take. Most common cause: missing `SKILL.md` (case-sensitive) inside the subdirectory, or the subdirectory itself is missing.
- Conflict with bundled skill names? The motor passes `ClaudeAgentOptions.skills=[...]` as a hard whitelist, but if you name your skill `review` (also a bundled name), the bundled one is what's exposed unless explicitly filtered. Rename your skills to avoid collision.
