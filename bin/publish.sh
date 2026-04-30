#!/usr/bin/env bash
# Build + (dry-check | publish) sophia-motor to PyPI.
#
# Usage:
#   bin/publish.sh check        # build + show what WOULD be uploaded (default)
#   bin/publish.sh testpypi     # upload to test.pypi.org sandbox
#   bin/publish.sh pypi         # upload to real pypi.org (PROD)
#
# Token resolution: env var TWINE_PASSWORD wins.
#                   Fallback: $HOME/.pypirc (twine reads it natively).
#
# Safety rails — refuses to upload if:
#   - the working tree is dirty (git status not clean)
#   - the version was already published to the target index
#   - twine check fails on the artifacts

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PY="${PY:-$REPO_ROOT/.venv/bin/python}"
MODE="${1:-check}"

echo "==> sophia-motor publish helper (mode: $MODE)"
echo "==> repo: $REPO_ROOT"
echo

# ── 1. Pre-flight: working tree clean ─────────────────────────────────
if [[ -n "$(git status --porcelain)" ]]; then
    echo "❌ working tree dirty — commit or stash before publishing"
    git status --short
    exit 1
fi

# ── 2. Read version from pyproject.toml ───────────────────────────────
VERSION="$(grep -E '^version *=' pyproject.toml | head -1 | sed -E 's/.*"([^"]+)".*/\1/')"
echo "==> package version: $VERSION"

# ── 3. Clean previous build artifacts ─────────────────────────────────
rm -rf dist/ build/ src/*.egg-info

# ── 4. Build wheel + sdist ────────────────────────────────────────────
echo "==> building wheel + sdist…"
$PY -m build --quiet

# ── 5. Validate metadata ──────────────────────────────────────────────
echo "==> twine check…"
$PY -m twine check dist/*

# ── 6. Show what's inside the wheel (the file users actually install) ─
echo
echo "==> wheel contents (THIS is what 'pip install' downloads):"
$PY -m zipfile -l dist/sophia_motor-${VERSION}-py3-none-any.whl

echo
echo "==> sdist contents (build-from-source, includes tests):"
tar tzf dist/sophia_motor-${VERSION}.tar.gz | sort

# ── 7. Mode-dependent action ──────────────────────────────────────────
case "$MODE" in
    check)
        echo
        echo "✓ build OK. Re-run with 'testpypi' or 'pypi' to upload."
        ;;
    testpypi)
        echo
        echo "==> Checking if version already exists on TestPyPI…"
        if curl -sf "https://test.pypi.org/pypi/sophia-motor/${VERSION}/json" >/dev/null; then
            echo "❌ version $VERSION already on test.pypi.org — bump pyproject.toml first"
            exit 1
        fi
        echo "==> uploading to test.pypi.org…"
        $PY -m twine upload --repository testpypi dist/*
        echo
        echo "✓ uploaded. Verify with:"
        echo "   pip install --index-url https://test.pypi.org/simple/ sophia-motor==$VERSION"
        ;;
    pypi)
        echo
        echo "==> Checking if version already exists on PyPI…"
        if curl -sf "https://pypi.org/pypi/sophia-motor/${VERSION}/json" >/dev/null; then
            echo "❌ version $VERSION already on pypi.org — bump pyproject.toml first"
            exit 1
        fi
        echo "⚠️  About to upload sophia-motor==$VERSION to pypi.org (PRODUCTION)"
        echo "   Press Enter to continue, Ctrl+C to abort."
        read -r
        $PY -m twine upload dist/*
        echo
        echo "✓ uploaded. Live at: https://pypi.org/project/sophia-motor/$VERSION/"
        echo "   Tag the release: git tag v$VERSION && git push --tags"
        ;;
    *)
        echo "❌ unknown mode: $MODE (use check | testpypi | pypi)"
        exit 1
        ;;
esac
