"""Configuration for a Motor instance."""
from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, Field, field_validator


# Tools blocked by default. Mirrors the sophia-agent DISALLOWED_TOOLS list.
# A compliance-reasoning agent has no business browsing the web, spawning
# subagents, scheduling cron, opening notebooks, or hitting MCP auth flows.
DEFAULT_DISALLOWED_TOOLS: list[str] = [
    # Web access
    "WebFetch", "WebSearch",
    # Agentic / interactive — escape hatches the model would over-use
    "AskUserQuestion",
    "TodoWrite", "Agent",
    "EnterPlanMode", "ExitPlanMode",
    "TaskOutput", "TaskStop",
    # Worktrees / cron — out of scope for an agent run
    "EnterWorktree", "ExitWorktree",
    "CronCreate", "CronDelete", "CronList",
    # Notebooks
    "NotebookEdit",
    # Remote triggers
    "RemoteTrigger",
    # MCP auth flows (not configured here)
    "mcp__claude_ai_Gmail__authenticate",
    "mcp__claude_ai_Gmail__complete_authentication",
    "mcp__claude_ai_Google_Calendar__authenticate",
    "mcp__claude_ai_Google_Calendar__complete_authentication",
]


class MotorConfig(BaseModel):
    """All settings needed to instantiate a Motor.

    Defaults are designed for "drop-in instance with no setup":
    - api_key reads ANTHROPIC_API_KEY from env if not provided
    - workspace_root defaults to ./.runs
    - proxy + audit dump + console log all on by default
    """

    # ── Anthropic API ────────────────────────────────────────────────
    api_key: str = Field(
        default_factory=lambda: os.environ.get("ANTHROPIC_API_KEY", ""),
        description="Anthropic API key. Defaults to ANTHROPIC_API_KEY env var.",
    )
    model: str = Field(
        default="claude-opus-4-6",
        description="Default model id used by the SDK and forwarded to upstream.",
    )
    upstream_base_url: str = Field(
        default="https://api.anthropic.com",
        description="Real Anthropic Messages API endpoint the proxy forwards to.",
    )
    anthropic_version: str = Field(
        default="2023-06-01",
        description="anthropic-version header forwarded upstream when SDK omits it.",
    )

    # ── Workspace ────────────────────────────────────────────────────
    workspace_root: Path = Field(
        default=Path("./.runs"),
        description="Root directory for per-run workspaces.",
    )

    # ── Proxy ────────────────────────────────────────────────────────
    proxy_enabled: bool = Field(
        default=True,
        description=(
            "If True, the Motor starts a local FastAPI proxy and routes the SDK "
            "through it. Required for audit dump + per-turn proxy events. Disable "
            "only for unit tests that mock the SDK."
        ),
    )
    proxy_host: str = "127.0.0.1"
    proxy_port: int | None = Field(
        default=None,
        description=(
            "If None, the proxy binds to a free kernel-assigned port (recommended "
            "for parallel Motor instances). Set to a specific port to make the "
            "proxy URL stable — useful for debugging with curl, sniffer, or fixed "
            "firewall rules."
        ),
    )
    proxy_dump_payloads: bool = Field(
        default=True,
        description="Persist every request and response body under <run>/audit/.",
    )
    proxy_strip_sdk_noise: bool = Field(
        default=True,
        description="Strip SDK billing-header and identity blocks from system field.",
    )

    # ── Logging ──────────────────────────────────────────────────────
    console_log_enabled: bool = Field(
        default=True,
        description="Register the default console logger for events and logs.",
    )

    # ── Run defaults (overridable per RunTask) ───────────────────────
    default_max_turns: int = 20
    default_timeout_seconds: int = 300
    default_disallowed_tools: list[str] = Field(
        default_factory=lambda: list(DEFAULT_DISALLOWED_TOOLS),
        description=(
            "Tools blocked by default on every run unless overridden by "
            "RunTask.disallowed_tools. Sensible defaults: web access, "
            "agentic spawning, worktrees/cron/notebook/remote, MCP auth — "
            "things a compliance-reasoning agent should never have."
        ),
    )

    model_config = {"arbitrary_types_allowed": True}

    @field_validator("workspace_root")
    @classmethod
    def _resolve_workspace_root(cls, v: Path) -> Path:
        return Path(v).resolve()

    def require_api_key(self) -> str:
        if not self.api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY not set. Pass api_key=... to MotorConfig "
                "or export ANTHROPIC_API_KEY in the environment."
            )
        return self.api_key
