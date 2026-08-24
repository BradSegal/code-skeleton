"""Shared deterministic artifact and atomic-publication primitives."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from anatomize._errors import AnatomizeError


@dataclass(frozen=True)
class JsonLimits:
    """Resource bounds applied before untrusted JSON reaches a public model."""

    max_bytes: int
    max_depth: int = 64
    max_values: int = 500_000
    max_string_bytes: int = 1_000_000

    def __post_init__(self) -> None:
        if min(self.max_bytes, self.max_depth, self.max_values, self.max_string_bytes) <= 0:
            raise ValueError("JSON artifact limits must be positive")


class BoundedJsonError(AnatomizeError):
    """Format-neutral bounded JSON failure for mapping to a public error contract."""


def require_unique(values: Sequence[str], label: str) -> None:
    """Reject duplicate portable identities or labels with one stable contract."""
    if len(values) != len(set(values)):
        raise ValueError(f"{label} must be unique")


def parse_bounded_json_object(raw: bytes, *, limits: JsonLimits) -> dict[str, Any]:
    """Decode an object while bounding bytes, depth, values, and individual strings."""
    if len(raw) > limits.max_bytes:
        raise BoundedJsonError("too_large", f"artifact is {len(raw)} bytes; limit is {limits.max_bytes}")
    try:
        text = raw.decode("utf-8")
        _preflight_json(text, limits=limits)
        payload = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise BoundedJsonError("corrupt", "artifact is not valid bounded UTF-8 JSON") from error
    if not isinstance(payload, dict):
        raise BoundedJsonError("incomplete", "artifact root must be a JSON object")
    _validate_json_limits(payload, limits=limits)
    return payload


def _preflight_json(text: str, *, limits: JsonLimits) -> None:
    """Reject depth, value, and string bombs before object materialisation."""
    depth = 0
    values = 0
    in_string = False
    escaped = False
    string_start = 0
    primitive = False
    for offset, character in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
                values += 1
                size = len(text[string_start:offset].encode("utf-8"))
                if size > limits.max_string_bytes:
                    raise BoundedJsonError(
                        "string_limit",
                        f"artifact string is {size} bytes; limit is {limits.max_string_bytes}",
                    )
            continue
        if character == '"':
            in_string = True
            string_start = offset + 1
            primitive = False
        elif character in "[{":
            depth += 1
            values += 1
            primitive = False
            if depth > limits.max_depth:
                raise BoundedJsonError("depth_limit", f"artifact exceeds maximum JSON depth {limits.max_depth}")
        elif character in "]}":
            depth -= 1
            primitive = False
        elif character in ",:":
            primitive = False
        elif not character.isspace() and not primitive:
            values += 1
            primitive = True
        if values > limits.max_values:
            raise BoundedJsonError("value_limit", f"artifact exceeds maximum JSON value count {limits.max_values}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise BoundedJsonError("corrupt", f"artifact repeats object key {key!r}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> Any:
    raise BoundedJsonError("corrupt", f"artifact contains non-finite JSON number {value}")


def _validate_json_limits(value: Any, *, limits: JsonLimits) -> None:
    stack: list[tuple[Any, int]] = [(value, 1)]
    seen = 0
    while stack:
        current, depth = stack.pop()
        if depth > limits.max_depth:
            raise BoundedJsonError("depth_limit", f"artifact exceeds maximum JSON depth {limits.max_depth}")
        seen += 1
        if seen > limits.max_values:
            raise BoundedJsonError("value_limit", f"artifact exceeds maximum JSON value count {limits.max_values}")
        if isinstance(current, str):
            size = len(current.encode("utf-8"))
            if size > limits.max_string_bytes:
                raise BoundedJsonError(
                    "string_limit",
                    f"artifact string is {size} bytes; limit is {limits.max_string_bytes}",
                )
        elif isinstance(current, dict):
            stack.extend((key, depth + 1) for key in current)
            stack.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, list):
            stack.extend((item, depth + 1) for item in current)


def canonicalize_json(value: Any) -> Any:
    """Recursively sort mappings and set-like lists into one JSON representation."""
    if isinstance(value, dict):
        return {key: canonicalize_json(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        normalized = [canonicalize_json(item) for item in value]
        return sorted(normalized, key=compact_json)
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    return value


def compact_json(value: Any) -> str:
    """Render one stable JSON value for ordering and content identity."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def canonical_json_bytes(value: Any) -> bytes:
    """Render human-reviewable deterministic UTF-8 JSON with a final newline."""
    canonical = canonicalize_json(value)
    return (
        json.dumps(canonical, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"
    ).encode("utf-8")


def canonical_ordered_json_bytes(value: Any) -> bytes:
    """Render deterministic JSON while retaining semantically ordered arrays."""
    canonical = _canonicalize_ordered_json(value)
    return (
        json.dumps(canonical, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"
    ).encode("utf-8")


def _canonicalize_ordered_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _canonicalize_ordered_json(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        return [_canonicalize_ordered_json(item) for item in value]
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    return value


def sha256_digest(raw: bytes) -> str:
    """Return the explicit digest form used by all public artifacts."""
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


def content_id(prefix: str, value: Any) -> str:
    """Return a portable content-derived identifier without formatting sensitivity."""
    raw = compact_json(canonicalize_json(value)).encode("utf-8")
    return f"{prefix}:{sha256_digest(raw)}"


def atomic_write_bytes(path: Path, raw: bytes) -> None:
    """Durably replace one file while preserving its prior complete contents on failure."""
    destination = path.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "wb",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        _fsync_directory(destination.parent)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _fsync_directory(path: Path) -> None:
    """Persist directory-entry replacement where the platform exposes directory fsync."""
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY)
        os.fsync(descriptor)
    except OSError:
        # Some supported platforms cannot open or fsync directories. File fsync and
        # atomic replacement still prevent readers from observing partial bytes.
        return
    finally:
        if descriptor is not None:
            os.close(descriptor)
