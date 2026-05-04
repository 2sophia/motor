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

A single Motor instance can drive any number of concurrent runs: the
proxy multiplexes them via per-run path prefixes. Just `await motor.run(...)`
or iterate `motor.stream(...)` from as many tasks as you want — fan out with
`asyncio.gather`, drive from a FastAPI endpoint, whatever — they execute
in parallel without serialization.
"""
from __future__ import annotations

import asyncio
import errno
import json
import os
import re
import shutil
import time
import uuid
from dataclasses import dataclass
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
from ._python_tools import (
    DEFAULT_SERVER_NAME as _PYTOOLS_SERVER,
    ToolMeta,
    compile_python_tools,
    normalize_run_tools,
    serialize_tools_list,
    tool as _python_tool,
)


@dataclass
class _ActiveRun:
    """Per-run state on the Motor side: the SDK client + interrupt flag."""
    client: ClaudeSDKClient
    interrupt_requested: bool = False


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

    Python tool decorator (alias of `sophia_motor.tool`):
        @Motor.tool
        async def my_tool(args: MyInput) -> MyOutput: ...
    """

    # Namespaced alias for `sophia_motor.tool`. Available without an
    # instance so callers can decorate at module top-level before the
    # Motor is constructed:  @Motor.tool  ↔  @tool
    tool = staticmethod(_python_tool)

    def __init__(self, config: Optional[MotorConfig] = None) -> None:
        self.config = config or MotorConfig()
        self.events = EventBus()

        # Expose the EventBus decorators directly on the Motor for ergonomics.
        self.on_event = self.events.on_event
        self.on_log = self.events.on_log

        self._proxy: Optional[ProxyServer] = None
        self._started = False
        # Multi-run registry. Concurrent runs on the same Motor coexist —
        # each gets its own slot keyed by run_id, with its own SDK client
        # and interrupt flag. `motor.interrupt(run_id=...)` finds the right
        # client here; `motor.interrupt()` no-arg is unambiguous only when
        # exactly one run is active.
        self._active_runs: dict[str, _ActiveRun] = {}

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

    # ── chat (multi-turn dialog) ─────────────────────────────────────

    def chat(
        self,
        *,
        chat_id: Optional[str] = None,
        session_id: Optional[str] = None,
        root: Optional[Path] = None,
    ) -> "Chat":
        """Open a multi-turn chat bound to this motor.

        Each `chat.send(prompt)` reuses the same SDK session so the
        agent remembers prior turns. The chat owns a shared workspace
        under `<workspace_root>/../chats/<chat_id>/`; per-turn audit
        dumps still go under `runs/<run_id>/audit/`.

        Persist `chat.chat_id` + `chat.session_id` to your DB to resume
        the dialog after a restart — pass them back here as kwargs.

        See `Chat` for the full API (`send`, `stream`, `reset`, etc).
        """
        from ._chat import Chat
        return Chat(self, chat_id=chat_id, session_id=session_id, root=root)

    # ── interactive console ──────────────────────────────────────────

    async def console(self) -> None:
        """Open an interactive REPL bound to this Motor.

        Renders the agent's stream live (text deltas, tool use lifecycle,
        file outputs) with `rich`, reads user input via `prompt-toolkit`
        with history + multiline + slash-command autocomplete. Pre-configure
        the motor with `default_*` (tools, system, attachments, skills, …)
        and the console will use them on every turn — type prompts and watch
        the agent work.

        Slash commands inside the console: `/help`, `/exit`, `/files`,
        `/audit`, `/clear`. `Ctrl+C` interrupts the running task without
        quitting the console; `Ctrl+D` quits.

        Requires the `[console]` extras:
            pip install sophia-motor[console]
        """
        # Lazy import so the console deps (rich, prompt-toolkit) are only
        # required for users who actually call this method.
        from ._console import run_console

        await run_console(self)

    # ── interrupt ────────────────────────────────────────────────────

    async def interrupt(self, run_id: Optional[str] = None) -> bool:
        """Interrupt a run currently in flight on this motor.

        This is the "Ctrl+C" of an active run — a deliberate user action,
        not an error. Distinct from `stop()` (which shuts the motor down).

        Mechanics: signals the SDK CLI subprocess for that run via
        `client.interrupt()`, which exits the current turn. The
        `async with ClaudeSDKClient` cleanup then closes the subprocess
        and the TCP to our proxy, letting upstream abort. The matching
        `motor.stream(task)` finishes normally with a `DoneChunk` whose
        `result.metadata.was_interrupted` is True (`is_error` stays False).

        Args:
            run_id: which run to interrupt. Required disambiguation when
                more than one run is active on this motor. `None` is
                allowed only when exactly one run is active (then it
                targets that one).

        Returns:
            True if a run was actively interrupted, False if no matching
            run is active. Idempotent; never raises in the no-active-run
            case.

        Raises:
            RuntimeError: when `run_id` is None and more than one run is
                active on the motor (caller must specify which).
        """
        if not self._active_runs:
            return False
        if run_id is None:
            if len(self._active_runs) > 1:
                raise RuntimeError(
                    f"motor has {len(self._active_runs)} active runs "
                    f"({list(self._active_runs)}); pass run_id=... to "
                    f"disambiguate which one to interrupt"
                )
            run_id = next(iter(self._active_runs))
        active = self._active_runs.get(run_id)
        if active is None:
            return False
        active.interrupt_requested = True
        try:
            await active.client.interrupt()
        except Exception as e:  # noqa: BLE001
            await self.events.log(
                "WARNING",
                f"interrupt() raised: {e!r} (treating as best-effort)",
                run_id=run_id,
            )
        await self.events.log("INFO", "interrupt requested", run_id=run_id)
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
            chat_workspace_dir=task.workspace_dir,
        )
        if self.config.persist_run_metadata:
            self._persist_input(
                workspace_dir, run_id, task,
                attachments_manifest, skills_manifest,
            )

        if self._proxy is not None:
            self._proxy.register_run(run_id, audit_dir)

        outputs_dir = agent_cwd / "outputs"

        opts = self._build_sdk_options(
            task, agent_cwd, claude_dir, api_key, run_id,
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
        # SDK session_id reported by the first SystemMessage(init); surfaced
        # on RunResult.metadata so callers (typically Chat) can persist + resume.
        current_session_id: Optional[str] = None

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

        try:
            async with ClaudeSDKClient(options=opts) as client:
                self._active_runs[run_id] = _ActiveRun(client=client)
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
                            sid = (
                                data.get("session_id")
                                if isinstance(data, dict) else None
                            )
                            current_session_id = sid
                            yield InitChunk(session_id=sid)

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
                        active_run = self._active_runs.get(run_id)
                        interrupt_was_requested = (
                            active_run is not None and active_run.interrupt_requested
                        )
                        is_error = (
                            bool(message.is_error) and not interrupt_was_requested
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
            active_run = self._active_runs.get(run_id)
            if active_run is not None and active_run.interrupt_requested:
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
                self._proxy.unregister_run(run_id)

        run_state = self._active_runs.pop(run_id, None)
        was_interrupted = (
            run_state is not None and run_state.interrupt_requested
        )

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
            session_id=current_session_id,
        )

        if self.config.persist_run_metadata:
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
        chat_workspace_dir: Optional[Path] = None,
    ) -> tuple[Path, Path, Path, Path, dict[str, str], dict[str, str]]:
        """Create workspace dirs and materialize attachments + skills.

        Two modes:

        **Standalone run** (`chat_workspace_dir=None`, the default): a
        fresh `<workspace_root>/<run_id>/` is created, with audit, cwd,
        and `.claude/` all under it. Each call to `motor.run()` is
        isolated — no carry-over.

        **Chat-mode** (`chat_workspace_dir` set, used by `Chat`): the
        chat root is reused across runs. Layout:

            <chat_root>/                        ← shared, persistent
              cwd/                              ← shared SDK cwd
                attachments/, outputs/
              .claude/                          ← shared CLAUDE_CONFIG_DIR
                skills/, plugins/, session.jsonl   ← session continuity
              runs/<run_id>/
                input.json, trace.json
                audit/                          ← per-run audit dump

        The CLAUDE_CONFIG_DIR-as-sibling-of-cwd layout is preserved in
        both modes. The CLI's upward project-root discovery quirk is
        avoided as long as `workspace_root` (or the chat root) lives
        outside repos that contain `.git/` / `pyproject.toml` markers.

        Returns (workspace_dir, agent_cwd, audit_dir, claude_dir,
        attachments_manifest, skills_manifest). `workspace_dir` is the
        per-run dir (chat-mode: under `runs/<run_id>/`; standalone: the
        same as the run root).
        """
        if chat_workspace_dir is not None:
            chat_root = chat_workspace_dir
            workspace_dir = chat_root / "runs" / run_id
            audit_dir = workspace_dir / "audit"
            claude_dir = chat_root / ".claude"
            skills_dir = claude_dir / "skills"
            plugins_dir = claude_dir / "plugins"
            agent_cwd = chat_root / "cwd"
            attachments_dir = agent_cwd / "attachments"
            outputs_dir = agent_cwd / "outputs"
        else:
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
            agents=(
                task.agents if task.agents is not None
                else dict(cfg.default_agents)
            ),
            custom_pre_tool_hooks=(
                task.custom_pre_tool_hooks
                if task.custom_pre_tool_hooks is not None
                else list(cfg.custom_pre_tool_hooks)
            ),
            # Chat-mode bits don't have config defaults — they're either
            # set by Chat (which manages them) or left None for standalone
            # runs. Either way the value passes through unchanged.
            session_id=task.session_id,
            workspace_dir=task.workspace_dir,
        )

    def _seed_claude_config_dir(self, claude_dir: Path, plugins_dir: Path) -> None:
        """Pre-seed CLAUDE_CONFIG_DIR. Currently a no-op: the CLI no longer
        creates `installed_plugins.json` on startup once
        CLAUDE_CODE_DISABLE_OFFICIAL_MARKETPLACE_AUTOINSTALL=1 is set
        (see _build_sdk_options). Kept as a hook for future seeding needs
        (e.g. policy file overlays). CLAUDE.md auto-loading is gated by
        CLAUDE_CODE_DISABLE_CLAUDE_MDS, not by anything written here.
        """
        return

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
                # tools may contain @tool callables; render as readable
                # `mcp__sophia__<name>` strings for the audit log.
                "tools": serialize_tools_list(task.tools),
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
        run_id: str,
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
            # File checkpointing is the CLI's `/rewind` undo feature for
            # interactive sessions — tracks code edits so the user can
            # revert. Irrelevant to programmatic motor runs (we never
            # rewind), and unrelated to session.jsonl conversation
            # persistence (controlled by --no-session-persistence).
            # Always disabled.
            "CLAUDE_CODE_DISABLE_FILE_CHECKPOINTING": "1",
            "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1",
            "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
            "CLAUDE_CODE_DISABLE_FEEDBACK_SURVEY": "1",
            "CLAUDE_CODE_DISABLE_TERMINAL_TITLE": "1",
            "CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS": "1",
            # Stop the CLI from creating/touching <CLAUDE_CONFIG_DIR>/plugins/
            # installed_plugins.json on startup. Without this the binary
            # writes a default {version:2, plugins:{}} manifest into the
            # ridirected config dir (~30 byte residual write per run).
            "CLAUDE_CODE_DISABLE_OFFICIAL_MARKETPLACE_AUTOINSTALL": "1",
            # Stop the CLI from materializing per-subagent meta files under
            # <CLAUDE_CONFIG_DIR>/projects/<sanitized-cwd>/<session>/subagents/
            # agent-<hash>.meta.json. Subagent definitions are passed in-memory
            # via SDK options; the on-disk meta is only used by the interactive
            # /agents UI which programmatic runs don't expose.
            "CLAUDE_CODE_DISABLE_AGENTS_FLEET": "1",
            # Generic anti-noise (DISABLE_* without CLAUDE_CODE_ prefix)
            "DISABLE_TELEMETRY": "1",
            "DISABLE_ERROR_REPORTING": "1",
            "DISABLE_AUTOUPDATER": "1",
            "DISABLE_AUTO_COMPACT": "1",
            "DISABLE_BUG_COMMAND": "1",
            "DISABLE_INSTALLATION_CHECKS": "1",
            "DISABLE_UPDATES": "1",
            # Strip the bundled CLI's built-in subagents (Explore,
            # general-purpose, Plan, statusline-setup, ...) from the
            # Agent tool description. Without this, every custom agent
            # the dev declares is mixed with 4+ Claude-Code-CLI defaults
            # the dev did not ask for, and the model frequently picks
            # one of those (e.g. general-purpose) over the custom ones.
            # Empirically verified via proxy dump (request_001 Agent
            # tool description) on 2026-05-02.
            "CLAUDE_AGENT_SDK_DISABLE_BUILTIN_AGENTS": "1",
        }
        if self.config.disable_claude_md:
            # Native env-var alternative to tengu_paper_halyard in
            # .config.json — both work, env is just less ceremonial.
            env["CLAUDE_CODE_DISABLE_CLAUDE_MDS"] = "1"
        if self._proxy is not None:
            # Per-run path prefix: each concurrent run gets its own URL
            # under /run/<id>/, so the proxy routes the request + audit
            # dump to the correct slot without singleton state.
            env["ANTHROPIC_BASE_URL"] = self._proxy.base_url_for_run(run_id)
            env["ANTHROPIC_AUTH_TOKEN"] = api_key

        # Single sweep across task.tools + agents — collect every Python
        # callable referenced anywhere in this run, dedupe by meta.name,
        # validate, compile into ONE in-process MCP server, and produce
        # parent + agent tool lists with callables rewritten to
        # `mcp__sophia__<name>`. From here on the motor talks pure
        # SDK-style strings; the heterogeneous str|callable entry point
        # is fully behind us.
        all_metas, parent_tools, normalized_agents = normalize_run_tools(
            task.tools, task.agents,
        )

        audit_dir = agent_cwd.parent / "audit"
        py_mcp_server, _ = compile_python_tools(
            all_metas,
            run_id=run_id,
            audit_dir=audit_dir,
            agent_cwd=agent_cwd,
            event_bus=self.events,
            server_name=_PYTOOLS_SERVER,
            dump_audit=self.config.proxy_dump_payloads,
        )

        # `tool_strings` retains the str-only view of the parent (used by
        # the disallowed_tools conflict resolution + the subagent gating
        # below). It's the str entries from the original `task.tools`,
        # not the prefixed callables.
        tool_strings = (
            None if parent_tools is None
            else [t for t in parent_tools if not t.startswith(f"mcp__{_PYTOOLS_SERVER}__")]
        )

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
        if tool_strings:
            disallowed = [t for t in disallowed if t not in tool_strings]

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
        if parent_tools is not None:
            sdk_kwargs["tools"] = list(parent_tools)

        # Mount the in-process MCP server hosting the Python tools.
        if py_mcp_server is not None:
            sdk_kwargs["mcp_servers"] = {_PYTOOLS_SERVER: py_mcp_server}

        # Subagents — by-design opt-in. If the caller asked for subagents
        # via task.agents (or MotorConfig.default_agents), the `Agent` tool
        # must be reachable: present in `tools` (when an explicit whitelist
        # is given) and absent from `disallowed_tools`. We refuse to silently
        # auto-fix the toolset — the dev declares what the agent can do.
        if task.agents:
            agent_in_tools = tool_strings is None or "Agent" in tool_strings
            agent_blocked = "Agent" in disallowed
            if not agent_in_tools or agent_blocked:
                missing = []
                if not agent_in_tools:
                    missing.append("add 'Agent' to RunTask.tools (or MotorConfig.default_tools)")
                if agent_blocked:
                    missing.append(
                        "remove 'Agent' from RunTask.disallowed_tools (or override "
                        "MotorConfig.default_disallowed_tools to a list without it)"
                    )
                raise RuntimeError(
                    "Subagents require the `Agent` tool to be reachable, but it is "
                    "not — by design the motor does NOT auto-fix the toolset. To "
                    "enable subagents: " + "; ".join(missing) + ". "
                    "See examples/subagents/ for the canonical setup."
                )
            # Pass the agents view with @tool callables already rewritten
            # to `mcp__sophia__<name>`. Untouched if the dev declared
            # `tools=None` (inheritance) or only used strings.
            sdk_kwargs["agents"] = normalized_agents

        # Resume an existing SDK session (chat-style multi-turn). The CLI
        # replays the prior conversation history before processing the new
        # prompt. For this to work the CLI needs `session.jsonl` to exist
        # under CLAUDE_CONFIG_DIR — Chat sets up the shared cwd + config
        # dir + disables file-checkpointing-disable so the file persists.
        if task.session_id is not None:
            sdk_kwargs["resume"] = task.session_id

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
        # guardrail='off' returns None and we drop the built-in hook —
        # custom hooks are still composed in below.
        pretool_hooks: list = []
        guard_hook = make_guard_hook(self.config.guardrail)  # type: ignore[arg-type]
        if guard_hook is not None:
            pretool_hooks.append(guard_hook)
        # User-supplied PreToolUse hooks run AFTER the built-in guard.
        # The SDK runs all hooks in the matcher block; any single deny
        # wins (logical AND of allow). Order matters only for log /
        # observability — the built-in guard's BLOCKED warning fires
        # first, which is what you want when debugging deny chains.
        #
        # Resolution: `_apply_config_defaults` normally fills
        # `task.custom_pre_tool_hooks` from the config when None. We
        # also fall back to `self.config.custom_pre_tool_hooks` here so
        # that callers who build SDK options directly (tests, advanced
        # use) still see the config defaults — and `[]` explicitly on
        # the task means "drop the config defaults", not "use config".
        custom_hooks = (
            task.custom_pre_tool_hooks
            if task.custom_pre_tool_hooks is not None
            else self.config.custom_pre_tool_hooks
        )
        pretool_hooks.extend(custom_hooks)
        if pretool_hooks:
            sdk_kwargs["hooks"] = {
                "PreToolUse": [HookMatcher(hooks=pretool_hooks)],
            }

        # extra_args: arbitrary CLI flags forwarded to the Claude Code CLI
        # subprocess. We use it to pass --bare and --no-session-persistence
        # which are documented CLI flags but not exposed as named SDK options.
        extra_args: dict = {}
        if self.config.cli_bare_mode:
            extra_args["bare"] = None  # boolean flag (no value)
        # `--no-session-persistence` tells the CLI to skip session.jsonl
        # writes. We default to ON for standalone runs (no follow-up,
        # so no need to persist) but MUST skip it for chat-mode runs —
        # the next turn's --resume needs the file on disk.
        if self.config.cli_no_session_persistence and task.workspace_dir is None:
            extra_args["no-session-persistence"] = None
        # `--strict-mcp-config` tells the CLI to use ONLY the MCP servers
        # passed via --mcp-config (the SDK forwards motor.mcp_servers there)
        # and ignore every other MCP source: claude.ai proxy connectors,
        # .mcp.json file walk from cwd upward, user settings MCP, plugin MCP.
        # For programmatic motor runs this is exactly the boundary we want:
        # only Python @tool-decorated functions registered by the dev reach
        # the model; nothing the host machine happens to have lying around.
        # Skipped under chat-mode workspace_dir (caller already chose what's
        # ambient) — same gate as no-session-persistence.
        if self.config.cli_strict_mcp_config and task.workspace_dir is None:
            extra_args["strict-mcp-config"] = None
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


