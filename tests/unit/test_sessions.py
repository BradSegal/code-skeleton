from __future__ import annotations

import asyncio
import shutil
import threading
import time
from pathlib import Path

import pytest
from pydantic import ValidationError

import anatomize.sessions.store as store_module
from anatomize._artifacts import sha256_digest
from anatomize.evidence import ProviderRunStatus, SourceStateRecord
from anatomize.sessions import (
    ArtifactRole,
    BudgetBinding,
    OmissionScope,
    ReviewSessionBundle,
    SchemaBinding,
    SessionArtifactError,
    SessionProviderBinding,
    SessionStatus,
    SessionStore,
    SessionStoreError,
    build_omission,
    build_query,
    build_session_artifact,
    build_session_bundle,
    build_session_manifest,
    canonical_session_bundle_bytes,
    load_session_bundle,
    parse_session_bundle,
    write_session_bundle,
)
from anatomize.temporal import (
    HistoryStatus,
    ProviderStateBinding,
    StateArtifactBinding,
    StateManifest,
)


def _digest(value: str) -> str:
    return sha256_digest(value.encode())


def _bundle(label: str, *, status: SessionStatus = SessionStatus.COMPLETE) -> ReviewSessionBundle:
    repository_id = "repository:sessions"
    state_id = f"state:{label}"
    provider_run_id = f"provider-run:{label}"
    provider_artifact_id = f"provider-artifact:{label}"
    raw_content = f"raw-provider:{label}\n".encode()
    raw_digest = sha256_digest(raw_content)
    evidence_content = f'{{"evidence":"{label}"}}\n'.encode()
    state = StateManifest(
        repository_id=repository_id,
        source_state=SourceStateRecord(
            state_id=state_id,
            repository_id=repository_id,
            revision=label,
            dirty=False,
            content_digest=_digest(f"source:{label}"),
            file_count=3,
        ),
        evidence_artifact_digest=sha256_digest(evidence_content),
        configuration_digest=_digest("configuration"),
        provider_states=[
            ProviderStateBinding(
                provider_run_id=provider_run_id,
                provider_id="provider.python",
                provider_version="1.0.0",
                configuration_digest=_digest("provider-configuration"),
                capabilities=["entities", "relationships"],
                artifacts=[StateArtifactBinding(artifact_id=provider_artifact_id, digest=raw_digest)],
                status=ProviderRunStatus.COMPLETE,
            )
        ],
        history_status=HistoryStatus.COMPLETE,
        baseline_available=True,
    )
    evidence_artifact, evidence_blob = build_session_artifact(
        role=ArtifactRole.NORMALIZED_EVIDENCE,
        content=evidence_content,
        media_type="application/vnd.anatomize.evidence+json",
        portable_path="normalized/evidence.json",
        source_state_ids=[state_id],
    )
    report_artifact, report_blob = build_session_artifact(
        role=ArtifactRole.REPORT,
        content=f"# Review {label}\n".encode(),
        media_type="text/markdown",
        portable_path="reports/review.md",
        source_state_ids=[state_id],
        derived_from_ids=[evidence_artifact.artifact_id],
    )
    slice_artifact, slice_blob = build_session_artifact(
        role=ArtifactRole.SOURCE_SLICE,
        content=b"def answer() -> int: ...\n",
        media_type="text/x-python",
        portable_path="slices/src/pkg/core.py",
        source_state_ids=[state_id],
        derived_from_ids=[report_artifact.artifact_id],
    )
    artifacts = [evidence_artifact, report_artifact, slice_artifact]
    manifest = build_session_manifest(
        repository_id=repository_id,
        status=status,
        configuration_digest=_digest("configuration"),
        policy_digest=_digest("policy"),
        source_states=[state],
        schemas=[
            SchemaBinding(
                artifact_type="anatomize.session",
                schema_version="1.0.0",
                schema_digest=_digest("session-schema"),
            ),
            SchemaBinding(
                artifact_type="anatomize.evidence",
                schema_version="1.0.0",
                schema_digest=_digest("evidence-schema"),
            ),
            SchemaBinding(
                artifact_type="anatomize.state-manifest",
                schema_version="1.0.0",
                schema_digest=_digest("state-schema"),
            ),
        ],
        providers=[
            SessionProviderBinding(
                provider_run_id=provider_run_id,
                provider_id="provider.python",
                provider_version="1.0.0",
                configuration_digest=_digest("provider-configuration"),
                status=ProviderRunStatus.COMPLETE,
            )
        ],
        query=build_query("review", {"focus": "src/pkg/core.py", "depth": 2}),
        budgets=[
            BudgetBinding(name="tokens", unit="tokens", limit=8000, used=2100),
            BudgetBinding(name="files", unit="files", limit=20, used=3),
        ],
        artifacts=artifacts,
        omissions=[
            build_omission(
                OmissionScope.PROVIDER,
                "runtime_not_requested",
                "Runtime evidence was outside this static review session.",
                provider_id="provider.runtime",
            )
        ],
    )
    return build_session_bundle(
        manifest,
        [evidence_blob, report_blob, slice_blob],
    )


def test_session_manifest_binds_every_reproducibility_dimension_without_duplicate_provider_bytes() -> None:
    bundle = _bundle("a")
    assert {item.role for item in bundle.manifest.artifacts} == {
        ArtifactRole.NORMALIZED_EVIDENCE,
        ArtifactRole.REPORT,
        ArtifactRole.SOURCE_SLICE,
    }
    assert bundle.manifest.recorded_at is None
    assert bundle.manifest.source_states[0].provider_states[0].artifacts[0].digest == _digest("raw-provider:a\n")
    assert {item.name: item.limit for item in bundle.manifest.budgets}["tokens"] == 8000
    assert bundle.manifest.omissions[0].scope is OmissionScope.PROVIDER
    assert canonical_session_bundle_bytes(_bundle("a")) == canonical_session_bundle_bytes(bundle)


