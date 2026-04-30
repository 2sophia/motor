# Copyright (c) 2026 Sophia AI
# SPDX-License-Identifier: MIT
"""Built-in PreToolUse hook — sandbox the agent inside its workspace.

Three modes (selected by `MotorConfig.guardrail`):

  - "strict"      (default): restrictive — Read/Edit/Glob/Grep must stay
                  inside cwd; Write must target outputs/; Bash blocks
                  developer/admin commands (curl, wget, ssh, git, docker,
                  pip, node, sudo, chmod, ...) plus `..`, `/dev/tcp`,
                  `bash -c`, `eval`/`exec`/`source` patterns.

  - "permissive": minimum sane blocks only — `..` path escapes, `/dev/tcp`,
                  `sudo`, `rm -rf /` and obvious exfiltration (`curl`,
                  `wget` with `-d`/`--data`/redirects). Useful for trusted
                  dev workflows where the agent legitimately needs git,
                  package managers, etc.

  - "off":        no hook at all — full SDK behaviour. Use only when you
                  fully control the prompts AND the host the agent runs on
                  (e.g. ephemeral container, dedicated VM).

The hook returns `{}` to allow, or `{"decision": "block", "reason": "..."}`
to refuse the tool call. The agent receives the reason as feedback and
typically retries with a corrected approach.
"""
from __future__ import annotations

import logging
import os.path
import pathlib
import re
from typing import Any, Awaitable, Callable, Literal

logger = logging.getLogger("sophia_motor.guard")

GuardrailMode = Literal["strict", "permissive", "off"]
HookCallback = Callable[[dict, Any, Any], Awaitable[dict]]


# ─────────────────────────────────────────────────────────────────────────
# Bash blocklist patterns
# ─────────────────────────────────────────────────────────────────────────

# Commands to flat-out reject when they appear at command-word position.
# Aimed at developer/admin tooling that has no place inside an agent run.
_STRICT_BLOCKED_CMDS = frozenset({
    # remote / network
    "ssh", "scp", "sftp", "rsync", "telnet", "nc", "ncat", "netcat",
    "curl", "wget", "ftp", "tftp",
    # version control / build / deploy
    "git", "hg", "svn",
    "docker", "podman", "buildah", "kubectl", "helm",
    "make", "cmake", "ninja",
    # package managers / installers
    "apt", "apt-get", "yum", "dnf", "pacman", "snap", "flatpak", "brew",
    "pip", "pip3", "pipx", "poetry", "uv", "conda", "mamba",
    "npm", "yarn", "pnpm", "node", "deno", "bun",
    "gem", "cargo", "go", "dotnet",
    # privilege escalation / system control
    "sudo", "doas", "su", "pkexec",
    "chmod", "chown", "chgrp", "setcap", "setfacl",
    "systemctl", "service", "rc-service",
    # process control / kill
    "kill", "pkill", "killall", "reboot", "shutdown", "halt", "poweroff",
    # mount / partition
    "mount", "umount", "fdisk", "mkfs", "parted",
    # symlink / hardlink creation — would let the agent plant a symlink
    # to escape cwd at the next Read; lexical path check below trusts
    # that no new symlinks appear during a run.
    "ln", "link", "symlink",
    # crontab / timers
    "crontab", "at", "batch",
    # firewall / network config
    "iptables", "nft", "ufw", "firewall-cmd",
    "ip", "route", "ifconfig", "iwconfig",
    # interactive shells / editors that escape
    "vi", "vim", "emacs", "nano", "less", "more",
    # misc dangerous
    "dd", "shred", "wipe",
})

# Permissive mode keeps a much smaller set — only obvious exfiltration
# and privilege escalation. Git/docker/pip/etc. are allowed here.
_PERMISSIVE_BLOCKED_CMDS = frozenset({
    "sudo", "doas", "su", "pkexec",
    "ssh", "scp", "sftp", "telnet", "nc", "ncat", "netcat",
    "reboot", "shutdown", "halt", "poweroff",
    "dd", "shred", "wipe",
    "mkfs", "fdisk", "parted",
})

