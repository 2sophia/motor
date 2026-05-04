# Copyright (c) 2026 Sophia AI
# SPDX-License-Identifier: MIT
"""Configuration for a Motor instance."""
from __future__ import annotations

import os
from pathlib import Path

from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


def _read_env_file(key: str) -> Optional[str]:
    """Read `key` from `./.env` in cwd. Returns None if absent."""
    env_path = Path.cwd() / ".env"
    if not env_path.exists():
        return None
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            if k.strip() == key:
                return v.strip().strip('"').strip("'")
    except OSError:
        pass
    return None


def _env_str(key: str) -> Optional[str]:
    """Resolution cascade: process env var → ./.env file → None."""
    if v := os.environ.get(key):
        return v
    return _read_env_file(key)


def _env_bool(key: str) -> Optional[bool]:
    """Parse a bool env var. `true/1/yes/on` → True, `false/0/no/off` → False.

    Returns None if unset (caller falls back to its hardcoded default).
    Anything else also falls back to None — be lenient on input, conservative
    on dispatch, never silently coerce typo'd values.
    """
    raw = _env_str(key)
    if raw is None:
        return None
    v = raw.strip().lower()
    if v in ("true", "1", "yes", "on"):
        return True
    if v in ("false", "0", "no", "off"):
        return False
    return None


def _resolve_api_key() -> str:
    """Resolve ANTHROPIC_API_KEY from (in order): env var → ./.env file.

    Returns "" if nothing is found; the Motor will then raise a clear
    error at first .run() call.
    """
    return _env_str("ANTHROPIC_API_KEY") or ""


def _resolve_workspace_root() -> Path:
    if v := _env_str("SOPHIA_MOTOR_WORKSPACE_ROOT"):
        return Path(v).expanduser().resolve()
    return (Path.home() / ".sophia-motor" / "runs").resolve()


def _resolve_model() -> str:
    return _env_str("SOPHIA_MOTOR_MODEL") or "claude-opus-4-6"


def _resolve_proxy_host() -> str:
    return _env_str("SOPHIA_MOTOR_PROXY_HOST") or "127.0.0.1"


def _resolve_console_log() -> bool:
    v = _env_bool("SOPHIA_MOTOR_CONSOLE_LOG")
    return False if v is None else v


def _resolve_audit_dump() -> bool:
    v = _env_bool("SOPHIA_MOTOR_AUDIT_DUMP")
    return False if v is None else v


def _resolve_upstream_base_url() -> str:
    return _env_str("SOPHIA_MOTOR_BASE_URL") or "https://api.anthropic.com"


def _resolve_upstream_adapter() -> str:
    return _env_str("SOPHIA_MOTOR_ADAPTER") or "anthropic"


# Tool description overrides applied at proxy layer.
# The default Claude CLI ships with verbose, dev-oriented descriptions for the
# core tools that don't fit a sandboxed agent run: Read says "use absolute
# paths", Bash mentions git/PR workflow, Write says "NEVER create .md files".
# We replace just the parts that matter for the motor — chiefly the path
# policy on Read — and leave the rest to the SDK default. Users can extend
# via MotorConfig.tool_description_overrides.
DEFAULT_TOOL_DESCRIPTION_OVERRIDES: dict[str, str] = {
    "Read": (
        "Read a text file from the local filesystem.\n\n"
        "PATH POLICY (enforced):\n"
        "- The `file_path` parameter MUST be a path relative to your current "
        "working directory (cwd).\n"
        "- NEVER use absolute paths. Your run is isolated inside a per-run "
        "sandbox; absolute paths from elsewhere on the filesystem will not "
        "resolve correctly.\n"
        "- Files seeded by the caller appear at their relative path under "
        "cwd. For example, a seed file declared as `scratch/sample.txt` is "
        "read with `Read(file_path=\"scratch/sample.txt\")`.\n\n"
        "Returns the file content prefixed with line numbers in the format "
        "`N\\tcontent`. When passing this output to Edit, strip the line-"
        "number prefix first."
    ),
}


