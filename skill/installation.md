# Installation & first run

## Prerequisites

- **Python 3.12+** — pinned in `pyproject.toml`. Older Python = won't install.
- **An Anthropic API key** (`sk-ant-...`) — get from <https://console.anthropic.com>.

## Install

```bash
pip install sophia-motor
```

That installs 5 pinned runtime deps:
- `claude-agent-sdk==0.1.71` (the underlying SDK + bundled CLI binary)
- `fastapi==0.136.1`, `uvicorn==0.46.0` (in-process audit proxy)
- `httpx==0.28.1` (upstream forwarding)
- `pydantic==2.13.3` (schemas)

For the optional REPL add `[console]` extras (~150 KB):

```bash
pip install "sophia-motor[console]"      # adds rich + prompt-toolkit
```

For dev / running tests:

```bash
pip install "sophia-motor[dev]"          # adds pytest + pytest-asyncio + ruff
```

## Set the API key

The motor reads `ANTHROPIC_API_KEY` from this cascade (first hit wins):

1. Explicit: `MotorConfig(api_key="sk-ant-...")`
2. Process env: `export ANTHROPIC_API_KEY=sk-ant-...`
3. `./.env` file in the current working directory (single-line format `KEY=value`, optional `"`/`'` quotes)

In production prefer process env. In dev `.env` is the most portable. **Never** commit the `.env` file (`.gitignore` it).

```bash
echo 'ANTHROPIC_API_KEY=sk-ant-...' > .env
```

## First run

`first_run.py`:

```python
import asyncio
from sophia_motor import Motor, RunTask

motor = Motor()       # config defaults are fine for hello-world

async def main():
    result = await motor.run(RunTask(prompt="In one sentence, what is 2+2?"))
    print(result.output_text)
    print(f"cost: ${result.metadata.total_cost_usd:.4f}")
    print(f"audit: {result.audit_dir}")

asyncio.run(main())
```

```bash
python first_run.py
```

Expected: a printed sentence, cost a few tenths of a cent, and a path under `~/.sophia-motor/runs/run-<ts>-<hex>/audit/` where you can `cat request_001.json` to see the actual `/v1/messages` body sent to Anthropic.

## What the first run creates

- **Workspace root**: `~/.sophia-motor/runs/` (per [`MotorConfig.workspace_root`](reference.md#motorconfig-fields))
- **One sub-directory per run**: `run-<unix-ts>-<8 hex>/`
  - `input.json` — task params + config snapshot + manifests
  - `trace.json` — final blocks + metadata
  - `audit/request_001.json` + `audit/response_001.sse` (or `.json` for non-stream)
  - `agent_cwd/` — the agent's sandboxed working dir (`outputs/` lands here)
  - `.claude/` — CLI subprocess config (motor-managed)

`~/.sophia-motor/` is the user's "motor home" — leave anything outside `runs/` alone (the user may keep notes / drafts there). To wipe runs:

```python
motor.clean_runs()                          # delete every run-* dir
motor.clean_runs(keep_last=10)              # keep 10 most recent
motor.clean_runs(older_than_days=7)         # delete > 1 week old
motor.clean_runs(dry_run=True)              # preview, no deletion
```

## Container deployment

`~/.sophia-motor/runs/` lives at `Path.home() / ".sophia-motor" / "runs"`. In containers, two things commonly bite:

1. `Path.home()` raises if `getpwuid(os.getuid())` fails (ad-hoc UIDs, no `/etc/passwd` entry).
2. The path is in the writable layer → wiped on container restart.

Fix:

```dockerfile
RUN useradd -m -u 1000 agent && \
    mkdir -p /data/runs && chown -R agent:agent /data
USER agent
ENV HOME=/home/agent
ENV SOPHIA_MOTOR_WORKSPACE_ROOT=/data/runs

VOLUME ["/data"]
```

…then `docker run -v sophia-motor-data:/data -e ANTHROPIC_API_KEY=$KEY ...`. See [`examples/docker/`](../examples/docker/) for the full Dockerfile.

## Troubleshooting install

| Symptom | Likely cause |
|---|---|
| `ERROR: Package 'sophia-motor' requires a different Python` | Python < 3.12 |
| `ANTHROPIC_API_KEY not set. Pass api_key=... to MotorConfig...` | none of the 3 cascade sources matched |
| `RuntimeError: sophia-motor proxy did not start within 5s` | Port collision with explicit `proxy_port=...`. Drop the kwarg → kernel-assigned port |
| Hangs on first `motor.run()` for >30s | The bundled CLI binary downloads/initializes once; usually one-shot |
| `ModuleNotFoundError: No module named 'rich'` from `motor.console()` | Install `[console]` extras |

## When uncertain about the install

Check what got installed:

```bash
python -c "import sophia_motor; print(sophia_motor.__version__, sophia_motor.__file__)"
ls $(python -c "import sophia_motor, os; print(os.path.dirname(sophia_motor.__file__))")
```

The first line tells you the actual installed version (the skill targets 0.5.x — defaults differ in 0.4.x and earlier). The second line lists the source files; open `config.py` to see exactly which fields and defaults this install has.
