# Copyright (c) 2026 Sophia AI
# SPDX-License-Identifier: MIT
"""Motor — instanceable agent engine.

Class API:

    config = MotorConfig(api_key="...")
    async with Motor(config) as motor:

        @motor.on_event
        async def handle(event):
            print(event)

        result = await motor.run(RunTask(
            prompt="Read the file note.txt and summarize it.",
            tools=["Read"],
            attachments={"note.txt": "..."},
        ))

A single Motor instance handles one run at a time (the proxy is bound to the
active run for audit-dump tagging). For parallelism, instantiate N Motors.
"""
from __future__ import annotations

import asyncio
import errno
import json
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, AsyncIterator, Optional

from pydantic import BaseModel, ValidationError

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
from claude_agent_sdk.types import HookMatcher, StreamEvent

from ._chunks import (
    DoneChunk,
    ErrorChunk,
    InitChunk,
    OutputFileReadyChunk,
    RunStartedChunk,
    StreamChunk,
    TextBlockChunk,
    TextDeltaChunk,
    ThinkingBlockChunk,
    ThinkingDeltaChunk,
    ToolResultChunk,
    ToolUseCompleteChunk,
    ToolUseDeltaChunk,
    ToolUseFinalizedChunk,
    ToolUseStartChunk,
)
from ._models import RunMetadata, RunResult, RunTask, discover_output_files
from ._partial_json import parse_partial_tool_input
from .cleanup import clean_runs as _clean_runs_helper
from .config import MotorConfig
from .guard import make_guard_hook
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
        # Set while a run is in flight — used by `motor.interrupt()` to
        # signal the SDK CLI subprocess of the active turn. None when no
        # run is active (idempotent: interrupt() is a no-op then).
        self._current_client: Optional[ClaudeSDKClient] = None
        self._current_run_id: Optional[str] = None
        self._interrupt_requested = False

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

    # ── interrupt ────────────────────────────────────────────────────

    async def interrupt(self, run_id: Optional[str] = None) -> bool:
        """Interrupt the run currently in flight on this motor.

        This is the "Ctrl+C" of an active run — a deliberate user action,
        not an error. Distinct from `stop()` (which shuts the motor down).

        Mechanics: signals the SDK CLI subprocess via `client.interrupt()`,
        which exits the current turn. The `async with ClaudeSDKClient`
        cleanup then closes the subprocess and the TCP to our proxy,
        letting upstream abort. The active `motor.stream(task)` finishes
        normally with a `DoneChunk` whose `result.metadata.was_interrupted`
        is True (`is_error` stays False).

        Args:
            run_id: when provided, only interrupt if it matches the current
                run. Race-condition guard for "user clicks stop, but their
                old run already finished and a new one started" — passing
                the run_id they saw on screen avoids interrupting the wrong
                one. `None` (default) means "interrupt whatever is current".

        Returns:
            True if a run was actively interrupted, False if no run is
            currently active or `run_id` did not match. Always idempotent;
            never raises.
        """
        client = self._current_client
        if client is None:
            return False
        if run_id is not None and run_id != self._current_run_id:
            return False
        self._interrupt_requested = True
        try:
            await client.interrupt()
        except Exception as e:  # noqa: BLE001
            await self.events.log(
                "WARNING",
                f"interrupt() raised: {e!r} (treating as best-effort)",
                run_id=self._current_run_id,
            )
        await self.events.log(
            "INFO", "interrupt requested", run_id=self._current_run_id,
        )
        return True

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

    # ── run / stream ─────────────────────────────────────────────────

    async def run(self, task: RunTask) -> RunResult:
        """Execute a task and return the final `RunResult`.

        Thin wrapper around `stream(task)`: consumes the chunk stream and
        returns the `RunResult` carried by the terminal `DoneChunk`. Use
        `run()` when you only care about the final answer; use `stream()`
        when you want live tokens / tool-use deltas in your UI.
        """
        final: Optional[RunResult] = None
        async for chunk in self.stream(task):
            if isinstance(chunk, DoneChunk):
                final = chunk.result
        assert final is not None, "stream() must always end with a DoneChunk"
        return final

    async def stream(self, task: RunTask) -> AsyncIterator[StreamChunk]:
        """Run a task and yield typed stream chunks live.

        Yields a sequence of `StreamChunk` (discriminated union on `type`):
        `run_started`, `init`, `text_delta`, `thinking_delta`,
        `tool_use_start`, `tool_use_delta`, `tool_use_complete`,
        `tool_use_finalized`, `tool_result`, optional `text_block` /
        `thinking_block` fallbacks, optional `error`, and a single terminal
        `done` carrying the final `RunResult`. The stream ALWAYS ends with
        `done`, including on error.
        """
        # Lazy auto-start: the proxy boots on first run, stays alive across
        # subsequent runs, and dies when the process terminates.
        if not self._started:
            await self.start()
        api_key = self.config.require_api_key()

        # Apply MotorConfig defaults to any RunTask field left at None / [].
        task = self._apply_config_defaults(task)

        # Pre-flight validation BEFORE we mint a run_id, set up workspace,
        # bind the proxy, or call the SDK. Fail loud here, not on tokens spent.
        attachments_items = _normalize_to_list(task.attachments)
        skills_items = _normalize_to_list(task.skills)
        _validate_attachments(attachments_items)
        _validate_skills(skills_items, task.disallowed_skills)

        async with self._run_lock:
            run_id = self._mint_run_id()
            (
                workspace_dir,
                agent_cwd,
                audit_dir,
                claude_dir,
                attachments_manifest,
                skills_manifest,
            ) = self._setup_workspace(
                run_id, attachments_items, skills_items, task.disallowed_skills,
            )
            self._persist_input(
                workspace_dir, run_id, task,
                attachments_manifest, skills_manifest,
            )

            if self._proxy is not None:
                self._proxy.set_active_run(run_id, audit_dir)

            outputs_dir = agent_cwd / "outputs"

            opts = self._build_sdk_options(
                task, agent_cwd, claude_dir, api_key,
                skills_allowed=list(skills_manifest.keys()),
            )

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
            yield RunStartedChunk(
                run_id=run_id,
                model=self.config.model,
                prompt_preview=task.prompt[:200],
                max_turns=opts.max_turns or self.config.default_max_turns,
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
            output_data: Optional[BaseModel] = None
            structured_raw: Any = None

            # Per-turn streaming state (reset on each `message_start`):
            # - text_streamed_in_turn / thinking_streamed_in_turn: skip the
            #   AssistantMessage Text/ThinkingBlock fallback chunk if we've
            #   already streamed the content as deltas
            # - active_tool_blocks[idx]: tool_use blocks mid-stream; we
            #   accumulate `input_json_delta` partials per index
            # - streamed_tool_use_ids: ids that streamed via deltas, so the
            #   AssistantMessage ToolUseBlock dedups to a `tool_use_finalized`
            text_streamed_in_turn = False
            thinking_streamed_in_turn = False
            active_tool_blocks: dict[int, dict] = {}
            streamed_tool_use_ids: set[str] = set()

            self._interrupt_requested = False
            self._current_run_id = run_id
            try:
                async with ClaudeSDKClient(options=opts) as client:
                    self._current_client = client
                    await client.query(prompt=task.prompt)
                    async for message in client.receive_response():
                        kind = type(message).__name__

                        if isinstance(message, StreamEvent):
                            # Raw API delta path. Drives the live UI:
                            # text/thinking chunks, tool_use open/delta/close.
                            event = message.event or {}
                            etype = event.get("type")

                            if etype == "message_start":
                                text_streamed_in_turn = False
                                thinking_streamed_in_turn = False
                                active_tool_blocks.clear()
                                streamed_tool_use_ids.clear()

                            elif etype == "content_block_start":
                                idx = event.get("index")
                                block = event.get("content_block") or {}
                                if block.get("type") == "tool_use":
                                    tool_use_id = block.get("id", "")
                                    tool_name = block.get("name", "")
                                    active_tool_blocks[idx] = {
                                        "id": tool_use_id,
                                        "name": tool_name,
                                        "partial_json": "",
                                    }
                                    streamed_tool_use_ids.add(tool_use_id)
                                    yield ToolUseStartChunk(
                                        tool_use_id=tool_use_id,
                                        tool=tool_name,
                                        index=idx,
                                    )

                            elif etype == "content_block_delta":
                                idx = event.get("index")
                                delta = event.get("delta") or {}
                                dtype = delta.get("type")
                                if dtype == "text_delta":
                                    chunk = delta.get("text") or ""
                                    if chunk:
                                        text_streamed_in_turn = True
                                        yield TextDeltaChunk(text=chunk)
                                elif dtype == "input_json_delta":
                                    partial = delta.get("partial_json") or ""
                                    tool_state = active_tool_blocks.get(idx)
                                    if tool_state and partial:
                                        tool_state["partial_json"] += partial
                                        extracted = parse_partial_tool_input(
                                            tool_state["partial_json"],
                                            tool_state["name"],
                                        )
                                        yield ToolUseDeltaChunk(
                                            tool_use_id=tool_state["id"],
                                            tool=tool_state["name"],
                                            partial_json=partial,
                                            extracted=extracted,
                                            index=idx,
                                        )
                                elif dtype == "thinking_delta":
                                    chunk = delta.get("thinking") or ""
                                    if chunk:
                                        thinking_streamed_in_turn = True
                                        yield ThinkingDeltaChunk(text=chunk)

                            elif etype == "content_block_stop":
                                idx = event.get("index")
                                tool_state = active_tool_blocks.pop(idx, None)
                                if tool_state:
                                    yield ToolUseCompleteChunk(
                                        tool_use_id=tool_state["id"],
                                        tool=tool_state["name"],
                                    )
                            continue

                        if isinstance(message, SystemMessage):
                            subtype = getattr(message, "subtype", None)
                            await self.events.emit_event(Event(
                                type="system_message",
                                run_id=run_id,
                                payload={"subtype": subtype},
                            ))
                            if subtype == "init":
                                data = getattr(message, "data", None) or {}
                                yield InitChunk(
                                    session_id=(
                                        data.get("session_id")
                                        if isinstance(data, dict) else None
                                    ),
                                )

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
                                    # Fallback: only yield the full block if we
                                    # didn't already stream its content as deltas.
                                    if not text_streamed_in_turn:
                                        yield TextBlockChunk(text=block.text)
                                elif isinstance(block, ThinkingBlock):
                                    collected.append({"type": "thinking", "text": block.thinking})
                                    await self.events.emit_event(Event(
                                        type="thinking",
                                        run_id=run_id,
                                        payload={"len": len(block.thinking)},
                                    ))
                                    if not thinking_streamed_in_turn:
                                        yield ThinkingBlockChunk(text=block.thinking)
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
                                    block_id = getattr(block, "id", None) or ""
                                    block_input = (
                                        block.input
                                        if isinstance(block.input, dict)
                                        else {"_raw": block.input}
                                    )
                                    yield ToolUseFinalizedChunk(
                                        tool_use_id=block_id,
                                        tool=block.name,
                                        input=block_input,
                                    )
                                    # Live signal for file-creation tools.
                                    # Only Write/Edit carry a file_path the
                                    # model committed to; Bash file creation
                                    # is captured at run-end via the
                                    # outputs/ directory walk instead.
                                    if block.name in ("Write", "Edit"):
                                        file_chunk = _output_file_ready_chunk(
                                            block.name, block_input, agent_cwd, outputs_dir,
                                        )
                                        if file_chunk is not None:
                                            yield file_chunk

                        elif isinstance(message, UserMessage):
                            for block in message.content:
                                if isinstance(block, ToolResultBlock):
                                    preview = _tool_result_preview(block)
                                    block_is_error = bool(getattr(block, "is_error", False))
                                    await self.events.emit_event(Event(
                                        type="tool_result",
                                        run_id=run_id,
                                        payload={
                                            "is_error": block_is_error,
                                            "preview": preview,
                                        },
                                    ))
                                    yield ToolResultChunk(
                                        tool_use_id=getattr(block, "tool_use_id", "") or "",
                                        is_error=block_is_error,
                                        preview=preview,
                                    )

                        elif isinstance(message, ResultMessage):
                            result_text = message.result
                            # If the user called motor.interrupt(), the SDK
                            # closes the turn with is_error=True. That's
                            # not a failure from the caller's perspective —
                            # the dedicated `was_interrupted` flag carries
                            # that signal, leaving is_error free for genuine
                            # upstream/schema/etc errors.
                            is_error = (
                                bool(message.is_error)
                                and not self._interrupt_requested
                            )
                            n_turns = message.num_turns or 0
                            total_cost = message.total_cost_usd or 0.0
                            usage = getattr(message, "usage", None) or {}
                            if isinstance(usage, dict):
                                input_tokens = int(usage.get("input_tokens") or 0)
                                output_tokens = int(usage.get("output_tokens") or 0)
                            structured_raw = getattr(message, "structured_output", None)
                            if task.output_schema is not None:
                                if structured_raw is None:
                                    is_error = True
                                    error_reason = (
                                        "output_schema set but CLI returned no "
                                        "structured_output (model didn't emit a "
                                        "schema-conforming payload)"
                                    )
                                else:
                                    try:
                                        output_data = task.output_schema.model_validate(
                                            structured_raw,
                                        )
                                    except ValidationError as ve:
                                        is_error = True
                                        error_reason = (
                                            f"structured_output failed Pydantic "
                                            f"validation against {task.output_schema.__name__}: {ve}"
                                        )
                                        output_data = None
                            await self.events.emit_event(Event(
                                type="result",
                                run_id=run_id,
                                payload={
                                    "is_error": is_error,
                                    "n_turns": n_turns,
                                    "cost_usd": total_cost,
                                    "result_preview": (result_text or "")[:200],
                                    "structured_output_present": structured_raw is not None,
                                    "output_data_validated": output_data is not None,
                                },
                            ))

                        else:
                            await self.events.emit_event(Event(
                                type="sdk_message",
                                run_id=run_id,
                                payload={"kind": kind},
                            ))

            except Exception as e:  # noqa: BLE001
                # Interrupt path: client.interrupt() raises an exception
                # inside the receive loop on purpose. That's the SDK's
                # signal to wind down the turn, not a failure — keep
                # is_error=False, the metadata.was_interrupted flag and
                # the DoneChunk carry the user-visible signal instead.
                if self._interrupt_requested:
                    await self.events.log(
                        "INFO",
                        "run interrupted by motor.interrupt()",
                        run_id=run_id,
                    )
                else:
                    is_error = True
                    error_reason = repr(e)
                    await self.events.log("ERROR", f"run failed: {e!r}", run_id=run_id)
                    yield ErrorChunk(message=repr(e))
            finally:
                if self._proxy is not None:
                    self._proxy.clear_active_run()
                self._current_client = None
                self._current_run_id = None

            was_interrupted = self._interrupt_requested
            self._interrupt_requested = False

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
                was_interrupted=was_interrupted,
            )

            self._persist_trace(workspace_dir, metadata, collected, result_text)

            await self.events.log(
                "INFO",
                f"run finished {run_id} duration={duration:.1f}s "
                f"turns={n_turns} tools={n_tool_calls} cost=${total_cost:.4f} "
                f"error={is_error}",
                run_id=run_id,
            )

            output_files = discover_output_files(outputs_dir)

            yield DoneChunk(
                result=RunResult(
                    run_id=run_id,
                    output_text=result_text,
                    blocks=collected,
                    metadata=metadata,
                    audit_dir=audit_dir,
                    workspace_dir=workspace_dir,
                    output_data=output_data,
                    output_files=output_files,
                ),
            )

    # ── helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _mint_run_id() -> str:
        return f"run-{int(time.time())}-{uuid.uuid4().hex[:8]}"

    def _setup_workspace(
        self,
        run_id: str,
        attachments_items: list,
        skills_items: list,
        disallowed_skills: list[str],
    ) -> tuple[Path, Path, Path, Path, dict[str, str], dict[str, str]]:
        """Create workspace dirs and materialize attachments + skills.

        Layout: CLAUDE_CONFIG_DIR is a SIBLING of the SDK cwd, never a
        descendant. When `.claude/` lives inside the cwd, the CLI
        mis-resolves session/projects paths and recreates the workspace
        structure inside cwd as `./.runs/<RID>/agent_cwd/.claude/`
        (verified empirically). Sibling layout avoids it.

            <run>/                  ← motor-owned root
              input.json, trace.json, audit/
              .claude/              ← CLAUDE_CONFIG_DIR (sibling of agent_cwd)
                skills/             ← link to source
              agent_cwd/            ← SDK cwd (agent sandbox)
                attachments/        ← link/inline
                outputs/            ← agent Write target

        Returns (workspace_dir, agent_cwd, audit_dir, claude_dir,
        attachments_manifest, skills_manifest).
        """
        workspace_dir = self.config.workspace_root / run_id
        audit_dir = workspace_dir / "audit"
        claude_dir = workspace_dir / ".claude"
        skills_dir = claude_dir / "skills"
        plugins_dir = claude_dir / "plugins"
        agent_cwd = workspace_dir / "agent_cwd"
        attachments_dir = agent_cwd / "attachments"
        outputs_dir = agent_cwd / "outputs"
        for d in (workspace_dir, audit_dir, agent_cwd, attachments_dir,
                  outputs_dir, claude_dir, skills_dir, plugins_dir):
            d.mkdir(parents=True, exist_ok=True)

        # Pre-seed CLAUDE_CONFIG_DIR with an empty plugins manifest so the
        # CLI doesn't try to load anything from disk on startup.
        self._seed_claude_config_dir(claude_dir, plugins_dir)

        attachments_manifest = _materialize_attachments(
            attachments_dir, attachments_items,
        )
        skills_manifest = _materialize_skills(
            skills_dir, skills_items, disallowed_skills,
        )
        return (
            workspace_dir,
            agent_cwd,
            audit_dir,
            claude_dir,
            attachments_manifest,
            skills_manifest,
        )

    def _apply_config_defaults(self, task: RunTask) -> RunTask:
        """Return a RunTask with MotorConfig defaults filled in for any
        field the caller left unset.

        Override semantics: explicit `None` / empty-list / empty-string on the
        task means "use the config default". Anything else (including `[]`
        for `tools` to mean "no tools") is treated as an explicit choice
        and wins over the default.
        """
        cfg = self.config
        return RunTask(
            prompt=task.prompt,
            system=task.system if task.system is not None else cfg.default_system,
            tools=task.tools if task.tools is not None else cfg.default_tools,
            allowed_tools=(
                task.allowed_tools if task.allowed_tools is not None
                else cfg.default_allowed_tools
            ),
            disallowed_tools=(
                task.disallowed_tools if task.disallowed_tools is not None
                else list(cfg.default_disallowed_tools)
            ),
            max_turns=task.max_turns if task.max_turns is not None else cfg.default_max_turns,
            attachments=task.attachments if task.attachments is not None else cfg.default_attachments,
            skills=task.skills if task.skills is not None else cfg.default_skills,
            disallowed_skills=(
                list(task.disallowed_skills) if task.disallowed_skills
                else list(cfg.default_disallowed_skills)
            ),
            output_schema=(
                task.output_schema if task.output_schema is not None
                else cfg.default_output_schema
            ),
        )

    def _seed_claude_config_dir(self, claude_dir: Path, plugins_dir: Path) -> None:
        """Seed an empty plugins manifest so the CLI doesn't try to load
        anything from disk on startup. CLAUDE.md auto-loading is gated by
        the CLAUDE_CODE_DISABLE_CLAUDE_MDS env var (set in _build_sdk_options
        when self.config.disable_claude_md is True), not by .config.json.
        """
        plugins_file = plugins_dir / "installed_plugins.json"
        if not plugins_file.exists():
            plugins_file.write_text(
                '{"version": 2, "plugins": {}}', encoding="utf-8",
            )

    def _persist_input(
        self,
        workspace_dir: Path,
        run_id: str,
        task: RunTask,
        attachments_manifest: dict[str, str],
        skills_manifest: dict[str, str],
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
                "skills": skills_manifest,
                "disallowed_skills": list(task.disallowed_skills),
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
        agent_cwd: Path,
        claude_dir: Path,
        api_key: str,
        skills_allowed: Optional[list[str]] = None,
    ) -> ClaudeAgentOptions:
        env: dict[str, str] = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "ANTHROPIC_API_KEY": api_key,
            # CLAUDE_CONFIG_DIR makes the SDK CLI look for skills under
            # <claude_dir>/skills/ instead of the default ~/.claude.
            "CLAUDE_CONFIG_DIR": str(claude_dir),
            # default model env vars used by the Claude CLI
            "ANTHROPIC_DEFAULT_OPUS_MODEL": self.config.model,
            "ANTHROPIC_DEFAULT_SONNET_MODEL": self.config.model,
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": self.config.model,
            # we want all tools loaded upfront, no deferred ToolSearch dance
            "ENABLE_TOOL_SEARCH": "false",
            # ── CLI native disable flags (extracted from binary symbols) ──
            # Stops the CLI from doing housekeeping that's irrelevant to a
            # programmatic motor run. Without these, the binary writes
            # session.jsonl, .claude.json, backups under a cwd-derived
            # fallback path and triggers telemetry + title/onboarding.
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "CLAUDE_CODE_DISABLE_FILE_CHECKPOINTING": "1",
            "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1",
            "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
            "CLAUDE_CODE_DISABLE_FEEDBACK_SURVEY": "1",
            "CLAUDE_CODE_DISABLE_TERMINAL_TITLE": "1",
            "CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS": "1",
            # Generic anti-noise (DISABLE_* without CLAUDE_CODE_ prefix)
            "DISABLE_TELEMETRY": "1",
            "DISABLE_ERROR_REPORTING": "1",
            "DISABLE_AUTOUPDATER": "1",
            "DISABLE_AUTO_COMPACT": "1",
            "DISABLE_BUG_COMMAND": "1",
        }
        if self.config.disable_claude_md:
            # Native env-var alternative to tengu_paper_halyard in
            # .config.json — both work, env is just less ceremonial.
            env["CLAUDE_CODE_DISABLE_CLAUDE_MDS"] = "1"
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

        # Conflict resolution: a tool explicitly listed in `tools` is the
        # caller's stated intent — drop it from the resolved disallowed
        # set so the SDK doesn't expose-and-then-block it (which would
        # waste tokens and surprise the model). The block list still
        # protects every tool the caller did NOT opt into.
        if task.tools:
            disallowed = [t for t in disallowed if t not in task.tools]

        # `tools` (hard whitelist) only set when explicitly provided.
        # SDK signature: list[str] | ToolsPreset | None. Pass-through.
        sdk_kwargs: dict = {
            "system_prompt": task.system,
            "allowed_tools": task.allowed_tools or [],
            "disallowed_tools": disallowed,
            "max_turns": task.max_turns or self.config.default_max_turns,
            "permission_mode": "bypassPermissions",
            "cwd": str(agent_cwd),
            "env": env,
            "plugins": [],
            "setting_sources": ["project"],
            # Stream raw API deltas (text_delta, input_json_delta, thinking_delta)
            # alongside the regular AssistantMessage/UserMessage envelope. Required
            # for `motor.stream(task)` to emit live token chunks; harmless overhead
            # for `motor.run(task)` which collects.
            "include_partial_messages": True,
        }
        if task.tools is not None:
            sdk_kwargs["tools"] = task.tools

        # Pass an explicit `skills` whitelist to the SDK whenever we know
        # which skills the run materialised. The Claude CLI ships a set
        # of bundled skills (update-config, simplify, review, ...) that
        # would otherwise leak into the agent's reminder catalogue —
        # nothing the dev configured, just SDK noise. An explicit list
        # (even an empty one) restricts the Skill tool to that set.
        if skills_allowed is not None:
            sdk_kwargs["skills"] = list(skills_allowed)

        # Built-in PreToolUse hook — sandbox the agent based on config.guardrail.
        # The hook matches on every tool name (matcher=None catches all) and
        # delegates the per-tool checks to make_guard_hook. Setting
        # guardrail='off' returns None and we skip the hooks block entirely.
        guard_hook = make_guard_hook(self.config.guardrail)  # type: ignore[arg-type]
        if guard_hook is not None:
            sdk_kwargs["hooks"] = {
                "PreToolUse": [HookMatcher(hooks=[guard_hook])],
            }

        # extra_args: arbitrary CLI flags forwarded to the Claude Code CLI
        # subprocess. We use it to pass --bare and --no-session-persistence
        # which are documented CLI flags but not exposed as named SDK options.
        extra_args: dict = {}
        if self.config.cli_bare_mode:
            extra_args["bare"] = None  # boolean flag (no value)
        if self.config.cli_no_session_persistence:
            extra_args["no-session-persistence"] = None
        # Strict structured output: forward the Pydantic-derived JSON Schema
        # to the CLI's `--json-schema` flag. The CLI validates the model's
        # final structured payload server-side; the result lands in
        # ResultMessage.structured_output and we Pydantic-validate it into
        # RunResult.output_data. NEVER set --output-format here: it conflicts
        # with the SDK's stream-json IPC and crashes the subprocess.
        if task.output_schema is not None:
            extra_args["json-schema"] = json.dumps(
                task.output_schema.model_json_schema(),
            )
        if extra_args:
            sdk_kwargs["extra_args"] = extra_args

        return ClaudeAgentOptions(**sdk_kwargs)


