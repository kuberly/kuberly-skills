"""kuberly-graphs — friendly alias for `kuberly-platform call regenerate_all`.

The full CLI (`kuberly-platform`) exposes serve / call / version, which is the
right shape for an MCP server but a confusing UX for "I just want to
refresh the graph". This file ships a small companion entrypoint with one
subcommand — `kuberly-graphs refresh` — that consumers can run after
`apm install` without remembering the `call regenerate_all` incantation.

It's a wrapper, not a fork: every flag is passed through to the existing
`_cmd_call` so behaviour stays identical.
"""

from __future__ import annotations

import argparse
import os
import sys

from .cli import _cmd_call


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="kuberly-graphs",
        description=(
            "Refresh the kuberly knowledge graph (LanceDB store at "
            "<repo>/.kuberly/lance/). Wraps `kuberly-platform call regenerate_all`."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_refresh = sub.add_parser(
        "refresh",
        help="Refresh every layer of the graph (calls regenerate_all)",
    )
    p_refresh.add_argument(
        "--repo",
        default=os.environ.get("KUBERLY_REPO", "."),
        help="Repository root the producers walk (default: cwd / KUBERLY_REPO)",
    )
    p_refresh.add_argument(
        "--persist-dir",
        default=os.environ.get("KUBERLY_PERSIST_DIR", ".kuberly"),
        help="LanceDB persist dir (default: .kuberly / KUBERLY_PERSIST_DIR)",
    )

    args = parser.parse_args(argv)

    if args.command == "refresh":
        ns = argparse.Namespace(
            tool="regenerate_all",
            args=None,
            repo=args.repo,
            persist_dir=args.persist_dir,
        )
        return _cmd_call(ns)

    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
