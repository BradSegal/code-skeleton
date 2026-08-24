"""Exact conversion between provider coordinates and canonical source ranges."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import Field, model_validator

from anatomize._errors import AnatomizeError
from anatomize.evidence import EvidenceModel, SourcePosition, SourceRange


class ColumnEncoding(str, Enum):
    """Unit used for a column within a decoded source line."""

    UNICODE_CODEPOINT = "unicode_codepoint"
    BYTE = "byte"
    UTF8_BYTE = "utf8_byte"
    UTF16_CODE_UNIT = "utf16_code_unit"


class CoordinateConvention(EvidenceModel):
    """Provider coordinate convention; all ranges remain half-open."""

    line_base: Literal[0, 1]
    column_base: Literal[0, 1] = 0
    column_encoding: ColumnEncoding
    range_end: Literal["exclusive"] = "exclusive"


class ProviderPosition(EvidenceModel):
    line: int = Field(ge=0)
    column: int = Field(ge=0)


class ProviderRange(EvidenceModel):
    start: ProviderPosition
    end: ProviderPosition

    @model_validator(mode="after")
    def validate_order(self) -> ProviderRange:
        if (self.end.line, self.end.column) < (self.start.line, self.start.column):
            raise ValueError("provider range end must not precede start")
        return self


class CoordinateError(AnatomizeError):
    """Stable coordinate-boundary failure with exact provider context."""

class SourceCoordinateMap:
    """Immutable line map for exact, round-trippable coordinate conversion."""

    def __init__(self, source: str) -> None:
        self._lines = _source_lines(source)

    def to_canonical(
        self,
        source_range: ProviderRange,
        convention: CoordinateConvention,
    ) -> SourceRange:
        """Convert a provider half-open range to canonical line-1/codepoint-0."""
        return SourceRange(
            start=self._to_canonical_position(source_range.start, convention),
            end=self._to_canonical_position(source_range.end, convention),
        )

    def from_canonical(
        self,
        source_range: SourceRange,
        convention: CoordinateConvention,
    ) -> ProviderRange:
        """Convert a canonical half-open range to the requested provider convention."""
        return ProviderRange(
            start=self._from_canonical_position(source_range.start, convention),
            end=self._from_canonical_position(source_range.end, convention),
        )

    def convert(
        self,
        source_range: ProviderRange,
        *,
        source_convention: CoordinateConvention,
        target_convention: CoordinateConvention,
    ) -> ProviderRange:
        """Convert between two provider conventions through the canonical form."""
        return self.from_canonical(self.to_canonical(source_range, source_convention), target_convention)

    def _to_canonical_position(
        self,
        position: ProviderPosition,
        convention: CoordinateConvention,
    ) -> SourcePosition:
        line_index = position.line - convention.line_base
        if line_index < 0 or line_index >= len(self._lines):
            raise CoordinateError("coordinate_line_out_of_bounds", f"line {position.line} is outside source")
        encoded_column = position.column - convention.column_base
        if encoded_column < 0:
            raise CoordinateError(
                "coordinate_column_before_base",
                f"column {position.column} precedes base {convention.column_base}",
            )
        column = _column_to_codepoint(
            self._lines[line_index],
            encoded_column,
            convention.column_encoding,
        )
        return SourcePosition(line=line_index + 1, column=column)

    def _from_canonical_position(
        self,
        position: SourcePosition,
        convention: CoordinateConvention,
    ) -> ProviderPosition:
        line_index = position.line - 1
        if line_index < 0 or line_index >= len(self._lines):
            raise CoordinateError("coordinate_line_out_of_bounds", f"line {position.line} is outside source")
        line = self._lines[line_index]
        if position.column > len(line):
            raise CoordinateError(
                "coordinate_column_out_of_bounds",
                f"column {position.column} is outside line {position.line}",
            )
        encoded_column = _codepoint_to_column(line, position.column, convention.column_encoding)
        return ProviderPosition(
            line=line_index + convention.line_base,
            column=encoded_column + convention.column_base,
        )


def _source_lines(source: str) -> tuple[str, ...]:
    raw_lines = source.splitlines(keepends=True)
    if not raw_lines:
        return ("",)
    lines = tuple(_strip_line_ending(line) for line in raw_lines)
    if source.endswith(("\n", "\r")):
        return (*lines, "")
    return lines


def _strip_line_ending(line: str) -> str:
    if line.endswith("\r\n"):
        return line[:-2]
    if line.endswith(("\r", "\n")):
        return line[:-1]
    return line


def _column_to_codepoint(line: str, column: int, encoding: ColumnEncoding) -> int:
    if encoding is ColumnEncoding.UNICODE_CODEPOINT:
        if column <= len(line):
            return column
        raise CoordinateError("coordinate_column_out_of_bounds", f"column {column} is outside source line")
    consumed = 0
    for index, character in enumerate(line):
        if consumed == column:
            return index
        consumed += _encoded_width(character, encoding)
        if consumed > column:
            raise CoordinateError(
                "coordinate_column_splits_character",
                f"column {column} splits a {encoding.value} character encoding",
            )
    if consumed == column:
        return len(line)
    raise CoordinateError("coordinate_column_out_of_bounds", f"column {column} is outside source line")


def _codepoint_to_column(line: str, column: int, encoding: ColumnEncoding) -> int:
    if encoding is ColumnEncoding.UNICODE_CODEPOINT:
        return column
    return sum(_encoded_width(character, encoding) for character in line[:column])


def _encoded_width(character: str, encoding: ColumnEncoding) -> int:
    if encoding in {ColumnEncoding.BYTE, ColumnEncoding.UTF8_BYTE}:
        return len(character.encode("utf-8"))
    if encoding is ColumnEncoding.UTF16_CODE_UNIT:
        return len(character.encode("utf-16-le")) // 2
    return 1