def _output_file_ready_chunk(
    tool_name: str,
    tool_input: dict,
    agent_cwd: Path,
    outputs_dir: Path,
) -> Optional[OutputFileReadyChunk]:
    """Build an OutputFileReadyChunk if the tool wrote a file under outputs/.

    Returns None when:
      - input has no file_path (malformed)
      - the resolved path is not under `outputs_dir` (the guard would have
        blocked it; we double-check so the chunk is never misleading)

    The path resolution mirrors what the SDK does: relative paths are
    interpreted against the agent's cwd, absolute paths used as-is.
    """
    raw = tool_input.get("file_path")
    if not isinstance(raw, str) or not raw:
        return None
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = agent_cwd / candidate
    try:
        resolved = candidate.resolve()
        outputs_resolved = outputs_dir.resolve()
        rel = resolved.relative_to(outputs_resolved)
    except (OSError, ValueError):
        return None
    return OutputFileReadyChunk(
        relative_path=str(rel),
        path=str(resolved),
        tool=tool_name,
    )


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
# polymorphic input normalization
# ─────────────────────────────────────────────────────────────────────────

def _normalize_to_list(value) -> list:
    """Normalize single | list | None → list. Pure helper, no side effects."""
    if value is None:
        return []
    if isinstance(value, list):
        return list(value)
    return [value]


