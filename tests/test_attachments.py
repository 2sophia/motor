# Copyright (c) 2026 Sophia AI
# SPDX-License-Identifier: MIT
"""Pre-flight + materialization tests for `RunTask.attachments`.

These run without ANTHROPIC_API_KEY (no SDK call). They exercise the
`_validate_attachments` and `_materialize_attachments` helpers directly,
plus `_normalize_to_list`.
"""
from __future__ import annotations

from pathlib import Path

import pytest


from sophia_motor.motor import (
    _materialize_attachments,
    _normalize_to_list,
    _validate_attachments,
)


# ─────────────────────────────────────────────────────────────────────────
# _normalize_to_list
# ─────────────────────────────────────────────────────────────────────────

def test_normalize_none_to_empty():
    assert _normalize_to_list(None) == []


def test_normalize_single_path_to_list(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("x")
    assert _normalize_to_list(f) == [f]


def test_normalize_single_dict_to_list():
    assert _normalize_to_list({"a.txt": "x"}) == [{"a.txt": "x"}]


def test_normalize_list_passthrough():
    items = [Path("/a"), {"b.txt": "x"}]
    assert _normalize_to_list(items) == items


# ─────────────────────────────────────────────────────────────────────────
# pre-flight validation — happy paths
# ─────────────────────────────────────────────────────────────────────────

def test_validate_empty_list_ok():
    _validate_attachments([])


def test_validate_inline_dict_ok():
    _validate_attachments([{"note.txt": "hello"}])


def test_validate_real_file_ok(tmp_path):
    f = tmp_path / "doc.txt"
    f.write_text("x")
    _validate_attachments([f])
    _validate_attachments([str(f)])


def test_validate_real_dir_ok(tmp_path):
    d = tmp_path / "docs"
    d.mkdir()
    (d / "a.txt").write_text("a")
    _validate_attachments([d])


def test_validate_mixed_ok(tmp_path):
    f = tmp_path / "doc.txt"
    f.write_text("x")
    _validate_attachments([f, {"note.txt": "hello"}, {"sub/n.txt": "world"}])


# ─────────────────────────────────────────────────────────────────────────
# pre-flight validation — failure modes (each with a specific exception)
# ─────────────────────────────────────────────────────────────────────────

def test_validate_missing_path_raises_FileNotFoundError(tmp_path):
    with pytest.raises(FileNotFoundError, match="does not exist"):
        _validate_attachments([tmp_path / "missing.txt"])


def test_validate_unreadable_path_raises_PermissionError(tmp_path):
    f = tmp_path / "secret.txt"
    f.write_text("x")
    f.chmod(0o000)
    try:
        with pytest.raises(PermissionError, match="not readable"):
            _validate_attachments([f])
    finally:
        f.chmod(0o644)


def test_validate_dict_value_not_str_raises_TypeError():
    with pytest.raises(TypeError, match="must be str"):
        _validate_attachments([{"note.txt": 123}])  # type: ignore[dict-item]


def test_validate_dict_key_absolute_raises_ValueError():
    with pytest.raises(ValueError, match="must be relative"):
        _validate_attachments([{"/etc/passwd": "x"}])


def test_validate_dict_key_dotdot_raises_ValueError():
    with pytest.raises(ValueError, match="must not contain"):
        _validate_attachments([{"../escape.txt": "x"}])


def test_validate_dict_empty_raises_ValueError():
    with pytest.raises(ValueError, match="empty dict"):
        _validate_attachments([{}])


def test_validate_unsupported_type_raises_TypeError():
    with pytest.raises(TypeError, match="unsupported type"):
        _validate_attachments([42])  # type: ignore[list-item]


def test_validate_conflicting_targets_raises_ValueError(tmp_path):
    f1 = tmp_path / "doc.txt"
    f1.write_text("a")
    with pytest.raises(ValueError, match="conflicts"):
        _validate_attachments([f1, {"doc.txt": "inline"}])


# ─────────────────────────────────────────────────────────────────────────
# materialization — link by default for real paths, write for inline
# ─────────────────────────────────────────────────────────────────────────

def test_materialize_inline_writes_real_file(tmp_path):
    target = tmp_path / "att"
    target.mkdir()
    manifest = _materialize_attachments(target, [{"note.txt": "hello"}])
    note = target / "note.txt"
    assert note.is_file()
    assert not note.is_symlink()
    assert note.read_text() == "hello"
    assert manifest == {"note.txt": "<inline>"}


def test_materialize_inline_with_subdir(tmp_path):
    target = tmp_path / "att"
    target.mkdir()
    _materialize_attachments(target, [{"sub/note.txt": "x"}])
    assert (target / "sub" / "note.txt").read_text() == "x"


def test_materialize_real_file_is_hardlinked(tmp_path):
    """Single-file attachments are hard-linked: same inode as the source,
    invisible to ripgrep's symlink-skip behaviour, zero storage cost."""
    src = tmp_path / "data.txt"
    src.write_text("payload")
    target = tmp_path / "att"
    target.mkdir()
    manifest = _materialize_attachments(target, [src])

    linked = target / "data.txt"
    # Hard-link: same inode as the source, NOT a symlink.
    assert not linked.is_symlink()
    assert linked.stat().st_ino == src.stat().st_ino
    assert linked.read_text() == "payload"
    assert manifest["data.txt"].startswith("→ ")
    assert manifest["data.txt"].endswith("(link)")
    assert str(src.resolve()) in manifest["data.txt"]


def test_materialize_real_dir_mirrors_as_tree_of_hardlinks(tmp_path):
    """Directory attachments are mirrored as real dirs with each leaf
    file hard-linked. Hard-links are seen as regular files by ripgrep
    (the SDK Glob backend), so every file remains discoverable AND we
    pay zero storage cost on the same filesystem."""
    src = tmp_path / "docs"
    src.mkdir()
    (src / "a.txt").write_text("A")
    (src / "sub").mkdir()
    (src / "sub" / "b.txt").write_text("B")
    target = tmp_path / "att"
    target.mkdir()
    manifest = _materialize_attachments(target, [src])

    mirrored = target / "docs"
    assert mirrored.is_dir() and not mirrored.is_symlink()
    # Each leaf is a hard-link sharing the inode of its source counterpart.
    a = mirrored / "a.txt"
    assert not a.is_symlink()
    assert a.stat().st_ino == (src / "a.txt").stat().st_ino
    sub_dir = mirrored / "sub"
    assert sub_dir.is_dir() and not sub_dir.is_symlink()
    b = sub_dir / "b.txt"
    assert not b.is_symlink()
    assert b.stat().st_ino == (src / "sub" / "b.txt").stat().st_ino
    # Manifest tags every leaf as a link.
    assert manifest[str(Path("docs/a.txt"))].endswith("(link)")
    assert manifest[str(Path("docs/sub/b.txt"))].endswith("(link)")


def test_materialize_mixed(tmp_path):
    src = tmp_path / "doc.pdf"
    src.write_bytes(b"PDF")
    target = tmp_path / "att"
    target.mkdir()
    manifest = _materialize_attachments(target, [
        src,
        {"note.txt": "inline content"},
        {"sub/n.txt": "deep"},
    ])
    doc = target / "doc.pdf"
    assert not doc.is_symlink()  # hard-linked, not a symlink
    assert doc.stat().st_ino == src.stat().st_ino
    assert doc.read_bytes() == b"PDF"
    note = target / "note.txt"
    assert note.is_file() and not note.is_symlink()
    assert note.read_text() == "inline content"
    assert (target / "sub" / "n.txt").read_text() == "deep"
    assert manifest["doc.pdf"].endswith("(link)")
    assert manifest["note.txt"] == "<inline>"
    assert manifest[str(Path("sub/n.txt"))] == "<inline>"
