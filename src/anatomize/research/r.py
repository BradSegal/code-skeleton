"""Conservative R package and source inventory; semantic resolution stays external."""

from __future__ import annotations

import re
from collections.abc import Mapping
from enum import Enum
from pathlib import PurePosixPath
from typing import Literal

from pydantic import Field, model_validator

from anatomize._artifacts import content_id, sha256_digest
from anatomize.evidence import EvidenceModel, EvidenceStrength, validate_repository_path
from anatomize.lifecycle.testing import TestIntent, extract_r_test_intent

R_ARTIFACT_TYPE: Literal["anatomize.r-repository"] = "anatomize.r-repository"
R_ARTIFACT_SCHEMA_VERSION: Literal["1.0.0"] = "1.0.0"


class RSourceLocator(EvidenceModel):
    path: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_locator(self) -> RSourceLocator:
        validate_repository_path(self.path)
        if self.end_line < self.start_line:
            raise ValueError("R source locator end must not precede start")
        return self


class RFunctionKind(str, Enum):
    FUNCTION = "function"
    S3_METHOD_CANDIDATE = "s3_method_candidate"
    S4_METHOD_DECLARATION = "s4_method_declaration"
    S4_GENERIC_DECLARATION = "s4_generic_declaration"
    S4_CLASS_DECLARATION = "s4_class_declaration"
    R6_CLASS_DECLARATION = "r6_class_declaration"


class RCall(EvidenceModel):
    call_id: str
    caller_id: str | None = None
    target: str
    locator: RSourceLocator
    strength: EvidenceStrength = EvidenceStrength.CONSERVATIVE
    dynamic: bool = False

    @model_validator(mode="after")
    def validate_call(self) -> RCall:
        if self.call_id != content_id(
            "r-call",
            self.model_dump(mode="json", exclude={"call_id", "strength"}),
        ):
            raise ValueError("R call identifier does not match source evidence")
        return self


class RFunction(EvidenceModel):
    function_id: str
    name: str
    qualified_name: str
    kind: RFunctionKind
    locator: RSourceLocator
    parameters: list[str]
    exported: bool | None = None
    roxygen: list[str] = Field(default_factory=list)
    calls: list[str] = Field(default_factory=list)
    dynamic: bool = False

    @model_validator(mode="after")
    def validate_function(self) -> RFunction:
        if self.function_id != _r_function_id(self):
            raise ValueError("R function identifier does not match stable language identity")
        return self


class RNamespaceKind(str, Enum):
    EXPORT = "export"
    EXPORT_PATTERN = "export_pattern"
    IMPORT = "import"
    IMPORT_FROM = "import_from"
    S3_METHOD = "s3_method"
    OTHER = "other"


class RNamespaceDirective(EvidenceModel):
    directive_id: str
    kind: RNamespaceKind
    package: str | None = None
    symbols: list[str] = Field(default_factory=list)
    raw_name: str
    locator: RSourceLocator

    @model_validator(mode="after")
    def validate_directive(self) -> RNamespaceDirective:
        if self.directive_id != content_id(
            "r-namespace",
            self.model_dump(mode="json", exclude={"directive_id"}),
        ):
            raise ValueError("R namespace identifier does not match source evidence")
        return self


class RDataDeclaration(EvidenceModel):
    declaration_id: str
    operation: str
    target: str | None
    locator: RSourceLocator
    content_accessed: Literal[False] = False

    @model_validator(mode="after")
    def validate_declaration(self) -> RDataDeclaration:
        if self.declaration_id != content_id(
            "r-data-declaration",
            self.model_dump(mode="json", exclude={"declaration_id", "content_accessed"}),
        ):
            raise ValueError("R data declaration identifier does not match source evidence")
        return self


class RPackage(EvidenceModel):
    package: str
    version: str | None = None
    title: str | None = None
    dependencies: dict[str, list[str]] = Field(default_factory=dict)
    configuration: dict[str, str] = Field(default_factory=dict)
    description_digest: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")


