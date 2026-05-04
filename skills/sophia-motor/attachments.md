# Attachments

Hand the agent a folder of files (or inline content) to read at run time. Motor materializes them into `<run>/agent_cwd/attachments/` so `Read` / `Glob` / `Grep` discover them under the sandboxed cwd.

## Three accepted forms

`RunTask.attachments` (or `MotorConfig.default_attachments`) takes:

| Form | Example | Result on disk |
|---|---|---|
| `Path` to a real file | `Path("/data/policy.pdf")` | hard-link at `attachments/policy.pdf` |
| `Path` to a real directory | `Path("/data/policy/")` | hard-link tree at `attachments/policy/...` |
| `dict[str, str]` (relative-path → text content) | `{"note.txt": "ciao"}` | real file at `attachments/note.txt` |
| `dict` with sub-paths | `{"sub/note.txt": "x"}` | real file at `attachments/sub/note.txt` |
| singleton (any of the above) | `attachments=Path("/data/")` | normalized to `[Path("/data/")]` |
| **mixed list** | `[Path("/a"), {"b.txt": "..."}]` | both materialized |

```python
RunTask(prompt="Summarise these.", attachments=[
    Path("/var/exports/report.pdf"),                    # real file → hard-link
    Path("/var/exports/policies/"),                     # real dir   → hard-link tree
    {"context.txt": "user is on enterprise tier"},       # inline    → file written
])
```

## Why hard-link, not symlink (THE quirk)

The SDK's `Glob` tool delegates to `ripgrep --files --glob <pattern> --no-ignore --hidden`. Ripgrep **does not follow symlinks** by default (would need `-L` / `--follow`, which the CLI doesn't pass). So:

- Symlinked files → invisible to `Glob`. The model says "I don't see anything in attachments/".
- Hard-linked files → share inode with the source → ripgrep sees them as regular files → `Glob` works.

The motor uses hard-link by default. Cross-filesystem (`EXDEV` errno) → fallback to **symlink**, in which case `Glob` won't find them. Solution when the source is on a different filesystem: pass the absolute source path in the prompt, so the model uses `Read` directly without needing `Glob` discovery.

The bug was reported upstream: <https://github.com/anthropics/claude-code/issues/16507>. Until they pass `-L` to ripgrep, the hard-link workaround is the safe path.

## Pre-flight validation (errors before tokens are spent)

Materialization happens **before** the SDK subprocess starts. Bad inputs raise immediately:

| Cause | Exception |
|---|---|
| Path doesn't exist | `FileNotFoundError` |
| Path is neither file nor directory (socket, fifo, ...) | `ValueError` |
| Caller can't read the path | `PermissionError` |
| dict key is absolute (`/foo/bar`) | `ValueError` |
| dict key contains `..` | `ValueError` (escape attempt) |
| dict value isn't a `str` | `TypeError` |
| Two list entries materialize to the same destination | `ValueError` (conflict) |

Catch these in your application's input layer; surface to the user before charging tokens.

## What `input.json` records

`<run>/input.json` ships a `manifest` of attachments — for each entry, either `<inline>` (when written from a dict) or `→ /abs/source/path (link)` (when linked from a Path). Auditors reading the run know exactly what the model could read.

## Default attachments + per-task additions

```python
motor = Motor(MotorConfig(
    default_attachments=Path("./shared-context/"),    # always present
))

# This task replaces the default (full replacement, not merge):
RunTask(prompt="...", attachments=Path("./special-files/"))

# To extend, build the list yourself:
RunTask(prompt="...", attachments=[
    motor.config.default_attachments,
    Path("./special-files/"),
])
```

## What the agent sees

Inside the run's cwd:

```
<run>/agent_cwd/
├── attachments/              ← what you put here
│   ├── policy.pdf            ← hard-link to source
│   └── context.txt           ← real file (was a dict entry)
└── outputs/                  ← what the agent writes
```

The model is told (via the cwd policy) to use **relative paths**: `Read(file_path="attachments/policy.pdf")`, never absolute. The motor's `tool_description_overrides` for `Read` enforces this.

## Live verification

`examples/attachments/` — hands the agent a real folder, asks for a typed summary, the agent uses `Glob` + `Read` to discover/parse files. Look at the example's `main.py` and `files/` for the canonical layout.

## When uncertain

- Hard-link vs symlink behaviour? Check `os.link(src, dst)` for the source code path: `_python_type_to_json_schema` is unrelated; the materialization is in `motor.py`. Search `_materialize_attachments` (it's around line 700-800 in `motor.py`).
- "I'm seeing the file in `attachments/` but `Glob` doesn't find it." → cross-filesystem fallback to symlink. Move the source onto the same filesystem as `workspace_root`, or pass the path in the prompt.
- Pre-flight error you don't recognize? Read `_persist_input` in `motor.py` for the exact validation order.
