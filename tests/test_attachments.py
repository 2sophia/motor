"""Pre-flight + materialization tests for `RunTask.attachments`.

These run without ANTHROPIC_API_KEY (no SDK call). They exercise the
`_validate_attachments` and `_materialize_attachments` helpers directly.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sophia_motor.motor import (  # noqa: E402
    _validate_attachments,
    _materialize_attachments,
)


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
        f.chmod(0o644)  # cleanup so tmp_path can be removed


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
    f2 = tmp_path / "subdir"
    f2.mkdir()
    f2_doc = f2 / "doc.txt"
    f1.write_text("a")
    f2_doc.write_text("b")
    # both map to attachments/doc.txt → conflict
    with pytest.raises(ValueError, match="conflicts"):
        _validate_attachments([f1, {"doc.txt": "inline"}])


# ─────────────────────────────────────────────────────────────────────────
# materialization
# ─────────────────────────────────────────────────────────────────────────

def test_materialize_inline_writes_file(tmp_path):
    target = tmp_path / "att"
    target.mkdir()
    manifest = _materialize_attachments(target, [{"note.txt": "hello"}])
    assert (target / "note.txt").read_text() == "hello"
    assert manifest == {"note.txt": "<inline>"}


def test_materialize_inline_with_subdir(tmp_path):
    target = tmp_path / "att"
    target.mkdir()
    _materialize_attachments(target, [{"sub/note.txt": "x"}])
    assert (target / "sub" / "note.txt").read_text() == "x"


def test_materialize_real_file_copied(tmp_path):
    src = tmp_path / "data.txt"
    src.write_text("payload")
    target = tmp_path / "att"
    target.mkdir()
    manifest = _materialize_attachments(target, [src])
    assert (target / "data.txt").read_text() == "payload"
    assert manifest == {"data.txt": str(src)}


def test_materialize_real_dir_copied(tmp_path):
    src = tmp_path / "docs"
    src.mkdir()
    (src / "a.txt").write_text("A")
    (src / "b.txt").write_text("B")
    target = tmp_path / "att"
    target.mkdir()
    _materialize_attachments(target, [src])
    assert (target / "docs" / "a.txt").read_text() == "A"
    assert (target / "docs" / "b.txt").read_text() == "B"


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
    assert (target / "doc.pdf").read_bytes() == b"PDF"
    assert (target / "note.txt").read_text() == "inline content"
    assert (target / "sub" / "n.txt").read_text() == "deep"
    assert manifest["doc.pdf"] == str(src)
    assert manifest["note.txt"] == "<inline>"
    assert manifest[str(Path("sub/n.txt"))] == "<inline>"
