#!/usr/bin/env python3
"""docs_graph — index every doc/skill/agent/prompt/openspec change in the
repo, plus the cross-link / cross-mention edges, into a single
`.claude/docs_overlay.json` consumed by KuberlyPlatform.

Where state_graph.py is the infra layer and k8s_graph.py is the runtime
layer, docs_graph.py is the **knowledge layer**: which file explains
which thing, what links what, what skill mentions which module.

The deterministic index (frontmatter + headings + markdown links + module
mentions) is always built. **Optional semantic embeddings** can be
layered on top by setting `KUBERLY_DOCS_EMBED=openai` and
`OPENAI_API_KEY` — incremental by content hash, so only changed files
get a fresh API call.

Designed to run from a pre-commit hook, fast on the deterministic path
(no network, stdlib only).

Usage:
    python3 docs_graph.py generate                       # incremental
    python3 docs_graph.py generate --full                # rescan all files
    python3 docs_graph.py generate --embed               # also embed (env-driven)
    python3 docs_graph.py generate --paths agents/,docs/ # subset

Output schema (see _validate_overlay for the strict version):
{
  "schema_version": 1,
  "generated_at": "...",
  "generator": "kuberly-skills/docs_graph.py",
  "embed_provider": "openai" | "",
  "docs": [
    {
      "id": "skill/kuberly-stack-context",
      "kind": "skill",
      "path": ".apm/skills/kuberly-stack-context/SKILL.md",
      "title": "Kuberly stack context",
      "description": "Orient agents to the kuberly-stack ...",
      "headings": ["Invariants", "First reads in the repo", ...],
      "tools": ["mcp__kuberly-platform__query_nodes", ...],
      "linked_docs": ["skill/openspec-changelog-audit", ...],
      "mentions": {
        "modules": ["loki", "grafana"],
        "components": [],
        "applications": []
      },
      "content_sha": "sha256:...",
      "embedding_b64": "base64-encoded float32 array if --embed used"
    }
  ]
}
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import struct
import subprocess
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1

# ----- doc kind detection ---------------------------------------------

# Maps a path -> doc kind. Order matters: most-specific first.
_KIND_PATTERNS = [
    (re.compile(r"^agents/[a-z][a-z0-9_-]*\.md$"), "agent"),
    (re.compile(r"^\.apm/skills/[a-z][a-zA-Z0-9_-]*/SKILL\.md$"), "skill"),
    (re.compile(r"^\.claude/skills/[a-z][a-zA-Z0-9_-]*/SKILL\.md$"), "skill"),
    (re.compile(r"^\.cursor/skills/[a-z][a-zA-Z0-9_-]*/SKILL\.md$"), "skill"),
    (re.compile(r"^openspec/changes/[a-zA-Z0-9_-]+/(?:proposal|tasks|design|CHANGELOG)\.md$"), "openspec"),
    (re.compile(r"^docs/[a-zA-Z0-9_./-]+\.md$"), "doc"),
    (re.compile(r"^references/[a-zA-Z0-9_./-]+\.md$"), "reference"),
    (re.compile(r"^prompts/[a-zA-Z0-9_./-]+\.md$"), "prompt"),
    (re.compile(r"^README\.md$"), "doc"),
    (re.compile(r"^[A-Z][A-Z0-9_]*\.md$"), "doc"),  # ARCHITECTURE.md, AGENTS.md, etc.
    (re.compile(r"^mcp/[a-zA-Z0-9_-]+/README\.md$"), "doc"),
]

# Files we always skip (autogen / build artifacts).
_SKIP_PREFIXES = (
    ".git/", ".terraform/", ".terragrunt-cache/", "node_modules/",
    "apm_modules/", ".venv", "__pycache__/",
    # Skip apm-deployed mirrors of skills — only the canonical
    # `.apm/skills/` source is indexed (other dirs are derived).
    ".claude/skills/", ".cursor/skills/", ".github/skills/",
    ".opencode/skills/", ".agents/skills/",
)

# Regexes for safety.
_RE_REL_PATH = re.compile(r"^[a-zA-Z0-9._/+@-]{1,512}$")
_RE_DOC_ID = re.compile(r"^[a-z][a-z0-9_]*/[a-zA-Z0-9._/+@-]{1,256}$")
_RE_TITLE = re.compile(r"^[\x20-\x7e]{0,256}$")  # printable ASCII; keep titles tame
_RE_NAME = re.compile(r"^[a-zA-Z0-9._-]{1,128}$")
_RE_TOOL = re.compile(r"^[a-zA-Z0-9_]{1,128}(?:__[a-zA-Z0-9_]{1,128}){0,3}$")

_MAX_HEADINGS = 50
_MAX_LINKS = 100
_MAX_MENTIONS = 200
_MAX_TITLE = 256
_MAX_DESC = 1024


def _classify(rel_path: str) -> str | None:
    for pat, kind in _KIND_PATTERNS:
        if pat.match(rel_path):
            return kind
    return None


def _doc_id(rel_path: str, kind: str) -> str:
    """Stable, human-readable id."""
    if kind == "skill":
        # path is `.apm/skills/<name>/SKILL.md` -> `skill/<name>`
        m = re.match(r"^\.apm/skills/([a-zA-Z0-9_-]+)/SKILL\.md$", rel_path)
        if m:
            return f"skill/{m.group(1)}"
    if kind == "agent":
        m = re.match(r"^agents/([a-zA-Z0-9_-]+)\.md$", rel_path)
        if m:
            return f"agent/{m.group(1)}"
    if kind == "openspec":
        m = re.match(r"^openspec/changes/([a-zA-Z0-9_-]+)/", rel_path)
        if m:
            slug = rel_path.rsplit("/", 1)[-1].replace(".md", "")
            return f"openspec/{m.group(1)}/{slug}"
    # Fallback: <kind>/<rel-path-without-ext>
    base = rel_path.replace(".md", "")
    return f"{kind}/{base}"


# ----- markdown parsing -----------------------------------------------

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
_MD_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
# Backtick-wrapped names: `module-name`, `skill-name`
_BACKTICK_RE = re.compile(r"`([a-z][a-z0-9_-]{2,128})`")


def _parse_frontmatter(text: str) -> dict:
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}
    body = m.group(1)
    # Hand-parse — no PyYAML dep. Only top-level scalar keys + simple lists.
    out: dict = {}
    current_list: list | None = None
    current_key: str | None = None
    for line in body.splitlines():
        line = line.rstrip()
        if not line or line.startswith("#"):
            continue
        if current_list is not None and line.startswith("  - "):
            current_list.append(line[4:].strip().strip('"').strip("'"))
            continue
        # End of list
        current_list = None
        m2 = re.match(r"^([a-zA-Z][a-zA-Z0-9_-]*):\s*(.*)$", line)
        if not m2:
            continue
        key = m2.group(1)
        val = m2.group(2)
        if not val:
            current_list = []
            current_key = key
            out[key] = current_list
            continue
        # inline list?  e.g. tools: a, b, c
        if "," in val and key in ("tools",):
            out[key] = [v.strip() for v in val.split(",") if v.strip()]
        else:
            out[key] = val.strip().strip('"').strip("'")
    # If a list ended up empty (no entries), drop it
    return {k: v for k, v in out.items() if v not in ([], "")}


def _parse_headings(text: str) -> list[str]:
    out: list[str] = []
    for m in _HEADING_RE.finditer(text):
        h = m.group(2).strip()
        if h and len(h) <= _MAX_TITLE:
            out.append(h)
        if len(out) >= _MAX_HEADINGS:
            break
    return out


def _parse_links(text: str, repo_root: Path, this_path: Path) -> list[str]:
    """Return relative paths to docs this doc links to."""
    out: list[str] = []
    for m in _MD_LINK_RE.finditer(text):
        href = m.group(1)
        # Skip URLs and anchors
        if href.startswith(("http://", "https://", "mailto:", "#")):
            continue
        # Strip query / fragment
        href = href.split("#", 1)[0].split("?", 1)[0]
        if not href:
            continue
        # Resolve relative to this file's parent
        resolved = (this_path.parent / href).resolve()
        try:
            rel = resolved.relative_to(repo_root)
        except ValueError:
            continue
        rs = str(rel).replace("\\", "/")
        if not _RE_REL_PATH.match(rs):
            continue
        if rs.endswith(".md"):
            out.append(rs)
        if len(out) >= _MAX_LINKS:
            break
    return out


def _detect_mentions(text: str, known_modules: set[str], known_components: set[str],
                     known_applications: set[str]) -> dict:
    """Find mentions of known module/component/app names — bounded by
    backtick-wrapped or word-boundary occurrences. Set-bound, so size is
    capped by the input set size."""
    found_modules: set[str] = set()
    found_components: set[str] = set()
    found_applications: set[str] = set()

    for m in _BACKTICK_RE.finditer(text):
        token = m.group(1)
        if token in known_modules:
            found_modules.add(token)
        elif token in known_components:
            found_components.add(token)
        elif token in known_applications:
            found_applications.add(token)

    return {
        "modules": sorted(found_modules)[:_MAX_MENTIONS],
        "components": sorted(found_components)[:_MAX_MENTIONS],
        "applications": sorted(found_applications)[:_MAX_MENTIONS],
    }


# ----- repo discovery --------------------------------------------------

def _discover_known_names(repo_root: Path) -> tuple[set[str], set[str], set[str]]:
    """Return (modules, components, applications) name sets."""
    modules: set[str] = set()
    components: set[str] = set()
    applications: set[str] = set()
    for cloud in (repo_root / "clouds").glob("*"):
        for mod in (cloud / "modules").glob("*"):
            if mod.is_dir():
                modules.add(mod.name)
    for env in (repo_root / "components").glob("*"):
        if env.is_dir():
            for jf in env.glob("*.json"):
                components.add(jf.stem)
    for env in (repo_root / "applications").glob("*"):
        if env.is_dir():
            for jf in env.glob("*.json"):
                applications.add(jf.stem)
    return modules, components, applications


def _walk_doc_files(repo_root: Path, paths_filter: list[str] | None) -> list[Path]:
    """Walk repo for .md files matching one of the kind patterns."""
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(repo_root):
        rel_dir = os.path.relpath(dirpath, repo_root).replace("\\", "/") + "/"
        if rel_dir == "./":
            rel_dir = ""
        if any(rel_dir.startswith(p) for p in _SKIP_PREFIXES):
            dirnames.clear()
            continue
        for fn in filenames:
            if not fn.endswith(".md"):
                continue
            full = Path(dirpath) / fn
            try:
                rel = full.relative_to(repo_root)
            except ValueError:
                continue
            rs = str(rel).replace("\\", "/")
            if any(rs.startswith(p) for p in _SKIP_PREFIXES):
                continue
            if paths_filter and not any(rs.startswith(p) for p in paths_filter):
                continue
            if _classify(rs) is None:
                continue
            out.append(full)
    return sorted(out)


# ----- per-doc extractor ----------------------------------------------

def _extract_doc(repo_root: Path, full: Path, known: tuple) -> dict | None:
    rel = str(full.relative_to(repo_root)).replace("\\", "/")
    kind = _classify(rel)
    if not kind:
        return None
    try:
        text = full.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    sha = "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]
    fm = _parse_frontmatter(text)
    headings = _parse_headings(text)
    title = (
        fm.get("name") or fm.get("title")
        or (headings[0] if headings else rel.rsplit("/", 1)[-1])
    )
    if not isinstance(title, str):
        title = str(title)
    title = title[:_MAX_TITLE]
    description = fm.get("description") or ""
    if not isinstance(description, str):
        description = ""
    description = description[:_MAX_DESC]

    # Tools are an agent-frontmatter convention; flatten string -> list.
    tools_raw = fm.get("tools")
    tools: list[str] = []
    if isinstance(tools_raw, str):
        tools = [t.strip() for t in tools_raw.split(",") if t.strip()]
    elif isinstance(tools_raw, list):
        tools = [t for t in tools_raw if isinstance(t, str)]
    tools = [t for t in tools if _RE_TOOL.match(t)][:64]

    linked = _parse_links(text, repo_root, full)
    mentions = _detect_mentions(text, *known)

    doc_id = _doc_id(rel, kind)
    if not _RE_DOC_ID.match(doc_id):
        return None

    return {
        "id": doc_id,
        "kind": kind,
        "path": rel,
        "title": title if _RE_TITLE.match(title) else "",
        "description": description if _RE_TITLE.match(description) else description.encode("ascii", "ignore").decode(),
        "headings": headings,
        "tools": tools,
        "linked_docs": linked,
        "mentions": mentions,
        "content_sha": sha,
    }


# ----- embeddings (optional) ------------------------------------------

def _embed_text(text: str, provider: str) -> bytes | None:
    """Return raw bytes (float32 array) for the embedding, or None on
    failure / unsupported provider. We swallow per-doc failures and
    keep going — the deterministic index always lands."""
    if provider == "openai":
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            return None
        body = json.dumps({
            "model": "text-embedding-3-small",
            "input": text[:8000],  # OpenAI limit ~8191 tokens; cap by chars
        }).encode("utf-8")
        req = urllib.request.Request(
            "https://api.openai.com/v1/embeddings",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = json.loads(resp.read())
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError):
            return None
        try:
            vec = payload["data"][0]["embedding"]
        except (KeyError, IndexError, TypeError):
            return None
        if not isinstance(vec, list):
            return None
        return struct.pack(f"{len(vec)}f", *vec)
    return None


def _embed_text_b64(text: str, provider: str) -> str:
    raw = _embed_text(text, provider)
    if raw is None:
        return ""
    return base64.b64encode(raw).decode("ascii")


# ----- top-level build ------------------------------------------------

def build_overlay(repo_root: Path, paths_filter: list[str] | None,
                  embed: bool, full_rescan: bool,
                  prev_overlay: dict | None) -> dict:
    """Build the overlay. If `prev_overlay` is given and `full_rescan` is
    False, we reuse `embedding_b64` from prev_overlay for any doc whose
    `content_sha` is unchanged — keeps embedding API calls minimal."""
    known = _discover_known_names(repo_root)
    files = _walk_doc_files(repo_root, paths_filter)
    docs: list[dict] = []

    prev_by_id: dict[str, dict] = {}
    if prev_overlay and isinstance(prev_overlay.get("docs"), list):
        for d in prev_overlay["docs"]:
            if isinstance(d, dict) and "id" in d:
                prev_by_id[d["id"]] = d

    provider = ""
    if embed:
        provider = os.environ.get("KUBERLY_DOCS_EMBED", "").strip()
        if provider not in ("openai",):
            print(
                "  embed: KUBERLY_DOCS_EMBED unset or unsupported — skipping vectors",
                file=sys.stderr,
            )
            provider = ""

    for f in files:
        d = _extract_doc(repo_root, f, known)
        if not d:
            continue
        if provider:
            prev = prev_by_id.get(d["id"])
            if prev and prev.get("content_sha") == d["content_sha"] and prev.get("embedding_b64"):
                d["embedding_b64"] = prev["embedding_b64"]
            else:
                # Concatenate title + description + headings + a tail of body
                # text for the embed, capped to keep API cost bounded.
                snippet = "\n".join([
                    d["title"], d["description"], "\n".join(d["headings"][:20]),
                ])
                text = snippet[:4000]
                d["embedding_b64"] = _embed_text_b64(text, provider)
        docs.append(d)

    overlay = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "generator": "kuberly-skills/docs_graph.py",
        "embed_provider": provider,
        "docs": sorted(docs, key=lambda d: d["id"]),
    }
    return _validate_overlay(overlay)


# ----- final validator -----------------------------------------------

def _validate_overlay(doc: dict) -> dict:
    if doc.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"unexpected schema_version: {doc.get('schema_version')!r}")
    out_docs: list[dict] = []
    seen_ids: set[str] = set()
    for d in doc.get("docs") or []:
        if not isinstance(d, dict):
            continue
        did = d.get("id")
        path = d.get("path")
        kind = d.get("kind")
        sha = d.get("content_sha")
        if (not isinstance(did, str) or not _RE_DOC_ID.match(did)
            or not isinstance(path, str) or not _RE_REL_PATH.match(path)
            or not isinstance(kind, str) or not re.match(r"^[a-z]+$", kind)
            or not isinstance(sha, str) or not sha.startswith("sha256:")):
            continue
        if did in seen_ids:
            continue
        seen_ids.add(did)

        title = d.get("title", "") or ""
        description = d.get("description", "") or ""
        headings_raw = d.get("headings", []) or []
        tools_raw = d.get("tools", []) or []
        linked_raw = d.get("linked_docs", []) or []
        mentions_raw = d.get("mentions", {}) or {}
        embed_b64 = d.get("embedding_b64", "") or ""

        # Filter all string-collection fields.
        headings = [h for h in headings_raw if isinstance(h, str) and len(h) <= _MAX_TITLE][:_MAX_HEADINGS]
        tools = [t for t in tools_raw if isinstance(t, str) and _RE_TOOL.match(t)][:64]
        linked = [l for l in linked_raw if isinstance(l, str) and _RE_REL_PATH.match(l)][:_MAX_LINKS]
        m_modules = [m for m in (mentions_raw.get("modules") or []) if isinstance(m, str) and _RE_NAME.match(m)][:_MAX_MENTIONS]
        m_components = [m for m in (mentions_raw.get("components") or []) if isinstance(m, str) and _RE_NAME.match(m)][:_MAX_MENTIONS]
        m_applications = [m for m in (mentions_raw.get("applications") or []) if isinstance(m, str) and _RE_NAME.match(m)][:_MAX_MENTIONS]

        # Embedding b64 sanity: only base64 chars, length plausible (~8KB max)
        if embed_b64 and (len(embed_b64) > 65536 or not re.match(r"^[A-Za-z0-9+/=]+$", embed_b64)):
            embed_b64 = ""

        out_docs.append({
            "id": did,
            "kind": kind,
            "path": path,
            "title": title[:_MAX_TITLE],
            "description": description[:_MAX_DESC],
            "headings": headings,
            "tools": tools,
            "linked_docs": linked,
            "mentions": {
                "modules": m_modules,
                "components": m_components,
                "applications": m_applications,
            },
            "content_sha": sha,
            "embedding_b64": embed_b64,
        })

    generated_at = doc.get("generated_at", "")
    if not isinstance(generated_at, str) or len(generated_at) > 40:
        generated_at = ""
    provider = doc.get("embed_provider", "")
    if not isinstance(provider, str) or not re.match(r"^[a-z0-9_-]{0,32}$", provider):
        provider = ""
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "generator": "kuberly-skills/docs_graph.py",
        "embed_provider": provider,
        "docs": out_docs,
    }


def _write_overlay(overlay: dict, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as fh:
        json.dump(overlay, fh, indent=2, sort_keys=False)
        fh.write("\n")


def _read_overlay(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


# ----- CLI ------------------------------------------------------------

def _cmd_generate(args: argparse.Namespace) -> int:
    repo = Path(args.repo or os.getcwd()).resolve()
    output = Path(args.output) if args.output else (repo / ".claude" / "docs_overlay.json")
    paths = (
        [p.strip().rstrip("/") + "/" for p in args.paths.split(",") if p.strip()]
        if args.paths else None
    )
    prev = None if args.full else _read_overlay(output)
    overlay = build_overlay(repo, paths, embed=args.embed, full_rescan=args.full,
                            prev_overlay=prev)
    if args.dry_run:
        print(json.dumps(overlay, indent=2))
        return 0
    _write_overlay(overlay, output)
    by_kind: dict[str, int] = {}
    n_emb = 0
    for d in overlay["docs"]:
        by_kind[d["kind"]] = by_kind.get(d["kind"], 0) + 1
        if d.get("embedding_b64"):
            n_emb += 1
    summary = ", ".join(f"{k}={v}" for k, v in sorted(by_kind.items()))
    rel = output.relative_to(repo) if output.is_relative_to(repo) else output
    msg = f"wrote {rel} — {len(overlay['docs'])} docs ({summary})"
    if overlay.get("embed_provider"):
        msg += f", embeddings={n_emb} via {overlay['embed_provider']}"
    print(msg)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="docs_graph")
    sub = p.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate", help="build / refresh the docs overlay")
    g.add_argument("--repo", help="repo root (default: cwd)")
    g.add_argument("--output", help="output path (default: <repo>/.claude/docs_overlay.json)")
    g.add_argument("--paths", help="comma-separated path prefixes to limit the scan, e.g. 'agents/,docs/'")
    g.add_argument("--full", action="store_true", help="ignore prior overlay (full rescan, including embeddings)")
    g.add_argument("--embed", action="store_true",
                   help="also compute embeddings if KUBERLY_DOCS_EMBED is set")
    g.add_argument("--dry-run", action="store_true", help="print to stdout, do not write")
    g.set_defaults(func=_cmd_generate)
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
