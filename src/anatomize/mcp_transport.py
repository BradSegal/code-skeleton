"""Optional, read-only MCP transport over the public review application."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from anatomize._artifacts import canonical_ordered_json_bytes
from anatomize._errors import AnatomizeError
from anatomize.dossiers import (
    DossierBudget,
    DossierFilters,
    DossierProfile,
    QueryDirection,
    SourceSlicePolicy,
    TargetSelector,
)
from anatomize.providers import ProviderEnvelope, load_provider_envelope
from anatomize.review import DossierExchange, ReviewApplication, ReviewApplicationError, review_capabilities
from anatomize.sessions import ReviewSessionBundle
from anatomize.version import __version__

MCP_TRANSPORT_API_VERSION = "1.0.0"
DEFAULT_MCP_TIMEOUT_SECONDS = 30.0
DEFAULT_MCP_MAX_INPUT_BYTES = 4 * 1024 * 1024
DEFAULT_MCP_MAX_RESULT_BYTES = 4 * 1024 * 1024

logger = logging.getLogger(__name__)
_ResultT = TypeVar("_ResultT")


class MCPReviewError(AnatomizeError):
    """Stable client-safe transport failure with no private-path disclosure."""

    def __str__(self) -> str:
        return f"error[{self.code}]: {super().__str__()}; remediation: {self.remediation}"


class MCPReviewService:
    """Concurrency-safe transport adapter; all semantics remain in ReviewApplication."""

    def __init__(
        self,
        root: Path,
        *,
        repository_id: str | None = None,
        provider_paths: Iterable[Path] = (),
        source_paths: Iterable[str] = (),
        timeout_seconds: float = DEFAULT_MCP_TIMEOUT_SECONDS,
        max_input_bytes: int = DEFAULT_MCP_MAX_INPUT_BYTES,
        max_result_bytes: int = DEFAULT_MCP_MAX_RESULT_BYTES,
        application: ReviewApplication | None = None,
    ) -> None:
        if timeout_seconds <= 0 or timeout_seconds > 300:
            raise ValueError("MCP timeout must be in (0, 300] seconds")
        if min(max_input_bytes, max_result_bytes) < 4_096:
            raise ValueError("MCP input and result limits must be at least 4096 bytes")
        resolved = root.resolve()
        if not resolved.exists() or not resolved.is_dir():
            raise ValueError("MCP repository root must be an existing directory")
        self._root = resolved
        self._repository_id = repository_id
        self._provider_paths = tuple(path.resolve() for path in provider_paths)
        self._source_paths = tuple(sorted(set(source_paths)))
        self._timeout_seconds = timeout_seconds
        self._max_input_bytes = max_input_bytes
        self._max_result_bytes = max_result_bytes
        self._application = application or ReviewApplication()
        self._bundle: ReviewSessionBundle | None = None
        self._bundle_lock = asyncio.Lock()

    def capabilities(self) -> dict[str, Any]:
        """Advertise protocol, service mapping, limits, and trust boundaries."""
        return self._bounded_result({
            "transport": "anatomize.mcp",
            "transport_api_version": MCP_TRANSPORT_API_VERSION,
            "anatomize_version": __version__,
            "review": review_capabilities(),
            "tools": {
                "anatomize_capabilities": "capabilities",
                "anatomize_session": "start",
                "anatomize_refresh": "refresh",
                "anatomize_dossier": "dossier",
                "anatomize_expand": "expand",
                "anatomize_check": "check",
            },
            "resources": ["anatomize://capabilities", "anatomize://session"],
            "limits": {
                "timeout_seconds": self._timeout_seconds,
                "max_input_bytes": self._max_input_bytes,
                "max_result_bytes": self._max_result_bytes,
            },
            "authority": {
                "read_only": True,
                "repository_mutation": False,
                "optional_provider_invocation": False,
                "provider_input": "server-operator-selected artifact paths only",
                "source_content": "content-free unless server operator selected exact source paths",
                "network": "transport only; review application performs no network access",
            },
        })

    async def session(self) -> dict[str, Any]:
        """Return safe session metadata while retaining bounded bundle bytes server-side."""
        bundle = await self._session_bundle()
        return self._bounded_result(
            {
                "artifact_type": "anatomize.mcp-session",
                "schema_version": MCP_TRANSPORT_API_VERSION,
                "bundle_id": bundle.bundle_id,
                "manifest": bundle.manifest.model_dump(mode="json"),
                "portable_bundle_included": False,
                "portable_bundle_omission": (
                    "Raw session blobs remain server-side; use the CLI or Python API for portable export."
                ),
            }
        )

    async def refresh(self) -> dict[str, Any]:
        """Rebuild and atomically swap the server-side source-bound session."""
        async with self._bundle_lock:
            previous = self._bundle
            try:
                providers = await self._run(self._load_providers)
                candidate = await self._run(
                    lambda: self._application.start(
                        self._root,
                        repository_id=self._repository_id,
                        provider_envelopes=providers,
                        source_paths=self._source_paths,
                    )
                )
            except Exception as error:
                raise self._public_error(error, operation="refresh") from error
            self._bundle = candidate
        return self._bounded_result(
            {
                "artifact_type": "anatomize.mcp-refresh",
                "schema_version": MCP_TRANSPORT_API_VERSION,
                "changed": previous is None or previous.bundle_id != candidate.bundle_id,
                "previous_bundle_id": previous.bundle_id if previous is not None else None,
                "bundle_id": candidate.bundle_id,
                "source_state_ids": [
                    item.source_state.state_id for item in candidate.manifest.source_states
                ],
            }
        )

    async def dossier(
        self,
        *,
        profile: str,
        targets: list[dict[str, Any]] | None = None,
        question: str | None = None,
        direction: str = "both",
        filters: dict[str, Any] | None = None,
        include: list[str] | None = None,
        exclude: list[str] | None = None,
        budget: dict[str, Any] | None = None,
        slice_policy: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Delegate one exact dossier query and return the unmodified exchange schema."""
        self._bounded_input(
            {
                "profile": profile,
                "targets": targets,
                "question": question,
                "direction": direction,
                "filters": filters,
                "include": include,
                "exclude": exclude,
                "budget": budget,
                "slice_policy": slice_policy,
            }
        )
        try:
            selectors = [TargetSelector.model_validate(item) for item in targets or []]
            parsed_filters = DossierFilters.model_validate(filters or {})
            parsed_budget = DossierBudget.model_validate(budget or {})
            parsed_slice_policy = SourceSlicePolicy.model_validate(slice_policy or {})
            bundle = await self._session_bundle()
            exchange = await self._run(
                lambda: self._application.dossier(
                    bundle,
                    profile=DossierProfile(profile),
                    targets=selectors,
                    question=question,
                    direction=QueryDirection(direction),
                    filters=parsed_filters,
                    include=include or [],
                    exclude=exclude or [],
                    budget=parsed_budget,
                    slice_policy=parsed_slice_policy,
                )
            )
            return self._bounded_model(exchange)
        except Exception as error:
            raise self._public_error(error, operation="dossier") from error

    async def expand(
        self,
        *,
        exchange: dict[str, Any],
        action_id: str,
        budget: dict[str, Any] | None = None,
        slice_policy: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Apply an advertised action to an exact client-supplied base exchange."""
        self._bounded_input(
            {
                "exchange": exchange,
                "action_id": action_id,
                "budget": budget,
                "slice_policy": slice_policy,
            }
        )
        try:
            base = DossierExchange.model_validate(exchange)
            parsed_budget = DossierBudget.model_validate(budget) if budget is not None else None
            parsed_slice = SourceSlicePolicy.model_validate(slice_policy) if slice_policy is not None else None
            bundle = await self._session_bundle()
            result = await self._run(
                lambda: self._application.expand(
                    bundle,
                    base,
                    action_id=action_id,
                    budget=parsed_budget,
                    slice_policy=parsed_slice,
                )
            )
            return self._bounded_model(result)
        except Exception as error:
            raise self._public_error(error, operation="expand") from error

    async def check(self, *, artifact: dict[str, Any]) -> dict[str, Any]:
        """Validate an inline public artifact with the same bounded dispatcher as Python and CLI."""
        raw = canonical_ordered_json_bytes(artifact)
        self._bounded_raw(raw, limit=self._max_input_bytes, label="input")
        try:
            result = await self._run(
                lambda: self._application.check_bytes(raw, max_bytes=self._max_input_bytes)
            )
            return self._bounded_model(result)
        except Exception as error:
            raise self._public_error(error, operation="check") from error

    async def _session_bundle(self) -> ReviewSessionBundle:
        if self._bundle is not None:
            return self._bundle
        async with self._bundle_lock:
            if self._bundle is not None:
                return self._bundle
            try:
                providers = await self._run(self._load_providers)
                candidate = await self._run(
                    lambda: self._application.start(
                        self._root,
                        repository_id=self._repository_id,
                        provider_envelopes=providers,
                        source_paths=self._source_paths,
                    )
                )
            except Exception as error:
                raise self._public_error(error, operation="session") from error
            self._bundle = candidate
            return candidate

    def _load_providers(self) -> list[ProviderEnvelope]:
        return [load_provider_envelope(path) for path in self._provider_paths]

    async def _run(self, operation: Callable[[], _ResultT]) -> _ResultT:
        try:
            return await asyncio.wait_for(asyncio.to_thread(operation), timeout=self._timeout_seconds)
        # asyncio.TimeoutError only became an alias of the built-in TimeoutError
        # in Python 3.11; name both to preserve the public code on Python 3.10.
        except (TimeoutError, asyncio.TimeoutError) as error:
            raise MCPReviewError(
                "mcp_operation_timeout",
                "The bounded review operation exceeded its server timeout",
                remediation="Narrow the query or raise the server-operator timeout within 300 seconds.",
            ) from error
        except asyncio.CancelledError:
            raise

    def _bounded_input(self, value: dict[str, Any]) -> None:
        self._bounded_raw(
            canonical_ordered_json_bytes(value),
            limit=self._max_input_bytes,
            label="input",
        )

    def _bounded_model(self, value: BaseModel) -> dict[str, Any]:
        return self._bounded_result(value.model_dump(mode="json"))

    def _bounded_result(self, value: dict[str, Any]) -> dict[str, Any]:
        self._bounded_raw(
            canonical_ordered_json_bytes(value),
            limit=self._max_result_bytes,
            label="result",
        )
        return value

    @staticmethod
    def _bounded_raw(raw: bytes, *, limit: int, label: str) -> None:
        if len(raw) > limit:
            raise MCPReviewError(
                f"mcp_{label}_too_large",
                f"MCP {label} is {len(raw)} bytes; limit is {limit} bytes",
                remediation=(
                    "Use a narrower dossier, typed expansion, or a larger explicit server-operator limit."
                ),
            )

    @staticmethod
    def _public_error(error: Exception, *, operation: str) -> MCPReviewError:
        if isinstance(error, MCPReviewError):
            return error
        if isinstance(error, ReviewApplicationError) or (
            isinstance(error, ValueError)
            and hasattr(error, "code")
            and hasattr(error, "remediation")
        ):
            return MCPReviewError(
                str(getattr(error, "code")),
                str(error),
                remediation=str(getattr(error, "remediation")),
            )
        if isinstance(error, (ValidationError, ValueError)):
            return MCPReviewError(
                f"mcp_{operation}_invalid",
                f"The {operation} request failed validation",
                remediation="Inspect the tool input schema and exact source-state bindings, then retry.",
            )
        logger.exception("MCP review operation failed", exc_info=error)
        return MCPReviewError(
            "mcp_internal_error",
            "The review operation failed internally",
            remediation="Inspect server-side logs without exposing them to the client, then retry.",
        )


def create_mcp_server(service: MCPReviewService) -> Any:
    """Create the optional official-SDK server without importing MCP in baseline use."""
    try:
        from mcp.server import MCPServer
        from mcp.types import ToolAnnotations
    except ImportError as error:
        raise MCPReviewError(
            "mcp_dependency_missing",
            "The optional MCP SDK is not installed",
            remediation="Install Anatomize with the 'mcp' extra: pip install 'anatomize[mcp]'.",
        ) from error

    server = MCPServer(
        name="anatomize",
        title="Anatomize repository review",
        description="Read-only source-bound repository intelligence and progressive review dossiers.",
        instructions=(
            "Negotiate capabilities first. Request bounded dossiers, preserve omissions and source-state "
            "bindings, and use only advertised expansion actions. Anatomize supplies evidence, not decisions."
        ),
        version=__version__,
        log_level="WARNING",
    )
    annotations = ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )

    @server.tool(
        name="anatomize_capabilities",
        description="Negotiate exact review operations, schemas, limits, and authority boundaries.",
        annotations=annotations,
        structured_output=True,
    )
    def capabilities() -> dict[str, Any]:
        return service.capabilities()

    @server.tool(
        name="anatomize_session",
        description="Build or reuse one deterministic repository session and return safe exact metadata.",
        annotations=annotations,
        structured_output=True,
    )
    async def session() -> dict[str, Any]:
        return await service.session()

    @server.tool(
        name="anatomize_refresh",
        description="Rebuild the read-only session after repository files change and atomically swap it.",
        annotations=annotations,
        structured_output=True,
    )
    async def refresh() -> dict[str, Any]:
        return await service.refresh()

    @server.tool(
        name="anatomize_dossier",
        description="Answer one bounded lifecycle question with the public dossier-exchange schema.",
        annotations=annotations,
        structured_output=True,
    )
    async def dossier(
        profile: str,
        targets: list[dict[str, Any]] | None = None,
        question: str | None = None,
        direction: str = "both",
        filters: dict[str, Any] | None = None,
        include: list[str] | None = None,
        exclude: list[str] | None = None,
        budget: dict[str, Any] | None = None,
        slice_policy: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await service.dossier(
            profile=profile,
            targets=targets,
            question=question,
            direction=direction,
            filters=filters,
            include=include,
            exclude=exclude,
            budget=budget,
            slice_policy=slice_policy,
        )

    @server.tool(
        name="anatomize_expand",
        description="Apply one action advertised by an exact dossier exchange.",
        annotations=annotations,
        structured_output=True,
    )
    async def expand(
        exchange: dict[str, Any],
        action_id: str,
        budget: dict[str, Any] | None = None,
        slice_policy: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await service.expand(
            exchange=exchange,
            action_id=action_id,
            budget=budget,
            slice_policy=slice_policy,
        )

    @server.tool(
        name="anatomize_check",
        description="Validate one inline public artifact through the shared bounded checker.",
        annotations=annotations,
        structured_output=True,
    )
    async def check(artifact: dict[str, Any]) -> dict[str, Any]:
        return await service.check(artifact=artifact)

    @server.resource(
        "anatomize://capabilities",
        name="Anatomize capabilities",
        description="Versioned review and transport contract.",
        mime_type="application/json",
    )
    def capabilities_resource() -> str:
        return json.dumps(service.capabilities(), ensure_ascii=False, sort_keys=True)

    @server.resource(
        "anatomize://session",
        name="Anatomize session",
        description="Exact session metadata; raw portable blobs remain server-side.",
        mime_type="application/json",
    )
    async def session_resource() -> str:
        return json.dumps(await service.session(), ensure_ascii=False, sort_keys=True)

    return server


def run_mcp_server(
    service: MCPReviewService,
    *,
    transport: str = "stdio",
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    """Run the optional official MCP transport with a loopback-only HTTP policy."""
    if transport not in {"stdio", "streamable-http"}:
        raise ValueError("MCP transport must be 'stdio' or 'streamable-http'")
    if transport == "streamable-http" and host not in {"127.0.0.1", "localhost", "::1"}:
        raise MCPReviewError(
            "mcp_network_exposure_refused",
            "Unauthenticated MCP HTTP serving is restricted to loopback interfaces",
            remediation="Use a loopback host or place an authenticated gateway in a separately reviewed deployment.",
        )
    if not 1 <= port <= 65_535:
        raise ValueError("MCP port must be in [1, 65535]")
    server = create_mcp_server(service)
    if transport == "stdio":
        server.run(transport="stdio")
    else:
        server.run(
            transport="streamable-http",
            host=host,
            port=port,
            json_response=True,
            stateless_http=True,
        )
