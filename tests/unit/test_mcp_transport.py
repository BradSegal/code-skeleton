from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Iterable
from pathlib import Path

import pytest

from anatomize.dossiers import DossierProfile, TargetKind
from anatomize.mcp_transport import MCPReviewError, MCPReviewService
from anatomize.providers import ProviderEnvelope
from anatomize.review import DossierExchange, ProviderArtifactInput, ReviewApplication, target_selector
from anatomize.sessions import ReviewSessionBundle

pytestmark = pytest.mark.unit


def _repository(root: Path) -> Path:
    (root / "src" / "pkg").mkdir(parents=True)
    (root / "src" / "pkg" / "core.py").write_text(
        "def answer() -> int:\n    return 42\n",
        encoding="utf-8",
    )
    (root / "README.md").write_text("# MCP fixture\n", encoding="utf-8")
    return root


def test_service_results_are_schema_identical_bounded_and_path_private(tmp_path: Path) -> None:
    root = _repository(tmp_path / "repository")
    service = MCPReviewService(root, repository_id="repository:mcp-test")
    application = ReviewApplication()
    bundle = application.start(root, repository_id="repository:mcp-test")

    async def journey() -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
        sessions = await asyncio.gather(*(service.session() for _ in range(6)))
        assert len({item["bundle_id"] for item in sessions}) == 1
        result = await service.dossier(profile="orientation")
        checked = await service.check(artifact=result)
        return sessions[0], result, checked

    metadata, result, checked = asyncio.run(journey())
    exchange = DossierExchange.model_validate(result)
    direct = application.dossier(bundle, profile=DossierProfile.ORIENTATION)

    assert exchange == direct
    assert checked["valid"] is True
    assert metadata["manifest"] == bundle.manifest.model_dump(mode="json")
    assert metadata["portable_bundle_included"] is False
    all_output = json.dumps([service.capabilities(), metadata, result])
    assert str(root.resolve()) not in all_output
    assert service.capabilities()["authority"]["optional_provider_invocation"] is False
    assert all(item.role.value != "source_slice" for item in bundle.manifest.artifacts)


def test_service_expansion_and_failures_preserve_application_contract(tmp_path: Path) -> None:
    root = _repository(tmp_path / "repository")
    service = MCPReviewService(root, repository_id="repository:mcp-test")

    async def journey() -> DossierExchange:
        base_payload = await service.dossier(profile="orientation")
        base = DossierExchange.model_validate(base_payload)
        expanded = await service.expand(
            exchange=base_payload,
            action_id=base.dossier.expansions[0].action_id,
        )
        with pytest.raises(MCPReviewError) as invalid:
            await service.expand(exchange=base_payload, action_id="action:not-issued")
        assert invalid.value.code == "dossier_action_unknown"
        assert str(root.resolve()) not in str(invalid.value)
        return DossierExchange.model_validate(expanded)

    result = asyncio.run(journey())
    assert result.request.expansion is not None
    assert result.dossier.base_dossier_id is not None


def test_explicit_refresh_atomically_rebinds_the_service_source_state(tmp_path: Path) -> None:
    root = _repository(tmp_path / "repository")
    service = MCPReviewService(root, repository_id="repository:mcp-refresh")

    async def journey() -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
        before = await service.session()
        (root / "src" / "pkg" / "core.py").write_text(
            "def answer() -> int:\n    return 43\n",
            encoding="utf-8",
        )
        cached = await service.session()
        refreshed = await service.refresh()
        after = await service.session()
        assert cached["bundle_id"] == before["bundle_id"]
        assert refreshed["changed"] is True
        assert refreshed["previous_bundle_id"] == before["bundle_id"]
        assert after["bundle_id"] == refreshed["bundle_id"]
        return before, refreshed, after

    before, refreshed, after = asyncio.run(journey())
    assert before["bundle_id"] != after["bundle_id"]
    assert service.capabilities()["tools"]["anatomize_refresh"] == "refresh"
    assert str(root.resolve()) not in json.dumps(refreshed)


class _SlowApplication(ReviewApplication):
    def start(
        self,
        root: Path,
        *,
        repository_id: str | None = None,
        provider_envelopes: Iterable[ProviderEnvelope] = (),
        provider_artifacts: Iterable[ProviderArtifactInput] = (),
        source_paths: Iterable[str] = (),
    ) -> ReviewSessionBundle:
        time.sleep(0.08)
        return super().start(
            root,
            repository_id=repository_id,
            provider_envelopes=provider_envelopes,
            provider_artifacts=provider_artifacts,
            source_paths=source_paths,
        )


def test_timeout_cancellation_and_result_limits_are_explicit(tmp_path: Path) -> None:
    root = _repository(tmp_path / "repository")

    async def timed_out() -> None:
        service = MCPReviewService(
            root,
            application=_SlowApplication(),
            timeout_seconds=0.01,
        )
        with pytest.raises(MCPReviewError) as captured:
            await service.session()
        assert captured.value.code == "mcp_operation_timeout"

    async def cancelled() -> None:
        service = MCPReviewService(root, application=_SlowApplication())
        task = asyncio.create_task(service.session())
        await asyncio.sleep(0.005)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    async def limited() -> None:
        service = MCPReviewService(root, max_result_bytes=4_096)
        with pytest.raises(MCPReviewError) as captured:
            await service.session()
        assert captured.value.code == "mcp_result_too_large"

    asyncio.run(timed_out())
    asyncio.run(cancelled())
    asyncio.run(limited())


def test_service_rejects_invalid_targets_before_disclosing_repository_state(tmp_path: Path) -> None:
    root = _repository(tmp_path / "repository")
    service = MCPReviewService(root)
    selector = target_selector("src/pkg/core.py", kind=TargetKind.FILE)

    async def invalid() -> None:
        with pytest.raises(MCPReviewError) as captured:
            await service.dossier(profile="unsupported", targets=[selector.model_dump(mode="json")])
        assert captured.value.code == "mcp_dossier_invalid"
        assert str(root.resolve()) not in str(captured.value)

    asyncio.run(invalid())
