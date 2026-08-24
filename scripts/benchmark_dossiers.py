"""Measure deterministic dossier engine construction, cold query, and cache reuse."""

from __future__ import annotations

import argparse
import json
import statistics
import time
import tracemalloc

from anatomize._artifacts import sha256_digest
from anatomize.dossiers import (
    DossierBudget,
    DossierContext,
    DossierEngine,
    DossierProfile,
    DossierRequest,
    TargetKind,
    TargetSelector,
    build_dossier_request,
    canonical_dossier_bytes,
)
from anatomize.evidence import (
    CompletenessRecord,
    CompletenessStatus,
    EdgeRecord,
    EvidenceProducer,
    EvidenceStrength,
    ObservationStance,
    ProviderRunRecord,
    ProviderRunStatus,
    RelationshipCategory,
    RepositoryEntity,
    RepositoryEvidence,
    SourceStateRecord,
    StructuralObservation,
    SymbolEntity,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--entities", type=int, default=5_000)
    parser.add_argument("--queries", type=int, default=100)
    parser.add_argument("--max-build-ms", type=float, default=1_500.0)
    parser.add_argument("--max-cold-p95-ms", type=float, default=50.0)
    parser.add_argument("--max-cached-p95-ms", type=float, default=0.1)
    parser.add_argument("--max-peak-mib", type=float, default=128.0)
    args = parser.parse_args()
    if args.entities < 10 or args.queries < 2:
        parser.error("entities must be at least 10 and queries at least 2")

    context = _context(args.entities)
    requests = [_request(context, index * args.entities // args.queries) for index in range(args.queries)]

    tracemalloc.start()
    started = time.perf_counter()
    engine = DossierEngine(context)
    build_ms = (time.perf_counter() - started) * 1_000
    cold_ms = []
    for request in requests:
        started = time.perf_counter()
        engine.query(request)
        cold_ms.append((time.perf_counter() - started) * 1_000)
    cached_ms = []
    for request in requests:
        started = time.perf_counter()
        engine.query(request)
        cached_ms.append((time.perf_counter() - started) * 1_000)
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    first = engine.query(requests[0])
    repeat = DossierEngine(context).query(requests[0])
    deterministic = canonical_dossier_bytes(first) == canonical_dossier_bytes(repeat)
    result = {
        "schema_version": "1.0.0",
        "entities": args.entities,
        "edges": args.entities - 1,
        "observations": args.entities - 1,
        "queries": args.queries,
        "engine_build_ms": round(build_ms, 3),
        "cold_query_ms": _summary(cold_ms),
        "cached_query_ms": _summary(cached_ms),
        "peak_mib": round(peak / 1024 / 1024, 3),
        "cached_query_count": engine.cached_query_count,
        "deterministic": deterministic,
        "thresholds": {
            "max_build_ms": args.max_build_ms,
            "max_cold_p95_ms": args.max_cold_p95_ms,
            "max_cached_p95_ms": args.max_cached_p95_ms,
            "max_peak_mib": args.max_peak_mib,
        },
    }
    failures = []
    if build_ms > args.max_build_ms:
        failures.append("engine_build")
    if float(result["cold_query_ms"]["p95"]) > args.max_cold_p95_ms:
        failures.append("cold_query_p95")
    if float(result["cached_query_ms"]["p95"]) > args.max_cached_p95_ms:
        failures.append("cached_query_p95")
    if peak / 1024 / 1024 > args.max_peak_mib:
        failures.append("peak_memory")
    if not deterministic:
        failures.append("determinism")
    result["failures"] = failures
    print(json.dumps(result, indent=2, sort_keys=True))
    if failures:
        raise SystemExit(1)


def _context(count: int) -> DossierContext:
    repository_id = "repository:benchmark"
    state_id = "state:benchmark"
    provider_run_id = "provider-run:benchmark"
    entities = [
        RepositoryEntity(
            entity_id="entity:repository",
            source_state_id=state_id,
            display_name="benchmark",
            repository_id=repository_id,
            root_name="benchmark",
        ),
        *[
            SymbolEntity(
                entity_id=f"entity:symbol:{index:06d}",
                source_state_id=state_id,
                display_name=f"benchmark.symbol_{index:06d}",
                language="python",
                symbol_kind="function",
                name=f"symbol_{index:06d}",
                qualified_name=f"benchmark.symbol_{index:06d}",
                public=index % 10 == 0,
            )
            for index in range(count)
        ],
    ]
    edges = [
        EdgeRecord(
            edge_id=f"edge:call:{index:06d}",
            source_state_id=state_id,
            source_entity_id=f"entity:symbol:{index:06d}",
            target_entity_id=f"entity:symbol:{index + 1:06d}",
            category=RelationshipCategory.CALL,
            predicate="calls",
        )
        for index in range(count - 1)
    ]
    observations = [
        StructuralObservation(
            observation_id=f"observation:call:{index:06d}",
            source_state_id=state_id,
            provider_run_id=provider_run_id,
            method="known-truth-chain",
            method_version="1.0.0",
            strength=EvidenceStrength.EXACT,
            stance=ObservationStance.SUPPORTS,
            completeness_id="completeness:benchmark",
            rationale="The fixture declares one exact call edge.",
            target_type="edge",
            target_id=edge.edge_id,
        )
        for index, edge in enumerate(edges)
    ]
    evidence = RepositoryEvidence(
        producer=EvidenceProducer(version="benchmark"),
        repository_id=repository_id,
        states=[
            SourceStateRecord(
                state_id=state_id,
                repository_id=repository_id,
                revision="benchmark",
                dirty=False,
                content_digest=_digest(f"source:{count}"),
                file_count=count,
            )
        ],
        provider_runs=[
            ProviderRunRecord(
                provider_run_id=provider_run_id,
                provider_id="benchmark",
                provider_version="1.0.0",
                source_state_id=state_id,
                configuration_digest=_digest("provider-configuration"),
                method="fixture",
                capabilities=["entities", "relationships"],
                status=ProviderRunStatus.COMPLETE,
            )
        ],
        entities=entities,
        edges=edges,
        observations=observations,
        completeness=[
            CompletenessRecord(
                completeness_id="completeness:benchmark",
                source_state_id=state_id,
                provider_run_id=provider_run_id,
                scope_type="repository",
                scope_id="entity:repository",
                evidence_families=["entities", "relationships"],
                status=CompletenessStatus.COMPLETE,
            )
        ],
    )
    return DossierContext.from_evidence(
        session_id="session:benchmark",
        session_manifest_digest=_digest("manifest"),
        repository_id=repository_id,
        source_state_ids=[state_id],
        provider_run_ids=[provider_run_id],
        policy_digest=_digest("policy"),
        evidence=[evidence],
    )


def _request(context: DossierContext, index: int) -> DossierRequest:
    return build_dossier_request(
        profile=DossierProfile.LOCALISATION,
        question=f"Locate benchmark symbol {index}.",
        session_id=context.session_id,
        session_manifest_digest=context.session_manifest_digest,
        targets=[TargetSelector(kind=TargetKind.SYMBOL, identity=f"entity:symbol:{index:06d}")],
        budget=DossierBudget(max_items=32, max_depth=2, max_payload_bytes=131_072),
    )


def _summary(values: list[float]) -> dict[str, float]:
    return {
        "median": round(statistics.median(values), 3),
        "p95": round(statistics.quantiles(values, n=20)[18], 3),
        "maximum": round(max(values), 3),
    }


def _digest(value: str) -> str:
    return sha256_digest(value.encode("utf-8"))


if __name__ == "__main__":
    main()
