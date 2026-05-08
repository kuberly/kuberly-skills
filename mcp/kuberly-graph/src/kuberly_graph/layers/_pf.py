"""Shared kubectl port-forward helper for observability layers.

v0.51.0: metrics/logs/traces all soft-fall-back to ``kubectl port-forward``
when the upstream MCP wrapper has no Prom/Loki/Tempo URL wired. Subprocesses
are owned by a ``contextlib.contextmanager`` so cleanup is guaranteed even
on exception. All callers go through this single helper to keep the
cleanup invariant in one place.
"""

from __future__ import annotations

import contextlib
import json as _json
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


def os_read_nonblock(stream, n: int = 4096) -> bytes:
    """Best-effort non-blocking read from a Popen pipe. ``stream`` may be a
    BufferedReader (when ``bufsize=0`` is set, it's actually a FileIO); fall
    back to ``read1`` / a small ``read`` if no fd is exposed.
    """
    import os as _os

    fd = None
    try:
        fd = stream.fileno()
    except Exception:
        fd = None
    if fd is not None:
        try:
            return _os.read(fd, n)
        except BlockingIOError:
            return b""
        except Exception:
            return b""
    # Fallback: small read1 call (raw IOBase). Returns immediately with
    # whatever's available.
    try:
        if hasattr(stream, "read1"):
            return stream.read1(n)
        return stream.read(1)
    except Exception:
        return b""


def _have_kubectl() -> bool:
    return bool(shutil.which("kubectl"))


def _have_current_context() -> bool:
    bin_path = shutil.which("kubectl")
    if not bin_path:
        return False
    try:
        r = subprocess.run(
            [bin_path, "config", "current-context"],
            capture_output=True,
            text=True,
            timeout=4,
        )
        return r.returncode == 0 and bool(r.stdout.strip())
    except Exception:
        return False


@contextlib.contextmanager
def kubectl_port_forward(
    namespace: str,
    service: str,
    remote_port: int,
    *,
    timeout_s: float = 60.0,
):
    """Spin up ``kubectl port-forward -n <ns> svc/<svc> 0:<remote_port>``.

    Yields the local port the kernel allocated. Kills the subprocess on
    context exit (try/finally — guaranteed even on exception). Raises
    ``RuntimeError`` if kubectl isn't on PATH, no current-context, or the
    port-forward fails to bind within ``timeout_s``.

    Note: kubectl writes "Forwarding from 127.0.0.1:<P> -> <remote_port>" to
    **stdout** (not stderr). On AWS clusters with SSO/IAM token refresh the
    first call can take ~10–15 s before printing — default timeout is 30 s.
    """
    bin_path = shutil.which("kubectl")
    if not bin_path:
        raise RuntimeError("kubectl not found on PATH")
    if not _have_current_context():
        raise RuntimeError("kubectl: no current-context configured")
    cmd = [
        bin_path,
        "port-forward",
        "-n",
        namespace,
        f"svc/{service}",
        f"0:{int(remote_port)}",
    ]
    # Use unbuffered stdout/stderr so we see the "Forwarding from..." line
    # immediately. ``bufsize=0`` + binary mode is the most portable; we
    # decode at line read time.
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
    )
    local_port = 0
    deadline = time.monotonic() + timeout_s
    out_buf = b""
    err_buf = b""
    try:
        try:
            import select as _sel
        except Exception:
            _sel = None
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                # Read whatever is left in the pipes.
                try:
                    if proc.stdout is not None:
                        out_buf += proc.stdout.read() or b""
                    if proc.stderr is not None:
                        err_buf += proc.stderr.read() or b""
                except Exception:
                    pass
                raise RuntimeError(
                    f"kubectl port-forward exited early: "
                    f"stdout={out_buf.decode('utf-8', errors='replace')[:200]} "
                    f"stderr={err_buf.decode('utf-8', errors='replace')[:200]}"
                )
            chunk = b""
            if _sel is not None:
                try:
                    r, _, _ = _sel.select([proc.stdout, proc.stderr], [], [], 0.5)
                    for stream in r:
                        try:
                            data = os_read_nonblock(stream)
                        except Exception:
                            data = b""
                        if not data:
                            continue
                        if stream is proc.stdout:
                            out_buf += data
                        else:
                            err_buf += data
                        chunk = data
                except Exception:
                    pass
            else:
                # Fallback: blocking line-read on stdout.
                try:
                    line = proc.stdout.readline() if proc.stdout else b""
                except Exception:
                    line = b""
                if line:
                    out_buf += line
                    chunk = line
            # Look for "127.0.0.1:<port>" in the accumulated stdout buffer.
            text = out_buf.decode("utf-8", errors="replace")
            idx = text.find("127.0.0.1:")
            if idx >= 0:
                tail = text[idx + len("127.0.0.1:") :]
                ds = ""
                for ch in tail:
                    if ch.isdigit():
                        ds += ch
                    else:
                        break
                if ds.isdigit():
                    local_port = int(ds)
                    break
            if not chunk:
                # Avoid a tight CPU spin when select returns nothing.
                time.sleep(0.05)
        if not local_port:
            raise RuntimeError(
                "kubectl port-forward did not announce a local port within "
                f"{timeout_s}s; "
                f"stdout={out_buf.decode('utf-8', errors='replace')[:200]} "
                f"stderr={err_buf.decode('utf-8', errors='replace')[:200]}"
            )
        # Belt-and-braces: confirm the port actually accepts connections.
        for _ in range(10):
            try:
                with socket.create_connection(
                    ("127.0.0.1", local_port), timeout=0.5
                ):
                    break
            except Exception:
                time.sleep(0.2)
        yield local_port
    finally:
        try:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except Exception:
                proc.kill()
                try:
                    proc.wait(timeout=2)
                except Exception:
                    pass
        except Exception:
            pass
        # Drain any remaining stdout/stderr so the file descriptors close
        # cleanly. Without this you can occasionally leave the kubectl
        # subprocess in a half-zombie state on macOS.
        for stream in (proc.stdout, proc.stderr):
            try:
                if stream is not None:
                    stream.close()
            except Exception:
                pass


def http_get_json(
    url: str,
    *,
    timeout_s: float = 12.0,
    headers: dict | None = None,
) -> dict | list | None:
    """GET ``url`` and JSON-decode the body; return ``None`` on any failure."""
    hdrs = {"Accept": "application/json"}
    if headers:
        hdrs.update(headers)
    try:
        req = urllib.request.Request(url, headers=hdrs)
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            body = resp.read().decode("utf-8", errors="replace")
        try:
            return _json.loads(body)
        except Exception:
            return None
    except (urllib.error.URLError, urllib.error.HTTPError, OSError):
        return None
    except Exception:
        return None


def http_get_json_qs(
    base_url: str,
    path: str,
    params: dict | None = None,
    *,
    timeout_s: float = 12.0,
    headers: dict | None = None,
) -> dict | list | None:
    qs = ""
    if params:
        qs = "?" + urllib.parse.urlencode(
            {k: v for k, v in params.items() if v is not None}, doseq=True
        )
    return http_get_json(
        base_url.rstrip("/") + path + qs,
        timeout_s=timeout_s,
        headers=headers,
    )


def warn(msg: str) -> None:
    print(msg, file=sys.stderr)


__all__ = [
    "_have_kubectl",
    "_have_current_context",
    "kubectl_port_forward",
    "http_get_json",
    "http_get_json_qs",
    "warn",
]
