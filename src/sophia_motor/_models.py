"""Input/output dataclasses for `Motor.run`."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Union


# ─────────────────────────────────────────────────────────────────────────
# attachments — polimorfo
#
# Lista di item che il motor materializza sotto <run>/attachments/
# prima di lanciare l'agent. Ogni item può essere una di queste tre cose:
#
#   1. str | Path  →  path esistente di un FILE reale
#         es. Path("/data/regulation.pdf")
#         risultato: <run>/attachments/regulation.pdf  (shutil.copy2)
#
#   2. str | Path  →  path esistente di una DIRECTORY reale
#         es. Path("/data/policy_dir/")
#         risultato: <run>/attachments/policy_dir/...  (shutil.copytree)
#
#   3. dict[str, str]  →  file inline {relpath: content}
#         es. {"note.txt": "ciao"}  oppure  {"sub/note.txt": "ciao"}
#         risultato: <run>/attachments/note.txt  (write_text)
#
# Pre-flight automatico prima di chiamare il SDK:
#   - path mancanti → FileNotFoundError
#   - non file né dir → ValueError
#   - non leggibile → PermissionError
#   - dict key absolute o con `..` → ValueError
#   - dict value non-str → TypeError
#   - due item che destinano allo stesso path → ValueError (conflitto)
#
# Niente symlink: copia sempre. Audit bit-perfect, niente sandbox-escape.
# ─────────────────────────────────────────────────────────────────────────
AttachmentItem = Union[str, Path, dict[str, str]]


@dataclass
class RunTask:
    """Single-shot input to `Motor.run()`.

    Tool semantics — confirmed against the Claude Agent SDK source:

      tools (HARD WHITELIST — what the model can SEE):
        None       → SDK default preset (claude_code) → all built-ins loaded
        []         → no tools at all
        ["Read"]   → only Read is available; everything else does not exist

      allowed_tools (PERMISSION SKIP — what auto-runs without prompting):
        These tools execute automatically without an approval step. Does NOT
        restrict — `allowed_tools=["Read"]` does NOT block Bash if Bash is in
        the loaded `tools` set. Pair with `tools=` for true restriction.

      disallowed_tools (HARD BLOCK — removed from the model's context):
        Even if `tools=` would allow them. Use this for "never ever" tools
        (WebFetch, agentic spawning, ...).
    """
    prompt: str
    system: Optional[str] = None
    tools: Optional[list[str]] = None
    allowed_tools: Optional[list[str]] = None
    disallowed_tools: Optional[list[str]] = None
    max_turns: Optional[int] = None
    attachments: list[AttachmentItem] = field(default_factory=list)


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