# Patterns blocked in BOTH strict and permissive — sandbox-escape vectors.
_SPECIAL_BLOCKED_RE: list[re.Pattern[str]] = [
    re.compile(r"/dev/tcp\b", re.IGNORECASE),
    re.compile(r"/dev/udp\b", re.IGNORECASE),
    re.compile(r"\bbash\s+-c\b"),
    re.compile(r"\bsh\s+-c\b"),
    re.compile(r"\bzsh\s+-c\b"),
    re.compile(r"\bexec\s+\d*[<>]"),  # exec 3<>/dev/tcp/...
    re.compile(r"\beval\s+"),
    re.compile(r"\bsource\s+\S"),
    re.compile(r"\.\s+/"),  # `. /path/to/script` (sourcing absolute)
    # python / node escape hatches
    re.compile(r"\bpython3?\s+-m\s+(pip|venv|http\.server|smtpd)\b"),
    re.compile(r"\bnode\s+-e\b"),
    re.compile(r"\bperl\s+-e\b"),
    re.compile(r"\bruby\s+-e\b"),
    # rm -rf at filesystem root or HOME
    re.compile(r"\brm\s+-[rRf]+\s+(/|~|\$HOME)"),
    # curl/wget with data exfiltration shapes (only checked in strict —
    # permissive lets these through; we still block them in strict via
    # _STRICT_BLOCKED_CMDS above)
]

# Exfiltration patterns that we want blocked even in permissive mode
# (bypass: still match before the per-mode command list).
_EXFIL_RE = re.compile(
    r"\b(curl|wget)\s+[^|;&]*?\s+(-d|--data|--data-binary|-T|--upload-file)\b",
    re.IGNORECASE,
)

# `..` path component as escape vector.
_DOT_DOT_RE = re.compile(r"(?:^|[\s=(])\.\.(?:/|\s|$|\))|/\.\.(?:/|\s|$)")

# Tokenize command-word positions.
_CMD_WORD_RE = re.compile(r"(?:^|[;|&]|&&|\|\|)\s*([\w./-]+)")


# ─────────────────────────────────────────────────────────────────────────
# Path helpers
# ─────────────────────────────────────────────────────────────────────────

def _resolve_under_cwd(path: str, cwd: str) -> str | None:
    """Resolve `path` against cwd, following symlinks. Returns None on error.

    Used by Write to detect symlink-escape into outputs/ (where we DO
    want anti-escape semantics, since the agent could otherwise plant a
    symlink earlier and then write through it).
    """
    if not cwd:
        return None
    try:
        target = pathlib.Path(path) if path.startswith("/") else pathlib.Path(cwd) / path
        return str(target.resolve(strict=False))
    except (OSError, RuntimeError):
        return None


def _is_path_in_cwd(path: str, cwd_resolved: str, cwd: str) -> bool:
    """True if `path` is lexically inside `cwd` (no symlink following).

    Lexical-only check by design: the motor places intentional symlinks
    under `attachments/` (and the CLI places skill symlinks under
    `.claude/`) that point at locations outside cwd. Following those at
    Read/Glob/Grep time would be a self-inflicted denial of service —
    every Path-based attachment would be blocked from being read.

    Symlink-escape resistance is enforced upstream: agent-driven symlink
    creation via Bash (`ln`, `link`, `cp -s`, `tar -h`, etc.) is in the
    strict blocklist. Write is checked separately with full resolution
    (see `_resolve_under_cwd` use in the Write branch).

    Implementation: build the absolute path WITHOUT following links,
    normalize `..` and `.` lexically with `os.path.normpath`, then
    require the result to be exactly `cwd_resolved` or a child of it.
    """
    if not path:
        return True
    if not cwd:
        return not path.startswith("/")
    abs_path = path if path.startswith("/") else os.path.join(cwd, path)
    normalized = os.path.normpath(abs_path)
    cwd_prefix = cwd_resolved.rstrip("/")
    return normalized == cwd_prefix or normalized.startswith(cwd_prefix + "/")


# ─────────────────────────────────────────────────────────────────────────
# Hook factory
# ─────────────────────────────────────────────────────────────────────────

