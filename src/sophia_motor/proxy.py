"""In-process FastAPI proxy that sits between the Claude Agent SDK and Anthropic.

Why a proxy even when calling the real Anthropic API:

- **Audit dump**: every request and response is persisted under <run>/audit/
  for compliance defense (BdI / internal audit). No way to retroactively
  reconstruct what the model saw without it.
- **Console events**: emit `proxy_request` / `proxy_response` events on every
  exchange so the user sees turns in real time.
- **Strip SDK noise**: the SDK injects a billing-header block and an identity
  line into the system field that are useless to us and cost tokens.
- **Future**: cost tracking, circuit breaker, schema preflight, prompt-cache
  marking. Hooks are already in place — we just need to wire them.

The proxy listens on a free local port chosen at start time. The Motor
points the SDK subprocess at it via `ANTHROPIC_BASE_URL` env var.
"""
from __future__ import annotations

import asyncio
import json
import socket
from pathlib import Path
from typing import Optional, TYPE_CHECKING

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .events import Event

if TYPE_CHECKING:
    from .config import MotorConfig
    from .events import EventBus


_SDK_IDENTITY_LINE = "You are a Claude agent, built on Anthropic's Claude Agent SDK."


class ProxyServer:
    """Local FastAPI app that forwards /v1/messages to the upstream Anthropic API."""

    def __init__(self, config: "MotorConfig", events: "EventBus") -> None:
        self.config = config
        self.events = events
        self._current_run_id: Optional[str] = None
        self._current_audit_dir: Optional[Path] = None
        self._req_counter: int = 0

        self.server: Optional[uvicorn.Server] = None
        self._task: Optional[asyncio.Task] = None
        self.port: Optional[int] = None
        self.app = self._build_app()

    # ── lifecycle ────────────────────────────────────────────────────

    async def start(self) -> str:
        """Bind to a free port and start serving. Returns the base URL."""
        self.port = _find_free_port()
        ucfg = uvicorn.Config(
            self.app,
            host=self.config.proxy_host,
            port=self.port,
            log_level="warning",
            access_log=False,
            lifespan="on",
        )
        self.server = uvicorn.Server(ucfg)
        self._task = asyncio.create_task(self.server.serve())
        # poll for ready (server.started flips True after startup hooks run)
        for _ in range(100):
            if self.server.started:
                break
            await asyncio.sleep(0.05)
        if not self.server.started:
            raise RuntimeError("sophia-motor proxy did not start within 5s")
        await self.events.log("INFO", f"proxy listening on {self.base_url}")
        return self.base_url

    async def stop(self) -> None:
        if self.server is not None:
            self.server.should_exit = True
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=10)
            except asyncio.TimeoutError:
                self._task.cancel()
        await self.events.log("INFO", "proxy stopped")

    @property
    def base_url(self) -> str:
        if self.port is None:
            raise RuntimeError("proxy not started")
        return f"http://{self.config.proxy_host}:{self.port}"

    # ── per-run binding ──────────────────────────────────────────────

    def set_active_run(self, run_id: str, audit_dir: Path) -> None:
        """Bind a run_id so dumps and events get tagged correctly.

        A single Motor instance handles one run at a time. For parallelism,
        instantiate multiple Motors (each has its own proxy on its own port).
        """
        self._current_run_id = run_id
        self._current_audit_dir = audit_dir
        self._req_counter = 0

    def clear_active_run(self) -> None:
        self._current_run_id = None
        self._current_audit_dir = None

    # ── app ──────────────────────────────────────────────────────────

    def _build_app(self) -> FastAPI:
        app = FastAPI(title="sophia-motor proxy")

        @app.get("/health")
        async def health():
            return {"status": "ok", "run_id": self._current_run_id}

        @app.post("/v1/messages")
        async def messages(req: Request):
            body_bytes = await req.body()
            try:
                body = json.loads(body_bytes)
            except Exception:
                body = {}

            stripped = (
                _strip_sdk_noise(body) if self.config.proxy_strip_sdk_noise else 0
            )

            self._req_counter += 1
            req_idx = self._req_counter

            # ── persist request ──
            if self.config.proxy_dump_payloads and self._current_audit_dir is not None:
                _dump_json(
                    self._current_audit_dir / f"request_{req_idx:03d}.json",
                    body,
                )

            await self.events.emit_event(Event(
                type="proxy_request",
                run_id=self._current_run_id,
                payload={
                    "idx": req_idx,
                    "model": body.get("model"),
                    "n_messages": len(body.get("messages", [])),
                    "n_tools": len(body.get("tools", [])),
                    "stream": body.get("stream", False),
                    "stripped_blocks": stripped,
                },
            ))
            await self.events.log(
                "INFO",
                f"→ anthropic request #{req_idx}",
                run_id=self._current_run_id,
                model=body.get("model"),
                n_messages=len(body.get("messages", [])),
                n_tools=len(body.get("tools", [])),
                stream=body.get("stream", False),
            )

            # ── forward upstream ──
            headers = _forward_headers(req, self.config)
            target = f"{self.config.upstream_base_url}/v1/messages"
            is_stream = body.get("stream", False)
            timeout = httpx.Timeout(600.0, connect=15.0)

            if is_stream:
                return StreamingResponse(
                    self._stream_upstream(target, body, headers, req_idx),
                    media_type="text/event-stream",
                )

            async with httpx.AsyncClient(timeout=timeout) as client:
                upstream = await client.post(target, json=body, headers=headers)

            response_bytes = upstream.content
            try:
                response_body = json.loads(response_bytes)
            except Exception:
                response_body = {"_raw": response_bytes.decode(errors="replace")}

            if self.config.proxy_dump_payloads and self._current_audit_dir is not None:
                _dump_json(
                    self._current_audit_dir / f"response_{req_idx:03d}.json",
                    response_body,
                )

            usage = response_body.get("usage") or {}
            await self.events.emit_event(Event(
                type="proxy_response",
                run_id=self._current_run_id,
                payload={
                    "idx": req_idx,
                    "status": upstream.status_code,
                    "stop_reason": response_body.get("stop_reason"),
                    "usage": usage,
                },
            ))
            await self.events.log(
                "INFO",
                f"← anthropic response #{req_idx} status={upstream.status_code} "
                f"stop={response_body.get('stop_reason')}",
                run_id=self._current_run_id,
                input_tokens=usage.get("input_tokens"),
                output_tokens=usage.get("output_tokens"),
            )
            return JSONResponse(response_body, status_code=upstream.status_code)

        return app

    # ── streaming forward ────────────────────────────────────────────

    async def _stream_upstream(self, target, body, headers, req_idx):
        """Pipe upstream SSE chunks straight to the client; collect for dump."""
        timeout = httpx.Timeout(600.0, connect=15.0)
        chunks: list[bytes] = []
        usage_seen: dict = {}
        stop_reason: Optional[str] = None
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("POST", target, json=body, headers=headers) as upstream:
                async for chunk in upstream.aiter_bytes():
                    chunks.append(chunk)
                    yield chunk
        full_text = b"".join(chunks).decode(errors="replace")
        for line in full_text.splitlines():
            if not line.startswith("data: "):
                continue
            data_str = line[6:]
            if data_str.strip() == "[DONE]":
                continue
            try:
                data = json.loads(data_str)
            except Exception:
                continue
            if data.get("type") == "message_delta":
                delta = data.get("delta", {}) or {}
                if delta.get("stop_reason"):
                    stop_reason = delta["stop_reason"]
                if isinstance(data.get("usage"), dict):
                    usage_seen.update(data["usage"])
            elif data.get("type") == "message_start":
                usage_seen.update((data.get("message", {}) or {}).get("usage", {}) or {})
        if self.config.proxy_dump_payloads and self._current_audit_dir is not None:
            _dump_text(
                self._current_audit_dir / f"response_{req_idx:03d}.sse",
                full_text,
            )
        await self.events.emit_event(Event(
            type="proxy_response",
            run_id=self._current_run_id,
            payload={
                "idx": req_idx,
                "stream": True,
                "stop_reason": stop_reason,
                "usage": usage_seen,
            },
        ))
        await self.events.log(
            "INFO",
            f"← anthropic stream response #{req_idx} stop={stop_reason}",
            run_id=self._current_run_id,
            input_tokens=usage_seen.get("input_tokens"),
            output_tokens=usage_seen.get("output_tokens"),
        )


