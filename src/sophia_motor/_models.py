"""Input/output dataclasses for `Motor.run`."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


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

    Fields:
      prompt:           user prompt for the agent
      system_prompt:    optional system prompt; defaults to SDK default
      tools:            HARD whitelist (see above); None = SDK default
      allowed_tools:    auto-allow set (no permission prompt); None = []
      disallowed_tools: HARD block set; None = MotorConfig.default_disallowed_tools
      max_turns:        per-run override of MotorConfig.default_max_turns
      cwd_files:        files to seed under the run workspace before launch
                        ({relative_path: text_content})
    """
    prompt: str
    system_prompt: Optional[str] = None
    tools: Optional[list[str]] = None
    allowed_tools: Optional[list[str]] = None
    disallowed_tools: Optional[list[str]] = None
    max_turns: Optional[int] = None
    cwd_files: dict[str, str] = field(default_factory=dict)


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
