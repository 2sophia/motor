"""Input/output dataclasses for `Motor.run`."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


@dataclass
class RunTask:
    """Single-shot input to `Motor.run()`.

    Fields:
      prompt:           the user prompt for the agent
      system_prompt:    optional system prompt; defaults to SDK default
      allowed_tools:    whitelist of tool names; None = SDK default
      disallowed_tools: blacklist of tool names; None = []
      max_turns:        per-run override of MotorConfig.default_max_turns
      cwd_files:        files to seed under the run workspace before launch
                        ({relative_path: text_content})
    """
    prompt: str
    system_prompt: Optional[str] = None
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
