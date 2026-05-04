# Copyright (c) 2026 Sophia AI
# SPDX-License-Identifier: MIT
"""Input/output dataclasses for `Motor.run`."""
from __future__ import annotations

import mimetypes
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Union

from pydantic import BaseModel


# ─────────────────────────────────────────────────────────────────────────
# attachments — polymorphic input
#
# Accepts a single value or a list. Valid forms (free mix in a list):
#
#   1. str | Path  →  real file       → hard-link in <run>/attachments/<name>
#   2. str | Path  →  real directory  → mirrored real-tree of file-level
#                                       hard-links in <run>/attachments/<name>/
#   3. dict[str, str]  →  inline      → file written to <run>/attachments/<relpath>
#
# Default: HARD-LINK for Path entries (zero-copy, same inode). Required
# because the SDK Glob tool delegates to `ripgrep --files` without `-L`,
# which silently skips symlinks — hard links are seen as regular files
# and remain discoverable. Cross-filesystem (EXDEV) falls back to a
# symlink: in that case glob discovery may miss the file, so the dev
# should pass the explicit path in the prompt.
#
# The audit trail goes through the SSE dump in <run>/audit/, not through
# the filesystem under attachments/. A hard link that the source mutates
# afterwards does not weaken the defense: the dumped tool_result records
# the TEXT the model read, not the current bytes on disk.
#
# Sandbox escape (malicious links to /etc/passwd etc.): trusted-dev
# assumption. For applications exposing untrusted end-users, layer a
# PreToolUse guard above the motor (see `guard.py`).
#
# Pre-flight checks before tokens are spent:
#   - missing path → FileNotFoundError
#   - not a file or directory → ValueError
#   - unreadable → PermissionError
#   - dict key absolute or containing `..` → ValueError
#   - dict value not a str → TypeError
#   - two items resolving to the same path → ValueError (conflict)
# ─────────────────────────────────────────────────────────────────────────
AttachmentItem = Union[str, Path, dict[str, str]]
AttachmentsInput = Union[AttachmentItem, list[AttachmentItem], None]


# ─────────────────────────────────────────────────────────────────────────
# skills — source folder(s) holding the program's SKILL.md files
#
# Accepts a single value or a list (the dev may have skills in multiple
# folders, e.g. one program-specific and one shared org-wide). For each
# source folder the motor:
#   - iterates its subdirectories
#   - among those with a SKILL.md and not in disallowed_skills
#   - creates a symlink <run>/.claude/skills/<skill_name> → <source>/<skill_name>
#
# Conflict detection: if two source folders provide a skill with the same
# name → clear ValueError. The dev renames one or uses disallowed_skills.
#
# Pre-flight checks:
#   - missing skills path → FileNotFoundError
#   - path is not a directory → ValueError
#   - name conflict across sources → ValueError
# ─────────────────────────────────────────────────────────────────────────
SkillsInput = Union[str, Path, list[Union[str, Path]], None]


