"""kuberly-platform MCP transport (official `mcp` SDK stdio server).

Tool schemas live in `manifest`; graph calls in `dispatch`. The CLI entrypoint
(`kuberly_platform.py mcp`) injects render + telemetry callables so this
package never imports the `__main__` module.
"""

from kuberly_mcp.stdio_app import run_stdio_server_blocking

__all__ = ["run_stdio_server_blocking"]
