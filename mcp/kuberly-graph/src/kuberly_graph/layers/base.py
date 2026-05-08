"""Layer base — duck-typed; subclasses override `scan(ctx)`.

Subclasses can also override ``to_document(node) -> str`` to customise the
text that gets embedded into LanceDB for semantic search. The default
template is generic ("<type> <label> key=value …") — fine for layers whose
node shapes don't carry meaningful prose. Higher-value layers (code, k8s,
applications, hcl) emit richer per-node documents so semantic_search can
answer questions like "find services with sidecars" or "modules that own
RDS clusters".
"""

from __future__ import annotations

import json


class Layer:
    name: str = "base"
    refresh_trigger: str = "manual"

    def scan(self, ctx: dict) -> tuple[list[dict], list[dict]]:
        return [], []

    def to_document(self, node: dict) -> str:
        """Render a node into a single-paragraph "document" for embedding.

        Default = generic template. Override per-layer when node attributes
        carry semantic content worth surfacing in similarity search. Output
        is truncated to ~512 chars by the store; aim for a tight, single-
        sentence summary.
        """
        return _default_document(node)


def _default_document(node: dict) -> str:
    """Generic fallback: type + label + key attributes flattened to text."""
    parts: list[str] = []
    ntype = node.get("type", "")
    label = node.get("label") or node.get("id", "")
    parts.append(f"{ntype} {label}")
    layer = node.get("layer")
    if layer:
        parts.append(f"layer={layer}")
    skip = {"id", "type", "label", "layer", "_embedding_doc"}
    for k, v in node.items():
        if k in skip:
            continue
        if v is None or v == "" or v == [] or v == {}:
            continue
        if isinstance(v, (str, int, float, bool)):
            parts.append(f"{k}={v}")
        elif isinstance(v, list) and v and isinstance(v[0], (str, int, float, bool)):
            parts.append(f"{k}=[{','.join(str(x) for x in v[:5])}]")
        else:
            try:
                parts.append(f"{k}={json.dumps(v, default=str)[:80]}")
            except Exception:
                parts.append(f"{k}={str(v)[:80]}")
    return " ".join(parts)[:512]


# Re-export so individual layer files can compose default + custom docs.
default_document = _default_document