_TOOL_RESULT_REMINDER_RE = re.compile(
    r"<system-reminder>.*?</system-reminder>\s*",
    re.DOTALL | re.IGNORECASE,
)


def _tool_result_preview(block: ToolResultBlock) -> str:
    """Best-effort short preview of a tool_result block content.

    Strips the `<system-reminder>...</system-reminder>` blocks the CLI
    appends to tool_result content (live instructions to the model,
    e.g. "remember to use the Skill tool"). Those reminders are noise
    in a UI preview — surface only the actual tool output the user
    wants to see.
    """
    content = getattr(block, "content", None)
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        text = ""
        for c in content:
            if isinstance(c, dict) and c.get("type") == "text":
                text = c.get("text") or ""
                break
    else:
        return ""
    cleaned = _TOOL_RESULT_REMINDER_RE.sub("", text).strip()
    return cleaned[:200]


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
    if target.exists() or target.is_symlink():
        # Idempotent re-link: chat-mode reuses the same attachments dir
        # across turns, so the second run finds the file already there.
        # Drop and re-link so the source can change between turns and
        # picking the latest is correct.
        target.unlink()
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
            if target.exists() or target.is_symlink():
                # Idempotent re-link for chat-mode reuse.
                target.unlink()
            target.symlink_to(child_resolved)
            manifest[name] = f"→ {child_resolved} (link)"
    return manifest
