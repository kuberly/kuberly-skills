#!/usr/bin/env bash
# Validate .apm/skills/**/SKILL.md frontmatter and basic secret heuristics.
set -euo pipefail
cd "$(dirname "$0")/.."
shopt -s globstar nullglob
files=(.apm/skills/**/SKILL.md)
if [[ ${#files[@]} -eq 0 ]]; then
  echo "No .apm/skills/**/SKILL.md found"
  exit 1
fi
failed=0
for f in "${files[@]}"; do
  if ! head -n 1 "$f" | grep -q '^---$'; then
    echo "ERROR: $f — missing opening --- frontmatter"
    failed=1
    continue
  fi
  if ! grep -q '^name:' "$f"; then
    echo "ERROR: $f — frontmatter must include name:"
    failed=1
  fi
  if ! grep -q '^description:' "$f"; then
    echo "ERROR: $f — frontmatter must include description:"
    failed=1
  fi
  if grep -Ei '(aws_secret_access_key|BEGIN (RSA |OPENSSH )?PRIVATE KEY|api_key\s*[:=])' "$f"; then
    echo "ERROR: $f — possible secret material"
    failed=1
  fi
done

# Slash commands (Cursor + Claude): .apm/cursor/commands/*.md
cmd_dir=".apm/cursor/commands"
if [[ -d "$cmd_dir" ]]; then
  shopt -s nullglob
  for f in "$cmd_dir"/*.md; do
    [[ -f "$f" ]] || continue
    if ! head -n 1 "$f" | grep -q '^---$'; then
      echo "ERROR: $f — missing opening --- frontmatter"
      failed=1
      continue
    fi
    if ! grep -q '^name:' "$f" || ! grep -q '^description:' "$f"; then
      echo "ERROR: $f — frontmatter must include name: and description:"
      failed=1
    fi
  done
  shopt -u nullglob
fi

# All markdown under .apm/skills must end with \n so infra forks' pre-commit (end-of-file-fixer)
# does not fight APM after `apm install` (Caveman + skills deploy).
if ! python3 <<'PY'
from pathlib import Path
roots = [Path(".apm/skills"), Path(".apm/cursor/commands")]
bad = []
for root in roots:
    if not root.is_dir():
        continue
    if root.name == "commands":
        paths = list(root.glob("*.md"))
    else:
        paths = list(root.rglob("*.md"))
    for p in paths:
        if p.is_file() and not p.read_bytes().endswith(b"\n"):
            bad.append(str(p))
if bad:
    print("ERROR: these files must end with a newline (POSIX text file):")
    for x in bad:
        print(" ", x)
    raise SystemExit(1)
PY
then
  failed=1
fi

# README index parity: every skill on disk must appear in an "## Index" table,
# and every skill named in those tables must exist on disk.
if ! python3 <<'PY'
import re, sys
from pathlib import Path

skills_dir = Path(".apm/skills")
readme = Path("README.md")
if not skills_dir.is_dir() or not readme.is_file():
    raise SystemExit(0)

on_disk = {p.parent.name for p in skills_dir.glob("*/SKILL.md")}

indexed = set()
in_index = False
for line in readme.read_text().splitlines():
    if line.startswith("## Index"):
        in_index = True
        continue
    if line.startswith("## ") and in_index:
        in_index = False
    if in_index:
        for m in re.finditer(r"\*\*`([a-z0-9][a-z0-9-]*)`\*\*", line):
            indexed.add(m.group(1))

missing_in_readme = sorted(on_disk - indexed)
missing_on_disk  = sorted(indexed - on_disk)
errors = False
if missing_in_readme:
    print("ERROR: skills exist on disk but are missing from README ## Index tables:")
    for s in missing_in_readme:
        print(f"  {s}")
    errors = True
if missing_on_disk:
    print("ERROR: README ## Index tables reference nonexistent skills:")
    for s in missing_on_disk:
        print(f"  {s}")
    errors = True
if errors:
    raise SystemExit(1)
PY
then
  failed=1
fi

exit "$failed"