@dataclass
class RunTask:
    """Single-shot input to `Motor.run()`.

    Tool semantics — confirmed against the Claude Agent SDK source:

      tools (HARD WHITELIST — what the model can SEE):
        None       → fall back to MotorConfig.default_tools (default `[]`)
        []         → no tools at all
        ["Read"]   → only Read is available; everything else does not exist

        The motor's MotorConfig.default_tools is `[]` out of the box —
        principle of least privilege: a fresh `Motor()` exposes zero
        tools and the dev opts in. Set MotorConfig.default_tools to
        `None` (advanced) to hand selection back to the SDK preset
        (every built-in available).

      allowed_tools (PERMISSION SKIP — rarely needed):
        The motor runs the SDK with `permission_mode="bypassPermissions"`,
        which already auto-approves every tool call. `allowed_tools` is
        therefore redundant for typical use — leave it `None`. Set it
        only if you switch the underlying permission_mode (advanced).

      disallowed_tools (HARD BLOCK — removed from the model's context):
        Use for "never ever" tools (WebFetch, agent spawning, ...).
    """
    prompt: str
    system: Optional[str] = None
    # tools accepts a heterogeneous list: str entries are built-in CLI
    # tool names, callables are @tool-decorated Python functions mounted
    # as in-process MCP and exposed as `mcp__sophia__<name>`.
    tools: Optional[list[Any]] = None
    allowed_tools: Optional[list[str]] = None
    disallowed_tools: Optional[list[str]] = None
    max_turns: Optional[int] = None

    # input data — single Path | str | dict, or a mixed list
    attachments: AttachmentsInput = None

    # code/tooling — single Path | str, or a list
    skills: SkillsInput = None
    disallowed_skills: list[str] = field(default_factory=list)

    # Subagents — dict of name → AgentDefinition (forwarded to ClaudeAgentOptions).
    # When non-empty, the model spawns isolated subagents via the Agent tool. The
    # caller MUST also include "Agent" in `tools` and ensure it is NOT in
    # `disallowed_tools` — otherwise the motor raises a clear RuntimeError at
    # run time. None falls back to MotorConfig.default_agents (full replacement,
    # never merged). Pass `{}` to explicitly disable subagents for this task even
    # when the motor has defaults set.
    agents: Optional[dict[str, Any]] = None

    # Strict structured output. When provided, the motor extracts the JSON
    # Schema via `output_schema.model_json_schema()` and forwards it to the
    # CLI via `--json-schema`. The CLI validates the model's structured
    # response server-side (constraints honored: enum, range, pattern,
    # additionalProperties:false, nested objects). The agent runs its full
    # multi-turn loop (tool calls, reasoning, cross-reference) and at the
    # end emits BOTH a free-text `result` AND a schema-conforming
    # `structured_output`. Pydantic-validated into `RunResult.output_data`.
    output_schema: Optional[type[BaseModel]] = None

    # SDK session to resume. When set, the underlying CLI replays the prior
    # conversation history before processing `prompt` — the agent "remembers"
    # past turns. Most callers should not set this directly; use the `Chat`
    # helper, which manages session_id roundtrip + shared workspace + file
    # checkpointing automatically. Setting `session_id` here without those
    # bits in place will silently fall back to a new session if the SDK
    # can't find the matching session.jsonl on disk.
    session_id: Optional[str] = None

    # Pre-existing chat workspace to reuse instead of minting a fresh one
    # under `MotorConfig.workspace_root`. When set, the run's cwd, .claude
    # dir, and CLAUDE_CONFIG_DIR all point INSIDE this directory, so SDK
    # session.jsonl persistence works across consecutive runs (the CLI's
    # file-checkpointing is left ENABLED for these runs). Audit dumps for
    # each turn live under `<workspace_dir>/runs/<run_id>/audit/`. Most
    # callers should not pass this directly — use `Chat` which sets it.
    workspace_dir: Optional[Path] = None


@dataclass
class RunMetadata:
    """Per-run telemetry."""
    run_id: str
    duration_s: float
    n_turns: int
    n_tool_calls: int
    input_tokens: int = 0
    output_tokens: int = 0
    total_cost_usd: float = 0.0
    is_error: bool = False
    error_reason: Optional[str] = None
    # True when the run terminated because `motor.interrupt()` was called.
    # `is_error` stays False: an interrupt is a deliberate user action, not
    # an upstream failure. Inspect this field to render "interrupted" UI
    # state distinct from both success and error.
    was_interrupted: bool = False
    # SDK session_id reported by the CLI's init message. Persist this to
    # resume the dialog later (e.g. for chat-style multi-turn). For runs
    # outside a `Chat`, this field is informational only — the run started
    # a fresh session unless `RunTask.session_id` was passed in.
    session_id: Optional[str] = None


