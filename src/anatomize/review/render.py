"""Accessible, deterministic presentation of public review artifacts."""

from __future__ import annotations

import json
import textwrap
from enum import Enum
from typing import Any

from pydantic import BaseModel

from anatomize._artifacts import canonical_ordered_json_bytes
from anatomize.dossiers import DossierGroup, DossierItem, DossierOmission, EvidenceLocator
from anatomize.review.models import DossierExchange


class ReviewOutputFormat(str, Enum):
    """Supported human and machine review projections."""

    TEXT = "text"
    JSON = "json"
    MARKDOWN = "markdown"


def render_review(value: BaseModel | dict[str, Any], *, format: ReviewOutputFormat, width: int = 88) -> str:
    """Render one result without ANSI, terminal control codes, or width-dependent semantics."""
    if width < 40:
        raise ValueError("review output width must be at least 40 columns")
    if format is ReviewOutputFormat.JSON:
        if isinstance(value, BaseModel):
            return canonical_ordered_json_bytes(value.model_dump(mode="json")).decode("utf-8")
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    if isinstance(value, DossierExchange):
        return _dossier_markdown(value) if format is ReviewOutputFormat.MARKDOWN else _dossier_text(value, width)
    if format is ReviewOutputFormat.MARKDOWN:
        return _generic_markdown(value)
    return _generic_text(value, width)


def _dossier_text(exchange: DossierExchange, width: int) -> str:
    dossier = exchange.dossier
    request = exchange.request
    lines = [
        f"Review dossier: {request.profile.value} / {dossier.status.value}",
        f"Question: {request.question}",
        f"Repository: {dossier.repository_id}",
        f"Evidence: {len(dossier.items)} items from {len(dossier.provider_run_ids)} provider run(s)",
        "",
    ]
    if dossier.boundary.targets:
        lines.append("Targets")
        for target in dossier.boundary.targets:
            lines.extend(_wrapped_item(f"{target.status.value}: {target.message}", width))
        lines.append("")
    items = {item.item_id: item for item in dossier.items}
    for group in dossier.groups:
        lines.extend(_text_group(group, items, width))
    if dossier.boundary.unsatisfied_stop_conditions:
        lines.append("Unresolved stop conditions")
        for condition in dossier.boundary.unsatisfied_stop_conditions:
            lines.extend(_wrapped_item(condition, width))
        lines.append("")
    if dossier.omissions:
        lines.append("Omissions and unknowns")
        for omission in dossier.omissions:
            label = "required" if omission.required else "bounded"
            lines.extend(
                _wrapped_item(
                    f"{label}; {omission.reason.value}; {omission.message}",
                    width,
                )
            )
        lines.append("")
    if dossier.expansions:
        lines.append("Next actions")
        for action in dossier.expansions:
            details = action.role.value if action.role is not None else action.target_id
            lines.extend(_wrapped_item(f"{action.kind.value}: {details or 'available in JSON output'}", width))
        lines.append("")
    use = dossier.budget_use
    lines.append(
        "Budget: "
        f"{use.items.used}/{use.items.limit} items; "
        f"{use.payload_bytes.used}/{use.payload_bytes.limit} payload bytes; "
        f"depth {use.depth.used}/{use.depth.limit}"
    )
    lines.append(f"Machine reference: {dossier.dossier_id}")
    return "\n".join(lines).rstrip() + "\n"


def _text_group(group: DossierGroup, items: dict[str, DossierItem], width: int) -> list[str]:
    required = "required" if group.required else "supporting"
    lines = [f"{group.section.value.replace('_', ' ').title()} / {group.role.value} ({required})"]
    lines.extend(_wrapped(group.proof_purpose, width))
    for item_id in group.item_ids:
        item = items[item_id]
        locator = _best_locator(item.locators)
        description = (
            f"{locator} — {item.record_kind}"
            if locator
            else f"{item.record_kind}: {_short_id(item.record_id)}"
        )
        qualifiers = [] if item.strength.value == "unknown" else [item.strength.value]
        if item.required:
            qualifiers.append("required")
        if qualifiers:
            description += f" ({', '.join(qualifiers)})"
        lines.extend(_wrapped_item(description, width))
        for message in _useful_reasons(item):
            lines.extend(_wrapped(message, width, indent="    "))
        for summary in _observation_summaries(item):
            lines.extend(_wrapped(summary, width, indent="    "))
        if item.conflict_ids:
            lines.extend(_wrapped(f"Conflicts: {len(item.conflict_ids)}", width, indent="    "))
        if item.unknown_ids:
            lines.extend(_wrapped(f"Unknowns: {len(item.unknown_ids)}", width, indent="    "))
    lines.append("")
    return lines