# Tools blocked by default. Covers web-access, agentic/interactive, and
# the deferred tools that the SDK CLI injects even when `tools=` is a
# strict whitelist (Monitor, PushNotification, ScheduleWakeup — IDE-style
# tools that have no place in a programmatic motor run).
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
    # IDE-style tools the CLI injects even with tools=["Read"] whitelist
    "Monitor", "PushNotification", "ScheduleWakeup",
    # Notebooks
    "NotebookEdit",
    # Remote triggers
    "RemoteTrigger",
    # MCP auth flows (not configured here)
    "mcp__claude_ai_Gmail__authenticate",
    "mcp__claude_ai_Gmail__complete_authentication",
    "mcp__claude_ai_Google_Calendar__authenticate",
    "mcp__claude_ai_Google_Calendar__complete_authentication",
    "mcp__claude_ai_Google_Drive__authenticate",
    "mcp__claude_ai_Google_Drive__complete_authentication",
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
        default_factory=_resolve_api_key,
        description=(
            "Anthropic API key. Resolution cascade: explicit param > "
            "ANTHROPIC_API_KEY env var > ./.env file in cwd. Empty string "
            "if all three are missing → first .run() raises with a clear msg."
        ),
    )
    model: str = Field(
        default_factory=_resolve_model,
        description=(
            "Default model id used by the SDK and forwarded to upstream. "
            "Resolution cascade: explicit param > SOPHIA_MOTOR_MODEL env "
            "var > 'claude-opus-4-6'."
        ),
    )
    upstream_base_url: str = Field(
        default_factory=_resolve_upstream_base_url,
        description=(
            "Upstream endpoint the proxy forwards to. Resolution cascade: "
            "explicit param > SOPHIA_MOTOR_BASE_URL env var > "
            "'https://api.anthropic.com'."
        ),
    )
    upstream_adapter: Any = Field(
        default_factory=_resolve_upstream_adapter,
        description=(
            "Provider adapter that customizes the proxy → upstream hop. "
            "Pass a string preset (`'anthropic'` default, `'vllm'`) or an "
            "`UpstreamAdapter` instance for full control (custom auth, "
            "body re-mapping, SSE cleanup, …). Subclass `UpstreamAdapter` "
            "to support other providers without forking the proxy. "
            "Resolution cascade: explicit param > SOPHIA_MOTOR_ADAPTER env "
            "var > 'anthropic'."
        ),
    )
    anthropic_version: str = Field(
        default="2023-06-01",
        description="anthropic-version header forwarded upstream when SDK omits it.",
    )

    # ── Workspace ────────────────────────────────────────────────────
    workspace_root: Path = Field(
        default_factory=_resolve_workspace_root,
        description=(
            "Root directory for per-run workspaces. Resolution cascade: "
            "explicit param > SOPHIA_MOTOR_WORKSPACE_ROOT env var > "
            "`~/.sophia-motor/runs/` — outside any repo, always safe.\n\n"
            "MUST be a directory whose ancestors do NOT contain `.git/`, "
            "`pyproject.toml`, or `package.json`. The bundled Claude CLI "
            "performs upward project-root discovery and, when triggered, "
            "rewrites its own session/backup state into a deeply-nested "
            "cwd-relative fallback path (verified empirically — no env var "
            "currently overrides this behaviour, including CLAUDE_PROJECT_DIR).\n\n"
            "Container deployments: pass an explicit `workspace_root` "
            "pointed at a mounted volume, e.g. `MotorConfig("
            "workspace_root='/data/sophia-motor/runs')` with `/data` mounted "
            "for audit persistence across container restarts."
        ),
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
    proxy_host: str = Field(
        default_factory=_resolve_proxy_host,
        description=(
            "Bind host for the local proxy. Resolution cascade: explicit "
            "param > SOPHIA_MOTOR_PROXY_HOST env var > '127.0.0.1'."
        ),
    )
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
        default_factory=_resolve_audit_dump,
        description=(
            "Persist every request and response body under <run>/audit/. "
            "Resolution cascade: explicit param > SOPHIA_MOTOR_AUDIT_DUMP "
            "env var > False. Default OFF: in production you want clean "
            "disk writes; flip on in dev (or via env) when you want to "
            "inspect what the SDK and the model actually exchanged."
        ),
    )
    proxy_strip_sdk_noise: bool = Field(
        default=True,
        description="Strip SDK billing-header and identity blocks from system field.",
    )
    proxy_strip_user_system_reminders: bool = Field(
        default=True,
        description=(
            "Strip <system-reminder>...</system-reminder> blocks injected by "
            "the Claude CLI into USER messages from turn 2 onwards (skill "
            "listings, date changes, 'task tools haven't been used recently' "
            "reminders). For task-driven agent runs they're noise; disable "
            "only if you want to see them for debug."
        ),
    )
    tool_description_overrides: dict[str, str] = Field(
        default_factory=lambda: dict(DEFAULT_TOOL_DESCRIPTION_OVERRIDES),
        description=(
            "Map of tool_name → description. The proxy replaces the default "
            "SDK description for matching tools before forwarding upstream. "
            "Set to {} to disable. Override per Motor instance to attach "
            "program-specific guidance (e.g. domain-specific Read semantics)."
        ),
    )

    # ── Security guardrail (PreToolUse hook) ─────────────────────────
    guardrail: str = Field(
        default="strict",
        description=(
            "Built-in PreToolUse hook that sandboxes the agent inside its "
            "workspace. Three modes:\n\n"
            "  - 'strict' (default): Read/Edit/Glob/Grep must stay inside "
            "cwd; Write must target outputs/; Bash blocks dev/admin commands "
            "(curl, wget, ssh, git, docker, pip, npm, sudo, chmod, ...) "
            "plus '..' escapes, /dev/tcp, bash -c, eval/exec patterns.\n"
            "  - 'permissive': only sane minimums — '..' escapes, sudo, "
            "rm -rf root, exfiltration via curl/wget --data, /dev/tcp.\n"
            "  - 'off': no hook (full SDK behaviour). Use only when you "
            "fully trust prompts AND host (e.g. ephemeral container).\n\n"
            "Default 'strict' is safe-by-default for prototypes, internal "
            "tools, web-form prompts. Set 'permissive' if the agent "
            "legitimately needs git/docker/package managers."
        ),
    )

    # ── Skip ambient context (CLAUDE.md / memory) ────────────────────
    disable_claude_md: bool = Field(
        default=True,
        description=(
            "When True, set CLAUDE_CODE_DISABLE_CLAUDE_MDS=1 in the CLI "
            "subprocess env so the binary skips auto-loading Project/Local "
            "CLAUDE.md and MEMORY.md into conversation context — keeps each "
            "motor run isolated from ambient repo guidance. Default True "
            "(motor runs are sandboxed by design). Set False to let the "
            "CLI's native claudeMd injection work (useful for repo-aware tasks)."
        ),
    )

    # ── Logging ──────────────────────────────────────────────────────
    console_log_enabled: bool = Field(
        default_factory=_resolve_console_log,
        description=(
            "Register the default console logger for events and logs. "
            "Resolution cascade: explicit param > SOPHIA_MOTOR_CONSOLE_LOG "
            "env var > False. Default OFF so the motor stays silent in "
            "production; flip on in dev (or via env) when you want to "
            "watch turns scroll by."
        ),
    )

    # ── CLI flags ────────────────────────────────────────────────────
    cli_bare_mode: bool = Field(
        default=False,
        description=(
            "Pass --bare to the Claude CLI subprocess. WARNING: in bare "
            "mode skills resolve as slash-commands (/skill-name) instead "
            "of as the Skill tool. The model can no longer invoke them "
            "via tool_use. Verified empirically — output becomes a literal "
            "`<tool_call>...` inline string instead of a real tool_use "
            "block. Default OFF; enable ONLY for skill-less runs."
        ),
    )
    cli_no_session_persistence: bool = Field(
        default=True,
        description=(
            "Pass --no-session-persistence to the Claude CLI subprocess: "
            "no session jsonl written under <CLAUDE_CONFIG_DIR>/projects/. "
            "Each motor run is isolated with its own audit dir; resume "
            "across runs is not a use case for this motor."
        ),
    )

    # ── Run defaults (overridable per RunTask) ───────────────────────
    # The pattern: define common settings here once at Motor construction,
    # then call `motor.run(RunTask(prompt="..."))` N times. RunTask fields
    # explicitly set by the caller WIN; otherwise these defaults apply.
    # Override semantics: full replacement, NOT merge — if the dev wants
    # to "extend" defaults for a single task, they pass the union manually.
    default_system: Optional[str] = Field(
        default=None,
        description="Default system prompt applied when RunTask.system is None.",
    )
    default_tools: Optional[list[Any]] = Field(
        default_factory=list,
        description=(
            "Default hard tool whitelist (what the model can SEE) when "
            "RunTask.tools is None. Default `[]` = no tools at all "
            "(pure reasoning). Entries can be:\n"
            "  - str: name of a built-in CLI tool ('Read', 'Glob', ...)\n"
            "  - callable: a Python function decorated with @tool (mounted "
            "as in-process MCP server, exposed to the model as "
            "`mcp__sophia__<name>`)\n"
            "Lists may mix both. Set to None to fall back to the SDK's "
            "`claude_code` preset (all built-ins) — explicit opt-in only."
        ),
    )
    default_allowed_tools: Optional[list[str]] = Field(
        default=None,
        description=(
            "Default permission-skip list when RunTask.allowed_tools is None. "
            "Tools listed here auto-run without prompting the user."
        ),
    )
    default_skills: Any = Field(
        default=None,
        description=(
            "Default skills source(s) when RunTask.skills is None. "
            "Same polymorphic shape as RunTask.skills (str | Path | list)."
        ),
    )
    default_attachments: Any = Field(
        default=None,
        description=(
            "Default attachments when RunTask.attachments is None. "
            "Useful for static reference material every task should see."
        ),
    )
    default_disallowed_skills: list[str] = Field(
        default_factory=list,
        description="Default disallowed_skills applied when RunTask.disallowed_skills is empty.",
    )
    default_agents: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Default subagents (name → AgentDefinition) applied when "
            "RunTask.agents is None. By design, this stays empty out-of-the-"
            "box: the `Agent` tool is in default_disallowed_tools, so even "
            "passing default_agents={...} alone does NOT enable subagents. "
            "The caller must ALSO include 'Agent' in `tools` and remove it "
            "from `disallowed_tools` — otherwise motor.run() raises a clear "
            "RuntimeError. This explicit-opt-in keeps strict mode strict."
        ),
    )
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
    default_output_schema: Any = Field(
        default=None,
        description=(
            "Default Pydantic BaseModel class for structured output when "
            "RunTask.output_schema is None. Useful when N tasks share the "
            "same output shape and only the prompt varies."
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
