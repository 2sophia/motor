# Copyright (c) 2026 Sophia AI
# SPDX-License-Identifier: MIT
"""Input/output dataclasses for `Motor.run`."""
from __future__ import annotations

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
    tools: Optional[list[str]] = None
    allowed_tools: Optional[list[str]] = None
    disallowed_tools: Optional[list[str]] = None
    max_turns: Optional[int] = None

    # input data — single Path | str | dict, or a mixed list
    attachments: AttachmentsInput = None

    # code/tooling — single Path | str, or a list
    skills: SkillsInput = None
    disallowed_skills: list[str] = field(default_factory=list)

    # Strict structured output. When provided, the motor extracts the JSON
    # Schema via `output_schema.model_json_schema()` and forwards it to the
    # CLI via `--json-schema`. The CLI validates the model's structured
    # response server-side (constraints honored: enum, range, pattern,
    # additionalProperties:false, nested objects). The agent runs its full
    # multi-turn loop (tool calls, reasoning, cross-reference) and at the
    # end emits BOTH a free-text `result` AND a schema-conforming
    # `structured_output`. Pydantic-validated into `RunResult.output_data`.
    output_schema: Optional[type[BaseModel]] = None


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


@dataclass
class RunResult:
    """Final result of a Motor.run()."""
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
