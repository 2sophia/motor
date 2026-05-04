# sophia-motor — Claude Code skill

A meticulous skill that teaches your local [Claude Code](https://docs.claude.com/en/docs/claude-code/) how to build agents with [`sophia-motor`](https://github.com/2sophia/motor) — the `@tool` decorator, `MotorConfig`, `RunTask`, subagents, in-process MCP integration, structured output, and the production patterns the maintainers ship.

## Install (recommended) — via Claude Code marketplace

Inside a Claude Code session:

```
/plugin marketplace add 2sophia/motor
/plugin install sophia-motor
```

Claude Code clones the repo, reads [`.claude-plugin/marketplace.json`](../../.claude-plugin/marketplace.json), and registers the skill. Update later with `/plugin update`. Zero filesystem ceremony.

## Install (alternative) — via npm

If you prefer npm-driven installs:

```bash
npm install -g @2sophia/sophia-motor-skill
SKILL_DIR=$(npm root -g)/@2sophia/sophia-motor-skill
mkdir -p ~/.claude/skills && ln -s "$SKILL_DIR" ~/.claude/skills/sophia-motor
```

The marketplace path is canonical and avoids the symlink dance — prefer it unless you have a specific reason for npm.

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

## Versioning

The skill version tracks the `sophia-motor` Python package version it was built against. `0.5.0` is built against `sophia-motor==0.5.0`. When the installed Python version differs, the skill instructs Claude to inspect the installed source for ground truth — fail-safe by design.

## Update

- Marketplace: `/plugin update` inside Claude Code
- npm: `npm update -g @2sophia/sophia-motor-skill` (the symlink picks up the new content automatically)

## Uninstall

- Marketplace: `/plugin uninstall sophia-motor`
- npm:
  ```bash
  rm ~/.claude/skills/sophia-motor
  npm uninstall -g @2sophia/sophia-motor-skill
  ```

## License

MIT — see [LICENSE](https://github.com/2sophia/motor/blob/main/LICENSE) in the main repo.
