# syntax=docker/dockerfile:1
FROM python:3.12-slim

# Non-root user with an explicit HOME — pathlib's Path.home() falls back
# on $HOME, and a UID without a /etc/passwd entry would otherwise crash
# the SDK on first call.
RUN useradd -m -u 1000 -d /home/agent agent
ENV HOME=/home/agent

WORKDIR /app

# pip install pulls the right platform wheel automatically:
#   linux/amd64  → manylinux_2_17_x86_64
#   linux/arm64  → manylinux_2_17_aarch64
RUN pip install --no-cache-dir sophia-motor

COPY main.py /app/main.py

# Override workspace_root via env so the same main.py works locally
# (writes under $HOME) and in container (writes under /data).
ENV SOPHIA_MOTOR_WORKSPACE_ROOT=/data/runs

# Mount target for audit dumps + trace files. Without this volume, runs
# vanish with the container.
VOLUME ["/data"]
RUN mkdir -p /data && chown agent:agent /data /app

USER agent

CMD ["python", "main.py"]
