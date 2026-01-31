"""Module with complex signatures."""

from __future__ import annotations


def f(a: int, b: int, /, c: int, d: int, e: int = 1, *args: int, **kwargs: str) -> None:
    pass


class C:
    def m(self, x: int, /, y: int, *, z: int = 0) -> int:
        return x + y + z
