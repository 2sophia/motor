# Troubleshooting — the 10 known gotchas

Symptoms you'll actually see in logs / errors, mapped to root causes. Read top-down — most are hit during first hours of using the motor.

## 1. `RuntimeError: ANTHROPIC_API_KEY not set...`

**Cause**: none of the resolution-cascade sources matched.

**Fix**: pick one of (in order of preference):
- `export ANTHROPIC_API_KEY=sk-ant-...`
- Add `ANTHROPIC_API_KEY=sk-ant-...` to `./.env` in the project root (current working directory at `Motor()` time)
- `Motor(MotorConfig(api_key="sk-ant-..."))` (test only — never commit)

Verify with `python -c "import os; print(bool(os.getenv('ANTHROPIC_API_KEY')))"`.

## 2. Glob/Grep doesn't find files in `attachments/`

**Cause**: cross-filesystem materialization (`EXDEV` errno) → motor falls back from hard-link to symlink → ripgrep doesn't follow symlinks (no `-L` flag passed by the CLI).

**Fix options**:
- Move the source onto the same filesystem as `MotorConfig.workspace_root` (typically `~/.sophia-motor/`).
- Pass the source path directly in the prompt: `"Read /var/data/policy.pdf and ..."` — `Read` doesn't need `Glob` discovery.
- Inline the content as `dict[str, str]` instead of `Path` if it's small.

Upstream issue: <https://github.com/anthropics/claude-code/issues/16507>.

See [`attachments.md`](attachments.md) for the why.

## 3. `RuntimeError: Subagents require the Agent tool to be reachable...`

**Cause**: declared `default_agents={...}` (or `agents={...}`) but didn't whitelist `"Agent"` in `tools`. Motor refuses to silently auto-fix.

**Fix** (the second move):
```python
MotorConfig(
    default_agents={"my_agent": AgentDefinition(...)},
    default_tools=["Read", ..., "Agent"],   # ← required
)
```

The motor's conflict-resolution then drops `Agent` from `default_disallowed_tools` for runs that whitelist it. **Don't** wipe the whole disallowed list (`default_disallowed_tools=[]`) — you'd unblock 17+ tools.

See [`subagents.md`](subagents.md).

## 4. `ToolUseDeltaChunk` never fires when running on vLLM/Qwen

**Cause**: vLLM's Anthropic-compat endpoint with Qwen typically does NOT emit `input_json_delta` server events. It's an upstream limitation, not a motor bug.

**Fix**: nothing to do — design your UI to use `ToolUseStartChunk` + `ToolUseFinalizedChunk` (which fire normally on vLLM) and skip the live "tool args streaming in" affordance for the vLLM path. Other chunk types (`TextDelta`, `ToolUseStart/Complete/Finalized`, `ToolResult`) all work.

See [`adapters.md`](adapters.md) "vLLM caveat" and [`streaming.md`](streaming.md) "vLLM upstream caveat".

## 5. Sessions / chats deeply nested under `<cwd>/.runs/<run_id>/agent_cwd/`

**Cause**: `MotorConfig.workspace_root` was set to a path **inside a repo** (any folder whose ancestors contain `.git/`, `pyproject.toml`, or `package.json`). The bundled Claude CLI does upward project-root discovery and re-paths its session/backup state into a deeply-nested fallback location.

**Fix**: move `workspace_root` outside any repo:
```python
MotorConfig(workspace_root=Path("/home/me/.sophia-motor/runs"))   # default — outside any repo
# or
MotorConfig(workspace_root=Path("/data/runs"))                    # in container with /data mounted
```

No env var (including `CLAUDE_PROJECT_DIR`) overrides this CLI behaviour. The defaults work — just don't override workspace_root to a location inside a repo.

See [`installation.md`](installation.md).

## 6. `result.output_files` is always empty even though I told the agent to write a file

**Possible causes** (debug in this order):

1. The agent didn't actually invoke `Write`. Check `result.blocks` for a `tool_use` block with `name: "Write"`. If absent, the model decided not to write — adjust the prompt.
2. The agent wrote OUTSIDE `outputs/`. The default guard refuses Writes outside `<cwd>/outputs/`, but for completeness check `result.output_files` is built from `<run>/agent_cwd/outputs/` walk. If the file is e.g. at `<run>/agent_cwd/random.md`, it's invisible to the discovery layer.
3. The agent used `Bash` (`echo > outputs/x.md`) — the **live** `OutputFileReadyChunk` doesn't fire for Bash creation, but the **final** `result.output_files` walk does. So the file should be in there at run end. If it's not, double-check the Bash command actually ran (look at `tool_result` for that Bash invocation).
4. You're checking `output_files` BEFORE awaiting the run — only valid post-`await motor.run(...)`.

Debug:
```bash
ls -la $(python -c "from pathlib import Path; print(Path.home() / '.sophia-motor' / 'runs')") | tail -3
# pick the latest run dir
ls <run-dir>/agent_cwd/outputs/
```

