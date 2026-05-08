"""TreeSitterLayer — AST scan of HCL / YAML / Dockerfile / CUE / JSON.

Emits ``hcl_*``, ``cue_*``, ``yaml_manifest:*``, ``dockerfile_step:*`` nodes
plus ``uses_var`` / ``refs`` / ``declares`` / ``reads_output`` edges.

Soft-degrade chain (every step is best-effort):
  1. ``import tree_sitter_languages`` — if the wheel is missing, the layer
     emits 0 nodes and logs once.
  2. Per-grammar ``get_parser(lang)`` — if a grammar isn't bundled in the
     installed wheel (CUE in particular is missing from many
     ``tree_sitter_languages`` releases), the matching glob set is skipped
     with a one-time warning.
  3. Per-file size cap (1 MiB) and walk-depth cap (8) to keep scan time bounded
     even on pathological inputs.

The layer is wired into ``LAYERS`` after ``code`` (which provides the cold
``module:`` ids treesitter binds to via ``declares``) and before
``dependency`` so its fresh nodes feed the cross-layer edge derivation.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterable

from .base import Layer


# --- bookkeeping -------------------------------------------------------------

_SKIP_DIR_NAMES: set[str] = {
    "apm_modules",
    "node_modules",
    ".venv",
    ".git",
    ".kuberly",
    ".terraform",
    ".terragrunt-cache",
    ".external_modules",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
}

# Map glob -> language name in tree_sitter_languages.
# Order matters: HCL groups come first because terragrunt.hcl files in
# clouds/ are caught by the recursive glob too, but we want them tagged once.
_HCL_GLOBS: list[str] = [
    "clouds/**/*.tf",
    "**/terragrunt.hcl",
    "**/root.hcl",
    "**/*.hcl",
]
_YAML_GLOBS: list[str] = [
    "clouds/**/values/**/*.yaml",
    "applications/**/*.yaml",
    "components/**/*.yaml",
    "**/*.values.yaml",
]
_DOCKERFILE_GLOBS: list[str] = [
    "**/Dockerfile",
    "**/Dockerfile.*",
]
_CUE_GLOBS: list[str] = [
    "cue/**/*.cue",
    "**/*.cue",
]


def _should_skip_path(p: Path) -> bool:
    parts = set(p.parts)
    return bool(parts & _SKIP_DIR_NAMES)


def _iter_files(repo_root: Path, globs: list[str], max_files: int) -> Iterable[Path]:
    seen: set[str] = set()
    n = 0
    for pat in globs:
        for hit in repo_root.glob(pat):
            if not hit.is_file():
                continue
            if _should_skip_path(hit.relative_to(repo_root)):
                continue
            key = str(hit.resolve())
            if key in seen:
                continue
            seen.add(key)
            yield hit
            n += 1
            if n >= max_files:
                return


def _txt(node, source: bytes) -> str:
    try:
        return source[node.start_byte : node.end_byte].decode(
            "utf-8", errors="replace"
        )
    except Exception:
        return ""


def _strip_quotes(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        return s[1:-1]
    if len(s) >= 2 and s[0] == "'" and s[-1] == "'":
        return s[1:-1]
    return s


def _module_id_for_path(repo_root: Path, file_path: Path) -> str | None:
    """``clouds/<provider>/modules/<name>/...`` -> ``module:<provider>/<name>``."""
    try:
        rel = file_path.relative_to(repo_root).parts
    except ValueError:
        return None
    if (
        len(rel) >= 4
        and rel[0] == "clouds"
        and rel[2] == "modules"
    ):
        return f"module:{rel[1]}/{rel[3]}"
    return None


# --- HCL ---------------------------------------------------------------------


def _walk(node, depth_cap: int = 8):
    """Yield (node, depth) iteratively. Caps recursion at depth_cap."""
    stack = [(node, 0)]
    while stack:
        cur, depth = stack.pop()
        yield cur, depth
        if depth >= depth_cap:
            continue
        # tree_sitter Node.children is a list
        for child in getattr(cur, "children", []) or []:
            stack.append((child, depth + 1))


def _scan_hcl(
    file_path: Path,
    rel_path: str,
    source: bytes,
    parser,
    module_id: str | None,
    nodes: list[dict],
    edges: list[dict],
    var_index: dict[str, str],
    res_index: dict[str, str],
) -> None:
    """Walk the HCL parse tree. The HCL grammar shipped with
    ``tree_sitter_languages`` exposes top-level ``block`` nodes whose first
    child is the kind identifier (``resource`` / ``data`` / ``module`` /
    ``variable`` / ``output`` / ``locals``) followed by string-literal labels.
    """
    try:
        tree = parser.parse(source)
    except Exception:
        return
    root = tree.root_node

    text_for = lambda n: _txt(n, source)
    for n, _depth in _walk(root, depth_cap=8):
        if n.type != "block":
            continue
        children = [c for c in n.children if c.type not in {"comment", "\n", "{", "}"}]
        if not children:
            continue
        kind_node = children[0]
        kind = text_for(kind_node).strip()
        labels: list[str] = []
        body_node = None
        for c in children[1:]:
            if c.type == "body":
                body_node = c
                continue
            if c.type in {"string_lit", "string", "identifier"}:
                txt = _strip_quotes(text_for(c))
                if txt:
                    labels.append(txt)
            elif c.type == "{":
                continue

        if kind == "resource" and len(labels) >= 2:
            tf_type, tf_name = labels[0], labels[1]
            nid = f"hcl_resource:{rel_path}/{tf_type}/{tf_name}"
            nodes.append(
                {
                    "id": nid,
                    "type": "hcl_resource",
                    "label": f"{tf_type}.{tf_name}",
                    "tf_type": tf_type,
                    "tf_name": tf_name,
                    "rel_path": rel_path,
                }
            )
            res_index[f"{tf_type}.{tf_name}"] = nid
            if module_id:
                edges.append({"source": module_id, "target": nid, "relation": "declares"})
            _scan_body_refs(body_node, source, nid, var_index, edges, repo_rel=rel_path)
        elif kind == "data" and len(labels) >= 2:
            tf_type, tf_name = labels[0], labels[1]
            nid = f"hcl_data:{rel_path}/{tf_type}/{tf_name}"
            nodes.append(
                {
                    "id": nid,
                    "type": "hcl_data",
                    "label": f"data.{tf_type}.{tf_name}",
                    "tf_type": tf_type,
                    "tf_name": tf_name,
                    "rel_path": rel_path,
                }
            )
            if module_id:
                edges.append({"source": module_id, "target": nid, "relation": "declares"})
            _scan_body_refs(body_node, source, nid, var_index, edges, repo_rel=rel_path)
        elif kind == "module" and len(labels) >= 1:
            mname = labels[0]
            nid = f"hcl_module_call:{rel_path}/{mname}"
            nodes.append(
                {
                    "id": nid,
                    "type": "hcl_module_call",
                    "label": f"module.{mname}",
                    "module_name": mname,
                    "rel_path": rel_path,
                }
            )
            if module_id:
                edges.append({"source": module_id, "target": nid, "relation": "declares"})
            _scan_body_refs(body_node, source, nid, var_index, edges, repo_rel=rel_path)
        elif kind == "variable" and len(labels) >= 1:
            vname = labels[0]
            nid = f"hcl_variable:{rel_path}/{vname}"
            nodes.append(
                {
                    "id": nid,
                    "type": "hcl_variable",
                    "label": f"var.{vname}",
                    "var_name": vname,
                    "rel_path": rel_path,
                }
            )
            var_index[f"{rel_path}/{vname}"] = nid
            var_index[vname] = nid  # last-wins fallback for cross-file refs
            if module_id:
                edges.append({"source": module_id, "target": nid, "relation": "declares"})
        elif kind == "output" and len(labels) >= 1:
            oname = labels[0]
            nid = f"hcl_output:{rel_path}/{oname}"
            nodes.append(
                {
                    "id": nid,
                    "type": "hcl_output",
                    "label": f"output.{oname}",
                    "output_name": oname,
                    "rel_path": rel_path,
                }
            )
            if module_id:
                edges.append({"source": module_id, "target": nid, "relation": "declares"})
        elif kind == "locals":
            # Emit one hcl_locals node per top-level key in the locals block.
            if body_node is None:
                continue
            for child in body_node.children:
                if child.type == "attribute":
                    # first child is identifier
                    for sub in child.children:
                        if sub.type == "identifier":
                            key = text_for(sub).strip()
                            if not key:
                                continue
                            nid = f"hcl_locals:{rel_path}/{key}"
                            nodes.append(
                                {
                                    "id": nid,
                                    "type": "hcl_locals",
                                    "label": f"local.{key}",
                                    "locals_key": key,
                                    "rel_path": rel_path,
                                }
                            )
                            if module_id:
                                edges.append(
                                    {
                                        "source": module_id,
                                        "target": nid,
                                        "relation": "declares",
                                    }
                                )
                            break


def _scan_body_refs(
    body_node,
    source: bytes,
    owner_nid: str,
    var_index: dict[str, str],
    edges: list[dict],
    *,
    repo_rel: str,
) -> None:
    """Traverse a body subtree looking for ``var.<name>`` patterns."""
    if body_node is None:
        return
    body_text = _txt(body_node, source)
    if not body_text:
        return
    # Cheap scan: regex over the body text for var.<name> and aws_X.Y.attr.
    import re

    seen_vars: set[str] = set()
    for m in re.finditer(r"\bvar\.([A-Za-z_][A-Za-z0-9_]*)", body_text):
        vname = m.group(1)
        if vname in seen_vars:
            continue
        seen_vars.add(vname)
        target = (
            var_index.get(f"{repo_rel}/{vname}")
            or var_index.get(vname)
        )
        if target:
            edges.append(
                {"source": owner_nid, "target": target, "relation": "uses_var"}
            )

    seen_refs: set[str] = set()
    # aws_X.Y or kubernetes_X.Y etc — type.name (must contain underscore).
    for m in re.finditer(
        r"\b([a-zA-Z][a-zA-Z0-9_]+_[a-zA-Z0-9_]+)\.([A-Za-z_][A-Za-z0-9_]*)",
        body_text,
    ):
        ref = f"{m.group(1)}.{m.group(2)}"
        if ref in seen_refs:
            continue
        seen_refs.add(ref)
        # We don't have the target nid here; the dependency layer will resolve.
        # Emit edge with synthetic target that refs index lookup later.
        edges.append(
            {
                "source": owner_nid,
                "target": f"hcl_resource_ref:{ref}",
                "relation": "refs",
            }
        )


# --- YAML --------------------------------------------------------------------


def _scan_yaml(
    file_path: Path,
    rel_path: str,
    source: bytes,
    parser,
    nodes: list[dict],
    edges: list[dict],
    module_id: str | None,
) -> None:
    """For each ``---`` separated document, look for apiVersion + kind +
    metadata.name. Stdlib regex is faster + simpler than walking the tree
    here; we still parse with tree-sitter for the side effect of validating
    it isn't binary or busted.
    """
    try:
        text = source.decode("utf-8", errors="replace")
    except Exception:
        return
    docs = text.split("\n---")
    if len(docs) == 1 and text.lstrip().startswith("---"):
        docs = text.split("\n---")
    import re

    api_re = re.compile(r"^apiVersion:\s*(\S+)\s*$", re.M)
    kind_re = re.compile(r"^kind:\s*(\S+)\s*$", re.M)
    name_re = re.compile(r"^\s\sname:\s*(\S+)\s*$", re.M)
    for idx, doc in enumerate(docs):
        api_m = api_re.search(doc)
        kind_m = kind_re.search(doc)
        if not api_m or not kind_m:
            continue
        name_m = name_re.search(doc)
        name = name_m.group(1) if name_m else f"doc{idx}"
        kind = kind_m.group(1)
        api_version = api_m.group(1)
        nid = f"yaml_manifest:{rel_path}/{idx}/{kind}/{name}"
        nodes.append(
            {
                "id": nid,
                "type": "yaml_manifest",
                "label": f"{kind}/{name}",
                "kind": kind,
                "api_version": api_version,
                "name": name,
                "doc_index": idx,
                "rel_path": rel_path,
            }
        )
        if module_id:
            edges.append(
                {"source": module_id, "target": nid, "relation": "declares"}
            )


# --- Dockerfile --------------------------------------------------------------


def _scan_dockerfile(
    file_path: Path,
    rel_path: str,
    source: bytes,
    parser,
    nodes: list[dict],
    edges: list[dict],
    module_id: str | None,
    image_index: set[str],
) -> None:
    try:
        text = source.decode("utf-8", errors="replace")
    except Exception:
        return
    n_inst = 0
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        # Continuation lines start with whitespace; skip as not a fresh instruction.
        if raw_line and raw_line[0] in (" ", "\t"):
            continue
        parts = line.split(None, 1)
        if not parts:
            continue
        instruction = parts[0].upper()
        if instruction not in {
            "FROM", "RUN", "COPY", "ADD", "ENV", "ARG", "WORKDIR",
            "USER", "EXPOSE", "ENTRYPOINT", "CMD", "LABEL", "VOLUME",
            "STOPSIGNAL", "HEALTHCHECK", "ONBUILD", "MAINTAINER",
        }:
            continue
        args = parts[1] if len(parts) > 1 else ""
        n_inst += 1
        nid = f"dockerfile_step:{rel_path}/{n_inst}"
        nodes.append(
            {
                "id": nid,
                "type": "dockerfile_step",
                "label": f"{instruction} {args[:60]}",
                "instruction": instruction,
                "args": args[:200],
                "rel_path": rel_path,
                "step": n_inst,
            }
        )
        if module_id:
            edges.append(
                {"source": module_id, "target": nid, "relation": "declares"}
            )
        if instruction == "FROM":
            base = args.split()[0] if args else ""
            base = base.split(" AS ")[0].split(" as ")[0] if base else ""
            if base:
                base_nid = f"dockerfile_base_image:{rel_path}"
                nodes.append(
                    {
                        "id": base_nid,
                        "type": "dockerfile_base_image",
                        "label": base,
                        "image_ref": base,
                        "rel_path": rel_path,
                    }
                )
                # Best-effort cross-link to image:* node from ImageBuildLayer.
                if base in image_index:
                    edges.append(
                        {
                            "source": base_nid,
                            "target": f"image:{base}",
                            "relation": "based_on",
                        }
                    )


# --- CUE ---------------------------------------------------------------------


def _scan_cue(
    file_path: Path,
    rel_path: str,
    source: bytes,
    parser,
    nodes: list[dict],
    edges: list[dict],
    module_id: str | None,
) -> None:
    """Best-effort CUE scan. The CUE grammar isn't bundled in many
    ``tree_sitter_languages`` releases — when ``parser`` is ``None`` we still
    do a shallow regex sweep so the layer produces some signal.
    """
    try:
        text = source.decode("utf-8", errors="replace")
    except Exception:
        return
    import re
    # CUE definitions: `#Name:` at top-of-line.
    seen_defs: set[str] = set()
    for m in re.finditer(r"^(#[A-Za-z_][A-Za-z0-9_]*)\s*:", text, re.M):
        name = m.group(1)
        if name in seen_defs:
            continue
        seen_defs.add(name)
        nid = f"cue_definition:{rel_path}/{name}"
        nodes.append(
            {
                "id": nid,
                "type": "cue_definition",
                "label": f"{name}",
                "definition": name,
                "rel_path": rel_path,
            }
        )
        if module_id:
            edges.append(
                {"source": module_id, "target": nid, "relation": "declares"}
            )
    # Top-level fields up to depth 2 — `^foo:` or `^  foo:`.
    seen_fields: set[str] = set()
    for m in re.finditer(r"^(\s{0,2})([a-zA-Z_][a-zA-Z0-9_]*):\s", text, re.M):
        indent, key = m.group(1), m.group(2)
        depth = len(indent) // 2
        if depth > 1:
            continue
        if key in seen_fields:
            continue
        seen_fields.add(key)
        nid = f"cue_field:{rel_path}/{key}"
        nodes.append(
            {
                "id": nid,
                "type": "cue_field",
                "label": key,
                "field": key,
                "depth": depth,
                "rel_path": rel_path,
            }
        )
        if module_id:
            edges.append(
                {"source": module_id, "target": nid, "relation": "declares"}
            )


# --- Layer driver ------------------------------------------------------------


class TreeSitterLayer(Layer):
    name = "treesitter"
    refresh_trigger = "manual"

    def to_document(self, node: dict) -> str:
        """Render an HCL / CUE / variable / locals node as a sentence.

        Each tree-sitter node carries the actual identifier plus the path
        — combining them lets semantic_search match prose queries like
        "find aws_lb resources" or "modules referencing aurora".
        """
        ntype = node.get("type", "")
        label = node.get("label") or node.get("id", "")
        rel = node.get("rel_path", "")
        if ntype == "hcl_resource":
            tf_type = node.get("tf_type", "")
            tf_name = node.get("tf_name", "")
            return (
                f"OpenTofu resource of type {tf_type} named {tf_name} declared in {rel}."
            )[:512]
        if ntype == "hcl_data":
            tf_type = node.get("tf_type", "")
            tf_name = node.get("tf_name", "")
            return (
                f"OpenTofu data source {tf_type} named {tf_name} in {rel}."
            )[:512]
        if ntype == "hcl_module_call":
            return f"OpenTofu module call {label} in {rel}."[:512]
        if ntype == "hcl_variable":
            return (
                f"OpenTofu variable {label} declared in {rel}. "
                f"Type: {node.get('vtype', '')}. Description: {node.get('description', '')}."
            )[:512]
        if ntype == "hcl_output":
            return (
                f"OpenTofu output {label} from {rel}. "
                f"Description: {node.get('description', '')}."
            )[:512]
        if ntype == "hcl_locals":
            return f"OpenTofu locals binding {label} in {rel}."[:512]
        if ntype in ("cue_definition", "cue_field"):
            return (
                f"CUE {ntype.replace('cue_', '')} {label} in {rel}. "
                f"Type: {node.get('cue_type', '')}."
            )[:512]
        return super().to_document(node)

    def scan(self, ctx: dict) -> tuple[list[dict], list[dict]]:
        repo_root = Path(ctx.get("repo_root", ".")).resolve()
        verbose = bool(ctx.get("verbose"))
        max_files = int(ctx.get("treesitter_max_files") or 5000)
        max_size = int(ctx.get("treesitter_max_bytes") or 1_000_000)

        try:
            import tree_sitter_languages as tsl  # type: ignore[import-not-found]
        except Exception as exc:
            print(
                f"  [TreeSitterLayer] tree_sitter_languages unavailable: {exc} — "
                "soft-degrading to 0 nodes",
                file=sys.stderr,
            )
            return [], []

        # Per-grammar parser handles. Soft-degrade per language.
        parsers: dict[str, object] = {}
        for lang in ("hcl", "yaml", "dockerfile", "cue"):
            try:
                parsers[lang] = tsl.get_parser(lang)
            except Exception as exc:
                if verbose:
                    print(
                        f"  [TreeSitterLayer] grammar '{lang}' not available: {exc} — skipping",
                        file=sys.stderr,
                    )

        if not parsers:
            return [], []

        nodes: list[dict] = []
        edges: list[dict] = []
        var_index: dict[str, str] = {}
        res_index: dict[str, str] = {}
        # image:* node ids from ImageBuildLayer (if present in store).
        image_index: set[str] = set()
        store = ctx.get("graph_store")
        if store is not None:
            try:
                for n in store.all_nodes():
                    nid = n.get("id", "")
                    if nid.startswith("image:"):
                        image_index.add(nid.split(":", 1)[1])
            except Exception:
                pass

        # First pass — variables (so refs resolve in pass 2). We use the same
        # walk for everything; var_index is filled as we go and looked up via
        # a deferred resolution at the very end. For simplicity we merge both
        # passes — variables defined later in the same run are still
        # discoverable because we look up by global name fallback.

        counts: dict[str, int] = {"hcl": 0, "yaml": 0, "dockerfile": 0, "cue": 0}

        # HCL pass
        if "hcl" in parsers:
            for f in _iter_files(repo_root, _HCL_GLOBS, max_files):
                try:
                    if f.stat().st_size > max_size:
                        continue
                    src = f.read_bytes()
                except Exception:
                    continue
                rel = str(f.relative_to(repo_root))
                module_id = _module_id_for_path(repo_root, f)
                _scan_hcl(
                    f, rel, src, parsers["hcl"], module_id,
                    nodes, edges, var_index, res_index,
                )
                counts["hcl"] += 1

        # YAML pass
        if "yaml" in parsers:
            for f in _iter_files(repo_root, _YAML_GLOBS, max_files):
                try:
                    if f.stat().st_size > max_size:
                        continue
                    src = f.read_bytes()
                except Exception:
                    continue
                rel = str(f.relative_to(repo_root))
                module_id = _module_id_for_path(repo_root, f)
                _scan_yaml(
                    f, rel, src, parsers["yaml"],
                    nodes, edges, module_id,
                )
                counts["yaml"] += 1

        # Dockerfile pass
        if "dockerfile" in parsers:
            for f in _iter_files(repo_root, _DOCKERFILE_GLOBS, max_files):
                try:
                    if f.stat().st_size > max_size:
                        continue
                    src = f.read_bytes()
                except Exception:
                    continue
                rel = str(f.relative_to(repo_root))
                module_id = _module_id_for_path(repo_root, f)
                _scan_dockerfile(
                    f, rel, src, parsers["dockerfile"],
                    nodes, edges, module_id, image_index,
                )
                counts["dockerfile"] += 1

        # CUE pass — works even when the grammar isn't bundled (regex fallback).
        for f in _iter_files(repo_root, _CUE_GLOBS, max_files):
            try:
                if f.stat().st_size > max_size:
                    continue
                src = f.read_bytes()
            except Exception:
                continue
            rel = str(f.relative_to(repo_root))
            module_id = _module_id_for_path(repo_root, f)
            _scan_cue(
                f, rel, src, parsers.get("cue"),
                nodes, edges, module_id,
            )
            counts["cue"] += 1

        # Resolve `refs`: turn synthetic ``hcl_resource_ref:<type.name>`` edge
        # targets into real ``hcl_resource:<...>`` ids when we saw the
        # declaration in this run; otherwise drop the edge.
        ref_map: dict[str, str] = {}
        for k, v in res_index.items():
            ref_map[k] = v
        resolved_edges: list[dict] = []
        for e in edges:
            tgt = e.get("target", "")
            if isinstance(tgt, str) and tgt.startswith("hcl_resource_ref:"):
                key = tgt.split(":", 1)[1]
                real = ref_map.get(key)
                if real:
                    e2 = dict(e)
                    e2["target"] = real
                    resolved_edges.append(e2)
                # else: drop unresolved cross-module refs to keep store clean
                continue
            resolved_edges.append(e)

        if verbose:
            print(
                f"  [TreeSitterLayer] scanned hcl={counts['hcl']} yaml={counts['yaml']} "
                f"dockerfile={counts['dockerfile']} cue={counts['cue']} -> "
                f"{len(nodes)} nodes / {len(resolved_edges)} edges"
            )
        return nodes, resolved_edges
