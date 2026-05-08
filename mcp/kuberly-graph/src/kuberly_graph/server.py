"""Single FastMCP app instance shared by every tool module.

The runtime configuration (repo root, persist dir) is held in
`SERVER_CONFIG` so tool functions don't need to receive it as a hidden
parameter.
"""

from __future__ import annotations

import os
from typing import Any

from mcp.server.fastmcp import FastMCP


SERVER_CONFIG: dict[str, Any] = {
    "repo_root": os.environ.get("KUBERLY_REPO", "."),
    "persist_dir": os.environ.get("KUBERLY_PERSIST_DIR", ".kuberly"),
}


mcp = FastMCP("kuberly-platform")


def configure(repo_root: str | None = None, persist_dir: str | None = None) -> None:
    """Update the global server config — called by `cli.serve()` before run."""
    if repo_root is not None:
        SERVER_CONFIG["repo_root"] = repo_root
    if persist_dir is not None:
        SERVER_CONFIG["persist_dir"] = persist_dir


def register_tools() -> None:
    """Import the tool modules so @mcp.tool() decorators register with `mcp`."""
    from . import tools  # noqa: F401


register_tools()
