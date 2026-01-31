"""Pyright language-server integration for usage slicing.

This module intentionally has *no* external dependencies beyond the standard
library. It speaks JSON-RPC/LSP over stdio to `pyright-langserver --stdio`.
"""

from __future__ import annotations

import json
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from queue import Queue
from time import sleep
from typing import Any
from urllib.parse import unquote, urlparse


@dataclass(frozen=True)
class LspPosition:
    line: int
    character: int


def pyright_referenced_files(
    *,
    root: Path,
    target_file: Path,
    positions: list[LspPosition],
    langserver_cmd: list[str],
    python_roots: list[Path],
    workspace_files: list[Path] | None = None,
) -> set[Path]:
    """Return files that reference symbols at the given positions.

    This uses `textDocument/references` for each position and unions the result.
    """
    client = _LspClient(langserver_cmd, cwd=root)
    try:
        client.start()
        root_uri = _path_to_uri(root)
        target_uri = _path_to_uri(target_file)

        extra_paths: list[str] = []
        for pr in python_roots:
            pr = pr.resolve()
            try:
                rel = pr.relative_to(root)
                extra_paths.append(rel.as_posix())
            except ValueError:
                extra_paths.append(pr.as_posix())
        extra_paths = sorted(set(extra_paths))

        py_settings = {
            "python": {
                "analysis": {
                    "extraPaths": extra_paths,
                    "diagnosticMode": "workspace",
                }
            }
        }

        client.request(
            "initialize",
            {
                "processId": None,
                "rootUri": root_uri,
                "initializationOptions": py_settings,
                "capabilities": {},
                "workspaceFolders": [{"uri": root_uri, "name": root.name}],
            },
        )
        client.notify("initialized", {})

        # Ensure workspace-wide analysis (needed for cross-file references) and make
        # `src/` style layouts import-resolvable via extraPaths.
        client.notify("workspace/didChangeConfiguration", {"settings": py_settings})

        to_open = workspace_files if workspace_files is not None else [target_file]
        opened = 0
        for doc_path in sorted({pp.resolve() for pp in to_open}):
            try:
                doc_path.relative_to(root)
            except ValueError:
                continue
            if doc_path.suffix != ".py":
                continue
            try:
                text = doc_path.read_text(encoding="utf-8")
            except Exception:
                continue
            client.notify(
                "textDocument/didOpen",
                {
                    "textDocument": {
                        "uri": _path_to_uri(doc_path),
                        "languageId": "python",
                        "version": 1,
                        "text": text,
                    }
                },
            )
            opened += 1

        if opened == 0:
            raise ValueError("pyright-lsp did not open any workspace files")

        # Warm up analysis deterministically before requesting references.
        try:
            client.request(
                "textDocument/documentSymbol",
                {"textDocument": {"uri": target_uri}},
            )
        except Exception:
            # Some pyright versions may not support this; references are still the source of truth.
            pass

        files: set[Path] = set()
        warmed = False
        for pos in positions:
            params = {
                "textDocument": {"uri": target_uri},
                "position": {"line": pos.line, "character": pos.character},
                "context": {"includeDeclaration": False},
            }

            res = client.request("textDocument/references", params)
            if not res:
                # Pyright may require a short warm-up for workspace analysis.
                # Retry deterministically up to a small bound, but only once
                # to avoid O(symbols) sleeps for large modules.
                if not warmed:
                    for _ in range(20):
                        sleep(0.25)
                        res = client.request("textDocument/references", params)
                        if res:
                            break
                    warmed = True
            if not res:
                continue
            for loc in res:
                uri = loc.get("uri")
                if not isinstance(uri, str):
                    continue
                p = _uri_to_path(uri)
                if p is None:
                    continue
                try:
                    p.relative_to(root)
                except ValueError:
                    continue
                files.add(p.resolve())
        return files
    finally:
        client.shutdown()


def _path_to_uri(path: Path) -> str:
    return path.resolve().as_uri()


def _uri_to_path(uri: str) -> Path | None:
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        return None

    netloc = parsed.netloc
    path = unquote(parsed.path)

    # file:///C:/... -> C:/... on Windows-style URIs.
    if path.startswith("/") and len(path) >= 3 and path[2] == ":":
        path = path[1:]

    # UNC: file://server/share/...
    if netloc and netloc != "localhost":
        path = f"//{netloc}{path}"

    try:
        return Path(path).resolve()
    except Exception:
        return None


