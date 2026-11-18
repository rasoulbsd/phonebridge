#!/bin/bash
# Publishes the current version: checks, public build, tag, push, GitHub release with the installer.
#
# Run from WSL (or Git Bash) in the repo, with the Windows venv set up and the GitHub CLI signed in
# to the repo owner's account. Commit your changes first; the tree must be clean.
#   1. bump __version__ in src/phonebridge/__init__.py
#   2. add a "## <version>" section to CHANGELOG.md
#   3. commit, then: bash packaging/release.sh
set -euo pipefail

cd "$(dirname "$0")/.."
version=$(sed -nE 's/^__version__ = "([^"]+)"/\1/p' src/phonebridge/__init__.py)
tag="v$version"
remote_url=$(git remote get-url origin)
repo=$(printf '%s' "$remote_url" | sed -E 's#(git@github.com:|https://github.com/)##; s#\.git$##')
owner=${repo%%/*}

say() { printf '==> %s\n' "$*"; }
fail() { printf 'release: %s\n' "$*" >&2; exit 1; }

[ -n "$version" ] || fail "couldn't read __version__"
[ -z "$(git status --porcelain)" ] || fail "commit or stash your changes first"
git rev-parse -q --verify "refs/tags/$tag" >/dev/null && fail "$tag already exists; bump __version__"
notes=$(awk -v v="## $version" '$0 == v {found=1; next} found && /^## / {exit} found {print}' CHANGELOG.md)
[ -n "$(printf '%s' "$notes" | tr -d '[:space:]')" ] || fail "add a '## $version' section to CHANGELOG.md"

say "Checking $tag"
.venv/Scripts/ruff.exe check .
.venv/Scripts/ruff.exe format --check .
.venv/Scripts/ty.exe check
.venv/Scripts/python.exe -m pytest -q

say "Building the public installer"
.venv/Scripts/python.exe packaging/build.py
installer="dist/Phonebridge-Setup-$version.exe"
[ -f "$installer" ] || fail "build didn't produce $installer"
assets=("$installer")
helper="packaging/mac/facetime-helper/PhonebridgeFaceTime.dylib"
[ -f "$helper" ] && assets+=("$helper")

say "Tagging and pushing $tag"
git tag -a "$tag" -m "Phonebridge $version"
git push origin HEAD
git push origin "$tag"

say "Publishing the GitHub release"
token=$(gh auth token --user "$owner" 2>/dev/null || gh auth token)
GH_TOKEN="$token" gh release create "$tag" "${assets[@]}" \
    --repo "$repo" --title "Phonebridge $version" --notes "$notes" --verify-tag
say "Released https://github.com/$repo/releases/tag/$tag"