# ─────────────────────────────────────────────────────────────────────────
# attachments — pre-flight validation + materialization (LINK by default)
#
# `_validate_attachments` runs BEFORE any state mutation: no run_id minted,
# no workspace created, no proxy bound, no SDK called.
#
# `_materialize_attachments` runs only after validation succeeds. Real
# paths become symlinks (no storage waste, no duplication); inline dicts
# are written as real text files.
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


def _link_file(src: Path, target: Path) -> None:
    """Materialize `target` as a zero-copy reference to `src`.

    Strategy: try a hard link first. Hard links share an inode with the
    source — the SDK's Glob tool (which delegates to `ripgrep --files`,
    no `-L`/`--follow` flag) treats them as regular files and includes
    them in results. A plain symlink would be silently ignored by
    ripgrep, hiding the file from the agent.

    Fallback: if the source and target live on different filesystems
    (`EXDEV`) hard-linking is impossible — we degrade to a symlink. The
    agent may then need to be told the file path explicitly (via the
    user prompt) since glob discovery will skip it.
    """
    try:
        os.link(src, target)
    except OSError as e:
        if e.errno != errno.EXDEV:
            raise
        target.symlink_to(src)


def _materialize_attachments(attachments_dir: Path, items: list) -> dict[str, str]:
    """Materialize attachments under `attachments_dir`.

    - **Inline `dict[str, str]`** → real file written verbatim.
    - **`Path` to a file** → hard-linked (or symlink fallback on EXDEV).
    - **`Path` to a directory** → mirrored as a real directory tree,
      with each leaf file hard-linked to the source (or symlink
      fallback on EXDEV). A single dir-level symlink would be invisible
      to glob, so we always rebuild the tree.

    Returns a manifest {final_relative_path → source_repr}:
      - "→ <abs_path> (link)" for hard-linked / symlinked files
      - "<inline>" for inline files
    """
    manifest: dict[str, str] = {}
    for item in items:
        if isinstance(item, dict):
            for rel_path, content in item.items():
                target = attachments_dir / rel_path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
                manifest[str(Path(rel_path))] = "<inline>"
            continue

        src = Path(item).resolve(strict=True)
        if src.is_dir():
            for child in src.rglob("*"):
                if not child.is_file():
                    continue
                rel = child.relative_to(src)
                target = attachments_dir / src.name / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                child_resolved = child.resolve(strict=True)
                _link_file(child_resolved, target)
                manifest[str(Path(src.name) / rel)] = f"→ {child_resolved} (link)"
        else:
            target = attachments_dir / src.name
            _link_file(src, target)
            manifest[src.name] = f"→ {src} (link)"
    return manifest