class RRepositoryArtifact(EvidenceModel):
    artifact_type: Literal["anatomize.r-repository"] = R_ARTIFACT_TYPE
    schema_version: Literal["1.0.0"] = R_ARTIFACT_SCHEMA_VERSION
    repository_id: str
    source_state_id: str
    package: RPackage | None = None
    files: dict[str, str]
    functions: list[RFunction]
    calls: list[RCall]
    namespace: list[RNamespaceDirective]
    tests: list[TestIntent]
    data_declarations: list[RDataDeclaration]
    limitations: list[str]

    @model_validator(mode="after")
    def validate_artifact(self) -> RRepositoryArtifact:
        for records, attribute in (
            (self.functions, "function_id"),
            (self.calls, "call_id"),
            (self.namespace, "directive_id"),
            (self.tests, "intent_id"),
            (self.data_declarations, "declaration_id"),
        ):
            identities = [getattr(item, attribute) for item in records]
            if len(identities) != len(set(identities)):
                raise ValueError(f"R artifact {attribute} values must be unique")
        return self


def extract_r_repository(
    sources: Mapping[str, str],
    *,
    repository_id: str,
    source_state_id: str,
) -> RRepositoryArtifact:
    """Inventory bounded supplied R source; no files, packages, or R code are loaded."""
    normalized = dict(sorted(sources.items()))
    for path in normalized:
        validate_repository_path(path)
    description = normalized.get("DESCRIPTION")
    package = _parse_description(description) if description is not None else None
    package_name = package.package if package is not None else PurePosixPath(repository_id).name
    namespace = _parse_namespace(normalized.get("NAMESPACE", ""))
    explicit_exports = {symbol for item in namespace if item.kind is RNamespaceKind.EXPORT for symbol in item.symbols}
    functions: list[RFunction] = []
    calls: list[RCall] = []
    tests: list[TestIntent] = []
    data: list[RDataDeclaration] = []
    limitations: set[str] = set()
    file_digests: dict[str, str] = {}
    for path, source in normalized.items():
        file_digests[path] = sha256_digest(source.encode())
        if not path.casefold().endswith((".r", ".rmd", ".qmd")):
            continue
        try:
            source_tests = (
                extract_r_test_intent(
                    source,
                    repository_id=repository_id,
                    source_state_id=source_state_id,
                    path=path,
                ).intents
                if _is_test_path(path)
                else []
            )
            source_functions, source_calls = _parse_r_source(
                source,
                path=path,
                source_state_id=source_state_id,
                package_name=package_name,
                explicit_exports=explicit_exports,
            )
            source_data = _parse_data_declarations(source, path=path)
        except ValueError as error:
            limitations.add(f"{path}: source inventory unavailable ({_stable_parse_error(error)})")
            continue
        tests.extend(source_tests)
        functions.extend(source_functions)
        calls.extend(source_calls)
        data.extend(source_data)
        dynamic = sorted(set(re.findall(r"\b(get|assign|do\.call|eval|parse|substitute)\s*\(", source)))
        if dynamic:
            limitations.add(
                f"{path}: dynamic R constructs are inventory-only and require runtime or language-server evidence: "
                + ", ".join(dynamic)
            )
    if not normalized:
        limitations.add("No R repository inputs were supplied.")
    limitations.add(
        "Baseline R extraction is conservative syntax inventory; dispatch, non-standard evaluation, "
        "and runtime calls are not resolved."
    )
    limitations.add(
        "Function and declaration inventory handles multiline signatures and balanced strings/comments, "
        "but full R grammar, S3/S4 dispatch, R6 member semantics, and NSE require a qualified R parser provider."
    )
    return RRepositoryArtifact(
        repository_id=repository_id,
        source_state_id=source_state_id,
        package=package,
        files=file_digests,
        functions=sorted({item.function_id: item for item in functions}.values(), key=lambda item: item.function_id),
        calls=sorted({item.call_id: item for item in calls}.values(), key=lambda item: item.call_id),
        namespace=namespace,
        tests=sorted({item.intent_id: item for item in tests}.values(), key=lambda item: item.intent_id),
        data_declarations=sorted(
            {item.declaration_id: item for item in data}.values(), key=lambda item: item.declaration_id
        ),
        limitations=sorted(limitations),
    )


