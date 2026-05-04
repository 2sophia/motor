# docker

Same `main.py` you'd run locally — the Dockerfile flips the workspace
root via env so audit dumps + trace files land on a mounted volume
instead of inside the container's ephemeral filesystem.

## The pattern

```python
from sophia_motor import Motor, RunTask

motor = Motor()  # picks up SOPHIA_MOTOR_WORKSPACE_ROOT from env if set
```

```dockerfile
ENV SOPHIA_MOTOR_WORKSPACE_ROOT=/data/runs
VOLUME ["/data"]
```

Locally — without the env override — `Motor()` writes to the OS tempdir
(e.g. `/tmp/sophia-motor/runs/` on Linux), which is ephemeral by design.
In the container, the env override redirects it to `/data/runs/`, which
is the mount point for the host volume — persistent across restarts.
**No code change between environments**.

## Files

- `main.py` — plain `Motor()` run, no path hardcoded
- `Dockerfile` — `python:3.12-slim` + non-root user with `HOME` set + `ENV SOPHIA_MOTOR_WORKSPACE_ROOT`
- `docker-compose.yml` — wires the host folder `./data` to `/data` in the container

## Run with docker compose

```bash
export ANTHROPIC_API_KEY=sk-ant-...
docker compose up --build
```

After the run, audit dumps and trace files are on the host under
`./data/runs/<run_id>/` — inspect, archive, or grep them like any
local file.

## Run with plain docker

```bash
docker build -t sophia-motor-demo .

docker run --rm \
  -e ANTHROPIC_API_KEY=sk-ant-... \
  -v "$(pwd)/data:/data" \
  sophia-motor-demo
```

## Two gotchas covered

1. **`Path.home()` can crash** for ad-hoc UIDs without a `/etc/passwd`
   entry. The Dockerfile creates a real user (`agent`, UID 1000) and
   sets `HOME` explicitly.
2. **The default tempdir workspace dies with the container** (and would
   die with the host's tempdir sweep too). The `VOLUME ["/data"]`
   declaration + the workspace env override redirect runs to the mount
   so they survive container teardown.

## Multi-arch

`pip install sophia-motor` pulls the right wheel for the build
platform automatically — both `linux/amd64` and `linux/arm64` are
supported by the upstream Claude Agent SDK. No extra config needed on
Apple Silicon or AWS Graviton.

## Production knobs (env vars)

| Env var                       | What it does                          | Default       |
|-------------------------------|---------------------------------------|---------------|
| `SOPHIA_MOTOR_WORKSPACE_ROOT` | Where runs are persisted              | `<tempdir>/sophia-motor/runs` (e.g. `/tmp/...`) |
| `SOPHIA_MOTOR_MODEL`          | Default model id                      | `claude-opus-4-6` |
| `SOPHIA_MOTOR_CONSOLE_LOG`    | Stream events to stdout               | `false` |
| `SOPHIA_MOTOR_AUDIT_DUMP`     | Write request/response bodies to disk | `false` |

`SOPHIA_MOTOR_CONSOLE_LOG` and `SOPHIA_MOTOR_AUDIT_DUMP` are off by
default so production stays clean (no stdout noise, no disk pressure).
Flip them on for local debugging or attach a debug profile to a
container.

## What NOT to put in the image

Don't bake `ANTHROPIC_API_KEY` into the layer. Pass it via env at run
time (compose: `environment:`, k8s: `Secret` mounted as env, ECS: task
definition `secrets:`). The image stays redistributable; the secret
stays out of `docker history`.