def _dossier_markdown(exchange: DossierExchange) -> str:
    request = exchange.request
    dossier = exchange.dossier
    lines = [
        f"# {request.profile.value.replace('_', ' ').title()} review",
        "",
        f"**{dossier.status.value.title()}** · `{dossier.repository_id}` · "
        f"{len(dossier.items)} evidence items · {len(dossier.provider_run_ids)} provider run(s)",
        "",
        request.question,
        "",
        "## Target",
        "",
    ]
    for target in dossier.boundary.targets:
        lines.append(f"- **{target.status.value.replace('_', ' ').title()}** — {target.message}")
    if not dossier.boundary.targets:
        lines.append("- Repository-wide orientation; no explicit target was required.")
    lines.extend(["", "## Evidence", ""])
    items = {item.item_id: item for item in dossier.items}
    for group in dossier.groups:
        lines.extend(
            [
                f"### {group.role.value.replace('_', ' ').title()}",
                "",
                group.proof_purpose,
                "",
            ]
        )
        for item_id in group.item_ids:
            lines.extend(_markdown_item(items[item_id]))
        lines.append("")
    lines.extend(["## Omissions and unknowns", ""])
    if dossier.boundary.unsatisfied_stop_conditions:
        for condition in dossier.boundary.unsatisfied_stop_conditions:
            lines.append(f"- Unsatisfied stop condition: {condition}")
    lines.extend(_markdown_omissions(dossier.omissions))
    if not dossier.omissions and not dossier.boundary.unsatisfied_stop_conditions:
        lines.append("- None declared within this dossier boundary.")
    lines.extend(["", "## Expansion actions", ""])
    visible_actions = dossier.expansions[:8]
    for action in visible_actions:
        details = []
        if action.target_id:
            details.append(f"target `{_short_id(action.target_id)}`")
        if action.role:
            details.append(f"role `{action.role.value}`")
        suffix = "; " + "; ".join(details) if details else ""
        lines.append(f"- `{action.kind.value}`{suffix}")
    if len(dossier.expansions) > len(visible_actions):
        hidden_actions = len(dossier.expansions) - len(visible_actions)
        lines.append(
            f"- {hidden_actions} additional typed actions are available in JSON output."
        )
    if not dossier.expansions:
        lines.append("- None.")
    lines.extend(["", "## Embedded content", ""])
    if not dossier.content:
        lines.append("No source content was embedded. Exact repository-relative locators remain above.")
    for content in dossier.content:
        location = content.path or content.external_locator or "unknown"
        lines.extend(
            [
                f"### `{location}`",
                "",
                f"Content class: `{content.content_class.value}`. Truncated: `{str(content.truncated).lower()}`.",
                "",
            ]
        )
        if content.text is not None:
            fence = "````" if "```" in content.text else "```"
            lines.extend([f"{fence}{content.language or 'text'}", content.text, fence, ""])
    lines.extend(
        [
            "## Machine references",
            "",
            f"- Session: `{dossier.session_id}`",
            f"- Request: `{request.request_id}`",
            f"- Dossier: `{dossier.dossier_id}`",
            f"- Exchange: `{exchange.exchange_id}`",
            f"- Source states: {', '.join(f'`{item}`' for item in dossier.source_state_ids)}",
            "",
            f"Authority: {dossier.boundary.authority}",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _markdown_item(item: DossierItem) -> list[str]:
    locations = ", ".join(f"`{value}`" for value in (_locator(item) for item in item.locators) if value) or "none"
    label = _item_label(item.record_id, locations)
    qualifiers = [item.record_kind]
    if item.strength.value != "unknown":
        qualifiers.append(item.strength.value)
    if item.required:
        qualifiers.append("required")
    lines = [
        f"- {label} — {', '.join(qualifiers)}."
    ]
    lines.extend(f"  - {message}" for message in _useful_reasons(item))
    lines.extend(f"  - {summary}" for summary in _observation_summaries(item))
    if item.relationship_ids:
        lines.append(f"  - Relationships: {len(item.relationship_ids)}")
    if item.conflict_ids:
        lines.append(f"  - Conflicts: {len(item.conflict_ids)}")
    if item.unknown_ids:
        lines.append(f"  - Unknowns: {len(item.unknown_ids)}")
    return lines


def _useful_reasons(item: DossierItem) -> list[str]:
    """Collapse boilerplate while retaining discriminating selection logic."""
    messages: list[str] = []
    for reason in item.selection_reasons:
        if reason.message.startswith("Selected for the ") and reason.message.endswith(" orientation boundary."):
            continue
        if reason.code.value in {"exact_target", "profile_required"} and len(item.selection_reasons) > 1:
            continue
        if reason.message not in messages:
            messages.append(reason.message)
    return messages


def _observation_summaries(item: DossierItem) -> list[str]:
    summaries: list[str] = []
    for observation in item.observations:
        value = f"{observation.method} ({observation.stance}, {observation.strength.value}): {observation.rationale}"
        if value not in summaries:
            summaries.append(value)
    return summaries


def _markdown_omissions(omissions: list[DossierOmission]) -> list[str]:
    grouped: dict[tuple[str, bool, str], list[DossierOmission]] = {}
    for omission in omissions:
        key = (
            omission.reason.value,
            omission.required,
            omission.role.value if omission.role is not None else "",
        )
        grouped.setdefault(key, []).append(omission)
    lines: list[str] = []
    for (reason, required, role), values in grouped.items():
        messages = list(dict.fromkeys(item.message for item in values))
        visible = messages[:3]
        detail = "; ".join(visible)
        if len(messages) > len(visible):
            detail += f"; +{len(messages) - len(visible)} more"
        role_suffix = f", {role}" if role else ""
        total = sum(item.total_count for item in values)
        lines.append(
            f"- **{reason.replace('_', ' ').title()}** "
            f"({'required' if required else 'bounded'}{role_suffix}, {total}): {detail}"
        )
    return lines


def _short_id(value: str) -> str:
    prefix, separator, digest = value.rpartition(":")
    if separator and len(digest) > 20:
        return f"{prefix.rsplit(':', 1)[-1]}:{digest[:12]}…"
    return value


def _semantic_label(record_id: str) -> str | None:
    if record_id.startswith("python:"):
        return record_id.removeprefix("python:").split("@", 1)[0]
    return None


def _item_label(record_id: str, locations: str) -> str:
    semantic = _semantic_label(record_id)
    if semantic is not None:
        return f"`{semantic}` — {locations}" if locations != "none" else f"`{semantic}`"
    return locations if locations != "none" else f"`{_short_id(record_id)}`"


def _generic_markdown(value: BaseModel | dict[str, Any]) -> str:
    payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    artifact_type = str(payload.get("artifact_type", "anatomize.result"))
    identity = next(
        (payload[key] for key in payload if key.endswith("_id") and isinstance(payload[key], str)),
        "not-applicable",
    )
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    return (
        f"# {artifact_type}\n\n"
        f"Stable reference: `{identity}`.\n\n"
        "The canonical payload below preserves exact source states, evidence references, differences, "
        "unknowns, and consumer-owned decisions.\n\n"
        f"```json\n{rendered}\n```\n"
    )


def _generic_text(value: BaseModel | dict[str, Any], width: int) -> str:
    payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    if payload.get("application") == "anatomize.review":
        interaction = payload.get("interaction", {})
        return "\n".join(
            [
                "Anatomize review application",
                f"API version: {payload.get('application_api_version', 'unknown')}",
                "Operations: " + ", ".join(str(item) for item in payload.get("operations", [])),
                "Profiles: " + ", ".join(str(item) for item in payload.get("profiles", [])),
                "Optional providers: "
                + str(interaction.get("optional_provider_invocation", "not declared")),
            ]
        ) + "\n"
    manifest = payload.get("manifest")
    if isinstance(manifest, dict):
        states = manifest.get("source_states", [])
        state_ids = [
            item.get("source_state", {}).get("state_id", "unknown")
            for item in states
            if isinstance(item, dict)
        ]
        providers = manifest.get("providers", [])
        provider_ids = [
            item.get("provider_id", "unknown") for item in providers if isinstance(item, dict)
        ]
        return "\n".join(
            [
                str(payload.get("artifact_type", "anatomize.session-bundle")),
                f"Status: {manifest.get('status', 'unknown')}",
                f"Repository: {manifest.get('repository_id', 'unknown')}",
                "Source state: " + ", ".join(str(item) for item in state_ids),
                "Providers: " + (", ".join(str(item) for item in provider_ids) or "none"),
                f"Session reference: {manifest.get('session_id', 'unknown')}",
                f"Bundle reference: {payload.get('bundle_id', 'unknown')}",
                f"Explicit omissions: {len(manifest.get('omissions', []))}",
            ]
        ) + "\n"
    lines = [str(payload.get("artifact_type", "anatomize result"))]
    for key in ("schema_version", "status", "outcome", "current", "message"):
        if key in payload:
            lines.extend(_wrapped(f"{key.replace('_', ' ').title()}: {payload[key]}", width))
    for key, item in payload.items():
        if key.endswith("_id") and isinstance(item, str):
            lines.extend(_wrapped(f"Reference: {item}", width))
            break
    return "\n".join(lines).rstrip() + "\n"


def _best_locator(locators: list[EvidenceLocator]) -> str | None:
    return next((value for value in (_locator(item) for item in locators) if value), None)


def _locator(locator: EvidenceLocator) -> str | None:
    if locator.path:
        value = locator.path
        if locator.start_line is not None:
            value += f":{locator.start_line}"
            if locator.start_column is not None:
                value += f":{locator.start_column}"
        return value
    return locator.external_locator


def _wrapped(value: str, width: int, *, indent: str = "") -> list[str]:
    return textwrap.wrap(
        value,
        width=width,
        initial_indent=indent,
        subsequent_indent=indent,
        break_long_words=False,
        break_on_hyphens=False,
    ) or [indent]


def _wrapped_item(value: str, width: int) -> list[str]:
    return textwrap.wrap(
        value,
        width=width,
        initial_indent="- ",
        subsequent_indent="  ",
        break_long_words=False,
        break_on_hyphens=False,
    )
