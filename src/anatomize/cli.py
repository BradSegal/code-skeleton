"""Public command-line entry point for Anatomize."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from anatomize.review.cli import review_app

app = typer.Typer(
    name="anatomize",
    help="Evidence-first repository review and agent lifecycle support.",
    add_completion=False,
    rich_markup_mode=None,
    pretty_exceptions_enable=False,
)
app.add_typer(review_app, name="review")


def version_callback(value: bool) -> None:
    """Print the installed release and exit."""
    if value:
        from anatomize.version import __version__

        typer.echo(f"anatomize {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool | None,
        typer.Option("--version", "-V", help="Show version and exit.", callback=version_callback, is_eager=True),
    ] = None,
) -> None:
    """Evidence-first repository review and agent lifecycle support."""


@app.command("mcp")
def mcp_server_command(
    root: Annotated[Path, typer.Argument(help="Repository root exposed read-only through MCP.")] = Path("."),
    repository_id: Annotated[
        str | None,
        typer.Option("--repository-id", help="Stable identity for the source-bound session."),
    ] = None,
    provider: Annotated[
        list[Path],
        typer.Option("--provider", help="Server-operator-selected artifact-import provider envelope."),
    ] = [],
    source: Annotated[
        list[str],
        typer.Option("--include-source", help="Exact source path the server may return; omitted by default."),
    ] = [],
    transport: Annotated[str, typer.Option("--transport", help="MCP transport: stdio or streamable-http.")] = "stdio",
    host: Annotated[str, typer.Option("--host", help="Loopback bind address for streamable HTTP.")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", help="Streamable HTTP port.")] = 8000,
    timeout: Annotated[float, typer.Option("--timeout", help="Per-operation timeout in seconds, at most 300.")] = 30.0,
    max_input_bytes: Annotated[
        int,
        typer.Option("--max-input-bytes", help="Maximum canonical tool-input size."),
    ] = 4 * 1024 * 1024,
    max_result_bytes: Annotated[
        int,
        typer.Option("--max-result-bytes", help="Maximum canonical tool-result size."),
    ] = 4 * 1024 * 1024,
) -> None:
    """Run the optional read-only MCP server (install with anatomize[mcp])."""
    try:
        from anatomize.mcp_transport import MCPReviewService, run_mcp_server

        service = MCPReviewService(
            root,
            repository_id=repository_id,
            provider_paths=provider,
            source_paths=source,
            timeout_seconds=timeout,
            max_input_bytes=max_input_bytes,
            max_result_bytes=max_result_bytes,
        )
        run_mcp_server(service, transport=transport, host=host, port=port)
    except (ImportError, ValueError, OSError) as error:
        _failure(error)


def _failure(error: Exception) -> None:
    code = str(getattr(error, "code", "review_invalid_input"))
    remediation = str(
        getattr(error, "remediation", "Inspect command help and regenerate exact current-state artifacts.")
    )
    exit_code = int(getattr(error, "exit_code", 2))
    typer.echo(f"error[{code}]: {error}", err=True)
    typer.echo(f"remediation: {remediation}", err=True)
    raise typer.Exit(exit_code)


if __name__ == "__main__":
    app()
