# Output — text, structured, files

Three places the agent's work surfaces in `RunResult`:

| Field | Type | When set |
|---|---|---|
| `result.output_text` | `Optional[str]` | Always (the final assistant text block, free-form) |
| `result.output_data` | `Optional[BaseModel]` | Iff `RunTask.output_schema` was set AND validation succeeded |
| `result.output_files` | `list[OutputFile]` | Always (empty list if the agent wrote nothing) |

## Free-form text

```python
result = await motor.run(RunTask(prompt="Summarise this in 2 sentences."))
print(result.output_text)
```

If the agent took multiple turns the field holds the **last** text block. `result.blocks` has every text/thinking/tool_use/tool_result block in order if you need them.

## Structured output (Pydantic schema)

Pass a Pydantic class as `output_schema`. The motor extracts `model_json_schema()` and forwards it to the CLI's `--json-schema` flag. The CLI **enforces the schema server-side** — enums, ranges, regex patterns, `additionalProperties: false`, nested objects all validated before the response leaves Anthropic.

```python
from pydantic import BaseModel, Field
from typing import Literal

class Verdict(BaseModel):
    severity: Literal["LOW", "MEDIUM", "HIGH"]
    score: int = Field(ge=0, le=100)
    rationale: str = Field(min_length=20, max_length=500)
    cited_paragraphs: list[int]

result = await motor.run(RunTask(
    prompt="Assess this contract...",
    output_schema=Verdict,
))

v: Verdict = result.output_data       # already validated, typed
print(v.severity, v.score)
```

The agent runs its full multi-turn loop (tool calls, reasoning, cross-reference) and **at the end** emits both:

- A free-text final message → `result.output_text`
- A schema-conforming structured payload → `result.output_data` (Pydantic-validated by the motor)

If validation fails (model returned something the CLI's schema check let through but Pydantic rejected — rare), `result.output_data = None` and `result.metadata.is_error = True` with a descriptive `error_reason`.

### Default schema for the motor

```python
motor = Motor(MotorConfig(default_output_schema=Verdict))
# Now every RunTask without an explicit output_schema auto-gets Verdict.
```

Useful for endpoints that always return the same shape:

```python
async def assess(query: str) -> Verdict:
    result = await motor.run(RunTask(prompt=query))
    if result.metadata.is_error:
        raise RuntimeError(result.metadata.error_reason)
    return result.output_data    # already typed Verdict
```

### Don't set `--output-format`

`MotorConfig._build_sdk_options` deliberately never passes `--output-format` to the CLI. It conflicts with the SDK's stream-json IPC and crashes the subprocess. Schema enforcement goes through `--json-schema` only.

## Generated files (`output_files`)

When the agent uses `Write` / `Edit` (or a custom `@tool` that writes to `ctx.outputs_dir`) the file lands at `<run>/agent_cwd/outputs/<rel-path>`. At run end the motor walks that directory and exposes everything as a `list[OutputFile]`:

```python
result = await motor.run(RunTask(
    prompt="Write a markdown report on X.",
    tools=["Read", "Write"],
))

for f in result.output_files:
    print(f.relative_path, f.size, f.mime)
    # report.md 1024 text/markdown
```

`OutputFile` shape:

```python
@dataclass
class OutputFile:
    path: Path             # absolute, inside the run workspace
    relative_path: str     # under outputs/, e.g. "report.md"
    size: int              # bytes
    mime: str              # best-effort guess via mimetypes
    ext: str               # ".md", ".pdf", etc.

    def read_bytes(self) -> bytes: ...
    def read_text(self, encoding="utf-8") -> str: ...
    def copy_to(self, dest: Path | str) -> Path: ...
    def move_to(self, dest: Path | str) -> Path: ...
```

`copy_to` / `move_to` `dest` semantics:

- `dest` is an existing directory → file lands at `dest/relative_path` (parents auto-created)
- `dest` is a non-existing path → treated as the full destination filename (with parent created)

```python
# Persist to a stable location
for f in result.output_files:
    f.copy_to(Path("./generated"))             # → ./generated/<rel-path>

# Or rename + persist
result.output_files[0].copy_to(Path("./final-report.md"))
```

## ⚠️ The transient workspace warning

**`<run>/agent_cwd/` does not stick around.** It can disappear because:

- `motor.clean_runs(...)` was called
- A cron / housekeeping job swept old runs
- The container was torn down (workspace was on tmpfs)
- Someone `rm -rf ~/.sophia-motor/runs/`'d

If `OutputFile.path` matters to you beyond the immediate run, **persist it before** any of the above:

```python
result = await motor.run(...)
for f in result.output_files:
    f.copy_to(Path("./durable-storage"))    # NOW, before workspace is cleaned
# After this, f.path may not exist anymore.
```

## `OutputFileReadyChunk` (during streaming)

If you're streaming, you get a live signal when each `Write` / `Edit` completes:

```python
async for chunk in motor.stream(task):
    if isinstance(chunk, OutputFileReadyChunk):
        print(f"✓ wrote {chunk.relative_path}")
```

**Caveat**: Bash-driven file creation (`echo > outputs/x.txt`) does NOT fire `OutputFileReadyChunk` — the live signal only triggers on `Write`/`Edit`. But Bash-created files still appear in the final `result.output_files` walk. The discovery layer is complete; only the live signal misses Bash. (Workaround would be a snapshot diff of `outputs/` pre/post Bash — not implemented.)

## Live verification

`examples/structured-output/main.py` (output_data) and `examples/file-creation/main.py` (output_files + streaming chunks). Read both for the canonical patterns.

## When uncertain

- "Should I always set `output_schema`?" → No. Use it when the **caller's downstream code needs typed data**. For dumping to logs, `output_text` is fine.
- "Why is `output_data` None when I set the schema?" → Either (a) the model errored mid-run (`is_error=True`), or (b) the schema check rejected the structured payload (rare, check `error_reason`). Look at `result.audit_dir` — `response_NNN.sse` shows the raw upstream output.
- "Why is my file not in `output_files`?" → Did you write it inside `outputs/`? Anything outside is invisible. Verify with `ls $(python -c "from pathlib import Path; print(Path.home() / '.sophia-motor' / 'runs')")`.