@dataclass
class OutputFile:
    """A file the agent wrote under `<run>/agent_cwd/outputs/`.

    The `path` is an absolute path inside the run workspace. **The run
    workspace is transient** — it lives until `motor.clean_runs()` or an
    external cleanup wipes it. Persist the file somewhere durable
    (`copy_to(...)` / `move_to(...)`) before that happens, otherwise the
    bytes are gone.
    """
    path: Path
    relative_path: str   # path relative to outputs/, e.g. "report.md"
    size: int            # bytes
    mime: str            # best-effort guess via mimetypes; "application/octet-stream" if unknown
    ext: str             # file extension including the dot, lowercased; "" if none

    def read_bytes(self) -> bytes:
        """Read the file's raw bytes."""
        return self.path.read_bytes()

    def read_text(self, encoding: str = "utf-8") -> str:
        """Read the file as text. Defaults to UTF-8."""
        return self.path.read_text(encoding=encoding)

    def copy_to(self, dest: Path | str) -> Path:
        """Copy the file to `dest` (a directory or a full file path).

        - If `dest` is an existing directory, the file lands at
          `dest / self.relative_path` (parents created as needed).
        - If `dest` is a non-existing path, it's treated as the full
          destination filename.

        Returns the absolute destination path. Use this to persist
        outputs before the run workspace is cleaned up.
        """
        dest_path = Path(dest)
        if dest_path.is_dir():
            dest_path = dest_path / self.relative_path
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.path, dest_path)
        return dest_path.resolve()

    def move_to(self, dest: Path | str) -> Path:
        """Move the file to `dest`. Same dest semantics as `copy_to`.

        After this call, `self.path` no longer points at a real file —
        the OutputFile entry is essentially consumed. Use when you don't
        need the run workspace copy at all (memory-bound pipelines,
        immediate hand-off to S3/blob/db).
        """
        dest_path = Path(dest)
        if dest_path.is_dir():
            dest_path = dest_path / self.relative_path
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(self.path), str(dest_path))
        return dest_path.resolve()


def _output_file_from_path(outputs_dir: Path, file_path: Path) -> OutputFile:
    """Build an OutputFile from an absolute `file_path` under `outputs_dir`."""
    rel = file_path.relative_to(outputs_dir)
    ext = file_path.suffix.lower()
    mime, _ = mimetypes.guess_type(str(file_path))
    return OutputFile(
        path=file_path,
        relative_path=str(rel),
        size=file_path.stat().st_size,
        mime=mime or "application/octet-stream",
        ext=ext,
    )


def discover_output_files(outputs_dir: Path) -> list[OutputFile]:
    """Walk `outputs_dir` and return one OutputFile per regular file.

    Skips directories, symlinks, and special files. Stable ordering:
    sorted by relative path so a re-run with the same content yields
    the same list.
    """
    if not outputs_dir.exists():
        return []
    files: list[OutputFile] = []
    for p in sorted(outputs_dir.rglob("*")):
        if p.is_file() and not p.is_symlink():
            files.append(_output_file_from_path(outputs_dir, p))
    return files


@dataclass
class RunResult:
    """Final result of a Motor.run().

    Note on `output_files`: the run workspace lives under
    `MotorConfig.workspace_root` which **defaults to a tempdir** (e.g.
    `/tmp/sophia-motor/runs/` on Linux) — so it is **transient by design**.
    The OS sweeps temp on its own schedule; `motor.clean_runs()` wipes
    it explicitly; cron sweeps or container teardowns may too. If the
    agent wrote a file you care about, persist it now via
    `output_file.copy_to(...)` — don't assume the path will be there in
    an hour. For full audit retention, set `workspace_root` to a
    persistent path.
    """
    run_id: str
    output_text: Optional[str]
    blocks: list[dict]
    metadata: RunMetadata
    audit_dir: Path
    workspace_dir: Path
    # Pydantic-validated payload, present iff RunTask.output_schema was set
    # AND the CLI returned a schema-conforming structured_output.
    # ValidationError → metadata.is_error = True, output_data = None.
    output_data: Optional[BaseModel] = None
    # Files the agent wrote under `<run>/agent_cwd/outputs/`. Discovered
    # at the end of the run via a directory walk — covers Write, Edit,
    # and Bash-driven file creation alike. Empty list if nothing was
    # written (or if `outputs/` doesn't exist for this run).
    output_files: list[OutputFile] = field(default_factory=list)