def _parse_description(source: str) -> RPackage:
    fields: dict[str, str] = {}
    current: str | None = None
    for line in source.splitlines():
        if line[:1].isspace() and current is not None:
            fields[current] += " " + line.strip()
        elif ":" in line:
            current, value = line.split(":", 1)
            current = current.strip()
            fields[current] = value.strip()
    package = fields.get("Package")
    if not package:
        raise ValueError("R DESCRIPTION requires a Package field")
    dependencies = {
        key.casefold(): sorted(item.strip().split()[0] for item in fields.get(key, "").split(",") if item.strip())
        for key in ("Depends", "Imports", "Suggests", "LinkingTo")
        if fields.get(key)
    }
    configuration = {
        key: value
        for key, value in fields.items()
        if key in {"Roxygen", "RoxygenNote", "Encoding", "SystemRequirements", "Config/testthat/edition"}
    }
    return RPackage(
        package=package,
        version=fields.get("Version"),
        title=fields.get("Title"),
        dependencies=dependencies,
        configuration=configuration,
        description_digest=sha256_digest(source.encode()),
    )


def _parse_namespace(source: str) -> list[RNamespaceDirective]:
    result: list[RNamespaceDirective] = []
    for line_number, line in enumerate(source.splitlines(), 1):
        stripped = line.strip()
        match = re.match(r"([A-Za-z0-9_.]+)\s*\((.*)\)\s*$", stripped)
        if not stripped or stripped.startswith("#") or match is None:
            continue
        raw_name, body = match.groups()
        arguments = [item.strip().strip("'\"") for item in body.split(",") if item.strip()]
        kind = {
            "export": RNamespaceKind.EXPORT,
            "exportPattern": RNamespaceKind.EXPORT_PATTERN,
            "import": RNamespaceKind.IMPORT,
            "importFrom": RNamespaceKind.IMPORT_FROM,
            "S3method": RNamespaceKind.S3_METHOD,
        }.get(raw_name, RNamespaceKind.OTHER)
        package = arguments[0] if kind in {RNamespaceKind.IMPORT, RNamespaceKind.IMPORT_FROM} else None
        symbols = arguments[1:] if kind is RNamespaceKind.IMPORT_FROM else arguments
        locator = RSourceLocator(path="NAMESPACE", start_line=line_number, end_line=line_number)
        result.append(
            RNamespaceDirective(
                directive_id=content_id(
                    "r-namespace",
                    {
                        "kind": kind.value,
                        "package": package,
                        "symbols": symbols,
                        "raw_name": raw_name,
                        "locator": locator.model_dump(mode="json"),
                    },
                ),
                kind=kind,
                package=package,
                symbols=symbols,
                raw_name=raw_name,
                locator=locator,
            )
        )
    return sorted(result, key=lambda item: item.directive_id)