def test_atomic_store_recovers_prior_generation_after_corruption_and_failed_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = SessionStore(tmp_path / "cache")
    first = _bundle("a")
    second = _bundle("b")
    first_generation = store.publish(first)
    second_generation = store.publish(second)
    assert store.load() == second

    (store.root / "generations" / second_generation / "bundle.json").write_bytes(b"corrupt")
    assert store.load(recover=True) == first
    assert first_generation == (store.root / "PREVIOUS").read_text(encoding="ascii").strip()

    original_write = store_module._write_bytes

    def exhaust_disk(path: Path, raw: bytes) -> None:
        if path.name == "bundle.json":
            raise OSError("simulated disk exhaustion")
        original_write(path, raw)

    monkeypatch.setattr(store_module, "_write_bytes", exhaust_disk)
    with pytest.raises(OSError, match="disk exhaustion"):
        store.publish(_bundle("c"))
    assert store.load(recover=True) == first


def test_refresh_serializes_writers_and_cancellation_or_partial_failure_preserves_current(
    tmp_path: Path,
) -> None:
    store = SessionStore(tmp_path / "cache")
    first = _bundle("a")
    store.publish(first)
    active = 0
    maximum_active = 0
    guard = threading.Lock()

    def builder(previous: ReviewSessionBundle | None) -> ReviewSessionBundle:
        nonlocal active, maximum_active
        assert previous is not None
        with guard:
            active += 1
            maximum_active = max(maximum_active, active)
        time.sleep(0.03)
        with guard:
            active -= 1
        return previous

    threads = [threading.Thread(target=store.refresh, args=(builder,)) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert maximum_active == 1

    def cancelled(previous: ReviewSessionBundle | None) -> ReviewSessionBundle:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        store.refresh(cancelled)
    with pytest.raises(SessionStoreError) as partial:
        store.publish(_bundle("partial", status=SessionStatus.PARTIAL))
    assert partial.value.code == "session_incomplete"
    assert store.load() == first


def test_concurrent_reader_observes_old_then_complete_new_generation(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "cache")
    first = _bundle("a")
    second = _bundle("b")
    store.publish(first)
    building = threading.Event()
    release = threading.Event()
    failures: list[BaseException] = []

    def builder(previous: ReviewSessionBundle | None) -> ReviewSessionBundle:
        assert previous == first
        building.set()
        release.wait(timeout=2)
        return second

    def refresh() -> None:
        try:
            store.refresh(builder)
        except BaseException as error:  # pragma: no cover - asserted through failures
            failures.append(error)

    thread = threading.Thread(target=refresh)
    thread.start()
    assert building.wait(timeout=2)
    assert store.load() == first
    release.set()
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert failures == []
    assert store.load() == second


def test_interrupted_current_pointer_replacement_preserves_previous_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = SessionStore(tmp_path / "cache")
    first = _bundle("a")
    store.publish(first)
    original_write = store_module._write_bytes

    def interrupt_pointer(path: Path, raw: bytes) -> None:
        if path.name == "CURRENT":
            raise OSError("simulated interrupted pointer replacement")
        original_write(path, raw)

    monkeypatch.setattr(store_module, "_write_bytes", interrupt_pointer)
    with pytest.raises(OSError, match="interrupted pointer"):
        store.publish(_bundle("b"))
    assert store.load() == first


def test_portable_export_is_bounded_deterministic_and_rebuilds_a_deleted_store(
    tmp_path: Path,
) -> None:
    bundle = _bundle("portable")
    export = tmp_path / "session.json"
    write_session_bundle(bundle, export)
    assert load_session_bundle(export) == bundle
    assert export.read_bytes() == canonical_session_bundle_bytes(bundle)

    store_path = tmp_path / "derived-store"
    first_store = SessionStore(store_path)
    first_store.publish(bundle)
    materialized = tmp_path / "materialized"
    first_store.materialize(first_store.load(), materialized)
    assert (materialized / "reports" / "review.md").read_text(encoding="utf-8") == "# Review portable\n"

    shutil.rmtree(store_path)
    rebuilt = SessionStore(store_path)
    rebuilt.publish(load_session_bundle(export))
    assert canonical_session_bundle_bytes(rebuilt.load()) == export.read_bytes()

    with pytest.raises(SessionArtifactError) as corrupt:
        parse_session_bundle(b"not-json")
    assert corrupt.value.code == "session_bundle_corrupt"
    with pytest.raises(SessionArtifactError) as oversized:
        parse_session_bundle(export.read_bytes(), max_bytes=8)
    assert oversized.value.code == "session_bundle_too_large"


def test_manifest_rejects_tampered_content_identity_native_artifact_and_cycles() -> None:
    payload = _bundle("tamper").model_dump(mode="json")
    payload["manifest"]["artifacts"][0]["digest"] = _digest("different")
    with pytest.raises(ValidationError):
        ReviewSessionBundle.model_validate(payload)
    payload = _bundle("cycle").model_dump(mode="json")
    first_id = payload["manifest"]["artifacts"][0]["artifact_id"]
    second_id = payload["manifest"]["artifacts"][1]["artifact_id"]
    payload["manifest"]["artifacts"][0]["derived_from_ids"] = [second_id]
    payload["manifest"]["artifacts"][1]["derived_from_ids"] = [first_id]
    with pytest.raises(ValidationError):
        ReviewSessionBundle.model_validate(payload)
