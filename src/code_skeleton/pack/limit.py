"""Output limits and parsing for `pack`."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class LimitKind(str, Enum):
    BYTES = "bytes"
    TOKENS = "tokens"


@dataclass(frozen=True)
class OutputLimit:
    kind: LimitKind
    value: int


def parse_output_limit(value: str) -> OutputLimit:
    """Parse an output limit specification.

    Supported:
    - tokens: `20000t`
    - bytes: `500kb`, `2mb`, `1000000b`, `123` (bytes)
    """
    raw = value.strip().lower()
    if not raw:
        raise ValueError("Empty limit")

    if raw.endswith("t"):
        num = raw[:-1].strip()
        if not num:
            raise ValueError(f"Invalid token limit: {value}")
        try:
            tokens = int(num.replace("_", ""))
        except ValueError as e:
            raise ValueError(f"Invalid token limit: {value}") from e
        if tokens <= 0:
            raise ValueError(f"Token limit must be > 0: {value}")
        return OutputLimit(kind=LimitKind.TOKENS, value=tokens)

    multipliers = {
        "gb": 1024 * 1024 * 1024,
        "mb": 1024 * 1024,
        "kb": 1024,
        "b": 1,
    }
    for suffix, mult in multipliers.items():
        if raw.endswith(suffix):
            num = raw[: -len(suffix)].strip()
            if not num:
                raise ValueError(f"Invalid byte limit: {value}")
            try:
                amount = float(num.replace("_", ""))
            except ValueError as e:
                raise ValueError(f"Invalid byte limit: {value}") from e
            bytes_ = int(amount * mult)
            if bytes_ <= 0:
                raise ValueError(f"Byte limit must be > 0: {value}")
            return OutputLimit(kind=LimitKind.BYTES, value=bytes_)

    # No suffix => bytes.
    try:
        bytes_ = int(raw.replace("_", ""))
    except ValueError as e:
        raise ValueError(f"Invalid output limit: {value}") from e
    if bytes_ <= 0:
        raise ValueError(f"Byte limit must be > 0: {value}")
    return OutputLimit(kind=LimitKind.BYTES, value=bytes_)
