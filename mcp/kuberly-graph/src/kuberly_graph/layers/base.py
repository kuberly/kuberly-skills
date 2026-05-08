"""Layer base — duck-typed; subclasses override `scan(ctx)`."""

from __future__ import annotations


class Layer:
    name: str = "base"
    refresh_trigger: str = "manual"

    def scan(self, ctx: dict) -> tuple[list[dict], list[dict]]:
        return [], []
