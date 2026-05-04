# @2sophia/sophia-motor-skill

A [Claude Code](https://docs.claude.com/en/docs/claude-code/skills) skill that teaches your local Claude how to build agents with [`sophia-motor`](https://github.com/2sophia/motor) — the `@tool` decorator, `MotorConfig`, `RunTask`, subagents, MCP integration, structured output, and the production patterns the maintainers ship.

## Install

```bash
npm install -g @2sophia/sophia-motor-skill
```

Then symlink (or copy) it into your Claude Code skills folder:

```bash
SKILL_DIR=$(npm root -g)/@2sophia/sophia-motor-skill
mkdir -p ~/.claude/skills
ln -s "$SKILL_DIR" ~/.claude/skills/sophia-motor
```

Verify Claude Code sees it:

```bash
ls ~/.claude/skills/sophia-motor/SKILL.md
```

## What the skill does

When you ask your Claude (in Claude Code) to write Python code that uses `sophia-motor`, it loads `SKILL.md` and follows the conventions documented inside — including the **golden rule**: when uncertain about a field, default, or signature, **read the installed source** of the `sophia-motor` package directly rather than guessing.

The skill covers:

- Installation, `.env`, first run
- `Motor` / `MotorConfig` / `RunTask` API + lifecycle + env-var cascade
- Built-in tools (Read, Glob, Bash, …) + the strict guardrail
- **`@tool` decorator** + `ToolContext` for Python functions exposed as in-process MCP tools
- `SKILL.md` mounting (the agent skills feature)
- Attachments (hard-link / symlink quirks)
- Structured output (`output_data`) + generated files (`output_files`)
- Streaming + every chunk type
- `Chat` + `motor.console()` + `motor.interrupt()`
- Subagents (inheritance + explicit-restrict patterns)
- Multi-provider adapters (Anthropic / vLLM / custom)
- Observability — events, logs, audit dump
- Production patterns — singleton motor, concurrency, chat backends
- Reference tables — full field/env-var/event-type lookup
- 10 known gotchas + recovery recipes

## Update

```bash
npm update -g @2sophia/sophia-motor-skill
```

The symlink picks up the new content automatically.

## Uninstall

```bash
rm ~/.claude/skills/sophia-motor
npm uninstall -g @2sophia/sophia-motor-skill
```

## Versioning

Skill `version` tracks the `sophia-motor` Python package version it was built against. `@2sophia/sophia-motor-skill@0.5.0` is built against `sophia-motor==0.5.0`. When the installed Python version differs, the skill instructs Claude to inspect the installed source for ground truth — fail-safe by design.

## License

MIT — see [LICENSE](https://github.com/2sophia/motor/blob/main/LICENSE) in the main repo.
