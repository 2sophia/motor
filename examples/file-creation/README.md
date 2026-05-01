# file-creation

Let the agent **write files**, see them appear live, and persist them
somewhere durable.

## Minimal example

```python
from pathlib import Path
from sophia_motor import Motor, RunTask

motor = Motor()

result = await motor.run(RunTask(
    prompt="Write outputs/report.md with the top-3 products by revenue.",
    tools=["Write"],
    attachments={"sales.json": "..."},
))

# Each file the agent created — `result.output_files` is the discovery layer.
for f in result.output_files:
    print(f.relative_path, f.size, f.mime)

# Persist what you care about — the run workspace is transient.
for f in result.output_files:
    f.copy_to(Path("./generated"))
```

## Run

```bash
pip install sophia-motor
export ANTHROPIC_API_KEY=sk-ant-...
cd examples/file-creation
python main.py
```

## What this example shows

- The `Write` tool, sandboxed by the strict guardrail to
  `<run>/agent_cwd/outputs/` only.
- Live `OutputFileReadyChunk` events as the agent commits each Write
  call (via `motor.stream()`).
- A final `result.output_files: list[OutputFile]` populated by a walk
  of the outputs directory at run end. Covers `Write`, `Edit`, AND
  files created indirectly via `Bash` (e.g. `echo > outputs/x`).
- `OutputFile.copy_to(dest)` to persist the file outside the run
  workspace. `dest` can be an existing directory or a full file path —
  parents are created as needed.
- `OutputFile.read_text()` / `read_bytes()` for the in-memory case.

## ⚠️  The run workspace is transient — persist what you need

Files in `<run>/agent_cwd/outputs/` live until **whatever wipes the
run directory first**:

- `motor.clean_runs(...)` (manual or `motor.clean_runs(keep_last=10)`)
- a cron / sweep / app cleanup that prunes old runs
- `workspace_root` on a tmpfs / container ephemeral volume that resets
  on restart
- a teammate running `rm -rf ~/.sophia-motor/runs/` (the path is
  documented as wipeable)

The audit trail under `<run>/audit/` (request/response dumps + the
`tool_result` block recording what the model wrote) is intended for
defense, not for serving the file back to a user. **If the artifact is
something the user / downstream system needs, copy it now**:

```python
result = await motor.run(task)
for f in result.output_files:
    f.copy_to(Path("/var/storage/reports") / f.relative_path)
    # or upload to S3 / write to a BLOB column / hand to a queue, etc.
```

`copy_to(dir)` keeps the filename. For a full custom destination, pass
a complete path: `f.copy_to(Path("/x/y/renamed.md"))`.

## Bash-created files

Files the agent creates by shelling out (`echo "x" > outputs/y.txt`)
do **not** trigger an `OutputFileReadyChunk` (we'd need a snapshot
diff for that, deferred to a later release). They DO show up in the
final `result.output_files` walk, so the discovery layer stays
complete — only the live UI signal is missing for this case.