class _LspClient:
    def __init__(self, cmd: list[str], *, cwd: Path, timeout_seconds: int = 30) -> None:
        self._cmd = cmd
        self._cwd = cwd
        self._proc: subprocess.Popen[bytes] | None = None
        self._next_id = 1
        self._responses: dict[int, Queue[dict[str, Any]]] = {}
        self._responses_lock = threading.Lock()
        self._reader_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._timeout_seconds = timeout_seconds

    def start(self) -> None:
        if "--stdio" not in self._cmd:
            raise ValueError("pyright language server command must include `--stdio`")

        try:
            proc = subprocess.Popen(
                self._cmd,
                cwd=self._cwd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except FileNotFoundError as e:
            raise ValueError(
                f"Pyright language server not found: {' '.join(self._cmd)}. "
                "Install Pyright (e.g. `npm i -g pyright`) or pass --pyright-langserver-cmd."
            ) from e

        if proc.stdin is None or proc.stdout is None:
            raise ValueError("Failed to start pyright language server (missing stdio)")
        self._proc = proc

        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader_thread.start()

        if proc.stderr is not None:
            self._stderr_thread = threading.Thread(target=self._drain_stderr, daemon=True)
            self._stderr_thread.start()

        # If the server exits immediately (e.g. invoked without `--stdio`), fail fast
        # with a clear error instead of timing out on initialize.
        sleep(0.2)
        if proc.poll() is not None:
            raise ValueError("Pyright language server exited immediately; ensure the command includes `--stdio`")

    def shutdown(self) -> None:
        if self._proc is None:
            return
        try:
            try:
                self.request("shutdown", {})
            except Exception:
                pass
            try:
                self.notify("exit", {})
            except Exception:
                pass
        finally:
            try:
                self._proc.kill()
            except Exception:
                pass
            self._proc = None

    def request(self, method: str, params: dict[str, Any]) -> Any:
        req_id = self._next_id
        self._next_id += 1
        q: Queue[dict[str, Any]] = Queue(maxsize=1)
        with self._responses_lock:
            self._responses[req_id] = q

        self._send({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
        try:
            resp = q.get(timeout=self._timeout_seconds)
        except Exception as e:
            raise ValueError(f"pyright-lsp timed out waiting for response to {method}") from e
        if "error" in resp and resp["error"] is not None:
            raise ValueError(f"pyright-lsp error for {method}: {resp['error']}")
        return resp.get("result")

    def notify(self, method: str, params: dict[str, Any]) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _send(self, msg: dict[str, Any]) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise ValueError("pyright language server is not running")
        raw = json.dumps(msg, ensure_ascii=False).encode("utf-8")
        header = f"Content-Length: {len(raw)}\r\n\r\n".encode("ascii")
        try:
            self._proc.stdin.write(header + raw)
            self._proc.stdin.flush()
        except BrokenPipeError as e:
            raise ValueError("pyright language server connection closed") from e

    def _reader_loop(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        stream = self._proc.stdout
        while True:
            headers = _read_headers(stream)
            if headers is None:
                return
            length = int(headers.get("content-length", "0"))
            if length <= 0:
                continue
            body = stream.read(length)
            if not body:
                return
            try:
                msg = json.loads(body.decode("utf-8"))
            except Exception:
                continue
            if isinstance(msg, dict) and "id" in msg and isinstance(msg["id"], int):
                req_id = msg["id"]
                with self._responses_lock:
                    q = self._responses.get(req_id)
                if q is not None:
                    q.put(msg)

    def _drain_stderr(self) -> None:
        assert self._proc is not None and self._proc.stderr is not None
        try:
            while self._proc.stderr.read(4096):
                pass
        except Exception:
            pass


def _read_headers(stream: Any) -> dict[str, str] | None:
    headers: dict[str, str] = {}
    while True:
        line = stream.readline()
        if not line:
            return None
        if line in (b"\r\n", b"\n"):
            return headers
        try:
            key, value = line.decode("ascii", errors="ignore").split(":", 1)
        except ValueError:
            continue
        headers[key.strip().lower()] = value.strip()
