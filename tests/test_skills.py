"""Pre-flight + materialization tests for `RunTask.skills`.

No SDK call. Exercises `_validate_skills` and `_materialize_skills`.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sophia_motor.motor import (  # noqa: E402
    _materialize_skills,
    _validate_skills,
)


def _make_skill(parent: Path, name: str, body: str = "# skill") -> Path:
    """Create a minimal skill subdir under `parent` with SKILL.md."""
    sd = parent / name
    sd.mkdir(parents=True)
    (sd / "SKILL.md").write_text(body)
    return sd


# ─────────────────────────────────────────────────────────────────────────
# _validate_skills — happy paths
# ─────────────────────────────────────────────────────────────────────────

def test_validate_empty_list_ok():
    _validate_skills([], [])


def test_validate_single_source_ok(tmp_path):
    src = tmp_path / "skills"
    src.mkdir()
    _make_skill(src, "search")
    _make_skill(src, "think")
    _validate_skills([src], [])


def test_validate_multi_source_no_conflict_ok(tmp_path):
    src1 = tmp_path / "a"
    src2 = tmp_path / "b"
    src1.mkdir()
    src2.mkdir()
    _make_skill(src1, "search")
    _make_skill(src2, "think")
    _validate_skills([src1, src2], [])


def test_validate_subdir_without_SKILL_md_is_ignored(tmp_path):
    src = tmp_path / "skills"
    src.mkdir()
    _make_skill(src, "real")
    (src / "not-a-skill").mkdir()  # no SKILL.md → ignored
    _validate_skills([src], [])


def test_validate_disallowed_resolves_conflict(tmp_path):
    """Same name in two sources is OK if one is disallowed."""
    src1 = tmp_path / "a"
    src2 = tmp_path / "b"
    src1.mkdir()
    src2.mkdir()
    _make_skill(src1, "search")
    _make_skill(src2, "search")
    # disallowed cuts src2's "search" → no conflict (only src1 remains)
    _validate_skills([src1, src2], disallowed=["search"])


# ─────────────────────────────────────────────────────────────────────────
# _validate_skills — failure modes
# ─────────────────────────────────────────────────────────────────────────

def test_validate_missing_source_raises_FileNotFoundError(tmp_path):
    with pytest.raises(FileNotFoundError, match="does not exist"):
        _validate_skills([tmp_path / "missing"], [])


def test_validate_source_is_file_raises_ValueError(tmp_path):
    f = tmp_path / "not-a-dir.txt"
    f.write_text("x")
    with pytest.raises(ValueError, match="not a directory"):
        _validate_skills([f], [])


def test_validate_unreadable_source_raises_PermissionError(tmp_path):
    src = tmp_path / "skills"
    src.mkdir()
    src.chmod(0o000)
    try:
        with pytest.raises(PermissionError, match="not readable"):
            _validate_skills([src], [])
    finally:
        src.chmod(0o755)


def test_validate_unsupported_type_raises_TypeError():
    with pytest.raises(TypeError, match="unsupported type"):
        _validate_skills([42], [])  # type: ignore[list-item]


def test_validate_name_conflict_between_sources_raises_ValueError(tmp_path):
    src1 = tmp_path / "a"
    src2 = tmp_path / "b"
    src1.mkdir()
    src2.mkdir()
    _make_skill(src1, "shared")
    _make_skill(src2, "shared")
    with pytest.raises(ValueError, match="conflicts with same-name skill"):
        _validate_skills([src1, src2], [])


# ─────────────────────────────────────────────────────────────────────────
# _materialize_skills
# ─────────────────────────────────────────────────────────────────────────

def test_materialize_creates_symlinks(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    _make_skill(src, "search", "# search skill")
    _make_skill(src, "think", "# think skill")
    target = tmp_path / "claude_skills"
    target.mkdir()

    manifest = _materialize_skills(target, [src], [])

    assert (target / "search").is_symlink()
    assert (target / "search" / "SKILL.md").read_text() == "# search skill"
    assert (target / "think").is_symlink()
    assert (target / "think" / "SKILL.md").read_text() == "# think skill"
    assert "search" in manifest and manifest["search"].endswith("(link)")
    assert "think" in manifest


def test_materialize_skips_disallowed(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    _make_skill(src, "search")
    _make_skill(src, "heavy")
    target = tmp_path / "claude_skills"
    target.mkdir()

    manifest = _materialize_skills(target, [src], disallowed=["heavy"])

    assert (target / "search").exists()
    assert not (target / "heavy").exists()
    assert manifest == {k: v for k, v in manifest.items() if "search" in k}


def test_materialize_skips_subdir_without_SKILL_md(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    _make_skill(src, "real")
    (src / "junk").mkdir()  # no SKILL.md
    target = tmp_path / "claude_skills"
    target.mkdir()

    _materialize_skills(target, [src], [])
    assert (target / "real").exists()
    assert not (target / "junk").exists()


def test_materialize_multi_source_merges(tmp_path):
    src_a = tmp_path / "a"
    src_b = tmp_path / "b"
    src_a.mkdir()
    src_b.mkdir()
    _make_skill(src_a, "from-a")
    _make_skill(src_b, "from-b")
    target = tmp_path / "claude_skills"
    target.mkdir()

    manifest = _materialize_skills(target, [src_a, src_b], [])
    assert (target / "from-a").is_symlink()
    assert (target / "from-b").is_symlink()
    assert "from-a" in manifest and "from-b" in manifest
