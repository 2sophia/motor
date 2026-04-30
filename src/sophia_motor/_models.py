"""Input/output dataclasses for `Motor.run`."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Union


# ─────────────────────────────────────────────────────────────────────────
# attachments — polimorfo
#
# Accetta singolo o lista. Forme valide (mix libero in lista):
#
#   1. str | Path  →  file reale  → symlink in <run>/attachments/<name>
#   2. str | Path  →  directory   → symlink in <run>/attachments/<name>/
#   3. dict[str, str]  →  inline  → file scritto in <run>/attachments/<relpath>
#
# Default: SYMLINK per Path (no copy, no storage waste, no duplicazione).
# L'audit BdI passa per gli SSE in <run>/audit/, non per il filesystem in
# attachments/. Un symlink che cambia dopo non rompe la difesa: il dump
# dei tool_result registra il TESTO che il modello ha letto, non i bytes
# correnti del file.
#
# Sandbox escape (link malevoli a /etc/passwd ecc.): trust nel dev. Per
# applicazioni con utenti finali untrusted, sopra il motor va messo un
# guard PreToolUse layer (pattern sophia).
#
# Pre-flight check prima di consumare token:
#   - path mancante → FileNotFoundError
#   - non file né dir → ValueError
#   - non leggibile → PermissionError
#   - dict key absolute o con `..` → ValueError
#   - dict value non-str → TypeError
#   - due item che destinano allo stesso path → ValueError (conflitto)
# ─────────────────────────────────────────────────────────────────────────
AttachmentItem = Union[str, Path, dict[str, str]]
AttachmentsInput = Union[AttachmentItem, list[AttachmentItem], None]


# ─────────────────────────────────────────────────────────────────────────
# skills — folder source delle SKILL.md del programma
#
# Accetta singolo o lista (il dev può avere skill in più dir, es. una del
# programma e una shared org-wide). Per ogni dir source il motor:
#   - itera le sue subdir
#   - tra quelle che hanno un SKILL.md e che non sono in disallowed_skills
#   - crea un symlink <run>/.claude/skills/<skill_name> → <source>/<skill_name>
#
# Conflict detection: se due dir source forniscono una skill con lo stesso
# nome → ValueError chiaro. Il dev rinomina una delle due o usa disallowed.
#
# Pre-flight check:
#   - skills path manacante → FileNotFoundError
#   - path non è una dir → ValueError
#   - conflict di nome tra source → ValueError
# ─────────────────────────────────────────────────────────────────────────
SkillsInput = Union[str, Path, list[Union[str, Path]], None]


@dataclass
class RunTask:
    """Single-shot input to `Motor.run()`.

    Tool semantics — confirmed against the Claude Agent SDK source:

      tools (HARD WHITELIST — what the model can SEE):
        None       → SDK default preset (claude_code) → all built-ins loaded
        []         → no tools at all
        ["Read"]   → only Read is available; everything else does not exist

      allowed_tools (PERMISSION SKIP — auto-runs without prompting):
        Skip the permission prompt; does NOT restrict.

      disallowed_tools (HARD BLOCK — removed from the model's context):
        Use for "never ever" tools (WebFetch, agent spawning, ...).
    """
    prompt: str
    system: Optional[str] = None
    tools: Optional[list[str]] = None
    allowed_tools: Optional[list[str]] = None
    disallowed_tools: Optional[list[str]] = None
    max_turns: Optional[int] = None

    # input data — singolo Path | str | dict, oppure lista mista
    attachments: AttachmentsInput = None

    # codice/strumenti — singolo Path | str, oppure lista
    skills: SkillsInput = None
    disallowed_skills: list[str] = field(default_factory=list)


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
