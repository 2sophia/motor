"""Motor — instanceable agent engine.

Class API:

    config = MotorConfig(api_key="...")
    async with Motor(config) as motor:

        @motor.on_event
        async def handle(event):
            print(event)

        result = await motor.run(RunTask(
            prompt="Read the file scratch/note.txt and summarize it.",
            allowed_tools=["Read"],
            cwd_files={"scratch/note.txt": "..."},
        ))

A single Motor instance handles one run at a time (the proxy is bound to the
active run for audit-dump tagging). For parallelism, instantiate N Motors.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Optional

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from ._models import RunMetadata, RunResult, RunTask
from .cleanup import clean_runs as _clean_runs_helper
from .config import MotorConfig
from .events import (
    Event,
    EventBus,
    default_console_event_logger,
    default_console_logger,
)
from .proxy import ProxyServer


class Motor:
    """Instanceable agent motor.

    Lifecycle:
        await motor.start()        # boots the proxy
        result = await motor.run(task)
        await motor.stop()         # shuts the proxy down

    or as context manager:
        async with Motor(config) as motor:
            ...

    Subscriber registration:
        motor.on_event(fn)  # decorator or direct call
        motor.on_log(fn)
    """

    def __init__(self, config: Optional[MotorConfig] = None) -> None:
        self.config = config or MotorConfig()
        self.events = EventBus()

        # Expose the EventBus decorators directly on the Motor for ergonomics.
        self.on_event = self.events.on_event
        self.on_log = self.events.on_log

        self._proxy: Optional[ProxyServer] = None
        self._started = False
        self._run_lock = asyncio.Lock()

        if self.config.console_log_enabled:
            self.events.on_log(default_console_logger)
            self.events.on_event(default_console_event_logger)

    # ── lifecycle ────────────────────────────────────────────────────

    async def __aenter__(self) -> "Motor":
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.stop()

    async def start(self) -> None:
        if self._started:
            return
        await self.events.log("INFO", "motor starting", model=self.config.model)
        if self.config.proxy_enabled:
            self._proxy = ProxyServer(self.config, self.events)
            await self._proxy.start()
        self._started = True

    async def stop(self) -> None:
        if self._proxy is not None:
            await self._proxy.stop()
            self._proxy = None
        self._started = False
        await self.events.log("INFO", "motor stopped")

    # ── workspace cleanup ────────────────────────────────────────────

    def clean_runs(
        self,
        *,
        keep_last: int = 0,
        older_than_days: float | None = None,
        dry_run: bool = False,
    ) -> list[Path]:
        """Remove run directories under this Motor's workspace_root.

        Bound version of `sophia_motor.clean_runs`: targets `self.config.workspace_root`.

        Args:
            keep_last:       keep the N most recent runs (default 0 = remove all)
            older_than_days: only consider runs older than this many days
            dry_run:         list what would be removed without deleting

        Returns:
            List of paths removed (or that would be removed in dry_run).
        """
        return _clean_runs_helper(
            self.config.workspace_root,
            keep_last=keep_last,
            older_than_days=older_than_days,
            dry_run=dry_run,
        )

    # ── run ──────────────────────────────────────────────────────────

    async def run(self, task: RunTask) -> RunResult:
        if not self._started:
            raise RuntimeError(
                "Motor not started. Use `async with Motor(...)` "
                "or call `await motor.start()` first."
            )
        api_key = self.config.require_api_key()

        # Pre-flight validation BEFORE we mint a run_id, set up workspace,
        # bind the proxy, or call the SDK. Better to fail loud here than to
        # spend tokens on a doomed run.
        _validate_attachments(task.attachments)

        async with self._run_lock:
            run_id = self._mint_run_id()
            workspace_dir, audit_dir, attachments_manifest = self._setup_workspace(run_id, task)
            self._persist_input(workspace_dir, run_id, task, attachments_manifest)

            if self._proxy is not None:
                self._proxy.set_active_run(run_id, audit_dir)

            opts = self._build_sdk_options(task, workspace_dir, api_key)

            await self.events.emit_event(Event(
                type="run_started",
                run_id=run_id,
                payload={
                    "prompt": task.prompt[:200],
                    "model": self.config.model,
                    "workspace": str(workspace_dir),
                    "max_turns": opts.max_turns,
                    "allowed_tools": task.allowed_tools,
                },
            ))
            await self.events.log(
                "INFO",
                f"run started → {run_id}",
                run_id=run_id,
                model=self.config.model,
            )

            t0 = time.monotonic()
            collected: list[dict] = []
            result_text: Optional[str] = None
            is_error = False
            error_reason: Optional[str] = None
            n_turns = 0
            n_tool_calls = 0
            input_tokens = 0
            output_tokens = 0
            total_cost = 0.0

            try:
                async with ClaudeSDKClient(options=opts) as client:
                    await client.query(prompt=task.prompt)
                    async for message in client.receive_response():
                        kind = type(message).__name__

                        if isinstance(message, SystemMessage):
                            await self.events.emit_event(Event(
                                type="system_message",
                                run_id=run_id,
                                payload={"subtype": getattr(message, "subtype", None)},
                            ))

                        elif isinstance(message, AssistantMessage):
                            for block in message.content:
                                if isinstance(block, TextBlock):
                                    collected.append({"type": "text", "text": block.text})
                                    await self.events.emit_event(Event(
                                        type="assistant_text",
                                        run_id=run_id,
                                        payload={
                                            "len": len(block.text),
                                            "preview": block.text[:200],
                                        },
                                    ))
                                elif isinstance(block, ThinkingBlock):
                                    collected.append({"type": "thinking", "text": block.thinking})
                                    await self.events.emit_event(Event(
                                        type="thinking",
                                        run_id=run_id,
                                        payload={"len": len(block.thinking)},
                                    ))
                                elif isinstance(block, ToolUseBlock):
                                    n_tool_calls += 1
                                    collected.append({
                                        "type": "tool_use",
                                        "tool": block.name,
                                        "input": block.input,
                                    })
                                    await self.events.emit_event(Event(
                                        type="tool_use",
                                        run_id=run_id,
                                        payload={
                                            "tool": block.name,
                                            "input_keys": list(block.input.keys())
                                            if isinstance(block.input, dict) else None,
                                        },
                                    ))

                        elif isinstance(message, UserMessage):
                            for block in message.content:
                                if isinstance(block, ToolResultBlock):
                                    preview = _tool_result_preview(block)
                                    await self.events.emit_event(Event(
                                        type="tool_result",
                                        run_id=run_id,
                                        payload={
                                            "is_error": getattr(block, "is_error", False),
                                            "preview": preview,
                                        },
                                    ))

                        elif isinstance(message, ResultMessage):
                            result_text = message.result
                            is_error = bool(message.is_error)
                            n_turns = message.num_turns or 0
                            total_cost = message.total_cost_usd or 0.0
                            usage = getattr(message, "usage", None) or {}
                            if isinstance(usage, dict):
                                input_tokens = int(usage.get("input_tokens") or 0)
                                output_tokens = int(usage.get("output_tokens") or 0)
                            await self.events.emit_event(Event(
                                type="result",
                                run_id=run_id,
                                payload={
                                    "is_error": is_error,
                                    "n_turns": n_turns,
                                    "cost_usd": total_cost,
                                    "result_preview": (result_text or "")[:200],
                                },
                            ))

                        else:
                            await self.events.emit_event(Event(
                                type="sdk_message",
                                run_id=run_id,
                                payload={"kind": kind},
                            ))

            except Exception as e:  # noqa: BLE001
                is_error = True
                error_reason = repr(e)
                await self.events.log("ERROR", f"run failed: {e!r}", run_id=run_id)
            finally:
                if self._proxy is not None:
                    self._proxy.clear_active_run()

            duration = time.monotonic() - t0
            metadata = RunMetadata(
                run_id=run_id,
                duration_s=round(duration, 3),
                n_turns=n_turns,
                n_tool_calls=n_tool_calls,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_cost_usd=total_cost,
                is_error=is_error,
                error_reason=error_reason,
            )

            self._persist_trace(workspace_dir, metadata, collected, result_text)

            await self.events.log(
                "INFO",
                f"run finished {run_id} duration={duration:.1f}s "
                f"turns={n_turns} tools={n_tool_calls} cost=${total_cost:.4f} "
                f"error={is_error}",
                run_id=run_id,
            )

            return RunResult(
                run_id=run_id,
                output_text=result_text,
                blocks=collected,
                metadata=metadata,
                audit_dir=audit_dir,
                workspace_dir=workspace_dir,
            )

    # ── helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _mint_run_id() -> str:
        return f"run-{int(time.time())}-{uuid.uuid4().hex[:8]}"

    def _setup_workspace(self, run_id: str, task: RunTask) -> tuple[Path, Path, dict[str, str]]:
        """Create workspace dirs and materialize attachments.

        Returns (workspace_dir, audit_dir, attachments_manifest). Attachments
        are validated by `_validate_attachments` before this is called.
        """
        workspace_dir = self.config.workspace_root / run_id
        audit_dir = workspace_dir / "audit"
        attachments_dir = workspace_dir / "attachments"
        outputs_dir = workspace_dir / "outputs"
        for d in (workspace_dir, audit_dir, attachments_dir, outputs_dir):
            d.mkdir(parents=True, exist_ok=True)

        manifest = _materialize_attachments(attachments_dir, task.attachments)
        return workspace_dir, audit_dir, manifest

    def _persist_input(
        self,
        workspace_dir: Path,
        run_id: str,
        task: RunTask,
        attachments_manifest: dict[str, str],
    ) -> None:
        dump = {
            "run_id": run_id,
            "task": {
                "prompt": task.prompt,
                "system": task.system,
                "tools": task.tools,
                "allowed_tools": task.allowed_tools,
                "disallowed_tools": task.disallowed_tools,
                "max_turns": task.max_turns or self.config.default_max_turns,
                "attachments": attachments_manifest,
            },
            "config": {
                "model": self.config.model,
                "upstream_base_url": self.config.upstream_base_url,
                "proxy_enabled": self.config.proxy_enabled,
            },
        }
        (workspace_dir / "input.json").write_text(
            json.dumps(dump, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _persist_trace(
        self,
        workspace_dir: Path,
        metadata: RunMetadata,
        blocks: list[dict],
        result_text: Optional[str],
    ) -> None:
        trace = {
            "metadata": {
                "run_id": metadata.run_id,
                "duration_s": metadata.duration_s,
                "n_turns": metadata.n_turns,
                "n_tool_calls": metadata.n_tool_calls,
                "input_tokens": metadata.input_tokens,
                "output_tokens": metadata.output_tokens,
                "total_cost_usd": metadata.total_cost_usd,
                "is_error": metadata.is_error,
                "error_reason": metadata.error_reason,
            },
            "blocks": blocks,
            "result_text": result_text,
        }
        (workspace_dir / "trace.json").write_text(
            json.dumps(trace, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )

    def _build_sdk_options(
        self,
        task: RunTask,
        workspace_dir: Path,
        api_key: str,
    ) -> ClaudeAgentOptions:
        env: dict[str, str] = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "ANTHROPIC_API_KEY": api_key,
            # default model env vars used by the Claude CLI
            "ANTHROPIC_DEFAULT_OPUS_MODEL": self.config.model,
            "ANTHROPIC_DEFAULT_SONNET_MODEL": self.config.model,
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": self.config.model,
            # we want all tools loaded upfront, no deferred ToolSearch dance
            "ENABLE_TOOL_SEARCH": "false",
        }
        if self._proxy is not None:
            env["ANTHROPIC_BASE_URL"] = self._proxy.base_url
            env["ANTHROPIC_AUTH_TOKEN"] = api_key

        # Resolve disallowed_tools: explicit None on the task → config default.
        # Explicit empty list [] on the task → run truly unblocked (escape hatch
        # for tests). Otherwise use what the task provides.
        if task.disallowed_tools is None:
            disallowed = list(self.config.default_disallowed_tools)
        else:
            disallowed = list(task.disallowed_tools)

        # `tools` (hard whitelist) only set when explicitly provided.
        # SDK signature: list[str] | ToolsPreset | None. Pass-through.
        sdk_kwargs: dict = {
            "system_prompt": task.system,
            "allowed_tools": task.allowed_tools or [],
            "disallowed_tools": disallowed,
            "max_turns": task.max_turns or self.config.default_max_turns,
            "permission_mode": "bypassPermissions",
            "cwd": str(workspace_dir),
            "env": env,
            "plugins": [],
            "setting_sources": ["project"],
        }
        if task.tools is not None:
            sdk_kwargs["tools"] = task.tools

        return ClaudeAgentOptions(**sdk_kwargs)


def _tool_result_preview(block: ToolResultBlock) -> str:
    """Best-effort short preview of a tool_result block content."""
    content = getattr(block, "content", None)
    if isinstance(content, str):
        return content[:200]
    if isinstance(content, list):
        for c in content:
            if isinstance(c, dict) and c.get("type") == "text":
                return (c.get("text") or "")[:200]
    return ""


# ─────────────────────────────────────────────────────────────────────────
# attachments — pre-flight validation + materialization
#
# `_validate_attachments` runs BEFORE any state mutation: no run_id minted,
# no workspace created, no proxy bound, no SDK called. The dev gets a clean
# error and can fix the input. No tokens consumed on a doomed run.
#
# `_materialize_attachments` runs only after validation succeeds. It copies
# real files/dirs and writes inline files into <run>/attachments/.
# ─────────────────────────────────────────────────────────────────────────

def _validate_attachments(items: list) -> None:
    """Pre-flight check. Raise with a specific exception type per cause."""
    seen_targets: dict[str, str] = {}  # target_rel → source_repr (for conflict msg)
    for i, item in enumerate(items):
        if isinstance(item, dict):
            if not item:
                raise ValueError(f"attachments[{i}]: empty dict is not allowed")
            for rel_path, content in item.items():
                if not isinstance(rel_path, str) or not rel_path:
                    raise ValueError(
                        f"attachments[{i}]: dict key must be a non-empty string, "
                        f"got {rel_path!r}"
                    )
                if not isinstance(content, str):
                    raise TypeError(
                        f"attachments[{i}]: dict value for {rel_path!r} must be str, "
                        f"got {type(content).__name__}"
                    )
                if rel_path.startswith("/"):
                    raise ValueError(
                        f"attachments[{i}]: inline relpath {rel_path!r} must be "
                        f"relative, not absolute"
                    )
                parts = Path(rel_path).parts
                if ".." in parts:
                    raise ValueError(
                        f"attachments[{i}]: inline relpath {rel_path!r} must not "
                        f"contain '..' (sandbox escape)"
                    )
                norm = str(Path(rel_path))
                if norm in seen_targets:
                    raise ValueError(
                        f"attachments[{i}]: target {norm!r} conflicts with earlier "
                        f"attachment from {seen_targets[norm]!r}"
                    )
                seen_targets[norm] = "<inline>"
        elif isinstance(item, (str, Path)):
            src = Path(item)
            if not src.exists():
                raise FileNotFoundError(
                    f"attachments[{i}]: path {str(src)!r} does not exist"
                )
            if not (src.is_file() or src.is_dir()):
                raise ValueError(
                    f"attachments[{i}]: path {str(src)!r} is neither a file nor "
                    f"a directory"
                )
            if not os.access(src, os.R_OK):
                raise PermissionError(
                    f"attachments[{i}]: path {str(src)!r} is not readable"
                )
            target_name = src.name
            if not target_name:
                raise ValueError(
                    f"attachments[{i}]: cannot derive target name from path "
                    f"{str(src)!r}"
                )
            if target_name in seen_targets:
                raise ValueError(
                    f"attachments[{i}]: target name {target_name!r} (from "
                    f"{str(src)!r}) conflicts with earlier attachment from "
                    f"{seen_targets[target_name]!r}"
                )
            seen_targets[target_name] = str(src)
        else:
            raise TypeError(
                f"attachments[{i}]: unsupported type {type(item).__name__}; "
                f"must be str | Path | dict[str, str]"
            )


def _materialize_attachments(attachments_dir: Path, items: list) -> dict[str, str]:
    """Copy/write all attachments into `attachments_dir`.

    Returns a manifest {final_relative_path → source_repr} suitable for
    persistence in input.json (audit trail).
    """
    manifest: dict[str, str] = {}
    for item in items:
        if isinstance(item, dict):
            for rel_path, content in item.items():
                target = attachments_dir / rel_path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
                manifest[str(Path(rel_path))] = "<inline>"
        else:
            src = Path(item)
            target = attachments_dir / src.name
            if src.is_file():
                shutil.copy2(src, target)
            else:  # src.is_dir() guaranteed by validation
                shutil.copytree(src, target)
            manifest[src.name] = str(src)
    return manifest
