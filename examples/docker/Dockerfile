# syntax=docker/dockerfile:1
FROM python:3.12-slim

# Non-root user with an explicit HOME — pathlib's Path.home() falls back
# on $HOME, and a UID without a /etc/passwd entry would otherwise crash
# the SDK on first call. Even though we override workspace_root below,
# leaving HOME well-defined keeps every internal Path.home() call safe.
RUN useradd -m -u 1000 -d /home/agent agent
ENV HOME=/home/agent

WORKDIR /app

# pip install pulls the right platform wheel automatically:
#   linux/amd64  → manylinux_2_17_x86_64
#   linux/arm64  → manylinux_2_17_aarch64
RUN pip install --no-cache-dir sophia-motor

COPY main.py /app/main.py

# Mount target for audit dumps + trace files. Override workspace_root in
# the Python config so runs land here instead of $HOME.
VOLUME ["/data"]
RUN mkdir -p /data && chown agent:agent /data /app

USER agent

CMD ["python", "main.py"]
