# docker

Run sophia-motor inside a container with audit dumps that **survive
restarts**. Two gotchas covered:

1. **`~/.sophia-motor/runs/` dies with the container** unless you mount
   a volume. Override `workspace_root` to a path under that volume.
2. **`Path.home()` can crash** for ad-hoc UIDs without a `/etc/passwd`
   entry. The Dockerfile creates a real user (`agent`, UID 1000) and
   sets `HOME` explicitly.

## Files

- `main.py` — minimal run with `MotorConfig(workspace_root=Path("/data/runs"))`
- `Dockerfile` — `python:3.12-slim` + non-root user + `VOLUME /data`
- `docker-compose.yml` — wires the host folder `./data` to `/data` in the container

## Run with docker compose

```bash
export ANTHROPIC_API_KEY=sk-ant-...
docker compose up --build
```

After the run, audit dumps and trace files are on the host under
`./data/runs/<run_id>/` — inspect, archive, or grep them like any local file.

## Run with plain docker

```bash
docker build -t sophia-motor-demo .

docker run --rm \
  -e ANTHROPIC_API_KEY=sk-ant-... \
  -v "$(pwd)/data:/data" \
  sophia-motor-demo
```

## Multi-arch

`pip install sophia-motor` pulls the right wheel for the build platform
automatically — both `linux/amd64` and `linux/arm64` are supported by
the upstream Claude Agent SDK. No extra config needed on Apple Silicon
or AWS Graviton.

## What to override in production

- `workspace_root` — point it at a persistent volume (Kubernetes PVC,
  EFS, host bind-mount, …). The default `~/.sophia-motor/runs/` is not
  what you want once the process is ephemeral.
- `proxy_dump_payloads` — leave on (default) for audit; flip off only
  if disk pressure is real and you don't need request/response bodies.
- `console_log_enabled` — flip off in production; structured logs go
  through `motor.events` / `motor.logs` and are easier to ingest.

## What NOT to put in the image

Don't bake `ANTHROPIC_API_KEY` into the layer. Pass it via env at run
time (compose: `environment:`, k8s: `Secret` mounted as env, ECS: task
definition `secrets:`). The image stays redistributable; the secret
stays out of `docker history`.
