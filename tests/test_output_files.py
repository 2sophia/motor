# Copyright (c) 2026 Sophia AI
# SPDX-License-Identifier: MIT
"""Deterministic tests for OutputFile + discover_output_files + chunks."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from pydantic import TypeAdapter

from sophia_motor import OutputFile, OutputFileReadyChunk, StreamChunk
from sophia_motor._models import discover_output_files


# ─── discover_output_files ──────────────────────────────────────────────

def test_discover_returns_empty_when_dir_missing(tmp_path):
    assert discover_output_files(tmp_path / "missing") == []


def test_discover_returns_empty_when_dir_empty(tmp_path):
    (tmp_path / "outputs").mkdir()
    assert discover_output_files(tmp_path / "outputs") == []


def test_discover_finds_top_level_files(tmp_path):
    out = tmp_path / "outputs"
    out.mkdir()
    (out / "report.md").write_text("# Title\n")
    (out / "data.json").write_text('{"ok": true}')

    files = discover_output_files(out)
    assert {f.relative_path for f in files} == {"report.md", "data.json"}


def test_discover_recurses_into_subdirs(tmp_path):
    out = tmp_path / "outputs"
    (out / "nested" / "deep").mkdir(parents=True)
    (out / "top.txt").write_text("a")
    (out / "nested" / "mid.txt").write_text("b")
    (out / "nested" / "deep" / "leaf.txt").write_text("c")

    files = discover_output_files(out)
    rels = {f.relative_path for f in files}
    assert rels == {
        "top.txt",
        os.path.join("nested", "mid.txt"),
        os.path.join("nested", "deep", "leaf.txt"),
    }


def test_discover_skips_symlinks(tmp_path):
    out = tmp_path / "outputs"
    out.mkdir()
    target = tmp_path / "target.txt"
    target.write_text("real")
    (out / "real.txt").write_text("native")
    (out / "linked.txt").symlink_to(target)

    rels = {f.relative_path for f in discover_output_files(out)}
    assert rels == {"real.txt"}


def test_discover_populates_size_mime_ext(tmp_path):
    out = tmp_path / "outputs"
    out.mkdir()
    (out / "doc.md").write_text("# hi")
    (out / "blob").write_bytes(b"\x00\x01\x02")

    files = {f.relative_path: f for f in discover_output_files(out)}
    md = files["doc.md"]
    assert md.size == 4
    assert md.ext == ".md"
    assert "markdown" in md.mime  # mimetypes maps .md to text/markdown

    blob = files["blob"]
    assert blob.size == 3
    assert blob.ext == ""
    assert blob.mime == "application/octet-stream"


def test_discover_returns_stable_sorted_order(tmp_path):
    out = tmp_path / "outputs"
    out.mkdir()
    for n in ["c.txt", "a.txt", "b.txt"]:
        (out / n).write_text("x")
    rels = [f.relative_path for f in discover_output_files(out)]
    assert rels == sorted(rels)


# ─── OutputFile helpers ─────────────────────────────────────────────────

def _make_file(tmp_path: Path, name: str, content: str = "data") -> OutputFile:
    out = tmp_path / "outputs"
    out.mkdir(exist_ok=True)
    (out / name).write_text(content)
    files = discover_output_files(out)
    return next(f for f in files if f.relative_path == name)


def test_output_file_read_text(tmp_path):
    f = _make_file(tmp_path, "x.txt", "hello world")
    assert f.read_text() == "hello world"


def test_output_file_read_bytes(tmp_path):
    f = _make_file(tmp_path, "x.bin", "abc")
    assert f.read_bytes() == b"abc"


def test_output_file_copy_to_directory(tmp_path):
    f = _make_file(tmp_path, "x.txt", "v1")
    dest_dir = tmp_path / "persist"
    dest_dir.mkdir()
    persisted = f.copy_to(dest_dir)
    assert persisted == (dest_dir / "x.txt").resolve()
    assert persisted.read_text() == "v1"
    # original still there
    assert f.path.exists()


def test_output_file_copy_to_full_path(tmp_path):
    f = _make_file(tmp_path, "x.txt", "v1")
    dest = tmp_path / "elsewhere" / "renamed.txt"
    persisted = f.copy_to(dest)
    assert persisted == dest.resolve()
    assert persisted.read_text() == "v1"
    assert f.path.exists()


def test_output_file_copy_to_creates_parents(tmp_path):
    f = _make_file(tmp_path, "x.txt", "v1")
    dest = tmp_path / "deep" / "nested" / "dir" / "x.txt"
    f.copy_to(dest)
    assert dest.read_text() == "v1"


def test_output_file_move_to_directory(tmp_path):
    f = _make_file(tmp_path, "x.txt", "v1")
    dest_dir = tmp_path / "persist"
    dest_dir.mkdir()
    moved = f.move_to(dest_dir)
    assert moved.read_text() == "v1"
    # original gone
    assert not f.path.exists()


def test_output_file_copy_preserves_subdir_path(tmp_path):
    """An OutputFile whose relative_path includes a subdir keeps the layout
    when copy_to(dir) is used."""
    out = tmp_path / "outputs"
    (out / "section").mkdir(parents=True)
    (out / "section" / "page.md").write_text("inner")
    files = discover_output_files(out)
    assert len(files) == 1
    f = files[0]
    assert f.relative_path == os.path.join("section", "page.md")

    persist = tmp_path / "persist"
    persist.mkdir()
    persisted = f.copy_to(persist)
    assert persisted == (persist / "section" / "page.md").resolve()
    assert persisted.read_text() == "inner"


# ─── OutputFileReadyChunk in StreamChunk union ──────────────────────────

def test_output_file_ready_chunk_validates_in_union():
    adapter = TypeAdapter(StreamChunk)
    chunk = adapter.validate_python({
        "type": "output_file_ready",
        "relative_path": "report.md",
        "path": "/tmp/run-1/agent_cwd/outputs/report.md",
        "tool": "Write",
    })
    assert isinstance(chunk, OutputFileReadyChunk)
    assert chunk.tool == "Write"
    assert chunk.relative_path == "report.md"


def test_output_file_ready_chunk_serializes_to_json(tmp_path):
    """Path is `str` so the chunk must be JSON-serializable as-is."""
    chunk = OutputFileReadyChunk(
        relative_path="x.json",
        path="/tmp/x.json",
        tool="Write",
    )
    payload = json.dumps(chunk.model_dump())
    parsed = json.loads(payload)
    assert parsed["type"] == "output_file_ready"
    assert parsed["tool"] == "Write"