def _parse_r_source(
    source: str,
    *,
    path: str,
    source_state_id: str,
    package_name: str,
    explicit_exports: set[str],
) -> tuple[list[RFunction], list[RCall]]:
    lines = source.splitlines()
    functions: list[RFunction] = []
    calls: list[RCall] = []
    for name, raw_parameters, index, end in _function_headers(source):
        roxygen = _preceding_roxygen(lines, index)
        exported = name in explicit_exports or any("@export" in item for item in roxygen)
        kind = RFunctionKind.S3_METHOD_CANDIDATE if "." in name else RFunctionKind.FUNCTION
        locator = RSourceLocator(path=path, start_line=index + 1, end_line=end + 1)
        parameters = [item.strip().split("=")[0].strip() for item in raw_parameters.split(",") if item.strip()]
        body = "\n".join(lines[index : end + 1])
        call_names = sorted(set(_r_call_names(body)).difference({"function"}))
        dynamic = any(value in {"get", "assign", "do.call", "eval", "parse", "substitute"} for value in call_names)
        provisional = RFunction.model_construct(
            function_id="pending",
            name=name,
            qualified_name=f"{package_name}::{name}",
            kind=kind,
            locator=locator,
            parameters=parameters,
            exported=exported,
            roxygen=roxygen,
            calls=call_names,
            dynamic=dynamic,
        )
        function = RFunction(
            function_id=_r_function_id(provisional),
            name=name,
            qualified_name=f"{package_name}::{name}",
            kind=kind,
            locator=locator,
            parameters=parameters,
            exported=exported,
            roxygen=roxygen,
            calls=call_names,
            dynamic=dynamic,
        )
        functions.append(function)
        for offset, body_line in enumerate(lines[index : end + 1], index + 1):
            for target in sorted(set(_r_call_names(body_line))):
                if target in {"function", "if", "for", "while", "return"}:
                    continue
                call_locator = RSourceLocator(path=path, start_line=offset, end_line=offset)
                is_dynamic = target in {"get", "assign", "do.call", "eval", "parse", "substitute"}
                calls.append(
                    RCall(
                        call_id=content_id(
                            "r-call",
                            {
                                "caller_id": function.function_id,
                                "target": target,
                                "locator": call_locator.model_dump(mode="json"),
                                "dynamic": is_dynamic,
                            },
                        ),
                        caller_id=function.function_id,
                        target=target,
                        locator=call_locator,
                        dynamic=is_dynamic,
                    )
                )
    declarations = (
        (r"(?m)^\s*setMethod\s*\(\s*(['\"])(.*?)\1\s*,\s*(['\"])(.*?)\3", RFunctionKind.S4_METHOD_DECLARATION),
        (r"(?m)^\s*setGeneric\s*\(\s*(['\"])(.*?)\1", RFunctionKind.S4_GENERIC_DECLARATION),
        (r"(?m)^\s*setClass\s*\(\s*(['\"])(.*?)\1", RFunctionKind.S4_CLASS_DECLARATION),
        (
            r"(?m)^\s*([A-Za-z.][\w.]*)\s*(?:<-|<<-|=)\s*(?:R6::)?R6Class\s*\(",
            RFunctionKind.R6_CLASS_DECLARATION,
        ),
    )
    for pattern, declaration_kind in declarations:
        for match in re.finditer(pattern, source):
            method_line = source.count("\n", 0, match.start()) + 1
            name = (
                f"{match.group(2)},{match.group(4)}"
                if declaration_kind is RFunctionKind.S4_METHOD_DECLARATION
                else match.group(2)
                if declaration_kind in {RFunctionKind.S4_GENERIC_DECLARATION, RFunctionKind.S4_CLASS_DECLARATION}
                else match.group(1)
            )
            locator = RSourceLocator(path=path, start_line=method_line, end_line=method_line)
            provisional = RFunction.model_construct(
                function_id="pending",
                name=name,
                qualified_name=f"{package_name}::{name}",
                kind=declaration_kind,
                locator=locator,
                parameters=[],
                exported=None,
                roxygen=[],
                calls=[declaration_kind.value],
                dynamic=False,
            )
            functions.append(
                RFunction(
                    function_id=_r_function_id(provisional),
                    name=name,
                    qualified_name=f"{package_name}::{name}",
                    kind=declaration_kind,
                    locator=locator,
                    parameters=[],
                    exported=None,
                    roxygen=[],
                    calls=[declaration_kind.value],
                    dynamic=False,
                )
            )
    return functions, calls


def _stable_parse_error(error: ValueError) -> str:
    message = str(error).strip()
    if message and "\n" not in message:
        return message[:240]
    return f"{type(error).__name__}: invalid extracted source evidence"


