"""Command-line interface for the evidence-first review application."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

if TYPE_CHECKING:
    from anatomize.review import ProviderArtifactInput

review_app = typer.Typer(
    name="review",
    help="Build source-bound review sessions and lifecycle evidence dossiers.",
    add_completion=False,
    rich_markup_mode=None,
    pretty_exceptions_enable=False,
)

@review_app.command("capabilities")
def review_capabilities_command(
    format: Annotated[
        str,
        typer.Option("--format", "-f", help="Output format: text, json, or markdown."),
    ] = "json",
    width: Annotated[int, typer.Option("--width", help="Plain-text wrap width (minimum 40).")] = 88,
) -> None:
    """Negotiate review operations, schemas, profiles, targets, and invocation policy."""
    try:
        from anatomize.review import ReviewApplication

        _emit_review(ReviewApplication().capabilities(), format=format, output=None, width=width)
    except (ValueError, OSError) as error:
        _review_failure(error)


@review_app.command("state")
def review_state_command(
    root: Annotated[
        Path,
        typer.Argument(help="Repository root to fingerprint without building semantic evidence."),
    ] = Path("."),
    repository_id: Annotated[
        str | None,
        typer.Option("--repository-id", help="Stable repository identity represented by the result."),
    ] = None,
    format: Annotated[
        str,
        typer.Option("--format", "-f", help="Output format: text, json, or markdown."),
    ] = "json",
    width: Annotated[int, typer.Option("--width", help="Plain-text wrap width (minimum 40).")] = 88,
) -> None:
    """Return the exact source identity used by review sessions."""
    try:
        from anatomize.review import ReviewApplication

        _emit_review(
            ReviewApplication().source_state(root, repository_id=repository_id),
            format=format,
            output=None,
            width=width,
        )
    except (ValueError, OSError) as error:
        _review_failure(error)


@review_app.command("start")
def review_start_command(
    root: Annotated[
        Path,
        typer.Argument(help="Repository root to index and bind into a review session."),
    ] = Path("."),
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Write the rendered session or report to this path."),
    ] = None,
    format: Annotated[
        str,
        typer.Option("--format", "-f", help="Output format: text, json, or markdown."),
    ] = "text",
    repository_id: Annotated[
        str | None,
        typer.Option("--repository-id", help="Stable identity shared by before and after sessions."),
    ] = None,
    provider: Annotated[
        list[Path],
        typer.Option(
            "--provider",
            help="Import an advanced normalized evidence file; Anatomize does not run its producer.",
        ),
    ] = [],
    artifact: Annotated[
        list[str],
        typer.Option(
            "--artifact",
            help=(
                "Normalize captured evidence as KIND[@VERSION]=PATH. Supported kinds: sarif, lsp, junit, "
                "coverage, mutation, jscpd, snakemake, targets, renv, cyclonedx, ro-crate. Repeat as needed."
            ),
        ),
    ] = [],
    test_selection: Annotated[
        list[str],
        typer.Option(
            "--test-selection",
            help="Exact test path or node selection represented by imported runtime artifacts.",
        ),
    ] = [],
    environment_digest: Annotated[
        str | None,
        typer.Option(
            "--environment-digest",
            help="sha256 digest of the environment represented by runtime artifacts.",
        ),
    ] = None,
    source: Annotated[
        list[str],
        typer.Option(
            "--include-source",
            help="Explicit repository-relative source path to embed; omitted by default for privacy.",
        ),
    ] = [],
    store: Annotated[
        Path | None,
        typer.Option("--store", help="Atomically publish the complete session to a recoverable store."),
    ] = None,
    width: Annotated[int, typer.Option("--width", help="Plain-text wrap width (minimum 40).")] = 88,
) -> None:
    """Index one exact state and create a deterministic portable review session."""
    try:
        from anatomize.providers import load_provider_envelope
        from anatomize.review import ReviewApplication
        from anatomize.sessions import SessionStore

        envelopes = [load_provider_envelope(path) for path in provider]
        artifacts = _provider_artifact_inputs(
            artifact,
            selection=test_selection,
            environment_digest=environment_digest,
        )
        bundle = ReviewApplication().start(
            root,
            repository_id=repository_id,
            provider_envelopes=envelopes,
            provider_artifacts=artifacts,
            source_paths=source,
        )
        if store is not None:
            SessionStore(store).publish(bundle)
        _emit_review(bundle, format=format, output=output, width=width)
    except (ValueError, OSError) as error:
        _review_failure(error)


def _provider_artifact_inputs(
    values: list[str],
    *,
    selection: list[str],
    environment_digest: str | None,
) -> list[ProviderArtifactInput]:
    """Parse compact CLI artifact declarations without weakening the typed API."""
    from anatomize.review import ProviderArtifactInput, ProviderArtifactKind

    result: list[ProviderArtifactInput] = []
    for value in values:
        declaration, separator, raw_path = value.partition("=")
        if not separator or not declaration or not raw_path:
            raise ValueError("--artifact requires KIND[@VERSION]=PATH")
        raw_kind, version_separator, version = declaration.partition("@")
        path = Path(raw_path)
        result.append(
            ProviderArtifactInput(
                kind=ProviderArtifactKind(raw_kind),
                content=path.read_bytes(),
                provider_version=version if version_separator else "unknown",
                selection=tuple(selection),
                environment_digest=environment_digest,
            )
        )
    return result


@review_app.command("dossier")
def review_dossier_command(
    session: Annotated[Path, typer.Argument(help="Portable review session bundle.")],
    targets: Annotated[
        list[str],
        typer.Argument(
            help=(
                "Zero or more target values; orientation and repository-wide design, audit, "
                "change-review, or closure may omit them."
            )
        ),
    ] = [],
    profile: Annotated[
        str,
        typer.Option(
            "--profile",
            "-p",
            help="orientation, design, audit, localisation, implementation, change_review, or closure.",
        ),
    ] = "orientation",
    target_kind: Annotated[
        str,
        typer.Option("--target-kind", help="Typed interpretation applied to each target value."),
    ] = "file",
    question: Annotated[str | None, typer.Option("--question", "-q", help="Exact lifecycle question.")] = None,
    direction: Annotated[
        str,
        typer.Option("--direction", help="Relationship traversal: inbound, outbound, or both."),
    ] = "both",
    role: Annotated[
        list[str],
        typer.Option("--role", help="Restrict evidence to a role; repeat for multiple roles."),
    ] = [],
    evidence_kind: Annotated[
        list[str],
        typer.Option("--evidence-kind", help="Restrict evidence record kinds."),
    ] = [],
    provider: Annotated[
        list[str],
        typer.Option("--provider", help="Restrict evidence to named imported evidence sources."),
    ] = [],
    include: Annotated[list[str], typer.Option("--include", help="Force an exact evidence identity into scope.")] = [],
    exclude: Annotated[list[str], typer.Option("--exclude", help="Exclude an exact evidence identity.")] = [],
    max_items: Annotated[int | None, typer.Option("--max-items", help="Optional evidence-item override.")] = None,
    max_bytes: Annotated[int | None, typer.Option("--max-bytes", help="Optional canonical-byte override.")] = None,
    depth: Annotated[int | None, typer.Option("--depth", help="Optional relationship-depth override.")] = None,
    output: Annotated[Path | None, typer.Option("--output", "-o", help="Write output to this path.")] = None,
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: text, json, or markdown.")] = "text",
    width: Annotated[int, typer.Option("--width", help="Plain-text wrap width (minimum 40).")] = 88,
) -> None:
    """Build a bounded, role-labelled dossier for one lifecycle question."""
    try:
        from anatomize.dossiers import (
            DossierBudget,
            DossierFilters,
            DossierProfile,
            EvidenceRole,
            QueryDirection,
            TargetKind,
        )
        from anatomize.review import ReviewApplication, target_selector
        from anatomize.sessions import load_session_bundle

        selected_profile = DossierProfile(profile)
        selected_kind = TargetKind(target_kind)
        filters = DossierFilters(
            evidence_kinds=evidence_kind,
            roles=[EvidenceRole(item) for item in role],
            provider_ids=provider,
        )
        budget = None
        if max_items is not None or max_bytes is not None or depth is not None:
            defaults = DossierBudget()
            budget = DossierBudget.model_validate(
                defaults.model_copy(update={
                    **({"max_items": max_items} if max_items is not None else {}),
                    **({"max_payload_bytes": max_bytes} if max_bytes is not None else {}),
                    **({"max_depth": depth} if depth is not None else {}),
                })
            )
        exchange = ReviewApplication().dossier(
            load_session_bundle(session),
            profile=selected_profile,
            targets=[target_selector(item, kind=selected_kind) for item in targets],
            question=question,
            direction=QueryDirection(direction),
            filters=filters,
            include=include,
            exclude=exclude,
            budget=budget,
        )
        _emit_review(exchange, format=format, output=output, width=width)
        if exchange.dossier.status.value in {"blocked", "failed"}:
            raise typer.Exit(3)
    except typer.Exit:
        raise
    except (ValueError, OSError) as error:
        _review_failure(error)


@review_app.command("expand")
def review_expand_command(
    session: Annotated[Path, typer.Argument(help="The exact portable session bundle.")],
    exchange: Annotated[Path, typer.Argument(help="Base dossier-exchange JSON artifact.")],
    action: Annotated[str, typer.Argument(help="An action_id advertised by the base dossier.")],
    max_items: Annotated[int | None, typer.Option("--max-items", help="Optional replacement item budget.")] = None,
    max_bytes: Annotated[int | None, typer.Option("--max-bytes", help="Optional replacement payload budget.")] = None,
    output: Annotated[Path | None, typer.Option("--output", "-o", help="Write output to this path.")] = None,
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: text, json, or markdown.")] = "text",
    width: Annotated[int, typer.Option("--width", help="Plain-text wrap width (minimum 40).")] = 88,
) -> None:
    """Apply one advertised expansion or cursor without changing the base dossier."""
    try:
        from anatomize.dossiers import DossierBudget
        from anatomize.review import DossierExchange, ReviewApplication, load_review_artifact
        from anatomize.sessions import load_session_bundle

        loaded = load_review_artifact(exchange, expected_type="anatomize.dossier-exchange")
        if not isinstance(loaded, DossierExchange):
            raise ValueError("Expected a dossier exchange")
        budget = None
        if max_items is not None or max_bytes is not None:
            budget = loaded.request.budget.model_copy(
                update={
                    **({"max_items": max_items} if max_items is not None else {}),
                    **({"max_payload_bytes": max_bytes} if max_bytes is not None else {}),
                }
            )
            budget = DossierBudget.model_validate(budget)
        result = ReviewApplication().expand(
            load_session_bundle(session),
            loaded,
            action_id=action,
            budget=budget,
        )
        _emit_review(result, format=format, output=output, width=width)
        if result.dossier.status.value in {"blocked", "failed"}:
            raise typer.Exit(3)
    except typer.Exit:
        raise
    except (ValueError, OSError) as error:
        _review_failure(error)


@review_app.command("similarity")
def review_similarity_command(
    session: Annotated[Path, typer.Argument(help="Exact portable review session bundle.")],
    minimum_lines: Annotated[int, typer.Option("--minimum-lines", min=1)] = 3,
    maximum_candidates: Annotated[int, typer.Option("--maximum-candidates", min=1, max=100_000)] = 1_000,
    role: Annotated[list[str], typer.Option("--role", help="Restrict candidate member roles.")] = [],
    output: Annotated[Path | None, typer.Option("--output", "-o", help="Write output to this path.")] = None,
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: text, json, or markdown.")] = "json",
    width: Annotated[int, typer.Option("--width", help="Plain-text wrap width (minimum 40).")] = 88,
) -> None:
    """Project source-bound similarity candidates already present in a session."""
    try:
        from anatomize.lifecycle import SimilarityQuery
        from anatomize.review import ReviewApplication
        from anatomize.sessions import load_session_bundle

        result = ReviewApplication().similarity(
            load_session_bundle(session),
            query=SimilarityQuery(
                minimum_lines=minimum_lines,
                maximum_candidates=maximum_candidates,
                roles=role,
            ),
        )
        _emit_review(result, format=format, output=output, width=width)
    except (ValueError, OSError) as error:
        _review_failure(error)


@review_app.command("change")
def review_change_command(
    before: Annotated[Path, typer.Argument(help="Exact before-state session bundle.")],
    after: Annotated[Path, typer.Argument(help="Exact after-state session bundle.")],
    output: Annotated[Path | None, typer.Option("--output", "-o", help="Write output to this path.")] = None,
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: text, json, or markdown.")] = "text",
    width: Annotated[int, typer.Option("--width", help="Plain-text wrap width (minimum 40).")] = 88,
) -> None:
    """Compare two exact states and produce a semantic change dossier."""
    try:
        from anatomize.review import ReviewApplication
        from anatomize.sessions import load_session_bundle

        result = ReviewApplication().change(load_session_bundle(before), load_session_bundle(after))
        _emit_review(result, format=format, output=output, width=width)
    except (ValueError, OSError) as error:
        _review_failure(error)


@review_app.command("consolidate")
def review_consolidate_command(
    exchange: Annotated[Path, typer.Argument(help="Base dossier-exchange JSON artifact.")],
    similarity: Annotated[Path, typer.Argument(help="Source-bound similarity JSON artifact.")],
    candidate: Annotated[str, typer.Argument(help="Exact similarity candidate_id.")],
    output: Annotated[Path | None, typer.Option("--output", "-o", help="Write output to this path.")] = None,
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: text, json, or markdown.")] = "text",
    width: Annotated[int, typer.Option("--width", help="Plain-text wrap width (minimum 40).")] = 88,
) -> None:
    """Build the complete consolidation-question dossier for one candidate."""
    try:
        from anatomize.lifecycle import SimilarityArtifact
        from anatomize.review import DossierExchange, ReviewApplication, load_review_artifact

        base = load_review_artifact(exchange, expected_type="anatomize.dossier-exchange")
        artifact = load_review_artifact(similarity, expected_type="anatomize.similarity")
        if not isinstance(base, DossierExchange) or not isinstance(artifact, SimilarityArtifact):
            raise ValueError("Expected dossier-exchange and similarity artifacts")
        selected = next((item for item in artifact.candidates if item.candidate_id == candidate), None)
        if selected is None:
            raise ValueError(f"Similarity artifact does not contain candidate {candidate!r}")
        result = ReviewApplication().consolidation(base, selected)
        _emit_review(result, format=format, output=output, width=width)
    except (ValueError, OSError) as error:
        _review_failure(error)


@review_app.command("overlay-create")
def review_overlay_create_command(
    session: Annotated[Path, typer.Argument(help="Exact current-state session bundle.")],
    similarity: Annotated[Path, typer.Argument(help="Source-bound similarity JSON artifact.")],
    candidate: Annotated[str, typer.Argument(help="Exact similarity candidate_id.")],
    disposition: Annotated[str, typer.Option("--disposition", help="Consumer decision disposition.")],
    rationale: Annotated[str, typer.Option("--rationale", help="Reviewer's reason for the decision.")],
    owner: Annotated[str, typer.Option("--owner", help="Stable consumer or review namespace.")],
    preserved_divergence: Annotated[
        list[str], typer.Option("--preserved-divergence", help="Difference that must remain distinct.")
    ] = [],
    review_condition: Annotated[
        list[str], typer.Option("--review-condition", help="Condition requiring future reconsideration.")
    ] = [],
    output: Annotated[Path | None, typer.Option("--output", "-o", help="Write output to this path.")] = None,
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: text, json, or markdown.")] = "json",
    width: Annotated[int, typer.Option("--width", help="Plain-text wrap width (minimum 40).")] = 88,
) -> None:
    """Record a review decision and rationale without changing the evidence."""
    try:
        from anatomize.lifecycle import DecisionDisposition, SimilarityArtifact
        from anatomize.review import ReviewApplication, load_review_artifact
        from anatomize.sessions import load_session_bundle

        artifact = load_review_artifact(similarity, expected_type="anatomize.similarity")
        if not isinstance(artifact, SimilarityArtifact):
            raise ValueError("Expected a similarity artifact")
        selected = next((item for item in artifact.candidates if item.candidate_id == candidate), None)
        if selected is None:
            raise ValueError(f"Similarity artifact does not contain candidate {candidate!r}")
        result = ReviewApplication().decision_overlay(
            load_session_bundle(session),
            selected,
            owner_namespace=owner,
            disposition=DecisionDisposition(disposition),
            rationale=rationale,
            preserved_divergence=preserved_divergence,
            review_conditions=review_condition,
        )
        _emit_review(result, format=format, output=output, width=width)
    except (ValueError, OSError) as error:
        _review_failure(error)


@review_app.command("overlay-check")
def review_overlay_check_command(
    overlay: Annotated[Path, typer.Argument(help="Decision-overlay JSON artifact.")],
    session: Annotated[Path, typer.Argument(help="Exact current-state session bundle.")],
    similarity: Annotated[Path, typer.Argument(help="Current source-bound similarity artifact.")],
    candidate: Annotated[str, typer.Argument(help="Current similarity candidate_id.")],
    output: Annotated[Path | None, typer.Option("--output", "-o", help="Write output to this path.")] = None,
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: text, json, or markdown.")] = "text",
    width: Annotated[int, typer.Option("--width", help="Plain-text wrap width (minimum 40).")] = 88,
) -> None:
    """Check decision currency against only its relevant current evidence."""
    try:
        from anatomize.lifecycle import DecisionOverlay, SimilarityArtifact
        from anatomize.review import ReviewApplication, load_review_artifact
        from anatomize.sessions import load_session_bundle

        decision = load_review_artifact(overlay, expected_type="anatomize.decision-overlay")
        artifact = load_review_artifact(similarity, expected_type="anatomize.similarity")
        if not isinstance(decision, DecisionOverlay) or not isinstance(artifact, SimilarityArtifact):
            raise ValueError("Expected decision-overlay and similarity artifacts")
        selected = next((item for item in artifact.candidates if item.candidate_id == candidate), None)
        if selected is None:
            raise ValueError(f"Similarity artifact does not contain candidate {candidate!r}")
        result = ReviewApplication().evaluate_overlay(decision, load_session_bundle(session), selected)
        _emit_review(result, format=format, output=output, width=width)
        if not result.current:
            raise typer.Exit(4)
    except typer.Exit:
        raise
    except (ValueError, OSError) as error:
        _review_failure(error)


@review_app.command("intent")
def review_intent_command(
    session: Annotated[Path, typer.Argument(help="Exact before-state session bundle.")],
    exchange: Annotated[Path, typer.Argument(help="Implementation dossier-exchange JSON artifact.")],
    obligations: Annotated[
        Path,
        typer.Argument(help="JSON object with an obligations array and optional declared_unknowns array."),
    ],
    decision_overlay: Annotated[
        str | None,
        typer.Option("--decision-overlay", help="Optional consumer decision_id governing implementation."),
    ] = None,
    output: Annotated[Path | None, typer.Option("--output", "-o", help="Write output to this path.")] = None,
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: text, json, or markdown.")] = "json",
    width: Annotated[int, typer.Option("--width", help="Plain-text wrap width (minimum 40).")] = 88,
) -> None:
    """Bind explicit consumer obligations to an exact before-state dossier."""
    try:
        from anatomize.lifecycle import ObligationKind, build_implementation_obligation
        from anatomize.review import DossierExchange, ReviewApplication, load_review_artifact
        from anatomize.review.io import json_object
        from anatomize.sessions import load_session_bundle

        base = load_review_artifact(exchange, expected_type="anatomize.dossier-exchange")
        if not isinstance(base, DossierExchange):
            raise ValueError("Expected a dossier exchange")
        payload = json_object(obligations)
        obligation_values = payload.get("obligations")
        if not isinstance(obligation_values, list):
            raise ValueError("Obligation input requires an obligations array")
        normalized_obligations = [item for item in obligation_values if isinstance(item, dict)]
        if len(normalized_obligations) != len(obligation_values):
            raise ValueError("Every obligation must be a JSON object")
        built = []
        for item in normalized_obligations:
            normalized = dict(item)
            normalized["kind"] = ObligationKind(str(normalized.get("kind")))
            built.append(build_implementation_obligation(**normalized))
        unknowns = payload.get("declared_unknowns", [])
        if not isinstance(unknowns, list) or any(not isinstance(item, str) for item in unknowns):
            raise ValueError("declared_unknowns must be an array of strings")
        result = ReviewApplication().implementation_intent(
            load_session_bundle(session),
            base,
            obligations=built,
            decision_overlay_id=decision_overlay,
            declared_unknowns=unknowns,
        )
        _emit_review(result, format=format, output=output, width=width)
    except (TypeError, ValueError, OSError) as error:
        _review_failure(error)


@review_app.command("verify")
def review_verify_command(
    intent: Annotated[Path, typer.Argument(help="Implementation-intent JSON artifact.")],
    after: Annotated[Path, typer.Argument(help="Exact after-state session bundle.")],
    observations: Annotated[
        Path,
        typer.Argument(help="JSON object containing an observations array."),
    ],
    output: Annotated[Path | None, typer.Option("--output", "-o", help="Write output to this path.")] = None,
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: text, json, or markdown.")] = "text",
    width: Annotated[int, typer.Option("--width", help="Plain-text wrap width (minimum 40).")]=88,
) -> None:
    """Verify every declared obligation against fresh after-state evidence."""
    try:
        from anatomize.lifecycle import ClosureObservation, ImplementationIntent
        from anatomize.review import ReviewApplication, load_review_artifact
        from anatomize.review.io import json_object
        from anatomize.sessions import load_session_bundle

        declared = load_review_artifact(intent, expected_type="anatomize.implementation-intent")
        bundle = load_session_bundle(after)
        if not isinstance(declared, ImplementationIntent):
            raise ValueError("Expected an implementation intent")
        if len(bundle.manifest.source_states) != 1:
            raise ValueError("Closure verification requires a one-state after session")
        state_id = bundle.manifest.source_states[0].source_state.state_id
        payload = json_object(observations)
        observation_values = payload.get("observations")
        if not isinstance(observation_values, list):
            raise ValueError("Observation input requires an observations array")
        built = [
            ClosureObservation.model_validate({**item, "source_state_id": state_id})
            for item in observation_values
            if isinstance(item, dict)
        ]
        if len(built) != len(observation_values):
            raise ValueError("Every observation must be a JSON object")
        result = ReviewApplication().verify(declared, bundle, observations=built)
        _emit_review(result, format=format, output=output, width=width)
        if result.outcome.value != "closed":
            raise typer.Exit(3)
    except typer.Exit:
        raise
    except (TypeError, ValueError, OSError) as error:
        _review_failure(error)


@review_app.command("export")
def review_export_command(
    artifact: Annotated[Path, typer.Argument(help="Any supported review JSON artifact.")],
    format: Annotated[
        str,
        typer.Option("--format", "-f", help="Output format: text, json, or markdown."),
    ] = "markdown",
    output: Annotated[Path | None, typer.Option("--output", "-o", help="Write output to this path.")] = None,
    width: Annotated[int, typer.Option("--width", help="Plain-text wrap width (minimum 40).")] = 88,
) -> None:
    """Render a validated review artifact for terminals, scripts, or pull requests."""
    try:
        from anatomize.review import load_review_artifact

        _emit_review(load_review_artifact(artifact), format=format, output=output, width=width)
    except (ValueError, OSError) as error:
        _review_failure(error)


@review_app.command("check")
def review_check_command(
    artifact: Annotated[Path, typer.Argument(help="Any supported review JSON artifact.")],
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: text, json, or markdown.")] = "text",
    width: Annotated[int, typer.Option("--width", help="Plain-text wrap width (minimum 40).")] = 88,
) -> None:
    """Validate schema, references, identities, and digests without mutating state."""
    try:
        from anatomize.review import ReviewApplication

        _emit_review(ReviewApplication().check(artifact), format=format, output=None, width=width)
    except (ValueError, OSError) as error:
        _review_failure(error)


@review_app.command("recover")
def review_recover_command(
    store: Annotated[Path, typer.Argument(help="Recoverable review-session store.")],
    output: Annotated[Path, typer.Option("--output", "-o", help="Write the recovered session or report.")],
    format: Annotated[
        str,
        typer.Option("--format", "-f", help="Output format: text, json, or markdown."),
    ] = "json",
    width: Annotated[int, typer.Option("--width", help="Plain-text wrap width (minimum 40).")] = 88,
) -> None:
    """Load CURRENT or the prior valid generation and render the recovered session."""
    try:
        from anatomize.sessions import SessionStore

        bundle = SessionStore(store).load(recover=True)
        _emit_review(bundle, format=format, output=output, width=width)
    except (ValueError, OSError) as error:
        _review_failure(error)


def _emit_review(value: object, *, format: str, output: Path | None, width: int) -> None:
    from pydantic import BaseModel

    from anatomize._artifacts import atomic_write_bytes
    from anatomize.review import ReviewOutputFormat, render_review

    if not isinstance(value, (BaseModel, dict)):
        raise ValueError("Review output must be a validated model or capability object")
    rendered = render_review(value, format=ReviewOutputFormat(format), width=width)
    if output is None:
        typer.echo(rendered, nl=False)
        return
    atomic_write_bytes(output, rendered.encode("utf-8"))
    typer.echo(f"Wrote review artifact: {output}")


def _review_failure(error: Exception) -> None:
    code = str(getattr(error, "code", "review_invalid_input"))
    remediation = str(
        getattr(
            error,
            "remediation",
            "Inspect the command help and regenerate exact current-state artifacts.",
        )
    )
    exit_code = int(getattr(error, "exit_code", 2))
    typer.echo(f"error[{code}]: {error}", err=True)
    typer.echo(f"remediation: {remediation}", err=True)
    raise typer.Exit(exit_code)