See [`output.md`](output.md).

## 7. `OutputFile.path` doesn't exist when I try to read it later

**Cause**: the workspace is **transient**. `motor.clean_runs()`, cron sweeps, container teardowns, `rm -rf ~/.sophia-motor/runs/`, tmpfs resets — any of these wipe `<run>/agent_cwd/outputs/`.

**Fix**: persist with `copy_to` BEFORE the workspace dies:
```python
result = await motor.run(...)
for f in result.output_files:
    f.copy_to(Path("./durable-storage"))
# Now your durable copy is independent of the run workspace.
```

See [`output.md`](output.md) "the transient workspace warning".

## 8. `output_data` is `None` even though I set `output_schema`

**Cause** (in order):
- `result.metadata.is_error == True` → check `result.metadata.error_reason`. If the model errored mid-run, no structured output was produced.
- The CLI's server-side schema validation passed but Pydantic's stricter validation rejected the payload. Rare. Check the audit dump's `response_NNN.sse` for the actual structured payload the model produced.
- You forgot to await the result.

**Fix**: surface `is_error` to the caller:
```python
result = await motor.run(RunTask(prompt=..., output_schema=Verdict))
if result.metadata.is_error:
    raise RuntimeError(result.metadata.error_reason)
return result.output_data       # only here is it guaranteed non-None
```

## 9. Agent says "I can't access /etc/passwd" / refuses to run a command I expected to work

**Cause**: strict guardrail (default) blocked a path or a Bash command. The guard returns `{"decision": "block", "reason": "..."}` and the SDK passes the reason to the model — the model gracefully says it's sandboxed.

**Fix**: pick the right tier:
- If the path is INSIDE the run workspace → use a relative path (`Read("attachments/foo.pdf")`, not `Read("/abs/path/attachments/foo.pdf")`).
- If the agent legitimately needs git/docker/pip → `MotorConfig(guardrail="permissive")`. Don't reach for `permissive` to "make tests pass" without thinking.
- For full disable (trusted prompts only, ephemeral container) → `guardrail="off"`.

See [`security.md`](security.md).

## 10. `python_tool_call` event has `ok=False` and the model says "tool failed"

**Cause**: your `@tool` function raised an exception. The dispatcher caught it (so the run continues), turned it into `is_error=True` with the truncated traceback as text, and emitted the event with `ok=False`.

**Fix**:
- Read the audit dump: `cat <run>/audit/tool_<name>_NNN.json` shows `input`, `error`, `duration_ms`. The `error` field has the exception type + message.
- Common causes: input validation failure (Pydantic `model_validate` raised — adjust your input model OR the prompt), unexpected `None` from a downstream service, network call without retries.
- Patterns:
  ```python
  @tool
  async def my_tool(args: Inp) -> Out:
      """..."""
      try:
          return await downstream.fetch(args.id)
      except DownstreamError as e:
          # Make the failure model-friendly — the message goes to the agent
          raise ValueError(f"Customer {args.id} not found in store: {e}") from e
  ```

The agent sees the error message and can react (retry with different args, ask the user, give up gracefully).

## Bonus — "the proxy didn't start"

**Symptom**: `RuntimeError: sophia-motor proxy did not start within 5s`.

**Cause**: explicit `proxy_port=...` and the port is occupied by something else.

**Fix**: drop the `proxy_port=` kwarg → kernel-assigned free port. Only set `proxy_port` if you really need a stable URL (debugging with `curl`, fixed firewall rule).

## Bonus — `ImportError` from `motor.console()`

**Symptom**: `ImportError: Motor.console() requires the [console] extras...`

**Fix**:
```bash
pip install "sophia-motor[console]"
```

Adds `rich` + `prompt-toolkit` (~150 KB). Lazy import; the base motor stays lean.

## Bonus — Force-pushed history broke something

If you `git filter-branch` or `git rebase` the motor's history and end up with the wrong tag pointing at the wrong commit, here's the recovery sequence (assuming `v0.5.0`):

```bash
# Delete remote release + tag
gh release delete v0.5.0 --repo 2sophia/motor --yes
git push origin --delete v0.5.0

# Re-tag at the correct commit
git tag -d v0.5.0
git tag v0.5.0 <correct-commit-hash>
git push origin v0.5.0

# Recreate release from notes file
gh release create v0.5.0 --repo 2sophia/motor --notes-file /path/to/notes.md
```

PyPI: a published version is **immutable** — you can't re-upload the same version with different content. Bump to the next patch / minor instead.

## When uncertain

The motor is pre-1.0 and ships rapidly. **When something differs from this skill, trust the source** — `python -c "import sophia_motor, os; print(os.path.dirname(sophia_motor.__file__))"` and `cat`/`grep` the file. The skill is stale-by-design.