def make_guard_hook(mode: GuardrailMode) -> HookCallback | None:
    """Return a PreToolUse async hook for the given mode, or None for "off".

    The hook signature matches `claude_agent_sdk.HookCallback`:
        async def hook(hook_input: dict, _result: Any, _context: Any) -> dict
    """
    if mode == "off":
        return None

    blocked_cmds = (
        _STRICT_BLOCKED_CMDS if mode == "strict" else _PERMISSIVE_BLOCKED_CMDS
    )

    async def _hook(hook_input: dict, _result: Any, _context: Any) -> dict:
        tool_name = hook_input.get("tool_name", "")
        tool_input = hook_input.get("tool_input", {}) or {}
        cwd = hook_input.get("cwd", "") or ""

        cwd_resolved = ""
        if cwd:
            try:
                cwd_resolved = str(pathlib.Path(cwd).resolve(strict=False))
            except (OSError, RuntimeError):
                cwd_resolved = cwd

        # ── Read / Edit: path must be inside cwd ─────────────────────────
        if tool_name in ("Read", "Edit"):
            file_path = str(tool_input.get("file_path", ""))
            if mode == "strict" and not _is_path_in_cwd(file_path, cwd_resolved, cwd):
                logger.warning("[guard] BLOCKED %s outside cwd: %s", tool_name, file_path)
                return {
                    "decision": "block",
                    "reason": (
                        f"Path '{file_path}' is outside the workspace "
                        f"({cwd}). Use a path relative to your cwd, "
                        f"e.g. attachments/<file> or outputs/<file>."
                    ),
                }

        # ── Glob / Grep: path parameter inside cwd ───────────────────────
        if tool_name in ("Glob", "Grep"):
            path = str(tool_input.get("path", ""))
            if mode == "strict" and path and not _is_path_in_cwd(path, cwd_resolved, cwd):
                logger.warning("[guard] BLOCKED %s outside cwd: %s", tool_name, path)
                return {
                    "decision": "block",
                    "reason": (
                        f"Search path '{path}' is outside the workspace "
                        f"({cwd}). Search relative to cwd, e.g. "
                        f"path='.' or path='attachments/'."
                    ),
                }

        # ── Write: target must be in outputs/ (strict) ───────────────────
        if tool_name == "Write" and mode == "strict":
            file_path = str(tool_input.get("file_path", ""))
            ok = (
                file_path.startswith("outputs/")
                or file_path.startswith("./outputs/")
                or (cwd and file_path.startswith(f"{cwd}/outputs/"))
            )
            if not ok:
                basename = file_path.rsplit("/", 1)[-1] if "/" in file_path else file_path
                logger.warning("[guard] BLOCKED Write outside outputs/: %s", file_path)
                return {
                    "decision": "block",
                    "reason": (
                        f"Write only to outputs/. "
                        f"Try Write(file_path='outputs/{basename}', ...)."
                    ),
                }
            if cwd:
                resolved = _resolve_under_cwd(file_path, cwd)
                outputs_root = str((pathlib.Path(cwd) / "outputs").resolve(strict=False))
                under = resolved and (
                    resolved == outputs_root or resolved.startswith(outputs_root + "/")
                )
                if not under:
                    logger.warning("[guard] BLOCKED Write symlink escape: %s → %s",
                                   file_path, resolved)
                    return {
                        "decision": "block",
                        "reason": (
                            "Write target resolves outside outputs/ "
                            "(possible symlink escape). Use a plain "
                            "filename inside outputs/."
                        ),
                    }

        # ── Bash: blocklist + special patterns ───────────────────────────
        if tool_name == "Bash":
            command = str(tool_input.get("command", ""))

            # 1. exfiltration via curl/wget with data flags (both modes)
            if _EXFIL_RE.search(command):
                logger.warning("[guard] BLOCKED Bash exfil pattern: %s", command[:200])
                return {
                    "decision": "block",
                    "reason": (
                        "Outbound data transfer (curl/wget with -d/--data/"
                        "--upload-file) is not allowed."
                    ),
                }

            # 2. special escape patterns (both modes)
            for pat in _SPECIAL_BLOCKED_RE:
                m = pat.search(command)
                if m:
                    matched = m.group(0)
                    logger.warning("[guard] BLOCKED Bash pattern '%s': %s",
                                   matched, command[:200])
                    return {
                        "decision": "block",
                        "reason": (
                            f"Pattern '{matched.strip()}' is not allowed — "
                            f"it can be used to escape the sandbox."
                        ),
                    }

            # 3. `..` path escapes
            if _DOT_DOT_RE.search(command):
                logger.warning("[guard] BLOCKED Bash '..' escape: %s", command[:200])
                return {
                    "decision": "block",
                    "reason": (
                        "Don't use `..` in paths — it tries to navigate "
                        "outside the workspace."
                    ),
                }

            # 4. command-word blocklist (strict vs permissive)
            for m in _CMD_WORD_RE.finditer(command):
                token = m.group(1)
                basename = token.rsplit("/", 1)[-1].lower()
                if basename in blocked_cmds:
                    logger.warning("[guard] BLOCKED Bash command '%s': %s",
                                   basename, command[:200])
                    if mode == "strict":
                        reason = (
                            f"Command '{basename}' is blocked in strict mode. "
                            f"This agent runs in a sandboxed workspace — no "
                            f"developer/admin tooling. Use Python "
                            f"(pandas, pypdf, ...) or built-in tools instead."
                        )
                    else:
                        reason = (
                            f"Command '{basename}' is blocked: privilege "
                            f"escalation or destructive system call."
                        )
                    return {"decision": "block", "reason": reason}

        return {}

    return _hook
