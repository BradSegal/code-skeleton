"""Token counting for pack output."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache

import tiktoken


@dataclass(frozen=True)
class TokenCounts:
    per_file_content_tokens: dict[str, int]
    content_total_tokens: int


def count_tokens(text: str, *, encoding_name: str) -> int:
    return len(_encoding(encoding_name).encode(text))


def count_content_tokens_by_path(payload_by_path: dict[str, str], *, encoding_name: str) -> TokenCounts:
    encoding = _encoding(encoding_name)
    per_file: dict[str, int] = {}
    total = 0
    for path in sorted(payload_by_path.keys()):
        tokens = len(encoding.encode(payload_by_path[path]))
        per_file[path] = tokens
        total += tokens
    return TokenCounts(per_file_content_tokens=per_file, content_total_tokens=total)


@cache
def _encoding(name: str) -> tiktoken.Encoding:
    return tiktoken.get_encoding(name)