# ─────────────────────────────────────────────────────────────────────────
# helpers
# ─────────────────────────────────────────────────────────────────────────

def _find_free_port() -> int:
    """Bind to port 0, read the kernel-assigned port, release."""
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _strip_sdk_noise(body: dict) -> int:
    """Remove SDK-injected billing header and identity blocks from system field."""
    system = body.get("system")
    if not isinstance(system, list):
        return 0
    new_blocks: list = []
    removed = 0
    for block in system:
        if not isinstance(block, dict):
            new_blocks.append(block)
            continue
        text = block.get("text", "")
        if isinstance(text, str):
            if text.startswith("x-anthropic-billing-header:"):
                removed += 1
                continue
            if text.strip() == _SDK_IDENTITY_LINE:
                removed += 1
                continue
        new_blocks.append(block)
    if removed:
        body["system"] = new_blocks
    return removed


def _forward_headers(req: Request, config) -> dict:
    """Build outgoing headers: forward what's relevant + inject our api key."""
    out: dict[str, str] = {}
    for k in ("anthropic-version", "content-type", "anthropic-beta"):
        v = req.headers.get(k)
        if v:
            out[k] = v
    out.setdefault("anthropic-version", config.anthropic_version)
    out.setdefault("content-type", "application/json")
    if config.api_key:
        out["x-api-key"] = config.api_key
    return out


def _dump_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _dump_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
