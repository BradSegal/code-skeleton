from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

mcp = pytest.importorskip("mcp")

from mcp import Client  # noqa: E402
from mcp.client.stdio import StdioServerParameters, stdio_client  # noqa: E402
from mcp.types import CallToolResult  # noqa: E402

from anatomize.mcp_transport import MCPReviewService, create_mcp_server  # noqa: E402
from anatomize.review import DossierExchange, ReviewApplication  # noqa: E402

pytestmark = pytest.mark.integration


def _repository(root: Path) -> Path:
    (root / "src").mkdir(parents=True)
    (root / "src" / "core.py").write_text("def answer() -> int:\n    return 42\n", encoding="utf-8")
    (root / "README.md").write_text("# MCP client fixture\n", encoding="utf-8")
    return root


def test_generic_client_completes_progressive_dossier_journey(tmp_path: Path) -> None:
    root = _repository(tmp_path / "repository")
    service = MCPReviewService(root, repository_id="repository:mcp-client")
    server = create_mcp_server(service)

    async def journey() -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
        async with Client(server) as client:
            tools = await client.list_tools()
            assert {item.name for item in tools.tools} == {
                "anatomize_capabilities",
                "anatomize_refresh",
                "anatomize_session",
                "anatomize_dossier",
                "anatomize_expand",
                "anatomize_check",
            }
            assert all(item.annotations is not None and item.annotations.read_only_hint for item in tools.tools)
            capability_result = await client.call_tool("anatomize_capabilities", {})
            session_result = await client.call_tool("anatomize_session", {})
            dossier_result = await client.call_tool("anatomize_dossier", {"profile": "orientation"})
            assert not capability_result.is_error
            assert not session_result.is_error
            assert not dossier_result.is_error
            assert capability_result.structured_content is not None
            assert session_result.structured_content is not None
            assert dossier_result.structured_content is not None
            base = DossierExchange.model_validate(dossier_result.structured_content)
            expansion_result = await client.call_tool(
                "anatomize_expand",
                {
                    "exchange": dossier_result.structured_content,
                    "action_id": base.dossier.expansions[0].action_id,
                },
            )
            assert not expansion_result.is_error
            assert expansion_result.structured_content is not None
            check_result = await client.call_tool(
                "anatomize_check",
                {"artifact": dossier_result.structured_content},
            )
            assert not check_result.is_error
            assert check_result.structured_content is not None
            resource = await client.read_resource("anatomize://capabilities")
            assert len(resource.contents) == 1
            resource_text = getattr(resource.contents[0], "text", "")
            assert json.loads(resource_text)["transport_api_version"] == "1.0.0"
            return (
                dossier_result.structured_content,
                expansion_result.structured_content,
                check_result.structured_content,
            )

    dossier_payload, expansion_payload, check_payload = asyncio.run(journey())
    exchange = DossierExchange.model_validate(dossier_payload)
    expanded = DossierExchange.model_validate(expansion_payload)
    direct_bundle = ReviewApplication().start(root, repository_id="repository:mcp-client")
    direct = ReviewApplication().dossier(direct_bundle, profile=exchange.request.profile)

    assert exchange == direct
    assert expanded.dossier.base_dossier_id == exchange.dossier.dossier_id
    assert check_payload["identity"] == exchange.exchange_id


def test_server_tool_errors_are_bounded_and_actionable(tmp_path: Path) -> None:
    root = _repository(tmp_path / "repository")
    server = create_mcp_server(MCPReviewService(root))

    async def invalid() -> CallToolResult:
        async with Client(server) as client:
            return await client.call_tool("anatomize_dossier", {"profile": "not-a-profile"})

    result = asyncio.run(invalid())
    assert result.is_error
    rendered = " ".join(getattr(item, "text", "") for item in result.content)
    assert "error[mcp_dossier_invalid]" in rendered
    assert "remediation:" in rendered
    assert str(root.resolve()) not in rendered


def test_generic_client_completes_real_stdio_transport_lifecycle(tmp_path: Path) -> None:
    root = _repository(tmp_path / "repository")
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "anatomize.cli",
            "mcp",
            str(root),
            "--repository-id",
            "repository:mcp-stdio",
        ],
        cwd=Path(__file__).resolve().parents[2],
    )

    async def journey() -> tuple[bool, str]:
        async with Client(stdio_client(parameters)) as client:
            capabilities = await client.call_tool("anatomize_capabilities", {})
            dossier = await client.call_tool("anatomize_dossier", {"profile": "orientation"})
            assert capabilities.structured_content is not None
            assert dossier.structured_content is not None
            return dossier.is_error, str(dossier.structured_content["dossier"]["status"])

    is_error, status = asyncio.run(journey())
    assert is_error is False
    assert status == "complete"
