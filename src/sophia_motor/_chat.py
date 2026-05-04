# Copyright (c) 2026 Sophia AI
# SPDX-License-Identifier: MIT
"""Chat — multi-turn dialog with persistent SDK session.

`Chat` is the thin layer that turns the motor's stateless `run()` /
`stream()` into a memory-bearing conversation, the same way
sophia-agent's `agent_service.send_message(chat_id, prompt)` does.

Mechanics:

- A chat owns a **shared workspace** under
  `<workspace_root>/../chats/<chat_id>/`. The cwd, `.claude/`, and
  attachments live here and are reused across turns; each turn's audit
  dump goes under `runs/<run_id>/audit/`.
- The first `send()` mints a new SDK session_id (captured from the
  CLI's init message) and stores it on the Chat instance.
- Subsequent `send()` / `stream()` calls pass that session_id back via
  `RunTask.session_id` — the CLI then resumes the conversation history,
  so the agent "remembers" prior turns.
- `chat.session_id` is a plain string, persistable to any DB / KV /
  file. To resume after a process restart: instantiate `Chat` with the
  saved `chat_id` + `session_id` and call `send()` as usual.

Public surface:

    chat = motor.chat()                           # mint new chat
    chat = motor.chat(chat_id="thread-42")        # custom id
    chat = motor.chat(chat_id="thread-42",
                      session_id=stored)          # resume from DB

    reply = await chat.send("hi")                 # RunResult
    async for chunk in chat.stream("continue"):   # StreamChunk iterator
        ...

    chat.chat_id        # str — stable identifier you control
    chat.session_id     # str | None — persist this to resume later
    chat.cwd            # Path — read-only shared workspace root

    await chat.reset()  # new SDK session, same chat_id + cwd
"""
from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, AsyncIterator, Optional, Union

from ._chunks import DoneChunk, StreamChunk
from ._models import RunResult, RunTask

if TYPE_CHECKING:
    from .motor import Motor


def _mint_chat_id() -> str:
    return f"chat-{uuid.uuid4().hex[:12]}"


class Chat:
    """A persistent multi-turn dialog bound to a `Motor`.

    Construct via `motor.chat(...)` for the common case, or directly
    with `Chat(motor, chat_id=..., session_id=...)` when you need a
    subclass or custom storage.
    """

    def __init__(
        self,
        motor: "Motor",
        *,
        chat_id: Optional[str] = None,
        session_id: Optional[str] = None,
        root: Optional[Path] = None,
    ) -> None:
        """Bind a chat session to `motor`.

        Args:
            motor: the Motor that drives the runs. Many Chat instances
                can share one Motor — the proxy multiplexes them.
            chat_id: stable identifier the caller controls (typically a
                user/thread id from your DB). Auto-minted if None.
            session_id: SDK session_id from a previous run, persisted by
                the caller. Pass it to resume the dialog after a restart.
            root: directory where the chat's shared workspace lives. Defaults
                to `<motor.config.workspace_root>/../chats/<chat_id>/`.
        """
        self.motor = motor
        self.chat_id = chat_id or _mint_chat_id()
        self.session_id: Optional[str] = session_id

        if root is not None:
            self._root = root / self.chat_id
        else:
            # Default sibling of the runs root: <workspace_root>/../chats/<id>/
            # (so chats live next to runs — ephemeral by default in /tmp,
            # persistent if the caller pointed workspace_root at a real path).
            self._root = motor.config.workspace_root.parent / "chats" / self.chat_id
        self._root.mkdir(parents=True, exist_ok=True)

    # ── introspection ─────────────────────────────────────────────────

    @property
    def cwd(self) -> Path:
        """The shared workspace root for this chat. Treat as read-only."""
        return self._root

    # ── send / stream ────────────────────────────────────────────────

    async def send(self, prompt_or_task: Union[str, RunTask]) -> RunResult:
        """Send one message and wait for the final result.

        `prompt_or_task` is either a plain prompt string (the common
        case) or a fully-constructed `RunTask` for per-turn overrides
        (e.g. swap `tools` for a single message). Either way, the
        chat's `session_id` and `workspace_dir` are injected so the
        run resumes the dialog.
        """
        task = self._build_task(prompt_or_task)
        result = await self.motor.run(task)
        if result.metadata.session_id:
            self.session_id = result.metadata.session_id
        return result

    async def stream(
        self,
        prompt_or_task: Union[str, RunTask],
    ) -> AsyncIterator[StreamChunk]:
        """Stream chunks for one message, updating session_id at the end.

        The terminal `DoneChunk` carries the same `RunResult` that
        `send()` would have returned; its `metadata.session_id` is
        what the chat picks up for the next turn.
        """
        task = self._build_task(prompt_or_task)
        async for chunk in self.motor.stream(task):
            if isinstance(chunk, DoneChunk):
                if chunk.result.metadata.session_id:
                    self.session_id = chunk.result.metadata.session_id
            yield chunk

    # ── lifecycle ────────────────────────────────────────────────────

    async def reset(self) -> None:
        """Start a fresh SDK session — same `chat_id`, same `cwd`.

        Use when the user clicks "new chat" but you want to keep the
        same workspace (e.g. the attachments stay around). Drops the
        SDK session.jsonl so the next `send()` starts clean.
        """
        self.session_id = None
        session_dir = self._root / ".claude" / "projects"
        if session_dir.exists():
            shutil.rmtree(session_dir, ignore_errors=True)
        # Also nuke the legacy session.jsonl path some CLI versions use.
        legacy = self._root / ".claude" / "session.jsonl"
        if legacy.exists():
            legacy.unlink()

    # ── helpers ──────────────────────────────────────────────────────

    def _build_task(self, prompt_or_task: Union[str, RunTask]) -> RunTask:
        """Wrap a prompt string into a RunTask, or take an existing one
        and inject the chat's session_id + workspace_dir if not already
        set explicitly."""
        if isinstance(prompt_or_task, str):
            task = RunTask(prompt=prompt_or_task)
        elif isinstance(prompt_or_task, RunTask):
            task = prompt_or_task
        else:
            raise TypeError(
                f"Chat.send/stream expects str or RunTask, got "
                f"{type(prompt_or_task).__name__}"
            )

        # Inject chat-mode bits. Caller-provided values win — if the
        # dev passes a RunTask with an explicit session_id or
        # workspace_dir, respect it (advanced use, e.g. forking).
        if task.session_id is None:
            task.session_id = self.session_id
        if task.workspace_dir is None:
            task.workspace_dir = self._root
        return task