def _parse_data_declarations(source: str, *, path: str) -> list[RDataDeclaration]:
    result: list[RDataDeclaration] = []
    pattern = re.compile(
        r"\b(data|load|readRDS|read\.csv|(?:[A-Za-z.][\w.]*::)?read_(?:csv|tsv|parquet|feather|delim|rds))"
        r"\s*\(\s*([^,)]+)"
    )
    for line_number, line in enumerate(source.splitlines(), 1):
        for match in pattern.finditer(line):
            raw_target = match.group(2).strip()
            target = (
                raw_target.strip("'\"")
                if re.fullmatch(r"(['\"]).*\1", raw_target)
                else raw_target
                if re.fullmatch(r"[A-Za-z.][\w.]*", raw_target)
                else None
            )
            locator = RSourceLocator(path=path, start_line=line_number, end_line=line_number)
            operation = match.group(1)
            result.append(
                RDataDeclaration(
                    declaration_id=content_id(
                        "r-data-declaration",
                        {
                            "operation": operation,
                            "target": target,
                            "locator": locator.model_dump(mode="json"),
                        },
                    ),
                    operation=operation,
                    target=target,
                    locator=locator,
                )
            )
    return result


def _r_function_id(function: RFunction) -> str:
    return content_id(
        "r-function",
        {
            "qualified_name": function.qualified_name,
            "path": function.locator.path,
            "start_line": function.locator.start_line,
            "end_line": function.locator.end_line,
            "language": "r",
        },
    )


def _function_headers(source: str) -> list[tuple[str, str, int, int]]:
    """Locate conservative function assignments with balanced multiline syntax."""
    masked = _r_code_mask(source)
    # Do not use ``\s`` for horizontal indentation: in multiline mode it
    # consumes preceding blank/comment lines and shifts every source locator.
    pattern = re.compile(r"(?m)^[ \t]*([A-Za-z.][\w.]*)[ \t]*(?:<-|<<-|=)[ \t]*function[ \t]*\(")
    result: list[tuple[str, str, int, int]] = []
    for match in pattern.finditer(masked):
        open_parameter = match.end() - 1
        close_parameter = _balanced_character(masked, open_parameter, "(", ")")
        if close_parameter is None:
            continue
        start_line = source.count("\n", 0, match.start())
        body_open = masked.find("{", close_parameter + 1)
        if body_open < 0 or masked[close_parameter + 1 : body_open].strip():
            end_line = start_line
        else:
            body_close = _balanced_character(masked, body_open, "{", "}") if body_open >= 0 else None
            end_line = source.count("\n", 0, body_close) if body_close is not None else start_line
        result.append((match.group(1), source[open_parameter + 1:close_parameter], start_line, end_line))
    return result


def _balanced_character(source: str, start: int, opening: str, closing: str) -> int | None:
    depth = 0
    for offset in range(start, len(source)):
        if source[offset] == opening:
            depth += 1
        elif source[offset] == closing:
            depth -= 1
            if depth == 0:
                return offset
    return None


def _r_code_mask(source: str) -> str:
    """Mask strings and comments while preserving offsets and newlines."""
    output = list(source)
    quote: str | None = None
    escaped = False
    comment = False
    for index, character in enumerate(source):
        if character == "\n":
            quote = None if quote == "`" else quote
            comment = False
            escaped = False
            continue
        if comment:
            output[index] = " "
            continue
        if quote is not None:
            output[index] = " "
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = None
            continue
        if character == "#":
            comment = True
            output[index] = " "
        elif character in {'"', "'", "`"}:
            quote = character
            output[index] = " "
    return "".join(output)


def _preceding_roxygen(lines: list[str], start: int) -> list[str]:
    result = []
    index = start - 1
    while index >= 0 and lines[index].lstrip().startswith("#'"):
        result.append(lines[index].strip()[2:].strip())
        index -= 1
    return list(reversed(result))


def _r_call_names(source: str) -> list[str]:
    return re.findall(r"(?<![\w.])([A-Za-z.][\w.]*(?:::{1,2}[A-Za-z.][\w.]*)?)\s*\(", source)


def _is_test_path(path: str) -> bool:
    lowered = path.casefold()
    return "testthat" in lowered or PurePosixPath(path).name.startswith("test-")
