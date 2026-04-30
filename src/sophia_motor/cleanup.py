"""Workspace cleanup helpers.

Each `Motor.run()` persists a directory under `workspace_root/<run_id>/` with
input.json, audit/, scratch/, outputs/, trace.json. Useful for audit, bulky
on disk for routine dev runs. This module exposes:

- `clean_runs(workspace_root, ...)` — remove run dirs under a root
- `Motor.clean_runs(...)` — same, bound to the instance config

Filtering options:
    keep_last:        keep the most recent N runs (by mtime)
    older_than_days:  only remove runs older than N days (by mtime)
    dry_run:          list what would be removed, do not delete

Returns the list of paths that were (or would be) removed.
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Optional


def clean_runs(
    workspace_root: Path | str,
    *,
    keep_last: int = 0,
    older_than_days: Optional[float] = None,
    dry_run: bool = False,
) -> list[Path]:
    """Remove run-* directories under `workspace_root`.

    Args:
        workspace_root:  directory containing run-XXX subdirs
        keep_last:       keep the N most recent runs (by mtime). 0 = remove all.
        older_than_days: only consider runs older than this many days.
        dry_run:         do not delete, just return what would be removed.

    Returns:
        List of paths that were removed (or would be removed in dry_run).
    """
    root = Path(workspace_root).resolve()
    if not root.exists():
        return []

    # Discover run dirs (anything matching `run-*` directly under root)
    candidates: list[tuple[Path, float]] = []
    for entry in root.iterdir():
        if not entry.is_dir() or not entry.name.startswith("run-"):
            continue
        try:
            mtime = entry.stat().st_mtime
        except OSError:
            continue
        candidates.append((entry, mtime))

    if not candidates:
        return []

    # Sort newest first for keep_last calculations
    candidates.sort(key=lambda x: x[1], reverse=True)

    # Apply keep_last filter
    if keep_last > 0:
        candidates = candidates[keep_last:]

    # Apply older_than_days filter
    if older_than_days is not None:
        cutoff = time.time() - (older_than_days * 86400.0)
        candidates = [(p, m) for p, m in candidates if m < cutoff]

    removed: list[Path] = []
    for path, _ in candidates:
        if dry_run:
            removed.append(path)
            continue
        try:
            shutil.rmtree(path)
            removed.append(path)
        except OSError:
            pass
    return removed
