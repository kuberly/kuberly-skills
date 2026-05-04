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

# All markdown under .apm/skills must end with \n so infra forks' pre-commit (end-of-file-fixer)
# does not fight APM after `apm install` (Caveman + skills deploy).
if ! python3 <<'PY'
from pathlib import Path
root = Path(".apm/skills")
if not root.is_dir():
    raise SystemExit(0)
bad = [str(p) for p in root.rglob("*.md") if p.is_file() and not p.read_bytes().endswith(b"\n")]
if bad:
    print("ERROR: these files must end with a newline (POSIX text file):")
    for x in bad:
        print(" ", x)
    raise SystemExit(1)
PY
then
  failed=1
fi

exit "$failed"