# ─────────────────────────────────────────────────────────────────────────
# skills — pre-flight validation + materialization
#
# Each source dir is a folder containing skill subdirs. A subdir is a valid
# skill iff it contains SKILL.md. Skills are linked individually into
# <run>/.claude/skills/<skill_name>/ — never copied. Conflict between
# sources (same skill name) raises a clear error.
# ─────────────────────────────────────────────────────────────────────────

def _validate_skills(items: list, disallowed: list[str]) -> None:
    """Pre-flight: every source must exist and be a dir; no name conflicts."""
    seen: dict[str, str] = {}  # skill_name → source_dir_path
    for i, item in enumerate(items):
        if not isinstance(item, (str, Path)):
            raise TypeError(
                f"skills[{i}]: unsupported type {type(item).__name__}; "
                f"must be str | Path"
            )
        src = Path(item)
        if not src.exists():
            raise FileNotFoundError(
                f"skills[{i}]: source dir {str(src)!r} does not exist"
            )
        if not src.is_dir():
            raise ValueError(
                f"skills[{i}]: source path {str(src)!r} is not a directory"
            )
        if not os.access(src, os.R_OK):
            raise PermissionError(
                f"skills[{i}]: source dir {str(src)!r} is not readable"
            )
        for child in sorted(src.iterdir()):
            if not child.is_dir():
                continue
            if not (child / "SKILL.md").is_file():
                continue  # not a skill, just a sibling dir
            name = child.name
            if name in disallowed:
                continue
            if name in seen:
                raise ValueError(
                    f"skills[{i}]: skill {name!r} provided by {str(src)!r} "
                    f"conflicts with same-name skill from {seen[name]!r}; "
                    f"rename one or add to disallowed_skills"
                )
            seen[name] = str(src)


def _materialize_skills(
    skills_dir: Path,
    items: list,
    disallowed: list[str],
) -> dict[str, str]:
    """Create one symlink per enabled skill under `skills_dir`.

    Returns a manifest {skill_name → "→ <abs_source> (link)"}.
    Skipped: subdirs without SKILL.md, or names listed in `disallowed`.
    """
    manifest: dict[str, str] = {}
    for item in items:
        src_root = Path(item).resolve(strict=True)
        for child in sorted(src_root.iterdir()):
            if not child.is_dir():
                continue
            if not (child / "SKILL.md").is_file():
                continue
            name = child.name
            if name in disallowed:
                continue
            target = skills_dir / name
            child_resolved = child.resolve(strict=True)
            target.symlink_to(child_resolved)
            manifest[name] = f"→ {child_resolved} (link)"
    return manifest
